from __future__ import annotations

import json
import re
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from core.cache import classification_cache_key, load_cache, save_cache, set_cached_closed_classification
from core.classify import ClassificationResult, classify_closed_pr, get_non_bot_comment_text, should_resolve_referenced_pull_request
from core.github import GhCancelled, GhRetryExhausted, cancel_running_gh, reset_gh_cancellation, run_gh
from core.leaderboard import configured_repo_leaderboard_exclusions, is_leaderboard_excluded_login, repo_leaderboard_config
from core.models import Cache, ClassificationEntry, Comment, Evidence, PullRequest, PullRequestRef, TimelineEvent, UserRef, int_value
from core.timeline import load_active_repos_from_text

CONSOLE = Console(stderr=True, highlight=False)


@dataclass(frozen=True)
class CacheRebuildResult:
    checked: int
    skipped: int
    failed: int
    divergences: int


@dataclass(frozen=True)
class CacheDivergence:
    key: str
    expected: ClassificationEntry
    actual: ClassificationResult


@dataclass(frozen=True)
class CacheRebuildWorkItem:
    index: int
    total: int
    key: str
    repo: str
    number: int
    expected: ClassificationEntry
    author_login: str


@dataclass(frozen=True)
class CacheRebuildWorkResult:
    item: CacheRebuildWorkItem
    pr: PullRequest | None
    actual: ClassificationResult | None = None
    error: str = ""
    skipped_excluded: bool = False


class CacheRebuildInterrupted(Exception):
    def __init__(self, result: CacheRebuildResult) -> None:
        super().__init__("classification cache rebuild interrupted")
        self.result = result


def rebuild_classification_cache(
    *,
    cache_file: Path,
    out_cache_file: Path,
    divergence_file: Path,
    repos_file: Path,
    active_repos_only: bool,
    limit: int | None = None,
    save_every: int = 25,
    workers: int = 4,
) -> CacheRebuildResult:
    if cache_file.resolve() == out_cache_file.resolve():
        raise ValueError("--out-cache-file must differ from --cache-file")
    if workers < 1:
        raise ValueError("--workers must be at least 1")
    reset_gh_cancellation()
    source_cache = load_cache(cache_file)
    output_cache = load_cache(out_cache_file) if out_cache_file.exists() else source_cache.model_copy(deep=True)
    out_cache_file.parent.mkdir(parents=True, exist_ok=True)
    active_repos = set(load_active_repos_from_text(repos_file.read_text(encoding="utf-8")))
    now = datetime.now(timezone.utc)
    # Seed from the existing report so a resumed run preserves divergences that
    # were recorded for entries this run will skip as already generated.
    divergences_by_key: dict[str, CacheDivergence] = load_divergence_report(divergence_file)
    checked = 0
    skipped = 0
    failed = 0
    candidates: list[tuple[str, ClassificationEntry]] = []
    ignored_excluded = 0
    for key, expected in sorted(source_cache.entries.items()):
        repo, _number = split_classification_cache_key(key)
        if active_repos_only and repo not in active_repos:
            continue
        author_login = _cached_author_login(output_cache, key) or _cached_author_login(source_cache, key)
        if _is_excluded_rebuild_author(repo, author_login):
            ignored_excluded += 1
            divergences_by_key.pop(key, None)
            continue
        candidates.append((key, expected))
    skipped += ignored_excluded
    ignored_note = f"; skipped {ignored_excluded} excluded author PRs" if ignored_excluded else ""
    _progress(f"Classifying {len(candidates)} closed PRs{ignored_note}...", "dim")
    try:
        work_items: list[CacheRebuildWorkItem] = []
        total = len(candidates)
        for index, (key, expected) in enumerate(candidates, start=1):
            repo, number = split_classification_cache_key(key)
            author_login = _cached_author_login(output_cache, key) or _cached_author_login(source_cache, key)
            if _entry_was_generated(output_cache, key, expected):
                skipped += 1
                _write_pr_prefix(index, total, repo, number, author_login)
                CONSOLE.print(f" {expected.classification} (cache)", style=_classification_style(expected.classification))
                continue
            work_items.append(
                CacheRebuildWorkItem(
                    index=index,
                    total=total,
                    key=key,
                    repo=repo,
                    number=number,
                    expected=expected,
                    author_login=author_login,
                )
            )
            if limit is not None and len(work_items) >= limit:
                break

        if workers == 1:
            for item in work_items:
                result = _classify_work_item(source_cache, item)
                checked, skipped, failed = _record_work_result(
                    result,
                    output_cache=output_cache,
                    divergences_by_key=divergences_by_key,
                    out_cache_file=out_cache_file,
                    divergence_file=divergence_file,
                    now=now,
                    checked=checked,
                    skipped=skipped,
                    failed=failed,
                    save_every=save_every,
                )
        else:
            executor = ThreadPoolExecutor(max_workers=workers)
            futures: dict[Future[CacheRebuildWorkResult], CacheRebuildWorkItem] = {
                executor.submit(_classify_work_item, source_cache, item): item for item in work_items
            }
            try:
                for future in as_completed(futures):
                    result = future.result()
                    checked, skipped, failed = _record_work_result(
                        result,
                        output_cache=output_cache,
                        divergences_by_key=divergences_by_key,
                        out_cache_file=out_cache_file,
                        divergence_file=divergence_file,
                        now=now,
                        checked=checked,
                        skipped=skipped,
                        failed=failed,
                        save_every=save_every,
                    )
            except BaseException:
                cancel_running_gh()
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            else:
                executor.shutdown(wait=True)
    except (KeyboardInterrupt, GhCancelled):
        cancel_running_gh()
        _save_rebuild_progress(output_cache, out_cache_file, divergences_by_key, divergence_file)
        _progress(
            f"Interrupted after classifying {checked} PRs, skipped {skipped}, failed {failed}, divergences {len(divergences_by_key)}. Checkpoint saved.",
            "yellow",
        )
        raise CacheRebuildInterrupted(
            CacheRebuildResult(checked=checked, skipped=skipped, failed=failed, divergences=len(divergences_by_key))
        ) from None

    _save_rebuild_progress(output_cache, out_cache_file, divergences_by_key, divergence_file)
    return CacheRebuildResult(checked=checked, skipped=skipped, failed=failed, divergences=len(divergences_by_key))


def classification_entry_matches_result(expected: ClassificationEntry, actual: ClassificationResult) -> bool:
    if (
        expected.classification == actual.classification == "shipped"
        and expected.evidenceKind == actual.evidence_kind == "direct-merge"
        and expected.release == actual.release
        and expected.viaLabel in {"", "direct"}
        and actual.via_label == "direct"
        and expected.viaUrl in {"", actual.via_url}
    ):
        return True
    return (
        expected.classification == actual.classification
        and expected.evidenceKind == actual.evidence_kind
        and expected.viaLabel == actual.via_label
        and expected.viaUrl == actual.via_url
        and expected.release == actual.release
    )


def load_divergence_report(path: Path) -> dict[str, CacheDivergence]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, list):
        return {}
    loaded: dict[str, CacheDivergence] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        key = item.get("key")
        expected = item.get("expected")
        actual = item.get("actual")
        if not isinstance(key, str) or not isinstance(expected, dict) or not isinstance(actual, dict):
            continue
        loaded[key] = CacheDivergence(
            key=key,
            expected=ClassificationEntry(
                classification=str(expected.get("classification") or ""),
                evidenceKind=str(expected.get("evidenceKind") or ""),
                viaLabel=str(expected.get("viaLabel") or ""),
                viaUrl=str(expected.get("viaUrl") or ""),
                release=str(expected.get("release") or ""),
            ),
            actual=ClassificationResult(
                classification=str(actual.get("classification") or ""),
                evidence_kind=str(actual.get("evidenceKind") or ""),
                via_label=str(actual.get("viaLabel") or ""),
                via_url=str(actual.get("viaUrl") or ""),
                release=str(actual.get("release") or ""),
            ),
        )
    return loaded


def write_divergence_report(divergences: list[CacheDivergence], path: Path) -> None:
    payload = [
        {
            "key": item.key,
            "expected": {
                "classification": item.expected.classification,
                "evidenceKind": item.expected.evidenceKind,
                "viaLabel": item.expected.viaLabel,
                "viaUrl": item.expected.viaUrl,
                "release": item.expected.release,
            },
            "actual": {
                "classification": item.actual.classification,
                "evidenceKind": item.actual.evidence_kind,
                "viaLabel": item.actual.via_label,
                "viaUrl": item.actual.via_url,
                "release": item.actual.release,
            },
        }
        for item in divergences
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_classification_cache_key(key: str) -> tuple[str, int]:
    repo, raw_number = key.rsplit("#", 1)
    return repo, int(raw_number)


def _classify_work_item(source_cache: Cache, item: CacheRebuildWorkItem) -> CacheRebuildWorkResult:
    try:
        pr = cached_or_live_pull_request(source_cache, item.repo, item.number)
        if pr is None:
            return CacheRebuildWorkResult(item=item, pr=None, error="could not load PR state")
        if _is_excluded_rebuild_author(item.repo, pr.author.login):
            return CacheRebuildWorkResult(item=item, pr=pr, skipped_excluded=True)
        evidence = live_evidence(item.repo, item.number, pr)
    except GhRetryExhausted as exc:
        return CacheRebuildWorkResult(item=item, pr=None, error=str(exc))
    return CacheRebuildWorkResult(item=item, pr=pr, actual=classify_closed_pr(pr, evidence))


def _record_work_result(
    result: CacheRebuildWorkResult,
    *,
    output_cache: Cache,
    divergences_by_key: dict[str, CacheDivergence],
    out_cache_file: Path,
    divergence_file: Path,
    now: datetime,
    checked: int,
    skipped: int,
    failed: int,
    save_every: int,
) -> tuple[int, int, int]:
    item = result.item
    author_login = result.pr.author.login if result.pr is not None and result.pr.author.login else item.author_login
    if result.pr is not None:
        _cache_rebuild_pull_request(output_cache, item.repo, item.number, result.pr)
    if result.skipped_excluded:
        skipped += 1
        divergences_by_key.pop(item.key, None)
        _write_pr_prefix(item.index, item.total, item.repo, item.number, author_login)
        CONSOLE.print(" skipped (excluded author)", style="dim")
        return checked, skipped, failed
    if result.actual is None:
        failed += 1
        _write_pr_prefix(item.index, item.total, item.repo, item.number, author_login)
        reason = result.error or "could not load PR state"
        CONSOLE.print(f" failed ({reason})", style="red")
        return checked, skipped, failed

    actual = result.actual
    set_cached_closed_classification(
        output_cache,
        repo=item.repo,
        number=item.number,
        classification=actual.classification,
        release=actual.release,
        via_label=actual.via_label,
        via_url=actual.via_url,
        evidence_kind=actual.evidence_kind,
        now=now,
    )
    should_save = save_every > 0 and (checked + 1) % save_every == 0
    save_suffix = " \\[saved]" if should_save else ""
    if not classification_entry_matches_result(item.expected, actual):
        divergences_by_key[item.key] = CacheDivergence(key=item.key, expected=item.expected, actual=actual)
        _write_pr_prefix(item.index, item.total, item.repo, item.number, author_login)
        CONSOLE.print(
            f" {actual.log_label} (DIVERGED from {item.expected.classification}/{item.expected.evidenceKind}){save_suffix}",
            style="red",
        )
    else:
        divergences_by_key.pop(item.key, None)
        write_pr_classification_progress(item.index, item.total, item.repo, item.number, author_login, f"{actual.log_label}{save_suffix}", actual.classification)
    checked += 1
    if should_save:
        _save_rebuild_progress(output_cache, out_cache_file, divergences_by_key, divergence_file)
    return checked, skipped, failed


def live_evidence(repo: str, number: int, pr: PullRequest) -> Evidence:
    comments = fetch_comments(repo, number)
    timeline_items = fetch_timeline(repo, number)
    comments_text = get_non_bot_comment_text(Evidence(comments=comments))
    reference_numbers = set(_timeline_source_numbers(timeline_items))
    reference_numbers.update(_referenced_numbers(comments_text))

    pull_states_by_pr: dict[int, PullRequestRef] = {}
    reference_text_by_pr: dict[int, str] = {}
    commit_author_logins_by_pr: dict[int, set[str]] = {}
    for referenced_number in sorted(reference_numbers):
        ref = live_pull_request_ref(repo, referenced_number)
        if ref is None:
            continue
        pull_states_by_pr[referenced_number] = ref
        reference_text_by_pr[referenced_number] = reference_text(repo, referenced_number, ref)
        if ref.state == "MERGED" or ref.mergedAt:
            commit_author_logins_by_pr[referenced_number] = commit_author_logins(repo, referenced_number)

    maintainers, integration_bots = repo_leaderboard_config(repo)
    return Evidence(
        comments=comments,
        timeline_items=timeline_items,
        reference_text_by_pr=reference_text_by_pr,
        pull_states_by_pr=pull_states_by_pr,
        commit_author_logins_by_pr=commit_author_logins_by_pr,
        maintainer_logins=set(maintainers),
        integration_bots=set(integration_bots),
        default_author_login=pr.author.login,
    )


def fetch_comments(repo: str, number: int) -> list[Comment]:
    raw = run_gh("api", f"repos/{repo}/issues/{number}/comments?per_page=100", suppress_errors=True)
    payload = json.loads(raw) if raw else []
    if not isinstance(payload, list):
        return []
    comments: list[Comment] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        user = item.get("user")
        login = user.get("login", "") if isinstance(user, dict) else ""
        comments.append(
            Comment(
                body=str(item.get("body") or ""),
                author=UserRef(login=str(login)),
                authorAssociation=str(item.get("author_association") or ""),
            ),
        )
    return comments


def fetch_timeline(repo: str, number: int) -> list[TimelineEvent]:
    raw = run_gh(
        "api",
        f"repos/{repo}/issues/{number}/timeline?per_page=100",
        "-H",
        "Accept: application/vnd.github+json",
        suppress_errors=True,
    )
    payload = json.loads(raw) if raw else []
    if not isinstance(payload, list):
        return []
    return [mapped for event in payload if isinstance(event, dict) for mapped in [_timeline_event(event)] if mapped is not None]


def cached_or_live_pull_request(cache: Cache, repo: str, number: int) -> PullRequest | None:
    key = classification_cache_key(repo, number)
    raw_state = cache.prPullStates.get(key)
    if raw_state is not None and raw_state.get("state") != "NOT_FOUND":
        author = raw_state.get("author") or cache.prAuthorsByNumber.get(key, "")
        return PullRequest(
            repo=repo,
            number=number,
            state=str(raw_state.get("state") or ""),
            merged=str(raw_state.get("state") or "") == "MERGED",
            mergedAt=str(raw_state.get("mergedAt") or ""),
            title=str(raw_state.get("title") or ""),
            url=f"https://github.com/{repo}/pull/{number}",
            author=UserRef(login=str(author)),
            body=str(raw_state.get("body") or ""),
        )
    ref = live_pull_request_ref(repo, number)
    if ref is None:
        return None
    return PullRequest(
        repo=repo,
        number=number,
        state=ref.state,
        merged=ref.state == "MERGED" or bool(ref.mergedAt),
        mergedAt=ref.mergedAt,
        title=ref.title,
        url=ref.url,
        author=ref.author,
        body=ref.body,
    )


def live_pull_request_ref(repo: str, number: int) -> PullRequestRef | None:
    raw = run_gh("pr", "view", str(number), "--repo", repo, "--json", "number,state,mergedAt,title,url,author,body", suppress_errors=True)
    if not raw:
        return None
    payload = json.loads(raw)
    author = payload.get("author")
    return PullRequestRef(
        number=int_value(payload.get("number"), default=number),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or f"https://github.com/{repo}/pull/{number}"),
        state=str(payload.get("state") or ""),
        merged=str(payload.get("state") or "") == "MERGED" or bool(payload.get("mergedAt")),
        mergedAt=str(payload.get("mergedAt") or ""),
        author=UserRef(login=str(author.get("login") or "") if isinstance(author, dict) else ""),
        body=str(payload.get("body") or ""),
    )


def reference_text(repo: str, number: int, ref: PullRequestRef) -> str:
    comments = fetch_comments(repo, number)
    return "\n---\n".join((ref.title, ref.body, get_non_bot_comment_text(Evidence(comments=comments))))


def commit_author_logins(repo: str, number: int) -> set[str]:
    raw = run_gh("pr", "view", str(number), "--repo", repo, "--json", "commits", suppress_errors=True)
    payload = json.loads(raw) if raw else {}
    commits = payload.get("commits") if isinstance(payload, dict) else []
    logins: set[str] = set()
    if not isinstance(commits, list):
        return logins
    for commit in commits:
        if not isinstance(commit, dict):
            continue
        authors = commit.get("authors")
        if not isinstance(authors, list):
            continue
        for author in authors:
            if isinstance(author, dict) and author.get("login"):
                logins.add(str(author["login"]))
    return logins


def _cache_rebuild_pull_request(cache: Cache, repo: str, number: int, pr: PullRequest) -> None:
    key = classification_cache_key(repo, number)
    if pr.author.login:
        cache.prAuthorsByNumber[key] = pr.author.login
    cache.prPullStates[key] = {
        "state": pr.state,
        "mergedAt": pr.mergedAt,
        "title": pr.title,
        "author": pr.author.login,
        "body": pr.body,
    }


def _entry_was_generated(output_cache: Cache, key: str, input_entry: ClassificationEntry) -> bool:
    output_entry = output_cache.entries.get(key)
    return output_entry is not None and output_entry.cachedAt != input_entry.cachedAt


def _timeline_event(event: dict[str, object]) -> TimelineEvent | None:
    event_name = str(event.get("event") or "")
    if event_name == "cross-referenced":
        issue = _source_issue(event)
        if issue is None or not isinstance(issue.get("pull_request"), dict):
            return None
        pull_request = issue["pull_request"]
        merged_at = str(pull_request.get("merged_at") or "") if isinstance(pull_request, dict) else ""
        source = PullRequestRef(
            number=int_value(issue.get("number")),
            title=str(issue.get("title") or ""),
            url=str(issue.get("html_url") or ""),
            state="MERGED" if merged_at else "CLOSED",
            merged=bool(merged_at),
            mergedAt=merged_at,
        )
        return TimelineEvent.model_validate({"__typename": "CrossReferencedEvent", "createdAt": str(event.get("created_at") or ""), "source": source})
    if event_name == "referenced" and event.get("commit_id"):
        return TimelineEvent.model_validate(
            {
                "__typename": "ReferencedEvent",
                "createdAt": str(event.get("created_at") or ""),
                "commit": {
                    "oid": str(event.get("commit_id") or ""),
                    "messageHeadline": "",
                    "url": str(event.get("commit_url") or ""),
                },
            },
        )
    if event_name == "closed":
        actor = event.get("actor")
        actor_login = str(actor.get("login") or "") if isinstance(actor, dict) else ""
        data: dict[str, object] = {"__typename": "ClosedEvent", "createdAt": str(event.get("created_at") or "")}
        if actor_login:
            data["actor"] = {"login": actor_login}
        return TimelineEvent.model_validate(data)
    return None


def _source_issue(event: dict[str, object]) -> dict[str, object] | None:
    source = event.get("source")
    if not isinstance(source, dict):
        return None
    issue = source.get("issue")
    return issue if isinstance(issue, dict) else None


def _timeline_source_numbers(items: list[TimelineEvent]) -> set[int]:
    return {item.source.number for item in items if item.source is not None and item.source.number > 0}


def _referenced_numbers(text: str) -> set[int]:
    seen: set[int] = set()
    for match in re.finditer(r"#(\d+)", text):
        number = int(match.group(1))
        if number in seen:
            continue
        if should_resolve_referenced_pull_request(text, number):
            seen.add(number)
        else:
            continue
    return seen


def _cached_author_login(cache: Cache, key: str) -> str:
    author = cache.prAuthorsByNumber.get(key, "")
    if author:
        return author
    raw_state = cache.prPullStates.get(key)
    if raw_state is None:
        return ""
    return str(raw_state.get("author") or "")


def _is_excluded_rebuild_author(repo: str, author_login: str) -> bool:
    if not author_login:
        return False
    return is_leaderboard_excluded_login(author_login, configured_repo_leaderboard_exclusions(repo))


def _progress(message: str, style: str) -> None:
    CONSOLE.print(message, style=style)


def _write_pr_prefix(index: int, total: int, repo: str, number: int, author_login: str) -> None:
    author = f", @{author_login}" if author_login else ""
    CONSOLE.print(f"  [{index}/{total}] #{number} ({repo.rsplit('/', 1)[-1]}{author})...", style="dim", end="")


def write_pr_classification_progress(index: int, total: int, repo: str, number: int, author_login: str, log_label: str, classification: str) -> None:
    _write_pr_prefix(index, total, repo, number, author_login)
    CONSOLE.print(f" {log_label}", style=_classification_style(classification))


def _save_rebuild_progress(
    output_cache: Cache,
    out_cache_file: Path,
    divergences_by_key: dict[str, CacheDivergence],
    divergence_file: Path,
) -> None:
    save_cache(output_cache, out_cache_file)
    write_divergence_report([divergences_by_key[key] for key in sorted(divergences_by_key)], divergence_file)


def _classification_style(classification: str) -> str:
    if classification == "shipped":
        return "green"
    if classification == "accepted-indirect":
        return "cyan"
    if classification == "superseded":
        return "yellow"
    if classification == "lost":
        return "red"
    if classification == "withdrawn":
        return "dim"
    return "white"
