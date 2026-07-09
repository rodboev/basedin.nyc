from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Final

from core.cache import _format_datetime, _parse_datetime
from core.github import GhRetryExhausted, run_gh
from core.models import Cache, JsonObject

RELEASE_CACHE_TTL_SECONDS: Final = 24 * 3600

GITHUB_AUTO_CREDIT: Final = re.compile(
    r"by\s+@([\w-]+)\s+in\s+(?:https://github\.com/[^/]+/[^/]+/pull/|#)(\d+)"
)
PR_NUMBER_ONLY: Final = re.compile(r"\(#(\d+)\)")


def parse_release_credits(
    body: str,
) -> tuple[dict[str, set[int]], set[int]]:
    attributed: dict[str, set[int]] = defaultdict(set)
    for match in GITHUB_AUTO_CREDIT.finditer(body):
        login = match.group(1)
        pr_number = int(match.group(2))
        attributed[login].add(pr_number)
    attributed_prs = {pr for prs in attributed.values() for pr in prs}
    unattributed: set[int] = set()
    for match in PR_NUMBER_ONLY.finditer(body):
        pr_number = int(match.group(1))
        if pr_number not in attributed_prs:
            unattributed.add(pr_number)
    return dict(attributed), unattributed


def fetch_repo_releases(repo: str, *, paginate: bool = True) -> list[dict[str, object]] | None:
    args = ["api", f"repos/{repo}/releases?per_page=100"]
    if paginate:
        args.append("--paginate")
    try:
        raw = run_gh(*args, suppress_errors=True)
    except GhRetryExhausted:
        return None
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        chunks = raw.split("\n")
        releases: list[dict[str, object]] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                page = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            if isinstance(page, list):
                releases.extend(page)
            elif isinstance(page, dict):
                releases.append(page)
        if not releases:
            return None
        return releases
    if isinstance(parsed, list):
        return parsed
    return None


def refresh_release_cache(
    repo: str,
    cache: Cache,
    *,
    author_pr_numbers: set[int],
    now: datetime,
) -> bool:
    existing = cache.releaseData.get(repo)
    if existing is not None and not _release_cache_expired(existing, now):
        return False

    cached_tags: set[str] = set()
    if existing is not None:
        raw_rels = existing.get("releases")
        for rel in (raw_rels if isinstance(raw_rels, list) else []):
            if isinstance(rel, dict):
                cached_tags.add(str(rel.get("tagName", "")))

    page1 = fetch_repo_releases(repo, paginate=False)
    if page1 is None:
        return False

    new_raw = [r for r in page1 if isinstance(r, dict) and str(r.get("tag_name", "")) not in cached_tags]
    if not new_raw and existing is not None:
        existing["cachedAt"] = _format_datetime(now)
        return False

    if existing is None or len(new_raw) == len(page1):
        # First fetch or all 100 are new: full paginated scan needed
        all_raw = fetch_repo_releases(repo, paginate=True) if len(page1) >= 100 else page1
        if all_raw is None:
            return False
        new_raw = [r for r in all_raw if isinstance(r, dict) and str(r.get("tag_name", "")) not in cached_tags]

    new_releases = _parse_raw_releases(new_raw, author_pr_numbers)

    prev_releases: list[dict[str, object]] = []
    prev_pr_to_release: dict[str, dict[str, str]] = {}
    if existing is not None:
        raw_prev = existing.get("releases")
        if isinstance(raw_prev, list):
            prev_releases = raw_prev
        raw_prev_idx = existing.get("prToRelease")
        if isinstance(raw_prev_idx, dict):
            prev_pr_to_release = {str(k): v for k, v in raw_prev_idx.items() if isinstance(v, dict)}

    all_releases = new_releases + prev_releases
    pr_to_release = dict(prev_pr_to_release)
    for rel in sorted(new_releases, key=lambda r: str(r.get("publishedAt", ""))):
        tag = str(rel.get("tagName", ""))
        url = str(rel.get("htmlUrl", ""))
        for pr_num in _all_pr_numbers(rel):
            key = str(pr_num)
            if key not in pr_to_release:
                pr_to_release[key] = {"tagName": tag, "htmlUrl": url}

    cache.releaseData[repo] = {
        "cachedAt": _format_datetime(now),
        "releases": all_releases,
        "prToRelease": pr_to_release,
    }
    return True


def _parse_raw_releases(
    raw_releases: list[dict[str, object]],
    author_pr_numbers: set[int],
) -> list[dict[str, object]]:
    releases: list[dict[str, object]] = []
    for rel in raw_releases:
        tag_name = str(rel.get("tag_name") or "")
        html_url = str(rel.get("html_url") or "")
        published_at = str(rel.get("published_at") or "")
        body = str(rel.get("body") or "")
        if not tag_name or not body:
            continue
        attributed, unattributed = parse_release_credits(body)
        credits_serialized: dict[str, list[int]] = {
            login: sorted(prs) for login, prs in attributed.items()
        }
        author_unattributed = sorted(unattributed & author_pr_numbers)
        releases.append({
            "tagName": tag_name,
            "htmlUrl": html_url,
            "publishedAt": published_at,
            "credits": credits_serialized,
            "unattributedAuthorPrs": author_unattributed,
        })
    return releases


def _all_pr_numbers(rel: dict[str, object]) -> list[int]:
    numbers: list[int] = []
    credits_raw = rel.get("credits")
    if isinstance(credits_raw, dict):
        for prs in credits_raw.values():
            if isinstance(prs, list):
                numbers.extend(int(pr) for pr in prs)
    unattr = rel.get("unattributedAuthorPrs")
    if isinstance(unattr, list):
        numbers.extend(int(pr) for pr in unattr)
    return numbers


def release_for_pr(
    cache: Cache,
    repo: str,
    number: int,
) -> tuple[str, str] | None:
    entry = cache.releaseData.get(repo)
    if entry is None:
        return None
    pr_to_release = entry.get("prToRelease")
    if not isinstance(pr_to_release, dict):
        return None
    info = pr_to_release.get(str(number))
    if not isinstance(info, dict):
        return None
    tag = str(info.get("tagName") or "")
    url = str(info.get("htmlUrl") or "")
    if not tag:
        return None
    return tag, url


def release_credit_counts(
    cache: Cache,
    repo: str,
) -> dict[str, int]:
    entry = cache.releaseData.get(repo)
    if entry is None:
        return {}
    releases = entry.get("releases")
    if not isinstance(releases, list):
        return {}
    counts: dict[str, int] = defaultdict(int)
    seen_prs: dict[str, set[int]] = defaultdict(set)
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        credits_raw = rel.get("credits")
        if not isinstance(credits_raw, dict):
            continue
        for login, prs in credits_raw.items():
            login_str = str(login)
            if isinstance(prs, list):
                for pr_num in prs:
                    pr_int = int(pr_num)
                    if pr_int not in seen_prs[login_str]:
                        seen_prs[login_str].add(pr_int)
                        counts[login_str] += 1
    return dict(counts)


def _release_cache_expired(entry: Mapping[str, object], now: datetime) -> bool:
    cached_at = _parse_datetime(str(entry.get("cachedAt") or ""))
    if cached_at is None:
        return True
    return (now - cached_at).total_seconds() > RELEASE_CACHE_TTL_SECONDS
