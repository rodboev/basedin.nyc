from __future__ import annotations

import json
import re
from pathlib import Path

from core.classify import ClassificationResult
from core.html import (
    ClassificationDisplay,
    ReportSanityInput,
    SortPill,
    compact_script_json,
    generated_report_sanity_issues,
    previous_report_total_prs,
    render_pr_bootstrap_script,
    render_sort_pills,
    render_status_tag,
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


def test_status_rendering_is_data_driven_by_classification_result() -> None:
    display = {
        "a": ClassificationDisplay(label="Alpha", tag_class="tag-a"),
        "b": ClassificationDisplay(label="Beta", tag_class="tag-b"),
    }

    assert render_status_tag(ClassificationResult(classification="a"), display) == '<span class="tag-a">Alpha</span>'
    assert render_status_tag(ClassificationResult(classification="b"), display) == '<span class="tag-b">Beta</span>'


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
