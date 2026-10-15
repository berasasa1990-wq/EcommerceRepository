(function () {
    var form = document.getElementById('bulkPrepareForm');
    if (!form) return;
    var names = document.getElementById('bulkNames');
    var saveForm = document.getElementById('bulkSaveForm');
    var saveButton = document.getElementById('bulkSaveButton');
    var rows = document.getElementById('bulkPreviewRows');
    var error = document.getElementById('bulkError');
    var status = document.getElementById('bulkPreviewStatus');
    var brands = JSON.parse(document.getElementById('bulkBrands').textContent);
    var timer;
    var requestNumber = 0;
    async function preview() {
        var current = ++requestNumber;
        saveButton.disabled = true;
        if (!names.value.trim()) { saveForm.hidden = true; status.textContent = ''; return; }
        status.textContent = 'Prepoznavanje brendova…';
        error.textContent = '';
        try {
            var response = await fetch(form.getAttribute('action') || window.location.href, {
                method: 'POST', body: new FormData(form), credentials: 'same-origin',
                headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' }
            });
            if (current !== requestNumber) return;
            if (response.redirected) {
                throw new Error('Sesija je istekla. Osvježi stranicu i prijavi se ponovo.');
            }
            var contentType = response.headers.get('Content-Type') || '';
            if (!contentType.toLowerCase().includes('application/json')) {
                if (response.status === 403) {
                    throw new Error('Zahtjev nije odobren. Osvježi stranicu i pokušaj ponovo.');
                }
                throw new Error('Server nije vratio pregled artikala (HTTP ' + response.status + '). Pokušaj ponovo.');
            }
            var data = await response.json();
            if (current !== requestNumber) return;
            if (!response.ok || data.error) throw new Error(data.error || 'Pregled nije pripremljen. Pokušaj ponovo.');
            rows.replaceChildren();
            data.rows.forEach(function (row, index) {
                var tr = document.createElement('tr');
                var nameCell = document.createElement('td');
                var nameField = document.createElement('input');
                nameField.name = 'product_name'; nameField.value = row.naziv;
                nameField.maxLength = 200; nameField.required = true;
                nameField.setAttribute('aria-label', 'Naziv artikla ' + (index + 1));
                nameCell.appendChild(nameField);
                var brandCell = document.createElement('td');
                var select = document.createElement('select');
                select.name = 'brand_id'; select.required = true;
                select.setAttribute('aria-label', 'Brend artikla ' + (index + 1));
                select.add(new Option('Odaberi brend', ''));
                brands.forEach(function (brand) { select.add(new Option(brand.naziv, brand.id)); });
                select.value = String(row.brand_id || '');
                brandCell.appendChild(select); tr.append(nameCell, brandCell); rows.appendChild(tr);
            });
            document.getElementById('bulkSourceText').value = names.value;
            saveForm.hidden = data.rows.length === 0;
            saveButton.disabled = false;
            status.textContent = 'Pripremljeno artikala: ' + data.rows.length + '.';
        } catch (err) {
            if (current !== requestNumber) return;
            error.textContent = err.message || 'Greška pri pripremi pregleda.';
            saveForm.hidden = true; status.textContent = '';
        }
    }
    form.addEventListener('submit', function (event) { event.preventDefault(); clearTimeout(timer); preview(); });
    names.addEventListener('input', function () {
        clearTimeout(timer); ++requestNumber; saveButton.disabled = true;
        timer = setTimeout(preview, 500);
    });
    saveForm.addEventListener('submit', function () { saveButton.disabled = true; saveButton.textContent = 'Dodavanje…'; });
})();
