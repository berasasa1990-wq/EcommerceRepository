(function () {
    var dialog = document.getElementById('uvozCalculationDialog');
    if (!dialog) return;
    var form = document.getElementById('uvozCalculationForm');
    var hidden = document.getElementById('uvozCalculationJson');
    var saved = document.getElementById('uvozCalculationData');
    var config = {};
    try { config = JSON.parse(saved ? saved.textContent : (hidden && hidden.value || '{}')); } catch (_) {}
    function num(value) {
        var text = String(value == null ? '' : value).trim().replace(/\s/g, '');
        if (text.includes(',')) text = text.replace(/\./g, '').replace(',', '.');
        if (!text) return 0;
        return /^\d+(\.\d+)?$/.test(text) ? Number(text) : NaN;
    }
    function read() {
        var data = {};
        ['jci','faktura','kurs','iznos_fakture','prevoz','carina','pdv','ostali_troskovi'].forEach(function (name) { data[name] = form.elements.namedItem(name).value.trim(); });
        return data;
    }
    function rate(data) { return (num(data.carina) + num(data.prevoz) + num(data.ostali_troskovi)) / num(data.iznos_fakture) * 100; }
    function showRate() {
        var value = rate(read());
        document.getElementById('uvozCostPercent').textContent = Number.isFinite(value) ? value.toFixed(4) + '%' : '—';
    }
    document.querySelectorAll('[data-calculation-open]').forEach(function (button) {
        button.addEventListener('click', function () {
            ['jci','faktura','kurs','iznos_fakture','prevoz','carina','pdv','ostali_troskovi'].forEach(function (name) { form.elements.namedItem(name).value = config[name] || (['prevoz','carina','pdv','ostali_troskovi'].includes(name) ? '0' : ''); });
            document.getElementById('uvozCalculationError').textContent = '';
            showRate(); dialog.showModal();
        });
    });
    form.querySelector('[data-calculation-close]').addEventListener('click', function () { dialog.close(); });
    form.addEventListener('input', showRate);
    form.addEventListener('submit', function (event) {
        var data = read();
        var valid = num(data.kurs) > 0 && num(data.iznos_fakture) > 0 && ['kurs','iznos_fakture','prevoz','carina','pdv','ostali_troskovi'].every(function (key) { return Number.isFinite(num(data[key])) && num(data[key]) >= 0; });
        if (!valid) { event.preventDefault(); document.getElementById('uvozCalculationError').textContent = 'Provjeri brojeve. Kurs i iznos fakture moraju biti veći od nule.'; return; }
        if (form.dataset.savedImport === '1') return;
        event.preventDefault();
        data.trosak_bez_pdv_pct = String(rate(data));
        config = data; hidden.value = JSON.stringify(config);
        document.dispatchEvent(new Event('uvoz-calculation-updated'));
        dialog.close();
    });
    window.warehouseImportCalculation = {
        row: function (row) {
            function fixed(value, digits) { return Number.isFinite(value) ? value.toFixed(digits) : ''; }
            var invoice = row.fakturna === '' ? NaN : num(row.fakturna);
            var qty = row.kolicina === '' ? NaN : num(row.kolicina);
            var mpc = row.mpc_brutto === '' ? NaN : num(row.mpc_brutto);
            var purchase = num(config.kurs) > 0 && Number.isFinite(rate(config)) ? invoice * num(config.kurs) * (1 + rate(config) / 100) : NaN;
            var wholesale = mpc / 1.17 / 1.38;
            row.nabavna = fixed(purchase, 4);
            row.vpc_netto = fixed(wholesale, 4);
            row.vpc_marza = purchase > 0 ? fixed(Number(wholesale.toFixed(4)) / Number(purchase.toFixed(4)) * 100, 6) : '';
            row.ukupno_fakturna = fixed(invoice * qty, 2);
            return row;
        }
    };
})();
