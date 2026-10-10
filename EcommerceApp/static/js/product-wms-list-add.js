(() => {
 const init = () => {
  const buttons = document.querySelectorAll('[data-list-add-stock]');
  if (!buttons.length) return;
  const dialog = document.createElement('dialog');
  dialog.className = 'product-wms-location-dialog';
  dialog.innerHTML = '<h2>Dodaj količinu</h2><form><input type="hidden" name="action" value="add"><div class="product-wms-add-fields"><label>Količina<input name="quantity" type="number" min="1" step="1" value="1" required></label><label>Lokacija<input name="location_choice" type="text" list="wmsStockLocationChoices" placeholder="Unesite lokaciju" autocomplete="off" required><datalist id="wmsStockLocationChoices"></datalist><input name="location" type="hidden"></label></div><p data-error role="alert"></p><div class="product-wms-location-actions"><button type="button" data-close>Odustani</button><button type="submit" data-add>Dodaj</button></div></form>';
  document.body.append(dialog);
  const form = dialog.querySelector('form');
  const error = dialog.querySelector('[data-error]');
  const submit = dialog.querySelector('[data-add]');
  let url, csrf, locationOptions = [];
  const locationInput = form.elements.location_choice;
  const locationChoices = dialog.querySelector('datalist');
  const resolveLocation = () => {
   const value = locationInput.value.trim().toLocaleLowerCase();
   const match = locationOptions.find(row => row.label.toLocaleLowerCase() === value);
   form.elements.location.value = match ? String(match.id) : '';
   locationInput.setCustomValidity(match ? '' : 'Odaberite lokaciju iz ponuđenih rezultata.');
  };
  locationInput.addEventListener('input', resolveLocation);
  buttons.forEach(button => button.addEventListener('click', async () => {
   button.disabled = true;
   try {
    const response = await fetch(button.dataset.listAddStock, {credentials:'same-origin'});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Učitavanje nije uspjelo.');
    url = button.dataset.listAddStock; csrf = data.csrf;
    form.reset(); error.textContent = '';
    const removing = button.dataset.stockAction === 'remove';
    form.elements.action.value = removing ? 'remove' : 'add';
    dialog.querySelector('h2').textContent = removing ? 'Skini količinu' : 'Dodaj količinu';
    submit.textContent = removing ? 'Skini' : 'Dodaj';
    locationOptions = data.locations.filter(row => !removing || data.stocks.some(stock => stock.lokacija_id === row.id && stock.kolicina > 0)).map(row => {
     const stock = data.stocks.find(stock => stock.lokacija_id === row.id);
     return {id: row.id, label: row.naziv + (removing ? ' · ' + stock.kolicina + ' kom' : '')};
    });
    locationChoices.replaceChildren(...locationOptions.map(row => new Option(row.label, row.label)));
    locationInput.setCustomValidity('');
    dialog.showModal(); form.elements.quantity.focus();
   } catch (exception) { window.alert(exception.message); }
   finally { button.disabled = false; }
  }));
  dialog.querySelector('[data-close]').onclick = () => dialog.close();
  form.addEventListener('submit', async event => {
   event.preventDefault();
   resolveLocation();
   if (!form.reportValidity()) return;
   submit.disabled = true; error.textContent = '';
   try {
    const response = await fetch(url, {method:'POST', credentials:'same-origin', headers:{'X-CSRFToken':csrf}, body:new FormData(form)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || 'Dodavanje nije uspjelo.');
    dialog.close(); window.location.reload();
   } catch (exception) { error.textContent = exception.message; }
   finally { submit.disabled = false; }
  });
 };
 if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();
