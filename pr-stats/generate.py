from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import TemplateError

from core.cache import classification_cache_key, load_cache, save_cache, set_cached_closed_classification
from core.leaderboard import DEFAULT_OVERLAY_CONFIG_DIR, fetch_community_leaderboard, set_overlay_config_dir
from core.classify import ClassificationResult, classify_closed_pr
from core.classification_rebuild import CacheRebuildInterrupted, live_evidence, rebuild_classification_cache, write_pr_classification_progress
from core.models import Cache, PullRequest, UserRef, int_value
from core.credit import cached_release_credit_counts
from core.github import GhError, GhPullRequestView, GhRetryExhausted, run_gh
from core.html import ReportSanityInput, write_report_if_sane
from core.page import (
    render_breakdown_section,
    render_leaderboard_sections,
    render_pr_bootstrap,
    render_pr_controls_and_table,
    render_report_page,
    render_repo_status_sections,
    render_representative_section,
    render_timeline_bootstrap,
)
from core.report import (
    EASTERN,
    PrReportItem,
    enrich_representative_items,
    parse_representative_readme,
    report_activity_summary,
    report_counts,
    report_item_from_pull_request_view,
    report_items_to_script_dicts,
    sort_report_items_by_effective_date,
    sort_repos_by_accepted_count,
)
from core.timeline import build_chart_payload, build_daily_data, load_active_repos_from_text, prepare_timeline_prs

DEFAULT_AUTHOR = "rodboev"
DEFAULT_CACHE_FILE = Path(".pr-classification-cache.json")
DEFAULT_REBUILD_CACHE_FILE = Path(".pr-classification-cache.rebuild.json")
DEFAULT_DIVERGENCE_FILE = Path("classification-divergences.json")
DEFAULT_README_FILE = Path(r"C:\Users\Rod\.claude\skills\pr\README.md")
README_REPO = "rodboev/pr-sweep"
PR_LIST_JSON_FIELDS = "number,state,title,createdAt,closedAt,mergedAt,headRefName,author,additions,deletions,changedFiles,url"
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
    parser.add_argument("--readme-file", type=Path, default=None)
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

    return generate_report(
        cache_file=args.cache_file,
        template_file=args.template_file,
        out_file=args.out_file or Path("index.html"),
        repos_file=args.repos_file,
        readme_file=args.readme_file,
        author=args.author,
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
    readme_file: Path | None = None,
    author: str = DEFAULT_AUTHOR,
    force_write: bool = False,
) -> int:
    try:
        template_text = template_file.read_text(encoding="utf-8")
        repos = load_active_repos_from_text(repos_file.read_text(encoding="utf-8"))
        live_prs, failed_repos = fetch_author_pull_requests(repos, author=author)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cache = load_cache(cache_file)
    now = datetime.now(timezone.utc)
    cache_updated_early = False
    for repo in repos:
        try:
            if fetch_community_leaderboard(repo, cache, now=now):
                cache_updated_early = True
        except GhRetryExhausted as exc:
            print(f"WARNING: leaderboard refresh failed for {repo}: {exc}", file=sys.stderr)
    try:
        typed_items, cache_updated = report_items_from_live_pull_requests(live_prs, cache, now=now)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if cache_updated or cache_updated_early:
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
        chart_json, repo_json, names_json, avg_prs, avg_loc = build_chart_payload(chart_data, repo_data, repo_names)
        representative_items = enrich_representative_items(
            parse_representative_readme(load_representative_readme_text(readme_file)),
            typed_items,
        )
        now_eastern = now.astimezone(EASTERN)
        today_label = now_eastern.strftime("%Y-%m-%d")
        context = {
            "breakdown": render_breakdown_section(
                counts,
                report_activity_summary(typed_items),
                avg_prs=avg_prs,
                avg_loc=avg_loc,
            ),
            "timeline_bootstrap": render_timeline_bootstrap(chart_json, repo_json, names_json, today_label),
            "today": today_label,
            "repo_status_sections": render_repo_status_sections(repos=display_repos, items=typed_items),
            "leaderboard_sections": render_leaderboard_sections(
                repos=display_repos,
                items=typed_items,
                cache=cache,
                now=now,
                author=author,
            ),
            "representative_section": render_representative_section(representative_items),
            "pr_controls": render_pr_controls_and_table(items=typed_items, display_repos=display_repos, visible_items=20),
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

def load_representative_readme_text(readme_file: Path | None) -> str:
    path = readme_file if readme_file is not None else DEFAULT_README_FILE
    if path.exists():
        return path.read_text(encoding="utf-8")
    try:
        return run_gh(
            "api",
            f"repos/{README_REPO}/contents/README.md",
            "-H",
            "Accept: application/vnd.github.raw",
            suppress_errors=True,
        )
    except GhError:
        return ""

def fetch_author_pull_requests(repos: list[str], *, author: str) -> tuple[list[tuple[str, GhPullRequestView]], tuple[str, ...]]:
    pulls: list[tuple[str, GhPullRequestView]] = []
    failed_repos: list[str] = []
    for repo in repos:
        raw = run_gh(
            "pr",
            "list",
            "--repo",
            repo,
            "--author",
            author,
            "--state",
            "all",
            "--limit",
            "500",
            "--json",
            PR_LIST_JSON_FIELDS,
            suppress_errors=True,
        )
        if not raw:
            failed_repos.append(repo)
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            failed_repos.append(repo)
            continue
        if not isinstance(payload, list):
            failed_repos.append(repo)
            continue
        for item in payload:
            if isinstance(item, dict):
                pulls.append((repo, GhPullRequestView.model_validate(item)))
    return pulls, tuple(failed_repos)


def report_items_from_live_pull_requests(
    pulls: list[tuple[str, GhPullRequestView]],
    cache: Cache,
    *,
    now: datetime,
) -> tuple[list[PrReportItem], bool]:
    items: list[PrReportItem] = []
    cache_updated = False
    classification_total = sum(1 for repo, pr in pulls if _needs_live_classification(repo, pr, cache))
    classified_count = 0
    for repo, pr in pulls:
        should_log_classification = _needs_live_classification(repo, pr, cache)
        classification, did_update_cache = live_pull_request_classification(repo, pr, cache, now=now)
        if should_log_classification and did_update_cache:
            classified_count += 1
            write_pr_classification_progress(classified_count, classification_total, repo, pr.number, pr.author.login, classification.log_label, classification.classification)
        cache_updated = cache_updated or did_update_cache
        items.append(report_item_from_pull_request_view(repo=repo, pr=pr, classification=classification))
    return items, cache_updated


def _needs_live_classification(repo: str, pr: GhPullRequestView, cache: Cache) -> bool:
    key = classification_cache_key(repo, pr.number)
    entry = cache.entries.get(key)
    return not (entry is not None and entry.classification) and pr.state != "OPEN"


def live_pull_request_classification(
    repo: str,
    pr: GhPullRequestView,
    cache: Cache,
    *,
    now: datetime,
) -> tuple[ClassificationResult, bool]:
    key = classification_cache_key(repo, pr.number)
    entry = cache.entries.get(key)
    if entry is not None and entry.classification:
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
    if pr.state == "OPEN":
        return ClassificationResult(classification="open", evidence_kind="open", log_label="open"), False
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
