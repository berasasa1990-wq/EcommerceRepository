/**
 * Live toast obavijesti za superusere.
 *
 * - Online: JEDAN sticky popup dok ima ≥1 kupca na sajtu (ne po osobi).
 * - Registracija / kupovina: poseban popup s detaljima.
 * - Klik → uživo analitika (akcije se rade tamo).
 */
(function () {
    const root = document.getElementById('staffAlertsRoot');
    if (!root) return;

    const pollUrl = root.dataset.pollUrl || '/nalog/uzivo-obavijesti/';
    const analyticsUrl = root.dataset.analyticsUrl || '/nalog/uzivo-analitika/';
    // Na uživo analitici već imaš live pregled — ne prikazuj toast popup-e.
    const path = window.location.pathname || '';
    if (
        path.indexOf('/nalog/uzivo-analitika') === 0
        || root.dataset.disableToasts === '1'
    ) {
        return;
    }

    const pollMs = 2000;
    const storageKey = 'staff_alerts_since_id';
    const dismissedOnlineKey = 'staff_alerts_online_summary_dismissed';
    let sinceId = 0;
    let stack = null;
    let onlineDismissed = false;

    try {
        const saved = parseInt(sessionStorage.getItem(storageKey) || '0', 10);
        if (!Number.isNaN(saved) && saved > 0) {
            sinceId = saved;
        }
    } catch (err) {
        /* ignore */
    }

    try {
        onlineDismissed = sessionStorage.getItem(dismissedOnlineKey) === '1';
    } catch (err) {
        onlineDismissed = false;
    }

    function setOnlineDismissed(value) {
        onlineDismissed = !!value;
        try {
            if (onlineDismissed) {
                sessionStorage.setItem(dismissedOnlineKey, '1');
            } else {
                sessionStorage.removeItem(dismissedOnlineKey);
            }
        } catch (err) {
            /* ignore */
        }
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

    function removeToastEl(toast) {
        if (!toast || !toast.isConnected) return;
        toast.classList.remove('is-visible');
        window.setTimeout(function () {
            toast.remove();
        }, 220);
    }

    function goToAnalytics(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        window.location.href = analyticsUrl;
    }

    function tipLabel(tip) {
        if (tip === 'online') return 'Online';
        if (tip === 'cart') return 'Korpa';
        if (tip === 'register') return 'Registracija';
        if (tip === 'purchase') return 'Kupovina';
        return 'Obavijest';
    }

    function onlineSummaryText(count) {
        const n = Math.max(0, parseInt(count, 10) || 0);
        if (n === 1) {
            return {
                naslov: 'Kupac na sajtu',
                poruka: '1 kupac je trenutno na sajtu. Otvori uživo analitiku za pregled i ponude.',
            };
        }
        return {
            naslov: 'Kupci na sajtu',
            poruka: n + ' kupaca je trenutno na sajtu. Otvori uživo analitiku za pregled i ponude.',
        };
    }

    function renderToastBody(toast, event, options) {
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

        if (options.count && options.count > 1) {
            const countBadge = document.createElement('span');
            countBadge.className = 'staff-alert-toast__buyer';
            countBadge.textContent = String(options.count);
            countBadge.title = 'Broj online posjetilaca';
            badges.appendChild(countBadge);
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
            meta.textContent = 'Aktivno · klikni za analitiku';
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

    function findOnlineSummaryToast() {
        ensureStack();
        if (!stack) return null;
        return stack.querySelector('.staff-alert-toast[data-online-summary="1"]');
    }

    function hideOnlineSummaryToast() {
        const toast = findOnlineSummaryToast();
        if (toast) removeToastEl(toast);
    }

    function showOrUpdateOnlineSummary(onlineCount) {
        const count = Math.max(0, parseInt(onlineCount, 10) || 0);
        if (count <= 0) {
            // Nema više online — dozvoli da se toast ponovo pojavi sljedeći put
            setOnlineDismissed(false);
            hideOnlineSummaryToast();
            return;
        }
        if (onlineDismissed) return;

        const summary = onlineSummaryText(count);
        let toast = findOnlineSummaryToast();
        if (toast) {
            toast.dataset.onlineCount = String(count);
            renderToastBody(toast, {
                tip: 'online',
                naslov: summary.naslov,
                poruka: summary.poruka,
            }, { sticky: true, count: count });
            return;
        }

        const host = ensureStack();
        toast = document.createElement('article');
        toast.className = 'staff-alert-toast staff-alert-toast--online staff-alert-toast--sticky staff-alert-toast--clickable';
        toast.dataset.onlineSummary = '1';
        toast.dataset.onlineCount = String(count);
        toast.setAttribute('role', 'link');
        toast.setAttribute('tabindex', '0');
        toast.setAttribute('aria-label', summary.naslov + ' — otvori uživo analitiku');
        toast.title = 'Klikni da otvoriš uživo analitiku';

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'staff-alert-toast__close';
        close.setAttribute('aria-label', 'Zatvori');
        close.textContent = '×';
        close.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            setOnlineDismissed(true);
            removeToastEl(toast);
        });
        toast.appendChild(close);

        renderToastBody(toast, {
            tip: 'online',
            naslov: summary.naslov,
            poruka: summary.poruka,
        }, { sticky: true, count: count });

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
    }

    /**
     * Event toasti: samo registracija i kupovina.
     * Online / korpa se ne prikazuju pojedinačno — ide jedan summary toast.
     */
    function showEventToast(event) {
        if (!event) return;
        const tip = event.tip || '';
        if (tip !== 'register' && tip !== 'purchase') {
            return;
        }

        const host = ensureStack();
        const toast = document.createElement('article');
        toast.className = 'staff-alert-toast staff-alert-toast--' + tip +
            ' staff-alert-toast--clickable';
        toast.setAttribute('role', 'link');
        toast.setAttribute('tabindex', '0');
        toast.setAttribute(
            'aria-label',
            (event.naslov || tipLabel(tip)) + ' — otvori uživo analitiku',
        );
        toast.title = 'Klikni da otvoriš uživo analitiku';

        const close = document.createElement('button');
        close.type = 'button';
        close.className = 'staff-alert-toast__close';
        close.setAttribute('aria-label', 'Zatvori');
        close.textContent = '×';
        close.addEventListener('click', function (e) {
            e.preventDefault();
            e.stopPropagation();
            removeToastEl(toast);
        });
        toast.appendChild(close);

        renderToastBody(toast, event, { sticky: false });

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

        window.setTimeout(function () {
            removeToastEl(toast);
        }, 12000);
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
                    showEventToast(event);
                });
            }

            const onlineSessions = data.online_sessions || [];
            showOrUpdateOnlineSummary(onlineSessions.length);

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

    // Očisti staru per-session sticky mapu (više se ne koristi)
    try {
        sessionStorage.removeItem('staff_alerts_sticky_online');
        sessionStorage.removeItem('staff_alerts_dismissed_online');
    } catch (err) {
        /* ignore */
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
