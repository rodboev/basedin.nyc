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
