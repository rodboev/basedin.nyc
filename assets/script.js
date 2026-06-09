function setPairHover(pair, on) {
  var els = document.querySelectorAll('[data-pair="' + pair + '"]');
  for (var i = 0; i < els.length; i++) els[i].classList.toggle('hover', on);
}

document.addEventListener('touchstart', function(e) {
  var a = e.target.closest('a');
  if (!a) return;
  a.classList.add('hover');
  var pair = a.getAttribute('data-pair');
  if (pair) setPairHover(pair, true);
}, { passive: true });

document.addEventListener('touchend', function() {
  var els = document.querySelectorAll('a.hover');
  for (var i = 0; i < els.length; i++) els[i].classList.remove('hover');
});

document.addEventListener('touchcancel', function() {
  var els = document.querySelectorAll('a.hover');
  for (var i = 0; i < els.length; i++) els[i].classList.remove('hover');
});

if (document.body.classList.contains('home')) {
  var links = Array.prototype.slice.call(
    document.querySelectorAll('a.preview')
  );
  var isLocalhost = document.location.hostname === 'localhost';
  var homeLinks = document.querySelector('.home-links');

  if (isLocalhost && homeLinks) {
    var gap = document.createElement('span');
    gap.className = 'home-links-gap home-link--desktop';
    gap.setAttribute('aria-hidden', 'true');
    homeLinks.appendChild(gap);
    var docs = document.createElement('a');
    docs.className = 'home-link--desktop';
    docs.href = 'docs/';
    docs.textContent = 'Docs';
    homeLinks.appendChild(docs);
  }

  document.querySelectorAll('[data-pair]').forEach(function(el) {
    var pair = el.getAttribute('data-pair');
    el.addEventListener('mouseenter', function() { setPairHover(pair, true); });
    el.addEventListener('mouseleave', function() { setPairHover(pair, false); });
  });

  links.forEach(function(link) {
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

  if (Boolean(window.location.search))
    history.replaceState({}, '', '/');
}

if (document.body.classList.contains('preview')) {
  var pdfFile = location.pathname.replace(/\/$/, '').split('/').pop() + '.pdf';

  function renderSvgPages(pdf) {
    var pages = [];
    for (var i = 1; i <= pdf.numPages; i++) pages.push(i);
    pages.reduce(function(chain, pageNum) {
      return chain.then(function() {
        return pdf.getPage(pageNum).then(function(page) {
          var displayWidth = Math.min(720, window.innerWidth * 0.96);
          var scale = displayWidth / page.getViewport({ scale: 1 }).width;
          var viewport = page.getViewport({ scale: scale });
          return page.getOperatorList().then(function(opList) {
            var svgGfx = new pdfjsLib.SVGGraphics(page.commonObjs, page.objs);
            return svgGfx.getSVG(opList, viewport);
          }).then(function(svg) {
            svg.removeAttribute('width');
            svg.removeAttribute('height');
            svg.style.width = displayWidth + 'px';
            document.body.appendChild(svg);
          });
        });
      });
    }, Promise.resolve());
  }

  function renderHiDpiPages(pdf) {
    var pages = [];
    for (var i = 1; i <= pdf.numPages; i++) pages.push(i);
    pages.reduce(function(chain, pageNum) {
      return chain.then(function() {
        return pdf.getPage(pageNum).then(function(page) {
          var dpr = window.devicePixelRatio || 1;
          var displayWidth = Math.min(720, window.innerWidth * 0.96);
          var scale = displayWidth / page.getViewport({ scale: 1 }).width;
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
            var svg = results[1];
            svg.removeAttribute('width');
            svg.removeAttribute('height');
            svg.style.width = displayWidth + 'px';
            var wrapper = document.createElement('div');
            wrapper.style.position = 'relative';
            wrapper.appendChild(svg);
            document.body.appendChild(wrapper);
            var svgRect = svg.getBoundingClientRect();
            var scaleX = hiDpiViewport.width / svgRect.width;
            var scaleY = hiDpiViewport.height / svgRect.height;
            var images = svg.querySelectorAll('image');
            Array.prototype.forEach.call(images, function(img) {
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
      });
    }, Promise.resolve());
  }

  var hasImages = pdfFile === 'recommendations.pdf';
  pdfjsLib.getDocument('/' + pdfFile).promise.then(hasImages ? renderHiDpiPages : renderSvgPages);
}
