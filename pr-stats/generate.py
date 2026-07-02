from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.cache import load_cache
from core.classification_rebuild import CacheRebuildInterrupted, rebuild_classification_cache
from core.models import Cache
from core.credit import cached_release_credit_counts
from core.github import run_gh
from core.html import ReportSanityInput, write_report_if_sane
from core.page import compose_report_html_from_snapshot
from core.report import report_activity_summary, report_counts, report_items_from_script_dicts, sort_repos_by_accepted_count
from core.timeline import build_chart_payload, build_daily_data, inject_timeline_chart, load_active_repos_from_text, load_pr_data_from_html, prepare_timeline_prs

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
    parser.add_argument("--cache-file", type=Path, default=Path(".pr-classification-cache.json"))
    parser.add_argument("--in-file", type=Path, default=Path("index.html"))
    parser.add_argument("--out-file", type=Path, default=None)
    parser.add_argument("--repos-file", type=Path, default=Path("generate.ps1"))
    parser.add_argument("--inject-timeline-only", action="store_true")
    parser.add_argument("--classify-cache", action="store_true")
    parser.add_argument("--out-cache-file", type=Path, default=None)
    parser.add_argument("--divergence-file", type=Path, default=Path(".rewrite-scratch/classification-divergences.json"))
    parser.add_argument("--active-repos-only", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--verify-webui-cached-credits-only", action="store_true")
    parser.add_argument("--verify-webui-credits-only", action="store_true", help="alias for --verify-webui-cached-credits-only during the rewrite")
    parser.add_argument("--changelog-file", type=Path, default=None)
    parser.add_argument("--contributors-file", type=Path, default=None)
    parser.add_argument("--force-write", action="store_true")
    args = parser.parse_args(argv)

    if args.verify_webui_cached_credits_only or args.verify_webui_credits_only:
        return verify_webui_credits_only(
            cache_file=args.cache_file,
            changelog_file=args.changelog_file,
            contributors_file=args.contributors_file,
        )
    if args.inject_timeline_only:
        return inject_timeline_only(
            in_file=args.in_file,
            out_file=args.out_file or args.in_file,
            repos_file=args.repos_file,
        )
    if args.classify_cache:
        if args.out_cache_file is None:
            print("ERROR: --classify-cache requires --out-cache-file", file=sys.stderr)
            return 2
        return classify_cache(
            cache_file=args.cache_file,
            out_cache_file=args.out_cache_file,
            divergence_file=args.divergence_file,
            repos_file=args.repos_file,
            active_repos_only=args.active_repos_only,
            limit=args.limit,
        )

    return generate_report(
        cache_file=args.cache_file,
        in_file=args.in_file,
        out_file=args.out_file or args.in_file,
        repos_file=args.repos_file,
        force_write=args.force_write,
    )

def classify_cache(
    *,
    cache_file: Path,
    out_cache_file: Path,
    divergence_file: Path,
    repos_file: Path,
    active_repos_only: bool,
    limit: int | None,
) -> int:
    try:
        result = rebuild_classification_cache(
            cache_file=cache_file,
            out_cache_file=out_cache_file,
            divergence_file=divergence_file,
            repos_file=repos_file,
            active_repos_only=active_repos_only,
            limit=limit,
        )
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
    in_file: Path,
    out_file: Path,
    repos_file: Path,
    force_write: bool = False,
) -> int:
    try:
        html = in_file.read_text(encoding="utf-8")
        repos = load_active_repos_from_text(repos_file.read_text(encoding="utf-8"))
        pr_items = load_pr_data_from_html(html)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cache = load_cache(cache_file)
    typed_items = report_items_from_script_dicts(pr_items)
    html = compose_report_html_from_snapshot(
        html=html,
        items=typed_items,
        display_repos=sort_repos_by_accepted_count(repos, pr_items, accepted_classifications=("shipped", "accepted-indirect")),
        activity=report_activity_summary(typed_items),
        visible_pr_items=20,
    )
    all_prs = prepare_timeline_prs(pr_items)
    chart_data, repo_data, repo_names = build_daily_data(all_prs, repos)
    chart_json, repo_json, names_json, avg_prs, avg_loc = build_chart_payload(chart_data, repo_data, repo_names)
    output_html = inject_timeline_chart(html, chart_json, repo_json, names_json, avg_prs, avg_loc)
    counts = report_counts(
        typed_items,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )
    report_cache_keys = {
        f"{str(item.get('repo', ''))}#{int(item.get('number', 0) or 0)}"
        for item in pr_items
    }
    issues = write_report_if_sane(
        out_file=out_file,
        html=output_html,
        report=ReportSanityInput(
            reported_count=counts.total,
            fetched_count=len(pr_items),
            acceptance_closed=counts.accepted + counts.not_shipped,
            open_count=counts.open,
            failed_repos=(),
            repo_count=len(repos),
            cached_classification_count=sum(1 for key in report_cache_keys if key in cache.entries),
        ),
        force_write=force_write,
    )
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1
    print(f"Generated report at {out_file}", file=sys.stderr)
    return 0

def inject_timeline_only(*, in_file: Path, out_file: Path, repos_file: Path) -> int:
    try:
        html = in_file.read_text(encoding="utf-8")
        repos = load_active_repos_from_text(repos_file.read_text(encoding="utf-8"))
        pr_items = load_pr_data_from_html(html)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    all_prs = prepare_timeline_prs(pr_items)
    chart_data, repo_data, repo_names = build_daily_data(all_prs, repos)
    chart_json, repo_json, names_json, avg_prs, avg_loc = build_chart_payload(chart_data, repo_data, repo_names)
    out_file.write_text(
        inject_timeline_chart(html, chart_json, repo_json, names_json, avg_prs, avg_loc),
        encoding="utf-8",
    )
    print(f"Injected chart + stat cards into {out_file}", file=sys.stderr)
    return 0

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
