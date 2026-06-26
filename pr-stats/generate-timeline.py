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
    <div class="sort-pill" data-view="cumulative">Cumulative</div>
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
(function() {{
var TL_ALL = {chart_json};
var TL_REPOS = {repo_json};
var TL_NAMES = {names_json};
var TL_TODAY = '{today}';
var TL_TODAY_LABEL = fmtLabel(TL_TODAY);
var activeRepo = null;
function activeTL() {{ return activeRepo ? (TL_REPOS[activeRepo] || []) : TL_ALL; }}
var isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
var C = {{
  green: isDark ? '#3fb950' : '#1a7f37',
  blue: isDark ? '#58a6ff' : '#3376d2',
  purple: isDark ? '#bc8cff' : '#8250df',
  text: isDark ? '#e6edf3' : '#1a1a1a',
  grid: isDark ? 'rgba(139,148,158,0.15)' : 'rgba(0,0,0,0.06)',
}};
Chart.defaults.color = C.text;
Chart.defaults.borderColor = C.grid;

var pillsEl = document.getElementById('tl-repo-pills');
var allPill = document.createElement('div');
allPill.className = 'sort-pill active';
allPill.setAttribute('data-repo', '');
allPill.textContent = 'All';
pillsEl.appendChild(allPill);
var TL_LABELS = {{"hermes-webui": "webui"}};
TL_NAMES.forEach(function(name) {{
  var pill = document.createElement('div');
  pill.className = 'sort-pill';
  pill.setAttribute('data-repo', name);
  pill.textContent = TL_LABELS[name] || name;
  pillsEl.appendChild(pill);
}});

function fmtLabel(s) {{
  var p = s.split('-');
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return months[+p[1]-1] + ' ' + +p[2];
}}
function fmtK(v) {{ return v >= 1000 ? (v/1000).toFixed(1)+'k' : v; }}
function trendline(vals) {{
  var n = vals.length;
  if (n < 2) return vals.map(function() {{ return null; }});
  var sx=0, sy=0, sxx=0, sxy=0;
  for (var i=0; i<n; i++) {{ sx+=i; sy+=vals[i]; sxx+=i*i; sxy+=i*vals[i]; }}
  var m = (n*sxy - sx*sy) / (n*sxx - sx*sx);
  var b = (sy - m*sx) / n;
  return vals.map(function(_, i) {{ return i === 0 || i === n-1 ? Math.max(0, m*i + b) : null; }});
}}
function sliceData(days) {{
  var src = activeTL();
  if (!days || !src.length) return src;
  var last = src[src.length-1].date, p = last.split('-');
  var cut = new Date(p[0], p[1]-1, p[2]);
  cut.setDate(cut.getDate() - days + 1);
  var cs = cut.toISOString().slice(0,10);
  return src.filter(function(d) {{ return d.date >= cs; }});
}}
function updateBreakdown(r) {{
  var sl = sliceData(r);
  var total = 0, shipped = 0, open = 0, superseded = 0, lost = 0;
  var totalLoc = 0, activeDays = 0;
  var firstDate = null, lastDate = null, prevDate = null;
  for (var i = 0; i < sl.length; i++) {{
    var d = sl[i];
    total += d.prsOpened;
    shipped += (d.clsShipped || 0);
    open += (d.clsOpen || 0);
    superseded += (d.clsSuperseded || 0);
    lost += (d.clsLost || 0);
    totalLoc += d.loc;
    if (d.prsOpened > 0) {{
      activeDays++;
      if (!firstDate) firstDate = d.date;
      prevDate = lastDate;
      lastDate = d.date;
    }}
  }}
  if (lastDate === TL_TODAY && activeDays > 1) {{ activeDays--; lastDate = prevDate; }}
  var lostSup = lost + superseded;
  var closedDenom = shipped + lost + superseded;
  var rate = closedDenom > 0 ? Math.round(shipped / closedDenom * 100) : 'N/A';
  var el;
  if (el = document.getElementById('bd-total')) el.textContent = total;
  if (el = document.getElementById('bd-shipped')) el.textContent = shipped;
  if (el = document.getElementById('bd-open')) el.textContent = open;
  if (el = document.getElementById('bd-lost-sup')) el.textContent = lostSup;
  if (el = document.getElementById('bd-rate')) el.textContent = typeof rate === 'number' ? rate + '%' : rate;
  if (el = document.getElementById('bd-rate-label')) el.textContent = 'Acceptance (' + superseded + ' superseded, ' + lost + ' lost)';
  var dayStr = activeDays === 1 ? '1 day' : activeDays + ' days';
  if (el = document.getElementById('bd-days')) el.textContent = dayStr;
  if (firstDate && lastDate) {{
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var fp = firstDate.split('-'), lp = lastDate.split('-');
    if (el = document.getElementById('bd-days-label')) el.textContent =
      'Active days from ' + months[+fp[1]-1] + ' ' + +fp[2] + ' - ' + months[+lp[1]-1] + ' ' + +lp[2];
  }} else {{
    if (el = document.getElementById('bd-days-label')) el.textContent = 'No active days in range';
  }}
  var avgPrs = activeDays > 0 ? (total / activeDays).toFixed(1) : '0';
  var rawAvgLoc = activeDays > 0 ? Math.round(totalLoc / activeDays) : 0;
  var avgLoc = rawAvgLoc >= 1000 ? (rawAvgLoc / 1000).toFixed(1) + 'k' : String(rawAvgLoc);
  if (el = document.getElementById('bd-avg-prs')) el.textContent = avgPrs;
  if (el = document.getElementById('bd-avg-loc')) el.textContent = avgLoc;
  var barTotal = total || 1;
  var segs = [
    ['bd-bar-shipped', shipped], ['bd-bar-superseded', superseded],
    ['bd-bar-lost', lost], ['bd-bar-open', open]
  ];
  segs.forEach(function(s) {{
    var el = document.getElementById(s[0]);
    if (!el) return;
    var pct = (s[1] / barTotal * 100).toFixed(1);
    el.setAttribute('data-width', pct);
    el.style.width = pct + '%';
    var wide = parseFloat(pct) > 4;
    if (el.classList.contains('bar-open')) {{ el.textContent = s[1]; el.title = String(s[1]); }}
    else {{ el.textContent = wide ? s[1] : ''; el.title = wide ? String(s[1]) : ''; }}
  }});
  var legs = {{
    'bd-leg-shipped': ['Shipped', shipped], 'bd-leg-superseded': ['Superseded', superseded],
    'bd-leg-lost': ['Lost', lost], 'bd-leg-open': ['Open', open]
  }};
  for (var lid in legs) {{
    var lel = document.getElementById(lid);
    if (!lel) continue;
    var dot = lel.querySelector('.legend-dot');
    lel.textContent = '';
    lel.appendChild(dot);
    lel.appendChild(document.createTextNode(' ' + legs[lid][0] + ' (' + legs[lid][1] + ')'));
  }}
}}

var range = 0, dChart, cChart, animId = 0, transId = 0;
function isToday(ctx) {{ return ctx.chart.data.labels[ctx.dataIndex] === TL_TODAY_LABEL; }}
function segToday(ctx) {{ return ctx.chart.data.labels[ctx.p1DataIndex] === TL_TODAY_LABEL ? 'transparent' : undefined; }}
function build(r) {{
  var sl = sliceData(r);
  var labs = sl.map(function(d){{ return fmtLabel(d.date); }});
  if (dChart) dChart.destroy();
  if (cChart) cChart.destroy();

  var hasToday = sl.length > 0 && sl[sl.length - 1].date === TL_TODAY;
  var slT = hasToday ? sl.slice(0, -1) : sl;
  var tOpened = trendline(slT.map(function(d){{return d.prsOpened}}));
  var tShipped = trendline(slT.map(function(d){{return d.prsShipped}}));
  if (hasToday) {{ tOpened.push(null); tShipped.push(null); }}

  dChart = new Chart(document.getElementById('tlDailyChart'), {{
    type: 'bar',
    data: {{
      labels: labs,
      datasets: [
        {{ label: 'Lines of code', data: sl.map(function(d){{return d.loc}}),
           backgroundColor: function(ctx) {{ return isToday(ctx) ? C.green+'20' : C.green+'40'; }},
           borderColor: function(ctx) {{ return isToday(ctx) ? C.green+'30' : C.green+'60'; }},
           borderWidth: 1, borderRadius: 3, yAxisID: 'yL', order: 4 }},
        {{ label: 'PRs opened', data: sl.map(function(d){{return d.prsOpened}}), type: 'line',
           borderColor: C.blue, borderWidth: 2.5, tension: 0.25, yAxisID: 'yP', order: 1,
           pointRadius: 4, pointHoverRadius: 6,
           pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.blue+'80' : C.blue; }},
           segment: {{ borderColor: segToday }} }},
        {{ label: ' ', data: tOpened, type: 'line',
           borderColor: C.blue, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 0, spanGaps: true }},
        {{ label: 'PRs shipped', data: sl.map(function(d){{return d.prsShipped}}), type: 'line',
           borderColor: C.purple, borderWidth: 2.5, borderDash: [5,3], tension: 0.25, yAxisID: 'yP', order: 3,
           pointRadius: 4, pointHoverRadius: 6,
           pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.purple+'80' : C.purple; }},
           segment: {{ borderColor: segToday }} }},
        {{ label: '  ', data: tShipped, type: 'line',
           borderColor: C.purple, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 2, spanGaps: true }},
      ],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, animation: {{ duration: 0 }}, interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ grid: {{display:false}}, ticks: {{maxRotation:50, font:{{size:11}}}} }},
        yL: {{ position:'left', title:{{display:true,text:'LOC'}}, ticks:{{stepSize:2500,callback:fmtK}}, grid:{{color:C.grid}}, beginAtZero:true }},
        yP: {{ position:'right', title:{{display:true,text:'PRs'}}, grid:{{drawOnChartArea:false}}, beginAtZero:true }},
      }},
      plugins: {{
        tooltip: {{ filter: function(ctx) {{ return ctx.dataset.label.trim().length > 0; }}, bodySpacing: 7, titleMarginBottom: 8, callbacks: {{
          title: function(ctx) {{
            var d = sl[ctx[0].dataIndex], p = d.date.split('-');
            var dt = new Date(p[0],p[1]-1,p[2]);
            return ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][dt.getDay()] + ', ' + labs[ctx[0].dataIndex] + ' ' + p[0];
          }},
          afterBody: function(ctx) {{
            var d = sl[ctx[0].dataIndex];
            return [' ', '+'+fmtK(d.additions)+'/-'+fmtK(d.deletions)+' ('+d.files+' files)',
              d.prsOpened > 0 ? 'LOC/PR: '+fmtK(d.locPerPr)+'  Files/PR: '+d.filesPerPr : ''].filter(Boolean);
          }}
        }} }},
        legend: {{ position: 'top', labels: {{ padding: 28, boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 2, filter: function(item) {{ return item.text.trim().length > 0; }} }} }},
      }},
    }},
  }});

  cChart = new Chart(document.getElementById('tlCumulativeChart'), {{
    type: 'line',
    data: {{
      labels: labs,
      datasets: [
        {{ label: 'Cum. LOC', data: sl.map(function(d){{return d.cumLoc}}),
           borderColor: C.green, borderWidth: 2, backgroundColor: C.green+'20', fill: true, tension: 0.3, yAxisID: 'yL',
           pointRadius: 3, pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.green+'80' : C.green; }},
           segment: {{ borderColor: segToday }} }},
        {{ label: 'Cum. PRs opened', data: sl.map(function(d){{return d.cumOpened}}),
           borderColor: C.blue, borderWidth: 2, tension: 0.3, yAxisID: 'yP',
           pointRadius: 3, pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.blue+'80' : C.blue; }},
           segment: {{ borderColor: segToday }} }},
        {{ label: 'Cum. PRs shipped', data: sl.map(function(d){{return d.cumShipped}}),
           borderColor: C.purple, borderWidth: 2, borderDash: [5,3], tension: 0.3, yAxisID: 'yP',
           pointRadius: 3, pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.purple+'80' : C.purple; }},
           segment: {{ borderColor: segToday }} }},
      ],
    }},
    options: {{
      responsive: true, maintainAspectRatio: false, animation: {{ duration: 0 }}, interaction: {{ mode: 'index', intersect: false }},
      scales: {{
        x: {{ grid: {{display:false}}, ticks: {{maxRotation:50, font:{{size:11}}}} }},
        yL: {{ position:'left', title:{{display:true,text:'LOC'}}, ticks:{{stepSize:2500,callback:fmtK}}, grid:{{color:C.grid}} }},
        yP: {{ position:'right', title:{{display:true,text:'PRs'}}, grid:{{drawOnChartArea:false}} }},
      }},
      plugins: {{ legend: {{ position: 'top', labels: {{ padding: 28, boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 2 }} }} }},
    }},
  }});
}}
function bdStats(r) {{
  var sl = sliceData(r);
  var t = 0, s = 0, o = 0, sp = 0, l = 0, loc = 0, ad = 0, todayActive = false;
  for (var i = 0; i < sl.length; i++) {{
    var d = sl[i]; t += d.prsOpened; s += (d.clsShipped||0); o += (d.clsOpen||0);
    sp += (d.clsSuperseded||0); l += (d.clsLost||0); loc += d.loc;
    if (d.prsOpened > 0) {{ ad++; if (d.date === TL_TODAY) todayActive = true; }}
  }}
  if (todayActive && ad > 1) ad--;
  return {{total:t, shipped:s, open:o, sup:sp, lost:l, loc:loc, activeDays:ad}};
}}
function bdDisplay(b) {{
  var ls = b.lost + b.sup, cd = b.shipped + b.lost + b.sup;
  var rate = cd > 0 ? Math.round(b.shipped / cd * 100) : 0;
  var ad = Math.max(1, b.activeDays);
  var avgPrs = b.total / ad, avgLoc = b.loc / ad;
  var bT = b.total || 1;
  return {{total:b.total, shipped:b.shipped, open:b.open, sup:b.sup, lost:b.lost, lostSup:ls,
    rate:rate, activeDays:ad, avgPrs:avgPrs, avgLoc:avgLoc,
    barSh:b.shipped/bT*100, barSp:b.sup/bT*100, barL:b.lost/bT*100, barO:b.open/bT*100}};
}}
function renderBdFrame(oD, nD, et) {{
  var cT = Math.round(oD.total+et*(nD.total-oD.total));
  var cSh = Math.round(oD.shipped+et*(nD.shipped-oD.shipped));
  var cO = Math.round(oD.open+et*(nD.open-oD.open));
  var cSp = Math.round(oD.sup+et*(nD.sup-oD.sup));
  var cL = Math.round(oD.lost+et*(nD.lost-oD.lost));
  var cR = Math.round(oD.rate+et*(nD.rate-oD.rate));
  var cAd = Math.max(1, Math.round(oD.activeDays+et*(nD.activeDays-oD.activeDays)));
  var cAp = oD.avgPrs+et*(nD.avgPrs-oD.avgPrs);
  var cAl = oD.avgLoc+et*(nD.avgLoc-oD.avgLoc);
  var rL = Math.round(cAl);
  var el;
  if (el = document.getElementById('bd-total')) el.textContent = cT;
  if (el = document.getElementById('bd-shipped')) el.textContent = cSh;
  if (el = document.getElementById('bd-open')) el.textContent = cO;
  if (el = document.getElementById('bd-lost-sup')) el.textContent = cL + cSp;
  if (el = document.getElementById('bd-rate')) el.textContent = cR + '%';
  if (el = document.getElementById('bd-avg-prs')) el.textContent = cAp.toFixed(1);
  if (el = document.getElementById('bd-avg-loc')) el.textContent = rL >= 1000 ? (rL/1000).toFixed(1)+'k' : rL;
  if (el = document.getElementById('bd-days')) el.textContent = cAd === 1 ? '1 day' : cAd + ' days';
  [['bd-bar-shipped',cSh,oD.barSh,nD.barSh],['bd-bar-superseded',cSp,oD.barSp,nD.barSp],
   ['bd-bar-lost',cL,oD.barL,nD.barL],['bd-bar-open',cO,oD.barO,nD.barO]].forEach(function(s) {{
    var el = document.getElementById(s[0]); if (!el) return;
    var pct = (s[2]+et*(s[3]-s[2])).toFixed(1); el.style.width = pct+'%';
    var wide = parseFloat(pct) > 4;
    if (el.classList.contains('bar-open')) {{ el.textContent = s[1]; el.title = String(s[1]); }}
    else {{ el.textContent = wide ? s[1] : ''; el.title = wide ? String(s[1]) : ''; }}
  }});
  var legs = {{'bd-leg-shipped':['Shipped',cSh],'bd-leg-superseded':['Superseded',cSp],
    'bd-leg-lost':['Lost',cL],'bd-leg-open':['Open',cO]}};
  for (var lid in legs) {{
    var lel = document.getElementById(lid); if (!lel) continue;
    var dot = lel.querySelector('.legend-dot'); lel.textContent = '';
    lel.appendChild(dot);
    lel.appendChild(document.createTextNode(' '+legs[lid][0]+' ('+legs[lid][1]+')'));
  }}
}}
function transitionRange(newR) {{
  if (transId) {{ cancelAnimationFrame(transId); transId = 0; }}
  var oldSl = sliceData(range);
  var newSl = sliceData(newR);
  var oldN = oldSl.length, newN = newSl.length;
  var oB = bdStats(range), nB = bdStats(newR);
  range = newR;
  if (oldN === newN) {{ build(newR); updateBreakdown(newR); return; }}
  var sup = oldN > newN ? oldSl : newSl;
  var supLen = sup.length;
  var supLabs = sup.map(function(d) {{ return fmtLabel(d.date); }});
  var supDates = sup.map(function(d) {{ return d.date; }});
  var dS = [sup.map(function(d){{return d.loc}}), sup.map(function(d){{return d.prsOpened}}),
    null, sup.map(function(d){{return d.prsShipped}}), null];
  var cS = [sup.map(function(d){{return d.cumLoc}}), sup.map(function(d){{return d.cumOpened}}),
    sup.map(function(d){{return d.cumShipped}})];
  var oD = bdDisplay(oB), nD = bdDisplay(nB);
  var tDur = 500, tStart = null;
  function ease(t) {{ return 1 - Math.pow(1 - t, 2); }}
  transId = requestAnimationFrame(function tick(now) {{
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : ease(Math.min(elapsed / tDur, 1));
    var n = done ? newN : Math.max(1, Math.round(oldN + et * (newN - oldN)));
    var si = supLen - n;
    var labs = supLabs.slice(si, si + n);
    var d0 = dS[0].slice(si, si+n), d1 = dS[1].slice(si, si+n), d3 = dS[3].slice(si, si+n);
    var lastIsToday = supDates[si + n - 1] === TL_TODAY;
    var d1T = lastIsToday ? d1.slice(0, -1) : d1, d3T = lastIsToday ? d3.slice(0, -1) : d3;
    var tO = trendline(d1T), tS = trendline(d3T);
    if (lastIsToday) {{ tO.push(null); tS.push(null); }}
    dChart.data.labels = labs;
    dChart.data.datasets[0].data = d0;
    dChart.data.datasets[1].data = d1;
    dChart.data.datasets[2].data = tO;
    dChart.data.datasets[3].data = d3;
    dChart.data.datasets[4].data = tS;
    dChart.update('none');
    cChart.data.labels = labs;
    cChart.data.datasets[0].data = cS[0].slice(si, si+n);
    cChart.data.datasets[1].data = cS[1].slice(si, si+n);
    cChart.data.datasets[2].data = cS[2].slice(si, si+n);
    cChart.update('none');
    renderBdFrame(oD, nD, et);
    if (!done) {{ transId = requestAnimationFrame(tick); }}
    else {{ transId = 0; updateBreakdown(newR); }}
  }});
}}
build(range);

(function animateOnLoad() {{
  var all = TL_ALL;
  var total = 0, shipped = 0, opn = 0, sup = 0, lost = 0, totalLoc = 0, activeDays = 0;
  var todayActive = false;
  for (var i = 0; i < all.length; i++) {{
    var d = all[i];
    total += d.prsOpened; shipped += (d.clsShipped || 0); opn += (d.clsOpen || 0);
    sup += (d.clsSuperseded || 0); lost += (d.clsLost || 0); totalLoc += d.loc;
    if (d.prsOpened > 0) {{ activeDays++; if (d.date === TL_TODAY) todayActive = true; }}
  }}
  if (todayActive && activeDays > 1) activeDays--;
  var phases = [];
  var prev = 0;
  [7, 14, 30].forEach(function(days) {{
    var s = sliceData(days), t = 0;
    for (var j = 0; j < s.length; j++) t += s[j].prsOpened;
    phases.push({{pill: String(days), from: prev, to: t}});
    prev = t;
  }});
  phases.push({{pill: '0', from: prev, to: total}});
  function dispAt(f) {{
    var t = Math.round(f * total), sh = Math.round(f * shipped);
    var o = Math.round(f * opn), sp = Math.round(f * sup), l = Math.round(f * lost);
    var cd = sh + l + sp, rate = cd > 0 ? Math.round(sh / cd * 100) : 0;
    var ad = Math.max(1, Math.round(f * activeDays)), loc = Math.round(f * totalLoc);
    var bT = t || 1;
    return {{total:t, shipped:sh, open:o, sup:sp, lost:l, lostSup:l+sp,
      rate:rate, activeDays:ad, avgPrs:t/ad, avgLoc:loc/ad,
      barSh:sh/bT*100, barSp:sp/bT*100, barL:l/bT*100, barO:o/bT*100}};
  }}
  phases.forEach(function(ph) {{
    ph.startD = dispAt(ph.from / total);
    ph.endD = dispAt(ph.to / total);
  }});
  var p0s = phases[0].startD, p0e = phases[0].endD;
  p0s.barSh = p0e.barSh; p0s.barSp = p0e.barSp; p0s.barL = p0e.barL; p0s.barO = p0e.barO;
  var dur = 2000, phaseDur = dur / phases.length, start = null;
  var pills = document.querySelectorAll('#bd-range-pills .sort-pill');
  var dLabsFull = dChart.data.labels.slice();
  var cLabsFull = cChart.data.labels.slice();
  var dFull = dChart.data.datasets.map(function(ds) {{ return ds.data.slice(); }});
  var cFull = cChart.data.datasets.map(function(ds) {{ return ds.data.slice(); }});
  var totalPts = dLabsFull.length;
  animId = requestAnimationFrame(function tick(now) {{
    if (!start) start = now;
    var elapsed = now - start, done = elapsed >= dur;
    var prog = done ? 1 : Math.min(elapsed / dur, 1);
    var n = done ? totalPts : Math.max(1, Math.ceil(prog * totalPts));
    dChart.data.labels = dLabsFull.slice(0, n);
    dChart.data.datasets.forEach(function(ds, i) {{ ds.data = dFull[i].slice(0, n); }});
    cChart.data.labels = cLabsFull.slice(0, n);
    cChart.data.datasets.forEach(function(ds, i) {{ ds.data = cFull[i].slice(0, n); }});
    dChart.update('none');
    cChart.update('none');
    var pi = done ? phases.length - 1 : Math.min(Math.floor(elapsed / phaseDur), phases.length - 1);
    var phase = phases[pi];
    var et = done ? 1 : Math.min((elapsed - pi * phaseDur) / phaseDur, 1);
    var sD = phase.startD, eD = phase.endD;
    var cT = Math.round(sD.total+et*(eD.total-sD.total));
    if (cT < 1) {{ animId = requestAnimationFrame(tick); return; }}
    var activePill = phase.pill;
    pills.forEach(function(p) {{ p.classList.toggle('active', p.getAttribute('data-range') === activePill); }});
    renderBdFrame(sD, eD, et);
    if (!done) {{ animId = requestAnimationFrame(tick); }}
    else {{
      animId = 0;
      dChart.data.labels = dLabsFull;
      dChart.data.datasets.forEach(function(ds, i) {{ ds.data = dFull[i]; }});
      cChart.data.labels = cLabsFull;
      cChart.data.datasets.forEach(function(ds, i) {{ ds.data = cFull[i]; }});
      dChart.update('none');
      cChart.update('none');
      updateBreakdown(0);
      pills.forEach(function(p) {{ p.classList.toggle('active', p.getAttribute('data-range') === '0'); }});
    }}
  }});
}})();

document.getElementById('bd-range-pills').addEventListener('click', function(e) {{
  var p = e.target.closest('.sort-pill'); if (!p) return;
  var newR = parseInt(p.getAttribute('data-range'), 10);
  if (transId) {{ cancelAnimationFrame(transId); transId = 0; }}
  document.querySelectorAll('#bd-range-pills .sort-pill').forEach(function(x){{ x.classList.remove('active') }});
  p.classList.add('active');
  if (animId) {{ cancelAnimationFrame(animId); animId = 0; range = newR; build(newR); updateBreakdown(newR); }}
  else {{ transitionRange(newR); }}
}});
document.getElementById('tl-view-pills').addEventListener('click', function(e) {{
  var p = e.target.closest('.sort-pill'); if (!p) return;
  var v = p.getAttribute('data-view');
  document.querySelectorAll('#tl-view-pills .sort-pill').forEach(function(x){{ x.classList.remove('active') }});
  p.classList.add('active');
  document.getElementById('tl-daily-wrap').style.display = v === 'daily' ? '' : 'none';
  document.getElementById('tl-cumulative-wrap').style.display = v === 'cumulative' ? '' : 'none';
}});
pillsEl.addEventListener('click', function(e) {{
  var p = e.target.closest('.sort-pill'); if (!p) return;
  if (animId) {{ cancelAnimationFrame(animId); animId = 0; }}
  if (transId) {{ cancelAnimationFrame(transId); transId = 0; }}
  var oD = bdDisplay(bdStats(range));
  var oldSl = sliceData(range);
  activeRepo = p.getAttribute('data-repo') || null;
  pillsEl.querySelectorAll('.sort-pill').forEach(function(x){{ x.classList.remove('active') }});
  p.classList.add('active');
  var newSl = sliceData(range);
  var nD = bdDisplay(bdStats(range));
  var dateSet = {{}};
  oldSl.forEach(function(d) {{ dateSet[d.date] = true; }});
  newSl.forEach(function(d) {{ dateSet[d.date] = true; }});
  var uDates = Object.keys(dateSet).sort();
  var uLabs = uDates.map(fmtLabel);
  var oldBy = {{}}, newBy = {{}};
  oldSl.forEach(function(d) {{ oldBy[d.date] = d; }});
  newSl.forEach(function(d) {{ newBy[d.date] = d; }});
  var oldOnly = [], newOnly = [], sharedIdx = [];
  var oV = {{loc:[],po:[],ps:[],cl:[],co:[],cs:[]}};
  var nV = {{loc:[],po:[],ps:[],cl:[],co:[],cs:[]}};
  var ocl=0,oco=0,ocs=0,ncl=0,nco=0,ncs=0;
  uDates.forEach(function(dt, i) {{
    var o = oldBy[dt], n = newBy[dt];
    if (o && n) sharedIdx.push(i);
    else if (o) oldOnly.push(i);
    else newOnly.push(i);
    oV.loc.push(o?o.loc:0); oV.po.push(o?o.prsOpened:0); oV.ps.push(o?o.prsShipped:0);
    nV.loc.push(n?n.loc:0); nV.po.push(n?n.prsOpened:0); nV.ps.push(n?n.prsShipped:0);
    if(o){{ocl=o.cumLoc;oco=o.cumOpened;ocs=o.cumShipped;}}
    if(n){{ncl=n.cumLoc;nco=n.cumOpened;ncs=n.cumShipped;}}
    oV.cl.push(ocl);oV.co.push(oco);oV.cs.push(ocs);
    nV.cl.push(ncl);nV.co.push(nco);nV.cs.push(ncs);
  }});
  var cat = {{}};
  oldOnly.forEach(function(i){{ cat[i]='o'; }});
  newOnly.forEach(function(i){{ cat[i]='n'; }});
  sharedIdx.forEach(function(i){{ cat[i]='s'; }});
  var startDL = dChart.scales.yL.max, startDP = dChart.scales.yP.max;
  var startCL = cChart.scales.yL.max, startCP = cChart.scales.yP.max;
  build(range);
  var endDL = dChart.scales.yL.max, endDP = dChart.scales.yP.max;
  var endCL = cChart.scales.yL.max, endCP = cChart.scales.yP.max;
  var initVis = oldOnly.concat(sharedIdx).sort(function(a,b){{return a-b;}});
  var initLabs = initVis.map(function(i){{return uLabs[i];}});
  var initL=[],initPO=[],initPS=[],initCL2=[],initCO2=[],initCS2=[];
  initVis.forEach(function(idx) {{
    initL.push(oV.loc[idx]); initPO.push(oV.po[idx]); initPS.push(oV.ps[idx]);
    initCL2.push(oV.cl[idx]); initCO2.push(oV.co[idx]); initCS2.push(oV.cs[idx]);
  }});
  var initNull = initLabs.map(function(){{return null;}});
  dChart.data.labels = initLabs;
  dChart.data.datasets[0].data = initL; dChart.data.datasets[1].data = initPO;
  dChart.data.datasets[2].data = initNull; dChart.data.datasets[3].data = initPS;
  dChart.data.datasets[4].data = initNull;
  dChart.options.scales.yL.max = startDL; dChart.options.scales.yP.max = startDP;
  dChart.update('none');
  cChart.data.labels = initLabs;
  cChart.data.datasets[0].data = initCL2; cChart.data.datasets[1].data = initCO2;
  cChart.data.datasets[2].data = initCS2;
  cChart.options.scales.yL.max = startCL; cChart.options.scales.yP.max = startCP;
  cChart.update('none');
  var tDur = 500, tStart = null;
  transId = requestAnimationFrame(function tick(now) {{
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : Math.min(elapsed / tDur, 1);
    var nPairs = Math.min(oldOnly.length, newOnly.length);
    var diff = newOnly.length - oldOnly.length;
    var paired = Math.round(et * nPairs);
    var oRem = oldOnly.length - paired;
    var nVis = paired;
    if (diff > 0) nVis += Math.round(et * diff);
    else if (diff < 0) oRem -= Math.round(et * (-diff));
    var vis = [];
    for (var i = 0; i < oRem; i++) vis.push(oldOnly[i]);
    sharedIdx.forEach(function(i){{ vis.push(i); }});
    for (var i = 0; i < nVis; i++) vis.push(newOnly[i]);
    vis.sort(function(a,b){{ return a-b; }});
    var fL=[],fLoc=[],fPO=[],fPS=[],fCL=[],fCO=[],fCS=[];
    vis.forEach(function(idx) {{
      fL.push(uLabs[idx]);
      var c = cat[idx];
      if (c==='s') {{
        fLoc.push(oV.loc[idx]+et*(nV.loc[idx]-oV.loc[idx]));
        fPO.push(oV.po[idx]+et*(nV.po[idx]-oV.po[idx]));
        fPS.push(oV.ps[idx]+et*(nV.ps[idx]-oV.ps[idx]));
        fCL.push(oV.cl[idx]+et*(nV.cl[idx]-oV.cl[idx]));
        fCO.push(oV.co[idx]+et*(nV.co[idx]-oV.co[idx]));
        fCS.push(oV.cs[idx]+et*(nV.cs[idx]-oV.cs[idx]));
      }} else if (c==='o') {{
        fLoc.push(oV.loc[idx]*(1-et)); fPO.push(oV.po[idx]*(1-et)); fPS.push(oV.ps[idx]*(1-et));
        fCL.push(oV.cl[idx]*(1-et)); fCO.push(oV.co[idx]*(1-et)); fCS.push(oV.cs[idx]*(1-et));
      }} else {{
        fLoc.push(nV.loc[idx]*et); fPO.push(nV.po[idx]*et); fPS.push(nV.ps[idx]*et);
        fCL.push(nV.cl[idx]*et); fCO.push(nV.co[idx]*et); fCS.push(nV.cs[idx]*et);
      }}
    }});
    dChart.options.scales.yL.max = startDL+et*(endDL-startDL);
    dChart.options.scales.yP.max = startDP+et*(endDP-startDP);
    cChart.options.scales.yL.max = startCL+et*(endCL-startCL);
    cChart.options.scales.yP.max = startCP+et*(endCP-startCP);
    var nullT = fL.map(function(){{return null;}});
    dChart.data.labels = fL;
    dChart.data.datasets[0].data = fLoc;
    dChart.data.datasets[1].data = fPO;
    dChart.data.datasets[2].data = nullT;
    dChart.data.datasets[3].data = fPS;
    dChart.data.datasets[4].data = nullT;
    dChart.update('none');
    cChart.data.labels = fL;
    cChart.data.datasets[0].data = fCL;
    cChart.data.datasets[1].data = fCO;
    cChart.data.datasets[2].data = fCS;
    cChart.update('none');
    renderBdFrame(oD, nD, et);
    if (!done) {{ transId = requestAnimationFrame(tick); }}
    else {{
      delete dChart.options.scales.yL.max; delete dChart.options.scales.yP.max;
      delete cChart.options.scales.yL.max; delete cChart.options.scales.yP.max;
      transId = 0; build(range); updateBreakdown(range);
    }}
  }});
}});
}})();
</script>
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
