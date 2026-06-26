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

    today = datetime.now(EASTERN).strftime("%Y-%m-%d")
    if all_dates and all_dates[-1] == today:
        all_dates = all_dates[:-1]

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

    # 3. Wrap Breakdown heading with range pills
    if 'id="bd-range-pills"' not in html:
        bd_header = (
            '<div class="landscape-row" style="position:static">\n'
            '<h2>Breakdown</h2>\n'
            '<div class="sort-pills" id="bd-range-pills">\n'
            '  <div class="sort-pill" data-range="7">7d</div>\n'
            '  <div class="sort-pill" data-range="14">14d</div>\n'
            '  <div class="sort-pill" data-range="30">30d</div>\n'
            '  <div class="sort-pill active" data-range="0">All</div>\n'
            '</div>\n'
            '</div>'
        )
        html = html.replace('<h2>Breakdown</h2>', bd_header)

    # 3. Insert chart section before <h2>Methodology</h2>
    chart_section = f"""{CHART_MARKER}
<div class="landscape-row" style="margin-top:2rem;position:static">
  <div class="pr-filter-group pr-filter-group-left">
    <h2>Progress</h2>
    <div class="sort-pills" id="tl-view-pills">
    <div class="sort-pill active" data-view="daily">Daily</div>
    <div class="sort-pill" data-view="cumulative">Cumulative</div>
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

var bdStatic = {{}};
(function() {{
  ['bd-total','bd-shipped','bd-open','bd-lost-sup','bd-rate','bd-rate-label',
   'bd-days','bd-days-label','bd-avg-prs','bd-avg-loc'].forEach(function(id) {{
    var el = document.getElementById(id);
    if (el) bdStatic[id] = el.textContent;
  }});
  ['bd-bar-shipped','bd-bar-superseded','bd-bar-lost','bd-bar-open'].forEach(function(id) {{
    var el = document.getElementById(id);
    if (el) bdStatic[id] = {{w: el.getAttribute('data-width'), t: el.textContent, title: el.title || ''}};
  }});
  ['bd-leg-shipped','bd-leg-superseded','bd-leg-lost','bd-leg-open'].forEach(function(id) {{
    var el = document.getElementById(id);
    if (el) bdStatic[id] = el.innerHTML;
  }});
}})();

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
  if (!r && !activeRepo) {{
    for (var id in bdStatic) {{
      var el = document.getElementById(id);
      if (!el) continue;
      var v = bdStatic[id];
      if (typeof v === 'object') {{
        el.setAttribute('data-width', v.w); el.style.width = v.w + '%'; el.textContent = v.t; el.title = v.title;
      }} else if (id.indexOf('bd-leg-') === 0) {{
        el.innerHTML = v;
      }} else {{
        el.textContent = v;
      }}
    }}
    return;
  }}
  var sl = sliceData(r);
  var total = 0, shipped = 0, open = 0, superseded = 0, lost = 0;
  var totalLoc = 0, activeDays = 0;
  var firstDate = null, lastDate = null;
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
      lastDate = d.date;
    }}
  }}
  var lostSup = lost + superseded;
  var closedDenom = shipped + lost + superseded;
  var rate = closedDenom > 0 ? Math.round(shipped / closedDenom * 100) : 'N/A';
  var el;
  if (el = document.getElementById('bd-total')) el.textContent = total;
  if (el = document.getElementById('bd-shipped')) el.textContent = shipped;
  if (el = document.getElementById('bd-open')) el.textContent = open;
  if (el = document.getElementById('bd-lost-sup')) el.textContent = lostSup;
  if (el = document.getElementById('bd-rate')) el.textContent = rate + '%';
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

var range = 0, dChart, cChart;
function build(r) {{
  var sl = sliceData(r), labs = sl.map(function(d){{ return fmtLabel(d.date); }});
  if (dChart) dChart.destroy();
  if (cChart) cChart.destroy();

  var tOpened = trendline(sl.map(function(d){{return d.prsOpened}}));
  var tShipped = trendline(sl.map(function(d){{return d.prsShipped}}));

  dChart = new Chart(document.getElementById('tlDailyChart'), {{
    type: 'bar',
    data: {{
      labels: labs,
      datasets: [
        {{ label: 'Lines of code', data: sl.map(function(d){{return d.loc}}),
           backgroundColor: C.green+'40', borderColor: C.green+'60', borderWidth: 1, borderRadius: 3, yAxisID: 'yL', order: 4 }},
        {{ label: 'PRs opened', data: sl.map(function(d){{return d.prsOpened}}), type: 'line',
           borderColor: C.blue, borderWidth: 2.5, pointRadius: 4, pointHoverRadius: 6, tension: 0.25, yAxisID: 'yP', order: 1 }},
        {{ label: ' ', data: tOpened, type: 'line',
           borderColor: C.blue, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 0, spanGaps: true }},
        {{ label: 'PRs shipped', data: sl.map(function(d){{return d.prsShipped}}), type: 'line',
           borderColor: C.purple, borderWidth: 2.5, borderDash: [5,3], pointRadius: 4, pointHoverRadius: 6, tension: 0.25, yAxisID: 'yP', order: 3 }},
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
           borderColor: C.green, borderWidth: 2, backgroundColor: C.green+'20', fill: true, tension: 0.3, pointRadius: 3, yAxisID: 'yL' }},
        {{ label: 'Cum. PRs opened', data: sl.map(function(d){{return d.cumOpened}}),
           borderColor: C.blue, borderWidth: 2, tension: 0.3, pointRadius: 3, yAxisID: 'yP' }},
        {{ label: 'Cum. PRs shipped', data: sl.map(function(d){{return d.cumShipped}}),
           borderColor: C.purple, borderWidth: 2, borderDash: [5,3], tension: 0.3, pointRadius: 3, yAxisID: 'yP' }},
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
build(range);

document.getElementById('bd-range-pills').addEventListener('click', function(e) {{
  var p = e.target.closest('.sort-pill'); if (!p) return;
  range = parseInt(p.getAttribute('data-range'), 10);
  document.querySelectorAll('#bd-range-pills .sort-pill').forEach(function(x){{ x.classList.remove('active') }});
  p.classList.add('active');
  build(range);
  updateBreakdown(range);
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
  activeRepo = p.getAttribute('data-repo') || null;
  pillsEl.querySelectorAll('.sort-pill').forEach(function(x){{ x.classList.remove('active') }});
  p.classList.add('active');
  build(range);
  updateBreakdown(range);
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
