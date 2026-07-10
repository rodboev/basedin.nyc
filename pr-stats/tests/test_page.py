from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.models import Cache
from core.page import (
    leaderboard_idle_status,
    render_breakdown_section,
    render_leaderboard_section,
    render_pr_bootstrap,
    render_pr_controls_and_table,
    render_report_page,
    render_repo_status_sections,
    render_representative_section,
    render_timeline_bootstrap,
)
from core.report import PrReportItem, ReportActivitySummary, ReportCounts, RepresentativeItem

NOW = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)


def test_render_breakdown_section_matches_ps1_owned_markup() -> None:
    assert render_breakdown_section(
        ReportCounts(total=10, accepted=7, open=1, superseded=1, lost=1, not_shipped=2, acceptance_rate=78),
        ReportActivitySummary(time_span="3 days", time_range="Active days from Jul 1 - Jul 3"),
        avg_prs="12.0",
        avg_loc="5.5k",
    ) == (
        "<h2>Breakdown</h2>\n\n"
        '<div class="grid grid-summary">\n'
        '  <div class="stat-card"><div class="number" id="bd-total">10</div><div class="label">Total PRs</div></div>\n'
        '  <div class="stat-card"><div class="number green" id="bd-shipped">7</div><div class="label">Shipped</div></div>\n'
        '  <div class="stat-card"><div class="number yellow" id="bd-open">1</div><div class="label">Open</div></div>\n'
        '  <div class="stat-card"><div class="number" id="bd-lost-sup">2</div><div class="label">Lost/Superseded</div></div>\n'
        "</div>\n"
        '<div class="grid grid-summary">\n'
        '  <div class="stat-card"><div class="number green" id="bd-rate">78%</div><div class="label" id="bd-rate-label">Acceptance (1 superseded, 1 lost)</div></div>\n'
        '  <div class="stat-card"><div class="number" id="bd-avg-prs">12.0</div><div class="label">Avg PRs/day</div></div>\n'
        '  <div class="stat-card"><div class="number" id="bd-avg-loc">5.5k</div><div class="label">Avg LOC/day</div></div>\n'
        '  <div class="stat-card"><div class="number blue" id="bd-days">3 days</div><div class="label" id="bd-days-label">Active days from Jul 1 - Jul 3</div></div>\n'
        "</div>\n\n"
        '<div class="bar-container">\n'
        '  <div class="bar-segment bar-shipped" id="bd-bar-shipped" data-width="70" title="7">7</div>\n'
        '  <div class="bar-segment bar-superseded" id="bd-bar-superseded" data-width="10">1</div>\n'
        '  <div class="bar-segment bar-lost" id="bd-bar-lost" data-width="10">1</div>\n'
        '  <div class="bar-segment bar-open" id="bd-bar-open" data-width="10" title="1">1</div>\n'
        "</div>\n"
        '<div class="legend">\n'
        '  <div class="legend-item" id="bd-leg-shipped"><div class="legend-dot legend-dot-shipped"></div> Shipped (7)</div>\n'
        '  <div class="legend-item" id="bd-leg-superseded"><div class="legend-dot legend-dot-superseded"></div> Superseded (1)</div>\n'
        '  <div class="legend-item" id="bd-leg-lost"><div class="legend-dot legend-dot-lost"></div> Lost (1)</div>\n'
        '  <div class="legend-item" id="bd-leg-open"><div class="legend-dot legend-dot-open"></div> Open (1)</div>\n'
        "</div>"
    )


def test_render_breakdown_section_keeps_decimal_acceptance_above_99() -> None:
    html = render_breakdown_section(
        ReportCounts(total=1000, accepted=999, open=0, superseded=1, lost=0, not_shipped=1, acceptance_rate=99.9),
        ReportActivitySummary(time_span="3 days", time_range="Active days from Jul 1 - Jul 3"),
        avg_prs="12.0",
        avg_loc="5.5k",
    )

    assert 'id="bd-rate">99.9%</div>' in html


def test_render_timeline_bootstrap_matches_injected_script_shape() -> None:
    assert render_timeline_bootstrap("[1]", '{"a":[]}', '["a"]', "2026-07-02") == (
        "var TL_ALL = [1];\n"
        'var TL_REPOS = {"a":[]};\n'
        'var TL_NAMES = ["a"];\n'
        "var TL_TODAY = '2026-07-02';"
    )


def test_render_repo_status_sections_orders_by_display_repos_and_skips_empty() -> None:
    items = [
        _item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped"),
        _item(number=2, repo="owner/beta", classification="open", statusKey="open"),
    ]

    html = render_repo_status_sections(repos=["owner/beta", "owner/alpha", "owner/empty"], items=items)

    assert html.index('<h2><a class="plain-link" href="https://github.com/owner/beta">owner/beta</a> (1 PRs)</h2>') < html.index('<h2><a class="plain-link" href="https://github.com/owner/alpha">owner/alpha</a> (1 PRs)</h2>')
    assert "empty" not in html
    assert '<table class="repo-status">' in html


def test_leaderboard_idle_status_uses_ps1_ladder() -> None:
    assert leaderboard_idle_status(0.5) == ("green", "Active")
    assert leaderboard_idle_status(1.5) == ("green", "Recent")
    assert leaderboard_idle_status(4.5) == ("yellow", "Slowing")
    assert leaderboard_idle_status(10.5) == ("dim", "Quiet")
    assert leaderboard_idle_status(999) == ("dim", "Gone")


def test_render_leaderboard_section_top_mode_statuses_and_projections() -> None:
    cache = _leaderboard_cache(
        {
            "alice": {"total": 30, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
            "bob": {"total": 10, "open": 0, "recentCount": 14, "lastCreatedAt": "2026-07-02T00:00:00Z"},
            "rodboev": {"total": 5, "open": 1, "recentCount": 7, "lastCreatedAt": "2026-07-02T00:00:00Z"},
        },
        shipped_counts={"alice": 30, "bob": 10, "rodboev": 4},
    )
    items = [_item(number=index, repo="owner/repo", classification="shipped", statusKey="shipped") for index in range(5)]

    html = render_leaderboard_section(repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev")

    assert '<h2><a class="plain-link" href="https://github.com/owner/repo">owner/repo</a> Community Leaderboard</h2>' in html
    assert 'id="lb-repo" data-collapse-mode="top" data-visible-items="10" data-rows-per-item="1"' in html
    assert ' collapsed' not in html
    assert "expand-row" not in html
    assert '  <tr data-rank="1"><td>#1</td><td><a href="https://github.com/alice">alice</a></td><td>30</td><td>0</td><td>0/d</td><td><span class="dim">Gone</span></td></tr>' in html
    assert '<td>2/d</td><td><span class="green">Active</span></td>' in html
    assert '<tr class="is-self" data-rank="3">' in html
    assert "<summary>Projections (rodboev @ 1/day Rate (7d), rank #3)</summary>" in html
    assert "  <tr><td>alice</td><td>30 (+25)</td><td>0/d</td><td>25d (Jul 27)</td></tr>" in html
    assert '  <tr><td>bob</td><td>10 (+5)</td><td>2/d</td><td class="red">not at current rates</td></tr>' in html


def test_render_leaderboard_section_context_mode_centers_author_window() -> None:
    stats = {
        f"user{index:02d}": {"total": 40 - index, "open": 0, "recentCount": 0, "lastCreatedAt": ""}
        for index in range(1, 15)
    }
    stats["rodboev"] = {"total": 1, "open": 0, "recentCount": 0, "lastCreatedAt": ""}
    cache = _leaderboard_cache(stats)
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_leaderboard_section(repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev")

    assert 'data-collapse-mode="context"' in html
    assert "data-visible-items" not in html
    assert ' collapsed' in html
    assert "Show all 15 contributors" in html
    assert '<tr class="context-hidden" data-rank="1">' in html
    assert '<tr class="context-hidden" data-rank="5">' in html
    assert '<tr data-rank="6">' in html
    assert '<tr class="is-self" data-rank="15">' in html
    # Expand row lands after the last visible row of the context window.
    assert html.index('data-rank="15"') < html.index("expand-row")


def test_render_leaderboard_section_caps_display_and_labels_top_50() -> None:
    stats = {
        f"user{index:03d}": {"total": 200 - index, "open": 0, "recentCount": 0, "lastCreatedAt": ""}
        for index in range(1, 60)
    }
    stats["rodboev"] = {"total": 500, "open": 0, "recentCount": 7, "lastCreatedAt": "2026-07-02T00:00:00Z"}
    cache = _leaderboard_cache(stats)
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_leaderboard_section(repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev")

    assert "Show top 50" in html
    assert 'data-rank="50"' in html
    assert 'data-rank="51"' not in html


def test_render_leaderboard_section_returns_empty_without_cached_board() -> None:
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    assert render_leaderboard_section(repo="owner/repo", items=items, cache=Cache(), now=NOW, author="rodboev") == ""


def test_render_leaderboard_projections_absent_when_author_rate_is_zero() -> None:
    cache = _leaderboard_cache(
        {
            "alice": {"total": 30, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
            "rodboev": {"total": 5, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
        },
    )
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_leaderboard_section(repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev")

    assert "projections" not in html


def test_render_representative_section_rows_release_and_via() -> None:
    html = render_representative_section(
        [
            RepresentativeItem(
                number=3571,
                url="https://github.com/nesquena/hermes-webui/pull/3571",
                repo="nesquena/hermes-webui",
                repoLabel="webui",
                desc="adds a saved prompts library",
                release="v0.51.338",
                releaseUrl="https://github.com/nesquena/hermes-webui/releases/tag/v0.51.338",
                viaLabel="#3860",
                viaUrl="https://github.com/nesquena/hermes-webui/pull/3860",
            ),
            RepresentativeItem(
                number=200,
                url="https://github.com/kenn-io/agentsview/pull/200",
                repo="kenn-io/agentsview",
                repoLabel="agentsview",
                desc="continued work",
                classification="accepted-indirect",
            ),
        ],
    )

    assert html.startswith("    <h2>Representative PRs</h2>\n")
    assert '<table class="rep-prs-table shipped-prs">' in html
    assert (
        '  <tr class="rep-main-row"><td><a href="https://github.com/nesquena/hermes-webui/pull/3571">#3571</a></td>'
        '<td><a class="plain-link" href="https://github.com/nesquena/hermes-webui">webui</a></td><td class="rep-desc-cell">adds a saved prompts library</td>'
        '<td><a href="https://github.com/nesquena/hermes-webui/releases/tag/v0.51.338">v0.51.338</a></td>'
        '<td><a href="https://github.com/nesquena/hermes-webui/pull/3860">#3860</a></td></tr>'
    ) in html
    assert '<div class="rep-desc-text">adds a saved prompts library</div>' in html
    assert "<td>indirect</td><td></td></tr>" in html


def test_render_representative_section_empty_state() -> None:
    assert render_representative_section([]) == '<p class="empty-state">Representative PRs unavailable.</p>'


def test_render_report_page_fills_slots() -> None:
    assert render_report_page("<html>{{ breakdown }}|{{ today }}</html>", {"breakdown": "B", "today": "T"}) == "<html>B|T</html>"


def test_render_report_page_rejects_template_missing_slots() -> None:
    with pytest.raises(ValueError, match="missing slots: today"):
        render_report_page("<html>{{ breakdown }}</html>", {"breakdown": "B", "today": "T"})


def test_render_report_page_rejects_unknown_template_slots() -> None:
    with pytest.raises(ValueError, match="unknown slots: mystery"):
        render_report_page("<html>{{ breakdown }}{{ mystery }}</html>", {"breakdown": "B"})


def test_render_pr_controls_and_table_matches_ps1_owned_markup() -> None:
    items = [
        _item(number=1, classification="shipped", statusKey="shipped"),
        _item(number=2, classification="open", statusKey="open"),
        _item(number=3, classification="lost", statusKey="lost"),
    ]

    assert render_pr_controls_and_table(
        items=items,
        display_repos=["nesquena/hermes-webui"],
        visible_items=20,
    ) == (
        '<div class="landscape-row" id="pr-landscape-row">\n'
        '  <div class="pr-filter-group pr-filter-group-left">\n'
        "    <h2>PRs</h2>\n"
        '    <div class="sort-pills" id="pr-repo-pills">\n'
        '    <div class="sort-pill active" data-repo="all">All</div>\n'
        '    <div class="sort-pill" data-repo="hermes-webui">hermes-webui</div>\n'
        "    </div>\n"
        "  </div>\n"
        '  <div class="pr-filter-group pr-filter-group-right">\n'
        '    <div class="sort-pills" id="pr-filter-pills">\n'
        '    <div class="sort-pill" data-status="open">Open (1)</div>\n'
        '    <div class="sort-pill active" data-status="shipped">Shipped (1)</div>\n'
        '    <div class="sort-pill" data-status="not-shipped">Not Shipped (1)</div>\n'
        "    </div>\n"
        "  </div>\n"
        "</div>\n"
        '<div class="collapsible-table collapsed" id="pr-list-collapsible" data-collapse-mode="top" data-visible-items="20" data-rows-per-item="2">\n'
        '<table class="targets-table pr-list-table" id="pr-list-table">\n'
        "  <colgroup>\n"
        '    <col class="pr-col-pr">\n'
        '    <col class="pr-col-repo">\n'
        '    <col class="pr-col-status">\n'
        '    <col class="pr-col-date">\n'
        '    <col class="pr-col-release">\n'
        '    <col class="pr-col-via">\n'
        "  </colgroup>\n"
        "  <thead><tr><th>PR</th><th>Repo</th><th>Status</th><th>Date</th><th>Release</th><th>Via</th></tr></thead>\n"
        '  <tbody id="pr-list-body"></tbody>\n'
        "</table>\n"
        '<div class="overlay-row" onclick="toggleCollapsedTable(\'pr-list-collapsible\', event)">Collapse <span class="caret">&#9650;</span></div>\n'
        "</div>"
    )


def test_render_pr_bootstrap_uses_existing_script_surface() -> None:
    assert render_pr_bootstrap(items=[_item(number=1, title="Fix")]) == (
        'var PR_FILTERS = [{"key":"open","label":"Open","count":0},{"key":"shipped","label":"Shipped","count":1},{"key":"not-shipped","label":"Not Shipped","count":0}];\n'
        'var PR_DATA = [{"number":1,"url":"https://github.com/owner/repo/pull/1","repo":"owner/repo","repoLabel":"repo","title":"Fix","classification":"shipped","statusKey":"shipped","statusLabel":"Shipped","statusClass":"tag-shipped","dateLabel":"7/2/26 9:27 AM","releaseLabel":"","releaseUrl":"","viaLabel":"","viaUrl":"","createdAt":"2026-07-01T00:00:00Z","closedAt":"2026-07-02T13:27:37Z","mergedAt":"","additions":1,"deletions":2,"changedFiles":3}];\n'
        "var CURRENT_PR_FILTER = {\n"
        "  statusKey: 'shipped',\n"
        "  repoKey: 'all'\n"
        "};"
    )


def _leaderboard_cache(
    stats: dict[str, dict[str, object]],
    *,
    shipped_counts: dict[str, int] | None = None,
) -> Cache:
    board: dict[str, object] = {"stats": stats}
    if shipped_counts is not None:
        board["shippedCounts"] = shipped_counts
    return Cache(leaderboards={"owner/repo|community-shipped-v4|all": board})


def _item(**overrides: object) -> PrReportItem:
    data: dict[str, object] = {
        "number": 1,
        "url": "https://github.com/owner/repo/pull/1",
        "repo": "owner/repo",
        "repoLabel": "repo",
        "title": "Title",
        "classification": "shipped",
        "statusKey": "shipped",
        "statusLabel": "Shipped",
        "statusClass": "tag-shipped",
        "dateLabel": "7/2/26 9:27 AM",
        "releaseLabel": "",
        "releaseUrl": "",
        "viaLabel": "",
        "viaUrl": "",
        "createdAt": "2026-07-01T00:00:00Z",
        "closedAt": "2026-07-02T13:27:37Z",
        "mergedAt": "",
        "additions": 1,
        "deletions": 2,
        "changedFiles": 3,
    }
    data.update(overrides)
    return PrReportItem(
        number=int(data["number"]),
        url=str(data["url"]),
        repo=str(data["repo"]),
        repoLabel=str(data["repoLabel"]),
        title=str(data["title"]),
        classification=str(data["classification"]),
        statusKey=str(data["statusKey"]),
        statusLabel=str(data["statusLabel"]),
        statusClass=str(data["statusClass"]),
        dateLabel=str(data["dateLabel"]),
        releaseLabel=str(data["releaseLabel"]),
        releaseUrl=str(data["releaseUrl"]),
        viaLabel=str(data["viaLabel"]),
        viaUrl=str(data["viaUrl"]),
        createdAt=str(data["createdAt"]),
        closedAt=str(data["closedAt"]),
        mergedAt=str(data["mergedAt"]),
        additions=int(data["additions"]),
        deletions=int(data["deletions"]),
        changedFiles=int(data["changedFiles"]),
    )
