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

Chart.register({{
  id: 'smoothLayout',
  beforeDatasetsDraw: function(chart) {{
    var sc = chart._barScales;
    if (!sc || !sc._xTransform) return;
    var n = chart.data.labels.length;
    if (n < 1) return;
    var eN = sc._xTransform.effectiveN;
    if (eN < 0.01) return;
    var aL = chart.chartArea.left, aW = chart.chartArea.right - aL;
    var eCW = aW / eN;
    var xS = chart.scales.x;
    var bR = 0.72;
    if (n > 1) {{
      var uCW = xS.getPixelForValue(1) - xS.getPixelForValue(0);
      if (uCW > 0) for (var di = 0; di < chart.data.datasets.length; di++) {{
        var m = chart.getDatasetMeta(di);
        if (m.type === 'bar' && m.data.length > 0 && m.data[0].width > 0) {{ bR = m.data[0].width / uCW; break; }}
      }}
    }}
    var pos = 0, eC = [];
    for (var i = 0; i < n; i++) {{ var s = i < sc.length ? sc[i] : 1; eC.push(aL + (pos + 0.5 * s) * eCW); pos += s; }}
    for (var di = 0; di < chart.data.datasets.length; di++) {{
      var meta = chart.getDatasetMeta(di);
      for (var i = 0; i < meta.data.length && i < n; i++) {{
        var el = meta.data[i];
        var dx = eC[i] - el.x;
        el.x = eC[i];
        if (typeof el.width !== 'undefined') {{ var s = i < sc.length ? sc[i] : 1; el.width = Math.max(0, s * eCW * bR); }}
        if (typeof el.cp1x !== 'undefined') el.cp1x += dx;
        if (typeof el.cp2x !== 'undefined') el.cp2x += dx;
      }}
    }}
    chart.ctx.save();
    chart.ctx.beginPath();
    chart.ctx.rect(aL, 0, aW, chart.canvas.height);
    chart.ctx.clip();
  }},
  afterDatasetsDraw: function(chart) {{
    if (chart._barScales && chart._barScales._xTransform) chart.ctx.restore();
  }}
}});

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
function wtrendline(vals, wts) {{
  var n = vals.length;
  if (n < 2) return vals.map(function(){{return null;}});
  var sw=0,sx=0,sy=0,sxx=0,sxy=0;
  for (var i=0;i<n;i++){{var w=wts[i];sw+=w;sx+=w*i;sy+=w*vals[i];sxx+=w*i*i;sxy+=w*i*vals[i];}}
  if (sw<0.01) return vals.map(function(){{return null;}});
  var d=sw*sxx-sx*sx;
  if(Math.abs(d)<1e-10) return vals.map(function(){{return null;}});
  var m=(sw*sxy-sx*sy)/d, b=(sy-m*sx)/sw;
  var fi=-1,li=-1;
  for(var i=0;i<n;i++) if(wts[i]>0.01){{if(fi<0)fi=i;li=i;}}
  if(fi<0||fi===li) return vals.map(function(){{return null;}});
  var r=vals.map(function(){{return null;}});
  r[fi]=Math.max(0,m*fi+b); r[li]=Math.max(0,m*li+b);
  return r;
}}
function ease(t) {{ return t<0.5 ? 4*t*t*t : 1-Math.pow(-2*t+2,3)/2; }}
var textRgb = (function(c){{return parseInt(c.slice(1,3),16)+','+parseInt(c.slice(3,5),16)+','+parseInt(c.slice(5,7),16);}})(C.text);
function textAlpha(a) {{ return 'rgba('+textRgb+','+a+')'; }}
function lerpFading(data, sc, len, keep) {{
  var out = data.slice();
  for (var i = 0; i < len; i++) {{
    if (sc[i] > 0.99) continue;
    var pI = -1, nI = -1;
    for (var j = i - 1; j >= 0; j--) if (sc[j] > 0.99) {{ pI = j; break; }}
    for (var j = i + 1; j < len; j++) if (sc[j] > 0.99) {{ nI = j; break; }}
    if (pI >= 0 && nI >= 0) {{
      var f = (i - pI) / (nI - pI);
      out[i] = data[i] * sc[i] + (data[pI] + f * (data[nI] - data[pI])) * (1 - sc[i]);
      if (sc[i] < 0.01) out[i] = null;
    }} else {{
      var k = Array.isArray(keep) ? keep[i] : keep;
      if (!k) out[i] = null;
    }}
  }}
  return out;
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
           borderColor: C.blue, borderWidth: 2.5, tension: 0, yAxisID: 'yP', order: 1,
           pointRadius: 4, pointHoverRadius: 6,
           pointBackgroundColor: function(ctx) {{ return isToday(ctx) ? C.blue+'80' : C.blue; }},
           segment: {{ borderColor: segToday }} }},
        {{ label: ' ', data: tOpened, type: 'line',
           borderColor: C.blue, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 0, spanGaps: true }},
        {{ label: 'PRs shipped', data: sl.map(function(d){{return d.prsShipped}}), type: 'line',
           borderColor: C.purple, borderWidth: 2.5, borderDash: [5,3], tension: 0, yAxisID: 'yP', order: 3,
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
  var expanding = newN > oldN;
  var edgeCount = Math.abs(newN - oldN);
  var lastIsToday = supDates[supLen - 1] === TL_TODAY;
  var tO0 = trendline((lastIsToday ? dS[1].slice(0,-1) : dS[1]) || []);
  var tS0 = trendline((lastIsToday ? dS[3].slice(0,-1) : dS[3]) || []);
  if (lastIsToday) {{ tO0.push(null); tS0.push(null); }}
  var startDL = dChart.scales.yL.max, startDP = dChart.scales.yP.max;
  var startCL = cChart.scales.yL.max, startCP = cChart.scales.yP.max;
  var dYLw0 = dChart.scales.yL.width, dYPw0 = dChart.scales.yP.width, dXh0 = dChart.scales.x.height;
  var cYLw0 = cChart.scales.yL.width, cYPw0 = cChart.scales.yP.width, cXh0 = cChart.scales.x.height;
  var dXrot0 = dChart.scales.x.labelRotation, dYLrot0 = dChart.scales.yL.labelRotation, dYProt0 = dChart.scales.yP.labelRotation;
  build(newR);
  var endDL = dChart.scales.yL.max, endDP = dChart.scales.yP.max;
  var endCL = cChart.scales.yL.max, endCP = cChart.scales.yP.max;
  var dYLw1 = dChart.scales.yL.width, dYPw1 = dChart.scales.yP.width;
  var cYLw1 = cChart.scales.yL.width, cYPw1 = cChart.scales.yP.width;
  var dXh1 = dChart.scales.x.height, cXh1 = cChart.scales.x.height;
  var dXrot1 = dChart.scales.x.labelRotation, dYLrot1 = dChart.scales.yL.labelRotation, dYProt1 = dChart.scales.yP.labelRotation;
  var dXhCur = dXh0, cXhCur = cXh0;
  var dYLwCur = dYLw0, dYPwCur = dYPw0, cYLwCur = cYLw0, cYPwCur = cYPw0;
  dChart.options.scales.yL.afterFit = function(a){{a.width=dYLwCur;}}; dChart.options.scales.yP.afterFit = function(a){{a.width=dYPwCur;}};
  cChart.options.scales.yL.afterFit = function(a){{a.width=cYLwCur;}}; cChart.options.scales.yP.afterFit = function(a){{a.width=cYPwCur;}};
  dChart.options.scales.x.afterFit = function(a){{a.height=dXhCur;}}; cChart.options.scales.x.afterFit = function(a){{a.height=cXhCur;}};
  dChart.data.labels = supLabs;
  dChart.data.datasets[0].data = dS[0];
  dChart.data.datasets[1].data = dS[1];
  dChart.data.datasets[2].data = tO0;
  dChart.data.datasets[3].data = dS[3];
  dChart.data.datasets[4].data = tS0;
  cChart.data.labels = supLabs;
  cChart.data.datasets[0].data = cS[0];
  cChart.data.datasets[1].data = cS[1];
  cChart.data.datasets[2].data = cS[2];
  var initSc = [];
  for (var i = 0; i < supLen; i++) {{
    if (expanding) initSc.push(i < edgeCount ? 0 : 1);
    else initSc.push(1);
  }}
  initSc._xTransform = {{ effectiveN: oldN }};
  var xSlide = Math.abs(dXh0 - dXh1) < 1 && dXrot0 === dXrot1;
  var dYLslide = Math.abs(dYLw0 - dYLw1) < 1 && dYLrot0 === dYLrot1;
  var dYPslide = Math.abs(dYPw0 - dYPw1) < 1 && dYProt0 === dYProt1;
  dChart._barScales = initSc;
  if (xSlide) {{
    dChart.options.scales.x.ticks.color = function(ctx) {{ return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; }};
  }} else {{
    dChart.options.scales.x.ticks.color = textAlpha(0);
  }}
  if (!dYLslide) dChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!dYPslide) dChart.options.scales.yP.ticks.color = textAlpha(0);
  dChart.data.datasets[1].spanGaps = true; dChart.data.datasets[3].spanGaps = true;
  dChart.options.scales.yL.max = startDL; dChart.options.scales.yP.max = startDP;
  dChart.update('none');
  cChart._barScales = initSc;
  cChart.options.scales.yL.max = startCL; cChart.options.scales.yP.max = startCP;
  cChart.update('none');
  var tDur = 500, tStart = null, rotSnapped = false;
  transId = requestAnimationFrame(function tick(now) {{
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : ease(Math.min(elapsed / tDur, 1));
    var nExact = oldN + et * (newN - oldN);
    var sc = [];
    for (var i = 0; i < supLen; i++) {{
      if (expanding) {{
        var fromRight = supLen - 1 - i;
        if (fromRight < nExact - 1) sc.push(1);
        else if (fromRight < nExact) sc.push(nExact - fromRight);
        else sc.push(0);
      }} else {{
        if (i < supLen - nExact) sc.push(Math.max(0, 1 - (supLen - nExact - i)));
        else sc.push(1);
      }}
    }}
    sc._xTransform = {{ effectiveN: Math.max(0.5, nExact) }};
    var twts = [];
    for (var i=0;i<supLen;i++) twts.push(supDates[i]===TL_TODAY ? 0 : sc[i]);
    var tOa = wtrendline(dS[1], twts), tSa = wtrendline(dS[3], twts);
    var lineO = lerpFading(dS[1], sc, supLen, expanding), lineS = lerpFading(dS[3], sc, supLen, expanding);
    dChart.data.datasets[1].data = lineO;
    dChart.data.datasets[2].data = tOa;
    dChart.data.datasets[3].data = lineS;
    dChart.data.datasets[4].data = tSa;
    dChart.options.scales.yL.max = startDL+et*(endDL-startDL);
    dChart.options.scales.yP.max = startDP+et*(endDP-startDP);
    cChart.options.scales.yL.max = startCL+et*(endCL-startCL);
    cChart.options.scales.yP.max = startCP+et*(endCP-startCP);
    var labelA = et<0.15 ? 1-et/0.15 : et>0.85 ? (et-0.85)/0.15 : 0;
    var labelC = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    if (!rotSnapped && labelA < 0.01) {{
      rotSnapped = true;
      if (!xSlide) {{ dChart.options.scales.x.ticks.minRotation = dXrot1; dChart.options.scales.x.ticks.maxRotation = dXrot1; }}
      if (!dYLslide) {{ dChart.options.scales.yL.ticks.minRotation = dYLrot1; dChart.options.scales.yL.ticks.maxRotation = dYLrot1; }}
      if (!dYPslide) {{ dChart.options.scales.yP.ticks.minRotation = dYProt1; dChart.options.scales.yP.ticks.maxRotation = dYProt1; }}
    }}
    if (xSlide) {{
      dChart.options.scales.x.ticks.color = function(ctx) {{
        var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
      }};
    }} else {{
      dChart.options.scales.x.ticks.color = labelC;
    }}
    if (!dYLslide) dChart.options.scales.yL.ticks.color = labelC;
    if (!dYPslide) dChart.options.scales.yP.ticks.color = labelC;
    dXhCur = dXh0+et*(dXh1-dXh0); cXhCur = cXh0+et*(cXh1-cXh0);
    dYLwCur = dYLw0+et*(dYLw1-dYLw0); dYPwCur = dYPw0+et*(dYPw1-dYPw0);
    cYLwCur = cYLw0+et*(cYLw1-cYLw0); cYPwCur = cYPw0+et*(cYPw1-cYPw0);
    dChart._barScales = sc; dChart.update('none');
    cChart._barScales = sc; cChart.update('none');
    renderBdFrame(oD, nD, et);
    if (!done) {{ transId = requestAnimationFrame(tick); }}
    else {{
      dChart._barScales = null; cChart._barScales = null;
      dChart.data.datasets[1].spanGaps = false; dChart.data.datasets[3].spanGaps = false;
      dChart.options.scales.x.ticks.color = C.text;
      dChart.options.scales.yL.ticks.color = C.text; dChart.options.scales.yP.ticks.color = C.text;
      delete dChart.options.scales.x.ticks.minRotation; dChart.options.scales.x.ticks.maxRotation = 50;
      delete dChart.options.scales.yL.ticks.minRotation; delete dChart.options.scales.yL.ticks.maxRotation;
      delete dChart.options.scales.yP.ticks.minRotation; delete dChart.options.scales.yP.ticks.maxRotation;
      delete dChart.options.scales.yL.max; delete dChart.options.scales.yP.max;
      delete cChart.options.scales.yL.max; delete cChart.options.scales.yP.max;
      delete dChart.options.scales.yL.afterFit; delete dChart.options.scales.yP.afterFit;
      delete cChart.options.scales.yL.afterFit; delete cChart.options.scales.yP.afterFit;
      delete dChart.options.scales.x.afterFit; delete cChart.options.scales.x.afterFit;
      transId = 0; build(newR); updateBreakdown(newR);
    }}
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
  var dur = 1000, phaseDur = dur / phases.length, start = null;
  var pills = document.querySelectorAll('#bd-range-pills .sort-pill');
  var dLabsFull = dChart.data.labels.slice();
  var cLabsFull = cChart.data.labels.slice();
  var dFull = dChart.data.datasets.map(function(ds) {{ return ds.data.slice(); }});
  var cFull = cChart.data.datasets.map(function(ds) {{ return ds.data.slice(); }});
  var totalPts = dLabsFull.length;
  dChart.options.scales.x.ticks.color = textAlpha(0);
  animId = requestAnimationFrame(function tick(now) {{
    if (!start) start = now;
    var elapsed = now - start, done = elapsed >= dur;
    var prog = done ? 1 : ease(Math.min(elapsed / dur, 1));
    var nExact = done ? totalPts : Math.max(0.5, prog * totalPts);
    var sc = [];
    for (var i = 0; i < totalPts; i++) {{
      if (i + 1 <= nExact) sc.push(1);
      else if (i < nExact) sc.push(nExact - i);
      else sc.push(0);
    }}
    sc._xTransform = {{ effectiveN: nExact }};
    var labelA = prog>0.7 ? (prog-0.7)/0.3 : 0;
    dChart.options.scales.x.ticks.color = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    dChart._barScales = sc; dChart.update('none');
    cChart._barScales = sc; cChart.update('none');
    var pi = done ? phases.length - 1 : Math.min(Math.floor(elapsed / phaseDur), phases.length - 1);
    var phase = phases[pi];
    var et = done ? 1 : ease(Math.min((elapsed - pi * phaseDur) / phaseDur, 1));
    var sD = phase.startD, eD = phase.endD;
    var cT = Math.round(sD.total+et*(eD.total-sD.total));
    if (cT < 1) {{ animId = requestAnimationFrame(tick); return; }}
    var activePill = phase.pill;
    pills.forEach(function(p) {{ p.classList.toggle('active', p.getAttribute('data-range') === activePill); }});
    renderBdFrame(sD, eD, et);
    if (!done) {{ animId = requestAnimationFrame(tick); }}
    else {{
      animId = 0;
      dChart._barScales = null; cChart._barScales = null;
      dChart.options.scales.x.ticks.color = C.text;
      dChart.update('none'); cChart.update('none');
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
  var uLen = uLabs.length;
  var oldBy = {{}}, newBy = {{}};
  oldSl.forEach(function(d) {{ oldBy[d.date] = d; }});
  newSl.forEach(function(d) {{ newBy[d.date] = d; }});
  var oldN = oldSl.length, newN = newSl.length;
  var cat = [];
  var oV = {{loc:[],po:[],ps:[],cl:[],co:[],cs:[]}};
  var nV = {{loc:[],po:[],ps:[],cl:[],co:[],cs:[]}};
  var ocl=0,oco=0,ocs=0,ncl=0,nco=0,ncs=0;
  uDates.forEach(function(dt, i) {{
    var o = oldBy[dt], n = newBy[dt];
    if (o && n) cat.push('s');
    else if (o) cat.push('o');
    else cat.push('n');
    oV.loc.push(o?o.loc:0); oV.po.push(o?o.prsOpened:0); oV.ps.push(o?o.prsShipped:0);
    nV.loc.push(n?n.loc:0); nV.po.push(n?n.prsOpened:0); nV.ps.push(n?n.prsShipped:0);
    if(o){{ocl=o.cumLoc;oco=o.cumOpened;ocs=o.cumShipped;}}
    if(n){{ncl=n.cumLoc;nco=n.cumOpened;ncs=n.cumShipped;}}
    oV.cl.push(ocl);oV.co.push(oco);oV.cs.push(ocs);
    nV.cl.push(ncl);nV.co.push(nco);nV.cs.push(ncs);
  }});
  var startDL = dChart.scales.yL.max, startDP = dChart.scales.yP.max;
  var startCL = cChart.scales.yL.max, startCP = cChart.scales.yP.max;
  var dYLw0 = dChart.scales.yL.width, dYPw0 = dChart.scales.yP.width, dXh0 = dChart.scales.x.height;
  var cYLw0 = cChart.scales.yL.width, cYPw0 = cChart.scales.yP.width, cXh0 = cChart.scales.x.height;
  var dXrot0 = dChart.scales.x.labelRotation, dYLrot0 = dChart.scales.yL.labelRotation, dYProt0 = dChart.scales.yP.labelRotation;
  build(range);
  var endDL = dChart.scales.yL.max, endDP = dChart.scales.yP.max;
  var endCL = cChart.scales.yL.max, endCP = cChart.scales.yP.max;
  var dYLw1 = dChart.scales.yL.width, dYPw1 = dChart.scales.yP.width;
  var cYLw1 = cChart.scales.yL.width, cYPw1 = cChart.scales.yP.width;
  var dXh1 = dChart.scales.x.height, cXh1 = cChart.scales.x.height;
  var dXrot1 = dChart.scales.x.labelRotation, dYLrot1 = dChart.scales.yL.labelRotation, dYProt1 = dChart.scales.yP.labelRotation;
  var dXhCur = dXh0, cXhCur = cXh0;
  var dYLwCur = dYLw0, dYPwCur = dYPw0, cYLwCur = cYLw0, cYPwCur = cYPw0;
  dChart.options.scales.yL.afterFit = function(a){{a.width=dYLwCur;}}; dChart.options.scales.yP.afterFit = function(a){{a.width=dYPwCur;}};
  cChart.options.scales.yL.afterFit = function(a){{a.width=cYLwCur;}}; cChart.options.scales.yP.afterFit = function(a){{a.width=cYPwCur;}};
  dChart.options.scales.x.afterFit = function(a){{a.height=dXhCur;}}; cChart.options.scales.x.afterFit = function(a){{a.height=cXhCur;}};
  var nullArr = uLabs.map(function(){{return null;}});
  dChart.data.labels = uLabs;
  dChart.data.datasets[0].data = oV.loc.slice();
  dChart.data.datasets[1].data = oV.po.slice();
  dChart.data.datasets[2].data = nullArr;
  dChart.data.datasets[3].data = oV.ps.slice();
  dChart.data.datasets[4].data = nullArr;
  dChart.options.scales.yL.max = startDL; dChart.options.scales.yP.max = startDP;
  var initSc = [];
  for (var i = 0; i < uLen; i++) initSc.push(cat[i] === 'n' ? 0 : 1);
  initSc._xTransform = {{ effectiveN: oldN }};
  var xSlide = Math.abs(dXh0 - dXh1) < 1 && dXrot0 === dXrot1;
  var dYLslide = Math.abs(dYLw0 - dYLw1) < 1 && dYLrot0 === dYLrot1;
  var dYPslide = Math.abs(dYPw0 - dYPw1) < 1 && dYProt0 === dYProt1;
  var keepArr = cat.map(function(c) {{ return c !== 'o'; }});
  dChart._barScales = initSc;
  if (xSlide) {{
    dChart.options.scales.x.ticks.color = function(ctx) {{ return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; }};
  }} else {{
    dChart.options.scales.x.ticks.color = textAlpha(0);
  }}
  if (!dYLslide) dChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!dYPslide) dChart.options.scales.yP.ticks.color = textAlpha(0);
  dChart.data.datasets[1].spanGaps = true; dChart.data.datasets[3].spanGaps = true;
  dChart.update('none');
  cChart.data.labels = uLabs;
  cChart.data.datasets[0].data = oV.cl.slice();
  cChart.data.datasets[1].data = oV.co.slice();
  cChart.data.datasets[2].data = oV.cs.slice();
  cChart.options.scales.yL.max = startCL; cChart.options.scales.yP.max = startCP;
  cChart._barScales = initSc;
  cChart.update('none');
  var tDur = 500, tStart = null, rotSnapped = false;
  transId = requestAnimationFrame(function tick(now) {{
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : ease(Math.min(elapsed / tDur, 1));
    var nExact = oldN + et * (newN - oldN);
    var sc = [];
    for (var i = 0; i < uLen; i++) {{
      if (cat[i] === 's') sc.push(1);
      else if (cat[i] === 'o') sc.push(1 - et);
      else sc.push(et);
    }}
    sc._xTransform = {{ effectiveN: Math.max(0.5, nExact) }};
    var fLoc=[], fPO=[], fPS=[], fCL=[], fCO=[], fCS=[];
    for (var i = 0; i < uLen; i++) {{
      if (cat[i] === 's') {{
        fLoc.push(oV.loc[i]+et*(nV.loc[i]-oV.loc[i]));
        fPO.push(oV.po[i]+et*(nV.po[i]-oV.po[i]));
        fPS.push(oV.ps[i]+et*(nV.ps[i]-oV.ps[i]));
        fCL.push(oV.cl[i]+et*(nV.cl[i]-oV.cl[i]));
        fCO.push(oV.co[i]+et*(nV.co[i]-oV.co[i]));
        fCS.push(oV.cs[i]+et*(nV.cs[i]-oV.cs[i]));
      }} else if (cat[i] === 'o') {{
        fLoc.push(oV.loc[i]*(1-et)); fPO.push(oV.po[i]*(1-et)); fPS.push(oV.ps[i]*(1-et));
        fCL.push(oV.cl[i]*(1-et)); fCO.push(oV.co[i]*(1-et)); fCS.push(oV.cs[i]*(1-et));
      }} else {{
        fLoc.push(nV.loc[i]*et); fPO.push(nV.po[i]*et); fPS.push(nV.ps[i]*et);
        fCL.push(nV.cl[i]*et); fCO.push(nV.co[i]*et); fCS.push(nV.cs[i]*et);
      }}
    }}
    dChart.options.scales.yL.max = startDL+et*(endDL-startDL);
    dChart.options.scales.yP.max = startDP+et*(endDP-startDP);
    cChart.options.scales.yL.max = startCL+et*(endCL-startCL);
    cChart.options.scales.yP.max = startCP+et*(endCP-startCP);
    var twts = [];
    for (var i=0;i<uLen;i++) twts.push(uDates[i]===TL_TODAY ? 0 : sc[i]);
    var tOa = wtrendline(fPO, twts), tSa = wtrendline(fPS, twts);
    var linePO = lerpFading(fPO, sc, uLen, keepArr), linePS = lerpFading(fPS, sc, uLen, keepArr);
    dChart.data.datasets[0].data = fLoc;
    dChart.data.datasets[1].data = linePO;
    dChart.data.datasets[2].data = tOa;
    dChart.data.datasets[3].data = linePS;
    dChart.data.datasets[4].data = tSa;
    var labelA = et<0.15 ? 1-et/0.15 : et>0.85 ? (et-0.85)/0.15 : 0;
    var labelC = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    if (!rotSnapped && labelA < 0.01) {{
      rotSnapped = true;
      if (!xSlide) {{ dChart.options.scales.x.ticks.minRotation = dXrot1; dChart.options.scales.x.ticks.maxRotation = dXrot1; }}
      if (!dYLslide) {{ dChart.options.scales.yL.ticks.minRotation = dYLrot1; dChart.options.scales.yL.ticks.maxRotation = dYLrot1; }}
      if (!dYPslide) {{ dChart.options.scales.yP.ticks.minRotation = dYProt1; dChart.options.scales.yP.ticks.maxRotation = dYProt1; }}
    }}
    if (xSlide) {{
      dChart.options.scales.x.ticks.color = function(ctx) {{
        var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
      }};
    }} else {{
      dChart.options.scales.x.ticks.color = labelC;
    }}
    if (!dYLslide) dChart.options.scales.yL.ticks.color = labelC;
    if (!dYPslide) dChart.options.scales.yP.ticks.color = labelC;
    dXhCur = dXh0+et*(dXh1-dXh0); cXhCur = cXh0+et*(cXh1-cXh0);
    dYLwCur = dYLw0+et*(dYLw1-dYLw0); dYPwCur = dYPw0+et*(dYPw1-dYPw0);
    cYLwCur = cYLw0+et*(cYLw1-cYLw0); cYPwCur = cYPw0+et*(cYPw1-cYPw0);
    dChart._barScales = sc; dChart.update('none');
    cChart.data.datasets[0].data = fCL;
    cChart.data.datasets[1].data = fCO;
    cChart.data.datasets[2].data = fCS;
    cChart._barScales = sc; cChart.update('none');
    renderBdFrame(oD, nD, et);
    if (!done) {{ transId = requestAnimationFrame(tick); }}
    else {{
      dChart._barScales = null; cChart._barScales = null;
      dChart.data.datasets[1].spanGaps = false; dChart.data.datasets[3].spanGaps = false;
      dChart.options.scales.x.ticks.color = C.text;
      dChart.options.scales.yL.ticks.color = C.text; dChart.options.scales.yP.ticks.color = C.text;
      delete dChart.options.scales.x.ticks.minRotation; dChart.options.scales.x.ticks.maxRotation = 50;
      delete dChart.options.scales.yL.ticks.minRotation; delete dChart.options.scales.yL.ticks.maxRotation;
      delete dChart.options.scales.yP.ticks.minRotation; delete dChart.options.scales.yP.ticks.maxRotation;
      delete dChart.options.scales.yL.max; delete dChart.options.scales.yP.max;
      delete cChart.options.scales.yL.max; delete cChart.options.scales.yP.max;
      delete dChart.options.scales.yL.afterFit; delete dChart.options.scales.yP.afterFit;
      delete cChart.options.scales.yL.afterFit; delete cChart.options.scales.yP.afterFit;
      delete dChart.options.scales.x.afterFit; delete cChart.options.scales.x.afterFit;
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
