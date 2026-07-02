from __future__ import annotations

from core.html import BarSegment, LegendItem, StatCard, render_bar_segments, render_legend_items, render_stat_grid
from core.report import ReportActivitySummary, ReportCounts, report_bar_items


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
