from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.report import ReportActivitySummary, ReportCounts, resolved_iso_date

SHIPPED_CLASSIFICATIONS = {"shipped", "accepted-indirect"}
EASTERN = ZoneInfo("America/New_York")

# Mirrors BD_LOAD_RANGES in timeline.js: the windows the load animation walks, in order.
BD_LOAD_RANGES = (2, 7, 14, 30, 0)

# The static breakdown is rendered at the first window. Not 1: a 1-day window is the only one in the
# data that closed nothing but shipped work, so its rate is a 0-denominator 100% that drops to 84%
# the moment a second day is in scope, and it is the only frame that would render a 4-char rate,
# since format_acceptance_rate switches to decimals above 99. Not 0 either: today's PRs are all still
# open, which is a 0/0 rate and a 100% open bar.
BD_LOAD_SEED_RANGE = BD_LOAD_RANGES[0]

# bd-rate alone seeds from the second window. A rate over BD_LOAD_SEED_RANGE is decided by whether a
# single bad day sits inside it, and no window that narrow can state one honestly; this is the
# narrowest window no single day can swing. The trade is that bd-rate disagrees with the counts and
# with bd-rate-label beneath it until the first phase lands.
BD_RATE_SEED_RANGE = BD_LOAD_RANGES[1]

TimelinePr = dict[str, str | int | bool]
TimelineDay = dict[str, str | int | float]


@dataclass(frozen=True)
class BreakdownSeed:
    counts: ReportCounts
    activity: ReportActivitySummary
    avg_prs: str
    avg_loc: str


def to_eastern_date(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d")


def repo_short_name(full_name: str) -> str:
    return full_name.split("/")[-1]


def load_active_repos_from_text(text: str) -> list[str]:
    repos = [entry for line in text.splitlines() if (entry := line.split("#", 1)[0].strip())]
    if not repos:
        raise ValueError("no active repos found")
    return repos


def load_pr_data_from_html(html: str) -> list[dict[str, Any]]:
    match = re.search(r"var PR_DATA = (\[.*?\]);", html, re.DOTALL)
    if not match:
        raise ValueError("could not find PR_DATA")

    items = json.loads(match.group(1))
    if not isinstance(items, list):
        raise ValueError("PR_DATA is not a list")
    required = {"repo", "classification", "createdAt", "closedAt", "mergedAt", "additions", "deletions", "changedFiles"}
    missing = required.difference(items[0].keys() if items else set())
    if missing:
        raise ValueError(f"PR_DATA missing fields required for timeline injection: {', '.join(sorted(missing))}")
    return items


def prepare_timeline_prs(pr_items: list[dict[str, Any]]) -> list[TimelinePr]:
    all_prs: list[TimelinePr] = []
    for item in pr_items:
        classification = str(item["classification"])
        is_shipped = classification in SHIPPED_CLASSIFICATIONS

        created_iso = str(item.get("createdAt") or "")
        created_date = to_eastern_date(created_iso) if created_iso else ""
        # Same rule the PR table dates its rows by, so the two can't drift apart.
        resolved_iso = resolved_iso_date(
            merged_at=str(item.get("mergedAt") or ""),
            closed_at=str(item.get("closedAt") or ""),
            created_at=created_iso,
        )
        resolved_date = to_eastern_date(resolved_iso) if resolved_iso else ""

        all_prs.append(
            {
                "repo": str(item["repo"]),
                "number": int(item["number"]),
                "additions": int(item.get("additions", 0) or 0),
                "deletions": int(item.get("deletions", 0) or 0),
                "changedFiles": int(item.get("changedFiles", 0) or 0),
                "classification": classification,
                "isShipped": is_shipped,
                "createdDate": created_date,
                "resolvedDate": resolved_date,
            },
        )
    return all_prs


def aggregate_daily(prs: list[TimelinePr]) -> list[TimelineDay]:
    daily_opened: defaultdict[str, dict[str, int]] = defaultdict(_empty_opened)
    daily_shipped: defaultdict[str, dict[str, int]] = defaultdict(_empty_shipped)
    # Outcomes land on the day the maintainer acted, so a range reads as "what happened in this
    # window" rather than "how the PRs opened in it turned out". A PR opened in June and closed
    # yesterday belongs to yesterday. Open PRs have no outcome yet and stay on their opened day.
    daily_class: defaultdict[str, dict[str, int]] = defaultdict(_empty_class)

    for pr in prs:
        if pr["classification"] == "withdrawn":
            continue

        day = str(pr["createdDate"])
        opened = daily_opened[day]
        # Net, not churn: a refactor that rewrites a file in place moves no lines, and summing both
        # sides double-counts every touched line (a 27.9k/27.2k i18n reshuffle reads as 55k).
        loc = int(pr["additions"]) - int(pr["deletions"])
        opened["count"] += 1
        opened["loc"] += loc
        opened["files"] += int(pr["changedFiles"])
        opened["additions"] += int(pr["additions"])
        opened["deletions"] += int(pr["deletions"])

        if pr["classification"] == "open":
            opened["clsOpen"] += 1
        else:
            outcome = daily_class[str(pr["resolvedDate"])]
            if pr["isShipped"]:
                outcome["clsShipped"] += 1
            elif pr["classification"] == "superseded":
                outcome["clsSuperseded"] += 1
            elif pr["classification"] == "lost":
                outcome["clsLost"] += 1

        if pr["isShipped"]:
            shipped = daily_shipped[str(pr["resolvedDate"])]
            shipped["count"] += 1
            shipped["loc"] += loc
            shipped["files"] += int(pr["changedFiles"])

    all_dates = sorted(set(daily_opened) | set(daily_shipped) | set(daily_class))
    chart_data: list[TimelineDay] = []
    cum_opened = 0
    cum_loc = 0
    cum_shipped = 0

    for day in all_dates:
        opened = daily_opened.get(day, _empty_opened())
        shipped = daily_shipped.get(day, _empty_shipped())
        outcome = daily_class.get(day, _empty_class())
        cum_opened += opened["count"]
        cum_loc += opened["loc"]
        cum_shipped += shipped["count"]

        chart_data.append(
            {
                "date": day,
                "prsOpened": opened["count"],
                "prsShipped": shipped["count"],
                "loc": opened["loc"],
                "additions": opened["additions"],
                "deletions": opened["deletions"],
                "files": opened["files"],
                "shippedLoc": shipped["loc"],
                "cumOpened": cum_opened,
                "cumShipped": cum_shipped,
                "cumLoc": cum_loc,
                "locPerPr": round(opened["loc"] / opened["count"]) if opened["count"] else 0,
                "filesPerPr": round(opened["files"] / opened["count"], 1) if opened["count"] else 0,
                "clsShipped": outcome["clsShipped"],
                "clsOpen": opened["clsOpen"],
                "clsSuperseded": outcome["clsSuperseded"],
                "clsLost": outcome["clsLost"],
            },
        )

    return chart_data


def build_daily_data(all_prs: list[TimelinePr], repos: list[str]) -> tuple[list[TimelineDay], dict[str, list[TimelineDay]], list[str]]:
    by_repo: defaultdict[str, list[TimelinePr]] = defaultdict(list)
    for pr in all_prs:
        by_repo[repo_short_name(str(pr["repo"]))].append(pr)

    repo_data = {name: aggregate_daily(prs) for name, prs in by_repo.items()}
    aggregate = aggregate_daily(all_prs)
    repo_names = [repo_short_name(repo) for repo in repos]
    return aggregate, repo_data, repo_names


def build_chart_payload(
    chart_data: list[TimelineDay],
    repo_data: dict[str, list[TimelineDay]],
    repo_names: list[str],
) -> tuple[str, str, str]:
    chart_json = json.dumps(chart_data).replace("</script", r"<\/script")
    repo_json = json.dumps(repo_data).replace("</script", r"<\/script")
    names_json = json.dumps(repo_names).replace("</script", r"<\/script")
    return chart_json, repo_json, names_json


def slice_daily(chart_data: list[TimelineDay], days: int) -> list[TimelineDay]:
    """Port of sliceData() in timeline.js: days=0 means every day, else the tail from last-days on."""
    if not days or not chart_data:
        return list(chart_data)
    cutoff = (date.fromisoformat(str(chart_data[-1]["date"])) - timedelta(days=days)).isoformat()
    return [day for day in chart_data if str(day["date"]) >= cutoff]


def breakdown_seed(chart_data: list[TimelineDay], today: str) -> BreakdownSeed:
    """The frame the load animation starts from, as bdStats()/bdDisplay() in timeline.js compute it.

    Must stay identical to the JS, or the static markup jumps on the first rendered frame. The
    window is BD_LOAD_SEED_RANGE; see the note on it for why shorter windows are unusable.
    """
    window = slice_daily(chart_data, BD_LOAD_SEED_RANGE)
    opened = shipped = open_ = superseded = lost = loc = active_days = 0
    first_active = last_active = prev_active = ""
    for day in window:
        day_opened = int(day["prsOpened"])
        opened += day_opened
        shipped += int(day["clsShipped"])
        open_ += int(day["clsOpen"])
        superseded += int(day["clsSuperseded"])
        lost += int(day["clsLost"])
        loc += int(day["loc"])
        if day_opened > 0:
            active_days += 1
            if not first_active:
                first_active = str(day["date"])
            prev_active, last_active = last_active, str(day["date"])
    display_days = active_days
    # Today is still in progress, so it counts for neither the day tally nor the range end.
    if last_active == today:
        display_days = max(0, display_days - 1)
        last_active = prev_active
    divisor = max(1, active_days)
    raw_avg_loc = round(loc / divisor)
    return BreakdownSeed(
        counts=ReportCounts(
            total=shipped + open_ + superseded + lost,
            accepted=shipped,
            open=open_,
            superseded=superseded,
            lost=lost,
            not_shipped=superseded + lost,
            acceptance_rate=_window_acceptance_rate(chart_data, BD_RATE_SEED_RANGE),
        ),
        activity=ReportActivitySummary(
            time_span="1 day" if display_days == 1 else f"{display_days} days",
            time_range=_active_days_label(first_active, last_active),
        ),
        avg_prs=str(int(opened / divisor + 0.5)),
        avg_loc=f"{raw_avg_loc / 1000:.1f}k" if raw_avg_loc >= 1000 else str(raw_avg_loc),
    )


def _window_acceptance_rate(chart_data: list[TimelineDay], days: int) -> float:
    """Matches `states[0].rate = states[1].rate` in animateOnLoad plus bdDisplay's null-to-0 coercion."""
    window = slice_daily(chart_data, days)
    shipped = sum(int(day["clsShipped"]) for day in window)
    closed = shipped + sum(int(day["clsSuperseded"]) + int(day["clsLost"]) for day in window)
    return (shipped / closed * 100) if closed > 0 else 0.0


def _active_days_label(first_active: str, last_active: str) -> str:
    """Port of the bd-days-label branch in updateBreakdown(); an all-today window has no range."""
    if not first_active or not last_active:
        return "No active days in range"
    first, last = date.fromisoformat(first_active), date.fromisoformat(last_active)
    return f"Active days from {first.strftime('%b')} {first.day} - {last.strftime('%b')} {last.day}"


def _empty_opened() -> dict[str, int]:
    return dict(count=0, loc=0, files=0, additions=0, deletions=0, clsOpen=0)


def _empty_shipped() -> dict[str, int]:
    return dict(count=0, loc=0, files=0)


def _empty_class() -> dict[str, int]:
    return dict(clsShipped=0, clsSuperseded=0, clsLost=0)
