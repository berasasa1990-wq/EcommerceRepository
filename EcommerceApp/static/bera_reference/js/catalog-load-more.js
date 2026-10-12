(() => {
    document.addEventListener('click', async event => {
        const link = event.target.closest('[data-catalog-load-more]');
        if (!link || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const catalog = link.closest('.product-catalog');
        const grid = catalog?.querySelector('.product-grid--catalog');
        if (!grid) return;
        event.preventDefault();
        if (link.dataset.loading === '1') return;
        link.dataset.loading = '1';
        link.setAttribute('aria-disabled', 'true');
        link.textContent = 'Učitavanje…';
        try {
            const response = await fetch(link.href, {credentials: 'same-origin'});
            if (!response.ok || response.redirected) throw new Error('Invalid response');
            const next = new DOMParser().parseFromString(await response.text(), 'text/html');
            const nextCatalog = next.querySelector('.product-catalog');
            const nextGrid = nextCatalog?.querySelector('.product-grid--catalog');
            const controls = nextCatalog?.querySelector('.catalog-load-more');
            if (!nextGrid || !controls) throw new Error('Missing results');
            const cards = [...nextGrid.children];
            // Rebind interactive buttons; copied server binding markers are never trusted.
            cards.forEach(card => card.querySelectorAll('[data-catalog-add]').forEach(button => delete button.dataset.catalogAddBound));
            grid.append(...cards);
            link.closest('.catalog-load-more').replaceWith(controls);
            document.dispatchEvent(new CustomEvent('catalog:loaded', {detail: {root: grid}}));
        } catch (_) {
            link.textContent = 'Pokušaj ponovo — Prikaži više';
        } finally {
            delete link.dataset.loading;
            link.removeAttribute('aria-disabled');
        }
    });
})();
