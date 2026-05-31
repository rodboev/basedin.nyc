document.addEventListener('touchstart', function(e) {
  var a = e.target.closest('a');
  if (a) a.classList.add('hover');
}, { passive: true });

document.addEventListener('touchend', function() {
  var els = document.querySelectorAll('a.hover');
  for (var i = 0; i < els.length; i++) els[i].classList.remove('hover');
});

document.addEventListener('touchcancel', function() {
  var els = document.querySelectorAll('a.hover');
  for (var i = 0; i < els.length; i++) els[i].classList.remove('hover');
});
