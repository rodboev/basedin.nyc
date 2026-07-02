from __future__ import annotations

from core.page import render_breakdown_section
from core.report import ReportActivitySummary, ReportCounts


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
