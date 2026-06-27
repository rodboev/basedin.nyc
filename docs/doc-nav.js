(function () {
  function initDocNav(row) {
    var pills = Array.from(row.querySelectorAll('.sort-pill[data-target]'));
    if (!pills.length) return;

    var targets = pills
      .map(function (pill) {
        return document.getElementById(pill.getAttribute('data-target'));
      })
      .filter(Boolean);

    if (!targets.length) return;

    function setActive(id) {
      pills.forEach(function (pill) {
        var isActive = pill.getAttribute('data-target') === id;
        pill.classList.toggle('active', isActive);
        if (isActive) {
          pill.setAttribute('aria-current', 'true');
        } else {
          pill.removeAttribute('aria-current');
        }
      });
    }

    function syncStickyOffset() {
      document.body.style.setProperty(
        '--landscape-row-offset',
        row.getBoundingClientRect().height + 'px'
      );
    }

    function scrollToTarget(id, behavior) {
      var target = document.getElementById(id);
      if (!target) return;
      target.scrollIntoView({ behavior: behavior, block: 'start' });
    }

    function updateActiveFromScroll() {
      var threshold = row.getBoundingClientRect().height + 24;
      var current = targets[0];

      targets.forEach(function (target) {
        if (target.getBoundingClientRect().top - threshold <= 0) {
          current = target;
        }
      });

      if (current) {
        setActive(current.id);
      }
    }

    row.addEventListener('click', function (event) {
      var pill = event.target.closest('.sort-pill[data-target]');
      if (!pill || !row.contains(pill)) return;

      var id = pill.getAttribute('data-target');
      if (!id) return;

      event.preventDefault();
      setActive(id);
      scrollToTarget(id, 'smooth');

      if (window.history && window.history.replaceState) {
        window.history.replaceState(null, '', '#' + id);
      } else {
        window.location.hash = id;
      }
    });

    syncStickyOffset();

    if (typeof ResizeObserver !== 'undefined') {
      new ResizeObserver(function () {
        syncStickyOffset();
        updateActiveFromScroll();
      }).observe(row);
    }

    window.addEventListener('resize', function () {
      syncStickyOffset();
      updateActiveFromScroll();
    });
    document.addEventListener('scroll', updateActiveFromScroll, { passive: true });

    if (window.location.hash) {
      var initialId = window.location.hash.slice(1);
      var initialTarget = document.getElementById(initialId);
      if (initialTarget) {
        setActive(initialId);
        window.setTimeout(function () {
          scrollToTarget(initialId, 'auto');
        }, 0);
        return;
      }
    }

    updateActiveFromScroll();
  }

  document.querySelectorAll('[data-doc-nav]').forEach(initDocNav);
})();
