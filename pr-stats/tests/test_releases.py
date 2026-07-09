from __future__ import annotations

from datetime import datetime, timezone

from core.models import Cache
from core.releases import (
    parse_release_credits,
    release_credit_counts,
    release_for_pr,
)


def test_parse_release_credits_github_auto_format() -> None:
    body = (
        "## What's Changed\n"
        "* fix(dashboard): price proxy savings by @rodboev in #1728\n"
        "* fix(proxy): surface codex ws failures by @rodboev in #1727\n"
        "* fix(savings): guard non-finite coercion by @inix-x in #1769\n"
    )
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {"rodboev": {1728, 1727}, "inix-x": {1769}}
    assert unattributed == set()


def test_parse_release_credits_url_format() -> None:
    body = "* Wire OpenAI Responses by @amcfague in https://github.com/headroomlabs-ai/headroom/pull/1438\n"
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {"amcfague": {1438}}
    assert unattributed == set()


def test_parse_release_credits_parenthetical() -> None:
    body = (
        "Bug Fixes:\n"
        "* Embeddings: Guard against an embed_batch count mismatch (#5966)\n"
        "* Memory: Re-raise LLM extraction failures (#5878)\n"
    )
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {}
    assert unattributed == {5966, 5878}


def test_parse_release_credits_mixed_formats() -> None:
    body = (
        "* fix: something by @alice in #100\n"
        "* another fix (#200)\n"
        "* yet another by @bob in #300\n"
    )
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {"alice": {100}, "bob": {300}}
    assert unattributed == {200}


def test_parse_release_credits_no_matches() -> None:
    body = "Patch release focused on cross-platform stability and worker/runtime correctness.\n## Fixes\n* Worker host: clients now honor CLAUDE_MEM_WORKER_HOST.\n"
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {}
    assert unattributed == set()


def test_parse_release_credits_parenthetical_not_duplicated_with_attributed() -> None:
    body = "* fix: something by @alice in #100\n* also mentions (#100) somewhere\n"
    attributed, unattributed = parse_release_credits(body)
    assert attributed == {"alice": {100}}
    assert unattributed == set()


def _make_cache_with_releases(
    repo: str,
    releases: list[dict[str, object]],
    pr_to_release: dict[str, dict[str, str]],
) -> Cache:
    from core.cache import empty_cache
    cache = empty_cache()
    cache.releaseData[repo] = {
        "cachedAt": "2026-07-09T00:00:00Z",
        "releases": releases,
        "prToRelease": pr_to_release,
    }
    return cache


def test_release_for_pr_found() -> None:
    cache = _make_cache_with_releases(
        "owner/repo",
        [],
        {"42": {"tagName": "v1.0.0", "htmlUrl": "https://github.com/owner/repo/releases/tag/v1.0.0"}},
    )
    result = release_for_pr(cache, "owner/repo", 42)
    assert result == ("v1.0.0", "https://github.com/owner/repo/releases/tag/v1.0.0")


def test_release_for_pr_not_found() -> None:
    cache = _make_cache_with_releases("owner/repo", [], {})
    assert release_for_pr(cache, "owner/repo", 999) is None


def test_release_for_pr_no_cache() -> None:
    from core.cache import empty_cache
    cache = empty_cache()
    assert release_for_pr(cache, "owner/repo", 1) is None


def test_release_credit_counts_aggregates() -> None:
    cache = _make_cache_with_releases(
        "owner/repo",
        [
            {"tagName": "v1.0.0", "credits": {"alice": [1, 2], "bob": [3]}},
            {"tagName": "v2.0.0", "credits": {"alice": [4, 2], "bob": [5]}},
        ],
        {},
    )
    counts = release_credit_counts(cache, "owner/repo")
    assert counts["alice"] == 3
    assert counts["bob"] == 2


def test_release_credit_counts_deduplicates_across_releases() -> None:
    cache = _make_cache_with_releases(
        "owner/repo",
        [
            {"tagName": "v1.0.0", "credits": {"alice": [1, 2]}},
            {"tagName": "v2.0.0", "credits": {"alice": [2, 3]}},
        ],
        {},
    )
    counts = release_credit_counts(cache, "owner/repo")
    assert counts["alice"] == 3


def test_release_credit_counts_empty() -> None:
    from core.cache import empty_cache
    cache = empty_cache()
    assert release_credit_counts(cache, "owner/repo") == {}
