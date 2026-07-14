from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from pathlib import Path

from core.classify import ClassificationResult


@dataclass(frozen=True)
class ClassificationDisplay:
    label: str
    tag_class: str


@dataclass(frozen=True)
class ReportSanityInput:
    reported_count: int
    fetched_count: int
    acceptance_closed: int
    open_count: int
    failed_repos: tuple[str, ...]
    repo_count: int
    cached_classification_count: int = 0


@dataclass(frozen=True)
class SortPill:
    key: str
    label: str
    count: int | None = None


@dataclass(frozen=True)
class StatCard:
    value: str
    label: str
    value_class: str = ""
    value_id: str = ""
    label_id: str = ""


@dataclass(frozen=True)
class BarSegment:
    key: str
    width: float
    title: str = ""
    content: str = ""


@dataclass(frozen=True)
class LegendItem:
    key: str
    label: str
    count: int


def generated_report_sanity_issues(report: ReportSanityInput, *, existing_output: str = "") -> list[str]:
    issues: list[str] = []
    if report.fetched_count == 0:
        issues.append("GitHub returned 0 author PRs across all repos")
    if report.repo_count > 0 and len(report.failed_repos) == report.repo_count:
        issues.append("every configured repo PR list fetch failed")
    if report.reported_count == 0 and report.open_count == 0:
        issues.append("report would contain no PRs")
    if report.acceptance_closed == 0 and report.open_count == 0:
        issues.append("acceptance rate would be N/A with no open PRs")

    previous_total = previous_report_total_prs(existing_output)
    if previous_total is not None and previous_total >= 20 and report.reported_count < previous_total // 2:
        issues.append(f"reported total dropped from {previous_total} to {report.reported_count} (>50% loss, likely partial API failure)")
    if report.cached_classification_count >= 50 and report.reported_count < report.cached_classification_count // 2:
        issues.append(f"reported total {report.reported_count} is far below {report.cached_classification_count} cached PR classifications")
    return issues


def previous_report_total_prs(html: str) -> int | None:
    match = re.search(r'<div class="number">(\d+)</div><div class="label">Total PRs</div>', html)
    return int(match.group(1)) if match else None


def write_report_if_sane(
    *,
    out_file: Path,
    html: str,
    report: ReportSanityInput,
    force_write: bool = False,
) -> list[str]:
    existing_output = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
    issues = [] if force_write else generated_report_sanity_issues(report, existing_output=existing_output)
    if issues:
        return issues
    out_file.write_text(html, encoding="utf-8")
    return []


def normalize_generated_html(html: str) -> str:
    normalized = re.sub(r"Generated [^.]+ from GitHub API", "Generated <DATE> from GitHub API", html)
    normalized = re.sub(r"var TL_TODAY = '[^']+';", "var TL_TODAY = '<DATE>';", normalized)
    normalized = re.sub(r"timeline\.js\?v=[0-9-]+", "timeline.js?v=<DATE>", normalized)
    normalized = re.sub(r">\s+<", "><", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip()


def render_status_tag(
    result: ClassificationResult,
    display_by_classification: Mapping[str, ClassificationDisplay],
) -> str:
    display = display_by_classification[result.classification]
    return f'<span class="{escape(display.tag_class, quote=True)}">{escape(display.label)}</span>'


def render_tag(*, label: str, tag_class: str) -> str:
    return f'<span class="tag {escape(tag_class, quote=True)}">{escape(label)}</span>'


def render_stat_card(card: StatCard) -> str:
    value_class = f' class="number {escape(card.value_class, quote=True)}"' if card.value_class else ' class="number"'
    value_id = f' id="{escape(card.value_id, quote=True)}"' if card.value_id else ""
    label_id = f' id="{escape(card.label_id, quote=True)}"' if card.label_id else ""
    return (
        f'<div class="stat-card"><div{value_class}{value_id}>{escape(card.value)}</div>'
        f'<div class="label"{label_id}>{escape(card.label)}</div></div>'
    )


def render_stat_grid(cards: list[StatCard]) -> str:
    return '<div class="grid grid-summary">\n  ' + "\n  ".join(render_stat_card(card) for card in cards) + "\n</div>"


def render_bar_segments(segments: list[BarSegment]) -> str:
    lines: list[str] = []
    for segment in segments:
        title = f' title="{escape(segment.title, quote=True)}"' if segment.title else ""
        lines.append(
            f'  <div class="bar-segment bar-{escape(segment.key, quote=True)}" '
            f'id="bd-bar-{escape(segment.key, quote=True)}" data-width="{segment.width:g}"{title}>'
            f"{escape(segment.content)}</div>",
        )
    return "\n".join(lines) + ("\n" if lines else "")


def render_legend_items(items: list[LegendItem]) -> str:
    lines = [
        f'  <div class="legend-item" id="bd-leg-{escape(item.key, quote=True)}">'
        f'<div class="legend-dot legend-dot-{escape(item.key, quote=True)}"></div> '
        f"{escape(item.label)} ({item.count})</div>"
        for item in items
    ]
    return "\n".join(lines) + ("\n" if lines else "")


def collapse_caret(*, up: bool) -> str:
    return "&#9650;" if up else "&#9660;"


def render_expand_row(*, block_id: str, label: str, colspan: int = 6) -> str:
    return (
        f'  <tr class="expand-row" onclick="toggleCollapsedTable(\'{escape(block_id, quote=True)}\', event)">'
        f'<td colspan="{colspan}">{escape(label)} <span class="caret">{collapse_caret(up=False)}</span></td></tr>\n'
    )


def render_collapse_overlay(*, block_id: str, label: str = "Collapse") -> str:
    return (
        f'<div class="overlay-row" onclick="toggleCollapsedTable(\'{escape(block_id, quote=True)}\', event)">'
        f'{escape(label)} <span class="caret">{collapse_caret(up=True)}</span></div>\n'
    )


def render_pr_table_shell(*, visible_items: int, block_id: str = "pr-list-collapsible") -> str:
    escaped_block_id = escape(block_id, quote=True)
    return (
        f'<div class="collapsible-table collapsed" id="{escaped_block_id}" data-collapse-mode="top" '
        f'data-visible-items="{visible_items}" data-rows-per-item="2">\n'
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
        f"{render_collapse_overlay(block_id=block_id)}"
        "</div>"
    )


def compact_script_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</script", r"<\/script")


def render_sort_pills(pills: list[SortPill], *, active_key: str, data_attribute: str) -> str:
    lines: list[str] = []
    attr = escape(data_attribute, quote=True)
    for pill in pills:
        active = " active" if pill.key == active_key else ""
        label = pill.label if pill.count is None else f"{pill.label} ({pill.count})"
        lines.append(
            f'    <div class="sort-pill{active}" data-{attr}="{escape(pill.key, quote=True)}">{escape(label)}</div>',
        )
    return "\n".join(lines) + ("\n" if lines else "")


def render_pr_bootstrap_script(
    *,
    filters: list[Mapping[str, object]],
    items: list[Mapping[str, object]],
    default_status_key: str,
    default_repo_key: str,
) -> str:
    return (
        f"var PR_FILTERS = {compact_script_json(filters)};\n"
        f"var PR_DATA = {compact_script_json(items)};\n"
        "var CURRENT_PR_FILTER = {\n"
        f"  statusKey: '{_single_quoted_js(default_status_key)}',\n"
        f"  repoKey: '{_single_quoted_js(default_repo_key)}'\n"
        "};"
    )


def _single_quoted_js(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")
