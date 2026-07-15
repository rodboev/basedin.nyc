from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta
from html import escape

from jinja2 import Environment, StrictUndefined, meta

from core.html import (
    BarSegment,
    LegendItem,
    SortPill,
    StatCard,
    render_bar_segments,
    render_collapse_overlay,
    render_expand_row,
    render_legend_items,
    render_pr_bootstrap_script,
    render_pr_table_shell,
    render_sort_pills,
    render_stat_grid,
    render_tag,
)
from core.leaderboard import CachedLeaderboardRow, cached_leaderboard_rows, configured_repo_leaderboard_exclusions
from core.repos import display_repo
from core.models import Cache
from core.report import (
    CLASSIFICATION_STATUS_META,
    EASTERN,
    PrReportItem,
    ReportActivitySummary,
    ReportCounts,
    RepresentativeItem,
    default_status_filter_dicts,
    format_acceptance_rate,
    repo_filter_dicts,
    report_bar_items,
    report_counts,
    report_items_to_script_dicts,
)

REPORT_TEMPLATE_SLOTS = frozenset(
    {
        "breakdown",
        "timeline_bootstrap",
        "today",
        "repo_matrix",
        "leaderboard_sections",
        "representative_section",
        "pr_controls",
        "pr_bootstrap",
        "generated_date",
    },
)


def render_report_page(template_text: str, context: Mapping[str, str]) -> str:
    env = Environment(undefined=StrictUndefined, autoescape=False, keep_trailing_newline=True)
    declared = meta.find_undeclared_variables(env.parse(template_text))
    missing_slots = set(context) - declared
    unknown_slots = declared - set(context)
    if missing_slots or unknown_slots:
        problems = []
        if missing_slots:
            problems.append(f"template is missing slots: {', '.join(sorted(missing_slots))}")
        if unknown_slots:
            problems.append(f"template uses unknown slots: {', '.join(sorted(unknown_slots))}")
        raise ValueError("; ".join(problems))
    return env.from_string(template_text).render(dict(context))


def render_breakdown_section(
    counts: ReportCounts,
    activity: ReportActivitySummary,
    *,
    avg_prs: str,
    avg_loc: str,
) -> str:
    primary_cards = [
        StatCard(value=str(counts.total), label="Total PRs", value_id="bd-total"),
        StatCard(value=str(counts.accepted), label="Shipped", value_class="green", value_id="bd-shipped"),
        StatCard(value=str(counts.open), label="Open", value_class="yellow", value_id="bd-open"),
        StatCard(value=str(counts.not_shipped), label="Lost/Superseded", value_id="bd-lost-sup"),
    ]
    rate = format_acceptance_rate(counts.acceptance_rate)
    secondary_cards = [
        StatCard(
            value=f"{rate}%",
            label=f"Acceptance rate ({counts.superseded} superseded, {counts.lost} lost)",
            value_class="green",
            value_id="bd-rate",
            label_id="bd-rate-label",
        ),
        StatCard(value=avg_prs, label="Avg PRs/day", value_id="bd-avg-prs"),
        StatCard(value=avg_loc, label="Avg LOC/day", value_id="bd-avg-loc"),
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


def render_timeline_bootstrap(chart_json: str, repo_json: str, names_json: str, today_label: str) -> str:
    return (
        f"var TL_ALL = {chart_json};\n"
        f"var TL_REPOS = {repo_json};\n"
        f"var TL_NAMES = {names_json};\n"
        f"var TL_TODAY = '{today_label}';"
    )


def render_repo_link(repo: str) -> str:
    label = display_repo(repo)
    return f'<a class="plain-link" href="https://github.com/{escape(label, quote=True)}">{escape(label)}</a>'


def render_repo_matrix_link(repo: str) -> str:
    label = display_repo(repo)
    short = label.rsplit("/", 1)[-1]
    return (
        f'<a class="plain-link" href="https://github.com/{escape(label, quote=True)}">'
        f'<span class="repo-full">{escape(label)}</span>'
        f'<span class="repo-short">{escape(short)}</span></a>'
    )


def _repo_counts(items: Iterable[PrReportItem]) -> ReportCounts:
    return report_counts(
        items,
        accepted_classifications=("shipped", "accepted-indirect"),
        open_status="open",
        superseded_status="superseded",
        lost_status="lost",
    )


def _matrix_cell(count: int) -> str:
    return '<td class="dim">0</td>' if count == 0 else f"<td>{count}</td>"


def _matrix_heading(*, label: str, tag_class: str, total: int) -> str:
    return (
        '<span class="matrix-heading">'
        f"{render_tag(label=label, tag_class=tag_class)}"
        f'<span class="matrix-heading-width" aria-hidden="true">{total}</span></span>'
    )


def render_repo_matrix_section(
    *,
    repos: Iterable[str],
    items: Iterable[PrReportItem],
    cache: Cache,
    now: datetime,
    author: str,
) -> str:
    item_list = list(items)
    rows: list[str] = []
    totals = [0, 0, 0, 0, 0]
    rank_digits = 1
    peer_digits = 1
    for repo in repos:
        repo_items = [item for item in item_list if item.repo == repo]
        if not repo_items:
            continue
        counts = _repo_counts(repo_items)
        for index, value in enumerate(
            (counts.accepted, counts.open, counts.superseded, counts.lost, counts.total),
        ):
            totals[index] += value
        board = author_leaderboard_rows(cache=cache, repo=repo, items=repo_items, now=now, author=author)
        me = next((row for row in board if row.login.lower() == author.lower()), None)
        if me is None:
            standing = "<td></td><td></td>"
        else:
            rank_digits = max(rank_digits, len(str(me.rank)))
            peer_digits = max(peer_digits, len(str(len(board))))
            standing = (
                f'<td><span class="rank-place">{me.rank}</span>'
                f'<span class="rank-sep">/</span>'
                f'<span class="rank-field">{len(board)}</span></td>'
                f"<td>{me.rate:g}/d</td>"
            )
        rows.append(
            f"  <tr><td>{render_repo_matrix_link(repo)}</td>"
            f"{_matrix_cell(counts.accepted)}{_matrix_cell(counts.open)}"
            f"{_matrix_cell(counts.superseded)}{_matrix_cell(counts.lost)}"
            f"<td>{counts.total}</td>{standing}</tr>\n",
        )
    if not rows:
        return ""
    shipped, opened, superseded, lost, total = totals
    # The total row is the next row in the zebra sequence, so it stripes when it lands on an odd one.
    foot_class = ' class="stripe"' if len(rows) % 2 == 0 else ""
    legend_entries: dict[str, str] = {}
    for status in ("shipped", "open", "superseded", "lost"):
        label, tag_class, details = CLASSIFICATION_STATUS_META[status]
        legend_entries[status] = (
            f'<div class="repo-legend-entry repo-legend-entry-{status}" role="listitem">'
            f"{render_tag(label=label, tag_class=tag_class)}"
            f'<span class="repo-legend-copy">{escape(details)}</span></div>'
        )
    # Widest rank and field size drive the two halves of the rank cell so the slashes line up.
    return (
        f'<table class="repo-matrix" style="--rank-digits:{rank_digits};--peer-digits:{peer_digits}">\n'
        "  <thead><tr><th>Repo</th>"
        f"<th>{_matrix_heading(label='Shipped', tag_class='tag-shipped', total=shipped)}</th>"
        f"<th>{_matrix_heading(label='Open', tag_class='tag-open', total=opened)}</th>"
        f"<th>{_matrix_heading(label='Superseded', tag_class='tag-superseded', total=superseded)}</th>"
        f"<th>{_matrix_heading(label='Lost', tag_class='tag-lost', total=lost)}</th>"
        "<th>Total</th><th>Rank</th><th>Rate (7d)</th></tr></thead>\n"
        "  <tbody>\n"
        f"{''.join(rows)}  </tbody>\n"
        f"  <tfoot><tr{foot_class}><td>Total</td>"
        f"<td>{shipped}</td><td>{opened}</td><td>{superseded}</td><td>{lost}</td>"
        f"<td>{total}</td><td></td><td></td></tr></tfoot>\n"
        "</table>\n"
        '<div class="repo-legend" role="list" aria-label="Pull request status definitions">\n'
        f'  <div class="repo-legend-group">{legend_entries["shipped"]}{legend_entries["superseded"]}</div>\n'
        f'  <div class="repo-legend-group repo-legend-group-right">{legend_entries["open"]}'
        f'{legend_entries["lost"]}</div>\n'
        "</div>"
    )


def author_leaderboard_rows(
    *,
    cache: Cache,
    repo: str,
    items: list[PrReportItem],
    now: datetime,
    author: str,
) -> list[CachedLeaderboardRow]:
    return cached_leaderboard_rows(
        cache=cache,
        repo=repo,
        exclusions=configured_repo_leaderboard_exclusions(repo),
        now=now,
        rate_window_days=7,
        author_login=author,
        author_credited=sum(1 for item in items if item.statusKey == "shipped"),
        author_open=sum(1 for item in items if item.statusKey == "open"),
        author_recent_created=[item.createdAt for item in items if item.classification != "withdrawn"],
        max_entries=None,
    )


def render_leaderboard_sections(
    *,
    repos: Iterable[str],
    items: Iterable[PrReportItem],
    cache: Cache,
    now: datetime,
    author: str,
    visible_entries: int = 10,
    max_entries: int = 50,
) -> str:
    item_list = list(items)
    sections = []
    for repo in repos:
        repo_items = [item for item in item_list if item.repo == repo]
        if not repo_items:
            continue
        section = render_leaderboard_section(
            repo=repo,
            items=repo_items,
            cache=cache,
            now=now,
            author=author,
            visible_entries=visible_entries,
            max_entries=max_entries,
        )
        if section:
            sections.append(f'<div class="leaderboard-cell">\n{section}</div>')
    if not sections:
        return ""
    cells = "\n".join(sections)
    return f'<h2>Community Leaderboards</h2>\n<div class="leaderboard-grid">\n{cells}\n</div>'


def render_leaderboard_section(
    *,
    repo: str,
    items: list[PrReportItem],
    cache: Cache,
    now: datetime,
    author: str,
    visible_entries: int = 10,
    max_entries: int = 50,
) -> str:
    rows = author_leaderboard_rows(cache=cache, repo=repo, items=items, now=now, author=author)
    if not rows:
        return ""
    total_community = len(rows)
    display = rows[:max_entries]
    total_contributors = len(display)
    my_rank = next(
        (row.rank for row in rows if row.login.lower() == author.lower()),
        total_community + 1,
    )
    expand_label = (
        f"Show top {max_entries}" if total_community > max_entries else f"Show all {total_community} contributors"
    )

    if visible_entries < my_rank <= total_contributors:
        collapse_mode = "context"
        half_window = visible_entries // 2
        visible_start = max(1, my_rank - half_window)
        visible_end = min(total_contributors, visible_start + visible_entries - 1)
        if visible_end == total_contributors:
            visible_start = max(1, visible_end - visible_entries + 1)
    else:
        collapse_mode = "top"
        visible_start = 1
        visible_end = min(visible_entries, total_contributors)
    expand_after_rank = visible_end if collapse_mode == "context" else visible_entries

    short = repo.rsplit("/", 1)[-1]
    block_id = f"lb-{short}"
    row_html: list[str] = []
    for row in display:
        row_classes = []
        if row.login.lower() == author.lower():
            row_classes.append("is-self")
        if collapse_mode == "context" and (row.rank < visible_start or row.rank > visible_end):
            row_classes.append("context-hidden")
        class_attr = f' class="{" ".join(row_classes)}"' if row_classes else ""
        status_class, status_label = leaderboard_idle_status(row.idle)
        row_html.append(
            f'  <tr{class_attr} data-rank="{row.rank}"><td>#{row.rank}</td>'
            f'<td><a href="https://github.com/{escape(row.login, quote=True)}">{escape(row.login)}</a></td>'
            f"<td>{row.credited}</td><td>{row.open}</td><td>{row.rate:g}/d</td>"
            f'<td><span class="{status_class}">{status_label}</span></td></tr>\n',
        )
        if row.rank == expand_after_rank and total_contributors > visible_entries:
            row_html.append(render_expand_row(block_id=block_id, label=expand_label, colspan=6))

    collapsed_class = " collapsed" if total_contributors > visible_entries else ""
    overlay = render_collapse_overlay(block_id=block_id) if total_contributors > visible_entries else ""
    top_attrs = (
        f' data-visible-items="{visible_entries}" data-rows-per-item="1"' if collapse_mode == "top" else ""
    )
    projections = render_leaderboard_projections(rows=rows, author=author, my_rank=my_rank, now=now)
    return (
        f"<h3>{render_repo_link(repo)}</h3>\n"
        f'<div class="collapsible-table leaderboard{collapsed_class}" id="{escape(block_id, quote=True)}" '
        f'data-collapse-mode="{collapse_mode}"{top_attrs}>\n'
        "<table>\n"
        "  <thead><tr><th>Rank</th><th>Contributor</th><th>Shipped</th><th>Open</th><th>Rate</th>"
        "<th>Status</th></tr></thead>\n"
        "  <tbody>\n"
        f"{''.join(row_html)}  </tbody>\n"
        "</table>\n"
        f"{overlay}"
        "</div>\n"
        f"{projections}"
    )


def leaderboard_idle_status(idle: float) -> tuple[str, str]:
    if idle < 1:
        label = "Active"
    elif idle < 3:
        label = "Recent"
    elif idle < 7:
        label = "Slowing"
    elif idle < 14:
        label = "Quiet"
    else:
        label = "Gone"
    status_class = "green" if idle < 3 else ("yellow" if idle < 7 else "dim")
    return status_class, label


def render_leaderboard_projections(
    *,
    rows: list[CachedLeaderboardRow],
    author: str,
    my_rank: int,
    now: datetime,
) -> str:
    me = next((row for row in rows if row.login.lower() == author.lower()), None)
    my_credited = me.credited if me is not None else 0
    my_rate = me.rate if me is not None else 0.0
    ahead = [row for row in rows if row.credited > my_credited]
    if not ahead or my_rate <= 0:
        return ""
    local_now = now.astimezone(EASTERN)
    proj_rows: list[str] = []
    for row in ahead:
        gap = row.credited - my_credited
        net_rate = my_rate - row.rate
        if net_rate <= 0:
            catchup_cell = '<td class="red">not at current rates</td>'
        else:
            days = round(gap / net_rate, 1)
            when = local_now + timedelta(days=days)
            catchup_cell = f"<td>{days:g}d ({when.strftime('%b')} {when.day})</td>"
        proj_rows.append(
            f"  <tr><td>{escape(row.login)}</td><td>{row.credited} (+{gap})</td><td>{row.rate:g}/d</td>{catchup_cell}</tr>\n",
        )
    return (
        '<details class="projections">\n'
        f"<summary>Projections ({escape(author)} @ {my_rate:g}/day Rate, rank #{my_rank})</summary>\n"
        "<table>\n"
        "  <tr><th>Contributor</th><th>Shipped</th><th>Rate</th><th>Catch-up</th></tr>\n"
        f"{''.join(proj_rows)}</table>\n"
        "</details>\n"
    )


def render_representative_section(items: Iterable[RepresentativeItem]) -> str:
    item_list = list(items)
    if not item_list:
        return '<p class="empty-state">Representative PRs unavailable.</p>'
    rows: list[str] = []
    for item in item_list:
        release_cell = _representative_release_cell(item)
        via_cell = _linked_label(item.viaLabel, item.viaUrl)
        repo_cell = (
            f'<a class="plain-link" href="https://github.com/{escape(item.repo, quote=True)}">{escape(item.repoLabel)}</a>'
            if item.repo
            else escape(item.repoLabel)
        )
        rows.append(
            f'  <tr class="rep-main-row"><td><a href="{escape(item.url, quote=True)}">#{item.number}</a></td>'
            f'<td>{repo_cell}</td><td class="rep-desc-cell">{item.desc}</td>'
            f"<td>{release_cell}</td><td>{via_cell}</td></tr>\n"
            f'  <tr class="rep-desc-row"><td class="rep-desc-gap"></td>'
            f'<td colspan="4"><div class="rep-desc-text">{item.desc}</div></td></tr>\n',
        )
    return (
        "    <h2>Representative PRs</h2>\n"
        '<table class="rep-prs-table shipped-prs">\n'
        "  <tr><th>PR</th><th>Repo</th><th>Description</th><th>Release</th><th>Via</th></tr>\n"
        "\n"
        f"{''.join(rows)}</table>"
    )


def _representative_release_cell(item: RepresentativeItem) -> str:
    cell = _linked_label(item.release, item.releaseUrl)
    if cell:
        return cell
    if item.classification == "accepted-indirect":
        return "indirect"
    return ""


def _linked_label(label: str, url: str) -> str:
    if label and url:
        return f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
    if label:
        return escape(label)
    return ""


def render_pr_controls_and_table(
    *,
    items: Iterable[PrReportItem],
    display_repos: Iterable[str],
    visible_items: int,
    default_status_key: str = "shipped",
    default_repo_key: str = "all",
) -> str:
    item_list = list(items)
    counts = _repo_counts(item_list)
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
    counts = _repo_counts(item_list)
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
