function stabilizeMobileProductCards() {
    if (window.innerWidth > 768) return;

    document.querySelectorAll('.product-card').forEach((card) => {
        card.style.transform = 'none';
    });

    document.querySelectorAll('.product-image img[data-main-image]').forEach((img) => {
        img.style.opacity = '1';
    });
}

window.addEventListener('pageshow', (event) => {
    if (event.persisted) {
        stabilizeMobileProductCards();
    }
});

document.addEventListener('DOMContentLoaded', () => {
    const header = document.getElementById('header');
    const navToggle = document.getElementById('navToggle');
    const navLinks = document.getElementById('navLinks');
    const searchBtn = document.querySelector('.search-btn');
    const searchOverlay = document.getElementById('searchOverlay');
    const searchClose = document.getElementById('searchClose');

    window.addEventListener('scroll', () => {
        header.classList.toggle('scrolled', window.scrollY > 10);
    });

    const megaMenuPanel = document.getElementById('megaMenuPanel');
    const megaItems = document.querySelectorAll('[data-nav-item].has-mega-menu');
    const megaMenus = document.querySelectorAll('[data-mega-menu]');
    let megaCloseTimer;

    function syncMegaPanelHeight() {
        if (!megaMenuPanel || !megaMenus.length) return;

        if (window.innerWidth <= 1024) {
            megaMenuPanel.style.removeProperty('min-height');
            megaMenuPanel.classList.remove('is-height-synced');
            return;
        }

        const activeMenu = document.querySelector('[data-mega-menu].active');
        const panelWasOpen = header?.classList.contains('mega-active');
        const previousPanelStyles = {
            display: megaMenuPanel.style.display,
            visibility: megaMenuPanel.style.visibility,
            position: megaMenuPanel.style.position,
            left: megaMenuPanel.style.left,
            width: megaMenuPanel.style.width,
        };

        megaMenuPanel.style.display = 'block';
        megaMenuPanel.style.visibility = 'hidden';
        megaMenuPanel.style.position = 'absolute';
        megaMenuPanel.style.left = '-9999px';
        megaMenuPanel.style.width = `${header?.offsetWidth || megaMenuPanel.offsetWidth}px`;

        let maxContentHeight = 0;

        megaMenus.forEach((menu) => {
            menu.classList.add('active');
            const content = menu.querySelector('.mega-menu-subcategories');
            if (content) {
                maxContentHeight = Math.max(maxContentHeight, content.getBoundingClientRect().height);
            }
            menu.classList.remove('active');
        });

        if (activeMenu) {
            activeMenu.classList.add('active');
        }

        megaMenuPanel.style.display = previousPanelStyles.display;
        megaMenuPanel.style.visibility = previousPanelStyles.visibility;
        megaMenuPanel.style.position = previousPanelStyles.position;
        megaMenuPanel.style.left = previousPanelStyles.left;
        megaMenuPanel.style.width = previousPanelStyles.width;

        if (!panelWasOpen) {
            header?.classList.remove('mega-active');
        }

        const panelStyle = getComputedStyle(megaMenuPanel);
        const verticalPadding = parseFloat(panelStyle.paddingTop) + parseFloat(panelStyle.paddingBottom);
        megaMenuPanel.style.minHeight = `${Math.ceil(maxContentHeight + verticalPadding)}px`;
        megaMenuPanel.classList.add('is-height-synced');
    }

    function positionMegaMenu(item, menu) {
        if (!megaMenuPanel || window.innerWidth <= 1024) return;
        const inner = menu.querySelector('.mega-menu-inner');
        const link = item.querySelector(':scope > a');
        if (!inner || !link) return;

        const panelRect = megaMenuPanel.getBoundingClientRect();
        const linkRect = link.getBoundingClientRect();
        const offset = Math.max(0, Math.round(linkRect.left - panelRect.left));
        inner.style.paddingLeft = `${offset}px`;
        inner.style.left = '';
    }

    function closeMegaMenu() {
        header?.classList.remove('mega-active');
        megaItems.forEach((el) => el.classList.remove('mega-open'));
        megaMenus.forEach((menu) => menu.classList.remove('active'));
    }

    function setMobileNavOpen(isOpen) {
        navLinks?.classList.toggle('mobile-open', isOpen);
        document.body.classList.toggle('mobile-nav-open', isOpen);
        if (!isOpen) {
            closeMegaMenu();
        }
    }

    navToggle?.addEventListener('click', () => {
        setMobileNavOpen(!navLinks?.classList.contains('mobile-open'));
    });

    function openMegaMenu(item) {
        clearTimeout(megaCloseTimer);
        const targetId = item.dataset.megaTarget;
        const menu = document.getElementById(targetId);
        if (!menu) return;

        megaItems.forEach((el) => el.classList.remove('mega-open'));
        megaMenus.forEach((m) => m.classList.remove('active'));

        item.classList.add('mega-open');
        menu.classList.add('active');
        header?.classList.add('mega-active');
        positionMegaMenu(item, menu);
    }

    function scheduleMegaClose() {
        clearTimeout(megaCloseTimer);
        megaCloseTimer = setTimeout(closeMegaMenu, 180);
    }

    if (window.innerWidth > 1024) {
        syncMegaPanelHeight();

        megaItems.forEach((item) => {
            item.addEventListener('mouseenter', () => openMegaMenu(item));
        });

        document.querySelectorAll('[data-nav-item]:not(.has-mega-menu)').forEach((item) => {
            item.addEventListener('mouseenter', () => {
                clearTimeout(megaCloseTimer);
                closeMegaMenu();
            });
        });

        header?.addEventListener('mouseenter', () => clearTimeout(megaCloseTimer));
        header?.addEventListener('mouseleave', scheduleMegaClose);
    }

    window.addEventListener('load', syncMegaPanelHeight);
    window.addEventListener('resize', () => {
        syncMegaPanelHeight();
        if (window.innerWidth > 1024) {
            const activeItem = document.querySelector('[data-nav-item].mega-open');
            const activeMenu = document.querySelector('[data-mega-menu].active');
            if (activeItem && activeMenu) {
                positionMegaMenu(activeItem, activeMenu);
            }
        }
    });

    megaItems.forEach((item) => {
        const link = item.querySelector(':scope > a');
        const submenu = item.querySelector('.nav-submenu');
        link?.addEventListener('click', (e) => {
            if (window.innerWidth <= 1024 && navLinks?.classList.contains('mobile-open') && submenu) {
                e.preventDefault();
                const isOpen = item.classList.contains('mega-open');
                megaItems.forEach((el) => el.classList.remove('mega-open'));
                if (!isOpen) {
                    item.classList.add('mega-open');
                }
            }
        });
    });

    window.addEventListener('resize', () => {
        if (window.innerWidth > 1024) {
            setMobileNavOpen(false);
        }
    });

    const searchForm = document.getElementById('searchForm');
    const searchInput = document.getElementById('searchInput');
    const searchSuggestions = document.getElementById('searchSuggestions');
    const searchSuggestUrl = '/api/pretraga/';
    let searchDebounceTimer;
    let searchFetchController;

    const placeholderThumbSvg = `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="M21 15l-5-5L5 21"/></svg>`;

    function focusSearchInput() {
        if (!searchInput) return;
        searchInput.focus({ preventScroll: true });
        const end = searchInput.value.length;
        if (typeof searchInput.setSelectionRange === 'function') {
            searchInput.setSelectionRange(end, end);
        }
    }

    function scheduleSearchFocus() {
        focusSearchInput();
        requestAnimationFrame(() => {
            requestAnimationFrame(focusSearchInput);
        });
        window.setTimeout(focusSearchInput, 50);
        window.setTimeout(focusSearchInput, 320);
    }

    function setSuggestionsExpanded(expanded) {
        if (!searchInput) return;
        searchInput.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    }

    function clearSearchSuggestions() {
        if (!searchSuggestions) return;
        searchSuggestions.innerHTML = '';
        searchSuggestions.hidden = true;
        setSuggestionsExpanded(false);
    }

    function renderSearchSuggestions(results, query) {
        if (!searchSuggestions) return;

        if (!query) {
            clearSearchSuggestions();
            return;
        }

        if (!results.length) {
            searchSuggestions.innerHTML = `<p class="search-suggestions-empty">Nema artikala za „${escapeHtml(query)}".</p>`;
            searchSuggestions.hidden = false;
            setSuggestionsExpanded(true);
            return;
        }

        const items = results.map((item) => {
            const thumb = item.image
                ? `<img src="${escapeHtml(item.image)}" alt="" width="48" height="48" loading="lazy" decoding="async">`
                : placeholderThumbSvg;
            const priceClass = item.on_sale ? ' search-suggestion-price--sale' : '';
            return `<a href="${escapeHtml(item.url)}" class="search-suggestion" role="option">
                <span class="search-suggestion-thumb">${thumb}</span>
                <span class="search-suggestion-name">${escapeHtml(item.naziv)}</span>
                <span class="search-suggestion-price${priceClass}">${escapeHtml(item.price)} KM</span>
            </a>`;
        }).join('');

        const base = searchForm?.getAttribute('action') || '/';
        const allResultsUrl = `${base}?q=${encodeURIComponent(query)}#product-showcase`;

        searchSuggestions.innerHTML = `${items}<p class="search-suggestions-footer"><a href="${escapeHtml(allResultsUrl)}">Vidi sve rezultate za „${escapeHtml(query)}"</a></p>`;
        searchSuggestions.hidden = false;
        setSuggestionsExpanded(true);
    }

    function escapeHtml(value) {
        return String(value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    async function fetchSearchSuggestions(query) {
        if (!searchSuggestions) return;

        searchFetchController?.abort();
        searchFetchController = new AbortController();

        searchSuggestions.innerHTML = '<p class="search-suggestions-loading">Pretraga…</p>';
        searchSuggestions.hidden = false;
        setSuggestionsExpanded(true);

        try {
            const response = await fetch(`${searchSuggestUrl}?q=${encodeURIComponent(query)}`, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                signal: searchFetchController.signal,
            });
            if (!response.ok) throw new Error('Search failed');
            const data = await response.json();
            if (searchInput?.value.trim() !== query) return;
            renderSearchSuggestions(data.results || [], data.query || query);
        } catch (error) {
            if (error.name === 'AbortError') return;
            searchSuggestions.innerHTML = '<p class="search-suggestions-empty">Pretraga trenutno nije dostupna.</p>';
            searchSuggestions.hidden = false;
        }
    }

    function queueSearchSuggestions() {
        clearTimeout(searchDebounceTimer);
        const value = searchInput?.value.trim() || '';
        if (!value) {
            clearSearchSuggestions();
            return;
        }
        searchDebounceTimer = window.setTimeout(() => fetchSearchSuggestions(value), 200);
    }

    function openSearchOverlay() {
        if (!searchOverlay) return;
        searchOverlay.classList.add('active');
        scheduleSearchFocus();
        if (searchInput?.value.trim()) {
            queueSearchSuggestions();
        }
    }

    function closeSearchOverlay() {
        searchOverlay?.classList.remove('active');
        clearSearchSuggestions();
        searchFetchController?.abort();
    }

    function navigateToSearch(query) {
        const trimmed = query.trim();
        if (!trimmed || !searchForm) return;
        const base = searchForm.getAttribute('action') || '/';
        const target = `${base}?q=${encodeURIComponent(trimmed)}#product-showcase`;
        if (`${window.location.pathname}${window.location.search}${window.location.hash}` === target) {
            closeSearchOverlay();
            const showcase = document.getElementById('product-showcase');
            showcase?.scrollIntoView({ block: 'start' });
            return;
        }
        window.location.assign(target);
    }

    searchBtn?.addEventListener('mousedown', (e) => {
        e.preventDefault();
    });
    searchBtn?.addEventListener('click', openSearchOverlay);

    searchClose?.addEventListener('click', closeSearchOverlay);

    searchForm?.addEventListener('submit', (e) => {
        e.preventDefault();
        navigateToSearch(searchInput?.value || '');
    });

    searchInput?.addEventListener('input', queueSearchSuggestions);

    const urlParams = new URLSearchParams(window.location.search);
    const activeSearchQuery = urlParams.get('q')?.trim();
    const hasListingFilters = urlParams.has('akcija') ||
        urlParams.has('kategorija') ||
                              urlParams.has('cijena_od') ||
                              urlParams.has('cijena_do') ||
                              urlParams.has('sort') ||
                              urlParams.has('page');

    if (activeSearchQuery) {
        document.body.classList.add('search-results-active');
    }

    if (activeSearchQuery || hasListingFilters) {
        const showcase = document.getElementById('product-showcase');
        if (showcase) {
            requestAnimationFrame(() => {
                showcase.scrollIntoView({ block: 'start' });
            });
        }
    }

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            closeSearchOverlay();
            closeMegaMenu();
            setMobileNavOpen(false);
        }
    });

    const carousel = document.getElementById('heroCarousel');
    if (carousel) {
        const slides = carousel.querySelectorAll('.carousel-slide');
        const dots = carousel.querySelectorAll('.dot');
        const prevBtn = document.getElementById('carouselPrev');
        const nextBtn = document.getElementById('carouselNext');
        let currentIndex = 0;
        let autoplayTimer;

        function goToSlide(index) {
            const total = slides.length;
            currentIndex = ((index % total) + total) % total;
            slides.forEach((slide, i) => slide.classList.toggle('active', i === currentIndex));
            dots.forEach((dot, i) => dot.classList.toggle('active', i === currentIndex));
        }

        function startAutoplay() {
            autoplayTimer = setInterval(() => goToSlide(currentIndex + 1), 5000);
        }

        function resetAutoplay() {
            clearInterval(autoplayTimer);
            startAutoplay();
        }

        prevBtn?.addEventListener('click', () => {
            goToSlide(currentIndex - 1);
            resetAutoplay();
        });

        nextBtn?.addEventListener('click', () => {
            goToSlide(currentIndex + 1);
            resetAutoplay();
        });

        dots.forEach((dot) => {
            dot.addEventListener('click', () => {
                goToSlide(parseInt(dot.dataset.index, 10));
                resetAutoplay();
            });
        });

        let touchStartX = 0;
        carousel.addEventListener('touchstart', (e) => {
            touchStartX = e.touches[0].clientX;
        }, { passive: true });

        carousel.addEventListener('touchend', (e) => {
            const diff = touchStartX - e.changedTouches[0].clientX;
            if (Math.abs(diff) > 50) {
                goToSlide(diff > 0 ? currentIndex + 1 : currentIndex - 1);
                resetAutoplay();
            }
        }, { passive: true });

        startAutoplay();
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function updateCartBadge(count) {
        const cartBtn = document.querySelector('.cart-btn');
        if (!cartBtn) return;
        let badge = cartBtn.querySelector('.cart-badge');
        if (count > 0) {
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'cart-badge';
                cartBtn.appendChild(badge);
            }
            badge.textContent = count;
        } else if (badge) {
            badge.remove();
        }
    }

    function showCartToast(message) {
        let toast = document.querySelector('.cart-toast');
        if (!toast) {
            toast = document.createElement('p');
            toast.className = 'cart-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('cart-toast--visible');
        clearTimeout(showCartToast.timer);
        showCartToast.timer = setTimeout(() => {
            toast.classList.remove('cart-toast--visible');
        }, 2200);
    }

    async function addVariationToCart(slug, variationId) {
        const body = new URLSearchParams({
            variation_id: variationId,
            quantity: '1',
            stay: '1',
        });
        const response = await fetch(`/artikal/${slug}/dodaj/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': getCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: body.toString(),
        });
        const data = await response.json();
        if (!response.ok || !data.ok) {
            throw new Error(data.message || 'Dodavanje u korpu nije uspjelo.');
        }
        updateCartBadge(data.cart_count);
        showCartToast(data.message);
        return data;
    }

    document.querySelectorAll('[data-copy-target]').forEach((button) => {
        button.addEventListener('click', async () => {
            const target = document.getElementById(button.dataset.copyTarget);
            if (!target) return;
            const text = target.textContent.trim();
            try {
                await navigator.clipboard.writeText(text);
                const original = button.textContent;
                button.textContent = 'Kopirano!';
                setTimeout(() => { button.textContent = original; }, 1500);
            } catch {
                button.textContent = 'Greška';
            }
        });
    });

    function buildPriceHtml(price, originalPrice, onSale) {
        const formatted = Number(price).toFixed(2);
        if (onSale === true || onSale === 'true') {
            const original = Number(originalPrice).toFixed(2);
            return `<span class="price-sale">${formatted} KM</span><span class="price-original">${original} KM</span>`;
        }
        return `<span class="price-current">${formatted} KM</span>`;
    }

    const sitePopupOverlay = document.getElementById('sitePopupOverlay');
    if (sitePopupOverlay) {
        const popupId = sitePopupOverlay.dataset.popupId || 'default';
        const lastShownKey = `site_popup_last_shown_${popupId}`;
        const sessionShownKey = `site_popup_shown_session_${popupId}`;
        const COOLDOWN_MS = 30 * 60 * 1000; // 30 minutes

        function shouldShowPopup() {
            const last = parseInt(localStorage.getItem(lastShownKey) || '0', 10);
            const cooldownPassed = !last || (Date.now() - last > COOLDOWN_MS);

            const shownInSession = sessionStorage.getItem(sessionShownKey);

            // Show if cooldown (30min) passed OR this is a new visit (no session mark = after closing site)
            if (cooldownPassed || !shownInSession) {
                return true;
            }
            return false;
        }

        function closePopup() {
            sitePopupOverlay.classList.remove('is-visible');
            sitePopupOverlay.hidden = true;
            document.body.classList.remove('popup-open');

            // Record for 30min cooldown
            localStorage.setItem(lastShownKey, Date.now().toString());
            // Mark session so refresh doesn't immediately re-show
            sessionStorage.setItem(sessionShownKey, '1');
        }

        function openPopup() {
            const popupImage = sitePopupOverlay.querySelector('.site-popup-image');
            if (popupImage && popupImage.dataset.src && !popupImage.getAttribute('src')) {
                popupImage.setAttribute('src', popupImage.dataset.src);
            }
            sitePopupOverlay.hidden = false;
            requestAnimationFrame(() => {
                sitePopupOverlay.classList.add('is-visible');
            });
            document.body.classList.add('popup-open');
            // Mark shown for this session
            sessionStorage.setItem(sessionShownKey, '1');
        }

        if (shouldShowPopup()) {
            setTimeout(openPopup, 600);
        }

        document.getElementById('sitePopupClose')?.addEventListener('click', closePopup);
        sitePopupOverlay.addEventListener('click', (e) => {
            if (e.target === sitePopupOverlay) closePopup();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && sitePopupOverlay.classList.contains('is-visible')) {
                closePopup();
            }
        });
    }

    // Upsell popup — server šalje overlay samo jednom nakon triggera (dodavanje u korpu).
    const upsellOverlay = document.getElementById('upsellPopupOverlay');
    if (upsellOverlay) {
        function closeUpsell() {
            upsellOverlay.classList.remove('is-visible');
            upsellOverlay.hidden = true;
            document.body.classList.remove('popup-open');
        }

        function openUpsell() {
            upsellOverlay.hidden = false;
            requestAnimationFrame(() => {
                upsellOverlay.classList.add('is-visible');
            });
            document.body.classList.add('popup-open');
        }

        setTimeout(openUpsell, 900);

        document.getElementById('upsellPopupClose')?.addEventListener('click', closeUpsell);
        upsellOverlay.addEventListener('click', (e) => {
            if (e.target === upsellOverlay) closeUpsell();
        });
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && upsellOverlay.classList.contains('is-visible')) {
                closeUpsell();
            }
        });
    }

    document.querySelectorAll('[data-product-card]').forEach((card) => {
        const mainImage = card.querySelector('[data-main-image]');
        const nameEl = card.querySelector('[data-product-name]');
        const priceEl = card.querySelector('[data-product-price]');
        const swatches = card.querySelectorAll('.variation-swatch');
        const defaultName = card.dataset.defaultName;
        const cartOnSwatch = card.hasAttribute('data-cart-on-swatch');

        swatches.forEach((swatch) => {
            swatch.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();

                swatches.forEach(s => s.classList.remove('active'));
                swatch.classList.add('active');

                const name = swatch.dataset.name;
                const price = swatch.dataset.price;
                const originalPrice = swatch.dataset.originalPrice;
                const onSale = swatch.dataset.onSale;

                if (mainImage && mainImage.tagName === 'IMG' && swatch.dataset.image) {
                    mainImage.src = swatch.dataset.image;
                }
                if (nameEl && name !== defaultName) {
                    nameEl.textContent = name;
                } else if (nameEl) {
                    nameEl.textContent = defaultName;
                }
                if (priceEl && price) {
                    priceEl.innerHTML = buildPriceHtml(price, originalPrice, onSale);
                }

                if (cartOnSwatch && swatch.dataset.productSlug && swatch.dataset.variationId) {
                    addVariationToCart(swatch.dataset.productSlug, swatch.dataset.variationId).catch((err) => {
                        showCartToast(err.message);
                    });
                }
            });
        });
    });

});