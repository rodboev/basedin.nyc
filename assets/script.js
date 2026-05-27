'use strict';

var links = Array.prototype.slice.call(
    document.querySelectorAll('a[href$=".pdf"]')
);

links.forEach(function(link) {
    var filename = link.getAttribute('href');

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
