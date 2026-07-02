from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

SHIPPED_CLASSIFICATIONS = {"shipped", "accepted-indirect"}
EASTERN = ZoneInfo("America/New_York")

TimelinePr = dict[str, str | int | bool]
TimelineDay = dict[str, str | int | float]


def to_eastern_date(iso_str: str) -> str:
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d")


def repo_short_name(full_name: str) -> str:
    return full_name.split("/")[-1]


def load_active_repos_from_text(ps1_text: str) -> list[str]:
    match = re.search(r"\[string\[\]\]\$Repos\s*=\s*@\((.*?)\)\s*,", ps1_text, re.DOTALL)
    if not match:
        raise ValueError("could not find $Repos default list")

    active_block = "\n".join(line.split("#", 1)[0] for line in match.group(1).splitlines())
    repos = re.findall(r'"([^"]+)"', active_block)
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
    daily_opened: defaultdict[str, dict[str, int]] = defaultdict(
        lambda: dict(count=0, loc=0, files=0, additions=0, deletions=0, clsShipped=0, clsOpen=0, clsSuperseded=0, clsLost=0),
    )
    daily_shipped: defaultdict[str, dict[str, int]] = defaultdict(lambda: dict(count=0, loc=0, files=0))

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

        if pr["isShipped"]:
            opened["clsShipped"] += 1
        elif pr["classification"] == "open":
            opened["clsOpen"] += 1
        elif pr["classification"] == "superseded":
            opened["clsSuperseded"] += 1
        elif pr["classification"] == "lost":
            opened["clsLost"] += 1

        if pr["isShipped"] and pr["resolvedDate"]:
            shipped = daily_shipped[str(pr["resolvedDate"])]
            shipped["count"] += 1
            shipped["loc"] += loc
            shipped["files"] += int(pr["changedFiles"])

    all_dates = sorted(set(daily_opened) | set(daily_shipped))
    chart_data: list[TimelineDay] = []
    cum_opened = 0
    cum_loc = 0
    cum_shipped = 0

    for day in all_dates:
        opened = daily_opened.get(day, _empty_opened())
        shipped = daily_shipped.get(day, _empty_shipped())
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
                "clsShipped": opened["clsShipped"],
                "clsOpen": opened["clsOpen"],
                "clsSuperseded": opened["clsSuperseded"],
                "clsLost": opened["clsLost"],
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


def _empty_opened() -> dict[str, int]:
    return dict(count=0, loc=0, files=0, additions=0, deletions=0, clsShipped=0, clsOpen=0, clsSuperseded=0, clsLost=0)


def _empty_shipped() -> dict[str, int]:
    return dict(count=0, loc=0, files=0)
