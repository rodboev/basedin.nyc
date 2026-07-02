from __future__ import annotations

from collections.abc import Iterable, Mapping

from core.html import (
    BarSegment,
    LegendItem,
    SortPill,
    StatCard,
    render_bar_segments,
    render_legend_items,
    render_pr_bootstrap_script,
    render_pr_table_shell,
    render_sort_pills,
    render_stat_grid,
)
from core.report import (
    PrReportItem,
    ReportActivitySummary,
    ReportCounts,
    default_status_filter_dicts,
    repo_filter_dicts,
    report_bar_items,
    report_counts,
    report_items_to_script_dicts,
)


def render_breakdown_section(counts: ReportCounts, activity: ReportActivitySummary) -> str:
    primary_cards = [
        StatCard(value=str(counts.total), label="Total PRs", value_id="bd-total"),
        StatCard(value=str(counts.accepted), label="Shipped", value_class="green", value_id="bd-shipped"),
        StatCard(value=str(counts.open), label="Open", value_class="yellow", value_id="bd-open"),
        StatCard(value=str(counts.not_shipped), label="Lost/Superseded", value_id="bd-lost-sup"),
    ]
    rate = "N/A" if counts.acceptance_rate is None else str(counts.acceptance_rate)
    secondary_cards = [
        StatCard(
            value=f"{rate}%",
            label=f"Acceptance ({counts.superseded} superseded, {counts.lost} lost)",
            value_class="green",
            value_id="bd-rate",
            label_id="bd-rate-label",
        ),
        StatCard(
            value=activity.time_span,
            label=activity.time_range,
            value_class="blue",
            value_id="bd-days",
            label_id="bd-days-label",
        ),
    ]
    bar_items = report_bar_items(counts)
    segments = [BarSegment(key=item.key, width=item.width, title=item.title, content=item.content) for item in bar_items]
    legend = [LegendItem(key=item.key, label=item.label, count=item.count) for item in bar_items]
    return (
        "<h2>Breakdown</h2>\n\n"
        f"{render_stat_grid(primary_cards)}\n"
        f"{render_stat_grid(secondary_cards)}\n\n"
        "<div class=\"bar-container\">\n"
        f"{render_bar_segments(segments)}</div>\n"
        "<div class=\"legend\">\n"
        f"{render_legend_items(legend)}</div>"
    )


def render_pr_controls_and_table(
    *,
    items: Iterable[PrReportItem],
    display_repos: Iterable[str],
    visible_items: int,
    default_status_key: str = "shipped",
    default_repo_key: str = "all",
) -> str:
    item_list = list(items)
    counts = report_counts(
        item_list,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )
    status_filters = default_status_filter_dicts(counts)
    repo_filters = repo_filter_dicts(display_repos)
    repo_pills = [SortPill(key=entry["key"], label=entry["label"]) for entry in repo_filters]
    status_pills = [
        SortPill(key=str(entry["key"]), label=str(entry["label"]), count=_int_entry_value(entry["count"]))
        for entry in status_filters
    ]
    return (
        '<div class="landscape-row" id="pr-landscape-row">\n'
        '  <div class="pr-filter-group pr-filter-group-left">\n'
        "    <h2>PRs</h2>\n"
        '    <div class="sort-pills" id="pr-repo-pills">\n'
        f"{render_sort_pills(repo_pills, active_key=default_repo_key, data_attribute='repo')}"
        "    </div>\n"
        "  </div>\n"
        '  <div class="pr-filter-group pr-filter-group-right">\n'
        '    <div class="sort-pills" id="pr-filter-pills">\n'
        f"{render_sort_pills(status_pills, active_key=default_status_key, data_attribute='status')}"
        "    </div>\n"
        "  </div>\n"
        "</div>\n"
        f"{render_pr_table_shell(visible_items=visible_items)}"
    )


def render_pr_bootstrap(
    *,
    items: Iterable[PrReportItem],
    default_status_key: str = "shipped",
    default_repo_key: str = "all",
) -> str:
    item_list = list(items)
    counts = report_counts(
        item_list,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )
    filters: list[Mapping[str, object]] = [entry for entry in default_status_filter_dicts(counts)]
    script_items: list[Mapping[str, object]] = [entry for entry in report_items_to_script_dicts(item_list)]
    return render_pr_bootstrap_script(
        filters=filters,
        items=script_items,
        default_status_key=default_status_key,
        default_repo_key=default_repo_key,
    )


def _int_entry_value(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else int(str(value))
