/**
 * Live toast obavijesti za superusere + brze akcije (registracija / ponuda).
 */
(function () {
    const root = document.getElementById('staffAlertsRoot');
    if (!root) return;

    const pollUrl = root.dataset.pollUrl || '/nalog/uzivo-obavijesti/';
    const registerUrl = root.dataset.registerUrl || '/nalog/uzivo-analitika/registracija/';
    const offerUrl = root.dataset.offerUrl || '/nalog/uzivo-analitika/ponuda/';
    const productSearchUrl = root.dataset.productSearchUrl || '/nalog/pretraga-artikala/';
    const pollMs = 5000;
    const storageKey = 'staff_alerts_since_id';
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

    function buildActions(event, toast) {
        if (!event.can_offer && !event.can_register) return null;
        if (!event.session_key) return null;

        const wrap = document.createElement('div');
        wrap.className = 'staff-alert-toast__actions';

        if (event.can_register) {
            const regBtn = document.createElement('button');
            regBtn.type = 'button';
            regBtn.className = 'staff-alert-toast__btn staff-alert-toast__btn--register';
            regBtn.textContent = 'Registracija';
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
                } catch (err) {
                    showMiniToast(err.message || 'Slanje nije uspjelo.', true);
                    regBtn.disabled = false;
                }
            });
            wrap.appendChild(regBtn);
        }

        if (event.can_offer) {
            const offerBtn = document.createElement('button');
            offerBtn.type = 'button';
            offerBtn.className = 'staff-alert-toast__btn staff-alert-toast__btn--offer';
            offerBtn.textContent = 'Ponuda';
            offerBtn.title = 'Pošalji ponudu / popust';
            offerBtn.addEventListener('click', function () {
                openOfferModal(event.session_key, event.ime || 'Gost');
            });
            wrap.appendChild(offerBtn);
        }

        return wrap;
    }

    function showToast(event) {
        const host = ensureStack();
        const toast = document.createElement('article');
        toast.className = 'staff-alert-toast staff-alert-toast--' + (event.tip || 'online');
        toast.setAttribute('role', 'status');

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

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'staff-alert-toast__close';
        close.setAttribute('aria-label', 'Zatvori');
        close.textContent = '×';
        close.addEventListener('click', function () {
            toast.classList.remove('is-visible');
            window.setTimeout(function () {
                toast.remove();
            }, 220);
        });

        toast.appendChild(close);
        toast.appendChild(tip);
        toast.appendChild(title);
        if (event.poruka) toast.appendChild(msg);

        const actions = buildActions(event, toast);
        if (actions) {
            toast.appendChild(actions);
            toast.classList.add('staff-alert-toast--actionable');
        }

        if (event.kreirano) toast.appendChild(meta);

        host.appendChild(toast);
        requestAnimationFrame(function () {
            toast.classList.add('is-visible');
        });

        // Toasts s akcijama traju duže da stigneš kliknuti
        const ttl = actions ? 22000 : 9000;
        window.setTimeout(function () {
            if (!toast.isConnected) return;
            toast.classList.remove('is-visible');
            window.setTimeout(function () {
                toast.remove();
            }, 220);
        }, ttl);
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
                events.forEach(showToast);
            }
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
