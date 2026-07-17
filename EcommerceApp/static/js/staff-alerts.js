/**
 * Live toast obavijesti za superusere.
 *
 * Samo akcioni događaji (NE „kupac je online”):
 * - Dodavanje u korpu
 * - Prihvaćena popup / AI / staff ponuda
 * - Registracija
 * - Kupovina (celebration)
 * Klik → uživo analitika / online narudžbe.
 */
(function () {
    const root = document.getElementById('staffAlertsRoot');
    if (!root) return;

    const pollUrl = root.dataset.pollUrl || '/nalog/uzivo-obavijesti/';
    const analyticsUrl = root.dataset.analyticsUrl || '/nalog/uzivo-analitika/';
    // Na uživo analitici: bez online toast-a, ali celebration za novu narudžbu i badge rade.
    const path = window.location.pathname || '';
    const quietMode = (
        path.indexOf('/nalog/uzivo-analitika') === 0
        || root.dataset.disableToasts === '1'
    );

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
        if (tip === 'offer') return 'Prihvaćena ponuda';
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
     * Event toasti: korpa, ponuda, registracija, kupovina.
     * Online summary toast je isključen.
     */
    function goToOnlineOrders(event) {
        if (event) {
            event.preventDefault();
            event.stopPropagation();
        }
        window.location.href = '/nalog/online-narudzbe/';
    }

    function updateNewOrdersBadge(count) {
        const n = Math.max(0, parseInt(count, 10) || 0);
        document.querySelectorAll('#adminNewOrdersBadge, [data-new-orders-badge]').forEach(function (badge) {
            badge.dataset.count = String(n);
            badge.textContent = String(n);
            badge.setAttribute('aria-label', n + ' novih narudžbi');
            if (n > 0) {
                badge.hidden = false;
                badge.classList.add('is-visible');
            } else {
                badge.hidden = true;
                badge.classList.remove('is-visible');
            }
        });
        try {
            window.dispatchEvent(new CustomEvent('staff-new-orders-count', { detail: { count: n } }));
        } catch (err) {
            /* ignore */
        }
    }

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function showOrderCelebration(event) {
        if (!event) return;
        // Jedan celebration u isto vrijeme
        const existing = document.getElementById('staffOrderCelebration');
        if (existing) existing.remove();

        const orderNo = event.order_number || '';
        const total = event.order_total || '';
        const ime = event.ime || 'Kupac';
        const grad = event.grad || '';
        const email = event.email || '';

        const overlay = document.createElement('div');
        overlay.id = 'staffOrderCelebration';
        overlay.className = 'staff-order-celebration';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-labelledby', 'staffOrderCelebrationTitle');

        const confetti = [];
        for (let i = 0; i < 36; i++) {
            confetti.push('<span class="staff-order-confetti" style="--i:' + i + '"></span>');
        }

        overlay.innerHTML =
            '<div class="staff-order-celebration__backdrop" data-order-celeb-close></div>' +
            '<div class="staff-order-celebration__card">' +
            '<div class="staff-order-celebration__confetti" aria-hidden="true">' +
            confetti.join('') +
            '</div>' +
            '<button type="button" class="staff-order-celebration__close" data-order-celeb-close aria-label="Zatvori">×</button>' +
            '<div class="staff-order-celebration__burst" aria-hidden="true">🎉</div>' +
            '<p class="staff-order-celebration__kicker">Nova narudžba na sajtu</p>' +
            '<h2 id="staffOrderCelebrationTitle" class="staff-order-celebration__title">Čestitamo!</h2>' +
            '<p class="staff-order-celebration__lead">Stigla je nova online narudžba — odlična vijest!</p>' +
            '<div class="staff-order-celebration__box">' +
            (orderNo
                ? '<div class="staff-order-celebration__row"><span>Broj</span><strong>#' +
                  escapeHtml(orderNo) + '</strong></div>'
                : '') +
            (total
                ? '<div class="staff-order-celebration__row"><span>Iznos</span><strong>' +
                  escapeHtml(total) + ' KM</strong></div>'
                : '') +
            '<div class="staff-order-celebration__row"><span>Kupac</span><strong>' +
            escapeHtml(ime) +
            (grad ? ' · ' + escapeHtml(grad) : '') +
            '</strong></div>' +
            (email
                ? '<div class="staff-order-celebration__row"><span>Email</span><strong>' +
                  escapeHtml(email) + '</strong></div>'
                : '') +
            '</div>' +
            '<div class="staff-order-celebration__actions">' +
            '<button type="button" class="staff-order-celebration__btn staff-order-celebration__btn--primary" data-order-celeb-orders>' +
            'Otvori online narudžbe</button>' +
            '<button type="button" class="staff-order-celebration__btn staff-order-celebration__btn--ghost" data-order-celeb-close>' +
            'Zatvori</button>' +
            '</div>' +
            '<p class="staff-order-celebration__hint">Zeleni broj pored „Online narudžbe” pokazuje koliko novih čeka.</p>' +
            '</div>';

        document.body.appendChild(overlay);
        document.body.classList.add('staff-order-celebration-open');
        requestAnimationFrame(function () {
            overlay.classList.add('is-visible');
        });

        function closeCeleb() {
            overlay.classList.remove('is-visible');
            document.body.classList.remove('staff-order-celebration-open');
            window.setTimeout(function () {
                overlay.remove();
            }, 280);
        }

        overlay.querySelectorAll('[data-order-celeb-close]').forEach(function (el) {
            el.addEventListener('click', closeCeleb);
        });
        overlay.querySelector('[data-order-celeb-orders]')?.addEventListener('click', function (e) {
            goToOnlineOrders(e);
        });

        // Auto-zatvori poslije 18s (može i ručno)
        window.setTimeout(function () {
            if (document.getElementById('staffOrderCelebration') === overlay) {
                closeCeleb();
            }
        }, 18000);
    }

    function showEventToast(event) {
        if (!event) return;
        const tip = event.tip || '';
        // Dozvoljeno: cart, offer, register, purchase — NE online
        if (tip !== 'register' && tip !== 'purchase' && tip !== 'cart' && tip !== 'offer') {
            return;
        }

        // Kupovina: veliki celebration popup (uvijek, i u quiet mode)
        if (tip === 'purchase') {
            showOrderCelebration(event);
        }

        // Na uživo analitici: korpa/ponuda/kupovina (ne spam)
        if (quietMode && tip !== 'offer' && tip !== 'purchase' && tip !== 'cart') {
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
            (event.naslov || tipLabel(tip)) + ' — otvori online narudžbe',
        );
        toast.title = tip === 'purchase'
            ? 'Klikni da otvoriš online narudžbe'
            : 'Klikni da otvoriš uživo analitiku';

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
        if (tip === 'purchase') {
            const cta = toast.querySelector('.staff-alert-toast__cta');
            if (cta) cta.textContent = 'Otvori online narudžbe →';
            const meta = toast.querySelector('.staff-alert-toast__meta');
            if (meta && !meta.classList.contains('staff-alert-toast__meta--live')) {
                meta.textContent = (event.kreirano || '') + ' · klikni za narudžbe';
            }
        }

        toast.addEventListener('click', function (e) {
            if (e.target && e.target.closest && e.target.closest('.staff-alert-toast__close')) {
                return;
            }
            if (tip === 'purchase') {
                goToOnlineOrders(e);
            } else {
                goToAnalytics(e);
            }
        });
        toast.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' || e.key === ' ') {
                if (tip === 'purchase') {
                    goToOnlineOrders(e);
                } else {
                    goToAnalytics(e);
                }
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

            // Online sticky summary isključen — ne prikazuj „kupac je na sajtu”

            if (typeof data.new_orders_count !== 'undefined') {
                updateNewOrdersBadge(data.new_orders_count);
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
