from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from core.report import ReportActivitySummary, ReportCounts

SHIPPED_CLASSIFICATIONS = {"shipped", "accepted-indirect"}
EASTERN = ZoneInfo("America/New_York")

# BD_LOAD_RANGES[0] in timeline.js. The static breakdown is rendered at this window.
BD_LOAD_SEED_RANGE = 1

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
        resolved_iso = str(item.get("mergedAt") or item.get("closedAt") or "")
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
        loc = int(pr["additions"]) + int(pr["deletions"])
        opened["count"] += 1
        opened["loc"] += loc
        opened["files"] += int(pr["changedFiles"])
        opened["additions"] += int(pr["additions"])
        opened["deletions"] += int(pr["deletions"])

        if pr["classification"] == "open":
            opened["clsOpen"] += 1
        else:
            outcome = daily_class[str(pr["resolvedDate"]) or day]
            if pr["isShipped"]:
                outcome["clsShipped"] += 1
            elif pr["classification"] == "superseded":
                outcome["clsSuperseded"] += 1
            elif pr["classification"] == "lost":
                outcome["clsLost"] += 1

        if pr["isShipped"] and pr["resolvedDate"]:
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
    window is BD_LOAD_RANGES[0]; anything shorter is a day whose PRs are all still open, which
    would put the acceptance rate at 0/0 and the bar at 100% open.
    """
    window = slice_daily(chart_data, BD_LOAD_SEED_RANGE)
    opened = shipped = open_ = superseded = lost = loc = active_days = 0
    today_active = False
    active_dates: list[str] = []
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
            active_dates.append(str(day["date"]))
            today_active = today_active or str(day["date"]) == today
    display_days = max(0, active_days - 1) if today_active else active_days
    closed = shipped + lost + superseded
    # bdDisplay() coerces a null rate to 0; nothing closed means there is no rate to show.
    rate = (shipped / closed * 100) if closed > 0 else 0.0
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
            acceptance_rate=rate,
        ),
        activity=ReportActivitySummary(
            time_span="1 day" if display_days == 1 else f"{display_days} days",
            time_range=_active_days_label(active_dates),
        ),
        avg_prs=str(int(opened / divisor + 0.5)),
        avg_loc=f"{raw_avg_loc / 1000:.1f}k" if raw_avg_loc >= 1000 else str(raw_avg_loc),
    )


def _active_days_label(active_dates: list[str]) -> str:
    """Port of the bd-days-label branch in updateBreakdown()."""
    if not active_dates:
        return "No active days in range"
    first, last = date.fromisoformat(active_dates[0]), date.fromisoformat(active_dates[-1])
    return f"Active days from {first.strftime('%b')} {first.day} - {last.strftime('%b')} {last.day}"


def _empty_opened() -> dict[str, int]:
    return dict(count=0, loc=0, files=0, additions=0, deletions=0, clsOpen=0)


def _empty_shipped() -> dict[str, int]:
    return dict(count=0, loc=0, files=0)


def _empty_class() -> dict[str, int]:
    return dict(clsShipped=0, clsSuperseded=0, clsLost=0)
