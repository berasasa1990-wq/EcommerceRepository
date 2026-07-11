/**
 * Live toast obavijesti za superusere.
 * Samo notifikacija da je kupac na sajtu — klik vodi u uživo analitiku.
 * Akcije (ponuda, registracija…) rade se tamo.
 */
(function () {
    const root = document.getElementById('staffAlertsRoot');
    if (!root) return;

    const pollUrl = root.dataset.pollUrl || '/nalog/uzivo-obavijesti/';
    const analyticsUrl = root.dataset.analyticsUrl || '/nalog/uzivo-analitika/';
    const pollMs = 2000;
    const storageKey = 'staff_alerts_since_id';
    const stickyOnlineKey = 'staff_alerts_sticky_online';
    const dismissedOnlineKey = 'staff_alerts_dismissed_online';
    let sinceId = 0;
    let stack = null;

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
        const ime = (event && event.ime) || '';
        const grad = (event && event.grad) || '';
        let poruka = (event && event.poruka) || '';
        if (!poruka) {
            const label = ime || 'Gost';
            poruka = grad
                ? (label + ' (' + grad + ') je na sajtu.')
                : (label + ' je na sajtu.');
        }
        return {
            tip: 'online',
            naslov: (event && event.naslov) || 'Kupac na sajtu',
            poruka: poruka,
            ime: ime,
            email: (event && event.email) || '',
            grad: grad,
            session_key: event && event.session_key,
            sticky: true,
            has_purchased: !!(event && event.has_purchased),
            kreirano: (event && event.kreirano) || '',
        };
    }

    function rememberStickyOnline(event) {
        if (!event || !event.session_key) return;
        const map = getStickyOnlineMap();
        const prev = map[event.session_key] || {};
        const next = stickyPayloadFromEvent(event);
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
            naslov: 'Kupac na sajtu',
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

    function goToAnalytics(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        window.location.href = analyticsUrl;
    }

    function renderSimpleBody(toast, event, options) {
        options = options || {};
        Array.from(toast.querySelectorAll(
            '.staff-alert-toast__badges, .staff-alert-toast__title, .staff-alert-toast__msg, ' +
            '.staff-alert-toast__meta, .staff-alert-toast__cta',
        )).forEach(function (el) {
            el.remove();
        });

        const badges = document.createElement('div');
        badges.className = 'staff-alert-toast__badges';

        const tip = document.createElement('span');
        tip.className = 'staff-alert-toast__tip';
        tip.textContent = tipLabel(event.tip || 'online');
        badges.appendChild(tip);

        if (event.has_purchased) {
            const buyer = document.createElement('span');
            buyer.className = 'staff-alert-toast__buyer';
            buyer.textContent = 'Kupac';
            buyer.title = 'Već je kupio/la preko sajta';
            badges.appendChild(buyer);
        }

        const title = document.createElement('strong');
        title.className = 'staff-alert-toast__title';
        title.textContent = event.naslov || tipLabel(event.tip || 'online');

        const msg = document.createElement('p');
        msg.className = 'staff-alert-toast__msg';
        msg.textContent = event.poruka || '';

        const meta = document.createElement('span');
        meta.className = 'staff-alert-toast__meta' +
            (options.sticky ? ' staff-alert-toast__meta--live' : '');
        if (options.sticky) {
            meta.textContent = 'Na sajtu · klikni za analitiku';
        } else {
            meta.textContent = event.kreirano
                ? (event.kreirano + ' · klikni za analitiku')
                : 'Klikni za uživo analitiku';
        }

        const cta = document.createElement('span');
        cta.className = 'staff-alert-toast__cta';
        cta.textContent = 'Otvori uživo analitiku →';

        toast.appendChild(badges);
        toast.appendChild(title);
        if (event.poruka) toast.appendChild(msg);
        toast.appendChild(meta);
        toast.appendChild(cta);
    }

    function applyVisitorStateToSticky(sessionKey, state) {
        if (!sessionKey || !state) return;
        const patch = {
            has_purchased: !!state.has_purchased,
        };
        if (state.ime) patch.ime = state.ime;
        if (state.email) patch.email = state.email;
        if (state.grad) patch.grad = state.grad;
        if (state.ime || state.grad) {
            const label = state.ime || 'Gost';
            const city = (state.grad || '').strip();
            patch.poruka = city
                ? (label + ' (' + city + ') je na sajtu.')
                : (label + ' je na sajtu.');
            patch.naslov = 'Kupac na sajtu';
        }
        const next = patchStickyOnline(sessionKey, patch);
        const toast = findStickyToast(sessionKey);
        if (toast && next) {
            renderSimpleBody(toast, next, { sticky: true });
        }
    }

    const offlineMisses = {};
    const OFFLINE_FORGET_MISSES = 3;

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

    function showToast(event, options) {
        options = options || {};
        if (!event) return;

        const host = ensureStack();
        const sessionKey = event.session_key || '';
        // Online + korpa: sticky dok je na sajtu (samo obavijest)
        const isStickyOnline = !!(
            (event.sticky || event.tip === 'online' || event.tip === 'cart')
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
                // Korpa event: samo osvježi poruku da je još na sajtu (bez detalja)
                if (event.tip === 'cart' && event.poruka) {
                    payload.poruka = event.poruka;
                }
                renderSimpleBody(existingToast, payload, { sticky: true });
                return;
            }
            if (!options.skipRemember) {
                rememberStickyOnline(Object.assign({}, event, {
                    tip: 'online',
                    naslov: event.tip === 'cart'
                        ? 'Kupac na sajtu'
                        : (event.naslov || 'Kupac na sajtu'),
                }));
            }
        }

        const toast = document.createElement('article');
        toast.className = 'staff-alert-toast staff-alert-toast--' +
            (isStickyOnline ? 'online' : (event.tip || 'online'));
        toast.classList.add('staff-alert-toast--clickable');
        toast.setAttribute('role', 'link');
        toast.setAttribute('tabindex', '0');
        toast.setAttribute(
            'aria-label',
            (event.naslov || tipLabel(event.tip)) + ' — otvori uživo analitiku',
        );
        toast.title = 'Klikni da otvoriš uživo analitiku';

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
        close.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            if (isStickyOnline) {
                dismissStickyOnline(sessionKey);
            }
            removeToastEl(toast);
        });
        toast.appendChild(close);

        if (isStickyOnline) {
            const payload = getStickyOnlineMap()[sessionKey] || stickyPayloadFromEvent(event);
            renderSimpleBody(toast, payload, { sticky: true });
        } else {
            renderSimpleBody(toast, event, { sticky: false });
        }

        toast.addEventListener('click', function (e) {
            if (e.target && e.target.closest && e.target.closest('.staff-alert-toast__close')) {
                return;
            }
            goToAnalytics(e);
        });
        toast.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                goToAnalytics(e);
            }
        });

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
