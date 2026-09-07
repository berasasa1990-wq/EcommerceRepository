(function () {
  'use strict';
  function open(id) { var dialog = document.getElementById(id); if (dialog && !dialog.open) dialog.showModal(); }
  document.querySelectorAll('[data-ld-open]').forEach(function (button) { button.addEventListener('click', function () { open(button.dataset.ldOpen); }); });
  document.querySelectorAll('[data-ld-close]').forEach(function (button) { button.addEventListener('click', function () { button.closest('dialog').close(); }); });
  var returnLine = document.getElementById('ldReturnLine'), returnQty = document.getElementById('ldReturnQty');
  function limitReturn() { var option = returnLine.selectedOptions[0]; returnQty.max = option.dataset.remaining || '1000000'; returnQty.value = '1'; }
  if (returnLine) returnLine.addEventListener('change', limitReturn);
  document.querySelectorAll('[data-ld-return]').forEach(function (button) { button.addEventListener('click', function () { returnLine.value = button.dataset.ldReturn; limitReturn(); open('ldReturn'); }); });
  var toggle = document.getElementById('ldGoodsToggle'), goods = document.getElementById('ldGoods');
  if (toggle) toggle.addEventListener('change', function () { goods.hidden = goods.disabled = !toggle.checked; document.getElementById('ldAmount').hidden = toggle.checked; });
  var search = document.getElementById('ldProductSearch'), timer, controller, generation = 0;
  if (search) search.addEventListener('input', function () {
    clearTimeout(timer); generation++;
    if (controller) controller.abort();
    var version = generation, results = document.getElementById('ldProductResults');
    results.replaceChildren();
    document.getElementById('ldProductId').value = '';
    document.getElementById('ldVariationId').value = '';
    document.getElementById('ldProductSelected').textContent = '';
    document.getElementById('ldProductLabel').value = '';
    var q = search.value.trim(); if (!q) return;
    timer = setTimeout(function () {
      controller = new AbortController();
      fetch(search.dataset.url + '?bez_zalihe=1&limit=20&q=' + encodeURIComponent(q), {credentials: 'same-origin', signal: controller.signal, headers: {'X-Requested-With': 'XMLHttpRequest'}})
        .then(function (response) { if (!response.ok) throw new Error('lookup'); return response.json(); })
        .then(function (data) {
          if (version !== generation) return;
          var rows = [];
          (data.results || []).forEach(function (p) { var variants = p.varijacije && p.varijacije.length ? p.varijacije : [null]; variants.forEach(function (v) { rows.push({id:p.id, variation:v ? v.id : '', name:p.naziv + (v ? ' — ' + v.naziv : ''), code:v && v.sifra || p.sifra || '', price:v && v.cijena || p.cijena || ''}); }); });
          if (!rows.length) results.textContent = 'Nema rezultata.';
          rows.forEach(function (row) {
            var button = document.createElement('button'); button.type = 'button'; button.textContent = row.name + ' · ' + row.code;
            button.addEventListener('click', function () {
              document.getElementById('ldProductId').value = row.id;
              document.getElementById('ldVariationId').value = row.variation;
              document.getElementById('ldProductSelected').textContent = row.name + ' · ' + row.code;
              document.getElementById('ldProductLabel').value = row.name + ' · ' + row.code;
              document.getElementById('ldPrice').value = row.price;
              results.replaceChildren();
            }); results.appendChild(button);
          });
        }).catch(function (error) { if (version === generation && error.name !== 'AbortError') results.textContent = 'Pretraga nije uspjela. Pokušaj ponovo.'; });
    }, 200);
  });
  var draft = [], draftInput = document.getElementById('ldItemsJson');
  function renderDraft() {
    if (!draftInput) return;
    draftInput.value = JSON.stringify(draft);
    var body = document.getElementById('ldDraftRows'); body.replaceChildren();
    var total = 0;
    draft.forEach(function (row, index) {
      var tr = document.createElement('tr');
      [row.label + ' · ' + row.location_label, row.quantity, row.price, (Number(row.price) * Number(row.quantity)).toFixed(2)].forEach(function (value) {
        var td = document.createElement('td'); td.textContent = value; tr.appendChild(td);
      });
      var td = document.createElement('td'), remove = document.createElement('button');
      remove.type = 'button'; remove.className = 'ld-btn'; remove.textContent = '×'; remove.setAttribute('aria-label', 'Ukloni ' + row.label);
      remove.addEventListener('click', function () { draft.splice(index, 1); renderDraft(); }); td.appendChild(remove); tr.appendChild(td); body.appendChild(tr);
      total += Math.round(Number(row.price) * 100) * Number(row.quantity);
    });
    document.getElementById('ldDraftTotal').textContent = (total / 100).toLocaleString('bs-BA', {minimumFractionDigits:2, maximumFractionDigits:2}) + ' KM';
  }
  var add = document.getElementById('ldAddItem');
  if (add) add.addEventListener('click', function () {
    var location = goods.querySelector('[name=location_id]'), qty = goods.querySelector('[name=quantity]').value;
    var price = document.getElementById('ldPrice').value.trim().replace(',', '.');
    var id = document.getElementById('ldProductId').value, error = document.getElementById('ldDraftError');
    if (!id || !location.value || !/^\d+$/.test(qty) || Number(qty) < 1 || Number(qty) > 1000000 || !/^\d+(\.\d{1,2})?$/.test(price) || Number(price) <= 0) {
      error.textContent = 'Odaberi artikal i lokaciju te unesi količinu i cijenu.'; return;
    }
    if (draft.length >= 100) { error.textContent = 'Najviše 100 stavki po knjiženju.'; return; }
    draft.push({product_id:id, variation_id:document.getElementById('ldVariationId').value, location_id:location.value,
      location_label:location.selectedOptions[0].textContent, quantity:qty, price:price, label:document.getElementById('ldProductLabel').value});
    error.textContent = ''; renderDraft();
    search.value = ''; search.dispatchEvent(new Event('input')); search.focus();
  });
  document.querySelectorAll('.ld-dialog form').forEach(function (form) { form.addEventListener('submit', function (event) { if (form.closest('#ldEntry') && toggle.checked && !draft.length) { event.preventDefault(); document.getElementById('ldDraftError').textContent = 'Prvo dodaj artikal na listu.'; return; } var button = form.querySelector('[type=submit], .ld-submit'); if (button) { button.disabled = true; button.textContent = 'Čuvanje…'; } }); });
  var retry = document.querySelector('[data-ld-reopen]');
  if (retry) {
    var saved = JSON.parse(document.getElementById('ldSubmitted').textContent || '{}');
    Object.keys(saved).forEach(function (name) {
      if (['csrfmiddlewaretoken', 'token', 'action', 'partner_id'].includes(name)) return;
      var field = retry.querySelector('[name="' + name.replace(/[^a-z_]/gi, '') + '"]');
      if (!field) return;
      if (field.type === 'checkbox') field.checked = saved[name] === '1'; else field.value = saved[name];
    });
    if (retry.id === 'ldEntry' && toggle) { toggle.checked = saved.with_goods === '1'; toggle.dispatchEvent(new Event('change')); }
    if (saved.items_json) { try { var parsed = JSON.parse(saved.items_json); if (Array.isArray(parsed)) draft = parsed; } catch (_) {} }
    renderDraft();
    if (toggle && toggle.checked) {
      toggle.dispatchEvent(new Event('change'));
      document.getElementById('ldProductSelected').textContent = saved.product_label || 'Odabrani artikal #' + (saved.product_id || '—');
    }
    open(retry.id);
  }
})();

(function () {
  'use strict';
  var form = document.getElementById('ldMissingForm'); if (!form) return;
  var dialog = document.getElementById('ldMissing'), select = document.getElementById('ldMissingOrder');
  var body = document.getElementById('ldMissingRows'), error = document.getElementById('ldMissingError');
  var version = 0, listVersion = 0, timer, chosen = null, damagedMode = false;
  function loadItems(saved) {
    var current = ++version; chosen = null; body.replaceChildren(); error.textContent = '';
    if (!select.value) return;
    fetch(form.dataset.url + '&order_id=' + encodeURIComponent(select.value), {credentials:'same-origin'})
      .then(function (r) { if (!r.ok) throw Error(); return r.json(); })
      .then(function (data) {
        if (current !== version) return;
        document.getElementById('ldSelectedOrder').textContent = 'Narudžba #' + data.number;
        if (data.cancelled) error.textContent = 'Narudžba je otkazana. Artikli su dostupni za pregled; knjiženje je onemogućeno.';
        data.items.forEach(function (item) {
          var tr = document.createElement('tr');
          [item.code + ' · ' + item.name, item.quantity, item.remaining].forEach(function (text) { var td = document.createElement('td'); td.textContent = text; tr.appendChild(td); });
          var td = document.createElement('td'), input = document.createElement('input');
          input.type = 'number'; input.min = '1'; input.max = '1000000'; input.step = '1'; input.value = saved && saved[item.id] || '1';
          input.disabled = data.cancelled; input.dataset.itemId = item.id; input.setAttribute('aria-label', 'Količina: ' + item.name);
          td.appendChild(input); tr.appendChild(td);
          var actions = document.createElement('td'); actions.className = 'ld-row-actions';
          (damagedMode ? [['damaged', 'OŠTEĆEN ARTIKAL']] : [['missing', 'NIJE DOŠLO'], ['excess', 'VIŠE POSLATO']]).forEach(function (action) {
            var button = document.createElement('button'); button.type = 'button'; button.className = 'ld-btn'; button.textContent = action[1];
            button.disabled = data.cancelled || (action[0] !== 'excess' && item.remaining <= 0);
            button.addEventListener('click', function () {
              if (!input.reportValidity()) return;
              if (action[0] !== 'excess' && Number(input.value) > item.remaining) { error.textContent = 'Za ovaj artikal možeš evidentirati još ' + item.remaining + ' komada za evidenciju oštećenja ili nedostajuće robe.'; return; }
              chosen = {item_id:item.id, quantity:input.value};
              form.querySelector('[name=action]').value = action[0];
              form.requestSubmit();
            }); actions.appendChild(button);
          });
          tr.appendChild(actions); body.appendChild(tr);
        });
      }).catch(function () { if (current === version) error.textContent = 'Učitavanje stavki nije uspjelo.'; });
  }
  function loadOrders(chosen, saved) {
    var current = ++listVersion; version++; body.replaceChildren(); select.replaceChildren(new Option('Odaberi narudžbu', ''));
    var q = document.getElementById('ldMissingSearch').value.trim();
    fetch(form.dataset.url + '&q=' + encodeURIComponent(q), {credentials:'same-origin'})
      .then(function (r) { if (!r.ok) throw Error(); return r.json(); })
      .then(function (data) {
        if (current !== listVersion) return;
        data.orders.forEach(function (order) { select.add(new Option('#' + order.number + ' · ' + order.date + ' · ' + order.status + ' · ' + order.amount + ' KM', order.id)); });
        if (chosen) {
          if (!Array.from(select.options).some(function (o) { return o.value === String(chosen); })) select.add(new Option('Odabrana narudžba', chosen));
          select.value = String(chosen); loadItems(saved);
        }
        if (!data.orders.length && !chosen) error.textContent = 'Nema narudžbi za ovog kupca i pretragu.';
      }).catch(function () { if (current === listVersion) error.textContent = 'Učitavanje narudžbi nije uspjelo.'; });
  }
  function openMissing(chosen, saved, damaged) {
    damagedMode = !!damaged;
    dialog.querySelector('h2').textContent = damagedMode ? 'Oštećen artikal — izaberi narudžbu kupca' : 'Artikli iz narudžbe';
    form.querySelector('p').textContent = damagedMode
      ? 'Izaberi narudžbu ovog kupca, zatim unesi količinu uz oštećeni artikal i klikni OŠTEĆEN ARTIKAL. Evidentira se naša obaveza prema kupcu, bez promjene lagera.'
      : 'Izaberi narudžbu i unesi količinu. NIJE DOŠLO = mi dugujemo kupcu; VIŠE POSLATO = kupac duguje nama. Lager se ne mijenja.';
    var entry = document.getElementById('ldEntry'); if (entry.open) entry.close();
    error.textContent = ''; document.getElementById('ldMissingSearch').value = '';
    var fixed = !!chosen;
    document.getElementById('ldMissingSearchLabel').hidden = fixed;
    document.getElementById('ldMissingOrderLabel').hidden = fixed;
    document.getElementById('ldSelectedOrder').hidden = !fixed;
    document.getElementById('ldSelectedOrder').textContent = 'Učitavanje narudžbe…';
    if (!dialog.open) dialog.showModal();
    if (fixed) {
      listVersion++;
      select.replaceChildren(new Option('Odabrana narudžba', chosen, true, true));
      loadItems(saved);
    } else { loadOrders(); }
  }
  document.querySelectorAll('[data-ld-missing]').forEach(function (button) { button.addEventListener('click', function () { openMissing(button.dataset.ldMissing); }); });
  document.getElementById('ldDamagedOpen').addEventListener('click', function () { openMissing(null, null, true); });
  var kind = document.querySelector('#ldEntry [name=kind]');
  kind.addEventListener('change', function () { if (kind.value === 'missing') { kind.value = 'debit'; openMissing(); } });
  select.addEventListener('change', function () { loadItems(); });
  document.getElementById('ldMissingSearch').addEventListener('input', function () { clearTimeout(timer); listVersion++; version++; body.replaceChildren(); select.value = ''; timer = setTimeout(function () { loadOrders(); }, 200); });
  form.addEventListener('submit', function (event) {
    if (!chosen) { event.preventDefault(); error.textContent = 'Izaberi NIJE DOŠLO ili VIŠE POSLATO uz artikal.'; return; }
    document.getElementById('ldMissingJson').value = JSON.stringify([chosen]);
    body.querySelectorAll('button').forEach(function (button) { button.disabled = true; });
    error.textContent = 'Čuvanje evidencije…';
  });
  if (dialog.hasAttribute('data-ld-reopen')) {
    var submitted = JSON.parse(document.getElementById('ldSubmitted').textContent || '{}'), saved = {};
    try { JSON.parse(submitted.missing_json || '[]').forEach(function (row) { saved[row.item_id] = row.quantity; }); } catch (_) {}
    openMissing(submitted.order_id, saved, submitted.action === 'damaged');
  }
})();
