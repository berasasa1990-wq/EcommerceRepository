(function () {
  'use strict';
  var frame = document.getElementById('settingsContent');
  if (!frame) return;
  var rows = Array.from(document.querySelectorAll('[data-section]'));
  function activate(row) {
    rows.forEach(function (item) { item.classList.toggle('is-active', item === row); });
    frame.title = row.querySelector('[data-section-link]').textContent.trim();
    var url = new URL(frame.dataset.workspaceUrl, window.location.origin);
    url.searchParams.set('sekcija', row.dataset.section);
    window.history.replaceState({}, '', url.pathname + url.search);
  }
  rows.forEach(function (row) {
    row.querySelectorAll('a').forEach(function (link) {
      link.addEventListener('click', function (event) {
        // Native same-origin frame navigation preserves form unload confirmation.
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        activate(row);
      });
    });
  });
  frame.addEventListener('load', function () {
    var path;
    try { path = frame.contentWindow.location.pathname; } catch (error) { return; }
    var row = rows.find(function (item) {
      var url = new URL(item.querySelector('[data-section-link]').href);
      return path.startsWith(url.pathname);
    });
    if (row) activate(row);
  });
})();
