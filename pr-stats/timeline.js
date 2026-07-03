(function() {
var TL_TODAY_LABEL = fmtLabel(TL_TODAY);
var activeRepo = null;
function activeTL() { return activeRepo ? (TL_REPOS[activeRepo] || []) : TL_ALL; }
var isDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
var C = {
  green: isDark ? '#3fb950' : '#1a7f37',
  blue: isDark ? '#58a6ff' : '#3376d2',
  purple: isDark ? '#bc8cff' : '#8250df',
  text: isDark ? '#e6edf3' : '#1a1a1a',
  grid: isDark ? 'rgba(139,148,158,0.15)' : 'rgba(0,0,0,0.06)',
};
Chart.defaults.color = C.text;
Chart.defaults.borderColor = C.grid;

Chart.register({
  id: 'smoothLayout',
  beforeDraw: function(chart) {
    var sc = chart._barScales;
    if (!sc || !sc._xTransform) return;
    var n = chart.data.labels.length;
    if (n < 1) return;
    var eN = sc._xTransform.effectiveN;
    if (eN < 0.01) return;
    var aL = chart.chartArea.left, aR = chart.chartArea.right;
    if (aR <= aL) return;
    var aW = aR - aL;
    var xS = chart.scales.x;
    chart._origGetPixel = xS.getPixelForValue.bind(xS);
    var useOffset = n > 1 && (xS.getPixelForValue(0) - aL) > 1;
    var pos = 0, eC = [];
    if (useOffset) {
      var eCW = aW / eN;
      for (var i = 0; i < n; i++) { var s = i < sc.length ? sc[i] : 1; eC.push(aL + (pos + 0.5 * s) * eCW); pos += s; }
      chart._smoothECW = eCW;
    } else {
      var cum = [], cpos = 0;
      for (var i = 0; i < n; i++) { var s = i < sc.length ? sc[i] : 1; cum.push(cpos); cpos += s; }
      var denom = n > 1 ? cum[n - 1] : 1;
      if (denom < 0.01) denom = 0.01;
      for (var i = 0; i < n; i++) eC.push(aL + (cum[i] / denom) * aW);
      chart._smoothECW = eN > 1 ? aW / (eN - 1) : aW;
    }
    chart._smoothECs = eC;
    xS.getPixelForValue = function(value) {
      if (value >= 0 && value < eC.length && value === (value | 0)) return eC[value];
      return chart._origGetPixel(value);
    };
    chart._origGetLabelItems = xS.getLabelItems.bind(xS);
    var adjustLabelItems = function(labelItems) {
      if (!labelItems) return labelItems;
      var rotation = xS.labelRotation || 0;
      var rotationRad = -rotation * Math.PI / 180;
      var ctx = chart.ctx;
      for (var li = 0; li < labelItems.length; li++) {
        var item = labelItems[li];
        var tick = xS.ticks && xS.ticks[li];
        var ci = tick && typeof tick.value === 'number' ? tick.value : li;
        if ((ci < 0 || ci >= eC.length || chart.data.labels[ci] !== item.label) && item.label != null) {
          var fi = chart.data.labels.indexOf(item.label);
          if (fi >= 0) ci = fi;
        }
        if (ci < 0 || ci >= eC.length) continue;
        var opts = item.options || item;
        if (typeof opts.rotation === 'number') opts.rotation = rotationRad;
        if (!opts.translation && !item.translation) continue;
        ctx.save();
        ctx.font = item.font && item.font.string ? item.font.string : ctx.font;
        var label = Array.isArray(item.label) ? item.label.join(' ') : item.label;
        var w = ctx.measureText(label || '').width;
        ctx.restore();
        var align = opts.textAlign || 'center';
        var localCenterX = align === 'right' || align === 'end' ? -w / 2 : align === 'left' || align === 'start' ? w / 2 : 0;
        var localCenterY = item.textOffset || 0;
        var centerOffset = Math.cos(rotationRad) * localCenterX - Math.sin(rotationRad) * localCenterY;
        var defaultCenter = eC[ci] + centerOffset;
        var startCenters = chart._xLabelStartCenters;
        var targetCenters = chart._xLabelTargetCenters;
        var targetFactor = typeof chart._xLabelTargetFactor === 'number' ? chart._xLabelTargetFactor : 0;
        var startCenter = startCenters && label != null ? startCenters[label] : null;
        var targetCenter = targetCenters && label != null ? targetCenters[label] : null;
        if (typeof startCenter !== 'number') startCenter = defaultCenter;
        if (typeof targetCenter !== 'number') targetCenter = defaultCenter;
        var desiredCenter = startCenter + (targetCenter - startCenter) * targetFactor;
        if (opts.translation) opts.translation[0] = desiredCenter - centerOffset;
        else item.translation[0] = desiredCenter - centerOffset;
      }
      return labelItems;
    };
    xS.getLabelItems = function(area) {
      return adjustLabelItems(chart._origGetLabelItems(area));
    };
    adjustLabelItems(xS._labelItems);
  },
  beforeDatasetsDraw: function(chart) {
    var sc = chart._barScales;
    if (!sc || !sc._xTransform) return;
    var eC = chart._smoothECs;
    var eCW = chart._smoothECW;
    if (!eC) return;
    var n = chart.data.labels.length;
    var bR = 0.72;
    if (n > 1 && chart._origGetPixel) {
      var uCW = chart._origGetPixel(1) - chart._origGetPixel(0);
      if (uCW > 0) for (var di = 0; di < chart.data.datasets.length; di++) {
        var m = chart.getDatasetMeta(di);
        if (m.type === 'bar' && m.data.length > 0 && m.data[0].width > 0) { bR = m.data[0].width / uCW; break; }
      }
    }
    for (var di = 0; di < chart.data.datasets.length; di++) {
      var meta = chart.getDatasetMeta(di);
      for (var i = 0; i < meta.data.length && i < n; i++) {
        var el = meta.data[i];
        var dx = eC[i] - el.x;
        el.x = eC[i];
        if (typeof el.width !== 'undefined') { var s = i < sc.length ? sc[i] : 1; el.width = Math.max(0, s * eCW * bR); }
        if (typeof el.cp1x !== 'undefined') el.cp1x += dx;
        if (typeof el.cp2x !== 'undefined') el.cp2x += dx;
      }
    }
    var aL = chart.chartArea.left, aW = chart.chartArea.right - aL;
    var clipL = aL, clipR = aL + aW;
    if (chart._clipEdges) {
      var fi = -1, li = -1;
      for (var ci = 0; ci < n; ci++) {
        var sv = ci < sc.length ? sc[ci] : 1;
        if (sv > 0.99) { if (fi < 0) fi = ci; li = ci; }
      }
      if (fi >= 0 && fi < eC.length) {
        clipL = Math.max(aL, eC[fi] - eCW * 0.5);
        clipR = Math.min(aL + aW, eC[li] + eCW * 0.5);
      }
    }
    chart.ctx.save();
    chart.ctx.beginPath();
    chart.ctx.rect(clipL, 0, clipR - clipL, chart.canvas.height);
    chart.ctx.clip();
  },
  afterDatasetsDraw: function(chart) {
    if (chart._barScales && chart._barScales._xTransform) chart.ctx.restore();
  },
  afterDraw: function(chart) {
    if (chart._origGetPixel) {
      chart.scales.x.getPixelForValue = chart._origGetPixel;
      if (chart._origGetLabelItems) chart.scales.x.getLabelItems = chart._origGetLabelItems;
      delete chart._origGetPixel;
      delete chart._origGetLabelItems;
      delete chart._xLabelStartCenters;
      delete chart._xLabelTargetCenters;
      delete chart._xLabelTargetFactor;
      delete chart._smoothECs;
      delete chart._smoothECW;
    }
  }
});

var pillsEl = document.getElementById('tl-repo-pills');
var allPill = document.createElement('div');
allPill.className = 'sort-pill active';
allPill.setAttribute('data-repo', '');
allPill.textContent = 'All';
pillsEl.appendChild(allPill);
var TL_LABELS = {"hermes-webui": "webui", "claude-mem": "cmem"};
TL_NAMES.forEach(function(name) {
  var pill = document.createElement('div');
  pill.className = 'sort-pill';
  pill.setAttribute('data-repo', name);
  pill.textContent = TL_LABELS[name] || name;
  pillsEl.appendChild(pill);
});

function fmtLabel(s) {
  var p = s.split('-');
  var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  return months[+p[1]-1] + ' ' + +p[2];
}
function fmtK(v) { return v >= 1000 ? (v/1000).toFixed(1)+'k' : v; }
function trendline(vals) {
  var n = vals.length;
  if (n < 2) return vals.map(function() { return null; });
  var sx=0, sy=0, sxx=0, sxy=0;
  for (var i=0; i<n; i++) { sx+=i; sy+=vals[i]; sxx+=i*i; sxy+=i*vals[i]; }
  var m = (n*sxy - sx*sy) / (n*sxx - sx*sx);
  var b = (sy - m*sx) / n;
  return vals.map(function(_, i) { return i === 0 || i === n-1 ? Math.max(0, m*i + b) : null; });
}
function wtrendline(vals, wts) {
  var n = vals.length;
  if (n < 2) return vals.map(function(){return null;});
  var sw=0,sx=0,sy=0,sxx=0,sxy=0;
  for (var i=0;i<n;i++){var w=wts[i];sw+=w;sx+=w*i;sy+=w*vals[i];sxx+=w*i*i;sxy+=w*i*vals[i];}
  if (sw<0.01) return vals.map(function(){return null;});
  var d=sw*sxx-sx*sx;
  if(Math.abs(d)<1e-10) return vals.map(function(){return null;});
  var m=(sw*sxy-sx*sy)/d, b=(sy-m*sx)/sw;
  var fi=-1,li=-1;
  for(var i=0;i<n;i++) if(wts[i]>0.01){if(fi<0)fi=i;li=i;}
  if(fi<0||fi===li) return vals.map(function(){return null;});
  var r=vals.map(function(){return null;});
  r[fi]=Math.max(0,m*fi+b); r[li]=Math.max(0,m*li+b);
  return r;
}
function wregression(vals, wts) {
  var n = vals.length;
  var sw=0,sx=0,sy=0,sxx=0,sxy=0;
  for (var i=0;i<n;i++){var w=wts[i];sw+=w;sx+=w*i;sy+=w*vals[i];sxx+=w*i*i;sxy+=w*i*vals[i];}
  if (sw<0.01) return null;
  var d=sw*sxx-sx*sx;
  if(Math.abs(d)<1e-10) return null;
  var m=(sw*sxy-sx*sy)/d, b=(sy-m*sx)/sw;
  var fi=-1,li=-1;
  for(var i=0;i<n;i++) if(wts[i]>0.01){if(fi<0)fi=i;li=i;}
  if(fi<0||fi===li) return null;
  return {m:m, b:b, fi:fi, li:li};
}
function trendFrame(regO, regN, et, done, n) {
  var arr = new Array(n);
  for (var i=0;i<n;i++) arr[i]=null;
  if (!regO && !regN) return arr;
  if (!regO) {
    arr[regN.fi] = done ? Math.max(0,regN.m*regN.fi+regN.b) : Math.max(0,regN.m*regN.fi+regN.b)*et;
    arr[regN.li] = done ? Math.max(0,regN.m*regN.li+regN.b) : Math.max(0,regN.m*regN.li+regN.b)*et;
    return arr;
  }
  if (!regN) {
    arr[regO.fi] = done ? null : Math.max(0,regO.m*regO.fi+regO.b)*(1-et);
    arr[regO.li] = done ? null : Math.max(0,regO.m*regO.li+regO.b)*(1-et);
    return arr;
  }
  var m = regO.m+et*(regN.m-regO.m), b = regO.b+et*(regN.b-regO.b);
  var fi = Math.round(regO.fi+et*(regN.fi-regO.fi));
  var li = Math.round(regO.li+et*(regN.li-regO.li));
  arr[fi] = Math.max(0,m*fi+b);
  arr[li] = Math.max(0,m*li+b);
  return arr;
}
function ease(t) { return t<0.5 ? 4*t*t*t : 1-Math.pow(-2*t+2,3)/2; }
var textRgb = (function(c){return parseInt(c.slice(1,3),16)+','+parseInt(c.slice(3,5),16)+','+parseInt(c.slice(5,7),16);})(C.text);
function textAlpha(a) { return 'rgba('+textRgb+','+a+')'; }
function labelVisualCenters(chart) {
  var xS = chart.scales.x, ctx = chart.ctx, items = xS.getLabelItems ? xS.getLabelItems() : xS._labelItems;
  var centers = {};
  if (!items) return centers;
  for (var i = 0; i < items.length; i++) {
    var item = items[i], opts = item.options || item;
    if (!opts.translation) continue;
    ctx.save();
    ctx.font = item.font && item.font.string ? item.font.string : ctx.font;
    var label = Array.isArray(item.label) ? item.label.join(' ') : item.label;
    var w = ctx.measureText(label || '').width;
    ctx.restore();
    var align = opts.textAlign || 'center';
    var localCenterX = align === 'right' || align === 'end' ? -w / 2 : align === 'left' || align === 'start' ? w / 2 : 0;
    var localCenterY = item.textOffset || 0;
    centers[label] = opts.translation[0] + Math.cos(opts.rotation || 0) * localCenterX - Math.sin(opts.rotation || 0) * localCenterY;
  }
  return centers;
}
function setXLabelTransition(chart, starts, targets, factor) {
  chart._xLabelStartCenters = starts || null;
  chart._xLabelTargetCenters = targets || null;
  chart._xLabelTargetFactor = factor;
}
function lerpFading(data, sc, len, keep) {
  var out = data.slice();
  for (var i = 0; i < len; i++) {
    if (sc[i] > 0.99) continue;
    var pI = -1, nI = -1;
    for (var j = i - 1; j >= 0; j--) if (sc[j] > 0.99) { pI = j; break; }
    for (var j = i + 1; j < len; j++) if (sc[j] > 0.99) { nI = j; break; }
    if (pI >= 0 && nI >= 0) {
      var f = (i - pI) / (nI - pI);
      out[i] = data[i] * sc[i] + (data[pI] + f * (data[nI] - data[pI])) * (1 - sc[i]);
      if (sc[i] < 0.01) out[i] = null;
    } else {
      var k = Array.isArray(keep) ? keep[i] : keep;
      if (!k) out[i] = null;
    }
  }
  return out;
}
function sliceData(days) {
  var src = activeTL();
  if (!days || !src.length) return src;
  var last = src[src.length-1].date, p = last.split('-');
  var cut = new Date(p[0], p[1]-1, p[2]);
  cut.setDate(cut.getDate() - days);
  var cs = cut.toISOString().slice(0,10);
  return src.filter(function(d) { return d.date >= cs; });
}
function updateBreakdown(r) {
  var sl = sliceData(r);
  var total = 0, shipped = 0, open = 0, superseded = 0, lost = 0;
  var totalLoc = 0, activeDays = 0;
  var firstDate = null, lastDate = null, prevDate = null;
  for (var i = 0; i < sl.length; i++) {
    var d = sl[i];
    total += d.prsOpened;
    shipped += (d.clsShipped || 0);
    open += (d.clsOpen || 0);
    superseded += (d.clsSuperseded || 0);
    lost += (d.clsLost || 0);
    totalLoc += d.loc;
    if (d.prsOpened > 0) {
      activeDays++;
      if (!firstDate) firstDate = d.date;
      prevDate = lastDate;
      lastDate = d.date;
    }
  }
  var displayDays = activeDays;
  if (lastDate === TL_TODAY) {
    displayDays = Math.max(0, displayDays - 1);
    lastDate = prevDate;
  }
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
  var dayStr = displayDays === 1 ? '1 day' : displayDays + ' days';
  if (el = document.getElementById('bd-days')) el.textContent = dayStr;
  if (firstDate && lastDate) {
    var months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    var fp = firstDate.split('-'), lp = lastDate.split('-');
    if (el = document.getElementById('bd-days-label')) el.textContent =
      'Active days from ' + months[+fp[1]-1] + ' ' + +fp[2] + ' - ' + months[+lp[1]-1] + ' ' + +lp[2];
  } else {
    if (el = document.getElementById('bd-days-label')) el.textContent = 'No active days in range';
  }
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
  segs.forEach(function(s) {
    var el = document.getElementById(s[0]);
    if (!el) return;
    var pct = (s[1] / barTotal * 100).toFixed(1);
    el.setAttribute('data-width', pct);
    el.style.width = pct + '%';
    var wide = parseFloat(pct) > 4;
    if (el.classList.contains('bar-open')) { el.textContent = s[1]; el.title = String(s[1]); }
    else { el.textContent = wide ? s[1] : ''; el.title = wide ? String(s[1]) : ''; }
  });
  var legs = {
    'bd-leg-shipped': ['Shipped', shipped], 'bd-leg-superseded': ['Superseded', superseded],
    'bd-leg-lost': ['Lost', lost], 'bd-leg-open': ['Open', open]
  };
  for (var lid in legs) {
    var lel = document.getElementById(lid);
    if (!lel) continue;
    var dot = lel.querySelector('.legend-dot');
    lel.textContent = '';
    lel.appendChild(dot);
    lel.appendChild(document.createTextNode(' ' + legs[lid][0] + ' (' + legs[lid][1] + ')'));
  }
}

var range = 0, dChart, cChart, animId = 0, transId = 0;
function cleanupAnim() {
  if (!dChart || !cChart) return;
  dChart._barScales = null; cChart._barScales = null;
  dChart._clipEdges = false; cChart._clipEdges = false;
  dChart.data.datasets.forEach(function(ds) { ds.spanGaps = false; });
  cChart.data.datasets.forEach(function(ds) { ds.spanGaps = false; });
  dChart.config._config.options.scales.x.ticks.color = C.text;
  cChart.config._config.options.scales.x.ticks.color = C.text;
  dChart.options.scales.yL.ticks.color = C.text; dChart.options.scales.yP.ticks.color = C.text;
  cChart.options.scales.yL.ticks.color = C.text; cChart.options.scales.yP.ticks.color = C.text;
  delete dChart.options.scales.x.ticks.autoSkip; dChart.options.scales.x.ticks.maxRotation = 50;
  delete cChart.options.scales.x.ticks.autoSkip; cChart.options.scales.x.ticks.maxRotation = 50;
  delete dChart.options.scales.yL.max; delete dChart.options.scales.yP.max;
  delete cChart.options.scales.yL.max; delete cChart.options.scales.yP.max;
  delete dChart.options.scales.yL.afterFit; delete dChart.options.scales.yP.afterFit;
  delete cChart.options.scales.yL.afterFit; delete cChart.options.scales.yP.afterFit;
  delete dChart.options.scales.x.afterFit; delete cChart.options.scales.x.afterFit;
}
function isToday(ctx) { return ctx.chart.data.labels[ctx.dataIndex] === TL_TODAY_LABEL; }
function segToday(ctx) { return ctx.chart.data.labels[ctx.p1DataIndex] === TL_TODAY_LABEL ? 'transparent' : undefined; }
function build(r) {
  var sl = sliceData(r);
  var labs = sl.map(function(d){ return fmtLabel(d.date); });
  if (dChart) dChart.destroy();
  if (cChart) cChart.destroy();

  var hasToday = sl.length > 0 && sl[sl.length - 1].date === TL_TODAY;
  var slT = hasToday ? sl.slice(0, -1) : sl;
  var tOpened = trendline(slT.map(function(d){return d.prsOpened}));
  var tShipped = trendline(slT.map(function(d){return d.prsShipped}));
  if (hasToday) { tOpened.push(null); tShipped.push(null); }

  dChart = new Chart(document.getElementById('tlDailyChart'), {
    type: 'bar',
    data: {
      labels: labs,
      datasets: [
        { label: 'Lines of code', data: sl.map(function(d){return d.loc}),
           backgroundColor: function(ctx) { return isToday(ctx) ? C.green+'20' : C.green+'40'; },
           borderColor: function(ctx) { return isToday(ctx) ? C.green+'30' : C.green+'60'; },
           borderWidth: 1, borderRadius: 3, yAxisID: 'yL', order: 4 },
        { label: 'PRs opened', data: sl.map(function(d){return d.prsOpened}), type: 'line',
           borderColor: C.blue, borderWidth: 2.5, tension: 0, yAxisID: 'yP', order: 1,
           pointRadius: 4, pointHoverRadius: 6,
           pointBackgroundColor: function(ctx) { return isToday(ctx) ? C.blue+'80' : C.blue; },
           segment: { borderColor: segToday } },
        { label: ' ', data: tOpened, type: 'line',
           borderColor: C.blue, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 0, spanGaps: true },
        { label: 'PRs shipped', data: sl.map(function(d){return d.prsShipped}), type: 'line',
           borderColor: C.purple, borderWidth: 2.5, borderDash: [5,3], tension: 0, yAxisID: 'yP', order: 3,
           pointRadius: 4, pointHoverRadius: 6,
           pointBackgroundColor: function(ctx) { return isToday(ctx) ? C.purple+'80' : C.purple; },
           segment: { borderColor: segToday } },
        { label: '  ', data: tShipped, type: 'line',
           borderColor: C.purple, borderWidth: 1.5, borderDash: [6,4], pointRadius: 0, pointHitRadius: 0, tension: 0, yAxisID: 'yP', order: 2, spanGaps: true },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 0 }, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: {display:false}, ticks: {maxRotation:50, font:{size:11}} },
        yL: { position:'left', title:{display:true,text:'LOC'}, ticks:{stepSize:2500,callback:fmtK}, grid:{color:C.grid}, beginAtZero:true },
        yP: { position:'right', title:{display:true,text:'PRs'}, grid:{drawOnChartArea:false}, beginAtZero:true },
      },
      plugins: {
        tooltip: { filter: function(ctx) { return ctx.dataset.label.trim().length > 0; }, bodySpacing: 7, titleMarginBottom: 8, callbacks: {
          title: function(ctx) {
            var d = sl[ctx[0].dataIndex], p = d.date.split('-');
            var dt = new Date(p[0],p[1]-1,p[2]);
            return ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][dt.getDay()] + ', ' + labs[ctx[0].dataIndex] + ' ' + p[0];
          },
          afterBody: function(ctx) {
            var d = sl[ctx[0].dataIndex];
            return [' ', '+'+fmtK(d.additions)+'/-'+fmtK(d.deletions)+' ('+d.files+' files)',
              d.prsOpened > 0 ? 'LOC/PR: '+fmtK(d.locPerPr)+'  Files/PR: '+d.filesPerPr : ''].filter(Boolean);
          }
        } },
        legend: { position: 'top', labels: { padding: 28, boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 2, filter: function(item) { return item.text.trim().length > 0; } } },
      },
    },
  });

  cChart = new Chart(document.getElementById('tlCumulativeChart'), {
    type: 'line',
    data: {
      labels: labs,
      datasets: [
        { label: 'Cum. LOC', data: sl.map(function(d){return d.cumLoc}),
           borderColor: C.green, borderWidth: 2, backgroundColor: C.green+'20', fill: true, tension: 0, yAxisID: 'yL',
           pointRadius: 3, pointBackgroundColor: function(ctx) { return isToday(ctx) ? C.green+'80' : C.green; } },
        { label: 'Cum. PRs opened', data: sl.map(function(d){return d.cumOpened}),
           borderColor: C.blue, borderWidth: 2, tension: 0, yAxisID: 'yP',
           pointRadius: 3, pointBackgroundColor: function(ctx) { return isToday(ctx) ? C.blue+'80' : C.blue; } },
        { label: 'Cum. PRs shipped', data: sl.map(function(d){return d.cumShipped}),
           borderColor: C.purple, borderWidth: 2, borderDash: [5,3], tension: 0, yAxisID: 'yP',
           pointRadius: 3, pointBackgroundColor: function(ctx) { return isToday(ctx) ? C.purple+'80' : C.purple; } },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false, animation: { duration: 0 }, interaction: { mode: 'index', intersect: false },
      scales: {
        x: { grid: {display:false}, ticks: {maxRotation:50, font:{size:11}} },
        yL: { position:'left', title:{display:true,text:'LOC'}, ticks:{stepSize:2500,callback:fmtK}, grid:{color:C.grid} },
        yP: { position:'right', title:{display:true,text:'PRs'}, grid:{drawOnChartArea:false} },
      },
      plugins: { legend: { position: 'top', labels: { padding: 28, boxWidth: 12, boxHeight: 12, useBorderRadius: true, borderRadius: 2 } } },
    },
  });
}
function bdStats(r) {
  var sl = sliceData(r);
  var t = 0, s = 0, o = 0, sp = 0, l = 0, loc = 0, ad = 0, todayActive = false;
  for (var i = 0; i < sl.length; i++) {
    var d = sl[i]; t += d.prsOpened; s += (d.clsShipped||0); o += (d.clsOpen||0);
    sp += (d.clsSuperseded||0); l += (d.clsLost||0); loc += d.loc;
    if (d.prsOpened > 0) { ad++; if (d.date === TL_TODAY) todayActive = true; }
  }
  var dd = todayActive ? Math.max(0, ad - 1) : ad;
  return {total:t, shipped:s, open:o, sup:sp, lost:l, loc:loc, activeDays:ad, displayDays:dd};
}
function bdDisplay(b) {
  var ls = b.lost + b.sup, cd = b.shipped + b.lost + b.sup;
  var rate = cd > 0 ? Math.round(b.shipped / cd * 100) : 0;
  var ad = Math.max(1, b.activeDays);
  var avgPrs = b.total / ad, avgLoc = b.loc / ad;
  var bT = b.total || 1;
  return {total:b.total, shipped:b.shipped, open:b.open, sup:b.sup, lost:b.lost, lostSup:ls,
    rate:rate, activeDays:ad, displayDays:b.displayDays, avgPrs:avgPrs, avgLoc:avgLoc,
    barSh:b.shipped/bT*100, barSp:b.sup/bT*100, barL:b.lost/bT*100, barO:b.open/bT*100};
}
function renderBdFrame(oD, nD, et) {
  var cT = Math.round(oD.total+et*(nD.total-oD.total));
  var cSh = Math.round(oD.shipped+et*(nD.shipped-oD.shipped));
  var cO = Math.round(oD.open+et*(nD.open-oD.open));
  var cSp = Math.round(oD.sup+et*(nD.sup-oD.sup));
  var cL = Math.round(oD.lost+et*(nD.lost-oD.lost));
  var cR = Math.round(oD.rate+et*(nD.rate-oD.rate));
  var cDd = Math.max(0, Math.round(oD.displayDays+et*(nD.displayDays-oD.displayDays)));
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
  if (el = document.getElementById('bd-days')) el.textContent = cDd === 1 ? '1 day' : cDd + ' days';
  [['bd-bar-shipped',cSh,oD.barSh,nD.barSh],['bd-bar-superseded',cSp,oD.barSp,nD.barSp],
   ['bd-bar-lost',cL,oD.barL,nD.barL],['bd-bar-open',cO,oD.barO,nD.barO]].forEach(function(s) {
    var el = document.getElementById(s[0]); if (!el) return;
    var pct = (s[2]+et*(s[3]-s[2])).toFixed(1); el.style.width = pct+'%';
    var wide = parseFloat(pct) > 4;
    if (el.classList.contains('bar-open')) { el.textContent = s[1]; el.title = String(s[1]); }
    else { el.textContent = wide ? s[1] : ''; el.title = wide ? String(s[1]) : ''; }
  });
  var legs = {'bd-leg-shipped':['Shipped',cSh],'bd-leg-superseded':['Superseded',cSp],
    'bd-leg-lost':['Lost',cL],'bd-leg-open':['Open',cO]};
  for (var lid in legs) {
    var lel = document.getElementById(lid); if (!lel) continue;
    var dot = lel.querySelector('.legend-dot'); lel.textContent = '';
    lel.appendChild(dot);
    lel.appendChild(document.createTextNode(' '+legs[lid][0]+' ('+legs[lid][1]+')'));
  }
}
function transitionRange(newR) {
  if (transId) { cancelAnimationFrame(transId); transId = 0; cleanupAnim(); build(range); }
  var oldSl = sliceData(range);
  var newSl = sliceData(newR);
  var oldN = oldSl.length, newN = newSl.length;
  var oB = bdStats(range), nB = bdStats(newR);
  range = newR;
  if (oldN === newN) { build(newR); updateBreakdown(newR); return; }
  var sup = oldN > newN ? oldSl : newSl;
  var supLen = sup.length;
  var supLabs = sup.map(function(d) { return fmtLabel(d.date); });
  var supDates = sup.map(function(d) { return d.date; });
  var dS = [sup.map(function(d){return d.loc}), sup.map(function(d){return d.prsOpened}),
    null, sup.map(function(d){return d.prsShipped}), null];
  var cS = [sup.map(function(d){return d.cumLoc}), sup.map(function(d){return d.cumOpened}),
    sup.map(function(d){return d.cumShipped})];
  var oD = bdDisplay(oB), nD = bdDisplay(nB);
  var expanding = newN > oldN;
  var edgeCount = Math.abs(newN - oldN);
  var lastIsToday = supDates[supLen - 1] === TL_TODAY;
  var oldTwts = [];
  for (var i = 0; i < supLen; i++) {
    if (supDates[i] === TL_TODAY) oldTwts.push(0);
    else if (expanding) oldTwts.push(i >= edgeCount ? 1 : 0);
    else oldTwts.push(1);
  }
  var newTwts = [];
  for (var i = 0; i < supLen; i++) {
    if (supDates[i] === TL_TODAY) newTwts.push(0);
    else if (expanding) newTwts.push(1);
    else newTwts.push(i >= edgeCount ? 1 : 0);
  }
  var rO0 = wregression(dS[1], oldTwts), rS0 = wregression(dS[3], oldTwts);
  var rO1 = wregression(dS[1], newTwts), rS1 = wregression(dS[3], newTwts);
  var startDL = dChart.scales.yL.max, startDP = dChart.scales.yP.max;
  var startCL = cChart.scales.yL.max, startCP = cChart.scales.yP.max;
  var dYLw0 = dChart.scales.yL.width, dYPw0 = dChart.scales.yP.width, dXh0 = dChart.scales.x.height;
  var cYLw0 = cChart.scales.yL.width, cYPw0 = cChart.scales.yP.width, cXh0 = cChart.scales.x.height;
  var dXrot0 = dChart.scales.x.labelRotation, dYLrot0 = dChart.scales.yL.labelRotation, dYProt0 = dChart.scales.yP.labelRotation;
  var cXrot0 = cChart.scales.x.labelRotation, cYLrot0 = cChart.scales.yL.labelRotation, cYProt0 = cChart.scales.yP.labelRotation;
  var dXStarts = labelVisualCenters(dChart), cXStarts = labelVisualCenters(cChart);
  build(newR);
  var endDL = dChart.scales.yL.max, endDP = dChart.scales.yP.max;
  var endCL = cChart.scales.yL.max, endCP = cChart.scales.yP.max;
  var dYLw1 = dChart.scales.yL.width, dYPw1 = dChart.scales.yP.width;
  var cYLw1 = cChart.scales.yL.width, cYPw1 = cChart.scales.yP.width;
  var dXh1 = dChart.scales.x.height, cXh1 = cChart.scales.x.height;
  var dXrot1 = dChart.scales.x.labelRotation, dYLrot1 = dChart.scales.yL.labelRotation, dYProt1 = dChart.scales.yP.labelRotation;
  var cXrot1 = cChart.scales.x.labelRotation, cYLrot1 = cChart.scales.yL.labelRotation, cYProt1 = cChart.scales.yP.labelRotation;
  var dXTargets = labelVisualCenters(dChart), cXTargets = labelVisualCenters(cChart);
  var dXhCur = dXh0, cXhCur = cXh0;
  var dXrotCur = dXrot0, cXrotCur = cXrot0;
  var dYLwCur = dYLw0, dYPwCur = dYPw0, cYLwCur = cYLw0, cYPwCur = cYPw0;
  dChart.options.scales.yL.afterFit = function(a){a.width=dYLwCur;}; dChart.options.scales.yP.afterFit = function(a){a.width=dYPwCur;};
  cChart.options.scales.yL.afterFit = function(a){a.width=cYLwCur;}; cChart.options.scales.yP.afterFit = function(a){a.width=cYPwCur;};
  dChart.options.scales.x.afterFit = function(a){a.height=dXhCur; a.labelRotation=dXrotCur;};
  cChart.options.scales.x.afterFit = function(a){a.height=cXhCur; a.labelRotation=cXrotCur;};
  var dVis = document.getElementById('tl-daily-wrap').style.display !== 'none';
  var cVis = document.getElementById('tl-cumulative-wrap').style.display !== 'none';
  dChart.data.labels = supLabs;
  dChart.data.datasets[0].data = dS[0];
  dChart.data.datasets[1].data = dS[1];
  dChart.data.datasets[2].data = trendFrame(rO0, rO1, 0, false, supLen);
  dChart.data.datasets[3].data = dS[3];
  dChart.data.datasets[4].data = trendFrame(rS0, rS1, 0, false, supLen);
  cChart.data.labels = supLabs;
  cChart.data.datasets[0].data = cS[0];
  cChart.data.datasets[1].data = cS[1];
  cChart.data.datasets[2].data = cS[2];
  var initSc = [];
  for (var i = 0; i < supLen; i++) {
    if (expanding) initSc.push(i < edgeCount ? 0 : 1);
    else initSc.push(1);
  }
  initSc._xTransform = { effectiveN: oldN };
  var dYLslide = Math.abs(dYLw0 - dYLw1) < 1 && dYLrot0 === dYLrot1;
  var dYPslide = Math.abs(dYPw0 - dYPw1) < 1 && dYProt0 === dYProt1;
  var cYLslide = Math.abs(cYLw0 - cYLw1) < 1 && cYLrot0 === cYLrot1;
  var cYPslide = Math.abs(cYPw0 - cYPw1) < 1 && cYProt0 === cYProt1;
  dChart._barScales = initSc;
  setXLabelTransition(dChart, dXStarts, dXTargets, 0);
  dChart._clipEdges = true;
  dChart.options.scales.x.ticks.autoSkip = false;
  dChart.config._config.options.scales.x.ticks.color = function(ctx) { return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; };
  if (!dYLslide) dChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!dYPslide) dChart.options.scales.yP.ticks.color = textAlpha(0);
  dChart.data.datasets[1].spanGaps = true; dChart.data.datasets[3].spanGaps = true;
  dChart.options.scales.yL.max = startDL; dChart.options.scales.yP.max = startDP;
  if (dVis) dChart.update('none');
  cChart._barScales = initSc;
  setXLabelTransition(cChart, cXStarts, cXTargets, 0);
  cChart._clipEdges = true;
  cChart.options.scales.x.ticks.autoSkip = false;
  cChart.config._config.options.scales.x.ticks.color = function(ctx) { return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; };
  if (!cYLslide) cChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!cYPslide) cChart.options.scales.yP.ticks.color = textAlpha(0);
  cChart.data.datasets[0].spanGaps = true; cChart.data.datasets[1].spanGaps = true; cChart.data.datasets[2].spanGaps = true;
  cChart.options.scales.yL.max = startCL; cChart.options.scales.yP.max = startCP;
  if (cVis) cChart.update('none');
  var tDur = 500, tStart = null;
  window._animLog = [];
  transId = requestAnimationFrame(function tick(now) {
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : ease(Math.min(elapsed / tDur, 1));
    var nExact = oldN + et * (newN - oldN);
    var sc = [];
    for (var i = 0; i < supLen; i++) {
      if (expanding) {
        var fromRight = supLen - 1 - i;
        if (fromRight < nExact - 1) sc.push(1);
        else if (fromRight < nExact) sc.push(nExact - fromRight);
        else sc.push(0);
      } else {
        if (i < supLen - nExact) sc.push(Math.max(0, 1 - (supLen - nExact - i)));
        else sc.push(1);
      }
    }
    sc._xTransform = { effectiveN: Math.max(0.5, nExact) };
    var tOa = trendFrame(rO0, rO1, et, done, supLen);
    var tSa = trendFrame(rS0, rS1, et, done, supLen);
    var lineO = lerpFading(dS[1], sc, supLen, true), lineS = lerpFading(dS[3], sc, supLen, true);
    dChart.data.datasets[1].data = lineO;
    dChart.data.datasets[2].data = tOa;
    dChart.data.datasets[3].data = lineS;
    dChart.data.datasets[4].data = tSa;
    dChart.options.scales.yL.max = Math.round(startDL+et*(endDL-startDL));
    dChart.options.scales.yP.max = Math.round(startDP+et*(endDP-startDP));
    cChart.options.scales.yL.max = Math.round(startCL+et*(endCL-startCL));
    cChart.options.scales.yP.max = Math.round(startCP+et*(endCP-startCP));
    var labelA = et<0.15 ? 1-et/0.15 : et>0.85 ? (et-0.85)/0.15 : 0;
    var labelC = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    dChart.config._config.options.scales.x.ticks.color = function(ctx) {
      var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
    };
    cChart.config._config.options.scales.x.ticks.color = function(ctx) {
      var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
    };
    if (!dYLslide) dChart.options.scales.yL.ticks.color = labelC;
    if (!dYPslide) dChart.options.scales.yP.ticks.color = labelC;
    if (!cYLslide) cChart.options.scales.yL.ticks.color = labelC;
    if (!cYPslide) cChart.options.scales.yP.ticks.color = labelC;
    dXhCur = dXh0+et*(dXh1-dXh0); cXhCur = cXh0+et*(cXh1-cXh0);
    dXrotCur = dXrot0+et*(dXrot1-dXrot0); cXrotCur = cXrot0+et*(cXrot1-cXrot0);
    dYLwCur = dYLw0+et*(dYLw1-dYLw0); dYPwCur = dYPw0+et*(dYPw1-dYPw0);
    cYLwCur = cYLw0+et*(cYLw1-cYLw0); cYPwCur = cYPw0+et*(cYPw1-cYPw0);
    dChart._barScales = sc; setXLabelTransition(dChart, dXStarts, dXTargets, et); if (dVis) dChart.update('none');
    var cf0 = [], cf1 = [], cf2 = [];
    for (var i = 0; i < supLen; i++) {
      if (sc[i] > 0.99) { cf0.push(cS[0][i]); cf1.push(cS[1][i]); cf2.push(cS[2][i]); }
      else { cf0.push(null); cf1.push(null); cf2.push(null); }
    }
    cChart.data.datasets[0].data = cf0;
    cChart.data.datasets[1].data = cf1;
    cChart.data.datasets[2].data = cf2;
    cChart._barScales = sc; setXLabelTransition(cChart, cXStarts, cXTargets, et); if (cVis) cChart.update('none');
    var vc = dVis ? dChart : cChart;
    if (window._animLog) window._animLog.push({f:window._animLog.length, ms:Math.round(elapsed), et:+et.toFixed(3), effN:+nExact.toFixed(1), area:[Math.round(vc.chartArea.left),Math.round(vc.chartArea.right)], ticks:vc.scales.x.ticks?vc.scales.x.ticks.length:0, xRot:+vc.scales.x.labelRotation.toFixed(1), xH:Math.round(vc.scales.x.height), sc:sc.map(function(v){return +v.toFixed(2)})});
    renderBdFrame(oD, nD, et);
    if (!done) { transId = requestAnimationFrame(tick); }
    else {
      if (window._animLog) { window._lastAnimLog = window._animLog; window._animLog = null; }
      transId = requestAnimationFrame(function() {
        cleanupAnim();
        transId = 0; build(newR); updateBreakdown(newR);
      });
    }
  });
}
build(range);

(function animateOnLoad() {
  var all = TL_ALL;
  var total = 0, shipped = 0, opn = 0, sup = 0, lost = 0, totalLoc = 0, activeDays = 0;
  var todayActive = false;
  for (var i = 0; i < all.length; i++) {
    var d = all[i];
    total += d.prsOpened; shipped += (d.clsShipped || 0); opn += (d.clsOpen || 0);
    sup += (d.clsSuperseded || 0); lost += (d.clsLost || 0); totalLoc += d.loc;
    if (d.prsOpened > 0) { activeDays++; if (d.date === TL_TODAY) todayActive = true; }
  }
  var displayDays = todayActive ? Math.max(0, activeDays - 1) : activeDays;
  var phases = [];
  var prev = 0;
  [7, 14, 30].forEach(function(days) {
    var s = sliceData(days), t = 0;
    for (var j = 0; j < s.length; j++) t += s[j].prsOpened;
    phases.push({pill: String(days), from: prev, to: t});
    prev = t;
  });
  phases.push({pill: '0', from: prev, to: total});
  function dispAt(f) {
    var t = Math.round(f * total), sh = Math.round(f * shipped);
    var o = Math.round(f * opn), sp = Math.round(f * sup), l = Math.round(f * lost);
    var cd = sh + l + sp, rate = cd > 0 ? Math.round(sh / cd * 100) : 0;
    var ad = Math.max(1, Math.round(f * activeDays)), loc = Math.round(f * totalLoc);
    var dd = Math.max(0, Math.round(f * displayDays));
    var bT = t || 1;
    return {total:t, shipped:sh, open:o, sup:sp, lost:l, lostSup:l+sp,
      rate:rate, activeDays:ad, displayDays:dd, avgPrs:t/ad, avgLoc:loc/ad,
      barSh:sh/bT*100, barSp:sp/bT*100, barL:l/bT*100, barO:o/bT*100};
  }
  phases.forEach(function(ph) {
    ph.startD = dispAt(ph.from / total);
    ph.endD = dispAt(ph.to / total);
  });
  var p0s = phases[0].startD, p0e = phases[0].endD;
  p0s.barSh = 66.7; p0s.barSp = 0; p0s.barL = 0; p0s.barO = 33.3;
  var dur = 1000, phaseDur = dur / phases.length, start = null;
  var pills = document.querySelectorAll('#bd-range-pills .sort-pill');
  var dLabsFull = dChart.data.labels.slice();
  var cLabsFull = cChart.data.labels.slice();
  var dFull = dChart.data.datasets.map(function(ds) { return ds.data.slice(); });
  var cFull = cChart.data.datasets.map(function(ds) { return ds.data.slice(); });
  var totalPts = dLabsFull.length;
  var dLoadTargets = labelVisualCenters(dChart), cLoadTargets = labelVisualCenters(cChart);
  dChart.config._config.options.scales.x.ticks.color = textAlpha(0);
  cChart.config._config.options.scales.x.ticks.color = textAlpha(0);
  animId = requestAnimationFrame(function tick(now) {
    if (!start) start = now;
    var elapsed = now - start, done = elapsed >= dur;
    var prog = done ? 1 : ease(Math.min(elapsed / dur, 1));
    var nExact = done ? totalPts : Math.max(0.5, prog * totalPts);
    var sc = [];
    for (var i = 0; i < totalPts; i++) {
      if (i + 1 <= nExact) sc.push(1);
      else if (i < nExact) sc.push(nExact - i);
      else sc.push(0);
    }
    sc._xTransform = { effectiveN: nExact };
    var labelA = prog>0.7 ? (prog-0.7)/0.3 : 0;
    dChart.config._config.options.scales.x.ticks.color = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    cChart.config._config.options.scales.x.ticks.color = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    dChart._barScales = sc; setXLabelTransition(dChart, null, dLoadTargets, prog); dChart.update('none');
    cChart._barScales = sc; setXLabelTransition(cChart, null, cLoadTargets, prog);
    var pi = done ? phases.length - 1 : Math.min(Math.floor(elapsed / phaseDur), phases.length - 1);
    var phase = phases[pi];
    var et = done ? 1 : ease(Math.min((elapsed - pi * phaseDur) / phaseDur, 1));
    var sD = phase.startD, eD = phase.endD;
    var cT = Math.round(sD.total+et*(eD.total-sD.total));
    if (cT < 1) { animId = requestAnimationFrame(tick); return; }
    var activePill = phase.pill;
    pills.forEach(function(p) { p.classList.toggle('active', p.getAttribute('data-range') === activePill); });
    renderBdFrame(sD, eD, et);
    if (!done) { animId = requestAnimationFrame(tick); }
    else {
      animId = 0;
      dChart._barScales = null; cChart._barScales = null;
      setXLabelTransition(dChart, null, null, 0); setXLabelTransition(cChart, null, null, 0);
      dChart.config._config.options.scales.x.ticks.color = C.text;
      cChart.config._config.options.scales.x.ticks.color = C.text;
      dChart.update('none'); cChart.update('none');
      updateBreakdown(0);
      pills.forEach(function(p) { p.classList.toggle('active', p.getAttribute('data-range') === '0'); });
    }
  });
})();

document.getElementById('bd-range-pills').addEventListener('click', function(e) {
  var p = e.target.closest('.sort-pill'); if (!p) return;
  var newR = parseInt(p.getAttribute('data-range'), 10);
  if (transId) { cancelAnimationFrame(transId); transId = 0; cleanupAnim(); build(range); }
  document.querySelectorAll('#bd-range-pills .sort-pill').forEach(function(x){ x.classList.remove('active') });
  p.classList.add('active');
  if (animId) { cancelAnimationFrame(animId); animId = 0; range = newR; build(newR); updateBreakdown(newR); }
  else { transitionRange(newR); }
});
document.getElementById('tl-view-pills').addEventListener('click', function(e) {
  var p = e.target.closest('.sort-pill'); if (!p) return;
  var v = p.getAttribute('data-view');
  document.querySelectorAll('#tl-view-pills .sort-pill').forEach(function(x){ x.classList.remove('active') });
  p.classList.add('active');
  document.getElementById('tl-daily-wrap').style.display = v === 'daily' ? '' : 'none';
  document.getElementById('tl-cumulative-wrap').style.display = v === 'cumulative' ? '' : 'none';
  if (v === 'daily') dChart.resize();
  else cChart.resize();
});
pillsEl.addEventListener('click', function(e) {
  var p = e.target.closest('.sort-pill'); if (!p) return;
  if (animId) { cancelAnimationFrame(animId); animId = 0; }
  if (transId) { cancelAnimationFrame(transId); transId = 0; cleanupAnim(); build(range); }
  var oD = bdDisplay(bdStats(range));
  var oldSl = sliceData(range);
  activeRepo = p.getAttribute('data-repo') || null;
  pillsEl.querySelectorAll('.sort-pill').forEach(function(x){ x.classList.remove('active') });
  p.classList.add('active');
  var newSl = sliceData(range);
  var nD = bdDisplay(bdStats(range));
  var dateSet = {};
  oldSl.forEach(function(d) { dateSet[d.date] = true; });
  newSl.forEach(function(d) { dateSet[d.date] = true; });
  var uDates = Object.keys(dateSet).sort();
  var uLabs = uDates.map(fmtLabel);
  var uLen = uLabs.length;
  var oldBy = {}, newBy = {};
  oldSl.forEach(function(d) { oldBy[d.date] = d; });
  newSl.forEach(function(d) { newBy[d.date] = d; });
  var oldN = oldSl.length, newN = newSl.length;
  var cat = [];
  var oV = {loc:[],po:[],ps:[],cl:[],co:[],cs:[]};
  var nV = {loc:[],po:[],ps:[],cl:[],co:[],cs:[]};
  var ocl=0,oco=0,ocs=0,ncl=0,nco=0,ncs=0;
  uDates.forEach(function(dt, i) {
    var o = oldBy[dt], n = newBy[dt];
    if (o && n) cat.push('s');
    else if (o) cat.push('o');
    else cat.push('n');
    oV.loc.push(o?o.loc:0); oV.po.push(o?o.prsOpened:0); oV.ps.push(o?o.prsShipped:0);
    nV.loc.push(n?n.loc:0); nV.po.push(n?n.prsOpened:0); nV.ps.push(n?n.prsShipped:0);
    if(o){ocl=o.cumLoc;oco=o.cumOpened;ocs=o.cumShipped;}
    if(n){ncl=n.cumLoc;nco=n.cumOpened;ncs=n.cumShipped;}
    oV.cl.push(ocl);oV.co.push(oco);oV.cs.push(ocs);
    nV.cl.push(ncl);nV.co.push(nco);nV.cs.push(ncs);
  });
  for (var i = 0; i < uLen; i++) {
    if (cat[i] === 'o') { nV.cl[i]=oV.cl[i]; nV.co[i]=oV.co[i]; nV.cs[i]=oV.cs[i]; }
    else if (cat[i] === 'n') { oV.cl[i]=nV.cl[i]; oV.co[i]=nV.co[i]; oV.cs[i]=nV.cs[i]; }
  }
  var firstS = -1, lastS = -1;
  for (var i = 0; i < uLen; i++) if (cat[i] === 's') { if (firstS < 0) firstS = i; lastS = i; }
  var startDL = dChart.scales.yL.max, startDP = dChart.scales.yP.max;
  var startCL = cChart.scales.yL.max, startCP = cChart.scales.yP.max;
  var dYLw0 = dChart.scales.yL.width, dYPw0 = dChart.scales.yP.width, dXh0 = dChart.scales.x.height;
  var cYLw0 = cChart.scales.yL.width, cYPw0 = cChart.scales.yP.width, cXh0 = cChart.scales.x.height;
  var dXrot0 = dChart.scales.x.labelRotation, dYLrot0 = dChart.scales.yL.labelRotation, dYProt0 = dChart.scales.yP.labelRotation;
  var cXrot0 = cChart.scales.x.labelRotation, cYLrot0 = cChart.scales.yL.labelRotation, cYProt0 = cChart.scales.yP.labelRotation;
  var dXStarts = labelVisualCenters(dChart), cXStarts = labelVisualCenters(cChart);
  build(range);
  var endDL = dChart.scales.yL.max, endDP = dChart.scales.yP.max;
  var endCL = cChart.scales.yL.max, endCP = cChart.scales.yP.max;
  var dYLw1 = dChart.scales.yL.width, dYPw1 = dChart.scales.yP.width;
  var cYLw1 = cChart.scales.yL.width, cYPw1 = cChart.scales.yP.width;
  var dXh1 = dChart.scales.x.height, cXh1 = cChart.scales.x.height;
  var dXrot1 = dChart.scales.x.labelRotation, dYLrot1 = dChart.scales.yL.labelRotation, dYProt1 = dChart.scales.yP.labelRotation;
  var cXrot1 = cChart.scales.x.labelRotation, cYLrot1 = cChart.scales.yL.labelRotation, cYProt1 = cChart.scales.yP.labelRotation;
  var dXTargets = labelVisualCenters(dChart), cXTargets = labelVisualCenters(cChart);
  var dXhCur = dXh0, cXhCur = cXh0;
  var dXrotCur = dXrot0, cXrotCur = cXrot0;
  var dYLwCur = dYLw0, dYPwCur = dYPw0, cYLwCur = cYLw0, cYPwCur = cYPw0;
  dChart.options.scales.yL.afterFit = function(a){a.width=dYLwCur;}; dChart.options.scales.yP.afterFit = function(a){a.width=dYPwCur;};
  cChart.options.scales.yL.afterFit = function(a){a.width=cYLwCur;}; cChart.options.scales.yP.afterFit = function(a){a.width=cYPwCur;};
  dChart.options.scales.x.afterFit = function(a){a.height=dXhCur; a.labelRotation=dXrotCur;};
  cChart.options.scales.x.afterFit = function(a){a.height=cXhCur; a.labelRotation=cXrotCur;};
  var dVis = document.getElementById('tl-daily-wrap').style.display !== 'none';
  var cVis = document.getElementById('tl-cumulative-wrap').style.display !== 'none';
  var nullArr = uLabs.map(function(){return null;});
  dChart.data.labels = uLabs;
  dChart.data.datasets[0].data = oV.loc.slice();
  dChart.data.datasets[1].data = oV.po.slice();
  dChart.data.datasets[2].data = trendFrame(rOold, rOnew, 0, false, uLen);
  dChart.data.datasets[3].data = oV.ps.slice();
  dChart.data.datasets[4].data = trendFrame(rSold, rSnew, 0, false, uLen);
  dChart.options.scales.yL.max = startDL; dChart.options.scales.yP.max = startDP;
  var initSc = [];
  for (var i = 0; i < uLen; i++) initSc.push(cat[i] === 'n' ? 0 : 1);
  initSc._xTransform = { effectiveN: oldN };
  var dYLslide = Math.abs(dYLw0 - dYLw1) < 1 && dYLrot0 === dYLrot1;
  var dYPslide = Math.abs(dYPw0 - dYPw1) < 1 && dYProt0 === dYProt1;
  var cYLslide = Math.abs(cYLw0 - cYLw1) < 1 && cYLrot0 === cYLrot1;
  var cYPslide = Math.abs(cYPw0 - cYPw1) < 1 && cYProt0 === cYProt1;
  var keepArr = cat.map(function(c) { return c !== 'o'; });
  var oTwts = [], nTwts = [];
  for (var i = 0; i < uLen; i++) {
    var tod = uDates[i] === TL_TODAY ? 0 : 1;
    oTwts.push(cat[i] !== 'n' ? tod : 0);
    nTwts.push(cat[i] !== 'o' ? tod : 0);
  }
  var rOold = wregression(oV.po, oTwts), rSold = wregression(oV.ps, oTwts);
  var rOnew = wregression(nV.po, nTwts), rSnew = wregression(nV.ps, nTwts);
  dChart._barScales = initSc;
  setXLabelTransition(dChart, dXStarts, dXTargets, 0);
  dChart.options.scales.x.ticks.autoSkip = false;
  dChart.config._config.options.scales.x.ticks.color = function(ctx) { return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; };
  if (!dYLslide) dChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!dYPslide) dChart.options.scales.yP.ticks.color = textAlpha(0);
  dChart.data.datasets[1].spanGaps = true; dChart.data.datasets[3].spanGaps = true;
  if (dVis) dChart.update('none');
  cChart.data.labels = uLabs;
  cChart.data.datasets[0].data = oV.cl.slice();
  cChart.data.datasets[1].data = oV.co.slice();
  cChart.data.datasets[2].data = oV.cs.slice();
  cChart.options.scales.yL.max = startCL; cChart.options.scales.yP.max = startCP;
  cChart._barScales = initSc;
  setXLabelTransition(cChart, cXStarts, cXTargets, 0);
  cChart.options.scales.x.ticks.autoSkip = false;
  cChart.config._config.options.scales.x.ticks.color = function(ctx) { return initSc[ctx.index] > 0.99 ? C.text : 'transparent'; };
  if (!cYLslide) cChart.options.scales.yL.ticks.color = textAlpha(0);
  if (!cYPslide) cChart.options.scales.yP.ticks.color = textAlpha(0);
  cChart.data.datasets[0].spanGaps = true; cChart.data.datasets[1].spanGaps = true; cChart.data.datasets[2].spanGaps = true;
  if (cVis) cChart.update('none');
  var tDur = 500, tStart = null;
  window._animLog = [];
  transId = requestAnimationFrame(function tick(now) {
    if (!tStart) tStart = now;
    var elapsed = now - tStart, done = elapsed >= tDur;
    var et = done ? 1 : ease(Math.min(elapsed / tDur, 1));
    var nExact = oldN + et * (newN - oldN);
    var sc = [];
    for (var i = 0; i < uLen; i++) {
      if (cat[i] === 's') sc.push(1);
      else if (cat[i] === 'o') sc.push(1 - et);
      else sc.push(et);
    }
    sc._xTransform = { effectiveN: Math.max(0.5, nExact) };
    var fLoc=[], fPO=[], fPS=[], fCL=[], fCO=[], fCS=[];
    for (var i = 0; i < uLen; i++) {
      if (cat[i] === 's') {
        fLoc.push(oV.loc[i]+et*(nV.loc[i]-oV.loc[i]));
        fPO.push(oV.po[i]+et*(nV.po[i]-oV.po[i]));
        fPS.push(oV.ps[i]+et*(nV.ps[i]-oV.ps[i]));
        fCL.push(oV.cl[i]+et*(nV.cl[i]-oV.cl[i]));
        fCO.push(oV.co[i]+et*(nV.co[i]-oV.co[i]));
        fCS.push(oV.cs[i]+et*(nV.cs[i]-oV.cs[i]));
      } else {
        var isOld = cat[i] === 'o', fade = isOld ? 1-et : et, rv = isOld ? oV : nV;
        fLoc.push(rv.loc[i]*fade); fPO.push(rv.po[i]*fade); fPS.push(rv.ps[i]*fade);
        var anchor = firstS >= 0 && i < firstS ? firstS : lastS >= 0 && i > lastS ? lastS : -1;
        if (anchor >= 0) {
          var sCl = oV.cl[anchor]+et*(nV.cl[anchor]-oV.cl[anchor]);
          var sCo = oV.co[anchor]+et*(nV.co[anchor]-oV.co[anchor]);
          var sCs = oV.cs[anchor]+et*(nV.cs[anchor]-oV.cs[anchor]);
          fCL.push(rv.cl[anchor]>0 ? rv.cl[i]/rv.cl[anchor]*sCl : null);
          fCO.push(rv.co[anchor]>0 ? rv.co[i]/rv.co[anchor]*sCo : null);
          fCS.push(rv.cs[anchor]>0 ? rv.cs[i]/rv.cs[anchor]*sCs : null);
        } else {
          fCL.push(null); fCO.push(null); fCS.push(null);
        }
      }
    }
    dChart.options.scales.yL.max = Math.round(startDL+et*(endDL-startDL));
    dChart.options.scales.yP.max = Math.round(startDP+et*(endDP-startDP));
    cChart.options.scales.yL.max = Math.round(startCL+et*(endCL-startCL));
    cChart.options.scales.yP.max = Math.round(startCP+et*(endCP-startCP));
    var tOa = trendFrame(rOold, rOnew, et, done, uLen);
    var tSa = trendFrame(rSold, rSnew, et, done, uLen);
    var linePO = lerpFading(fPO, sc, uLen, keepArr), linePS = lerpFading(fPS, sc, uLen, keepArr);
    dChart.data.datasets[0].data = fLoc;
    dChart.data.datasets[1].data = linePO;
    dChart.data.datasets[2].data = tOa;
    dChart.data.datasets[3].data = linePS;
    dChart.data.datasets[4].data = tSa;
    var labelA = et<0.15 ? 1-et/0.15 : et>0.85 ? (et-0.85)/0.15 : 0;
    var labelC = labelA<0.01 ? 'transparent' : textAlpha(labelA);
    dChart.config._config.options.scales.x.ticks.color = function(ctx) {
      var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
    };
    cChart.config._config.options.scales.x.ticks.color = function(ctx) {
      var s = sc[ctx.index]; return s > 0.99 ? C.text : s < 0.01 ? 'transparent' : textAlpha(s);
    };
    if (!dYLslide) dChart.options.scales.yL.ticks.color = labelC;
    if (!dYPslide) dChart.options.scales.yP.ticks.color = labelC;
    if (!cYLslide) cChart.options.scales.yL.ticks.color = labelC;
    if (!cYPslide) cChart.options.scales.yP.ticks.color = labelC;
    dXhCur = dXh0+et*(dXh1-dXh0); cXhCur = cXh0+et*(cXh1-cXh0);
    dXrotCur = dXrot0+et*(dXrot1-dXrot0); cXrotCur = cXrot0+et*(cXrot1-cXrot0);
    dYLwCur = dYLw0+et*(dYLw1-dYLw0); dYPwCur = dYPw0+et*(dYPw1-dYPw0);
    cYLwCur = cYLw0+et*(cYLw1-cYLw0); cYPwCur = cYPw0+et*(cYPw1-cYPw0);
    dChart._barScales = sc; setXLabelTransition(dChart, dXStarts, dXTargets, et); if (dVis) dChart.update('none');
    cChart.data.datasets[0].data = fCL;
    cChart.data.datasets[1].data = fCO;
    cChart.data.datasets[2].data = fCS;
    cChart._barScales = sc; setXLabelTransition(cChart, cXStarts, cXTargets, et); if (cVis) cChart.update('none');
    var vc = dVis ? dChart : cChart;
    if (window._animLog) window._animLog.push({f:window._animLog.length, ms:Math.round(elapsed), et:+et.toFixed(3), effN:+nExact.toFixed(1), area:[Math.round(vc.chartArea.left),Math.round(vc.chartArea.right)], xRot:+vc.scales.x.labelRotation.toFixed(1), xH:Math.round(vc.scales.x.height), cat:cat.join(''), sc:sc.map(function(v){return +v.toFixed(2)})});
    renderBdFrame(oD, nD, et);
    if (!done) { transId = requestAnimationFrame(tick); }
    else {
      if (window._animLog) { window._lastAnimLog = window._animLog; window._animLog = null; }
      transId = requestAnimationFrame(function() {
        cleanupAnim();
        transId = 0; build(range); updateBreakdown(range);
      });
    }
  });
});
})();
