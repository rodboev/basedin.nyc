from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from core.cache import load_cache
from core.leaderboard import (
    cached_leaderboard_rows,
    community_contributor_logins,
    configured_repo_leaderboard_exclusions,
    is_leaderboard_bot,
    is_leaderboard_excluded_login,
    leaderboard_cache_key,
    merge_community_contributor_logins,
    new_leaderboard_stat,
    repo_leaderboard_exclusions,
    top_credited_logins,
)
from core.report import repo_label
from core.timeline import load_active_repos_from_text


def test_leaderboard_bot_detection() -> None:
    assert is_leaderboard_bot("")
    assert is_leaderboard_bot("app/copilot")
    assert is_leaderboard_bot("APP/copilot")
    assert is_leaderboard_bot("dependabot[bot]")
    assert is_leaderboard_bot("Dependabot[bot]")
    assert not is_leaderboard_bot("rodboev")


def test_repo_exclusions_include_owner_maintainers_and_bots() -> None:
    exclusions = repo_leaderboard_exclusions(
        owner="owner",
        maintainer_logins=("maintainer", ""),
        integration_bots=("bot",),
    )

    assert exclusions.all == ("owner", "maintainer", "bot")
    assert is_leaderboard_excluded_login("owner", exclusions)
    assert is_leaderboard_excluded_login("OWNER", exclusions)
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


def test_new_leaderboard_stat_parses_legacy_cache_datetime() -> None:
    stat = new_leaderboard_stat(
        total=3,
        open_count=0,
        recent_count=0,
        last_created_at="06/25/2026 12:39:14",
        now=datetime(2026, 6, 26, 12, 39, 14, tzinfo=timezone.utc),
        rate_window_days=7,
    )

    assert stat.idle == 1.0


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

def test_cached_webui_leaderboard_rows_match_rendered_ps1_output(repo_root: Path) -> None:
    cache_path = repo_root / ".pr-classification-cache.json"
    html_path = repo_root / "index.html"
    cache = load_cache(cache_path)
    html = html_path.read_text(encoding="utf-8")
    author_credited, author_open = _author_repo_counts_from_html(html, "nesquena/hermes-webui")
    exclusions = repo_leaderboard_exclusions(
        owner="nesquena",
        maintainer_logins=("nesquena",),
        integration_bots=("nesquena-hermes",),
    )
    rows = cached_leaderboard_rows(
        cache=cache,
        repo="nesquena/hermes-webui",
        exclusions=exclusions,
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
        rate_window_days=7,
        author_login="rodboev",
        author_credited=author_credited,
        author_open=author_open,
        max_entries=51,
    )
    rendered_rows = _leaderboard_rows_from_html(html, "lb-hermes-webui")

    expected_rows = [(row.login, row.credited, row.open) for row in rows[: len(rendered_rows)]]
    actual_rows = [(login, credited, open_count) for _rank, login, credited, open_count in rendered_rows]
    truncated_next = rows[len(rendered_rows)] if len(rows) > len(rendered_rows) else None
    truncated_tie = (
        truncated_next is not None
        and len(expected_rows) > 0
        and (truncated_next.credited, truncated_next.open) == expected_rows[-1][1:]
    )

    assert [(credited, open_count) for _login, credited, open_count in expected_rows] == [
        (credited, open_count) for _login, credited, open_count in actual_rows
    ]
    assert _complete_tie_groups(expected_rows, truncated_tie=truncated_tie) == _complete_tie_groups(
        actual_rows,
        truncated_tie=truncated_tie,
    )

def test_cached_leaderboard_rows_override_author_with_report_counts(tmp_path: Path) -> None:
    cache_path = tmp_path / "cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 3,
                "leaderboards": {
                    "owner/repo|community-shipped-v4|all": {
                        "stats": {
                            "rodboev": {"total": 46, "open": 6, "recentCount": 0, "lastCreatedAt": ""},
                            "alice": {"total": 10, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
                        },
                        "shippedCounts": {"rodboev": 40, "alice": 10},
                    },
                },
            },
        ),
        encoding="utf-8",
    )

    rows = cached_leaderboard_rows(
        cache=load_cache(cache_path),
        repo="owner/repo",
        exclusions=repo_leaderboard_exclusions(owner="owner"),
        now=datetime(2026, 7, 2, tzinfo=timezone.utc),
        rate_window_days=7,
        author_login="rodboev",
        author_credited=39,
        author_open=3,
    )

    rod = next(row for row in rows if row.login == "rodboev")
    assert (rod.credited, rod.open) == (39, 3)

def test_cached_leaderboard_rows_match_all_rendered_ps1_boards(repo_root: Path) -> None:
    cache_path = repo_root / ".pr-classification-cache.json"
    html_path = repo_root / "index.html"
    cache = load_cache(cache_path)
    html = html_path.read_text(encoding="utf-8")
    repos = load_active_repos_from_text((repo_root / "repos.txt").read_text(encoding="utf-8"))
    repo_by_label = {repo_label(repo): repo for repo in repos}
    repo_by_label.update({repo.rsplit("/", 1)[-1]: repo for repo in repos})

    for table_label in _leaderboard_table_labels(html):
        repo = repo_by_label[table_label]
        author_credited, author_open = _author_repo_counts_from_html(html, repo)
        rows = cached_leaderboard_rows(
            cache=cache,
            repo=repo,
            exclusions=configured_repo_leaderboard_exclusions(repo),
            now=datetime(2026, 7, 2, tzinfo=timezone.utc),
            rate_window_days=7,
            author_login="rodboev",
            author_credited=author_credited,
            author_open=author_open,
            max_entries=51,
        )
        rendered_rows = _leaderboard_rows_from_html(html, f"lb-{table_label}")

        expected_rows = [(row.login, row.credited, row.open) for row in rows[: len(rendered_rows)]]
        actual_rows = [(login, credited, open_count) for _rank, login, credited, open_count in rendered_rows]
        truncated_next = rows[len(rendered_rows)] if len(rows) > len(rendered_rows) else None
        truncated_tie = (
            truncated_next is not None
            and len(expected_rows) > 0
            and (truncated_next.credited, truncated_next.open) == expected_rows[-1][1:]
        )

        assert [(credited, open_count) for _login, credited, open_count in expected_rows] == [
            (credited, open_count) for _login, credited, open_count in actual_rows
        ], repo
        assert _complete_tie_groups(expected_rows, truncated_tie=truncated_tie) == _complete_tie_groups(
            actual_rows,
            truncated_tie=truncated_tie,
        ), repo

def _complete_tie_groups(
    rows: list[tuple[str, int, int]],
    *,
    truncated_tie: bool,
) -> list[tuple[int, int, frozenset[str]]]:
    groups = _tie_groups(rows)
    return groups[:-1] if truncated_tie else groups

def _tie_groups(rows: list[tuple[str, int, int]]) -> list[tuple[int, int, frozenset[str]]]:
    groups: list[tuple[int, int, frozenset[str]]] = []
    index = 0
    while index < len(rows):
        _login, credited, open_count = rows[index]
        logins: set[str] = set()
        while index < len(rows) and rows[index][1:] == (credited, open_count):
            logins.add(rows[index][0])
            index += 1
        groups.append((credited, open_count, frozenset(logins)))
    return groups

def _author_repo_counts_from_html(html: str, repo: str) -> tuple[int, int]:
    match = re.search(r"var PR_DATA = (\[.*?\]);", html, re.S)
    assert match is not None
    items = json.loads(match.group(1))
    credited = sum(1 for item in items if item.get("repo") == repo and item.get("statusKey") == "shipped")
    open_count = sum(1 for item in items if item.get("repo") == repo and item.get("statusKey") == "open")
    return credited, open_count

def _leaderboard_rows_from_html(html: str, table_id: str) -> list[tuple[int, str, int, int]]:
    table_match = re.search(
        rf'<div class="collapsible-table leaderboard[^"]*" id="{re.escape(table_id)}".*?<tbody>\s*(.*?)\s*</tbody>',
        html,
        re.S,
    )
    assert table_match is not None
    rows: list[tuple[int, str, int, int]] = []
    for match in re.finditer(
        r'<tr(?: class="[^"]*")? data-rank="(\d+)"><td>#\d+</td><td><a href="https://github.com/([^"]+)">[^<]+</a></td><td>(\d+)</td><td>(\d+)</td>',
        table_match.group(1),
    ):
        rows.append((int(match.group(1)), match.group(2), int(match.group(3)), int(match.group(4))))
    return rows

def _leaderboard_table_labels(html: str) -> list[str]:
    return re.findall(r'<div class="collapsible-table leaderboard[^"]*" id="lb-([^"]+)"', html)
