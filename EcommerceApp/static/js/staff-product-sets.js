(function () {
  'use strict';
  var form = document.getElementById('productSetForm');
  if (!form) return;
  var rows = JSON.parse(document.getElementById('setSavedComponents').textContent);
  var body = document.getElementById('setComponentRows'), search = document.getElementById('setSearch');
  var results = document.getElementById('setSearchResults'), error = document.getElementById('setError');
  var timer, controller, version = 0;
  function updatePrice() {
    var total = rows.reduce(function (sum, row) {
      return sum + Math.round(Number(row.unit_price) * 100) * (Number(row.quantity) || 0);
    }, 0);
    document.getElementById('setRegular').value = (total / 100).toFixed(2);
  }
  function render() {
    body.replaceChildren();
    rows.forEach(function (row, index) {
      var tr = document.createElement('tr'), name = document.createElement('td');
      name.textContent = row.label || ('Artikal #' + row.product_id); tr.appendChild(name);
      var qtyCell = document.createElement('td'), qty = document.createElement('input');
      qty.type = 'number'; qty.min = '1'; qty.max = '1000000'; qty.step = '1'; qty.required = true;
      qty.value = row.quantity; qty.setAttribute('aria-label', 'Količina: ' + name.textContent);
      qty.addEventListener('input', function () { row.quantity = qty.value; updatePrice(); }); qtyCell.appendChild(qty); tr.appendChild(qtyCell);
      var cell = document.createElement('td'), remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'mg-btn'; remove.textContent = 'Ukloni';
      remove.addEventListener('click', function () { rows.splice(index, 1); render(); }); cell.appendChild(remove); tr.appendChild(cell);
      body.appendChild(tr);
    });
    updatePrice();
  }
  search.addEventListener('input', function () {
    clearTimeout(timer); var current = ++version;
    if (controller) controller.abort(); results.replaceChildren();
    var q = search.value.trim(); if (!q) return;
    timer = setTimeout(function () {
      controller = new AbortController();
      fetch(search.dataset.url + '?bez_zalihe=1&limit=20&q=' + encodeURIComponent(q), {credentials:'same-origin', signal:controller.signal})
        .then(function (r) { if (!r.ok) throw Error(); return r.json(); })
        .then(function (data) {
          if (version !== current) return;
          (data.results || []).forEach(function (product) {
            if (product.is_set) return;
            (product.varijacije && product.varijacije.length ? product.varijacije : [null]).forEach(function (variation) {
              var label = product.naziv + (variation ? ' — ' + variation.naziv : '');
              var button = document.createElement('button'); button.type = 'button'; button.className = 'mg-btn';
              button.textContent = label + ' · ' + (variation && variation.sifra || product.sifra || '');
              button.addEventListener('click', function () {
                var variationId = variation ? variation.id : '';
                if (rows.some(function (row) { return String(row.product_id) === String(product.id) && String(row.variation_id || '') === String(variationId); })) {
                  error.textContent = 'Artikal je već u setu. Promijeni njegovu količinu.'; return;
                }
                rows.push({product_id:product.id, variation_id:variationId, quantity:1, label:label, unit_price:variation ? variation.regular_price : product.regular_price});
                error.textContent = ''; render(); results.replaceChildren(); search.value = ''; search.focus();
              }); results.appendChild(button);
            });
          });
          if (!results.childElementCount) results.textContent = 'Nema artikala za ovu pretragu.';
        }).catch(function (e) { if (e.name !== 'AbortError' && version === current) results.textContent = 'Pretraga nije uspjela. Pokušaj ponovo.'; });
    }, 200);
  });
  form.addEventListener('submit', function (event) {
    if (!rows.length) { event.preventDefault(); error.textContent = 'Dodaj barem jedan artikal u set.'; return; }
    document.getElementById('setComponentsJson').value = JSON.stringify(rows);
  });
  render();
})();
