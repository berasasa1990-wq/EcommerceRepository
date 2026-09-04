/**
 * Live obavijesti za superusere.
 * Samo nova narudžba — korpa i registracija se ne prikazuju.
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

    const pollMs = 5000;
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

    function goToOrder(url, clickEvent) {
        if (clickEvent) {
            clickEvent.preventDefault();
            clickEvent.stopPropagation();
        }
        window.location.href = url || '/nalog/online-narudzbe/';
    }

    function pendingOrdersCopy(count) {
        const n = Math.max(1, parseInt(count, 10) || 1);
        if (n === 1) return 'Imate 1 novu narudžbu koja čeka obradu.';
        if (n >= 2 && n <= 4) return 'Imate ' + n + ' nove narudžbe koje čekaju obradu.';
        return 'Imate ' + n + ' novih narudžbi koje čekaju obradu.';
    }

    function rowIcon(kind) {
        if (kind === 'tag') {
            return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8z"/><circle cx="7.5" cy="7.5" r="1.2" fill="currentColor" stroke="none"/></svg>';
        }
        if (kind === 'user') {
            return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="8" r="3.2"/><path d="M5 19c1.4-3.2 3.8-4.8 7-4.8s5.6 1.6 7 4.8"/></svg>';
        }
        if (kind === 'date') {
            return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="4" y="5" width="16" height="16" rx="2"/><path d="M8 3v4M16 3v4M4 10h16"/></svg>';
        }
        if (kind === 'total') {
            return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><circle cx="12" cy="12" r="9"/><path d="M12 7v10M15 9.2c-.7-.8-1.7-1.2-3-1.2-1.8 0-3 1-3 2.3 0 3.2 6 1.4 6 4.4 0 1.4-1.3 2.3-3.2 2.3-1.4 0-2.5-.5-3.2-1.3"/></svg>';
        }
        return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M3 7h13l3 5H8"/><path d="M3 7v11h13"/><circle cx="7.5" cy="19.5" r="1.5"/><circle cx="16.5" cy="19.5" r="1.5"/></svg>';
    }

    function detailRow(kind, label, value, accent) {
        if (!value) return '';
        return (
            '<div class="staff-order-celebration__row">' +
            '<span class="staff-order-celebration__label">' +
            '<span class="staff-order-celebration__ico" aria-hidden="true">' + rowIcon(kind) + '</span>' +
            escapeHtml(label) +
            '</span>' +
            '<strong class="staff-order-celebration__value' +
            (accent ? ' is-accent' : '') +
            '">' + escapeHtml(value) + '</strong>' +
            '</div>'
        );
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

    function showOrderCelebration(event, pendingCount) {
        if (!event) return;
        const existing = document.getElementById('staffOrderCelebration');
        if (existing) existing.remove();

        const orderNo = event.order_number || '';
        const total = event.order_total || '';
        const ime = event.ime || 'Kupac';
        const dateLabel = event.order_date || '';
        const shipping = event.shipping || 'Brza dostava';
        const orderUrl = event.order_url || (orderNo
            ? '/nalog/provjera-narudzbi/' + encodeURIComponent(orderNo) + '/'
            : '/nalog/online-narudzbe/');
        const pending = Math.max(1, parseInt(pendingCount, 10) || 1);
        const totalLabel = total ? (String(total).indexOf('KM') >= 0 ? total : total + ' KM') : '';

        const overlay = document.createElement('div');
        overlay.id = 'staffOrderCelebration';
        overlay.className = 'staff-order-celebration';
        overlay.setAttribute('role', 'dialog');
        overlay.setAttribute('aria-modal', 'true');
        overlay.setAttribute('aria-labelledby', 'staffOrderCelebrationTitle');

        overlay.innerHTML =
            '<div class="staff-order-celebration__backdrop" data-order-celeb-close></div>' +
            '<div class="staff-order-celebration__card">' +
            '<button type="button" class="staff-order-celebration__close" data-order-celeb-close aria-label="Zatvori">×</button>' +
            '<div class="staff-order-celebration__hero" aria-hidden="true">' +
            '<span class="staff-order-celebration__cart">' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7">' +
            '<circle cx="9" cy="20" r="1.4"/><circle cx="18" cy="20" r="1.4"/>' +
            '<path d="M3 4h2l2.2 11.2a2 2 0 0 0 2 1.6h8.4a2 2 0 0 0 2-1.5L21 8H7"/>' +
            '</svg>' +
            '<span class="staff-order-celebration__badge">' + pending + '</span>' +
            '</span>' +
            '</div>' +
            '<h2 id="staffOrderCelebrationTitle" class="staff-order-celebration__title">Stigla je nova narudžba!</h2>' +
            '<p class="staff-order-celebration__lead">' + escapeHtml(pendingOrdersCopy(pending)) + '</p>' +
            '<div class="staff-order-celebration__box">' +
            detailRow('tag', 'Broj narudžbe:', orderNo ? '#' + orderNo : '', true) +
            detailRow('user', 'Kupac:', ime, false) +
            detailRow('date', 'Datum:', dateLabel, false) +
            detailRow('total', 'Ukupno:', totalLabel, true) +
            detailRow('ship', 'Način dostave:', shipping, false) +
            '</div>' +
            '<div class="staff-order-celebration__actions">' +
            '<button type="button" class="staff-order-celebration__btn staff-order-celebration__btn--ghost" data-order-celeb-close>Kasnije</button>' +
            '<button type="button" class="staff-order-celebration__btn staff-order-celebration__btn--primary" data-order-celeb-orders>' +
            '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true">' +
            '<path d="M8 7h8M8 12h8M8 17h5"/><path d="M5 4h14a1 1 0 0 1 1 1v16l-3-2-3 2-3-2-3 2-3-2V5a1 1 0 0 1 1-1z"/>' +
            '</svg>' +
            'PREGLEDAJ NARUDŽBU</button>' +
            '</div>' +
            '</div>';

        document.body.appendChild(overlay);
        document.body.classList.add('staff-order-celebration-open');
        requestAnimationFrame(function () {
            overlay.classList.add('is-visible');
        });

        function closeCeleb() {
            overlay.classList.remove('is-visible');
            document.body.classList.remove('staff-order-celebration-open');
            document.removeEventListener('keydown', onKey);
            window.setTimeout(function () {
                if (overlay.parentNode) overlay.remove();
            }, 280);
        }

        function onKey(e) {
            if (e.key === 'Escape') closeCeleb();
        }

        overlay.querySelectorAll('[data-order-celeb-close]').forEach(function (el) {
            el.addEventListener('click', closeCeleb);
        });
        overlay.querySelector('[data-order-celeb-orders]')?.addEventListener('click', function (e) {
            goToOrder(orderUrl, e);
        });
        document.addEventListener('keydown', onKey);
    }

    function showEventToast(event, pendingCount) {
        if (!event) return;
        if ((event.tip || '') !== 'purchase') return;
        showOrderCelebration(event, pendingCount);
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
                let lastPurchase = null;
                events.forEach(function (event) {
                    if ((event.tip || '') === 'purchase') lastPurchase = event;
                });
                if (lastPurchase) {
                    showEventToast(lastPurchase, data.new_orders_count);
                }
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
