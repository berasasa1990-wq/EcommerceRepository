/**
 * Live toast obavijesti za superusere + brze akcije (registracija / ponuda).
 * Online toast stoji dok je posjetilac na sajtu; korpa se prikazuje u istom prozoru.
 */
(function () {
    const root = document.getElementById('staffAlertsRoot');
    if (!root) return;

    const pollUrl = root.dataset.pollUrl || '/nalog/uzivo-obavijesti/';
    const registerUrl = root.dataset.registerUrl || '/nalog/uzivo-analitika/registracija/';
    const offerUrl = root.dataset.offerUrl || '/nalog/uzivo-analitika/ponuda/';
    const productSearchUrl = root.dataset.productSearchUrl || '/nalog/pretraga-artikala/';
    const pollMs = 2000;
    const storageKey = 'staff_alerts_since_id';
    const stickyOnlineKey = 'staff_alerts_sticky_online';
    const dismissedOnlineKey = 'staff_alerts_dismissed_online';
    let sinceId = 0;
    let stack = null;
    let offerModal = null;

    try {
        const saved = parseInt(sessionStorage.getItem(storageKey) || '0', 10);
        if (!Number.isNaN(saved) && saved > 0) {
            sinceId = saved;
        }
    } catch (err) {
        /* ignore */
    }

    function readJsonStorage(key, fallback) {
        try {
            const raw = sessionStorage.getItem(key);
            if (!raw) return fallback;
            const parsed = JSON.parse(raw);
            return parsed == null ? fallback : parsed;
        } catch (err) {
            return fallback;
        }
    }

    function writeJsonStorage(key, value) {
        try {
            sessionStorage.setItem(key, JSON.stringify(value));
        } catch (err) {
            /* ignore */
        }
    }

    function getStickyOnlineMap() {
        const map = readJsonStorage(stickyOnlineKey, {});
        return map && typeof map === 'object' && !Array.isArray(map) ? map : {};
    }

    function setStickyOnlineMap(map) {
        writeJsonStorage(stickyOnlineKey, map || {});
    }

    function getDismissedOnlineSet() {
        const list = readJsonStorage(dismissedOnlineKey, []);
        return new Set(Array.isArray(list) ? list : []);
    }

    function setDismissedOnlineSet(set) {
        writeJsonStorage(dismissedOnlineKey, Array.from(set || []));
    }

    function cssEscape(value) {
        if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
            return CSS.escape(String(value || ''));
        }
        return String(value || '').replace(/["\\]/g, '\\$&');
    }

    function stickyPayloadFromEvent(event) {
        return {
            tip: 'online',
            naslov: event.naslov || 'Novi posjetilac online',
            poruka: event.poruka || '',
            ime: event.ime || '',
            email: event.email || '',
            grad: event.grad || '',
            session_key: event.session_key,
            can_register: event.can_register !== false,
            can_offer: event.can_offer !== false,
            sticky: true,
            has_purchased: !!event.has_purchased,
            offer_rejected: !!event.offer_rejected,
            register_rejected: !!event.register_rejected,
            offer_active: !!event.offer_active,
            register_active: !!event.register_active,
            cart_items: Array.isArray(event.cart_items) ? event.cart_items : [],
            cart_count: event.cart_count || 0,
            cart_total: event.cart_total || '0,00',
            kreirano: event.kreirano || '',
        };
    }

    function rememberStickyOnline(event) {
        if (!event || !event.session_key) return;
        const map = getStickyOnlineMap();
        const prev = map[event.session_key] || {};
        const next = stickyPayloadFromEvent(event);
        // Zadrži korpu ako novi event nema stavke
        if ((!next.cart_items || !next.cart_items.length) && prev.cart_items && prev.cart_items.length) {
            next.cart_items = prev.cart_items;
            next.cart_count = prev.cart_count || prev.cart_items.length;
            next.cart_total = prev.cart_total || next.cart_total;
        }
        if (prev.has_purchased) next.has_purchased = true;
        map[event.session_key] = next;
        setStickyOnlineMap(map);
        const dismissed = getDismissedOnlineSet();
        if (dismissed.has(event.session_key)) {
            dismissed.delete(event.session_key);
            setDismissedOnlineSet(dismissed);
        }
    }

    function patchStickyOnline(sessionKey, patch) {
        if (!sessionKey || !patch) return null;
        const map = getStickyOnlineMap();
        const prev = map[sessionKey] || {
            tip: 'online',
            naslov: 'Posjetilac online',
            poruka: '',
            session_key: sessionKey,
            sticky: true,
        };
        const next = Object.assign({}, prev, patch, {
            tip: 'online',
            session_key: sessionKey,
            sticky: true,
        });
        if (prev.has_purchased || patch.has_purchased) {
            next.has_purchased = true;
        }
        map[sessionKey] = next;
        setStickyOnlineMap(map);
        return next;
    }

    function forgetStickyOnline(sessionKey) {
        if (!sessionKey) return;
        const map = getStickyOnlineMap();
        if (map[sessionKey]) {
            delete map[sessionKey];
            setStickyOnlineMap(map);
        }
    }

    function dismissStickyOnline(sessionKey) {
        if (!sessionKey) return;
        forgetStickyOnline(sessionKey);
        const dismissed = getDismissedOnlineSet();
        dismissed.add(sessionKey);
        setDismissedOnlineSet(dismissed);
    }

    function findStickyToast(sessionKey) {
        if (!sessionKey) return null;
        ensureStack();
        if (!stack) return null;
        return stack.querySelector(
            '.staff-alert-toast[data-sticky-online="1"][data-session-key="' +
            cssEscape(sessionKey) + '"]',
        );
    }

    function removeToastEl(toast) {
        if (!toast || !toast.isConnected) return;
        toast.classList.remove('is-visible');
        window.setTimeout(function () {
            toast.remove();
        }, 220);
    }

    function ensureStack() {
        if (stack && document.body.contains(stack)) return stack;
        stack = document.createElement('div');
        stack.id = 'staffAlertsStack';
        stack.className = 'staff-alerts-stack';
        stack.setAttribute('aria-live', 'polite');
        stack.setAttribute('aria-relevant', 'additions');
        document.body.appendChild(stack);
        return stack;
    }

    function tipLabel(tip) {
        if (tip === 'online') return 'Online';
        if (tip === 'cart') return 'Korpa';
        if (tip === 'register') return 'Registracija';
        if (tip === 'purchase') return 'Kupovina';
        return 'Obavijest';
    }

    function readCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function showMiniToast(message, isError) {
        let el = document.querySelector('.staff-alerts-feedback');
        if (!el) {
            el = document.createElement('p');
            el.className = 'staff-alerts-feedback';
            document.body.appendChild(el);
        }
        el.textContent = message;
        el.classList.toggle('staff-alerts-feedback--error', !!isError);
        el.classList.add('is-visible');
        window.setTimeout(function () {
            el.classList.remove('is-visible');
        }, 3200);
    }

    async function postStaffAction(url, fields) {
        const csrf = readCsrfToken();
        if (!csrf) {
            throw new Error('Sigurnosni token nije dostupan. Osvježite stranicu.');
        }
        const body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', csrf);
        Object.keys(fields || {}).forEach(function (key) {
            if (fields[key] !== undefined && fields[key] !== null && fields[key] !== '') {
                body.set(key, fields[key]);
            }
        });
        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': csrf,
                'X-Requested-With': 'XMLHttpRequest',
            },
            credentials: 'same-origin',
            body: body.toString(),
        });
        let data = {};
        try {
            data = await response.json();
        } catch (err) {
            if (response.status === 403) {
                throw new Error('Sesija je istekla. Osvježite stranicu.');
            }
            throw new Error('Akcija nije uspjela.');
        }
        if (!response.ok || !data.ok) {
            throw new Error(data.message || 'Akcija nije uspjela.');
        }
        return data;
    }

    function ensureOfferModal() {
        if (offerModal && document.body.contains(offerModal)) return offerModal;

        offerModal = document.createElement('div');
        offerModal.className = 'staff-alert-offer-modal';
        offerModal.id = 'staffAlertOfferModal';
        offerModal.hidden = true;
        offerModal.innerHTML =
            '<div class="staff-alert-offer-modal__card" role="dialog" aria-modal="true" aria-labelledby="staffAlertOfferTitle">' +
            '<button type="button" class="staff-alert-offer-modal__close" data-offer-close aria-label="Zatvori">×</button>' +
            '<h2 id="staffAlertOfferTitle">Pošalji ponudu</h2>' +
            '<p class="staff-alert-offer-modal__sub">Kupac: <strong data-offer-visitor>—</strong></p>' +
            '<form id="staffAlertOfferForm" class="staff-alert-offer-modal__form">' +
            '<input type="hidden" name="session_key" data-offer-session value="">' +
            '<label class="staff-alert-offer-label" for="staffAlertProductSearch">Artikal <span>(opcionalno)</span></label>' +
            '<div class="staff-alert-offer-search">' +
            '<input type="search" id="staffAlertProductSearch" placeholder="Pretraži po nazivu ili šifri…" autocomplete="off">' +
            '<input type="hidden" name="product_id" id="staffAlertProductId" value="">' +
            '<div class="staff-alert-offer-results" id="staffAlertProductResults" hidden></div>' +
            '<p class="staff-alert-offer-selected" id="staffAlertSelectedProduct" hidden></p>' +
            '</div>' +
            '<label class="staff-alert-offer-label" for="staffAlertDiscount">Popust % na narudžbu</label>' +
            '<p class="staff-alert-offer-hint">Bez artikla — samo kod za popust. S artiklom — popust na taj artikal.</p>' +
            '<input type="number" name="discount_percent" id="staffAlertDiscount" min="0" max="50" step="1" value="10" required>' +
            '<div class="staff-alert-offer-modal__actions">' +
            '<button type="button" class="btn btn-secondary" data-offer-close>Poništi</button>' +
            '<button type="submit" class="btn btn-primary">Pošalji ponudu</button>' +
            '</div></form></div>';

        document.body.appendChild(offerModal);

        const searchInput = offerModal.querySelector('#staffAlertProductSearch');
        const productIdEl = offerModal.querySelector('#staffAlertProductId');
        const resultsEl = offerModal.querySelector('#staffAlertProductResults');
        const selectedEl = offerModal.querySelector('#staffAlertSelectedProduct');
        let searchTimer = null;

        offerModal.querySelectorAll('[data-offer-close]').forEach(function (el) {
            el.addEventListener('click', closeOfferModal);
        });

        searchInput?.addEventListener('input', function () {
            const query = searchInput.value.trim();
            productIdEl.value = '';
            selectedEl.hidden = true;
            if (searchTimer) window.clearTimeout(searchTimer);
            if (query.length < 2) {
                resultsEl.hidden = true;
                resultsEl.innerHTML = '';
                return;
            }
            searchTimer = window.setTimeout(async function () {
                try {
                    const response = await fetch(
                        productSearchUrl + '?q=' + encodeURIComponent(query),
                        {
                            headers: { 'X-Requested-With': 'XMLHttpRequest' },
                            credentials: 'same-origin',
                        },
                    );
                    if (!response.ok) return;
                    const data = await response.json();
                    const results = data.results || [];
                    if (!results.length) {
                        resultsEl.innerHTML = '<p class="staff-alert-offer-no-results">Nema rezultata.</p>';
                        resultsEl.hidden = false;
                        return;
                    }
                    resultsEl.innerHTML = results.map(function (item) {
                        return (
                            '<button type="button" class="staff-alert-offer-result" ' +
                            'data-id="' + item.id + '" data-label="' +
                            escapeAttr(item.label + ' (' + item.price + ' KM)') + '">' +
                            '<span>' + escapeHtml(item.label) + '</span>' +
                            '<span>' + escapeHtml(item.sifra || '') + ' · ' +
                            escapeHtml(item.price) + ' KM</span></button>'
                        );
                    }).join('');
                    resultsEl.hidden = false;
                    resultsEl.querySelectorAll('[data-id]').forEach(function (btn) {
                        btn.addEventListener('click', function () {
                            productIdEl.value = btn.dataset.id;
                            selectedEl.textContent = 'Odabrano: ' + btn.dataset.label;
                            selectedEl.hidden = false;
                            searchInput.value = (btn.dataset.label || '').split(' (')[0];
                            resultsEl.hidden = true;
                        });
                    });
                } catch (err) {
                    /* ignore */
                }
            }, 250);
        });

        offerModal.querySelector('#staffAlertOfferForm')?.addEventListener('submit', async function (e) {
            e.preventDefault();
            const sessionKey = offerModal.querySelector('[data-offer-session]').value;
            const productId = productIdEl.value;
            const discount = offerModal.querySelector('#staffAlertDiscount').value;
            const submitBtn = offerModal.querySelector('button[type="submit"]');
            if (!productId && (!discount || parseFloat(discount) <= 0)) {
                showMiniToast('Unesite popust % ili odaberite artikal.', true);
                return;
            }
            if (submitBtn) submitBtn.disabled = true;
            try {
                const data = await postStaffAction(offerUrl, {
                    session_key: sessionKey,
                    product_id: productId,
                    discount_percent: discount,
                });
                closeOfferModal();
                showMiniToast(data.message || 'Ponuda poslana.');
                updateStickyFlags(sessionKey, {
                    offer_rejected: false,
                    offer_active: true,
                    register_active: false,
                });
            } catch (err) {
                showMiniToast(err.message || 'Slanje ponude nije uspjelo.', true);
            } finally {
                if (submitBtn) submitBtn.disabled = false;
            }
        });

        return offerModal;
    }

    function openOfferModal(sessionKey, visitorName) {
        const modal = ensureOfferModal();
        modal.querySelector('[data-offer-session]').value = sessionKey || '';
        modal.querySelector('[data-offer-visitor]').textContent = visitorName || 'Gost';
        modal.querySelector('#staffAlertProductSearch').value = '';
        modal.querySelector('#staffAlertProductId').value = '';
        modal.querySelector('#staffAlertSelectedProduct').hidden = true;
        modal.querySelector('#staffAlertProductResults').hidden = true;
        modal.querySelector('#staffAlertProductResults').innerHTML = '';
        modal.querySelector('#staffAlertDiscount').value = '10';
        modal.hidden = false;
        document.body.classList.add('popup-open');
        modal.querySelector('#staffAlertProductSearch')?.focus();
    }

    function closeOfferModal() {
        if (!offerModal) return;
        offerModal.hidden = true;
        document.body.classList.remove('popup-open');
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function escapeAttr(value) {
        return escapeHtml(value).replace(/'/g, '&#39;');
    }

    function buildRejectMark(title) {
        const mark = document.createElement('span');
        mark.className = 'staff-alert-toast__reject-x';
        mark.setAttribute('aria-label', title || 'Odbijeno');
        mark.title = title || 'Odbijeno';
        mark.textContent = '×';
        return mark;
    }

    function buildActionSlot(btn, rejected, rejectTitle) {
        const slot = document.createElement('div');
        slot.className = 'staff-alert-toast__action-slot';
        if (rejected) {
            slot.appendChild(buildRejectMark(rejectTitle));
            slot.classList.add('is-rejected');
        }
        slot.appendChild(btn);
        return slot;
    }

    function buildActions(event) {
        if (!event.session_key) return null;
        const canRegister = event.can_register !== false;
        const canOffer = event.can_offer !== false;
        if (!canRegister && !canOffer) return null;

        const wrap = document.createElement('div');
        wrap.className = 'staff-alert-toast__actions';
        wrap.dataset.actionsFor = event.session_key;

        if (canRegister) {
            const regBtn = document.createElement('button');
            regBtn.type = 'button';
            regBtn.className = 'staff-alert-toast__btn staff-alert-toast__btn--register';
            regBtn.textContent = event.register_active ? 'Poslano' : 'Registracija';
            if (event.register_active) regBtn.classList.add('is-sent');
            regBtn.title = 'Pošalji poziv na registraciju (+10%)';
            regBtn.addEventListener('click', async function () {
                const name = event.ime || 'Gost';
                if (!window.confirm('Pošalji poziv na registraciju kupcu „' + name + '”?')) {
                    return;
                }
                regBtn.disabled = true;
                try {
                    const data = await postStaffAction(registerUrl, {
                        session_key: event.session_key,
                    });
                    showMiniToast(data.message || 'Poziv poslan.');
                    regBtn.textContent = 'Poslano';
                    regBtn.classList.add('is-sent');
                    updateStickyFlags(event.session_key, {
                        register_rejected: false,
                        register_active: true,
                        offer_active: false,
                    });
                } catch (err) {
                    showMiniToast(err.message || 'Slanje nije uspjelo.', true);
                    regBtn.disabled = false;
                }
            });
            wrap.appendChild(buildActionSlot(
                regBtn,
                !!event.register_rejected,
                'Posjetilac je odbio poziv na registraciju',
            ));
        }

        if (canOffer) {
            const offerBtn = document.createElement('button');
            offerBtn.type = 'button';
            offerBtn.className = 'staff-alert-toast__btn staff-alert-toast__btn--offer';
            offerBtn.textContent = event.offer_active ? 'Ponuda aktivna' : 'Ponuda';
            if (event.offer_active) offerBtn.classList.add('is-sent');
            offerBtn.title = 'Pošalji ponudu / popust';
            offerBtn.addEventListener('click', function () {
                openOfferModal(event.session_key, event.ime || 'Gost');
            });
            wrap.appendChild(buildActionSlot(
                offerBtn,
                !!event.offer_rejected,
                'Posjetilac je odbio ponudu',
            ));
        }

        return wrap;
    }

    function buildCartSection(event) {
        const items = Array.isArray(event.cart_items) ? event.cart_items : [];
        if (!items.length) return null;

        const box = document.createElement('div');
        box.className = 'staff-alert-toast__cart';
        box.dataset.cartFor = event.session_key || '';

        const head = document.createElement('div');
        head.className = 'staff-alert-toast__cart-head';
        head.textContent = 'Korpa (' + (event.cart_count || items.length) + ')';
        box.appendChild(head);

        const list = document.createElement('ul');
        list.className = 'staff-alert-toast__cart-list';
        items.slice(0, 6).forEach(function (item) {
            const li = document.createElement('li');
            const name = (item && item.name) || 'Artikal';
            const qty = (item && item.qty) || 1;
            const total = (item && item.total) || (item && item.price) || '';
            li.innerHTML =
                '<span class="staff-alert-toast__cart-name">' + escapeHtml(name) + '</span>' +
                '<span class="staff-alert-toast__cart-meta">×' + escapeHtml(qty) +
                (total ? ' · ' + escapeHtml(total) + ' KM' : '') + '</span>';
            list.appendChild(li);
        });
        if (items.length > 6) {
            const more = document.createElement('li');
            more.className = 'staff-alert-toast__cart-more';
            more.textContent = '+' + (items.length - 6) + ' još…';
            list.appendChild(more);
        }
        box.appendChild(list);

        if (event.cart_total) {
            const totalEl = document.createElement('div');
            totalEl.className = 'staff-alert-toast__cart-total';
            totalEl.textContent = 'Ukupno: ' + event.cart_total + ' KM';
            box.appendChild(totalEl);
        }
        return box;
    }

    function buildBadges(event) {
        const wrap = document.createElement('div');
        wrap.className = 'staff-alert-toast__badges';

        const tip = document.createElement('span');
        tip.className = 'staff-alert-toast__tip';
        tip.textContent = tipLabel(event.tip === 'cart' ? 'online' : (event.tip || 'online'));
        wrap.appendChild(tip);

        if (event.has_purchased) {
            const buyer = document.createElement('span');
            buyer.className = 'staff-alert-toast__buyer';
            buyer.textContent = 'Kupac';
            buyer.title = 'Već je kupio/la preko sajta';
            wrap.appendChild(buyer);
        }
        return wrap;
    }

    function renderStickyBody(toast, event) {
        // Očisti dinamičke dijelove (badges, msg, cart, actions) — zadrži close
        Array.from(toast.querySelectorAll(
            '.staff-alert-toast__badges, .staff-alert-toast__title, .staff-alert-toast__msg, ' +
            '.staff-alert-toast__cart, .staff-alert-toast__actions, .staff-alert-toast__meta',
        )).forEach(function (el) {
            el.remove();
        });

        const close = toast.querySelector('.staff-alert-toast__close');
        const badges = buildBadges(event);
        const title = document.createElement('strong');
        title.className = 'staff-alert-toast__title';
        title.textContent = event.naslov || tipLabel('online');

        const msg = document.createElement('p');
        msg.className = 'staff-alert-toast__msg';
        msg.textContent = event.poruka || '';

        const cart = buildCartSection(event);
        const actions = buildActions(event);

        const meta = document.createElement('span');
        meta.className = 'staff-alert-toast__meta staff-alert-toast__meta--live';
        meta.textContent = (event.kreirano ? event.kreirano + ' · ' : '') + 'Još je na sajtu';

        const insertAfter = close || null;
        function appendPart(el) {
            if (!el) return;
            toast.appendChild(el);
        }
        // redoslijed: close (postoji), badges, title, msg, cart, actions, meta
        if (insertAfter && insertAfter.parentNode === toast) {
            // close već na mjestu
        }
        appendPart(badges);
        appendPart(title);
        if (event.poruka) appendPart(msg);
        appendPart(cart);
        if (actions) {
            appendPart(actions);
            toast.classList.add('staff-alert-toast--actionable');
        } else {
            toast.classList.remove('staff-alert-toast--actionable');
        }
        appendPart(meta);

        toast.classList.toggle('staff-alert-toast--has-cart', !!(event.cart_items && event.cart_items.length));
        toast.classList.toggle('staff-alert-toast--is-buyer', !!event.has_purchased);
    }

    function updateStickyFlags(sessionKey, flags) {
        const next = patchStickyOnline(sessionKey, flags || {});
        const toast = findStickyToast(sessionKey);
        if (toast && next) {
            renderStickyBody(toast, next);
        }
    }

    function applyVisitorStateToSticky(sessionKey, state) {
        if (!sessionKey || !state) return;
        const patch = {
            has_purchased: !!state.has_purchased,
            can_register: state.can_register !== false,
            can_offer: state.can_offer !== false,
            offer_rejected: !!state.offer_rejected,
            register_rejected: !!state.register_rejected,
            offer_active: !!state.offer_active,
            register_active: !!state.register_active,
            cart_items: Array.isArray(state.cart_items) ? state.cart_items : [],
            cart_count: state.cart_count || 0,
            cart_total: state.cart_total || '0,00',
        };
        if (state.ime) patch.ime = state.ime;
        if (state.email) patch.email = state.email;
        if (state.grad) patch.grad = state.grad;
        if (state.ime || state.grad) {
            const label = state.ime || 'Gost';
            const city = (state.grad || '').trim();
            patch.poruka = city ? (label + ' (' + city + ') je na sajtu.') : (label + ' je na sajtu.');
        }
        const next = patchStickyOnline(sessionKey, patch);
        const toast = findStickyToast(sessionKey);
        if (toast && next) {
            renderStickyBody(toast, next);
        }
    }

    // Kratki grace za unutrašnju navigaciju; leave cache skida toast odmah
    const offlineMisses = {};
    const OFFLINE_FORGET_MISSES = 3; // ~6s — zaboravi sticky nakon stvarnog odlaska

    function hideOfflineStickyToasts(online) {
        ensureStack();
        if (!stack) return;
        stack.querySelectorAll('.staff-alert-toast[data-sticky-online="1"]').forEach(function (toast) {
            const sessionKey = toast.dataset.sessionKey || '';
            if (!sessionKey || !online.has(sessionKey)) {
                removeToastEl(toast);
            }
        });
    }

    function syncStickyOnlineToasts(onlineSessions, visitorStates) {
        const online = new Set(onlineSessions || []);
        const stickyMap = getStickyOnlineMap();
        const dismissed = getDismissedOnlineSet();
        const states = visitorStates || {};
        let mapChanged = false;
        let dismissedChanged = false;

        // ODMAH skloni toast ako kupac više nije u online listi
        hideOfflineStickyToasts(online);

        Object.keys(stickyMap).forEach(function (sessionKey) {
            if (online.has(sessionKey)) {
                offlineMisses[sessionKey] = 0;
                return;
            }

            offlineMisses[sessionKey] = (offlineMisses[sessionKey] || 0) + 1;
            const toast = findStickyToast(sessionKey);
            if (toast) removeToastEl(toast);

            if (offlineMisses[sessionKey] >= OFFLINE_FORGET_MISSES) {
                delete stickyMap[sessionKey];
                delete offlineMisses[sessionKey];
                mapChanged = true;
                if (dismissed.has(sessionKey)) {
                    dismissed.delete(sessionKey);
                    dismissedChanged = true;
                }
            }
        });

        if (mapChanged) setStickyOnlineMap(stickyMap);
        if (dismissedChanged) setDismissedOnlineSet(dismissed);

        // Rehydrate samo ako je SESIJA STVARNO online
        const currentMap = getStickyOnlineMap();
        Object.keys(currentMap).forEach(function (sessionKey) {
            if (!online.has(sessionKey) || dismissed.has(sessionKey)) return;
            offlineMisses[sessionKey] = 0;
            if (states[sessionKey]) {
                applyVisitorStateToSticky(sessionKey, states[sessionKey]);
            }
            if (!findStickyToast(sessionKey)) {
                const payload = getStickyOnlineMap()[sessionKey] || currentMap[sessionKey];
                showToast(Object.assign({}, payload, { sticky: true, tip: 'online' }), {
                    skipRemember: true,
                });
            }
        });
    }

    function handleCartEvent(event) {
        const sessionKey = event && event.session_key;
        if (!sessionKey) return;

        // Korpa ide u postojeći sticky prozor — bez novog toasta
        const statePatch = {
            cart_items: Array.isArray(event.cart_items) ? event.cart_items : undefined,
            cart_count: event.cart_count,
            cart_total: event.cart_total,
            has_purchased: event.has_purchased,
            offer_rejected: event.offer_rejected,
            register_rejected: event.register_rejected,
            offer_active: event.offer_active,
            register_active: event.register_active,
            can_register: event.can_register,
            can_offer: event.can_offer !== false,
            ime: event.ime,
            email: event.email,
            grad: event.grad,
            kreirano: event.kreirano,
        };
        Object.keys(statePatch).forEach(function (key) {
            if (statePatch[key] === undefined) delete statePatch[key];
        });

        const existing = getStickyOnlineMap()[sessionKey];
        if (!existing && getDismissedOnlineSet().has(sessionKey)) {
            // Ručno zatvoren — ne otvaraj novi popup samo zbog korpe
            return;
        }

        if (!existing) {
            const created = stickyPayloadFromEvent(Object.assign({}, event, {
                tip: 'online',
                naslov: 'Posjetilac online',
                poruka: event.poruka || ((event.ime || 'Gost') + ' je dodao/la u korpu.'),
                sticky: true,
            }));
            rememberStickyOnline(created);
            showToast(created, { skipRemember: true });
            return;
        }

        const next = patchStickyOnline(sessionKey, statePatch);
        // Ako nema cart_items u eventu, zadrži stare i prikaži poruku u title-u
        if (next && event.poruka) {
            // blagi update poruke nije obavezan
        }
        let toast = findStickyToast(sessionKey);
        if (!toast) {
            showToast(Object.assign({}, next, { sticky: true, tip: 'online' }), {
                skipRemember: true,
            });
            return;
        }
        renderStickyBody(toast, next || existing);
        // Kratki flash da se vidi update korpe
        toast.classList.add('staff-alert-toast--cart-pulse');
        window.setTimeout(function () {
            toast.classList.remove('staff-alert-toast--cart-pulse');
        }, 700);
    }

    function showToast(event, options) {
        options = options || {};
        if (!event) return;

        // Korpa: uvijek u postojeći sticky prozor
        if (event.tip === 'cart') {
            handleCartEvent(event);
            return;
        }

        const host = ensureStack();
        const sessionKey = event.session_key || '';
        const isStickyOnline = !!(
            (event.sticky || event.tip === 'online')
            && sessionKey
        );

        if (isStickyOnline) {
            if (getDismissedOnlineSet().has(sessionKey) && !options.force) {
                return;
            }
            const existingToast = findStickyToast(sessionKey);
            if (existingToast) {
                if (!options.skipRemember) rememberStickyOnline(event);
                const payload = getStickyOnlineMap()[sessionKey] || stickyPayloadFromEvent(event);
                renderStickyBody(existingToast, payload);
                return;
            }
            if (!options.skipRemember) {
                rememberStickyOnline(event);
            }
        }

        const toast = document.createElement('article');
        toast.className = 'staff-alert-toast staff-alert-toast--' + (event.tip || 'online');
        toast.setAttribute('role', 'status');
        if (isStickyOnline) {
            toast.dataset.stickyOnline = '1';
            toast.dataset.sessionKey = sessionKey;
            toast.classList.add('staff-alert-toast--sticky');
        }

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'staff-alert-toast__close';
        close.setAttribute('aria-label', 'Zatvori');
        close.textContent = '×';
        close.addEventListener('click', function () {
            if (isStickyOnline) {
                dismissStickyOnline(sessionKey);
            }
            removeToastEl(toast);
        });
        toast.appendChild(close);

        if (isStickyOnline) {
            const payload = getStickyOnlineMap()[sessionKey] || stickyPayloadFromEvent(event);
            renderStickyBody(toast, payload);
        } else {
            const tip = document.createElement('span');
            tip.className = 'staff-alert-toast__tip';
            tip.textContent = tipLabel(event.tip);

            const title = document.createElement('strong');
            title.className = 'staff-alert-toast__title';
            title.textContent = event.naslov || tipLabel(event.tip);

            const msg = document.createElement('p');
            msg.className = 'staff-alert-toast__msg';
            msg.textContent = event.poruka || '';

            const meta = document.createElement('span');
            meta.className = 'staff-alert-toast__meta';
            meta.textContent = event.kreirano || '';

            toast.appendChild(tip);
            toast.appendChild(title);
            if (event.poruka) toast.appendChild(msg);
            if (event.kreirano) toast.appendChild(meta);
        }

        host.appendChild(toast);
        requestAnimationFrame(function () {
            toast.classList.add('is-visible');
        });

        if (!isStickyOnline) {
            window.setTimeout(function () {
                removeToastEl(toast);
            }, 9000);
        }
    }

    async function poll() {
        if (document.hidden) return;
        try {
            const url = pollUrl + (pollUrl.indexOf('?') >= 0 ? '&' : '?') +
                'since=' + encodeURIComponent(sinceId);
            const response = await fetch(url, {
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
            });
            if (!response.ok) return;
            const data = await response.json();
            if (!data || !data.ok) return;

            const nextId = parseInt(data.latest_id || sinceId, 10) || sinceId;
            const events = data.events || [];
            if (events.length) {
                events.forEach(function (event) {
                    showToast(event);
                });
            }
            syncStickyOnlineToasts(
                data.online_sessions || [],
                data.visitor_states || {},
            );
            if (nextId > sinceId) {
                sinceId = nextId;
                try {
                    sessionStorage.setItem(storageKey, String(sinceId));
                } catch (err) {
                    /* ignore */
                }
            }
        } catch (err) {
            /* tiho */
        }
    }

    poll().finally(function () {
        window.setInterval(poll, pollMs);
    });

    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) {
            poll();
        }
    });
})();
