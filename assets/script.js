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
