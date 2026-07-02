from __future__ import annotations

import argparse
import sys
from pathlib import Path

from core.cache import load_cache
from core.models import Cache
from core.credit import cached_release_credit_counts
from core.github import run_gh

WEBUI_REPO = "nesquena/hermes-webui"
WEBUI_EXCLUDED_LOGINS = ("nesquena", "nesquena-hermes")
WEBUI_CREDIT_CHECKS = (
    ("franksong2702", 170, 200),
    ("Michaelyklam", 115, 140),
    ("rodboev", 115, 135),
    ("ai-ag2026", 85, 110),
)

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate pr-stats output.")
    parser.add_argument("--cache-file", type=Path, default=Path(".pr-classification-cache.json"))
    parser.add_argument("--verify-webui-credits-only", action="store_true")
    parser.add_argument("--changelog-file", type=Path, default=None)
    parser.add_argument("--contributors-file", type=Path, default=None)
    args = parser.parse_args(argv)

    if args.verify_webui_credits_only:
        return verify_webui_credits_only(
            cache_file=args.cache_file,
            changelog_file=args.changelog_file,
            contributors_file=args.contributors_file,
        )

    parser.error("the Python entry point currently supports --verify-webui-credits-only")
    return 2

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
    for login, minimum, maximum in WEBUI_CREDIT_CHECKS:
        value = counts.get(login, 0)
        ok = minimum <= value <= maximum
        print(f"{login}: {value} (expected {minimum}-{maximum}) {'OK' if ok else 'FAIL'}")
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
