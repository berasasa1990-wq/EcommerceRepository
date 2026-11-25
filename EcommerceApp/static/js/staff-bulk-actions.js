(function () {
    'use strict';
    const el = id => document.getElementById(id);
    const selected = new Set();
    let page = 1, pages = 1, visible = [], timer, sequence = 0, busy = false;
    let wholeStockSelected = false, catalogLoading = false;
    const dialog = el('baDialog');
    function selection() {
        el('baCount').textContent = wholeStockSelected ? 'Odabrano: kompletan lager' : 'Odabrano: ' + selected.size;
        el('baOpenStock').setAttribute('aria-pressed', String(wholeStockSelected));
        el('baOpen').disabled = wholeStockSelected || !selected.size;
        el('baSelect').disabled = wholeStockSelected || catalogLoading || !visible.length;
        el('baCatalog').querySelectorAll('input[type="checkbox"]').forEach(input => {
            input.checked = wholeStockSelected || selected.has(Number(input.value));
            input.disabled = wholeStockSelected;
        });
    }
    async function load() {
        const seq = ++sequence;
        el('baStatus').textContent = 'Učitavanje kataloga…';
        catalogLoading = true;
        selection();
        try {
            const params = new URLSearchParams({catalog: '1', q: el('baSearch').value.trim(), page: String(page)});
            const response = await fetch(location.pathname + '?' + params);
            if (!response.ok) throw new Error();
            const data = await response.json();
            if (seq !== sequence) return;
            page = data.page; pages = data.pages; visible = data.results;
            el('baCatalog').replaceChildren();
            visible.forEach(row => {
                const label = document.createElement('label'); label.className = 'ba-product';
                const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.value = row.id;
                checkbox.setAttribute('aria-label', 'Odaberi ' + row.name);
                checkbox.onchange = () => { if (checkbox.checked) selected.add(row.id); else selected.delete(row.id); selection(); };
                const image = document.createElement('div'); image.className = 'ba-image';
                if (row.image) { const img = document.createElement('img'); img.src = row.image; img.alt = ''; img.loading = 'lazy'; image.append(img); } else image.textContent = 'Bez slike';
                const name = document.createElement('strong'); name.textContent = row.name;
                const meta = document.createElement('span'); meta.textContent = [row.sku, row.price + ' KM'].filter(Boolean).join(' · ');
                label.append(checkbox, image, name, meta); el('baCatalog').append(label);
            });
            el('baStatus').textContent = data.total ? 'Pronađeno artikala: ' + data.total : 'Nema rezultata.';
            el('baPage').textContent = page + ' / ' + pages;
            el('baPrev').disabled = page <= 1; el('baNext').disabled = page >= pages;
        } catch (_) { if (seq === sequence) { visible = []; el('baCatalog').replaceChildren(); el('baStatus').textContent = 'Katalog nije učitan. Pokušaj ponovo pretragom.'; } }
        finally {
            if (seq === sequence) {
                catalogLoading = false;
                selection();
            }
        }
    }
    el('baSearch').oninput = () => { catalogLoading = true; selection(); ++sequence; clearTimeout(timer); page = 1; timer = setTimeout(load, 250); };
    el('baPrev').onclick = () => { page--; load(); };
    el('baNext').onclick = () => { page++; load(); };
    el('baSelect').onclick = () => { visible.forEach(row => selected.add(row.id)); selection(); };
    function clearSelection() {
        wholeStockSelected = false;
        selected.clear();
        selection();
    }
    el('baClear').onclick = clearSelection;
    function openDialog(wholeStock) {
        el('baScope').value = wholeStock ? 'stock' : 'selected';
        el('baStockFields').hidden = el('baStockFields').disabled = !wholeStock;
        el('baSelectedFields').hidden = el('baSelectedFields').disabled = wholeStock;
        el('baSummary').textContent = wholeStock ? 'Kompletan lager' : 'Odabrano artikala: ' + selected.size;
        el('baError').textContent = '';
        dialog.showModal();
    }
    el('baOpenStock').onclick = () => {
        wholeStockSelected = true;
        selection();
        openDialog(true);
    };
    el('baOpen').onclick = () => openDialog(false);
    el('baCancel').onclick = () => dialog.close();
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    function scheduledDateTime(prefix) {
        const date = el(prefix + 'Date').value;
        const hour = String(el(prefix + 'Hour').valueAsNumber).padStart(2, '0');
        const minute = String(el(prefix + 'Minute').valueAsNumber).padStart(2, '0');
        return date + 'T' + hour + ':' + minute;
    }
    el('baForm').onsubmit = async event => {
        event.preventDefault();
        const wholeStock = el('baScope').value === 'stock';
        if (busy || (!wholeStock && !selected.size)) return;
        busy = true; el('baApply').disabled = true; el('baCancel').disabled = true;
        const data = new FormData(el('baForm'));
        if (wholeStock) {
            data.set('starts_at', scheduledDateTime('baStart'));
            data.set('ends_at', scheduledDateTime('baEnd'));
        } else {
            selected.forEach(id => data.append('artikal', id));
        }
        try {
            const response = await fetch(location.pathname, {method: 'POST', body: data});
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || 'Postavljanje nije uspjelo.');
            dialog.close();
            clearSelection();
            el('baSaved').textContent = result.message;
        } catch (error) { el('baError').textContent = error.message || 'Postavljanje nije uspjelo.'; }
        busy = false; el('baApply').disabled = false; el('baCancel').disabled = false;
    };
    load();
})();
