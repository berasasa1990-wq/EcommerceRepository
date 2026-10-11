(function () {
    'use strict';
    const form = document.getElementById('pnCustomerForm');
    if (!form) return;
    const search = document.getElementById('pnCustomerSearch');
    const results = document.getElementById('pnCustomerResults');
    const addCustomer = document.getElementById('pnNewCustomer');
    const status = document.getElementById('pnCustomerStatus');
    const mode = document.getElementById('pnCustomerMode');
    const id = document.getElementById('pnCustomerId');
    const fields = ['ime_prezime', 'telefon', 'email', 'adresa', 'grad'];
    const selected = document.getElementById('pnCustomerSelected');
    const searchWrap = document.getElementById('pnCustomerSearchWrap');
    async function readJson(response) {
        if (!(response.headers.get('content-type') || '').includes('application/json')) {
            if (response.redirected) throw new Error('Prijava je istekla. Osvježi stranicu i prijavi se ponovo.');
            throw new Error('Server nije završio zahtjev. Pokušaj ponovo ili osvježi stranicu.');
        }
        return response.json();
    }
    let saving = false;
    let timer, controller, version = 0;
    function fill(customer) {
        id.value = customer.id;
        fields.concat(['postanski_broj']).forEach(name => { form.elements[name].value = customer[name] || ''; });
        ['vp_kupac', 'odbio_posiljku'].forEach(name => { form.elements[name].checked = !!customer[name]; });
    }
    async function save(customer) {
        if (saving) return;
        saving = true;
        status.textContent = 'Spremanje kupca…';
        const data = new FormData(form);
        if (customer) {
            data.set('customer_id', customer.id);
            data.set('customer_mode', '');
        }
        try {
            const response = await fetch(form.getAttribute('action') || window.location.href, {method: 'POST', body: data, headers: {'X-Requested-With': 'XMLHttpRequest'}});
            const payload = await readJson(response);
            if (!response.ok || !payload.ok) throw new Error(payload.error || 'Kupac nije sačuvan.');
            if (payload.customer) fill(payload.customer);
            document.getElementById('pnCustomerName').textContent = form.elements.ime_prezime.value;
            form.hidden = true;
            selected.hidden = false;
            searchWrap.hidden = true;
            addCustomer.hidden = true;
            results.replaceChildren();
            status.textContent = '';
            document.getElementById('pnQuery').focus();
        } catch (error) {
            status.textContent = error.message || 'Spremanje nije uspjelo. Pokušaj ponovo.';
        } finally { saving = false; }
    }
    form.addEventListener('submit', function (event) { event.preventDefault(); save(); });
    document.getElementById('pnEditCustomer').addEventListener('click', function () {
        setMode(false);
        mode.value = id.value ? 'edit' : '';
        document.getElementById('pnNewFields').hidden = !id.value;
        fields.forEach(name => { form.elements[name].readOnly = false; });
        form.elements.ime_prezime.focus();
    });
    document.getElementById('pnChangeCustomer').addEventListener('click', function () {
        searchWrap.hidden = false;
        form.hidden = true;
        search.value = '';
        search.focus();
    });
    function setMode(isNew) {
        form.hidden = false;
        mode.value = isNew ? 'new' : '';
        document.getElementById('pnNewFields').hidden = !isNew;
        ['ime_prezime', 'telefon', 'postanski_broj'].forEach(name => { form.elements[name].required = isNew; });
        fields.forEach(name => { form.elements[name].readOnly = !isNew && !!id.value; });
    }
    addCustomer.addEventListener('click', function () {
        addCustomer.hidden = true;
        version++;
        clearTimeout(timer);
        if (controller) controller.abort();
        id.value = '';
        search.value = '';
        results.replaceChildren();
        fields.concat(['postanski_broj']).forEach(name => { form.elements[name].value = ''; });
        ['vp_kupac', 'odbio_posiljku'].forEach(name => { form.elements[name].checked = false; });
        setMode(true);
        status.textContent = 'Novi kupac će biti sačuvan u Kupcima i dodan na ponudu.';
        form.elements.ime_prezime.focus();
    });
    search.addEventListener('input', function () {
        addCustomer.hidden = true;
        const current = ++version;
        clearTimeout(timer);
        if (controller) controller.abort();
        results.replaceChildren();
        const q = search.value.trim();
        if (!q) return;
        timer = setTimeout(async function () {
            controller = new AbortController();
            try {
                const response = await fetch(search.dataset.url + '?q=' + encodeURIComponent(q), {signal: controller.signal});
                if (!response.ok) throw new Error('search');
                const data = await readJson(response);
                if (current !== version) return;
                const rows = data.results || [];
                addCustomer.hidden = rows.length !== 0;
                if (!rows.length) results.textContent = 'Kupac nije pronađen. Klikni + Dodaj kupca.';
                rows.forEach(customer => {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'mg-btn';
                    button.style.cssText = 'display:block;width:100%;text-align:left;white-space:normal;min-height:44px;margin-top:6px';
                    button.textContent = [customer.ime_prezime, customer.telefon, customer.grad, customer.vp_kupac ? 'VP kupac' : ''].filter(Boolean).join(' · ');
                    button.addEventListener('click', function () {
                        save(customer);
                    });
                    results.appendChild(button);
                });
            } catch (error) {
                if (current === version && error.name !== 'AbortError') results.textContent = 'Pretraga nije uspjela. Pokušaj ponovo.';
            }
        }, 180);
    });
})();
