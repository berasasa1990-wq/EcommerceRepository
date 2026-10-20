(() => {
    const dialog = document.getElementById('postOrderOffer');
    if (!dialog) return;
    const expiry = Number(dialog.dataset.expires);
    const finish = dialog.querySelector('[data-offer-finish]');
    const status = dialog.querySelector('[data-offer-confirm-status]');
    const cards = [...dialog.querySelectorAll('[data-offer-product]')];
    const pending = new Set();
    let busy = false;
    let confirmedTotalCents = Math.round(Number(dialog.dataset.baseTotal) * 100);
    const money = value => `${Number(value).toLocaleString('bs-BA', {minimumFractionDigits: 2, maximumFractionDigits: 2})} KM`;
    const update = () => {
        const extraCents = [...pending].reduce((total, card) => {
            const variation = card.querySelector('[data-offer-variation]');
            const price = variation ? variation.selectedOptions[0].dataset.price : card.dataset.price;
            return total + Math.round(Number(price) * 100);
        }, 0);
        dialog.querySelector('[data-offer-base]').textContent = money(confirmedTotalCents / 100);
        dialog.querySelector('[data-offer-selected]').hidden = !pending.size;
        const selectedItems = dialog.querySelector('[data-offer-selected-items]');
        selectedItems.replaceChildren();
        pending.forEach(card => {
            const variation = card.querySelector('[data-offer-variation]');
            const row = document.createElement('li');
            const name = document.createElement('span');
            name.textContent = '+ ' + card.querySelector('h3').textContent.trim() + (variation ? ' — ' + variation.selectedOptions[0].textContent : '');
            const price = document.createElement('strong');
            price.textContent = money(variation ? variation.selectedOptions[0].dataset.price : card.dataset.price);
            row.append(name, price);
            selectedItems.append(row);
        });
        dialog.querySelector('[data-offer-total]').textContent = money((confirmedTotalCents + extraCents) / 100);
        finish.disabled = busy || !pending.size || Date.now() >= expiry;
        cards.forEach(card => {
            const button = card.querySelector('[data-offer-add]');
            const select = card.querySelector('[data-offer-variation]');
            const added = button.dataset.added === '1';
            button.disabled = busy || added || Date.now() >= expiry;
            if (select) select.disabled = busy || added || Date.now() >= expiry;
            if (!added) {
                button.textContent = pending.has(card) ? '✓ Odabrano — ukloni' : '+ Dodaj u narudžbu';
                button.setAttribute('aria-pressed', String(pending.has(card)));
            }
        });
    };
    dialog.querySelectorAll('[data-offer-close]').forEach(button => button.addEventListener('click', () => { if (!busy) dialog.close(); }));
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    dialog.addEventListener('close', () => {
        pending.clear();
        status.textContent = '';
        update();
        document.documentElement.classList.remove('post-offer-open');
    });
    cards.forEach(card => {
        const select = card.querySelector('[data-offer-variation]');
        select?.addEventListener('change', () => {
            const option = select.selectedOptions[0];
            card.querySelector('[data-offer-regular]').textContent = money(option.dataset.regular);
            card.querySelector('[data-offer-price]').textContent = money(option.dataset.price);
            update();
        });
        card.querySelector('[data-offer-add]').addEventListener('click', () => {
            if (busy || Date.now() >= expiry) return;
            if (pending.has(card)) pending.delete(card); else pending.add(card);
            status.textContent = '';
            update();
        });
    });
    finish.addEventListener('click', async () => {
        if (busy || !pending.size || Date.now() >= expiry) return;
        const selected = [...pending];
        busy = true;
        update();
        status.textContent = 'Potvrđujemo dodavanje…';
        try {
            const items = selected.map(card => ({line_id: card.querySelector('[data-offer-add]').dataset.offerAdd, variation_id: card.querySelector('[data-offer-variation]')?.value || ''}));
            const response = await fetch(dialog.dataset.url, {
                method: 'POST', headers: {'X-CSRFToken': dialog.querySelector('[name="csrfmiddlewaretoken"]').value},
                body: new URLSearchParams({confirmed: '1', items: JSON.stringify(items)})
            });
            const data = await response.json();
            if (!response.ok) throw new Error(data.error || 'Dodavanje nije uspjelo.');
            selected.forEach(card => {
                const button = card.querySelector('[data-offer-add]');
                button.dataset.added = '1';
                button.textContent = '✓ Dodano u narudžbu';
                const list = document.querySelector('[data-order-items]');
                if (list) {
                    const select = card.querySelector('[data-offer-variation]');
                    const item = document.createElement('li');
                    item.className = 'order-confirmation__item';
                    const media = document.createElement('div');
                    media.className = 'order-confirmation__image';
                    const image = card.querySelector('.post-offer__media img');
                    if (image) media.append(image.cloneNode(true));
                    else media.textContent = '◇';
                    const copy = document.createElement('div');
                    copy.className = 'order-confirmation__copy';
                    const name = document.createElement('h3');
                    name.textContent = `${card.querySelector('h3').textContent.trim()}${select ? ' — ' + select.selectedOptions[0].textContent : ''}`;
                    const quantity = document.createElement('p');
                    quantity.textContent = 'Količina: 1';
                    copy.append(name, quantity);
                    const price = document.createElement('strong');
                    price.className = 'order-confirmation__price';
                    price.textContent = card.querySelector('[data-offer-price]').textContent;
                    item.append(media, copy, price);
                    list.append(item);
                }
            });
            confirmedTotalCents = Math.round(Number(data.total) * 100);
            pending.clear();
            document.querySelectorAll('[data-order-total]').forEach(node => node.textContent = money(data.total));
            busy = false;
            dialog.close();
        } catch (error) {
            status.textContent = error.message || 'Pokušajte ponovo.';
        } finally {
            busy = false;
            update();
        }
    });
    function tick() {
        const remaining = Math.max(0, Math.ceil((expiry - Date.now()) / 1000));
        dialog.querySelector('[data-offer-timer]').textContent = `${String(Math.floor(remaining / 60)).padStart(2, '0')}:${String(remaining % 60).padStart(2, '0')}`;
        if (!remaining) {
            update();
            dialog.querySelector('.post-offer__expired').hidden = false;
            clearInterval(interval);
        }
    }
    const interval = setInterval(tick, 1000);
    tick();
    update();
    if (Date.now() < expiry) { dialog.showModal(); document.documentElement.classList.add('post-offer-open'); }
})();
