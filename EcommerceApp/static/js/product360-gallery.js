(() => {
  const open = document.getElementById('product360Open'), root = document.getElementById('product360View');
  if (!open || !root) return;
  const urls = JSON.parse(document.getElementById('product360Frames').textContent), gallery = document.getElementById('mainProductImageWrap');
  let viewer;
  open.addEventListener('click', () => {
    document.querySelectorAll('.product-thumbnail').forEach(thumb => { thumb.classList.remove('active'); thumb.setAttribute('aria-pressed', 'false'); });
    root.hidden = false; gallery.classList.add('is-360'); open.classList.add('active');
    if (!viewer) viewer = window.BeraProduct360(root, urls);
    root.focus({preventScroll: true});
  });
  document.querySelectorAll('.product-thumbnail').forEach(thumb => thumb.addEventListener('click', () => {
    root.hidden = true; gallery.classList.remove('is-360'); open.classList.remove('active'); if (viewer) viewer.stop();
  }, true));
  document.addEventListener('visibilitychange', () => { if (document.hidden && viewer) viewer.stop(); });
  window.addEventListener('pagehide', () => { if (viewer) viewer.stop(); });
})();
