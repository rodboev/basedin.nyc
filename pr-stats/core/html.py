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


def render_status_tag(
    result: ClassificationResult,
    display_by_classification: Mapping[str, ClassificationDisplay],
) -> str:
    display = display_by_classification[result.classification]
    return f'<span class="{escape(display.tag_class, quote=True)}">{escape(display.label)}</span>'


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
