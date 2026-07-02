from __future__ import annotations

import json
import re
from pathlib import Path

from core.classify import ClassificationResult
from core.html import (
    BarSegment,
    ClassificationDisplay,
    LegendItem,
    ReportSanityInput,
    SortPill,
    StatCard,
    StatusRow,
    collapse_caret,
    compact_script_json,
    generated_report_sanity_issues,
    normalize_generated_html,
    previous_report_total_prs,
    render_bar_segments,
    render_collapse_overlay,
    render_expand_row,
    render_legend_items,
    render_pr_bootstrap_script,
    render_pr_table_shell,
    render_repo_status_section,
    render_sort_pills,
    render_stat_grid,
    render_status_tag,
    render_tag,
    write_report_if_sane,
)


def test_previous_report_total_prs_reads_existing_total() -> None:
    assert previous_report_total_prs('<div class="number">42</div><div class="label">Total PRs</div>') == 42
    assert previous_report_total_prs("<html></html>") is None


def test_sanity_gate_detects_empty_and_failed_report() -> None:
    issues = generated_report_sanity_issues(
        ReportSanityInput(
            reported_count=0,
            fetched_count=0,
            acceptance_closed=0,
            open_count=0,
            failed_repos=("a", "b"),
            repo_count=2,
        ),
    )

    assert issues == [
        "GitHub returned 0 author PRs across all repos",
        "every configured repo PR list fetch failed",
        "report would contain no PRs",
        "acceptance rate would be N/A with no open PRs",
    ]


def test_sanity_gate_detects_drop_from_existing_output() -> None:
    issues = generated_report_sanity_issues(
        ReportSanityInput(
            reported_count=9,
            fetched_count=9,
            acceptance_closed=1,
            open_count=1,
            failed_repos=(),
            repo_count=1,
            cached_classification_count=50,
        ),
        existing_output='<div class="number">30</div><div class="label">Total PRs</div>',
    )

    assert issues == [
        "reported total dropped from 30 to 9 (>50% loss, likely partial API failure)",
        "reported total 9 is far below 50 cached PR classifications",
    ]


def test_write_report_if_sane_leaves_existing_file_untouched_without_force(tmp_path: Path) -> None:
    out_file = tmp_path / "index.html"
    out_file.write_text('<div class="number">30</div><div class="label">Total PRs</div>', encoding="utf-8")

    issues = write_report_if_sane(
        out_file=out_file,
        html="broken",
        report=ReportSanityInput(
            reported_count=0,
            fetched_count=0,
            acceptance_closed=0,
            open_count=0,
            failed_repos=("repo",),
            repo_count=1,
        ),
    )

    assert issues
    assert out_file.read_text(encoding="utf-8") == '<div class="number">30</div><div class="label">Total PRs</div>'


def test_write_report_if_sane_force_write_overrides_guard(tmp_path: Path) -> None:
    out_file = tmp_path / "index.html"
    out_file.write_text("old", encoding="utf-8")

    issues = write_report_if_sane(
        out_file=out_file,
        html="new",
        report=ReportSanityInput(
            reported_count=0,
            fetched_count=0,
            acceptance_closed=0,
            open_count=0,
            failed_repos=("repo",),
            repo_count=1,
        ),
        force_write=True,
    )

    assert issues == []
    assert out_file.read_text(encoding="utf-8") == "new"


def test_normalize_generated_html_masks_dates_and_whitespace() -> None:
    left = "<p>Generated July 2, 2026 from GitHub API</p>\n<script>var TL_TODAY = '2026-07-02';</script><script src=\"timeline.js?v=2026-07-02\"></script>"
    right = "<p>Generated July 3, 2026 from GitHub API</p> <script>var TL_TODAY = '2026-07-03';</script><script src=\"timeline.js?v=2026-07-03\"></script>"

    assert normalize_generated_html(left) == normalize_generated_html(right)


def test_status_rendering_is_data_driven_by_classification_result() -> None:
    display = {
        "a": ClassificationDisplay(label="Alpha", tag_class="tag-a"),
        "b": ClassificationDisplay(label="Beta", tag_class="tag-b"),
    }

    assert render_status_tag(ClassificationResult(classification="a"), display) == '<span class="tag-a">Alpha</span>'
    assert render_status_tag(ClassificationResult(classification="b"), display) == '<span class="tag-b">Beta</span>'


def test_generic_tag_and_stat_grid_render_existing_structure() -> None:
    assert render_tag(label="Alpha", tag_class="tag-alpha") == '<span class="tag tag-alpha">Alpha</span>'
    assert render_stat_grid(
        [
            StatCard(value="12", label="Total PRs", value_id="bd-total"),
            StatCard(value="98%", label="Rate", value_class="green", value_id="bd-rate", label_id="bd-rate-label"),
        ],
    ) == (
        '<div class="grid grid-summary">\n'
        '  <div class="stat-card"><div class="number" id="bd-total">12</div><div class="label">Total PRs</div></div>\n'
        '  <div class="stat-card"><div class="number green" id="bd-rate">98%</div><div class="label" id="bd-rate-label">Rate</div></div>\n'
        "</div>"
    )


def test_bar_and_legend_render_existing_ids_and_data_attributes() -> None:
    assert render_bar_segments([BarSegment(key="alpha", width=12.5, title="3", content="3")]) == (
        '  <div class="bar-segment bar-alpha" id="bd-bar-alpha" data-width="12.5" title="3">3</div>\n'
    )
    assert render_legend_items([LegendItem(key="alpha", label="Alpha", count=3)]) == (
        '  <div class="legend-item" id="bd-leg-alpha"><div class="legend-dot legend-dot-alpha"></div> Alpha (3)</div>\n'
    )


def test_repo_status_section_renders_existing_table_shape() -> None:
    assert render_repo_status_section(
        title="repo (2 PRs)",
        rows=[StatusRow(label="Alpha", tag_class="tag-alpha", count=2, details="Merged")],
    ) == (
        "<h2>repo (2 PRs)</h2>\n"
        '<table class="repo-status">\n'
        "  <tr><th>Status</th><th>Count</th><th>Details</th></tr>\n"
        '  <tr><td><span class="tag tag-alpha">Alpha</span></td><td>2</td><td>Merged</td></tr>\n'
        "</table>\n"
    )


def test_collapse_helpers_match_existing_markup() -> None:
    assert collapse_caret(up=False) == "&#9660;"
    assert collapse_caret(up=True) == "&#9650;"
    assert render_expand_row(block_id="pr-list", label="Show all 10") == (
        '  <tr class="expand-row" onclick="toggleCollapsedTable(\'pr-list\', event)">'
        '<td colspan="6">Show all 10 <span class="caret">&#9660;</span></td></tr>\n'
    )
    assert render_collapse_overlay(block_id="pr-list") == (
        '<div class="overlay-row" onclick="toggleCollapsedTable(\'pr-list\', event)">'
        'Collapse <span class="caret">&#9650;</span></div>\n'
    )


def test_pr_table_shell_renders_existing_ids_and_columns() -> None:
    assert render_pr_table_shell(visible_items=20) == (
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
        '<div class="overlay-row" onclick="toggleCollapsedTable(\'pr-list-collapsible\', event)">'
        'Collapse <span class="caret">&#9650;</span></div>\n'
        "</div>"
    )


def test_compact_script_json_matches_ps1_shape_and_escapes_script_end() -> None:
    assert compact_script_json([{"key": "a", "body": "</script>"}]) == r'[{"key":"a","body":"<\/script>"}]'


def test_sort_pills_render_existing_data_attribute_shape() -> None:
    assert render_sort_pills(
        [
            SortPill(key="all", label="All"),
            SortPill(key="repo", label="repo", count=3),
        ],
        active_key="all",
        data_attribute="repo",
    ) == (
        '    <div class="sort-pill active" data-repo="all">All</div>\n'
        '    <div class="sort-pill" data-repo="repo">repo (3)</div>\n'
    )


def test_pr_bootstrap_script_matches_ps1_variable_surface() -> None:
    script = render_pr_bootstrap_script(
        filters=[{"key": "a", "label": "A", "count": 1}],
        items=[{"number": 1, "title": "Fix"}],
        default_status_key="a",
        default_repo_key="all",
    )

    assert script == (
        'var PR_FILTERS = [{"key":"a","label":"A","count":1}];\n'
        'var PR_DATA = [{"number":1,"title":"Fix"}];\n'
        "var CURRENT_PR_FILTER = {\n"
        "  statusKey: 'a',\n"
        "  repoKey: 'all'\n"
        "};"
    )


def test_pr_bootstrap_script_matches_generated_index_surface(repo_root: Path) -> None:
    content = (repo_root / "index.html").read_text(encoding="utf-8")
    filters = json.loads(_script_json(content, "PR_FILTERS"))
    items = json.loads(_script_json(content, "PR_DATA"))
    expected = re.search(r"var PR_FILTERS = .*?var CURRENT_PR_FILTER = \{\s*statusKey: '[^']+',\s*repoKey: '[^']+'\s*\};", content, re.S)
    assert expected is not None

    assert render_pr_bootstrap_script(
        filters=filters,
        items=items,
        default_status_key="shipped",
        default_repo_key="all",
    ) == expected.group(0)


def test_html_module_does_not_embed_classification_status_vocabulary(repo_root: Path) -> None:
    source = (repo_root / "core" / "html.py").read_text(encoding="utf-8").lower()

    assert "shipped" not in source
    assert "accepted-indirect" not in source
    assert "withdrawn" not in source
    assert "superseded" not in source
    assert "lost" not in source


def _script_json(content: str, name: str) -> str:
    match = re.search(rf"var {name} = (.*?);", content, re.S)
    assert match is not None
    return match.group(1)
