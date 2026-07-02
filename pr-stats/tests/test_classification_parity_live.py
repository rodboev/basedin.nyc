from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable

import pytest

from core.cache import load_cache
from core.classify import ClassificationResult, classify_closed_pr, get_non_bot_comment_text, should_resolve_referenced_pull_request
from core.github import run_gh
from core.models import Cache, Comment, Evidence, PullRequest, PullRequestRef, TimelineEvent

REPO_MAINTAINERS: dict[str, tuple[str, ...]] = {
    "nesquena/hermes-webui": ("nesquena",),
    "kenn-io/agentsview": ("wesm", "mariusvniekerk", "cpcloud"),
    "thedotmack/claude-mem": ("thedotmack",),
    "stablyai/orca": ("nwparker", "AmethystLiang", "Jinwoo-H", "brennanb2025", "tmchow"),
    "mem0ai/mem0": ("taranjeet", "deshraj", "kartik-mem0", "chaithanyak42", "prathameshagrawal", "agumpandey"),
}
REPO_INTEGRATION_BOTS: dict[str, tuple[str, ...]] = {
    "nesquena/hermes-webui": ("nesquena-hermes",),
    "stablyai/orca": ("buf0-bot[bot]",),
}
ACCEPTED_CLASSIFICATION_DIVERGENCES: dict[str, str] = {
    "headroomlabs-ai/headroom#102": "cached before positive-context sibling credit tightened; current #107 text says continuation without credit vocabulary",
    "kenn-io/agentsview#15": "cached accepted via #18, but current evidence only has author superseded text and no positive sibling credit context",
    "kenn-io/agentsview#200": "cached accepted via #210, but current #210 body says Supersedes #200 and is negative under current context rules",
    "mastra-ai/mastra#17781": "cached as plain withdrawn before author follow-up evidence was present; current ladder records author-withdrawn evidence kind",
    "nesquena/hermes-webui#3444": "cached shipped before broad duplicate handling; current technical discussion contains duplicate provider group before shipped fallback",
    "nesquena/hermes-webui#3345": "cached as shipped before broad author-close handling; current author comment contains closeLiveStream and trips author-withdrawn first",
    "nesquena/hermes-webui#3997": "cached superseded before maintainer carry-forward credit; current comment credits shipped #4230 with co-author preservation",
    "nesquena/hermes-webui#4329": "cached superseded before maintainer carry-forward credit; current comment credits shipped #4332 with co-author preservation",
    "nesquena/hermes-webui#4573": "cached superseded before positive release reference handling; current release #4610 positively references #4573",
    "nesquena/hermes-webui#1001": "cached release credit came from #1031 batch context, but current body has range #1000-#1002 and no exact #1001 reference",
}

@pytest.mark.live
def test_live_classification_replay_matches_cached_powershell_results(live_cache_path: object) -> None:
    cache = load_cache(live_cache_path)
    mismatches: list[str] = []
    checked = 0
    limit = _optional_int(os.environ.get("PR_STATS_CLASSIFICATION_PARITY_LIMIT"))
    selected_keys = _optional_key_filter(os.environ.get("PR_STATS_CLASSIFICATION_PARITY_KEYS"))

    for key, expected in sorted(cache.entries.items()):
        if selected_keys is not None and key not in selected_keys:
            continue
        repo, number = _split_cache_key(key)
        pr = _cached_or_live_pr(cache, repo, number)
        if pr is None:
            mismatches.append(f"{key}: could not load PR state")
            continue
        evidence = _live_evidence(repo, number, pr)
        actual = classify_closed_pr(pr, evidence)
        if not _classification_matches(actual, expected.classification, expected.evidenceKind, expected.viaLabel, expected.viaUrl, expected.release):
            if key in ACCEPTED_CLASSIFICATION_DIVERGENCES:
                continue
            mismatches.append(
                f"{key}: expected {(expected.classification, expected.evidenceKind, expected.viaLabel, expected.viaUrl, expected.release)} "
                f"got {(actual.classification, actual.evidence_kind, actual.via_label, actual.via_url, actual.release)}",
            )
        checked += 1
        if limit is not None and checked >= limit:
            break

    assert mismatches == []

def _live_evidence(repo: str, number: int, pr: PullRequest) -> Evidence:
    comments = _fetch_comments(repo, number)
    timeline_items = _fetch_timeline(repo, number)
    comments_text = get_non_bot_comment_text(Evidence(comments=comments))
    reference_numbers = set(_timeline_source_numbers(timeline_items))
    reference_numbers.update(_referenced_numbers(comments_text))

    pull_states_by_pr: dict[int, PullRequestRef] = {}
    reference_text_by_pr: dict[int, str] = {}
    commit_author_logins_by_pr: dict[int, set[str]] = {}
    for referenced_number in sorted(reference_numbers):
        ref = _live_pull_request_ref(repo, referenced_number)
        if ref is None:
            continue
        pull_states_by_pr[referenced_number] = ref
        reference_text_by_pr[referenced_number] = _reference_text(repo, referenced_number, ref)
        if ref.state == "MERGED" or ref.mergedAt:
            commit_author_logins_by_pr[referenced_number] = _commit_author_logins(repo, referenced_number)

    return Evidence(
        comments=comments,
        timeline_items=timeline_items,
        reference_text_by_pr=reference_text_by_pr,
        pull_states_by_pr=pull_states_by_pr,
        commit_author_logins_by_pr=commit_author_logins_by_pr,
        maintainer_logins=set(REPO_MAINTAINERS.get(repo, ())),
        integration_bots=set(REPO_INTEGRATION_BOTS.get(repo, ())),
        default_author_login=pr.author.login,
    )

def _fetch_comments(repo: str, number: int) -> list[Comment]:
    try:
        raw = run_gh("api", f"repos/{repo}/issues/{number}/comments?per_page=100", suppress_errors=True)
    except Exception:
        return []
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
                author={"login": str(login)},
                authorAssociation=str(item.get("author_association") or ""),
            ),
        )
    return comments

def _fetch_timeline(repo: str, number: int) -> list[TimelineEvent]:
    try:
        raw = run_gh(
            "api",
            f"repos/{repo}/issues/{number}/timeline?per_page=100",
            "-H",
            "Accept: application/vnd.github+json",
            suppress_errors=True,
        )
    except Exception:
        return []
    payload = json.loads(raw) if raw else []
    if not isinstance(payload, list):
        return []
    items: list[TimelineEvent] = []
    for event in payload:
        if not isinstance(event, dict):
            continue
        mapped = _timeline_event(repo, event)
        if mapped is not None:
            items.append(mapped)
    return items

def _timeline_event(repo: str, event: dict[str, object]) -> TimelineEvent | None:
    event_name = str(event.get("event") or "")
    if event_name == "cross-referenced":
        issue = _source_issue(event)
        if issue is None or not isinstance(issue.get("pull_request"), dict):
            return None
        pull_request = issue["pull_request"]
        merged_at = str(pull_request.get("merged_at") or "") if isinstance(pull_request, dict) else ""
        source = PullRequestRef(
            number=int(issue.get("number") or 0),
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
        return TimelineEvent.model_validate({"__typename": "ClosedEvent", "createdAt": str(event.get("created_at") or "")})
    return None

def _source_issue(event: dict[str, object]) -> dict[str, object] | None:
    source = event.get("source")
    if not isinstance(source, dict):
        return None
    issue = source.get("issue")
    return issue if isinstance(issue, dict) else None

def _cached_or_live_pr(cache: Cache, repo: str, number: int) -> PullRequest | None:
    key = f"{repo}#{number}"
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
            author={"login": str(author)},
            body=str(raw_state.get("body") or ""),
        )
    ref = _live_pull_request_ref(repo, number)
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

def _live_pull_request_ref(repo: str, number: int) -> PullRequestRef | None:
    try:
        raw = run_gh("pr", "view", str(number), "--repo", repo, "--json", "number,state,mergedAt,title,url,author,body", suppress_errors=True)
    except Exception:
        return None
    if not raw:
        return None
    payload = json.loads(raw)
    author = payload.get("author")
    return PullRequestRef(
        number=int(payload.get("number") or number),
        title=str(payload.get("title") or ""),
        url=str(payload.get("url") or f"https://github.com/{repo}/pull/{number}"),
        state=str(payload.get("state") or ""),
        merged=str(payload.get("state") or "") == "MERGED" or bool(payload.get("mergedAt")),
        mergedAt=str(payload.get("mergedAt") or ""),
        author={"login": str(author.get("login") or "") if isinstance(author, dict) else ""},
        body=str(payload.get("body") or ""),
    )

def _reference_text(repo: str, number: int, ref: PullRequestRef) -> str:
    comments = _fetch_comments(repo, number)
    return "\n---\n".join((ref.title, ref.body, get_non_bot_comment_text(Evidence(comments=comments))))

def _commit_author_logins(repo: str, number: int) -> set[str]:
    try:
        raw = run_gh("pr", "view", str(number), "--repo", repo, "--json", "commits", suppress_errors=True)
    except Exception:
        return set()
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

def _timeline_source_numbers(items: Iterable[TimelineEvent]) -> Iterable[int]:
    for item in items:
        if item.source is not None and item.source.number > 0:
            yield item.source.number

def _referenced_numbers(text: str) -> Iterable[int]:
    seen: set[int] = set()
    for match in re.finditer(r"#(\d+)", text):
        number = int(match.group(1))
        if number in seen:
            continue
        seen.add(number)
        if should_resolve_referenced_pull_request(text, number):
            yield number

def _classification_matches(
    actual: ClassificationResult,
    classification: str,
    evidence_kind: str,
    via_label: str,
    via_url: str,
    release: str,
) -> bool:
    return (
        actual.classification == classification
        and actual.evidence_kind == evidence_kind
        and actual.via_label == via_label
        and actual.via_url == via_url
        and actual.release == release
    )

def _split_cache_key(key: str) -> tuple[str, int]:
    repo, raw_number = key.rsplit("#", 1)
    return repo, int(raw_number)

def _optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None

def _optional_key_filter(value: str | None) -> set[str] | None:
    if not value:
        return None
    keys = {item.strip() for item in value.split(",") if item.strip()}
    return keys if keys else None
