(function () {
    'use strict';
    const root = document.getElementById('bulkPrices');
    if (!root) return;
    const search = document.getElementById('bulkSearch');
    const results = document.getElementById('bulkResults');
    const entry = document.getElementById('bulkEntry');
    const barcode = document.getElementById('bulkBarcode');
    const quantity = document.getElementById('bulkQuantity');
    const status = document.getElementById('bulkStatus');
    const save = document.getElementById('bulkSave');
    const list = document.getElementById('bulkList');
    let picked = null, rows = [], timer, busy = false;
    function render() {
        list.replaceChildren();
        rows.forEach((row, index) => {
            const li = document.createElement('li');
            const text = document.createElement('span');
            text.textContent = [row.name, row.barcode, row.quantity + ' etiketa'].filter(Boolean).join(' · ');
            const remove = document.createElement('button');
            remove.type = 'button'; remove.className = 'mg-btn'; remove.textContent = 'Ukloni';
            remove.disabled = busy;
            remove.onclick = () => { rows.splice(index, 1); render(); };
            li.append(text, remove); list.append(li);
        });
        document.getElementById('bulkCount').textContent = rows.length;
        save.disabled = busy || !rows.length;
    }
    search.addEventListener('input', () => {
        clearTimeout(timer);
        const q = search.value.trim();
        results.replaceChildren();
        if (!q) return;
        timer = setTimeout(async () => {
            try {
                const response = await fetch(root.dataset.lookup + '?bez_zalihe=1&limit=20&q=' + encodeURIComponent(q));
                if (!response.ok) throw new Error();
                const data = await response.json();
                if (search.value.trim() !== q) return;
                results.replaceChildren();
                (data.results || []).forEach(product => {
                    const variations = product.varijacije || [];
                    (variations.length ? variations : [null]).forEach(variation => {
                        const button = document.createElement('button');
                        button.type = 'button'; button.className = 'mg-btn';
                        const name = product.naziv + (variation ? ' — ' + variation.naziv : '');
                        button.textContent = name + ' · ' + ((variation && variation.sifra) || product.sifra || '');
                        button.onclick = () => {
                            picked = {token: String(product.id) + (variation ? ':' + variation.id : ''), name: name, expectedBarcode: (product.barkod || '').trim()};
                            document.getElementById('bulkPicked').textContent = name;
                            entry.hidden = false; barcode.value = ''; quantity.value = '';
                            results.replaceChildren(); status.textContent = ''; quantity.focus();
                        };
                        results.append(button);
                    });
                });
                if (!results.children.length) results.textContent = 'Nema rezultata.';
            } catch (_) { status.textContent = 'Pretraga nije uspjela. Pokušaj ponovo.'; }
        }, 200);
    });
    barcode.addEventListener('mg-scanned', () => quantity.focus());
    entry.addEventListener('submit', event => {
        event.preventDefault();
        if (!picked || busy || !entry.reportValidity()) return;
        if (barcode.value.trim() && barcode.value.trim() !== picked.expectedBarcode) {
            status.textContent = 'Skenirani barkod ne odgovara odabranom artiklu.';
            barcode.focus(); return;
        }
        rows.push({...picked, barcode: barcode.value.trim(), quantity: Number(quantity.value)});
        picked = null; entry.hidden = true; search.value = ''; search.focus();
        status.textContent = ''; render();
    });
    save.addEventListener('click', async () => {
        if (busy || !rows.length) return;
        busy = true; render();
        try {
            const response = await fetch(root.dataset.save, {
                method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRFToken': root.querySelector('[name=csrfmiddlewaretoken]').value},
                body: JSON.stringify({items: rows})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Čuvanje nije uspjelo.');
            rows = []; status.textContent = 'Sačuvana lista #' + data.id + '. Na desktopu otvori Štampaj cijenu.';
        } catch (error) { status.textContent = error.message || 'Čuvanje nije uspjelo. Pokušaj ponovo.'; }
        busy = false; render();
    });
})();
