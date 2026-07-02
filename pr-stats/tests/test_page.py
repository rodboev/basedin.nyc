from __future__ import annotations

from core.page import render_breakdown_section, render_pr_bootstrap, render_pr_controls_and_table
from core.report import PrReportItem, ReportActivitySummary, ReportCounts


def test_render_breakdown_section_matches_ps1_owned_markup() -> None:
    assert render_breakdown_section(
        ReportCounts(total=10, accepted=7, open=1, superseded=1, lost=1, not_shipped=2, acceptance_rate=78),
        ReportActivitySummary(time_span="3 days", time_range="Active days from Jul 1 - Jul 3"),
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
        '    <div class="sort-pill" data-repo="webui">webui</div>\n'
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
        'var PR_DATA = [{"number":1,"url":"https://github.com/owner/repo/pull/1","repo":"owner/repo","repoLabel":"repo","title":"Fix","classification":"shipped","statusKey":"shipped","statusLabel":"Shipped","statusClass":"tag-shipped","dateLabel":"7/2/26 9:27 AM","releaseLabel":"","viaLabel":"","viaUrl":"","createdAt":"2026-07-01T00:00:00Z","closedAt":"2026-07-02T13:27:37Z","mergedAt":"","additions":1,"deletions":2,"changedFiles":3}];\n'
        "var CURRENT_PR_FILTER = {\n"
        "  statusKey: 'shipped',\n"
        "  repoKey: 'all'\n"
        "};"
    )


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
        viaLabel=str(data["viaLabel"]),
        viaUrl=str(data["viaUrl"]),
        createdAt=str(data["createdAt"]),
        closedAt=str(data["closedAt"]),
        mergedAt=str(data["mergedAt"]),
        additions=int(data["additions"]),
        deletions=int(data["deletions"]),
        changedFiles=int(data["changedFiles"]),
    )
