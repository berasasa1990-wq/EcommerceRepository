(() => {
    const key = 'shop-favorite-products';
    let favorites = {};
    try {
        const saved = JSON.parse(localStorage.getItem(key) || '{}');
        if (saved && typeof saved === 'object' && !Array.isArray(saved)) favorites = saved;
    } catch (_) { /* Storage may be unavailable; keep this session usable. */ }
    const dialog = document.getElementById('mobileFavorites');
    const popover = document.getElementById('wishlistPopover');
    const headerHeart = document.querySelector('.reference-header-heart');
    const wishlistMenu = document.querySelector('.wishlist-menu');
    const trashSvg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16M9 7V5h6v2M8 7l1 13h6l1-13"/></svg>';
    function isDesktopWishlist() {
        return window.matchMedia('(min-width: 1025px)').matches;
    }
    function favoriteItems() {
        return Object.values(favorites).filter(item => item && typeof item.url === 'string' && typeof item.name === 'string');
    }
    function favoriteCount() {
        return favoriteItems().length;
    }
    function saveFavorites() {
        try { localStorage.setItem(key, JSON.stringify(favorites)); } catch (_) {}
    }
    function formatPrice(value) {
        if (value == null || value === '') return '';
        const n = Number(String(value).replace(',', '.').replace(/[^\d.-]/g, ''));
        if (!Number.isFinite(n)) return String(value).includes('KM') ? String(value) : `${value} KM`;
        return `${n.toLocaleString('bs-BA', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} KM`;
    }
    function cardFavoriteInfo(el) {
        const card = el?.closest?.('[data-product-card]') || (el?.matches?.('[data-product-card]') ? el : null);
        if (!card) return null;
        const id = card.dataset.productId;
        if (!id) return null;
        const link = card.querySelector('[data-product-name]');
        const detail = card.querySelector('#productDetailInfo');
        const name = (detail?.dataset.productName || link?.textContent || card.dataset.defaultName || '').trim();
        const url = detail ? location.pathname : (link?.getAttribute('href') || card.querySelector('a[href*="/artikal/"]')?.getAttribute('href') || '');
        if (!name || !url) return null;
        const img = card.querySelector('img[data-main-image], img');
        const image = img && img.tagName === 'IMG' ? (img.currentSrc || img.src || '') : '';
        const price = card.dataset.defaultPrice || card.dataset.contentValue || '';
        const category = (card.dataset.category || card.querySelector('.home-card-brand, .pd-reference-brand')?.textContent || '').trim();
        const inStock = card.dataset.inStock === '1' || Boolean(card.querySelector('.product-stock-label--in'));
        return { id, name, url, image, price, category, inStock };
    }
    function pulseWishlistHeader() {
        const header = document.querySelector('.reference-header-heart');
        if (!header) return;
        header.classList.remove('wishlist-btn--pulse-once');
        void header.offsetWidth;
        header.classList.add('wishlist-btn--pulse-once');
        const svg = header.querySelector('svg');
        if (!svg) return;
        const clearPulse = (event) => {
            if (event.animationName && event.animationName !== 'cart-icon-add-pulse') return;
            header.classList.remove('wishlist-btn--pulse-once');
            svg.removeEventListener('animationend', clearPulse);
        };
        svg.addEventListener('animationend', clearPulse);
    }
    function syncWishlistUi() {
        const count = favoriteCount();
        document.querySelectorAll('.home-card-heart').forEach(heart => {
            const info = cardFavoriteInfo(heart);
            if (!info) return;
            heart.dataset.favoriteId = info.id;
            heart.setAttribute('aria-pressed', String(Boolean(favorites[info.id])));
        });
        document.querySelectorAll('[data-account-favorites-count]').forEach(node => { node.textContent = String(count); });
        document.querySelectorAll('.wishlist-badge').forEach(node => { node.textContent = String(count); });
        document.querySelectorAll('[data-mobile-favorites-count]').forEach(node => {
            node.textContent = String(count);
            node.hidden = count === 0;
        });
        document.querySelectorAll('[data-wishlist-title-count]').forEach(node => { node.textContent = String(count); });
        document.querySelectorAll('[data-mobile-favorites]').forEach(node => {
            node.dataset.wishlistCount = String(count);
            node.classList.toggle('wishlist-btn--has-items', count > 0);
            if (node.matches('.reference-header-heart')) {
                node.setAttribute('aria-label', `Lista želja — ${count}`);
            }
        });
    }
    function persistFavorite(info) {
        const prev = favorites[info.id] || {};
        favorites[info.id] = {
            name: info.name,
            url: info.url,
            image: info.image || prev.image || '',
            price: info.price || prev.price || '',
            category: info.category || prev.category || '',
            inStock: typeof info.inStock === 'boolean' ? info.inStock : prev.inStock,
        };
    }
    function toggleFavorite(heart) {
        const info = cardFavoriteInfo(heart);
        if (!info) return;
        const adding = !favorites[info.id];
        if (adding) persistFavorite(info);
        else delete favorites[info.id];
        saveFavorites();
        syncWishlistUi();
        renderFavorites();
        if (adding) pulseWishlistHeader();
    }
    function ensureHeartButton(heart) {
        if (heart.tagName === 'BUTTON') return heart;
        const info = cardFavoriteInfo(heart);
        const button = document.createElement('button');
        button.type = 'button';
        button.className = heart.className;
        button.innerHTML = heart.innerHTML;
        button.setAttribute('aria-label', info ? `Omiljeno — ${info.name}` : 'Omiljeno');
        if (info) button.dataset.favoriteId = info.id;
        heart.replaceWith(button);
        return button;
    }
    document.querySelectorAll('.home-card-heart').forEach(ensureHeartButton);
    document.querySelectorAll('[data-product-card]').forEach(card => {
        const info = cardFavoriteInfo(card);
        if (info && favorites[info.id]) persistFavorite(info);
    });
    saveFavorites();
    syncWishlistUi();
    function renderFavoriteRow(item) {
        let url;
        try { url = new URL(item.url, location.origin); } catch (_) { return null; }
        if (url.origin !== location.origin || !['http:', 'https:'].includes(url.protocol)) return null;
        const row = document.createElement('article');
        row.className = 'wishlist-item';
        const media = document.createElement('a');
        media.className = 'wishlist-item__media';
        media.href = url.href;
        if (item.image) {
            const img = document.createElement('img');
            img.src = item.image;
            img.alt = item.name;
            media.append(img);
        } else {
            media.classList.add('wishlist-item__media--empty');
        }
        const info = document.createElement('div');
        info.className = 'wishlist-item__info';
        const name = document.createElement('a');
        name.className = 'wishlist-item__name';
        name.href = url.href;
        name.textContent = item.name;
        info.append(name);
        if (item.category) {
            const cat = document.createElement('span');
            cat.className = 'wishlist-item__cat';
            cat.textContent = item.category;
            info.append(cat);
        }
        if (item.price) {
            const price = document.createElement('span');
            price.className = 'wishlist-item__price';
            price.textContent = formatPrice(item.price);
            info.append(price);
        }
        const stock = document.createElement('span');
        stock.className = 'wishlist-item__stock' + (item.inStock === false ? ' is-out' : '');
        stock.textContent = item.inStock === false ? 'Nije na stanju' : 'Na stanju';
        info.append(stock);
        const remove = document.createElement('button');
        remove.type = 'button';
        remove.className = 'wishlist-item__remove';
        remove.dataset.wishlistRemove = Object.keys(favorites).find(id => favorites[id] === item) || '';
        remove.setAttribute('aria-label', `Ukloni ${item.name}`);
        remove.innerHTML = trashSvg;
        row.append(media, info, remove);
        return row;
    }
    function favoriteIdForItem(item) {
        return Object.keys(favorites).find(id => favorites[id] === item) || '';
    }
    function renderFavorites() {
        syncWishlistUi();
        const items = favoriteItems();
        document.querySelectorAll('[data-favorites-list]').forEach(list => {
            list.replaceChildren();
            items.forEach(item => {
                const row = renderFavoriteRow(item);
                if (!row) return;
                const remove = row.querySelector('[data-wishlist-remove]');
                if (remove) remove.dataset.wishlistRemove = favoriteIdForItem(item);
                list.append(row);
            });
            if (!list.children.length) {
                const empty = document.createElement('p');
                empty.className = 'wishlist-item--empty';
                empty.textContent = 'Još nema omiljenih proizvoda. Označite srce na proizvodu.';
                list.append(empty);
            }
        });
    }
    function setDockWishlistOpen(open) {
        document.querySelectorAll('.mobile-dock-favorites').forEach(node => {
            node.classList.toggle('is-open', open);
        });
    }
    function closeWishlistPopover() {
        if (popover) popover.hidden = true;
        wishlistMenu?.classList.remove('is-open');
        headerHeart?.setAttribute('aria-expanded', 'false');
    }
    function closeMobileWishlist() {
        if (dialog?.open) dialog.close();
        setDockWishlistOpen(false);
        headerHeart?.setAttribute('aria-expanded', 'false');
    }
    function openWishlistPopover() {
        renderFavorites();
        if (!popover) return;
        popover.hidden = false;
        wishlistMenu?.classList.add('is-open');
        headerHeart?.setAttribute('aria-expanded', 'true');
    }
    function openMobileWishlist() {
        renderFavorites();
        closeWishlistPopover();
        if (!dialog) return;
        if (!dialog.open) dialog.showModal();
        setDockWishlistOpen(true);
        headerHeart?.setAttribute('aria-expanded', 'true');
    }
    function toggleWishlistFrom(trigger) {
        renderFavorites();
        if (isDesktopWishlist()) {
            closeMobileWishlist();
            if (popover && !popover.hidden) closeWishlistPopover();
            else openWishlistPopover();
            return;
        }
        closeWishlistPopover();
        if (dialog?.open) closeMobileWishlist();
        else openMobileWishlist();
    }
    renderFavorites();
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
    window.matchMedia('(max-width: 1024px)').addEventListener('change', event => { if (!event.matches) closeCategories(); });
    document.addEventListener('click', event => {
        const heart = event.target.closest('.home-card-heart');
        if (!heart) return;
        event.preventDefault();
        event.stopPropagation();
        toggleFavorite(ensureHeartButton(heart));
    }, true);
    document.addEventListener('click', event => {
        const removeBtn = event.target.closest('[data-wishlist-remove]');
        if (removeBtn) {
            event.preventDefault();
            event.stopPropagation();
            const id = removeBtn.dataset.wishlistRemove;
            if (id && favorites[id]) {
                delete favorites[id];
                saveFavorites();
                syncWishlistUi();
                renderFavorites();
            }
            return;
        }
        if (event.target.closest('#wishlistPopover, .mobile-favorites')) return;
        if (event.target.closest('[data-mobile-home-menu]')) openCategories();
        if (event.target.closest('[data-mobile-home-search]')) {
            closeCategories();
            if (window.openMobileSearch && window.matchMedia('(max-width: 1024px)').matches) { window.openMobileSearch(); return; }
            window.scrollTo({ top: 0, behavior: 'smooth' });
            document.getElementById('header')?.classList.add('header-search-open');
            document.getElementById('searchInput')?.focus({ preventScroll: true });
        }
        const wishTrigger = event.target.closest('[data-mobile-favorites]');
        if (wishTrigger) {
            event.preventDefault();
            closeCategories();
            toggleWishlistFrom(wishTrigger);
            return;
        }
        closeWishlistPopover();
    });
    dialog?.addEventListener('click', event => {
        if (event.target === dialog) closeMobileWishlist();
    });
    dialog?.addEventListener('close', () => setDockWishlistOpen(false));
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') {
            closeWishlistPopover();
            closeMobileWishlist();
        }
    });
    window.matchMedia('(min-width: 1025px)').addEventListener('change', event => {
        if (event.matches) closeMobileWishlist();
        else closeWishlistPopover();
    });
})();

// Mirror the header count for all cart flows, including product detail and bundles.
(() => {
    const headerCart = document.querySelector('.cart-btn');
    const badge = document.querySelector('[data-mobile-cart-count]');
    if (!headerCart || !badge) return;
    function syncCount() {
        const count = Math.max(0, parseInt(headerCart.dataset.cartCount || '0', 10) || 0);
        badge.textContent = String(count);
        badge.hidden = count === 0;
        badge.parentElement.setAttribute('aria-label', `Korpa — ${count} artikala`);
    }
    new MutationObserver(syncCount).observe(headerCart, {
        attributes: true, attributeFilter: ['data-cart-count']
    });
    window.addEventListener('pageshow', syncCount);
    syncCount();
})();
