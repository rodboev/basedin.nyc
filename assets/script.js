var isLocalhost = document.location.hostname === 'localhost';

function setPairHover(pair, on) {
  document.querySelectorAll('[data-pair="' + pair + '"]').forEach(function(el) {
    el.classList.toggle('hover', on);
  });
}

function clearTouchHover() {
  document.querySelectorAll('a.hover').forEach(function(el) {
    el.classList.remove('hover');
  });
}

document.addEventListener('touchstart', function(e) {
  var a = e.target.closest('a');
  if (!a) return;
  a.classList.add('hover');
  var pair = a.getAttribute('data-pair');
  if (pair) setPairHover(pair, true);
}, { passive: true });

document.addEventListener('touchend', clearTouchHover);
document.addEventListener('touchcancel', clearTouchHover);

function injectLocalNavLinks() {
  if (!isLocalhost) return;

  var nav = document.querySelector('.nav-links');
  if (!nav) return;

  function makeSep() {
    var sep = document.createElement('span');
    sep.className = 'nav-sep';
    sep.textContent = '/';
    return sep;
  }

  var repoEl = nav.querySelector('.nav-repo') ||
    nav.querySelector('a[href*="github.com/rodboev/pr-sweep"]');
  var currentEl = nav.querySelector('.current');
  var nodes = [];

  var hasTargets = nav.querySelector('a[href*="pr-targets"]') ||
    (currentEl && currentEl.textContent.trim() === 'Targets');
  if (!hasTargets) {
    var onTargets = document.location.pathname.indexOf('/pr-targets') !== -1;
    var targetsEl = onTargets ? document.createElement('span') : document.createElement('a');
    if (onTargets) {
      targetsEl.className = 'current';
      targetsEl.textContent = 'Targets';
    } else {
      targetsEl.href = '../pr-targets/';
      targetsEl.textContent = 'Targets';
    }
    nodes.push(makeSep(), targetsEl);
  }

  var hasDocsLink = nav.querySelector('a[href*="docs"]') ||
    (currentEl && currentEl.textContent.trim() === 'Docs');
  if (!hasDocsLink) {
    var docsLink = document.createElement('a');
    docsLink.href = document.location.pathname.indexOf('/docs') !== -1 ? './' : '../docs/';
    docsLink.textContent = 'Docs';
    nodes.push(makeSep(), docsLink);
  }

  if (nodes.length) {
    if (repoEl) {
      for (var i = nodes.length - 1; i >= 0; i--) {
        repoEl.parentNode.insertBefore(nodes[i], repoEl);
      }
    } else {
      nodes.forEach(function(node) { nav.appendChild(node); });
    }
  }

  if (!repoEl) {
    var repoLink = document.createElement('a');
    repoLink.href = 'https://github.com/rodboev/pr-sweep';
    repoLink.textContent = 'Repo';
    nav.appendChild(makeSep());
    nav.appendChild(repoLink);
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', injectLocalNavLinks);
} else {
  injectLocalNavLinks();
}

if (document.body.classList.contains('home')) {
  var homeLinks = document.querySelector('.home-links');

  if (isLocalhost && homeLinks) {
    function appendHomeLink(href, text) {
      var gap = document.createElement('span');
      gap.className = 'home-links-gap home-link--desktop';
      gap.setAttribute('aria-hidden', 'true');
      homeLinks.appendChild(gap);
      var a = document.createElement('a');
      a.className = 'home-link--desktop';
      a.href = href;
      a.textContent = text;
      homeLinks.appendChild(a);
    }
    appendHomeLink('docs/', 'Docs');
    appendHomeLink('https://github.com/rodboev/pr-sweep', 'Repo');
  }

  document.querySelectorAll('[data-pair]').forEach(function(el) {
    var pair = el.getAttribute('data-pair');
    el.addEventListener('mouseenter', function() { setPairHover(pair, true); });
    el.addEventListener('mouseleave', function() { setPairHover(pair, false); });
  });

  document.querySelectorAll('a.preview').forEach(function(link) {
    var filename = link.getAttribute('href').replace(/\/$/, '') + '.pdf';

    pdfjsLib.getDocument(filename).promise
    .then(function(pdf) { return pdf.getPage(1); })
    .then(function(page) {
      var elStyle = window.getComputedStyle(link),
          scale = parseFloat(elStyle.height) / parseFloat(elStyle.width) * window.devicePixelRatio,
          viewport = page.getViewport({ scale: scale }),
          canvas = document.createElement('canvas'),
          context = canvas.getContext('2d');

      canvas.height = viewport.height;
      canvas.width = viewport.width;
      canvas.style.display = 'none';
      document.body.appendChild(canvas);

      page.render({ canvasContext: context, viewport: viewport }).promise
      .then(function() {
        link.style.backgroundImage = 'url(' + canvas.toDataURL() + ')';
        canvas.parentNode.removeChild(canvas);
      });
    });
  });

  (function(i,s,o,g,r,a,m){i['GoogleAnalyticsObject']=r;i[r]=i[r]||function(){
  (i[r].q=i[r].q||[]).push(arguments)},i[r].l=1*new Date();a=s.createElement(o),
  m=s.getElementsByTagName(o)[0];a.async=1;a.src=g;m.parentNode.insertBefore(a,m)
  })(window,document,'script','https://www.google-analytics.com/analytics.js','ga');
  ga('create', 'UA-114825587-1', 'auto');
  ga('send', 'pageview');

  if (window.location.search)
    history.replaceState({}, '', '/');
}

if (document.body.classList.contains('pr')) {
  function collapsibleVisibleItems(block) {
    return parseInt(block.getAttribute('data-visible-items') || '0', 10);
  }

  function collapsibleRowsPerItem(block) {
    return parseInt(block.getAttribute('data-rows-per-item') || '1', 10);
  }

  function collapseCaret(up) {
    return ' <span class="caret">' + (up ? '&#9650;' : '&#9660;') + '</span>';
  }

  function expandRowHtml(blockId, label, colspan) {
    return '<tr class="expand-row" onclick="toggleCollapsedTable(\'' + blockId + '\', event)"><td colspan="' + colspan + '">' +
      label + collapseCaret(false) + '</td></tr>';
  }

  function collapseOverlayHtml(blockId, label) {
    return '<div class="overlay-row" onclick="toggleCollapsedTable(\'' + blockId + '\', event)">' +
      (label || 'Collapse') + collapseCaret(true) + '</div>';
  }

  function syncTopCollapsedRows(block) {
    if (!block || block.getAttribute('data-collapse-mode') !== 'top') return;
    var visibleItems = collapsibleVisibleItems(block);
    if (!visibleItems) return;
    var rowsPerItem = collapsibleRowsPerItem(block);
    var tbody = block.querySelector('tbody');
    if (!tbody) return;
    var dataRowCount = 0;
    var collapsed = block.classList.contains('collapsed');
    tbody.querySelectorAll('tr').forEach(function(row) {
      if (row.classList.contains('expand-row')) return;
      dataRowCount++;
      var itemIndex = Math.ceil(dataRowCount / rowsPerItem);
      row.classList.toggle('collapse-hidden', collapsed && itemIndex > visibleItems);
    });
  }

  function topModeAnchorRow(block) {
    var visibleItems = collapsibleVisibleItems(block);
    if (!visibleItems) return null;
    var rowsPerItem = collapsibleRowsPerItem(block);
    var targetDataRow = visibleItems * rowsPerItem;
    var tbody = block.querySelector('tbody');
    if (!tbody) return null;
    var dataRowCount = 0;
    var anchor = null;
    tbody.querySelectorAll('tr').forEach(function(row) {
      if (row.classList.contains('expand-row')) return;
      dataRowCount++;
      if (dataRowCount === targetDataRow) anchor = row;
    });
    return anchor;
  }

  function initCollapsibleTables() {
    document.querySelectorAll('.collapsible-table[data-collapse-mode="top"]').forEach(syncTopCollapsedRows);
  }

  function setCollapsibleCollapsed(block, collapsed) {
    if (!block) return;
    block.classList.toggle('collapsed', collapsed);
    syncTopCollapsedRows(block);
  }

  window.collapsibleVisibleItems = collapsibleVisibleItems;
  window.expandRowHtml = expandRowHtml;
  window.collapseOverlayHtml = collapseOverlayHtml;
  window.setCollapsibleCollapsed = setCollapsibleCollapsed;
  window.initCollapsibleTables = initCollapsibleTables;

  // The collapse overlay is always shown at the bottom of an expanded table
  // (CSS handles visibility), mirroring the expand row's position. Kept as a
  // no-op so existing callers remain safe.
  window.updateCollapsedOverlays = function() {};

  window.toggleCollapsedTable = function(id, evt) {
    var el = document.getElementById(id);
    if (!el) return;
    var willCollapse = !el.classList.contains('collapsed');
    var collapseMode = el.getAttribute('data-collapse-mode');

    if (willCollapse) {
      // Collapsing: keep whatever the user is anchored on fixed in the viewport.
      // Collapsing only hides the rows *below* the boundary row, so leaving the
      // scroll position untouched naturally keeps every surviving row exactly
      // where it is. We only scroll-correct in two cases:
      //  1. The top of the table is on screen -> you are reading from the top, so
      //     keep the table top pinned (corrects for any scroll clamping).
      //  2. The top has scrolled off above AND the bottom of the collapsed table
      //     would not land within the viewport -> pin the clicked control to
      //     where the floating overlay was, so "Show all" stays reachable at the
      //     bottom of the viewport.
      // Otherwise (top off-screen, but the surviving rows keep the collapsed
      // bottom in view) we leave the scroll alone so the top edge does not jump
      // back into view. Geometry-only, so it stays width-agnostic.
      var vh = window.innerHeight || document.documentElement.clientHeight;
      var tableEl = el.querySelector('table');
      var tableTopBefore = tableEl ? tableEl.getBoundingClientRect().top : null;
      var topVisible = tableTopBefore != null && tableTopBefore >= 0 && tableTopBefore < vh;
      var overlay = el.querySelector('.overlay-row');
      var overlayTopBefore = overlay ? overlay.getBoundingClientRect().top : null;

      el.classList.toggle('collapsed');
      syncTopCollapsedRows(el);

      if (topVisible) {
        window.scrollBy(0, tableEl.getBoundingClientRect().top - tableTopBefore);
      } else {
        var expandRow = el.querySelector('tr.expand-row');
        var controlRect = expandRow ? expandRow.getBoundingClientRect() : null;
        var controlInView = controlRect && controlRect.top >= 0 && controlRect.bottom <= vh;
        if (!controlInView && overlayTopBefore != null && expandRow && expandRow.offsetParent) {
          window.scrollBy(0, expandRow.getBoundingClientRect().top - overlayTopBefore);
        }
      }
    } else {
      // Expanding: keep the last visible row fixed so the newly revealed rows
      // flow in below it.
      var anchor = null;
      if (collapseMode === 'context') {
        anchor = el.querySelector('tr.is-self') || el.querySelector('tr[data-rank]');
      } else if (collapseMode === 'top') {
        anchor = topModeAnchorRow(el);
      }
      var anchorTop = anchor ? anchor.getBoundingClientRect().top : null;
      el.classList.toggle('collapsed');
      syncTopCollapsedRows(el);
      if (anchor && anchorTop != null) {
        window.scrollBy(0, anchor.getBoundingClientRect().top - anchorTop);
      }
    }

    updateCollapsedOverlays();
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function() {
      initCollapsibleTables();
    });
  } else {
    initCollapsibleTables();
  }
}

if (document.body.classList.contains('preview')) {
  var pdfFile = location.pathname.replace(/\/$/, '').split('/').pop() + '.pdf';

  function pdfDisplayWidth(page) {
    return Math.min(720, window.innerWidth * 0.96);
  }

  function pdfScale(page, displayWidth) {
    return displayWidth / page.getViewport({ scale: 1 }).width;
  }

  function styleSvg(svg, displayWidth) {
    svg.removeAttribute('width');
    svg.removeAttribute('height');
    svg.style.width = displayWidth + 'px';
    return svg;
  }

  function renderPages(pdf, renderPage) {
    var chain = Promise.resolve();
    for (var pageNum = 1; pageNum <= pdf.numPages; pageNum++) {
      chain = chain.then(function(n) {
        return function() { return renderPage(pdf, n); };
      }(pageNum));
    }
    return chain;
  }

  function renderSvgPage(pdf, pageNum) {
    return pdf.getPage(pageNum).then(function(page) {
      var displayWidth = pdfDisplayWidth(page);
      var scale = pdfScale(page, displayWidth);
      var viewport = page.getViewport({ scale: scale });
      return page.getOperatorList().then(function(opList) {
        var svgGfx = new pdfjsLib.SVGGraphics(page.commonObjs, page.objs);
        return svgGfx.getSVG(opList, viewport);
      }).then(function(svg) {
        document.body.appendChild(styleSvg(svg, displayWidth));
      });
    });
  }

  function renderHiDpiPage(pdf, pageNum) {
    return pdf.getPage(pageNum).then(function(page) {
      var dpr = window.devicePixelRatio || 1;
      var displayWidth = pdfDisplayWidth(page);
      var scale = pdfScale(page, displayWidth);
      var viewport = page.getViewport({ scale: scale });
      var hiDpiViewport = page.getViewport({ scale: scale * dpr * 2 });
      var fullCanvas = document.createElement('canvas');
      fullCanvas.width = hiDpiViewport.width;
      fullCanvas.height = hiDpiViewport.height;
      var canvasPromise = page.render({ canvasContext: fullCanvas.getContext('2d'), viewport: hiDpiViewport }).promise;
      var svgPromise = page.getOperatorList().then(function(opList) {
        var svgGfx = new pdfjsLib.SVGGraphics(page.commonObjs, page.objs, true);
        return svgGfx.getSVG(opList, viewport);
      });
      return Promise.all([canvasPromise, svgPromise]).then(function(results) {
        var svg = styleSvg(results[1], displayWidth);
        var wrapper = document.createElement('div');
        wrapper.style.position = 'relative';
        wrapper.appendChild(svg);
        document.body.appendChild(wrapper);
        var svgRect = svg.getBoundingClientRect();
        var scaleX = hiDpiViewport.width / svgRect.width;
        var scaleY = hiDpiViewport.height / svgRect.height;
        svg.querySelectorAll('image').forEach(function(img) {
          var clipEl = img.closest('[clip-path]');
          var rect = (clipEl || img).getBoundingClientRect();
          if (rect.width < 1 || rect.height < 1) return;
          var cx = (rect.left - svgRect.left) * scaleX;
          var cy = (rect.top - svgRect.top) * scaleY;
          var cw = rect.width * scaleX;
          var ch = rect.height * scaleY;
          var crop = document.createElement('canvas');
          crop.width = Math.round(cw);
          crop.height = Math.round(ch);
          crop.getContext('2d').drawImage(fullCanvas,
            Math.round(cx), Math.round(cy), Math.round(cw), Math.round(ch),
            0, 0, Math.round(cw), Math.round(ch));
          crop.style.cssText = 'position:absolute;display:block;pointer-events:none';
          crop.style.left = (rect.left - svgRect.left) + 'px';
          crop.style.top = (rect.top - svgRect.top) + 'px';
          crop.style.width = rect.width + 'px';
          crop.style.height = rect.height + 'px';
          wrapper.appendChild(crop);
        });
      });
    });
  }

  pdfjsLib.getDocument('/' + pdfFile).promise.then(function(pdf) {
    return renderPages(pdf, pdfFile === 'recommendations.pdf' ? renderHiDpiPage : renderSvgPage);
  });
}
