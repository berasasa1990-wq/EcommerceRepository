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
    const updateCount = () => {
        editor.querySelector('[data-role=selected-count]').textContent = selected.querySelectorAll('input:checked').length;
    };
    selected.addEventListener('change', () => {
        updateCount();
        search.dispatchEvent(new Event('input'));
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
                    button.disabled = Boolean(existing && existing.checked);
                    button.addEventListener('click', () => {
                        if (existing) existing.checked = true;
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
