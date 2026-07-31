from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from pytest import MonkeyPatch

import core.leaderboard as leaderboard_mod
from core.cache import load_cache
from core.leaderboard import (
    _parse_datetime,
    cached_leaderboard_rows,
    configured_repo_leaderboard_exclusions,
    fetch_community_leaderboard,
    is_leaderboard_bot,
    is_leaderboard_excluded_login,
    leaderboard_cache_key,
    new_leaderboard_stat,
    repo_leaderboard_exclusions,
)
from core.models import Cache
from core.report import repo_label


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
        rate_window_days=7,
    )

    assert stat.credited == 7
    assert stat.open == 5
    assert stat.total == 12
    assert stat.rate == 0.6


def test_new_leaderboard_stat_floors_credited_and_survives_a_zero_window() -> None:
    stat = new_leaderboard_stat(
        total=3,
        open_count=9,
        recent_count=0,
        rate_window_days=0,
    )

    assert stat.credited == 0
    assert stat.rate == 0


def test_parse_datetime_reads_the_ps1_era_cache_format() -> None:
    # PS1 wrote cachedAt in local en-US format; _author_recent_count still anchors on it.
    assert _parse_datetime("06/25/2026 12:39:14") == datetime(2026, 6, 25, 12, 39, 14, tzinfo=timezone.utc)


def _pr_node(login: str, *, state: str = "MERGED", created_at: str = "2026-07-01T00:00:00Z", typename: str = "User") -> dict[str, object]:
    return {"state": state, "createdAt": created_at, "author": {"login": login, "__typename": typename}}


def _graphql_page(nodes: list[dict[str, object]], *, has_next: bool = False, cursor: str = "") -> str:
    return json.dumps(
        {
            "data": {
                "repository": {
                    "pullRequests": {
                        "pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                        "nodes": nodes,
                    },
                },
            },
        },
    )


def _use_overlay_dir(monkeypatch: MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(leaderboard_mod, "_overlay_dir", path)
    monkeypatch.setattr(leaderboard_mod, "_overlay_cache", {})


def test_fetch_community_leaderboard_skips_fresh_entry_within_ttl(monkeypatch: MonkeyPatch) -> None:
    now = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)
    cache = Cache()
    key = leaderboard_cache_key("owner/repo", None)
    cache.leaderboards[key] = {"cachedAt": "2026-07-03T00:00:00Z", "stats": {}}

    def _fail(*_args: str, **_kwargs: object) -> str:
        raise AssertionError("gh must not run while the entry is inside its TTL")

    monkeypatch.setattr(leaderboard_mod, "run_gh", _fail)

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is False


def test_fetch_community_leaderboard_preserves_credit_keys_and_evidence_counts(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    cache = Cache()
    key = leaderboard_cache_key("owner/repo", None)
    cache.leaderboards[key] = {
        "cachedAt": "2026-06-01T00:00:00Z",
        "releaseCreditCounts": {"alice": 7},
        "commentShippedCounts": {"alice": 2},
        "releaseCreditMeta": {"verified": True},
        "shippedCounts": {"Alice": 5, "bob": 1},
        "stats": {"alice": {"total": 5}},
    }
    page = _graphql_page(
        [
            _pr_node("alice"),
            _pr_node("alice"),
            _pr_node("bob"),
            _pr_node("bob"),
            _pr_node("bob", state="OPEN", created_at="2026-07-02T00:00:00Z"),
        ],
    )
    monkeypatch.setattr(leaderboard_mod, "run_gh", lambda *_args, **_kwargs: page)

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is True

    entry = cache.leaderboards[key]
    assert entry["releaseCreditCounts"] == {"alice": 7}
    assert entry["commentShippedCounts"] == {"alice": 2}
    assert entry["releaseCreditMeta"] == {"verified": True}
    assert entry["shippedCounts"] == {"alice": 5, "bob": 2}
    stats = entry["stats"]
    assert isinstance(stats, dict)
    assert stats["bob"] == {"total": 3, "open": 1, "recentCount": 3}
    assert entry["cachedAt"] == "2026-07-03T00:00:00Z"


def test_fetch_community_leaderboard_paginates_past_first_page(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    pages = [
        _graphql_page([_pr_node("alice")], has_next=True, cursor="CUR1"),
        _graphql_page([_pr_node("alice"), _pr_node("bob")]),
    ]
    calls: list[tuple[str, ...]] = []

    def _fake_run_gh(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        return pages[len(calls) - 1]

    monkeypatch.setattr(leaderboard_mod, "run_gh", _fake_run_gh)
    cache = Cache()

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is True

    assert len(calls) == 2
    assert "cursor=CUR1" in calls[1]
    entry = cache.leaderboards[leaderboard_cache_key("owner/repo", None)]
    stats = entry["stats"]
    assert isinstance(stats, dict)
    assert stats["alice"]["total"] == 2
    assert stats["bob"]["total"] == 1


def test_fetch_community_leaderboard_keeps_existing_entry_on_partial_fetch(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    pages = [_graphql_page([_pr_node("alice")], has_next=True, cursor="CUR1"), ""]
    calls: list[tuple[str, ...]] = []

    def _fake_run_gh(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        return pages[len(calls) - 1]

    monkeypatch.setattr(leaderboard_mod, "run_gh", _fake_run_gh)
    cache = Cache()
    key = leaderboard_cache_key("owner/repo", None)
    stale_entry = {"cachedAt": "2026-06-01T00:00:00Z", "stats": {"alice": {"total": 9}}}
    cache.leaderboards[key] = dict(stale_entry)

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is False
    assert cache.leaderboards[key] == stale_entry


def test_fetch_community_leaderboard_excludes_bots_owner_and_members(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    (tmp_path / "repo").mkdir()
    (tmp_path / "repo" / "config.md").write_text(
        "- Members:\n  - maintainer: collaborator, https://github.com/maintainer\n",
        encoding="utf-8",
    )
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    page = _graphql_page(
        [
            _pr_node("owner"),
            _pr_node("maintainer"),
            _pr_node("copilot", typename="Bot"),
            _pr_node("legit[bot]"),
            _pr_node("carol"),
        ],
    )
    monkeypatch.setattr(leaderboard_mod, "run_gh", lambda *_args, **_kwargs: page)
    cache = Cache()

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is True

    entry = cache.leaderboards[leaderboard_cache_key("owner/repo", None)]
    assert entry["logins"] == ["carol"]


def test_fetch_community_leaderboard_caches_empty_community_scan(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    page = _graphql_page([_pr_node("owner"), _pr_node("copilot", typename="Bot")])
    calls: list[tuple[str, ...]] = []

    def _fake_run_gh(*args: str, **_kwargs: object) -> str:
        calls.append(args)
        return page

    monkeypatch.setattr(leaderboard_mod, "run_gh", _fake_run_gh)
    cache = Cache()

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is True

    entry = cache.leaderboards[leaderboard_cache_key("owner/repo", None)]
    assert entry["logins"] == []
    assert entry["cachedAt"] == "2026-07-03T00:00:00Z"
    assert fetch_community_leaderboard("owner/repo", cache, now=now) is False
    assert len(calls) == 1


def test_fetch_community_leaderboard_aborts_at_page_cap_instead_of_truncating(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(leaderboard_mod, "LEADERBOARD_MAX_PAGES", 2)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    page = _graphql_page([_pr_node("alice")], has_next=True, cursor="CUR")
    monkeypatch.setattr(leaderboard_mod, "run_gh", lambda *_args, **_kwargs: page)
    cache = Cache()

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is False
    assert cache.leaderboards == {}


def test_fetch_community_leaderboard_keeps_shipped_counts_for_deleted_accounts(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)
    now = datetime(2026, 7, 3, tzinfo=timezone.utc)
    cache = Cache()
    key = leaderboard_cache_key("owner/repo", None)
    cache.leaderboards[key] = {
        "cachedAt": "2026-06-01T00:00:00Z",
        "shippedCounts": {"ghost": 3},
        "stats": {},
    }
    page = _graphql_page([_pr_node("alice")])
    monkeypatch.setattr(leaderboard_mod, "run_gh", lambda *_args, **_kwargs: page)

    assert fetch_community_leaderboard("owner/repo", cache, now=now) is True
    assert cache.leaderboards[key]["shippedCounts"] == {"ghost": 3, "alice": 1}


def test_repo_leaderboard_config_keeps_orca_integration_bot(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    _use_overlay_dir(monkeypatch, tmp_path)

    _members, integration_bots = leaderboard_mod.repo_leaderboard_config("stablyai/orca")

    assert integration_bots == ("buf0-bot[bot]",)


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
    # Boards key on the canonical repo name, which repos.txt need not spell (it may name a
    # repo by its pre-transfer owner), so take the names the page itself rendered from.
    repos = sorted({str(item["repo"]) for item in _pr_data(html)})
    repo_by_label = {repo_label(repo): repo for repo in repos}
    repo_by_label.update({repo.rsplit("/", 1)[-1]: repo for repo in repos})

    for table_label in _leaderboard_table_labels(html):
        repo = repo_by_label[table_label]
        author_credited, author_open = _author_repo_counts_from_html(html, repo)
        rows = cached_leaderboard_rows(
            cache=cache,
            repo=repo,
            exclusions=configured_repo_leaderboard_exclusions(repo),
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

def _pr_data(html: str) -> list[dict[str, object]]:
    match = re.search(r"var PR_DATA = (\[.*?\]);", html, re.S)
    assert match is not None
    items: list[dict[str, object]] = json.loads(match.group(1))
    return items


def _author_repo_counts_from_html(html: str, repo: str) -> tuple[int, int]:
    items = _pr_data(html)
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
