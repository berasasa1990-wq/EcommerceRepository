(() => {
 const init = () => {
  const quantity = document.getElementById('id_stanje');
  const location = document.getElementById('id_wms_lokacija');
  const allocation = document.getElementById('id_wms_raspored');
  if (!quantity || !location || !allocation) return;
  const locationRow = location.closest('.form-row');
  if (locationRow) locationRow.hidden = true;
  quantity.readOnly = true;
  quantity.classList.add('product-wms-stock-total');
  quantity.hidden = true;
  quantity.style.display = 'none';
  const quantityRow = quantity.closest('.form-row');
  if (quantityRow) {
   quantityRow.querySelectorAll('label[for="id_stanje"], .help').forEach(element => { element.hidden = true; element.style.display = 'none'; });
  }
  const widget = location.closest('.related-widget-wrapper') || location;
  widget.style.display = 'none';
  const display = document.createElement('span');
  display.className = 'product-wms-location-display';
  widget.after(display);
  let rows = [];
  try { rows = JSON.parse(allocation.value || '[]'); } catch (_) {}
  const label = id => Array.from(location.options).find(option => option.value === String(id))?.textContent || '';
  const refresh = () => {
   display.textContent = rows.length ? rows.map(row => label(row.lokacija_id) + ': ' + row.kolicina + ' kom').join(' · ') : 'Lokacija nije odabrana';
  };
  refresh();
  const dialog = document.createElement('dialog');
  dialog.className = 'product-wms-location-dialog';
  dialog.setAttribute('aria-labelledby', 'productWmsAddTitle');
  dialog.innerHTML = '<h2 id="productWmsAddTitle">Dodaj količinu</h2><p>Unesi količinu koju dodaješ i odaberi lokaciju.</p><div class="product-wms-add-fields"><label for="productWmsAddQuantity">Količina<input id="productWmsAddQuantity" type="number" min="1" step="1" value="1" inputmode="numeric"></label><label for="productWmsAddLocation">Lokacija<select id="productWmsAddLocation"></select></label></div><p data-unassigned hidden></p><p data-error role="alert"></p><p data-success role="status" aria-live="polite"></p><div class="product-wms-location-actions"><button type="button" data-add>Dodaj</button></div>';
  document.body.append(dialog);
  const input = dialog.querySelector('input');
  const select = dialog.querySelector('select');
  const error = dialog.querySelector('[data-error]');
  const success = dialog.querySelector('[data-success]');
  const unassigned = () => Math.max(0, Number(quantity.value) - rows.reduce((n, row) => n + row.kolicina, 0));
  const updateNote = () => {
   const note = dialog.querySelector('[data-unassigned]');
   const missing = unassigned();
   note.hidden = !missing;
   note.textContent = 'Postojeća količina bez lokacije neće se dodati odabranoj lokaciji.';
  };
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'product-wms-quantity-add';
  button.textContent = 'Dodaj količinu';
  button.setAttribute('aria-haspopup', 'dialog');
  quantity.after(button);
  button.onclick = () => {
   select.replaceChildren(...Array.from(location.options, option => option.cloneNode(true)));
   select.value = '';
   input.value = '1';
   error.textContent = '';
   success.textContent = '';
   updateNote();
   dialog.showModal();
   input.focus();
   input.select();
  };
  const add = () => {
   const amount = Number(input.value);
   const id = Number(select.value);
   if (!id || !input.checkValidity() || !Number.isSafeInteger(amount) || amount < 1) {
    error.textContent = 'Unesite količinu veću od nule i odaberite lokaciju.';
    return;
   }
   const total = rows.reduce((n, row) => n + Number(row.kolicina), 0) + amount;
   if (!Number.isSafeInteger(total) || total > 2147483647) {
    error.textContent = 'Unesena količina je prevelika.';
    return;
   }
   const row = rows.find(row => Number(row.lokacija_id) === id);
   if (row) row.kolicina = Number(row.kolicina) + amount;
   else rows.push({lokacija_id: id, kolicina: amount});
   allocation.value = JSON.stringify(rows);
   quantity.value = total;
   location.value = String(rows[0].lokacija_id);
   refresh();
   updateNote();
   error.textContent = '';
   success.textContent = 'Dodano ' + amount + ' kom na ' + label(id) + '. Ukupno stanje: ' + total + ' kom. Sačuvajte artikal da potvrdite izmjene.';
   dialog.close();
   button.focus();
  };
  dialog.querySelector('[data-add]').onclick = add;
  dialog.addEventListener('keydown', event => {
   if (event.key === 'Enter' && event.target === input) { event.preventDefault(); add(); }
  });
 };
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
