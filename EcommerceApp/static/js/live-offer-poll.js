(function () {
    if (window.location.pathname.startsWith('/nalog/') || window.location.pathname.startsWith('/admin/')) {
        return;
    }

    const pollUrl = '/ponuda/status/';
    const pollIntervalMs = 1000;
    const LIVE_OFFER_ACTIVE_KEY = 'live_offer_active_session';
    const pageLoadedAt = Date.now();
    let lastOfferVersion = null;
    let activeOffer = null;
    let overlay = null;
    let countdownTimer = null;
    let timerSeconds = 0;
    let pollCsrfToken = null;

    function storePollCsrf(data) {
        if (data && data.csrf_token) {
            pollCsrfToken = data.csrf_token;
            const meta = document.querySelector('meta[name="csrf-token"]');
            if (meta) {
                meta.content = data.csrf_token;
            }
        }
    }

    function hideCompetingPopups() {
        // Namjerno prazno: ne gasimo druge popupove — čekamo u SiteModalQueue.
        // (Zadržano ime radi dismissCartRecoveryOverlay poziva ispod.)
    }

    function anyOtherPopupVisible() {
        return !!document.querySelector(
            '.site-popup-overlay.is-visible:not(.live-offer-overlay):not(.live-offer-confirm-overlay)'
        );
    }

    function markLiveOfferActive() {
        try {
            sessionStorage.setItem(LIVE_OFFER_ACTIVE_KEY, '1');
        } catch (err) {
            /* ignore */
        }
    }

    function clearLiveOfferActive() {
        try {
            sessionStorage.removeItem(LIVE_OFFER_ACTIVE_KEY);
        } catch (err) {
            /* ignore */
        }
    }

    function dismissCartRecoveryOverlay() {
        hideCompetingPopups();
        fetch('/korpa/podsjetnik/zatvori/', {
            method: 'POST',
            headers: {
                'X-CSRFToken': readCsrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            credentials: 'same-origin',
            body: 'csrfmiddlewaretoken=' + encodeURIComponent(readCsrfToken()),
        }).catch(function () {
            /* ignore */
        });
    }

    function showCartToast(message) {
        if (typeof window.showCartToast === 'function') {
            window.showCartToast(message);
            return;
        }
        let toast = document.querySelector('.cart-toast');
        if (!toast) {
            toast = document.createElement('p');
            toast.className = 'cart-toast';
            document.body.appendChild(toast);
        }
        toast.textContent = message;
        toast.classList.add('cart-toast--visible');
        window.setTimeout(function () {
            toast.classList.remove('cart-toast--visible');
        }, 2200);
    }

    async function parseJsonResponse(response) {
        const text = await response.text();
        try {
            return JSON.parse(text);
        } catch (err) {
            if (response.status === 403) {
                throw new Error('Sesija je istekla. Osvježite stranicu i pokušajte ponovo.');
            }
            throw new Error('Dodavanje u korpu nije uspjelo.');
        }
    }

    function readCsrfToken() {
        if (pollCsrfToken) {
            return pollCsrfToken;
        }
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) {
            return meta.content;
        }
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        if (match) {
            return decodeURIComponent(match[1]);
        }
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) {
            return input.value;
        }
        return '';
    }

    function buildOfferPostBody(form) {
        const csrf = readCsrfToken();
        if (!csrf) {
            throw new Error('Sigurnosni token nije dostupan. Osvježite stranicu.');
        }
        const body = new URLSearchParams(new FormData(form));
        body.set('csrfmiddlewaretoken', csrf);
        body.set('stay', '1');
        return { csrf: csrf, body: body };
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function formatTimer(totalSeconds) {
        const seconds = Math.max(0, totalSeconds | 0);
        const minutes = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return String(minutes) + ':' + String(secs).padStart(2, '0');
    }

    function stopCountdown() {
        if (countdownTimer) {
            window.clearInterval(countdownTimer);
            countdownTimer = null;
        }
    }

    function startCountdown(seconds) {
        stopCountdown();
        timerSeconds = Math.max(0, seconds | 0);
        const timerEl = overlay ? overlay.querySelector('[data-live-offer-timer]') : null;
        if (!timerEl) return;
        timerEl.textContent = formatTimer(timerSeconds);
        countdownTimer = window.setInterval(function () {
            timerSeconds = Math.max(0, timerSeconds - 1);
            timerEl.textContent = formatTimer(timerSeconds);
            if (timerSeconds <= 0) {
                stopCountdown();
            }
        }, 1000);
    }

    function closeOverlay() {
        if (!overlay) return;
        overlay.classList.remove('is-visible');
        overlay.hidden = true;
        document.body.classList.remove('popup-open');
        stopCountdown();
        if (window.SiteModalQueue && typeof window.SiteModalQueue.notifyClosed === 'function') {
            window.SiteModalQueue.notifyClosed('live-offer');
        }
    }

    function showLiveOfferNow() {
        if (!overlay || !overlay.isConnected) {
            if (window.SiteModalQueue) window.SiteModalQueue.notifyClosed('live-offer');
            return;
        }
        // Nikad preko drugog
        if (anyOtherPopupVisible()) {
            if (window.SiteModalQueue) {
                window.SiteModalQueue.notifyClosed('live-offer');
                window.SiteModalQueue.enqueue({
                    id: 'live-offer',
                    canShow: function () { return !!overlay && overlay.isConnected; },
                    show: showLiveOfferNow,
                });
            } else {
                window.setTimeout(openOverlay, 400);
            }
            return;
        }
        markLiveOfferActive();
        overlay.hidden = false;
        overlay.classList.add('is-visible');
        document.body.classList.add('popup-open');
        if (activeOffer) {
            startCountdown(activeOffer.timer_seconds);
        }
    }

    function openOverlay() {
        if (!overlay) return;
        // Sakrij dok red ne odobri prikaz (HTML se više ne montira s is-visible)
        overlay.hidden = true;
        overlay.classList.remove('is-visible');
        // Ako je neki drugi popup otvoren — stani u red, ne preko njega i ne propadaj
        if (window.SiteModalQueue && typeof window.SiteModalQueue.enqueue === 'function') {
            window.SiteModalQueue.enqueue({
                id: 'live-offer',
                canShow: function () {
                    return !!overlay && overlay.isConnected;
                },
                show: showLiveOfferNow,
            });
            return;
        }
        if (anyOtherPopupVisible() || document.body.classList.contains('popup-open')) {
            window.setTimeout(openOverlay, 400);
            return;
        }
        showLiveOfferNow();
    }

    function buildVariationOptions(offer) {
        if (!offer.has_variations) {
            return '<input type="hidden" name="variation_id" value="">';
        }
        const options = ['<option value="">Izaberite varijaciju</option>'];
        offer.variations.forEach(function (variation) {
            const price = variation.has_discount ? variation.final_price : variation.base_price;
            options.push(
                '<option value="' + variation.id + '" ' +
                'data-base="' + escapeHtml(variation.base_price) + '" ' +
                'data-final="' + escapeHtml(variation.final_price) + '" ' +
                'data-has-discount="' + (variation.has_discount ? '1' : '0') + '">' +
                escapeHtml(variation.naziv) + ' — ' + escapeHtml(price) + ' KM</option>',
            );
        });
        return '<select name="variation_id" class="live-offer-var-select" id="liveOfferVariation" required>' +
            options.join('') + '</select>';
    }

    function buildPricesHtml(offer) {
        if (offer.has_discount) {
            return (
                '<span class="live-offer-price-original" id="liveOfferPriceOriginal">' +
                escapeHtml(offer.display_base_price) + ' KM</span>' +
                '<span class="live-offer-price-final" id="liveOfferPriceFinal">' +
                escapeHtml(offer.display_final_price) + ' KM</span>'
            );
        }
        return '<span class="live-offer-price-final" id="liveOfferPriceFinal">' +
            escapeHtml(offer.display_base_price) + ' KM</span>';
    }

    function buildFreeShippingOverlayHtml(offer) {
        return (
            '<div class="site-popup-overlay live-offer-overlay" id="liveOfferOverlay">' +
            '<div class="site-popup site-popup--akcija live-offer-popup" role="dialog" aria-modal="true">' +
            '<button type="button" class="site-popup-close" data-live-offer-close aria-label="Zatvori">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg></button>' +
            '<div class="live-offer-timer-box" style="margin:0 auto 12px;max-width:220px;">' +
            '<span class="live-offer-timer-label">Ponuda ističe za</span>' +
            '<span class="live-offer-timer-value" data-live-offer-timer>9:00</span>' +
            '</div>' +
            '<div class="live-offer-body">' +
            '<p class="live-offer-kicker">Posebna ponuda</p>' +
            '<h3 class="live-offer-order-title">' +
            escapeHtml(offer.title || 'Besplatna dostava na prvu kupovinu') +
            '</h3>' +
            '<p class="live-offer-reg-message">' +
            escapeHtml(offer.message || 'Prihvatite ponudu — na prvu narudžbu dostava vam je besplatna.') +
            '</p>' +
            (offer.activation_code
                ? ('<div class="live-offer-code-box">' +
                   '<span class="live-offer-code-label">Vaš kod</span>' +
                   '<span class="live-offer-code-value">' + escapeHtml(offer.activation_code) + '</span>' +
                   '</div>')
                : '') +
            '<form method="post" action="' + escapeHtml(offer.activate_url || '/ponuda/aktiviraj/') +
            '" class="live-offer-activate-form" id="liveOfferActivateForm">' +
            '<button type="submit" class="btn btn-primary site-popup-cta live-offer-activate-cta">' +
            escapeHtml(offer.cta_label || 'Prihvati besplatnu dostavu') +
            '</button>' +
            '</form></div></div></div>'
        );
    }

    function buildOrderOverlayHtml(offer) {
        const pct = escapeHtml(offer.discount_percent);
        const freeShip = !!offer.free_shipping;
        const title = freeShip
            ? ('Imate popust od <strong>' + pct + '%</strong> + besplatnu dostavu na prvu kupovinu')
            : ('Imate popust od <strong>' + pct + '%</strong> na cijelu narudžbu');
        return (
            '<div class="site-popup-overlay live-offer-overlay" id="liveOfferOverlay">' +
            '<div class="site-popup site-popup--akcija live-offer-popup" role="dialog" aria-modal="true">' +
            '<button type="button" class="site-popup-close" data-live-offer-close aria-label="Zatvori">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg></button>' +
            '<div class="live-offer-timer-box" style="margin:0 auto 12px;max-width:220px;">' +
            '<span class="live-offer-timer-label">Ponuda ističe za</span>' +
            '<span class="live-offer-timer-value" data-live-offer-timer>9:00</span>' +
            '</div>' +
            '<div class="live-offer-body">' +
            '<p class="live-offer-kicker">Posebna ponuda</p>' +
            '<h3 class="live-offer-order-title">' + title + '</h3>' +
            '<div class="live-offer-code-box">' +
            '<span class="live-offer-code-label">Vaš kod</span>' +
            '<span class="live-offer-code-value">' + escapeHtml(offer.activation_code) + '</span>' +
            '</div>' +
            '<form method="post" action="' + escapeHtml(offer.activate_url) + '" class="live-offer-activate-form" id="liveOfferActivateForm">' +
            '<button type="submit" class="btn btn-primary site-popup-cta live-offer-activate-cta">Aktiviraj kod</button>' +
            '</form></div></div></div>'
        );
    }

    function buildRegistrationOverlayHtml(offer) {
        const pct = offer.discount_percent;
        const benefits = (offer.benefits && offer.benefits.length)
            ? offer.benefits
            : (pct
                ? [
                    pct + '% popusta na prvu narudžbu',
                    'Automatski se primjenjuje u korpi',
                    'Vrijedi samo jednom — nakon porudžbe prestaje',
                ]
                : [
                    'Besplatna dostava na prvu narudžbu',
                    'Automatski se primjenjuje u korpi',
                    'Vrijedi samo jednom — nakon porudžbe prestaje',
                ]);
        const benefitsHtml = benefits.map(function (item) {
            return '<li>' + escapeHtml(item) + '</li>';
        }).join('');
        return (
            '<div class="site-popup-overlay live-offer-overlay" id="liveOfferOverlay">' +
            '<div class="site-popup site-popup--akcija live-offer-popup live-offer-popup--registration" role="dialog" aria-modal="true">' +
            '<button type="button" class="site-popup-close" data-live-offer-close aria-label="Zatvori">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg></button>' +
            '<div class="live-offer-reg-icon" aria-hidden="true">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
            '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/>' +
            '<circle cx="9" cy="7" r="4"/>' +
            '<line x1="19" y1="8" x2="19" y2="14"/>' +
            '<line x1="22" y1="11" x2="16" y2="11"/>' +
            '</svg></div>' +
            '<div class="live-offer-body">' +
            '<p class="live-offer-kicker">Besplatna dostava na prvu narudžbu</p>' +
            '<h3 class="live-offer-order-title">' +
            escapeHtml(offer.title || 'Registrujte se i ostvarite besplatnu dostavu') +
            '</h3>' +
            '<p class="live-offer-reg-message">' + escapeHtml(offer.message || '') + '</p>' +
            '<ul class="live-offer-reg-benefits">' + benefitsHtml + '</ul>' +
            '<a href="' + escapeHtml(offer.register_url || '/registracija/') +
            '" class="btn btn-primary site-popup-cta live-offer-cta live-offer-reg-cta" data-live-offer-register>' +
            escapeHtml(offer.cta_label || 'Registruj se i uzmi besplatnu dostavu') +
            '</a>' +
            '</div></div></div>'
        );
    }

    function showActivationConfirm(message) {
        const existing = document.getElementById('liveOfferConfirmOverlay');
        if (existing) existing.remove();

        const confirmOverlay = document.createElement('div');
        confirmOverlay.className = 'site-popup-overlay live-offer-confirm-overlay is-visible';
        confirmOverlay.id = 'liveOfferConfirmOverlay';
        confirmOverlay.innerHTML =
            '<div class="site-popup live-offer-confirm-popup" role="dialog" aria-modal="true">' +
            '<h3 class="live-offer-confirm-title">Kod je aktiviran!</h3>' +
            '<p class="live-offer-confirm-text">' + escapeHtml(message) + '</p>' +
            '<button type="button" class="btn btn-primary live-offer-confirm-cta" data-live-offer-confirm-close>Nastavi kupovinu</button>' +
            '</div>';
        document.body.appendChild(confirmOverlay);
        document.body.classList.add('popup-open');
        confirmOverlay.querySelector('[data-live-offer-confirm-close]')?.addEventListener('click', function () {
            confirmOverlay.classList.remove('is-visible');
            confirmOverlay.remove();
            document.body.classList.remove('popup-open');
        });
    }

    function buildBrowseProductCard(product, offer) {
        const pct = offer.discount_percent;
        const imageHtml = product.image_url
            ? '<img src="' + escapeHtml(product.image_url) + '" alt="' + escapeHtml(product.product_name) +
              '" class="browse-offer-card-image" width="120" height="120" loading="eager" decoding="async">'
            : '<div class="browse-offer-card-image browse-offer-card-image--empty"></div>';

        let variationsHtml = '';
        if (product.has_variations && product.variations && product.variations.length) {
            const options = ['<option value="">Varijacija</option>'];
            product.variations.forEach(function (variation) {
                const price = variation.has_discount ? variation.final_price : variation.base_price;
                options.push(
                    '<option value="' + variation.id + '" ' +
                    'data-base="' + escapeHtml(variation.base_price) + '" ' +
                    'data-final="' + escapeHtml(variation.final_price) + '" ' +
                    'data-has-discount="' + (variation.has_discount ? '1' : '0') + '">' +
                    escapeHtml(variation.naziv) + ' — ' + escapeHtml(price) + ' KM</option>',
                );
            });
            variationsHtml =
                '<select name="variation_id" class="live-offer-var-select browse-offer-var" required>' +
                options.join('') + '</select>';
        } else {
            variationsHtml =
                '<input type="hidden" name="variation_id" value="' +
                escapeHtml(product.variation_id || '') + '">';
        }

        const pricesHtml = product.has_discount
            ? (
                '<span class="live-offer-price-original browse-offer-price-original">' +
                escapeHtml(product.display_base_price) + ' KM</span>' +
                '<span class="live-offer-price-final">' +
                escapeHtml(product.display_final_price) + ' KM</span>'
            )
            : (
                '<span class="live-offer-price-final">' +
                escapeHtml(product.display_base_price) + ' KM</span>'
            );

        return (
            '<article class="browse-offer-card" data-product-id="' + product.product_id + '">' +
            '<a href="' + escapeHtml(product.product_url) + '" class="browse-offer-card-media">' +
            imageHtml +
            (pct ? '<span class="browse-offer-badge">-' + escapeHtml(pct) + '%</span>' : '') +
            '</a>' +
            '<div class="browse-offer-card-body">' +
            '<h4 class="browse-offer-card-name">' +
            '<a href="' + escapeHtml(product.product_url) + '">' +
            escapeHtml(product.product_name) + '</a></h4>' +
            '<div class="browse-offer-card-prices">' + pricesHtml + '</div>' +
            '<form method="post" action="' + escapeHtml(offer.add_url || '/preporuka/dodaj/') +
            '" class="browse-offer-form live-offer-form">' +
            '<input type="hidden" name="product_id" value="' + product.product_id + '">' +
            variationsHtml +
            '<button type="submit" class="btn btn-primary site-popup-cta live-offer-cta browse-offer-cta">' +
            'Uzmi -' + escapeHtml(pct || '10') + '%</button>' +
            '</form></div></article>'
        );
    }

    function isMobileViewport() {
        try {
            return window.matchMedia && window.matchMedia('(max-width: 768px)').matches;
        } catch (err) {
            return window.innerWidth <= 768;
        }
    }

    function buildBrowseInterestOverlayHtml(offer) {
        // Mobilni: max 2 artikla radi preglednosti (desktop do 4)
        let products = offer.products || [];
        if (isMobileViewport() && products.length > 2) {
            products = products.slice(0, 2);
        }
        const cardsHtml = products.map(function (product) {
            return buildBrowseProductCard(product, offer);
        }).join('');
        const multiClass = products.length > 1 ? ' live-offer-popup--browse-multi' : '';
        const gridClass = products.length <= 2
            ? 'browse-offer-grid browse-offer-grid--1x2'
            : 'browse-offer-grid browse-offer-grid--2x2';

        return (
            '<div class="site-popup-overlay live-offer-overlay" id="liveOfferOverlay">' +
            '<div class="site-popup site-popup--akcija live-offer-popup live-offer-popup--browse' + multiClass +
            '" role="dialog" aria-modal="true">' +
            '<button type="button" class="site-popup-close" data-live-offer-close aria-label="Zatvori">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg></button>' +
            '<div class="live-offer-timer-box browse-offer-timer" style="margin:0 auto 10px;max-width:220px;">' +
            '<span class="live-offer-timer-label">Ponuda ističe za</span>' +
            '<span class="live-offer-timer-value" data-live-offer-timer>2:00</span>' +
            '</div>' +
            '<div class="live-offer-body browse-offer-body">' +
            '<p class="live-offer-kicker">' + escapeHtml(offer.kicker || 'Specijalna ponuda za vas') + '</p>' +
            '<h3 class="live-offer-order-title browse-offer-title">' +
            escapeHtml(offer.title || 'Specijalna ponuda za vas') + '</h3>' +
            '<p class="live-offer-reg-message browse-offer-message">' +
            escapeHtml(offer.message || '') + '</p>' +
            '<div class="' + gridClass + '" data-browse-count="' + products.length + '">' +
            cardsHtml +
            '</div></div></div></div>'
        );
    }

    function buildOverlayHtml(offer) {
        if (offer.offer_type === 'registration') {
            return buildRegistrationOverlayHtml(offer);
        }
        if (offer.offer_type === 'free_shipping') {
            return buildFreeShippingOverlayHtml(offer);
        }
        if (offer.offer_type === 'order') {
            return buildOrderOverlayHtml(offer);
        }
        if (offer.offer_type === 'browse_interest') {
            return buildBrowseInterestOverlayHtml(offer);
        }
        let promoText = offer.has_discount && offer.discount_percent
            ? 'Dodatni popust od <strong>' + escapeHtml(offer.discount_percent) + '%</strong>'
            : 'Posebna ponuda samo za vas';
        if (offer.free_shipping) {
            promoText += ' + <strong>besplatna dostava</strong> na prvu kupovinu';
        }
        const imageHtml = offer.image_url
            ? '<img src="' + escapeHtml(offer.image_url) + '" alt="' + escapeHtml(offer.product_name) +
              '" class="live-offer-image" width="240" height="240" loading="eager" decoding="async">'
            : '<div class="live-offer-image-placeholder"></div>';

        return (
            '<div class="site-popup-overlay live-offer-overlay" id="liveOfferOverlay">' +
            '<div class="site-popup site-popup--akcija live-offer-popup" role="dialog" aria-modal="true">' +
            '<button type="button" class="site-popup-close" data-live-offer-close aria-label="Zatvori">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">' +
            '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>' +
            '</svg></button>' +
            '<div class="live-offer-hero">' +
            '<div class="live-offer-image-wrap">' + imageHtml + '</div>' +
            '<div class="live-offer-timer-box">' +
            '<span class="live-offer-timer-label">Ponuda ističe za</span>' +
            '<span class="live-offer-timer-value" data-live-offer-timer>9:00</span>' +
            '</div></div>' +
            '<div class="live-offer-body">' +
            '<p class="live-offer-kicker">Posebna ponuda</p>' +
            '<h3 class="live-offer-product-name">' + escapeHtml(offer.product_name) + '</h3>' +
            '<p class="live-offer-promo">' + promoText + '</p>' +
            '<div class="live-offer-prices" id="liveOfferPrices">' + buildPricesHtml(offer) + '</div>' +
            '<form method="post" action="' + escapeHtml(offer.add_url) + '" class="live-offer-form" id="liveOfferForm">' +
            buildVariationOptions(offer) +
            '<button type="submit" class="btn btn-primary site-popup-cta live-offer-cta">Dodaj u korpu</button>' +
            '</form></div></div></div>'
        );
    }

    function bindOverlayEvents() {
        if (!overlay) return;

        // Zatvaranje samo preko X — klik pored popupa ne gasi ponudu
        overlay.querySelector('[data-live-offer-close]')?.addEventListener('click', dismissOffer);

        const registerCta = overlay.querySelector('[data-live-offer-register]');
        if (registerCta) {
            registerCta.addEventListener('click', async function (e) {
                e.preventDefault();
                const href = registerCta.getAttribute('href') || '/registracija/';
                await dismissOffer({ keepNavigation: true });
                window.location.href = href;
            });
        }

        const variationSelect = overlay.querySelector('#liveOfferVariation');
        const priceOriginal = overlay.querySelector('#liveOfferPriceOriginal');
        const priceFinal = overlay.querySelector('#liveOfferPriceFinal');
        if (variationSelect && priceFinal) {
            variationSelect.addEventListener('change', function () {
                const option = variationSelect.options[variationSelect.selectedIndex];
                if (!option || !option.value) return;
                const hasDiscount = option.dataset.hasDiscount === '1';
                if (hasDiscount && priceOriginal) {
                    priceOriginal.textContent = option.dataset.base + ' KM';
                    priceOriginal.hidden = false;
                    priceFinal.textContent = option.dataset.final + ' KM';
                } else if (priceOriginal) {
                    priceOriginal.hidden = true;
                    priceFinal.textContent = option.dataset.base + ' KM';
                }
            });
        }

        const activateForm = overlay.querySelector('#liveOfferActivateForm');
        if (activateForm) {
            activateForm.addEventListener('submit', async function (e) {
                e.preventDefault();
                const submitBtn = activateForm.querySelector('button[type="submit"]');
                if (submitBtn) submitBtn.disabled = true;
                try {
                    const postData = buildOfferPostBody(activateForm);
                    const response = await fetch(activateForm.action, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-CSRFToken': postData.csrf,
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        credentials: 'same-origin',
                        body: postData.body.toString(),
                    });
                    const data = await parseJsonResponse(response);
                    if (!response.ok || !data.ok) {
                        throw new Error(data.message || 'Aktivacija koda nije uspjela.');
                    }
                    lastOfferVersion = null;
                    activeOffer = null;
                    clearLiveOfferActive();
                    dismissCartRecoveryOverlay();
                    closeOverlay();
                    if (overlay && overlay.parentNode) {
                        overlay.parentNode.removeChild(overlay);
                        overlay = null;
                    }
                    showActivationConfirm(data.message);
                } catch (err) {
                    alert(err.message || 'Aktivacija koda nije uspjela.');
                } finally {
                    if (submitBtn) submitBtn.disabled = false;
                }
            });
        }

        function bindAddToCartForm(offerForm) {
            offerForm.addEventListener('submit', async function (e) {
                e.preventDefault();
                const submitBtn = offerForm.querySelector('button[type="submit"]');
                if (submitBtn) submitBtn.disabled = true;
                try {
                    const postData = buildOfferPostBody(offerForm);
                    const response = await fetch(offerForm.action, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'X-CSRFToken': postData.csrf,
                            'X-Requested-With': 'XMLHttpRequest',
                        },
                        credentials: 'same-origin',
                        body: postData.body.toString(),
                    });
                    const data = await parseJsonResponse(response);
                    if (!response.ok || !data.ok) {
                        throw new Error(data.message || 'Dodavanje u korpu nije uspjelo.');
                    }

                    lastOfferVersion = null;
                    activeOffer = null;
                    clearLiveOfferActive();
                    dismissCartRecoveryOverlay();
                    closeOverlay();
                    if (overlay && overlay.parentNode) {
                        overlay.parentNode.removeChild(overlay);
                        overlay = null;
                    }

                    showCartToast(data.message);
                    const cartBtn = document.querySelector('.cart-btn');
                    if (cartBtn && data.cart_count != null) {
                        cartBtn.dataset.cartCount = String(data.cart_count);
                        cartBtn.classList.toggle('cart-btn--has-items', data.cart_count > 0);
                        let badge = cartBtn.querySelector('.cart-badge');
                        if (data.cart_count > 0) {
                            if (!badge) {
                                badge = document.createElement('span');
                                badge.className = 'cart-badge';
                                cartBtn.appendChild(badge);
                            }
                            badge.textContent = String(data.cart_count);
                        } else if (badge) {
                            badge.remove();
                        }
                    }
                } catch (err) {
                    alert(err.message || 'Dodavanje u korpu nije uspjelo.');
                } finally {
                    if (submitBtn) submitBtn.disabled = false;
                }
            });
        }

        const offerForm = overlay.querySelector('#liveOfferForm');
        if (offerForm) {
            bindAddToCartForm(offerForm);
        }
        overlay.querySelectorAll('.browse-offer-form').forEach(function (form) {
            bindAddToCartForm(form);
            const select = form.querySelector('select.browse-offer-var');
            if (select) {
                select.addEventListener('change', function () {
                    const card = form.closest('.browse-offer-card');
                    if (!card) return;
                    const option = select.options[select.selectedIndex];
                    if (!option || !option.value) return;
                    const original = card.querySelector('.browse-offer-price-original');
                    const finalEl = card.querySelector('.live-offer-price-final');
                    if (!finalEl) return;
                    const hasDiscount = option.dataset.hasDiscount === '1';
                    if (hasDiscount && original) {
                        original.textContent = option.dataset.base + ' KM';
                        original.hidden = false;
                        finalEl.textContent = option.dataset.final + ' KM';
                    } else if (original) {
                        original.hidden = true;
                        finalEl.textContent = option.dataset.base + ' KM';
                    }
                });
            }
        });
    }

    async function dismissOffer(options) {
        const keepNavigation = options && options.keepNavigation;
        const dismissUrl = (activeOffer && activeOffer.dismiss_url) || '/ponuda/zatvori/';
        try {
            await fetch(dismissUrl, {
                method: 'POST',
                headers: {
                    'X-CSRFToken': readCsrfToken(),
                    'X-Requested-With': 'XMLHttpRequest',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                body: 'csrfmiddlewaretoken=' + encodeURIComponent(readCsrfToken()),
            });
        } catch (err) {
            /* ignore */
        }
        lastOfferVersion = null;
        clearLiveOfferActive();
        if (!keepNavigation) {
            closeOverlay();
        }
    }

    function renderOffer(offer) {
        const mount = document.getElementById('liveOfferMount');
        if (!mount) return;

        const isNew = offer.offer_version !== lastOfferVersion;
        const wasVisible = overlay && !overlay.hidden && overlay.classList.contains('is-visible');

        if (overlay && overlay.parentNode) {
            overlay.parentNode.removeChild(overlay);
            overlay = null;
        }

        mount.innerHTML = buildOverlayHtml(offer);
        overlay = document.getElementById('liveOfferOverlay');
        activeOffer = offer;
        bindOverlayEvents();

        if (isNew || !wasVisible) {
            openOverlay();
        } else if (activeOffer) {
            startCountdown(activeOffer.timer_seconds);
        }
        lastOfferVersion = offer.offer_version;
    }

    async function pollOffer() {
        try {
            const welcomeElapsed = Math.max(0, (Date.now() - pageLoadedAt) / 1000);
            const url = pollUrl +
                (pollUrl.indexOf('?') >= 0 ? '&' : '?') +
                'welcome_elapsed=' + encodeURIComponent(welcomeElapsed.toFixed(2));
            const response = await fetch(url, {
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-Viewport-Mobile': isMobileViewport() ? '1' : '0',
                },
                credentials: 'same-origin',
            });
            if (!response.ok) return;
            const data = await response.json();
            storePollCsrf(data);
            if (!data.active || !data.offer) {
                if (overlay) closeOverlay();
                lastOfferVersion = null;
                activeOffer = null;
                return;
            }
            if (data.offer.offer_version !== lastOfferVersion) {
                renderOffer(data.offer);
            } else if (overlay && activeOffer) {
                activeOffer.timer_seconds = data.offer.timer_seconds;
                if (overlay.classList.contains('is-visible')) {
                    startCountdown(data.offer.timer_seconds);
                }
            }
        } catch (err) {
            /* ignore */
        }
    }

    pollOffer();
    window.setInterval(pollOffer, pollIntervalMs);
})();