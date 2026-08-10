from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.models import Cache
from core.page import (
    render_breakdown_section,
    render_leaderboard_section,
    render_leaderboard_sections,
    render_pr_bootstrap,
    render_pr_controls_and_table,
    render_report_page,
    render_repo_matrix_section,
    render_timeline_bootstrap,
)
from core.report import PrReportItem, ReportActivitySummary, ReportCounts
from core.repos import set_repo_display_names

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
        '  <div class="stat-card"><div class="number green" id="bd-rate">78%</div><div class="label" id="bd-rate-label">Acceptance rate (1 superseded, 1 lost)</div></div>\n'
        '  <div class="stat-card"><div class="number" id="bd-avg-prs">12.0</div><div class="label">Avg PRs/day</div></div>\n'
        '  <div class="stat-card"><div class="number" id="bd-avg-loc">5.5k</div><div class="label">Avg net LOC/day</div></div>\n'
        '  <div class="stat-card"><div class="number blue" id="bd-days">3 days</div><div class="label" id="bd-days-label">Active days from Jul 1 - Jul 3</div></div>\n'
        "</div>\n\n"
        '<div class="bar-container">\n'
        '  <div class="bar-segment bar-shipped" id="bd-bar-shipped" style="width:70%" title="7">7</div>\n'
        '  <div class="bar-segment bar-superseded" id="bd-bar-superseded" style="width:10%">1</div>\n'
        '  <div class="bar-segment bar-lost" id="bd-bar-lost" style="width:10%">1</div>\n'
        '  <div class="bar-segment bar-open" id="bd-bar-open" style="width:10%" title="1">1</div>\n'
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


def test_render_repo_matrix_orders_by_display_repos_and_skips_empty() -> None:
    items = [
        _item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped"),
        _item(number=2, repo="owner/beta", classification="open", statusKey="open"),
    ]

    html = render_repo_matrix_section(
        repos=["owner/beta", "owner/alpha", "owner/empty"],
        items=items,
        cache=Cache(),
        now=NOW,
        author="rodboev",
    )

    assert '<table class="repo-matrix"' in html
    assert html.index("owner/beta") < html.index("owner/alpha")
    assert "empty" not in html


def test_render_repo_matrix_counts_totals_both_axes_and_dims_zero_cells() -> None:
    items = [
        _item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped"),
        _item(number=2, repo="owner/alpha", classification="accepted-indirect", statusKey="shipped"),
        _item(number=3, repo="owner/alpha", classification="lost", statusKey="lost"),
        _item(number=4, repo="owner/beta", classification="open", statusKey="open"),
    ]

    html = render_repo_matrix_section(
        repos=["owner/alpha", "owner/beta"],
        items=items,
        cache=Cache(),
        now=NOW,
        author="rodboev",
    )

    # alpha: 2 shipped (direct + indirect roll up), 0 open, 0 superseded, 1 lost, 3 total
    assert '<td>2</td><td class="dim">0</td><td class="dim">0</td><td>1</td><td>3</td>' in html
    # beta: 0 shipped, 1 open, 1 total
    assert '<td class="dim">0</td><td>1</td><td class="dim">0</td><td class="dim">0</td><td>1</td>' in html
    # Two repo rows, so the total lands on row 3 and keeps the odd-row stripe going.
    assert (
        '<tfoot><tr class="stripe"><td>Total</td><td>2</td><td>1</td><td>0</td><td>1</td>'
        "<td>4</td><td></td><td></td></tr></tfoot>"
    ) in html


def test_render_repo_matrix_drops_total_stripe_when_it_lands_on_an_even_row() -> None:
    items = [_item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["owner/alpha"], items=items, cache=Cache(), now=NOW, author="rodboev",
    )

    # One repo row, so the total is row 2: even, and the zebra sequence skips it.
    assert "<tfoot><tr><td>Total</td>" in html
    assert 'class="stripe"' not in html


def test_render_repo_matrix_heads_status_columns_with_two_tone_pills() -> None:
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["owner/repo"], items=items, cache=Cache(), now=NOW, author="rodboev",
    )

    assert '<span class="tag tag-shipped">Shipped</span><span class="matrix-heading-width" aria-hidden="true">1</span>' in html
    assert '<span class="tag tag-open">Open</span><span class="matrix-heading-width" aria-hidden="true">0</span>' in html
    assert '<span class="tag tag-superseded">Superseded</span><span class="matrix-heading-width" aria-hidden="true">0</span>' in html
    assert '<span class="tag tag-lost">Lost</span><span class="matrix-heading-width" aria-hidden="true">0</span>' in html
    assert "<h2>Repos</h2>" not in html


def test_render_repo_matrix_explains_status_pills_below_the_table() -> None:
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["owner/repo"], items=items, cache=Cache(), now=NOW, author="rodboev",
    )

    legend = html[html.index('<div class="repo-legend"'):]
    assert html.index("</table>") < html.index('<div class="repo-legend"')
    assert "<th" not in legend
    assert "Merged, released, or accepted with credit" in html
    assert "Pending review" in html
    assert "Replaced by a newer PR" in html
    assert "Maintainer-closed without acceptance" in html
    assert legend.count('role="listitem"') == 4
    for status in ("shipped", "open", "superseded", "lost"):
        assert f"repo-legend-entry-{status}" in legend
    assert legend.count('class="repo-legend-group"') == 1
    assert legend.count('class="repo-legend-group repo-legend-group-right"') == 1


def test_render_repo_matrix_joins_rank_over_field_with_rate_last() -> None:
    cache = _leaderboard_cache(
        {
            "alice": {"total": 30, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
            "rodboev": {"total": 5, "open": 0, "recentCount": 14, "lastCreatedAt": "2026-07-02T00:00:00Z"},
        },
        shipped_counts={"alice": 30, "rodboev": 1},
    )
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["owner/repo"], items=items, cache=cache, now=NOW, author="rodboev",
    )

    assert "<th>Total</th><th>Rank</th><th>Rate (7d)</th>" in html
    # Rank reads "2/2" with no pound sign; the slash is its own span so its gap is CSS-tunable.
    assert (
        '<td>1</td><td><span class="rank-place">2</span><span class="rank-sep">/</span>'
        '<span class="rank-field">2</span></td><td>2/d</td>'
    ) in html


def test_render_repo_matrix_sizes_rank_halves_to_the_widest_values() -> None:
    boards = {}
    for short, peers, mine in (("alpha", 4, 1), ("beta", 1111, 900)):
        stats = {f"u{n}": {"total": 500 - n, "open": 0, "recentCount": 0, "lastCreatedAt": ""} for n in range(peers - 1)}
        stats["rodboev"] = {"total": 500 - mine, "open": 0, "recentCount": 0, "lastCreatedAt": ""}
        boards[f"owner/{short}|community-shipped-v4|all"] = {"stats": stats}
    items = [
        _item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped"),
        _item(number=2, repo="owner/beta", classification="shipped", statusKey="shipped"),
    ]

    html = render_repo_matrix_section(
        repos=["owner/alpha", "owner/beta"], items=items, cache=Cache(leaderboards=boards), now=NOW, author="rodboev",
    )

    # Widest rank is 3 digits, widest field is 4, so both halves reserve that many characters.
    assert 'style="--rank-digits:3;--peer-digits:4"' in html


def test_render_repo_matrix_leaves_standing_blank_without_cached_board() -> None:
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["owner/repo"], items=items, cache=Cache(), now=NOW, author="rodboev",
    )

    # Total still renders; rank and rate stay empty.
    assert "<td>1</td><td></td><td></td></tr>" in html


def test_render_repo_matrix_labels_a_renamed_repo_as_written_in_repos_txt() -> None:
    set_repo_display_names({"data-privacy-stack/presidio": "microsoft/presidio"})
    items = [_item(number=1, repo="data-privacy-stack/presidio", classification="shipped", statusKey="shipped")]

    html = render_repo_matrix_section(
        repos=["data-privacy-stack/presidio"],
        items=items,
        cache=Cache(),
        now=NOW,
        author="rodboev",
    )

    assert '<a class="plain-link" href="https://github.com/microsoft/presidio">' in html
    assert '<span class="repo-full">microsoft/presidio</span>' in html
    assert '<span class="repo-short">presidio</span>' in html
    assert "data-privacy-stack" not in html


def test_render_leaderboard_section_top_mode_rows_and_projections() -> None:
    cache = _leaderboard_cache(
        {
            "alice": {"total": 30, "open": 0, "recentCount": 0, "lastCreatedAt": ""},
            "bob": {"total": 10, "open": 0, "recentCount": 14, "lastCreatedAt": "2026-07-02T00:00:00Z"},
            "rodboev": {"total": 5, "open": 1, "recentCount": 7, "lastCreatedAt": "2026-07-02T00:00:00Z"},
        },
        shipped_counts={"alice": 30, "bob": 10, "rodboev": 4},
    )
    items = [_item(number=index, repo="owner/repo", classification="shipped", statusKey="shipped") for index in range(5)]

    html = render_leaderboard_section(
        repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev", visible_entries=10,
    )

    # The repo name is a plain h3 now; the "Community Leaderboards" h2 spans the whole grid.
    assert '<h3><a class="plain-link" href="https://github.com/owner/repo">owner/repo</a></h3>' in html
    assert "<h2>" not in html
    assert 'id="lb-repo" data-collapse-mode="top" data-visible-items="10" data-rows-per-item="1"' in html
    assert ' collapsed' not in html
    assert "expand-row" not in html
    assert "<th>Rank</th><th>Contributor</th><th>Shipped</th><th>Open</th><th>Rate</th></tr>" in html
    assert '  <tr data-rank="1"><td>#1</td><td><a href="https://github.com/alice">alice</a></td><td>30</td><td>0</td><td>0/d</td></tr>' in html
    assert "<td>2/d</td></tr>" in html
    assert '<tr class="is-self" data-rank="3">' in html
    assert "<summary>Projections (rodboev @ 1/day Rate, rank #3)</summary>" in html
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

    html = render_leaderboard_section(
        repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev", visible_entries=10,
    )

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


def test_render_leaderboard_section_shows_ten_rows_before_expanding_by_default() -> None:
    stats = {
        f"user{index:02d}": {"total": 40 - index, "open": 0, "recentCount": 0, "lastCreatedAt": ""}
        for index in range(1, 16)
    }
    cache = _leaderboard_cache(stats)
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    html = render_leaderboard_section(repo="owner/repo", items=items, cache=cache, now=NOW, author="rodboev")

    assert 'data-visible-items="10" data-rows-per-item="1"' in html
    # The expand row lands after rank 10, so ranks 11 and beyond start collapsed.
    assert html.index('data-rank="10"') < html.index("expand-row") < html.index('data-rank="11"')


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


def test_render_leaderboard_sections_heads_the_grid_once_and_cells_each_board() -> None:
    cache = Cache(
        leaderboards={
            f"owner/{short}|community-shipped-v4|all": {
                "stats": {"alice": {"total": 30, "open": 0, "recentCount": 0, "lastCreatedAt": ""}},
            }
            for short in ("alpha", "beta")
        },
    )
    items = [
        _item(number=1, repo="owner/alpha", classification="shipped", statusKey="shipped"),
        _item(number=2, repo="owner/beta", classification="shipped", statusKey="shipped"),
    ]

    html = render_leaderboard_sections(
        repos=["owner/alpha", "owner/beta"], items=items, cache=cache, now=NOW, author="rodboev",
    )

    assert html.startswith('<h2>Community Leaderboards</h2>\n<div class="leaderboard-grid">')
    assert html.count("<h2>") == 1
    assert html.count('<div class="leaderboard-cell">') == 2
    assert html.count("<h3>") == 2


def test_render_leaderboard_sections_returns_empty_without_any_board() -> None:
    items = [_item(number=1, repo="owner/repo", classification="shipped", statusKey="shipped")]

    assert render_leaderboard_sections(
        repos=["owner/repo"], items=items, cache=Cache(), now=NOW, author="rodboev",
    ) == ""


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
