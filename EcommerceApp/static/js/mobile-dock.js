(() => {
    const key = 'shop-favorite-products';
    let favorites = {};
    try {
        const saved = JSON.parse(localStorage.getItem(key) || '{}');
        if (saved && typeof saved === 'object' && !Array.isArray(saved)) favorites = saved;
    } catch (_) { /* Storage may be unavailable; keep this session usable. */ }
    const dialog = document.getElementById('mobileFavorites');
    function updateAccountFavoritesCount() {
        document.querySelectorAll('[data-account-favorites-count]').forEach(node => { node.textContent = Object.values(favorites).filter(item => item && typeof item.url === 'string' && typeof item.name === 'string').length; });
    }
    updateAccountFavoritesCount();
    function renderFavorites() {
        updateAccountFavoritesCount();
        const list = dialog.querySelector('[data-favorites-list]');
        list.replaceChildren();
        Object.values(favorites).forEach(item => {
            if (!item || typeof item.url !== 'string' || typeof item.name !== 'string') return;
            let url;
            try { url = new URL(item.url, location.origin); } catch (_) { return; }
            if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) return;
            const link = document.createElement('a');
            link.href = url.href;
            link.textContent = item.name;
            list.append(link);
        });
        if (!list.children.length) list.textContent = 'Još nema omiljenih proizvoda. Označite srce na proizvodu.';
    }
    document.querySelectorAll('.home-card-heart').forEach(heart => {
        const card = heart.closest('[data-product-card]');
        const link = card?.querySelector('[data-product-name]');
        const detail = card?.querySelector('#productDetailInfo');
        const name = detail?.dataset.productName || link?.textContent.trim();
        const url = detail ? location.pathname : link?.href;
        if (!card || !name || !url) return;
        const button = document.createElement('button');
        button.type = 'button';
        button.className = heart.className;
        button.innerHTML = heart.innerHTML;
        button.setAttribute('aria-label', `Omiljeno — ${name}`);
        button.dataset.favoriteId = card.dataset.productId;
        button.setAttribute('aria-pressed', String(Boolean(favorites[card.dataset.productId])));
        heart.replaceWith(button);
        button.addEventListener('click', () => {
            const id = card.dataset.productId;
            if (favorites[id]) delete favorites[id];
            else favorites[id] = { name, url };
            try { localStorage.setItem(key, JSON.stringify(favorites)); } catch (_) {}
            document.querySelectorAll('[data-favorite-id]').forEach(other => {
                other.setAttribute('aria-pressed', String(Boolean(favorites[other.dataset.favoriteId])));
            });
        });
    });
    const categories = document.getElementById('mobileCategoriesDialog');
    const dock = document.querySelector('.mobile-home-dock');
    const dockHome = document.createComment('mobile dock position');
    dock?.before(dockHome);
    function restoreDock() {
        if (dock?.parentElement === categories) dockHome.after(dock);
        document.documentElement.classList.remove('categories-dialog-open');
        dock?.querySelector('[data-mobile-home-menu]')?.setAttribute('aria-expanded', 'false');
    }
    function closeCategories() { if (categories?.open) { categories.close(); restoreDock(); } }
    function openCategories() {
        if (!categories || !window.matchMedia('(max-width: 1024px)').matches) return;
        if (categories.open) { closeCategories(); return; }
        if (dock) categories.append(dock);
        categories.showModal();
        document.documentElement.classList.add('categories-dialog-open');
        dock?.querySelector('[data-mobile-home-menu]')?.setAttribute('aria-expanded', 'true');
        categories.querySelector('[data-categories-close]')?.focus();
    }
    categories?.addEventListener('close', restoreDock);
    categories?.addEventListener('cancel', restoreDock);
    categories?.querySelector('[data-categories-close]')?.addEventListener('click', closeCategories);
    categories?.addEventListener('click', event => { if (event.target === categories) { const r=categories.getBoundingClientRect(); if(event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) closeCategories(); } });
    categories?.querySelectorAll('[data-category-entry]').forEach(entry => entry.addEventListener('toggle', () => {
        if (entry.open) categories.querySelectorAll('[data-category-entry]').forEach(other => { if (other !== entry) other.open = false; });
    }));
    const categorySearch = categories?.querySelector('input[type="search"]');
    categorySearch?.addEventListener('input', () => {
        const query = categorySearch.value.trim().toLocaleLowerCase();
        let count = 0;
        categories.querySelectorAll('[data-category-entry]').forEach(entry => {
            entry.hidden = !entry.textContent.toLocaleLowerCase().includes(query);
            if (!entry.hidden) count++;
        });
        categories.querySelector('[data-categories-empty]').hidden = count > 0;
    });
    window.matchMedia('(max-width: 1024px)').addEventListener('change', event => { if (!event.matches) closeCategories(); });
    document.addEventListener('click', event => {
        if (event.target.closest('[data-mobile-home-menu]')) openCategories();
        if (event.target.closest('[data-mobile-home-search]')) {
            closeCategories();
            if (window.openMobileSearch && window.matchMedia('(max-width: 1024px)').matches) { window.openMobileSearch(); return; }
            window.scrollTo({ top: 0, behavior: 'smooth' });
            document.getElementById('header')?.classList.add('header-search-open');
            document.getElementById('searchInput')?.focus({ preventScroll: true });
        }
        if (event.target.closest('[data-mobile-favorites]') && dialog) {
            closeCategories();
            renderFavorites();
            dialog.showModal();
        }
    });
})();
