'use strict';

var links = document.querySelectorAll('a'),
    docs = [];

if (typeof links.forEach !== 'function')
    links = Array.prototype.slice.call(links);

links.forEach(function(link) {
    var parts = link.href.split('/'),
        lastPart = parts.pop() || parts.pop(),
        canvas = document.createElement('canvas');

    canvas.id = lastPart;
    document.body.appendChild(canvas);
    docs.push(lastPart);
});

docs.forEach(function(doc) {
    PDFJS.getDocument(doc)
    .then(function(pdf) {
        return pdf.getPage(1);
    })
    .then(function(page) {
        var selector = 'a[href^="' + doc + '"]',
            el = document.querySelector(selector),
            elStyle = window.getComputedStyle(el),
            scale = parseFloat(elStyle.height) / parseFloat(elStyle.width) * window.devicePixelRatio,
            viewport = page.getViewport(scale),
            canvas = document.getElementById(doc),
            context = canvas.getContext('2d'),
            task = page.render({ canvasContext: context, viewport: viewport });

        canvas.height = viewport.height;
        canvas.width = viewport.width;

        task.promise.then(function() {
            var css = document.createElement('style');
            css.appendChild(document.createTextNode(
                selector + ' { background-image: url(' + canvas.toDataURL() + ') };'
            ));
            document.querySelector('head').appendChild(css);
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
