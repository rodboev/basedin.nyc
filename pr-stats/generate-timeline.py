#!/usr/bin/env python3
"""Post-process pr-stats/index.html: inject a Progress chart and avg stat cards.

Reads the classification cache from generate.ps1 and fetches LOC data from GitHub.
Run after generate.ps1 to augment its output in-place.
"""

import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = Path(__file__).parent
CACHE_FILE = SCRIPT_DIR / ".pr-classification-cache.json"
INDEX_FILE = SCRIPT_DIR / "index.html"
GENERATE_PS1 = SCRIPT_DIR / "generate.ps1"

AUTHOR = "rodboev"
SHIPPED_CLASSIFICATIONS = {"shipped", "accepted-indirect"}
EASTERN = ZoneInfo("America/New_York")

CHART_MARKER = "<!-- timeline-chart -->"


def fetch_prs(repo, author):
    result = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--author", author,
         "--state", "all", "--limit", "500",
         "--json", "number,state,createdAt,closedAt,mergedAt,additions,deletions,changedFiles"],
        capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        print(f"    WARN: fetch failed for {repo}", file=sys.stderr)
        return []
    return json.loads(result.stdout)


def load_classifications(cache_file):
    if not cache_file.exists():
        return {}
    with open(cache_file, encoding="utf-8") as f:
        cache = json.load(f)
    return {
        key: entry.get("classification", "")
        for key, entry in cache.get("entries", {}).items()
    }


def to_eastern_date(iso_str):
    dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return dt.astimezone(EASTERN).strftime("%Y-%m-%d")


def classify_pr(pr, repo, classifications):
    cache_key = f"{repo}#{pr['number']}"
    if pr["state"] == "OPEN":
        return "open"
    if cache_key in classifications:
        return classifications[cache_key]
    if pr.get("mergedAt"):
        return "shipped"
    return "lost"


def repo_short_name(full_name):
    return full_name.split("/")[-1]


def load_active_repos(ps1_path):
    text = ps1_path.read_text(encoding="utf-8")
    match = re.search(r"\[string\[\]\]\$Repos\s*=\s*@\((.*?)\)\s*,", text, re.DOTALL)
    if not match:
        raise ValueError(f"could not find $Repos default list in {ps1_path}")

    active_block = "\n".join(
        line.split("#", 1)[0]
        for line in match.group(1).splitlines()
    )
    repos = re.findall(r'"([^"]+)"', active_block)
    if not repos:
        raise ValueError(f"no active repos found in {ps1_path}")
    return repos


def _aggregate_daily(prs):
    daily_opened = defaultdict(lambda: dict(count=0, loc=0, files=0, additions=0, deletions=0,
                                             clsShipped=0, clsOpen=0, clsSuperseded=0, clsLost=0))
    daily_shipped = defaultdict(lambda: dict(count=0, loc=0, files=0))

    for pr in prs:
        if pr["classification"] == "withdrawn":
            continue

        day = pr["createdDate"]
        d = daily_opened[day]
        loc = pr["additions"] + pr["deletions"]
        d["count"] += 1
        d["loc"] += loc
        d["files"] += pr["changedFiles"]
        d["additions"] += pr["additions"]
        d["deletions"] += pr["deletions"]

        if pr["isShipped"]:
            d["clsShipped"] += 1
        elif pr["classification"] == "open":
            d["clsOpen"] += 1
        elif pr["classification"] == "superseded":
            d["clsSuperseded"] += 1
        elif pr["classification"] == "lost":
            d["clsLost"] += 1

        if pr["isShipped"] and pr["resolvedDate"]:
            s = daily_shipped[pr["resolvedDate"]]
            s["count"] += 1
            s["loc"] += loc
            s["files"] += pr["changedFiles"]

    all_dates = sorted(set(list(daily_opened) + list(daily_shipped)))

    chart_data = []
    cum_opened = cum_loc = cum_shipped = 0
    empty_opened = dict(count=0, loc=0, files=0, additions=0, deletions=0,
                        clsShipped=0, clsOpen=0, clsSuperseded=0, clsLost=0)
    empty_shipped = dict(count=0, loc=0, files=0)

    for day in all_dates:
        o = daily_opened.get(day, empty_opened)
        s = daily_shipped.get(day, empty_shipped)
        cum_opened += o["count"]
        cum_loc += o["loc"]
        cum_shipped += s["count"]

        chart_data.append(dict(
            date=day,
            prsOpened=o["count"],
            prsShipped=s["count"],
            loc=o["loc"],
            additions=o["additions"],
            deletions=o["deletions"],
            files=o["files"],
            shippedLoc=s["loc"],
            cumOpened=cum_opened,
            cumShipped=cum_shipped,
            cumLoc=cum_loc,
            locPerPr=round(o["loc"] / o["count"]) if o["count"] else 0,
            filesPerPr=round(o["files"] / o["count"], 1) if o["count"] else 0,
            clsShipped=o["clsShipped"],
            clsOpen=o["clsOpen"],
            clsSuperseded=o["clsSuperseded"],
            clsLost=o["clsLost"],
        ))

    return chart_data


def build_daily_data(all_prs, repos):
    by_repo = defaultdict(list)
    for pr in all_prs:
        by_repo[repo_short_name(pr["repo"])].append(pr)

    repo_data = {name: _aggregate_daily(prs) for name, prs in by_repo.items()}
    aggregate = _aggregate_daily(all_prs)
    repo_names = [repo_short_name(r) for r in repos]
    return aggregate, repo_data, repo_names


def build_chart_html(chart_data, repo_data, repo_names):
    chart_json = json.dumps(chart_data).replace("</script", r"<\/script")
    repo_json = json.dumps(repo_data).replace("</script", r"<\/script")
    names_json = json.dumps(repo_names).replace("</script", r"<\/script")
    active_days = len([d for d in chart_data if d["prsOpened"] > 0])
    total_loc = sum(d["loc"] for d in chart_data)
    total_opened = sum(d["prsOpened"] for d in chart_data)
    raw_avg_loc = round(total_loc / active_days) if active_days else 0
    avg_loc = f"{raw_avg_loc / 1000:.1f}k" if raw_avg_loc >= 1000 else str(raw_avg_loc)
    avg_prs = str(round(total_opened / active_days, 1)) if active_days else "0"

    return chart_json, repo_json, names_json, avg_prs, avg_loc


def inject_into_index(html, chart_json, repo_json, names_json, avg_prs, avg_loc):
    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    # Remove prior injection if re-running
    html = re.sub(
        rf'{re.escape(CHART_MARKER)}.*?{re.escape(CHART_MARKER)}',
        '', html, flags=re.DOTALL
    )

    # 1. Add Chart.js CDN before </head>
    chartjs_tags = (
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>\n'
        '    <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>\n'
    )
    if "chart.js@4" not in html:
        html = html.replace("</head>", f"    {chartjs_tags}  </head>")

    # 2. Add avg stat cards before the active-days card
    avg_cards = (
        f'  <div class="stat-card"><div class="number" id="bd-avg-prs">{avg_prs}</div>'
        f'<div class="label">Avg PRs/day</div></div>\n'
        f'  <div class="stat-card"><div class="number" id="bd-avg-loc">{avg_loc}</div>'
        f'<div class="label">Avg LOC/day</div></div>\n'
    )
    active_days_pattern = r'(<div class="stat-card"><div class="number blue"[^>]*>\d+ days?)'
    html = re.sub(r'\s*<div class="stat-card"><div class="number"[^>]*>[^<]*</div><div class="label">Avg PRs/day</div></div>\n?', '', html)
    html = re.sub(r'\s*<div class="stat-card"><div class="number"[^>]*>[^<]*</div><div class="label">Avg LOC/day</div></div>\n?', '', html)
    html = re.sub(active_days_pattern, avg_cards + r'\1', html)

    # 3. Remove range pills from Breakdown (now in Progress section)
    html = re.sub(
        r'<div class="landscape-row"[^>]*>\s*\n<h2>Breakdown</h2>\n'
        r'<div class="sort-pills" id="bd-range-pills">.*?</div>\s*\n</div>\s*\n</div>',
        '<h2>Breakdown</h2>', html, flags=re.DOTALL
    )

    # 4. Insert chart section before <h2>Methodology</h2>
    chart_section = f"""{CHART_MARKER}
<div class="landscape-row" style="margin-top:2rem;position:static">
  <div class="pr-filter-group pr-filter-group-left">
    <h2>Progress</h2>
    <div class="sort-pills" id="tl-view-pills">
    <div class="sort-pill active" data-view="daily">Daily</div>
    <div class="sort-pill" data-view="cumulative"><span class="cumul-full">Cumulative</span><span class="cumul-short">Cum.</span></div>
    </div>
  </div>
  <div class="pr-filter-group pr-filter-group-right">
    <div class="sort-pills" id="bd-range-pills">
      <div class="sort-pill" data-range="7">7d</div>
      <div class="sort-pill" data-range="14">14d</div>
      <div class="sort-pill" data-range="30">30d</div>
      <div class="sort-pill active" data-range="0">All</div>
    </div>
  </div>
</div>
<div id="tl-daily-wrap"><canvas id="tlDailyChart"></canvas></div>
<div id="tl-cumulative-wrap" style="display:none"><canvas id="tlCumulativeChart"></canvas></div>
<div class="sort-pills tl-repo-pills" id="tl-repo-pills"></div>

<script>
var TL_ALL = {chart_json};
var TL_REPOS = {repo_json};
var TL_NAMES = {names_json};
var TL_TODAY = '{today}';
</script>
<script src="timeline.js?v={today}"></script>
{CHART_MARKER}
"""
    # Insert after the breakdown legend div (before the first repo heading)
    m = re.search(r'(</div>\s*<div class="legend">.*?</div>\s*</div>)\s*\n', html, re.DOTALL)
    if m:
        insert_at = m.end()
        html = html[:insert_at] + '\n' + chart_section + '\n' + html[insert_at:]
    else:
        html = html.replace("<h2>Methodology</h2>", chart_section + "\n<h2>Methodology</h2>")
    return html


def main():
    if not INDEX_FILE.exists():
        print(f"ERROR: {INDEX_FILE} not found. Run generate.ps1 first.", file=sys.stderr)
        sys.exit(1)

    try:
        repos = load_active_repos(GENERATE_PS1)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    classifications = load_classifications(CACHE_FILE)
    print(f"Loaded {len(classifications)} classifications from cache", file=sys.stderr)

    all_prs = []
    for repo in repos:
        print(f"  {repo}...", file=sys.stderr)
        prs = fetch_prs(repo, AUTHOR)
        for pr in prs:
            classification = classify_pr(pr, repo, classifications)
            is_shipped = classification in SHIPPED_CLASSIFICATIONS

            created_date = to_eastern_date(pr["createdAt"])
            resolved_iso = pr.get("mergedAt") or pr.get("closedAt") or ""
            resolved_date = to_eastern_date(resolved_iso) if resolved_iso else ""

            all_prs.append(dict(
                repo=repo,
                number=pr["number"],
                additions=pr.get("additions", 0) or 0,
                deletions=pr.get("deletions", 0) or 0,
                changedFiles=pr.get("changedFiles", 0) or 0,
                classification=classification,
                isShipped=is_shipped,
                createdDate=created_date,
                resolvedDate=resolved_date,
            ))

    print(f"  {len(all_prs)} PRs total", file=sys.stderr)

    chart_data, repo_data, repo_names = build_daily_data(all_prs, repos)
    chart_json, repo_json, names_json, avg_prs, avg_loc = build_chart_html(chart_data, repo_data, repo_names)

    html = INDEX_FILE.read_text(encoding="utf-8")
    html = inject_into_index(html, chart_json, repo_json, names_json, avg_prs, avg_loc)
    INDEX_FILE.write_text(html, encoding="utf-8")
    print(f"Injected chart + stat cards into {INDEX_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
