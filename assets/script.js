'use strict';

var links = Array.prototype.slice.call(
    document.querySelectorAll('a.preview')
);

links.forEach(function(link) {
    link.addEventListener('touchstart', function() { link.classList.add('hover'); }, { passive: true });
    link.addEventListener('touchend', function() { link.classList.remove('hover'); });
    link.addEventListener('touchcancel', function() { link.classList.remove('hover'); });

    var filename = link.getAttribute('href').replace(/\/$/, '') + '.pdf';

    pdfjsLib.getDocument(filename).promise
    .then(function(pdf) { return pdf.getPage(1); })
    .then(function(page) {
        var scale = parseFloat(window.getComputedStyle(link).width) /
            page.getViewport({ scale: 1 }).width;
        var viewport = page.getViewport({ scale: scale });

        return page.getOperatorList().then(function(opList) {
            var svgGfx = new pdfjsLib.SVGGraphics(page.commonObjs, page.objs);
            return svgGfx.getSVG(opList, viewport);
        });
    })
    .then(function(svg) {
        svg.style.width = '100%';
        svg.style.height = '100%';
        svg.style.borderRadius = 'inherit';
        link.insertBefore(svg, link.firstChild);
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
