(() => {
    document.querySelectorAll('.section-form').forEach(editor => {
    const search = editor.querySelector('[data-role=product-search]');
    if (!search) return;
    const name = editor.querySelector('input[name=naziv]');
    if (name) {
    name.placeholder = 'Npr. Izdvojeni artikli';
    name.addEventListener('blur', () => {
        const value = name.value.trim().toLocaleLowerCase('bs');
        name.value = value.charAt(0).toLocaleUpperCase('bs') + value.slice(1);
    });
    }
    const selected = editor.querySelector('[data-role=selected-products]');
    const results = editor.querySelector('[data-role=product-results]');
    const status = editor.querySelector('[data-role=search-status]');
    const timedOffer = editor.dataset.timedOffer;
    const selectedInputs = () => selected.querySelectorAll(timedOffer ? 'input[name="artikli"]' : 'input:checked');
    const updateCount = () => {
        editor.querySelector('[data-role=selected-count]').textContent = selectedInputs().length;
    };
    selected.addEventListener('change', () => {
        if (timedOffer) return;
        updateCount();
        search.dispatchEvent(new Event('input'));
    });
    if (timedOffer) selected.addEventListener('click', event => {
        const remove = event.target.closest('[data-remove-timed-product]');
        if (!remove) return;
        remove.closest('.home-timed-editor-row').remove();
        updateCount();
        search.dispatchEvent(new Event('input'));
        search.focus();
    });
    updateCount();
    let timer;
    let sequence = 0;
    search.addEventListener('input', () => {
        clearTimeout(timer);
        const current = ++sequence;
        results.replaceChildren();
        status.textContent = '';
        const query = search.value.trim();
        if (!query) return;
        timer = setTimeout(async () => {
            status.textContent = 'Pretraga…';
            try {
                const url = new URL(window.location.href);
                url.searchParams.set('search', '1');
                url.searchParams.set('q', query);
                if (timedOffer) url.searchParams.set('timed', '1');
                const response = await fetch(url, {headers: {'Accept': 'application/json'}});
                if (!response.ok) throw new Error('search');
                const data = await response.json();
                if (current !== sequence) return;
                status.textContent = data.products.length ? 'Klikni artikal da ga dodaš. Prikazano do 30 rezultata.' : 'Nema pronađenih artikala.';
                for (const product of data.products) {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.textContent = product.label;
                    const existing = [...selected.querySelectorAll('input[name="artikli"]')].find(input => input.value === String(product.id));
                    button.disabled = Boolean(existing && (timedOffer || existing.checked));
                    button.addEventListener('click', () => {
                        if (timedOffer) {
                            const row = document.createElement('div');
                            row.className = 'home-timed-editor-row';
                            const id = document.createElement('input');
                            id.type = 'hidden';
                            id.name = 'artikli';
                            id.value = product.id;
                            const title = document.createElement('span');
                            title.textContent = product.label;
                            const percent = document.createElement('input');
                            percent.name = `popust_${product.id}`;
                            percent.inputMode = 'decimal';
                            percent.placeholder = 'Popust (%)';
                            percent.required = true;
                            percent.setAttribute('aria-label', `Popust za ${product.label} (%)`);
                            const discount = document.createElement('div');
                            discount.className = 'home-timed-discount';
                            const unit = document.createElement('span');
                            unit.textContent = '%';
                            unit.setAttribute('aria-hidden', 'true');
                            discount.append(percent, unit);
                            const remove = document.createElement('button');
                            remove.type = 'button';
                            remove.dataset.removeTimedProduct = '';
                            remove.textContent = 'Ukloni';
                            row.append(id, title, discount, remove);
                            selected.append(row);
                            percent.focus();
                        } else if (existing) existing.checked = true;
                        else {
                            const label = document.createElement('label');
                            const input = document.createElement('input');
                            input.type = 'checkbox';
                            input.name = 'artikli';
                            input.value = product.id;
                            input.checked = true;
                            label.append(input, document.createTextNode(` ${product.label}`));
                            selected.append(label);
                        }
                        updateCount();
                        button.disabled = true;
                    });
                    results.append(button);
                }
            } catch (error) {
                if (current === sequence) status.textContent = 'Pretraga nije uspjela. Pokušaj ponovo.';
            }
        }, 250);
    });
    editor.querySelector('.section-close')?.addEventListener('click', () => {
        editor.closest('details').open = false;
    });
    });
    document.querySelectorAll('.delete-section').forEach(form => {
        form.addEventListener('submit', event => {
            if (!window.confirm('Obrisati ovu sekciju početne stranice?')) event.preventDefault();
        });
    });
})();
