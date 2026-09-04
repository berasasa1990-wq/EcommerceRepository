/**
 * Evidencija: kursor na „Dodaj u korpu”, bez klika.
 * → staff #1 preporuka + Poslednji minut artikal.
 *
 * SAMO na stranici artikla (/artikal/…) — ne na početnoj ni katalog listama
 * (tamo svi prolaze kursorom preko dugmadi).
 */
(function () {
    if (window.location.pathname.startsWith('/nalog/') || window.location.pathname.startsWith('/admin/')) {
        return;
    }

    const TRACK_URL = '/uzivo/skoro-korpa/';
    const MIN_HOVER_MS = 400;
    // Samo dugmad na product detail (ne product-card na listama)
    const SELECTOR = [
        'button.btn-add-to-bag',
        'button.btn-detail-variation-add',
        'button[data-almost-cart]',
        '.add-to-cart-form button[type="submit"]',
        '.product-detail-variation-form button[type="submit"]',
        '.product-detail button[type="submit"]',
    ].join(',');

    /** Samo ako je kupac ušao u artikal. */
    function isProductDetailPage() {
        const path = (window.location.pathname || '/').replace(/\/+$/, '') || '/';
        return /^\/artikal\/[^/]+$/.test(path);
    }

    if (!isProductDetailPage()) {
        return;
    }

    const hoverState = new WeakMap();

    function readCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (match) return decodeURIComponent(match[1]);
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        return input ? input.value : '';
    }

    function resolveProduct(el) {
        if (!el) return null;
        const card = el.closest('[data-product-id], [data-almost-product-id], .product-card, .product-detail');
        let id = el.getAttribute('data-product-id')
            || el.getAttribute('data-almost-product-id')
            || (card && (card.getAttribute('data-product-id') || card.getAttribute('data-almost-product-id')))
            || '';
        let name = el.getAttribute('data-product-name')
            || (card && card.getAttribute('data-product-name'))
            || '';
        if (!id) {
            const root = document.getElementById('productDetailInfo')
                || document.querySelector('.product-detail');
            if (root) {
                id = root.getAttribute('data-product-id') || '';
                name = name || root.getAttribute('data-product-name') || '';
            }
        }
        if (!id) {
            // slug → ne znamo id; pokušaj iz form action /artikal/slug/dodaj/
            const form = el.closest('form');
            const action = form && form.getAttribute('action');
            if (action) {
                const m = action.match(/\/artikal\/([^/]+)\/dodaj\/?/);
                if (m) {
                    return { slug: m[1], id: '', name: name };
                }
            }
            return null;
        }
        return { id: String(id), name: name || '', slug: '' };
    }

    function sendTrack(product, clicked) {
        if (!product || (!product.id && !product.slug)) return;
        const body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', readCsrfToken());
        if (product.id) body.set('product_id', product.id);
        if (product.name) body.set('product_name', product.name);
        if (product.slug) body.set('product_slug', product.slug);
        if (clicked) body.set('clicked', '1');
        // product_id obavezan na backendu — slug fallback preko data attribute
        if (!product.id) return;
        try {
            if (navigator.sendBeacon && clicked) {
                const blob = new Blob([body.toString()], {
                    type: 'application/x-www-form-urlencoded',
                });
                navigator.sendBeacon(TRACK_URL, blob);
                return;
            }
        } catch (err) {
            /* fall through */
        }
        fetch(TRACK_URL, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': readCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
            body: body.toString(),
            keepalive: true,
        }).catch(function () {
            /* ignore */
        });
    }

    function onEnter(el) {
        const product = resolveProduct(el);
        if (!product || !product.id) return;
        hoverState.set(el, {
            product: product,
            started: Date.now(),
            clicked: false,
        });
    }

    function onLeave(el) {
        const state = hoverState.get(el);
        if (!state || state.clicked) return;
        const elapsed = Date.now() - (state.started || 0);
        hoverState.delete(el);
        if (elapsed < MIN_HOVER_MS) return;
        sendTrack(state.product, false);
    }

    function onClick(el) {
        const state = hoverState.get(el) || {};
        const product = state.product || resolveProduct(el);
        if (state) state.clicked = true;
        if (product && product.id) {
            sendTrack(product, true);
        }
    }

    function bind(el) {
        if (!el || el.dataset.almostCartBound === '1') return;
        el.dataset.almostCartBound = '1';
        el.addEventListener('mouseenter', function () { onEnter(el); });
        el.addEventListener('mouseleave', function () { onLeave(el); });
        el.addEventListener('click', function () { onClick(el); });
        // touch: short press without add is hard — skip; mouse is main signal
    }

    function scan(root) {
        (root || document).querySelectorAll(SELECTOR).forEach(bind);
    }

    scan(document);
    if (typeof MutationObserver !== 'undefined') {
        const obs = new MutationObserver(function (mutations) {
            mutations.forEach(function (m) {
                m.addedNodes.forEach(function (node) {
                    if (node.nodeType !== 1) return;
                    if (node.matches && node.matches(SELECTOR)) bind(node);
                    if (node.querySelectorAll) scan(node);
                });
            });
        });
        obs.observe(document.documentElement, { childList: true, subtree: true });
    }
})();
