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
  var pathname = document.location.pathname;
  var onDocs = pathname.indexOf('/docs') !== -1;

  function makeSep() {
    var sep = document.createElement('span');
    sep.className = 'nav-sep';
    sep.textContent = '/';
    return sep;
  }

  var currentEl = nav.querySelector('.current');

  var hasDocsLink = nav.querySelector('a[href*="docs"]') ||
    (currentEl && currentEl.textContent.trim() === 'Docs');
  if (!hasDocsLink) {
    var docsLink = document.createElement('a');
    docsLink.href = onDocs ? './' : '../docs/';
    docsLink.textContent = 'Docs';
    nav.appendChild(makeSep());
    nav.appendChild(docsLink);
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
    function appendHomeSep() {
      var sep = document.createElement('span');
      sep.className = 'home-links-sep';
      sep.setAttribute('aria-hidden', 'true');
      sep.textContent = '/';
      homeLinks.appendChild(sep);
    }

    function appendHomeLink(href, text) {
      var a = document.createElement('a');
      a.href = href;
      a.textContent = text;
      homeLinks.appendChild(a);
    }

    appendHomeSep();
    appendHomeLink('docs/', 'Docs');
  }

  document.querySelectorAll('[data-pair]').forEach(function(el) {
    var pair = el.getAttribute('data-pair');
    el.addEventListener('mouseenter', function() { setPairHover(pair, true); });
    el.addEventListener('mouseleave', function() { setPairHover(pair, false); });
  });

  document.querySelectorAll('a.preview > img').forEach(function(img) {
    function show() { img.classList.add('loaded'); }
    if (img.complete) show();
    else img.addEventListener('load', show);
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
      row.classList.remove('top-collapsed-row');
      row.classList.toggle('collapse-hidden', collapsed && itemIndex > visibleItems);
    });
  }

  function setupTopCollapsedBlock(block) {
    if (!block || block.getAttribute('data-collapse-mode') !== 'top') return;
    var visibleItems = collapsibleVisibleItems(block);
    if (!visibleItems) return;
    var tbody = block.querySelector('tbody');
    if (!tbody) return;
    var colspan = parseInt(block.getAttribute('data-expand-colspan') || '0', 10);
    if (!colspan) {
      var firstRow = tbody.querySelector('tr');
      colspan = firstRow ? firstRow.children.length : 1;
    }
    if (!tbody.querySelector('.expand-row')) {
      tbody.insertAdjacentHTML('afterbegin', expandRowHtml(block.id, 'Show latest ' + visibleItems, colspan));
    }
    syncTopCollapsedRows(block);
  }

  function syncBottomCollapsedRows(block) {
    if (!block || block.getAttribute('data-collapse-mode') !== 'bottom') return;
    var visibleItems = collapsibleVisibleItems(block);
    if (!visibleItems) return;
    var rowsPerItem = collapsibleRowsPerItem(block);
    var tbody = block.querySelector('tbody');
    if (!tbody) return;
    var rows = Array.from(tbody.querySelectorAll('tr')).filter(function(row) {
      return !row.classList.contains('expand-row');
    });
    var totalItems = Math.ceil(rows.length / rowsPerItem);
    var collapsed = block.classList.contains('collapsed');
    rows.forEach(function(row, index) {
      var itemIndex = Math.floor(index / rowsPerItem) + 1;
      row.classList.remove('bottom-collapsed-row');
      row.classList.toggle('collapse-hidden', collapsed && itemIndex <= totalItems - visibleItems);
    });
    var overlay = block.querySelector('.overlay-row');
    if (overlay) {
      overlay.hidden = collapsed || totalItems <= visibleItems;
    }
  }

  function setupBottomCollapsedBlock(block) {
    if (!block || block.getAttribute('data-collapse-mode') !== 'bottom') return;
    var visibleItems = collapsibleVisibleItems(block);
    if (!visibleItems) return;
    var overlayLabel = block.getAttribute('data-collapse-label') || 'Collapse';
    if (!block.querySelector('.overlay-row')) {
      block.insertAdjacentHTML('beforeend', collapseOverlayHtml(block.id, overlayLabel));
    }
    syncBottomCollapsedRows(block);
  }

  window.toggleCollapsedTable = function(blockId, event) {
    if (event) event.preventDefault();
    var block = document.getElementById(blockId);
    if (!block) return;
    block.classList.toggle('collapsed');
    syncTopCollapsedRows(block);
    syncBottomCollapsedRows(block);
  };

  document.querySelectorAll('.collapsible-table').forEach(function(block) {
    setupTopCollapsedBlock(block);
    setupBottomCollapsedBlock(block);
  });
}
