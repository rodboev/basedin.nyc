"""Generate the pr-stats page (index.html) from cached GitHub PR data.

Usage:
    python generate.py [options]

Three modes, selected by flags:
    (default)                          Render index.html from the cache; refreshes
                                       leaderboards and release data as a side effect.
    --classify-cache                   Rebuild every closed-PR classification from
                                       scratch. Slow (4+ hours); needs explicit approval.
    --verify-webui-cached-credits-only Print the hermes-webui release-credit check and
                                       exit without rendering.

Options:
    --cache-file PATH        Classification cache to read/write
                             (default: .pr-classification-cache.json).
    --template-file PATH     Jinja2 shell to render (default: template.html).
    --out-file PATH          Rendered page path (default: index.html).
    --repos-file PATH        Active repo list, one owner/repo per line
                             (default: repos.txt).
    --author LOGIN           GitHub login whose PRs are reported (default: rodboev).
    --force-write            Write the page even if the sanity checks reject it.
    --silent                 Suppress progress output and the end-of-run pause.
    --workers N              Thread pool size for PR fetches (default: 4).
    --overlay-config-dir DIR pr-sweep overlay bundle root feeding maintainer lists
                             and leaderboard exclusions.

    Classification-rebuild only (--classify-cache):
    --out-cache-file PATH    Write the rebuilt cache here instead of promoting it in
                             place (default writes .pr-classification-cache.rebuild.json
                             and promotes it).
    --divergence-file PATH   Where to record classification divergences
                             (default: classification-divergences.json).
    --active-repos-only      Restrict the rebuild to repos.txt's active entries.
    --limit N                Cap the number of PRs reclassified.

    Credit verification only (--verify-webui-cached-credits-only):
    --changelog-file PATH    hermes-webui CHANGELOG override.
    --contributors-file PATH hermes-webui contributors file override.
    --verify-webui-credits-only  Alias for --verify-webui-cached-credits-only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from datetime import timedelta
from pathlib import Path

from jinja2 import TemplateError

from core.cache import classification_cache_key, load_cache, save_cache, set_cached_closed_classification
from core.leaderboard import CHANGELOG_RELEASE_PROFILE, DEFAULT_OVERLAY_CONFIG_DIR, fetch_community_leaderboard, repo_credit_profile, set_overlay_config_dir, warn_stale_leaderboards
from core.classify import ClassificationResult, classify_closed_pr
from core.classification_rebuild import CacheRebuildInterrupted, live_evidence, rebuild_classification_cache, write_pr_classification_progress
from core.models import Cache, ClassificationEntry, PullRequest, UserRef, int_value
from core.repos import resolve_canonical_repos, set_repo_display_names
from core.credit import cached_release_credit_counts
from core.releases import refresh_release_cache, release_for_pr
from core.github import GhPullRequestView, GhRetryExhausted, run_gh
from core.html import ReportSanityInput, write_report_if_sane
from core.page import (
    render_breakdown_section,
    render_leaderboard_sections,
    render_pr_bootstrap,
    render_pr_controls_and_table,
    render_report_page,
    render_repo_matrix_section,
    render_timeline_bootstrap,
)
from core.report import (
    EASTERN,
    PrReportItem,
    report_counts,
    report_item_from_pull_request_view,
    report_items_to_script_dicts,
    sort_report_items_by_effective_date,
    sort_repos_by_accepted_count,
)
from core.timeline import (
    breakdown_seed,
    build_chart_payload,
    build_daily_data,
    load_active_repos_from_text,
    load_pr_data_from_html,
    prepare_timeline_prs,
)

DEFAULT_AUTHOR = "rodboev"
DEFAULT_CACHE_FILE = Path(".pr-classification-cache.json")
DEFAULT_REBUILD_CACHE_FILE = Path(".pr-classification-cache.rebuild.json")
DEFAULT_DIVERGENCE_FILE = Path("classification-divergences.json")
AUTHOR_PULL_LOOKBACK_HOURS = 48
WITHDRAWN_RECHECK_DAYS = 14
WITHDRAWN_RECHECK_INTERVAL_HOURS = 6
AUTHOR_PULL_SEARCH_PAGE_SIZE = 100
AUTHOR_PULL_SEARCH_MAX_PAGES = 10
AUTHOR_PULL_GRAPHQL_QUERY = """\
query($searchQuery: String!, $pageSize: Int!, $cursor: String) {
  search(type: ISSUE, first: $pageSize, after: $cursor, query: $searchQuery) {
    issueCount
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on PullRequest {
        number
        state
        title
        createdAt
        updatedAt
        closedAt
        mergedAt
        headRefName
        additions
        deletions
        changedFiles
        url
        author { login }
        repository { nameWithOwner }
      }
    }
  }
}
"""
WEBUI_REPO = "nesquena/hermes-webui"
WEBUI_EXCLUDED_LOGINS = ("nesquena", "nesquena-hermes")
WEBUI_CREDIT_MINIMUMS = (
    ("franksong2702", 200),
    ("Michaelyklam", 100),
    ("rodboev", 150),
    ("ai-ag2026", 80),
)
WEBUI_CREDIT_RATIO_CHECKS = (
    ("rodboev", "franksong2702", 0.50),
    ("Michaelyklam", "franksong2702", 0.35),
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate pr-stats output.")
    parser.add_argument("--cache-file", type=Path, default=DEFAULT_CACHE_FILE)
    parser.add_argument("--template-file", type=Path, default=Path("template.html"))
    parser.add_argument("--out-file", type=Path, default=None)
    parser.add_argument("--repos-file", type=Path, default=Path("repos.txt"))
    parser.add_argument("--author", default=DEFAULT_AUTHOR)
    parser.add_argument("--classify-cache", action="store_true")
    parser.add_argument("--out-cache-file", type=Path, default=None)
    parser.add_argument("--divergence-file", type=Path, default=DEFAULT_DIVERGENCE_FILE)
    parser.add_argument("--active-repos-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--verify-webui-cached-credits-only", action="store_true")
    parser.add_argument("--verify-webui-credits-only", action="store_true", help="alias for --verify-webui-cached-credits-only during the rewrite")
    parser.add_argument("--changelog-file", type=Path, default=None)
    parser.add_argument("--contributors-file", type=Path, default=None)
    parser.add_argument("--force-write", action="store_true")
    parser.add_argument("--overlay-config-dir", type=Path, default=DEFAULT_OVERLAY_CONFIG_DIR)
    parser.add_argument("--silent", action="store_true", help="suppress progress output and end-of-run pause")
    args = parser.parse_args(argv)

    set_overlay_config_dir(args.overlay_config_dir)

    if args.verify_webui_cached_credits_only or args.verify_webui_credits_only:
        return verify_webui_credits_only(
            cache_file=args.cache_file,
            changelog_file=args.changelog_file,
            contributors_file=args.contributors_file,
        )
    if args.classify_cache:
        return classify_cache(
            cache_file=args.cache_file,
            out_cache_file=args.out_cache_file or DEFAULT_REBUILD_CACHE_FILE,
            divergence_file=args.divergence_file,
            repos_file=args.repos_file,
            active_repos_only=args.active_repos_only,
            limit=args.limit,
            workers=args.workers,
            promote_output=args.out_cache_file is None,
        )

    rc = generate_report(
        cache_file=args.cache_file,
        template_file=args.template_file,
        out_file=args.out_file or Path("index.html"),
        repos_file=args.repos_file,
        author=args.author,
        force_write=args.force_write,
        silent=args.silent,
        workers=args.workers,
    )
    if not args.silent:
        try:
            input("\nPress Enter to exit...")
        except (EOFError, KeyboardInterrupt):
            pass
    return rc

def classify_cache(
    *,
    cache_file: Path,
    out_cache_file: Path,
    divergence_file: Path,
    repos_file: Path,
    active_repos_only: bool,
    limit: int | None,
    workers: int,
    promote_output: bool = False,
) -> int:
    try:
        result = rebuild_classification_cache(
            cache_file=cache_file,
            out_cache_file=out_cache_file,
            divergence_file=divergence_file,
            repos_file=repos_file,
            active_repos_only=active_repos_only,
            limit=limit,
            workers=workers,
        )
        if promote_output and out_cache_file != cache_file:
            out_cache_file.replace(cache_file)
    except CacheRebuildInterrupted as exc:
        print(
            f"Interrupted after classifying {exc.result.checked} PRs, skipped {exc.result.skipped}, "
            f"failed {exc.result.failed}, divergences {exc.result.divergences}. "
            f"Checkpoint saved to {out_cache_file}",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0 if result.failed == 0 else 1

def generate_report(
    *,
    cache_file: Path,
    template_file: Path,
    out_file: Path,
    repos_file: Path,
    author: str = DEFAULT_AUTHOR,
    force_write: bool = False,
    silent: bool = False,
    workers: int = 4,
) -> int:
    def log(msg: str) -> None:
        if not silent:
            print(msg, file=sys.stderr)

    try:
        template_text = template_file.read_text(encoding="utf-8")
        # The page keeps showing each repo as written in repos.txt; everything behind it
        # (fetches, cache keys, leaderboards) keys on the repo's canonical name.
        canonical_by_entry = resolve_canonical_repos(
            load_active_repos_from_text(repos_file.read_text(encoding="utf-8")),
            workers=workers,
        )
        set_repo_display_names({canonical: entry for entry, canonical in canonical_by_entry.items()})
        repos = list(canonical_by_entry.values())
        cache = load_cache(cache_file)
        now = datetime.now(timezone.utc)
        log(f"Fetching PRs for {len(repos)} repos...")
        live_prs, failed_repos, author_cache_updated = fetch_author_pull_requests(
            repos,
            author=author,
            cache=cache,
            out_file=out_file,
            now=now,
            log=log,
            workers=workers,
        )
        log(f"Fetched {len(live_prs)} PRs ({len(failed_repos)} repos failed)" if failed_repos else f"Fetched {len(live_prs)} PRs")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cache_updated_early = False
    for repo in repos:
        if repo_credit_profile(repo) == CHANGELOG_RELEASE_PROFILE:
            if repo in cache.releaseData:
                del cache.releaseData[repo]
                cache_updated_early = True
            continue
        author_prs = {pr.number for r, pr in live_prs if r == repo}
        try:
            if refresh_release_cache(repo, cache, author_pr_numbers=author_prs, now=now):
                cache_updated_early = True
        except GhRetryExhausted:
            pass
    for repo in repos:
        try:
            refresh = fetch_community_leaderboard(repo, cache, now=now)
            if refresh.status in {"failed", "cooldown"}:
                print(f"WARNING: leaderboard refresh {refresh.status} for {repo}: {refresh.reason}", file=sys.stderr)
            if refresh.cache_updated:
                cache_updated_early = True
        except GhRetryExhausted as exc:
            print(f"WARNING: leaderboard refresh failed for {repo}: {exc}", file=sys.stderr)
    warn_stale_leaderboards(repos, cache, now=now)
    try:
        typed_items, cache_updated = report_items_from_live_pull_requests(live_prs, cache, now=now)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    typed_items, release_backfill_updated = backfill_release_data(typed_items, cache, now=now)
    cache_updated = cache_updated or release_backfill_updated
    if cache_updated or cache_updated_early or author_cache_updated:
        try:
            save_cache(cache, cache_file)
        except OSError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    typed_items = sort_report_items_by_effective_date(typed_items)
    pr_items = report_items_to_script_dicts(typed_items)
    display_repos = sort_repos_by_accepted_count(repos, pr_items, accepted_classifications=("shipped", "accepted-indirect"))
    counts = report_counts(
        typed_items,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )
    report_cache_keys = {
        classification_cache_key(str(item.get("repo", "")), int_value(item.get("number")))
        for item in pr_items
    }
    try:
        all_prs = prepare_timeline_prs(pr_items)
        chart_data, repo_data, repo_names = build_daily_data(all_prs, repos)
        chart_json, repo_json, names_json = build_chart_payload(chart_data, repo_data, repo_names)
        now_eastern = now.astimezone(EASTERN)
        today_label = now_eastern.strftime("%Y-%m-%d")
        # Rendered at the load animation's first frame, not the all-time totals, so the page does not
        # jump the moment timeline.js starts lerping. The settled all-time state is JS's to fill in.
        seed = breakdown_seed(chart_data, today_label)
        context = {
            "breakdown": render_breakdown_section(
                seed.counts,
                seed.activity,
                avg_prs=seed.avg_prs,
                avg_loc=seed.avg_loc,
            ),
            "timeline_bootstrap": render_timeline_bootstrap(chart_json, repo_json, names_json, today_label),
            "today": today_label,
            "repo_matrix": render_repo_matrix_section(
                repos=display_repos,
                items=typed_items,
                cache=cache,
                now=now,
                author=author,
            ),
            "leaderboard_sections": render_leaderboard_sections(
                repos=display_repos,
                items=typed_items,
                cache=cache,
                now=now,
                author=author,
            ),
            "pr_controls": render_pr_controls_and_table(items=typed_items, display_repos=display_repos, visible_items=40),
            "pr_bootstrap": render_pr_bootstrap(items=typed_items),
            "generated_date": f"{now_eastern.strftime('%B')} {now_eastern.day}, {now_eastern.year}",
        }
        output_html = render_report_page(template_text, context)
        issues = write_report_if_sane(
            out_file=out_file,
            html=output_html,
            report=ReportSanityInput(
                reported_count=counts.total,
                fetched_count=len(pr_items),
                acceptance_closed=counts.accepted + counts.not_shipped,
                open_count=counts.open,
                failed_repos=failed_repos,
                repo_count=len(repos),
                cached_classification_count=sum(1 for key in report_cache_keys if key in cache.entries),
            ),
            force_write=force_write,
        )
    except (OSError, ValueError, json.JSONDecodeError, TemplateError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"Generated report at {out_file}", file=sys.stderr)
    return 0

def fetch_author_pull_requests(
    repos: list[str],
    *,
    author: str,
    cache: Cache,
    out_file: Path,
    now: datetime,
    log: Callable[[str], None] = lambda _: None,
    workers: int = 4,
) -> tuple[list[tuple[str, GhPullRequestView]], tuple[str, ...], bool]:
    cache_updated = seed_author_pull_cache_from_html(cache, out_file=out_file, repos=repos, author=author)

    def fetch_one(repo: str) -> tuple[str, list[dict[str, object]] | None]:
        try:
            return repo, fetch_author_pull_deltas(repo, author=author, cache=cache, out_file=out_file, now=now)
        except GhRetryExhausted:
            return repo, None

    failed_repos: list[str] = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_one, repo): repo for repo in repos}
        for future in as_completed(futures):
            repo, rows = future.result()
            done += 1
            if rows is None:
                failed_repos.append(repo)
                count = len(_cached_author_pull_views(cache, repo))
            else:
                for row in rows:
                    cache.authorPulls[classification_cache_key(repo, int_value(row.get("number")))] = row
                cache.authorPullScanMeta[repo] = {
                    "scannedAt": _format_datetime(now),
                    "source": "graphql-search",
                    "lookbackHours": AUTHOR_PULL_LOOKBACK_HOURS,
                }
                cache_updated = True
                count = len(_cached_author_pull_views(cache, repo))
            log(f"  [{done}/{len(repos)}] {repo}: {count} PRs")

    pulls: list[tuple[str, GhPullRequestView]] = []
    for repo in repos:
        for view in _cached_author_pull_views(cache, repo):
            pulls.append((repo, view))
    return pulls, tuple(failed_repos), cache_updated


def seed_author_pull_cache_from_html(cache: Cache, *, out_file: Path, repos: list[str], author: str) -> bool:
    if not out_file.exists():
        return False
    try:
        items = load_pr_data_from_html(out_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    active_repos = set(repos)
    changed = False
    for item in items:
        repo = str(item.get("repo") or "")
        number = int_value(item.get("number"))
        if repo not in active_repos or number <= 0:
            continue
        key = classification_cache_key(repo, number)
        if key in cache.authorPulls:
            continue
        cache.authorPulls[key] = _author_pull_row_from_script_item(item, author=author)
        changed = True
    return changed


def fetch_author_pull_deltas(
    repo: str,
    *,
    author: str,
    cache: Cache,
    out_file: Path,
    now: datetime,
) -> list[dict[str, object]] | None:
    queries = [f"repo:{repo} is:pr author:{author} is:open"]
    cutoff = _author_pull_cutoff(cache, repo=repo, out_file=out_file)
    if cutoff:
        queries.append(f"repo:{repo} is:pr author:{author} updated:>={cutoff}")
    else:
        queries = [f"repo:{repo} is:pr author:{author}"]

    rows_by_key: dict[str, dict[str, object]] = {}
    for query in queries:
        rows = _fetch_author_pull_search_rows(query)
        if rows is None:
            return None
        for row in rows:
            row_repo = str(row.get("repo") or "")
            number = int_value(row.get("number"))
            if row_repo != repo or number <= 0:
                continue
            rows_by_key[classification_cache_key(repo, number)] = row
    if not rows_by_key and not _cached_author_pull_views(cache, repo):
        return []
    return list(rows_by_key.values())


def _fetch_author_pull_search_rows(query: str) -> list[dict[str, object]] | None:
    rows: list[dict[str, object]] = []
    cursor = ""
    for _page in range(AUTHOR_PULL_SEARCH_MAX_PAGES):
        args = [
            "api", "graphql",
            "-f", f"query={AUTHOR_PULL_GRAPHQL_QUERY}",
            "-F", f"searchQuery={query}",
            "-F", f"pageSize={AUTHOR_PULL_SEARCH_PAGE_SIZE}",
        ]
        if cursor:
            args.extend(["-F", f"cursor={cursor}"])
        raw = run_gh(*args, suppress_errors=True)
        if not raw:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        search = _graphql_search_connection(payload)
        if search is None:
            return None
        nodes = search.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    row = _author_pull_row_from_graphql_node(node)
                    if row is not None:
                        rows.append(row)
        page_info = search.get("pageInfo")
        if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
            return rows
        cursor = str(page_info.get("endCursor") or "")
        if not cursor:
            return rows
    return None


def _graphql_search_connection(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    search = data.get("search")
    return search if isinstance(search, dict) else None


def _author_pull_cutoff(cache: Cache, *, repo: str, out_file: Path) -> str:
    meta = cache.authorPullScanMeta.get(repo)
    scanned_at = str(meta.get("scannedAt") or "") if isinstance(meta, dict) else ""
    scanned = _parse_datetime(scanned_at)
    if scanned is not None:
        return _format_datetime(scanned - timedelta(hours=AUTHOR_PULL_LOOKBACK_HOURS))
    if any(key.startswith(f"{repo}#") for key in cache.authorPulls):
        try:
            mtime = datetime.fromtimestamp(out_file.stat().st_mtime, timezone.utc)
        except OSError:
            return ""
        return _format_datetime(mtime - timedelta(hours=AUTHOR_PULL_LOOKBACK_HOURS))
    return ""


def _cached_author_pull_views(cache: Cache, repo: str) -> list[GhPullRequestView]:
    views: list[GhPullRequestView] = []
    prefix = f"{repo}#"
    for key, raw in cache.authorPulls.items():
        if not key.startswith(prefix):
            continue
        view = _author_pull_view_from_cache_row(raw)
        if view is not None:
            views.append(view)
    return sorted(views, key=lambda item: _parse_datetime(item.updatedAt or item.createdAt) or datetime.min.replace(tzinfo=timezone.utc), reverse=True)


def _author_pull_view_from_cache_row(raw: object) -> GhPullRequestView | None:
    if not isinstance(raw, dict):
        return None
    try:
        return GhPullRequestView.model_validate(raw)
    except ValueError:
        return None


def _author_pull_row_from_graphql_node(node: dict[str, object]) -> dict[str, object] | None:
    repo = _graphql_repo_name(node)
    number = int_value(node.get("number"))
    if not repo or number <= 0:
        return None
    author = node.get("author")
    login = str(author.get("login") or "") if isinstance(author, dict) else ""
    return {
        "repo": repo,
        "number": number,
        "state": str(node.get("state") or ""),
        "title": str(node.get("title") or ""),
        "createdAt": str(node.get("createdAt") or ""),
        "updatedAt": str(node.get("updatedAt") or ""),
        "closedAt": str(node.get("closedAt") or "") or None,
        "mergedAt": str(node.get("mergedAt") or "") or None,
        "headRefName": str(node.get("headRefName") or ""),
        "author": {"login": login},
        "additions": int_value(node.get("additions")),
        "deletions": int_value(node.get("deletions")),
        "changedFiles": int_value(node.get("changedFiles")),
        "url": str(node.get("url") or f"https://github.com/{repo}/pull/{number}"),
    }


def _graphql_repo_name(node: dict[str, object]) -> str:
    repository = node.get("repository")
    if not isinstance(repository, dict):
        return ""
    return str(repository.get("nameWithOwner") or "")


def _author_pull_row_from_script_item(item: dict[str, object], *, author: str) -> dict[str, object]:
    repo = str(item.get("repo") or "")
    number = int_value(item.get("number"))
    merged_at = str(item.get("mergedAt") or "")
    closed_at = str(item.get("closedAt") or "")
    classification = str(item.get("classification") or "")
    if classification == "open":
        state = "OPEN"
    elif merged_at:
        state = "MERGED"
    else:
        state = "CLOSED"
    return {
        "repo": repo,
        "number": number,
        "state": state,
        "title": str(item.get("title") or ""),
        "createdAt": str(item.get("createdAt") or ""),
        "updatedAt": "",
        "closedAt": closed_at or None,
        "mergedAt": merged_at or None,
        "headRefName": "",
        "author": {"login": author},
        "additions": int_value(item.get("additions")),
        "deletions": int_value(item.get("deletions")),
        "changedFiles": int_value(item.get("changedFiles")),
        "url": str(item.get("url") or f"https://github.com/{repo}/pull/{number}"),
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidate = value
    if candidate.endswith("Z"):
        candidate = f"{candidate[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _format_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        return value.isoformat()
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def report_items_from_live_pull_requests(
    pulls: list[tuple[str, GhPullRequestView]],
    cache: Cache,
    *,
    now: datetime,
) -> tuple[list[PrReportItem], bool]:
    items: list[PrReportItem] = []
    cache_updated = False
    classification_total = sum(1 for repo, pr in pulls if _needs_live_classification(repo, pr, cache, now))
    classified_count = 0
    for repo, pr in pulls:
        should_log_classification = _needs_live_classification(repo, pr, cache, now)
        classification, did_update_cache = live_pull_request_classification(repo, pr, cache, now=now)
        if should_log_classification and did_update_cache:
            classified_count += 1
            write_pr_classification_progress(classified_count, classification_total, repo, pr.number, pr.author.login, classification.log_label, classification.classification)
        cache_updated = cache_updated or did_update_cache
        items.append(report_item_from_pull_request_view(repo=repo, pr=pr, classification=classification))
    return items, cache_updated


def backfill_release_data(
    items: list[PrReportItem],
    cache: Cache,
    *,
    now: datetime,
) -> tuple[list[PrReportItem], bool]:
    updated = False
    result: list[PrReportItem] = []
    for item in items:
        rel = release_for_pr(cache, item.repo, item.number)
        if rel is None:
            result.append(item)
            continue
        tag_name, release_url = rel
        if item.statusKey == "shipped" and not item.releaseLabel:
            item = replace(item, releaseLabel=tag_name, releaseUrl=release_url)
            entry = cache.entries.get(classification_cache_key(item.repo, item.number))
            if entry is not None and not entry.release:
                set_cached_closed_classification(
                    cache,
                    repo=item.repo,
                    number=item.number,
                    classification=entry.classification,
                    release=tag_name,
                    via_label=entry.viaLabel,
                    via_url=entry.viaUrl,
                    evidence_kind=entry.evidenceKind,
                    now=now,
                )
                updated = True
        elif item.classification in ("lost", "withdrawn"):
            item = replace(
                item,
                classification="shipped",
                statusKey="shipped",
                statusLabel="Shipped",
                statusClass="tag-shipped",
                releaseLabel=tag_name,
                releaseUrl=release_url,
                evidenceKind="release-note",
            )
            set_cached_closed_classification(
                cache,
                repo=item.repo,
                number=item.number,
                classification="shipped",
                release=tag_name,
                via_label=item.viaLabel,
                via_url=item.viaUrl,
                evidence_kind="release-note",
                now=now,
            )
            updated = True
        result.append(item)
    return result, updated


def _needs_live_classification(repo: str, pr: GhPullRequestView, cache: Cache, now: datetime) -> bool:
    if pr.state == "OPEN":
        return False
    key = classification_cache_key(repo, pr.number)
    entry = cache.entries.get(key)
    if entry is None or not entry.classification:
        return True
    return _withdrawn_recheck_due(entry, pr, now)


def _withdrawn_recheck_due(entry: ClassificationEntry, pr: GhPullRequestView, now: datetime) -> bool:
    # A withdrawal can be provisional: the author closed in favor of a takeover PR
    # that has not merged yet. Keep reclassifying live until the close is 14 days
    # old so a replacement that lands with credit upgrades the entry; after that
    # the withdrawal is settled and the cache entry is final. Within that window,
    # throttle to WITHDRAWN_RECHECK_INTERVAL_HOURS against cachedAt (rewritten on
    # every recheck) so a run does not reclassify the same young withdrawal twice.
    if entry.classification != "withdrawn":
        return False
    closed_at = _parse_datetime(pr.closedAt)
    if closed_at is None or now - closed_at >= timedelta(days=WITHDRAWN_RECHECK_DAYS):
        return False
    cached_at = _parse_datetime(entry.cachedAt)
    if cached_at is None:
        return True
    return now - cached_at >= timedelta(hours=WITHDRAWN_RECHECK_INTERVAL_HOURS)


def live_pull_request_classification(
    repo: str,
    pr: GhPullRequestView,
    cache: Cache,
    *,
    now: datetime,
) -> tuple[ClassificationResult, bool]:
    key = classification_cache_key(repo, pr.number)
    entry = cache.entries.get(key)
    if pr.state == "OPEN":
        # A cached closed classification on a PR that is open again means the PR
        # was reopened; drop the stale entry so the next close reclassifies.
        stale = entry is not None and bool(entry.classification)
        cache.entries.pop(key, None)
        return ClassificationResult(classification="open", evidence_kind="open", log_label="open"), stale
    if entry is not None and entry.classification and not _withdrawn_recheck_due(entry, pr, now):
        return (
            ClassificationResult(
                classification=entry.classification,
                release=entry.release,
                via_label=entry.viaLabel,
                via_url=entry.viaUrl,
                evidence_kind=entry.evidenceKind,
                from_cache=True,
                log_label=entry.classification,
            ),
            False,
        )
    if pr.state == "MERGED" or pr.mergedAt:
        result = ClassificationResult(
            classification="shipped",
            via_label="direct",
            via_url=pr.url or f"https://github.com/{repo}/pull/{pr.number}",
            evidence_kind="direct-merge",
            log_label="shipped (merged directly)",
        )
    else:
        pull_request = _pull_request_from_view(repo, pr)
        result = classify_closed_pr(pull_request, live_evidence(repo, pr.number, pull_request))
    set_cached_closed_classification(
        cache,
        repo=repo,
        number=pr.number,
        classification=result.classification,
        release=result.release,
        via_label=result.via_label,
        via_url=result.via_url,
        evidence_kind=result.evidence_kind,
        now=now,
    )
    return result, True


def _pull_request_from_view(repo: str, pr: GhPullRequestView) -> PullRequest:
    return PullRequest(
        repo=repo,
        repoShort=repo.rsplit("/", 1)[-1],
        number=pr.number,
        title=pr.title,
        url=pr.url or f"https://github.com/{repo}/pull/{pr.number}",
        state=pr.state,
        merged=pr.state == "MERGED" or bool(pr.mergedAt),
        mergedAt=pr.mergedAt or "",
        closedAt=pr.closedAt or "",
        author=UserRef(login=pr.author.login),
        body=pr.body,
    )

def verify_webui_credits_only(
    *,
    cache_file: Path,
    changelog_file: Path | None,
    contributors_file: Path | None,
) -> int:
    cache = load_cache(cache_file)
    if changelog_file is None:
        stale_reason = _live_release_credit_cache_stale_reason(cache, WEBUI_REPO)
        if stale_reason:
            print(stale_reason, file=sys.stderr)
            return 1
    changelog_text = _read_fixture_or_repo_file(changelog_file, WEBUI_REPO, "CHANGELOG.md")
    contributors_text = _read_fixture_or_repo_file(contributors_file, WEBUI_REPO, "CONTRIBUTORS.md")
    counts = cached_release_credit_counts(
        cache=cache,
        repo=WEBUI_REPO,
        changelog_text=changelog_text,
        contributors_text=contributors_text,
        excluded_logins=WEBUI_EXCLUDED_LOGINS,
    )

    failed = False
    for login, minimum in WEBUI_CREDIT_MINIMUMS:
        value = counts.get(login, 0)
        ok = value >= minimum
        print(f"{login}: {value} (expected >= {minimum}) {'OK' if ok else 'FAIL'}")
        failed = failed or not ok
    for numerator_login, denominator_login, minimum_ratio in WEBUI_CREDIT_RATIO_CHECKS:
        numerator = counts.get(numerator_login, 0)
        denominator = counts.get(denominator_login, 0)
        ratio = numerator / denominator if denominator else 0.0
        ok = ratio >= minimum_ratio
        print(
            f"{numerator_login}/{denominator_login}: {ratio:.2f} "
            f"(expected >= {minimum_ratio:.2f}) {'OK' if ok else 'FAIL'}",
        )
        failed = failed or not ok
    return 1 if failed else 0

def _read_fixture_or_repo_file(path: Path | None, repo: str, file_path: str) -> str:
    if path is not None:
        return path.read_text(encoding="utf-8")
    return run_gh(
        "api",
        f"https://api.github.com/repos/{repo}/contents/{file_path}",
        "-H",
        "Accept: application/vnd.github.raw",
    )

def _live_release_credit_cache_stale_reason(cache: Cache, repo: str) -> str:
    current_sha = run_gh(
        "api",
        f"https://api.github.com/repos/{repo}/contents/CHANGELOG.md",
        "--jq",
        ".sha",
    ).strip()
    expected_sha = _cached_release_credit_changelog_sha(cache, repo)
    if not expected_sha:
        return f"release credit cache for {repo} has no changelog SHA; rebuild is not implemented in Python yet"
    if current_sha != expected_sha:
        return (
            f"release credit cache for {repo} is stale: cached CHANGELOG SHA {expected_sha}, "
            f"current SHA {current_sha}; rebuild is not implemented in Python yet"
        )
    return ""

def _cached_release_credit_changelog_sha(cache: Cache, repo: str) -> str:
    entry = cache.leaderboards.get(f"{repo}|community-shipped-v4|all")
    if entry is None:
        return ""
    meta = entry.get("releaseCreditMeta")
    if not isinstance(meta, dict):
        return ""
    value = meta.get("changelogSha")
    return value if isinstance(value, str) else ""

if __name__ == "__main__":
    sys.exit(main())
