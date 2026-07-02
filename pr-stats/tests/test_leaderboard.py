from __future__ import annotations

from datetime import datetime, timezone

from core.leaderboard import (
    community_contributor_logins,
    is_leaderboard_bot,
    is_leaderboard_excluded_login,
    leaderboard_cache_key,
    merge_community_contributor_logins,
    new_leaderboard_stat,
    repo_leaderboard_exclusions,
    top_credited_logins,
)


def test_leaderboard_bot_detection() -> None:
    assert is_leaderboard_bot("")
    assert is_leaderboard_bot("app/copilot")
    assert is_leaderboard_bot("dependabot[bot]")
    assert not is_leaderboard_bot("rodboev")


def test_repo_exclusions_include_owner_maintainers_and_bots() -> None:
    exclusions = repo_leaderboard_exclusions(
        owner="owner",
        maintainer_logins=("maintainer", ""),
        integration_bots=("bot",),
    )

    assert exclusions.all == ("owner", "maintainer", "bot")
    assert is_leaderboard_excluded_login("owner", exclusions)
    assert is_leaderboard_excluded_login("maintainer", exclusions)
    assert is_leaderboard_excluded_login("bot", exclusions)
    assert not is_leaderboard_excluded_login("contributor", exclusions)


def test_leaderboard_cache_key_matches_ps1_version() -> None:
    assert leaderboard_cache_key("owner/repo", None) == "owner/repo|community-shipped-v4|all"
    assert leaderboard_cache_key("owner/repo", datetime(2026, 6, 2, tzinfo=timezone.utc)) == "owner/repo|community-shipped-v4|2026-06-02"


def test_new_leaderboard_stat_matches_ps1_math() -> None:
    stat = new_leaderboard_stat(
        total=12,
        open_count=5,
        recent_count=4,
        last_created_at="2026-07-01T00:00:00Z",
        now=datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc),
        rate_window_days=7,
    )

    assert stat.credited == 7
    assert stat.open == 5
    assert stat.total == 12
    assert stat.rate == 0.6
    assert stat.idle == 1.5


def test_new_leaderboard_stat_handles_missing_last_date() -> None:
    stat = new_leaderboard_stat(
        total=3,
        open_count=9,
        recent_count=0,
        last_created_at="",
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
        rate_window_days=0,
    )

    assert stat.credited == 0
    assert stat.rate == 0
    assert stat.idle == 999


def test_top_credited_logins_sorts_by_credited_then_open_and_keeps_author() -> None:
    now = datetime(2026, 7, 2, tzinfo=timezone.utc)
    stats = {
        "alice": new_leaderboard_stat(total=10, open_count=1, recent_count=0, last_created_at="", now=now, rate_window_days=7),
        "bob": new_leaderboard_stat(total=10, open_count=3, recent_count=0, last_created_at="", now=now, rate_window_days=7),
        "rodboev": new_leaderboard_stat(total=1, open_count=0, recent_count=0, last_created_at="", now=now, rate_window_days=7),
    }

    assert top_credited_logins(stats, author="rodboev", top=1) == ["alice", "rodboev"]


def test_community_contributor_logins_preserve_order_filter_exclusions_and_prepend_author() -> None:
    exclusions = repo_leaderboard_exclusions(owner="owner", maintainer_logins=("maintainer",), integration_bots=())

    result = community_contributor_logins(
        recent_author_logins=("maintainer", "alice", "alice", "bob"),
        exclusions=exclusions,
        author="rodboev",
    )

    assert result == ["rodboev", "alice", "bob"]


def test_merge_community_contributor_logins_combines_prior_seed_recent_then_filters() -> None:
    exclusions = repo_leaderboard_exclusions(owner="owner", maintainer_logins=("maintainer",), integration_bots=())

    result = merge_community_contributor_logins(
        prior_logins=("old", "maintainer"),
        seed_logins=("seed", "old"),
        recent_author_logins=("recent", "seed"),
        exclusions=exclusions,
        author="rodboev",
    )

    assert result == ["old", "seed", "rodboev", "recent"]
