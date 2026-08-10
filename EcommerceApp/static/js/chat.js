document.addEventListener('DOMContentLoaded', () => {
    const configEl = document.getElementById('chatConfig');
    if (!configEl) return;

    let config = {};
    try {
        config = JSON.parse(configEl.textContent || '{}');
    } catch (e) {
        console.error('chat config', e);
        return;
    }

    const siteChat = document.getElementById('siteChat');
    const launchers = document.getElementById('siteChatLaunchers');
    const customerPanel = document.getElementById('customerChatPanel');
    const customerBtn = document.getElementById('customerChatBtn');
    const customerClose = document.getElementById('customerChatClose');
    const customerMessages = document.getElementById('customerChatMessages');
    const customerForm = document.getElementById('customerChatForm');
    const customerInput = document.getElementById('customerChatInput');
    const customerBadge = document.getElementById('customerChatBadge');

    const staffBtn = document.getElementById('staffChatBtn');
    const staffPanel = document.getElementById('staffChatPanel');
    const staffClose = document.getElementById('staffChatClose');
    const staffInbox = document.getElementById('staffChatInbox');
    const staffInboxList = document.getElementById('staffChatInboxList');
    const staffInboxEmpty = document.getElementById('staffInboxEmpty');
    const staffInboxCount = document.getElementById('staffInboxCount');
    const staffThread = document.getElementById('staffChatThread');
    const staffThreadPlaceholder = document.getElementById('staffThreadPlaceholder');
    const staffMessages = document.getElementById('staffChatMessages');
    const staffForm = document.getElementById('staffChatForm');
    const staffInput = document.getElementById('staffChatInput');
    const staffThreadMeta = document.getElementById('staffThreadMeta');
    const staffBadge = document.getElementById('staffChatBadge');
    const staffChatPulse = document.getElementById('staffChatPulse');
    const staffChatStatusText = document.getElementById('staffChatStatusText');
    const staffLayout = staffPanel?.querySelector('.site-chat-staff-layout');
    const staffProductSearch = document.getElementById('staffProductSearch');
    const staffProductDiscount = document.getElementById('staffProductDiscount');
    const staffProductSend = document.getElementById('staffProductSend');
    const staffProductResults = document.getElementById('staffProductResults');
    const staffProductToggle = document.getElementById('staffProductToggle');
    const staffProductExpand = document.getElementById('staffProductExpand');
    const staffShowOffline = document.getElementById('staffShowOfflineChats');

    const STORAGE_ENTER = 'site_chat_enter_at';
    const STORAGE_PROACTIVE = 'site_chat_proactive_done';
    const STORAGE_INTERNAL_NAV = 'site_chat_internal_nav';
    const STORAGE_VISIBLE = 'site_chat_launcher_visible';
    const STORAGE_PANEL_OPEN = 'site_chat_panel_open';
    const STORAGE_SHOW_OFFLINE = 'site_chat_show_offline';
    let PROACTIVE_DELAY = Number(config.proactiveDelayMs) || 120000;

    let customerOpen = false;
    let staffOpen = false;
    let lastMessageId = 0;
    let customerPollTimer = null;
    let staffPollTimer = null;
    let staffPingTimer = null;
    let proactiveTimer = null;
    let productSearchTimer = null;
    let activeStaffConversationId = null;
    let selectedProduct = null;
    let conversationClosed = false;
    let leaveSent = false;
    let customerChatRevealed = false;
    let lastStaffUnreadTotal = 0;
    let staffAutoOpenBusy = false;
    let showOfflineChats = false;
    try {
        showOfflineChats = sessionStorage.getItem(STORAGE_SHOW_OFFLINE) === '1';
    } catch (e) { /* ignore */ }
    if (staffShowOffline) {
        staffShowOffline.checked = showOfflineChats;
    }

    function getCsrfToken() {
        const match = document.cookie.match(/csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    async function apiFetch(url, options = {}) {
        const headers = {
            'X-CSRFToken': getCsrfToken(),
            'X-Requested-With': 'XMLHttpRequest',
            ...(options.headers || {}),
        };
        if (options.body && !headers['Content-Type']) {
            headers['Content-Type'] = 'application/json';
        }
        const response = await fetch(url, { ...options, headers, credentials: 'same-origin' });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
            const err = new Error(data.error || 'Zahtjev nije uspio.');
            err.data = data;
            throw err;
        }
        return data;
    }

    function formatTime(iso) {
        if (!iso) return '';
        const date = new Date(iso);
        return date.toLocaleString('bs-BA', {
            day: '2-digit',
            month: '2-digit',
            hour: '2-digit',
            minute: '2-digit',
        });
    }

    function isTouchPhone() {
        return window.matchMedia('(max-width: 768px), (hover: none) and (pointer: coarse)').matches;
    }

    function autoResize(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = `${Math.min(textarea.scrollHeight, 110)}px`;
    }

    /** Na telefonu ne fokusiraj polje — ne otvaraj tastaturu automatski */
    function safeFocus(el) {
        if (!el || isTouchPhone()) return;
        try {
            el.focus({ preventScroll: true });
        } catch (e) {
            try { el.focus(); } catch (err) { /* ignore */ }
        }
    }

    /** Sigurno pretvori URL-ove u klikabilne linkove (http/https/www). */
    function fillMessageBodyWithLinks(el, text) {
        if (!el) return;
        el.textContent = '';
        const raw = text || '';
        const re = /(https?:\/\/[^\s<>"']+|www\.[^\s<>"']+)/gi;
        let last = 0;
        let match;
        while ((match = re.exec(raw)) !== null) {
            if (match.index > last) {
                el.appendChild(document.createTextNode(raw.slice(last, match.index)));
            }
            let href = match[0];
            // trim trailing punctuation common in sentences
            let trailing = '';
            const trailMatch = href.match(/[.,;:!?)]+$/);
            if (trailMatch) {
                trailing = trailMatch[0];
                href = href.slice(0, -trailing.length);
            }
            const a = document.createElement('a');
            a.className = 'site-chat-link';
            a.href = href.startsWith('http') ? href : `https://${href}`;
            a.target = '_blank';
            a.rel = 'noopener noreferrer';
            a.textContent = href;
            el.appendChild(a);
            if (trailing) {
                el.appendChild(document.createTextNode(trailing));
            }
            last = match.index + match[0].length;
        }
        if (last < raw.length) {
            el.appendChild(document.createTextNode(raw.slice(last)));
        }
        if (!raw) {
            el.textContent = '';
        }
    }

    function revealCustomerLauncher() {
        customerChatRevealed = true;
        try {
            sessionStorage.setItem(STORAGE_VISIBLE, '1');
        } catch (e) { /* ignore */ }
        if (customerBtn) customerBtn.hidden = false;
        launchers?.classList.remove('is-customer-hidden');
    }

    function setCustomerPanelOpenState(open) {
        try {
            sessionStorage.setItem(STORAGE_PANEL_OPEN, open ? '1' : '0');
        } catch (e) { /* ignore */ }
    }

    function buildProductOfferCard(offer, { interactive = true } = {}) {
        const card = document.createElement('div');
        card.className = 'site-chat-product-card';
        card.dataset.messageId = String(offer.message_id || '');

        if (offer.image) {
            const img = document.createElement('img');
            img.className = 'site-chat-product-card__img';
            img.src = offer.image;
            img.alt = offer.naziv || '';
            img.loading = 'lazy';
            card.appendChild(img);
        }

        const info = document.createElement('div');
        info.className = 'site-chat-product-card__info';

        const name = document.createElement('a');
        name.className = 'site-chat-product-card__name';
        name.href = offer.url || '#';
        name.textContent = offer.naziv || 'Artikal';
        if (offer.url) {
            name.target = '_blank';
            name.rel = 'noopener';
        }

        const priceRow = document.createElement('div');
        priceRow.className = 'site-chat-product-card__price';
        if (offer.has_discount) {
            const oldP = document.createElement('span');
            oldP.className = 'site-chat-product-card__old';
            oldP.textContent = `${offer.bazna_cijena} KM`;
            const newP = document.createElement('span');
            newP.className = 'site-chat-product-card__new';
            newP.textContent = `${offer.cijena} KM`;
            priceRow.append(oldP, newP);
            if (offer.popust_postotak) {
                const badge = document.createElement('span');
                badge.className = 'site-chat-product-card__pct';
                const pct = Number(offer.popust_postotak);
                badge.textContent = `−${pct % 1 === 0 ? Math.round(pct) : pct}%`;
                priceRow.appendChild(badge);
            }
        } else {
            const p = document.createElement('span');
            p.className = 'site-chat-product-card__new';
            p.textContent = `${offer.cijena} KM`;
            priceRow.appendChild(p);
        }

        info.append(name, priceRow);

        if (interactive && offer.message_id) {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'site-chat-product-card__cta';
            btn.textContent = 'Dodaj u korpu';
            btn.addEventListener('click', async () => {
                btn.disabled = true;
                btn.textContent = '…';
                try {
                    const data = await apiFetch('/api/chat/add-product/', {
                        method: 'POST',
                        body: JSON.stringify({ message_id: offer.message_id }),
                    });
                    btn.textContent = 'Dodano ✓';
                    btn.classList.add('is-added');
                    // Ista korpa badge logika kao ostatak sajta
                    if (data.cart_count != null) {
                        if (typeof window.updateCartBadge === 'function') {
                            window.updateCartBadge(data.cart_count);
                        } else {
                            const cartBtn = document.querySelector('.cart-btn');
                            if (cartBtn) {
                                const nextCount = Math.max(0, parseInt(data.cart_count, 10) || 0);
                                cartBtn.classList.toggle('cart-btn--has-items', nextCount > 0);
                                cartBtn.dataset.cartCount = String(nextCount);
                                cartBtn.setAttribute('data-cart-count', String(nextCount));
                                let badge = cartBtn.querySelector('.cart-badge');
                                if (nextCount > 0) {
                                    if (!badge) {
                                        badge = document.createElement('span');
                                        badge.className = 'cart-badge';
                                        cartBtn.appendChild(badge);
                                    }
                                    badge.hidden = false;
                                    badge.style.display = '';
                                    badge.textContent = String(nextCount);
                                } else if (badge) {
                                    badge.remove();
                                }
                            }
                        }
                    }
                } catch (error) {
                    btn.disabled = false;
                    btn.textContent = 'Dodaj u korpu';
                    alert(error.message);
                }
            });
            info.appendChild(btn);
        }

        card.appendChild(info);
        return card;
    }

    function renderMessage(message, container, { interactiveOffers = true, viewer = 'customer', customerName = 'Kupac' } = {}) {
        if (!container || !message) return;
        if (container.querySelector(`[data-message-id="${message.id}"]`)) {
            lastMessageId = Math.max(lastMessageId, message.id);
            return;
        }
        const item = document.createElement('div');
        item.className = `site-chat-message site-chat-message--${message.sender_type}`;
        item.dataset.messageId = String(message.id);

        const meta = document.createElement('div');
        meta.className = 'site-chat-message-meta';
        if (viewer === 'staff') {
            meta.textContent = message.sender_type === 'staff'
                ? 'Vi'
                : (customerName || 'Kupac');
        } else {
            meta.textContent = message.sender_type === 'staff'
                ? (message.staff_name || 'Zaposlenik')
                : 'Vi';
        }

        item.appendChild(meta);

        if (message.is_product_offer && message.product_offer) {
            item.appendChild(buildProductOfferCard(message.product_offer, {
                interactive: interactiveOffers && message.sender_type === 'staff',
            }));
            if (message.body && !message.body.startsWith('Preporučujemo:')) {
                const body = document.createElement('div');
                body.className = 'site-chat-message-body';
                fillMessageBodyWithLinks(body, message.body);
                item.appendChild(body);
            }
        } else {
            const body = document.createElement('div');
            body.className = 'site-chat-message-body';
            fillMessageBodyWithLinks(body, message.body || '');
            item.appendChild(body);
        }

        const time = document.createElement('div');
        time.className = 'site-chat-message-time';
        time.textContent = formatTime(message.created_at);
        item.appendChild(time);

        container.appendChild(item);
        container.scrollTop = container.scrollHeight;
        lastMessageId = Math.max(lastMessageId, message.id);
    }

    function setBadge(el, count) {
        if (!el) return;
        const n = Number(count) || 0;
        const launcher = el === staffBadge ? staffBtn : (el === customerBadge ? customerBtn : null);
        if (n > 0) {
            el.textContent = n > 9 ? '9+' : String(n);
            el.hidden = false;
            el.classList.add('is-pulse');
            el.setAttribute('aria-label', n === 1 ? '1 nepročitana poruka' : `${n} nepročitanih poruka`);
            launcher?.classList.add('has-unread');
        } else {
            el.hidden = true;
            el.classList.remove('is-pulse');
            el.removeAttribute('aria-label');
            launcher?.classList.remove('has-unread');
        }
    }

    function syncLauncherState() {
        launchers?.classList.toggle('is-customer-open', customerOpen);
        launchers?.classList.toggle('is-staff-open', staffOpen);
        document.body.classList.toggle('site-chat-panel-open', customerOpen || staffOpen);
        if (siteChat) {
            siteChat.hidden = !(customerOpen || staffOpen);
        }
    }

    function togglePanel(panel, open) {
        if (!panel) return;
        panel.hidden = !open;
        syncLauncherState();
    }

    function showClosedNote() {
        // Registrovan korisnik: istorija ostaje, može i dalje pisati (server reopen)
        if (config.isAuthenticated) {
            conversationClosed = false;
            if (customerForm) {
                customerForm.querySelector('textarea')?.removeAttribute('disabled');
                customerForm.querySelector('button')?.removeAttribute('disabled');
            }
            return;
        }
        if (!customerMessages || customerMessages.querySelector('.site-chat-closed-note')) return;
        const note = document.createElement('div');
        note.className = 'site-chat-closed-note';
        note.textContent = 'Chat je zatvoren. Osvežite stranicu za novi razgovor.';
        customerMessages.appendChild(note);
        if (customerForm) {
            customerForm.querySelector('textarea')?.setAttribute('disabled', 'disabled');
            customerForm.querySelector('button')?.setAttribute('disabled', 'disabled');
        }
    }

    async function loadCustomerChat({ proactive = false } = {}) {
        const qs = new URLSearchParams({ mark_read: '1' });
        if (proactive) qs.set('proactive', '1');
        const data = await apiFetch(`/api/chat/?${qs.toString()}`);
        if (customerMessages) {
            customerMessages.innerHTML = '';
        }
        lastMessageId = 0;
        conversationClosed = data.status === 'closed';
        (data.messages || []).forEach((message) => {
            renderMessage(message, customerMessages, { interactiveOffers: true });
        });
        if (conversationClosed) showClosedNote();
        setBadge(customerBadge, 0);
        return data;
    }

    function playCustomerChatSound() {
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            const ctx = new Ctx();
            const now = ctx.currentTime;
            const notes = [523.25, 659.25, 783.99]; // C5 E5 G5
            notes.forEach((freq, i) => {
                const o = ctx.createOscillator();
                const g = ctx.createGain();
                o.type = 'sine';
                o.frequency.value = freq;
                g.gain.setValueAtTime(0.0001, now);
                g.gain.exponentialRampToValueAtTime(0.07, now + 0.02 + i * 0.08);
                g.gain.exponentialRampToValueAtTime(0.0001, now + 0.22 + i * 0.1);
                o.connect(g);
                g.connect(ctx.destination);
                o.start(now + i * 0.09);
                o.stop(now + 0.35 + i * 0.1);
            });
            setTimeout(() => {
                try { ctx.close(); } catch (e) { /* ignore */ }
            }, 800);
        } catch (e) { /* ignore */ }
    }

    function showCustomerChatAppearPopup() {
        let popup = document.getElementById('customerChatAppearPopup');
        if (!popup) {
            popup = document.createElement('div');
            popup.id = 'customerChatAppearPopup';
            popup.className = 'site-chat-customer-popup';
            popup.setAttribute('role', 'status');
            popup.innerHTML = `
                <button type="button" class="site-chat-customer-popup__close" aria-label="Zatvori">×</button>
                <div class="site-chat-customer-popup__icon" aria-hidden="true">💬</div>
                <strong class="site-chat-customer-popup__title">Tu smo da pomognemo!</strong>
                <p class="site-chat-customer-popup__text">Imate pitanje ili trebate preporuku? Pišite nam u chatu.</p>
            `;
            document.body.appendChild(popup);
            popup.querySelector('.site-chat-customer-popup__close')?.addEventListener('click', () => {
                popup.classList.remove('is-visible');
            });
            popup.addEventListener('click', (e) => {
                if (e.target === popup) popup.classList.remove('is-visible');
            });
        }
        // restart animacije
        popup.classList.remove('is-visible');
        void popup.offsetWidth;
        popup.classList.add('is-visible');
        clearTimeout(showCustomerChatAppearPopup._t);
        showCustomerChatAppearPopup._t = setTimeout(() => {
            popup.classList.remove('is-visible');
        }, 5500);
    }

    async function openCustomerChat({ proactive = false, announce = false } = {}) {
        if (!customerPanel) return;
        revealCustomerLauncher();
        customerOpen = true;
        setCustomerPanelOpenState(true);
        if (staffOpen) {
            staffOpen = false;
            togglePanel(staffPanel, false);
        }
        togglePanel(customerPanel, true);
        try {
            await loadCustomerChat({ proactive });
            startCustomerPolling();
            // Bez auto-fokusa na mobitelu (ne otvara tastaturu / ne zumira)
            safeFocus(customerInput);
            // Zvuk + popup samo kad se chat prvi put pojavi (proaktivno)
            if (announce) {
                playCustomerChatSound();
                showCustomerChatAppearPopup();
            }
        } catch (error) {
            console.error(error);
        }
    }

    /** Minimiziraj — chat ostaje aktivan do izlaska sa sajta */
    function closeCustomerChat() {
        customerOpen = false;
        setCustomerPanelOpenState(false);
        togglePanel(customerPanel, false);
        // Polling ostaje radi badge-a za nove poruke zaposlenika
        if (!customerPollTimer) {
            startCustomerPolling();
        }
        // Poll even when minimized — need a lighter poll
        stopCustomerPolling();
        startMinimizedCustomerPolling();
    }

    let customerMinimizedPollTimer = null;
    function startMinimizedCustomerPolling() {
        clearInterval(customerMinimizedPollTimer);
        customerMinimizedPollTimer = setInterval(async () => {
            if (customerOpen) return;
            try {
                const data = await apiFetch('/api/chat/badge/');
                setBadge(customerBadge, data.customer_unread_count);
            } catch (e) { /* ignore */ }
        }, config.isAuthenticated ? 3500 : 5000);
    }
    function stopMinimizedCustomerPolling() {
        clearInterval(customerMinimizedPollTimer);
        customerMinimizedPollTimer = null;
    }

    async function pollCustomerChat() {
        if (!customerOpen) return;
        try {
            const data = await apiFetch(`/api/chat/poll/?after_id=${lastMessageId}&open=1`);
            if (data.closed) {
                conversationClosed = true;
                showClosedNote();
                return;
            }
            (data.messages || []).forEach((message) => {
                renderMessage(message, customerMessages, { interactiveOffers: true });
            });
        } catch (error) {
            console.error(error);
        }
    }

    function startCustomerPolling() {
        stopMinimizedCustomerPolling();
        clearInterval(customerPollTimer);
        customerPollTimer = setInterval(pollCustomerChat, 3000);
    }

    function stopCustomerPolling() {
        clearInterval(customerPollTimer);
        customerPollTimer = null;
    }

    customerBtn?.addEventListener('click', async () => {
        sessionStorage.setItem(STORAGE_PROACTIVE, '1');
        clearTimeout(proactiveTimer);
        if (customerOpen) {
            // Minimize — ikona ostaje, može se opet otvoriti
            closeCustomerChat();
            return;
        }
        stopMinimizedCustomerPolling();
        await openCustomerChat({ proactive: false });
    });

    customerClose?.addEventListener('click', () => {
        closeCustomerChat();
    });

    customerForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (conversationClosed) return;
        const body = customerInput.value.trim();
        if (!body) return;
        try {
            const data = await apiFetch('/api/chat/send/', {
                method: 'POST',
                body: JSON.stringify({ body }),
            });
            renderMessage(data.message, customerMessages);
            customerInput.value = '';
            autoResize(customerInput);
        } catch (error) {
            if (error.data?.closed) {
                conversationClosed = true;
                showClosedNote();
            } else {
                alert(error.message);
            }
        }
    });

    customerInput?.addEventListener('input', () => autoResize(customerInput));
    customerInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            customerForm?.requestSubmit();
        }
    });

    /* —— Staff —— */
    let activeStaffCustomerName = 'Kupac';

    function initialsFromName(name) {
        const parts = String(name || 'G')
            .trim()
            .split(/\s+/)
            .filter(Boolean);
        if (!parts.length) return 'G';
        if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
        return (parts[0][0] + parts[1][0]).toUpperCase();
    }

    function setStaffPulse(active) {
        if (staffChatPulse) staffChatPulse.hidden = !active;
        staffBtn?.classList.toggle('has-pulse', Boolean(active));
    }

    function filterConversationsForDisplay(conversations) {
        // Samo oni koji su pisali (backend već filtrira has_customer_message)
        let list = (conversations || []).filter((c) => c.has_customer_message !== false);
        if (!showOfflineChats) {
            list = list.filter((c) => c.is_online);
        }
        return list;
    }

    /** Mala tačka za prebacivanje razgovora (iznad KUPCI) */
    function renderInboxDot(conversation) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'site-chat-dot';
        btn.setAttribute('role', 'tab');
        btn.setAttribute(
            'aria-label',
            `${conversation.display_name || 'Kupac'}${conversation.staff_unread_count ? ', nova poruka' : ''}`,
        );
        btn.title = conversation.display_name || 'Kupac';

        if (conversation.is_online) {
            btn.classList.add('is-online');
        } else {
            btn.classList.add('is-offline');
        }
        if (conversation.staff_unread_count > 0) {
            btn.classList.add('is-unread');
        }
        if (conversation.id === activeStaffConversationId) {
            btn.classList.add('is-active');
        }

        // Inicijal u tački (sitno)
        const initial = document.createElement('span');
        initial.className = 'site-chat-dot-initial';
        const name = (conversation.display_name || 'G').trim();
        initial.textContent = name.charAt(0).toUpperCase();
        btn.appendChild(initial);

        if (conversation.staff_unread_count > 0) {
            const pip = document.createElement('span');
            pip.className = 'site-chat-dot-pip';
            pip.setAttribute('aria-hidden', 'true');
            btn.appendChild(pip);
        }

        btn.addEventListener('click', () => openStaffConversation(conversation.id));
        return btn;
    }

    function paintStaffInboxList(conversations) {
        const listRoot = staffInboxList || staffInbox;
        if (!listRoot) return;
        listRoot.querySelectorAll('.site-chat-dot, .site-chat-inbox-item').forEach((item) => item.remove());

        const visible = filterConversationsForDisplay(conversations);
        if (staffInboxCount) {
            if (visible.length > 0) {
                staffInboxCount.hidden = false;
                staffInboxCount.textContent = String(visible.length);
            } else {
                staffInboxCount.hidden = true;
            }
        }

        if (!visible.length) {
            if (staffInboxEmpty) {
                staffInboxEmpty.hidden = false;
                staffInboxEmpty.textContent = showOfflineChats
                    ? 'Nema kupaca koji su pisali.'
                    : 'Nema online kupaca s porukom.';
            }
            return;
        }

        if (staffInboxEmpty) staffInboxEmpty.hidden = true;
        visible.forEach((conversation) => {
            listRoot.appendChild(renderInboxDot(conversation));
        });
    }

    async function loadStaffInbox() {
        if (!staffInbox) return;
        const data = await apiFetch('/api/chat/staff/inbox/');
        const all = data.conversations || [];
        const onlineUnread = data.online_unread_conversations
            ?? all.filter((c) => c.is_online && c.has_customer_message !== false && (c.staff_unread_count || 0) > 0).length;
        // Pulse / badge: prioritet online nepročitani
        setBadge(staffBadge, onlineUnread || (showOfflineChats ? (data.unread_conversations || 0) : 0));
        setStaffPulse(onlineUnread > 0 || (showOfflineChats && (data.unread_conversations || 0) > 0));
        if (staffBtn) staffBtn.hidden = false;

        paintStaffInboxList(all);
        return data;
    }

    async function openStaffConversation(conversationId) {
        activeStaffConversationId = conversationId;
        selectedProduct = null;
        if (staffProductSend) staffProductSend.disabled = true;
        if (staffProductSearch) staffProductSearch.value = '';
        if (staffProductResults) {
            staffProductResults.innerHTML = '';
            staffProductResults.hidden = true;
        }

        const data = await apiFetch(`/api/chat/staff/${conversationId}/`);
        const conversation = data.conversation;
        activeStaffCustomerName = conversation.display_name || 'Kupac';

        if (staffThread) staffThread.hidden = false;
        if (staffThreadPlaceholder) staffThreadPlaceholder.hidden = true;
        staffLayout?.classList.add('has-thread');

        staffMessages.innerHTML = '';
        lastMessageId = 0;
        data.messages.forEach((message) => {
            renderMessage(message, staffMessages, {
                interactiveOffers: false,
                viewer: 'staff',
                customerName: activeStaffCustomerName,
            });
        });

        const registeredLabel = conversation.is_registered ? 'Registrovan' : 'Gost';
        const emailLine = conversation.display_email || '—';
        staffThreadMeta.innerHTML = `
            <span class="site-chat-thread-meta__avatar">${initialsFromName(conversation.display_name)}</span>
            <span class="site-chat-thread-meta__text">
                <strong>${conversation.display_name || 'Gost'}</strong>
                <span>${registeredLabel} · ${emailLine}</span>
            </span>
        `;
        if (staffChatStatusText) {
            staffChatStatusText.textContent = `U razgovoru s ${conversation.display_name || 'kupcem'}`;
        }
        // Bez auto-tastature na telefonu
        safeFocus(staffInput);
        await loadStaffInbox();
    }

    async function openStaffPanel() {
        if (!staffPanel) return;
        staffOpen = true;
        if (customerOpen) closeCustomerChat();
        togglePanel(staffPanel, true);
        if (staffBtn) staffBtn.hidden = false;
        startStaffTimers();
        try {
            await loadStaffInbox();
        } catch (error) {
            console.error(error);
        }
    }

    /**
     * Superuser/staff: čim kupac nešto napiše → otvori chat i taj razgovor.
     */
    async function maybeAutoOpenOnCustomerMessage(unreadCount, conversations) {
        if (!config.isStaff || !config.autoOpenOnCustomerMessage) return;
        if (staffAutoOpenBusy) return;

        const unread = Number(unreadCount) || 0;
        const prev = lastStaffUnreadTotal;
        // Nova poruka (porast unread) ili prvi put dok panel nije otvoren
        const isNew = unread > prev;
        const shouldOpen = unread > 0 && (isNew || (!staffOpen && prev < 0));
        lastStaffUnreadTotal = unread;

        if (!shouldOpen || unread <= 0) return;

        staffAutoOpenBusy = true;
        try {
            let list = conversations;
            if (!list) {
                const inbox = await apiFetch('/api/chat/staff/inbox/');
                list = inbox.conversations || [];
                const onlineUnread = inbox.online_unread_conversations
                    ?? list.filter((c) => c.is_online && c.has_customer_message !== false && (c.staff_unread_count || 0) > 0).length;
                setBadge(staffBadge, onlineUnread);
                lastStaffUnreadTotal = onlineUnread;
            }
            const onlineList = (list || []).filter(
                (c) => c.is_online && c.has_customer_message !== false,
            );
            const target = onlineList.find((c) => (c.staff_unread_count || 0) > 0)
                || onlineList[0];
            if (!target) return;

            // Upozorenje superuseru
            if (isNew || prev < 0) {
                showStaffNewMessageAlert(target.display_name);
            }

            if (!staffOpen) {
                await openStaffPanel();
            }
            if (target.id !== activeStaffConversationId) {
                await openStaffConversation(target.id);
            }
            if (staffChatStatusText) {
                staffChatStatusText.textContent = `Nova poruka od ${target.display_name || 'kupca'}`;
            }
        } catch (error) {
            console.error(error);
        } finally {
            staffAutoOpenBusy = false;
        }
    }

    async function pollStaffInbox() {
        if (!config.isStaff) return;
        try {
            const inbox = await apiFetch('/api/chat/staff/inbox/');
            const all = inbox.conversations || [];
            const onlineUnread = inbox.online_unread_conversations
                ?? all.filter((c) => c.is_online && (c.staff_unread_count || 0) > 0).length;

            if (staffOpen) {
                paintStaffInboxList(all);
                setBadge(staffBadge, onlineUnread || (showOfflineChats ? (inbox.unread_conversations || 0) : 0));
                setStaffPulse(onlineUnread > 0 || (showOfflineChats && (inbox.unread_conversations || 0) > 0));

                // Nova poruka od online kupca → prebaci na taj thread
                const unreadConv = all.find(
                    (c) => c.is_online && c.has_customer_message !== false && (c.staff_unread_count || 0) > 0,
                );
                if (
                    unreadConv
                    && unreadConv.id !== activeStaffConversationId
                    && config.autoOpenOnCustomerMessage
                ) {
                    const typing = staffInput && document.activeElement === staffInput && staffInput.value.trim();
                    if (!typing) {
                        await openStaffConversation(unreadConv.id);
                    }
                } else if (onlineUnread > lastStaffUnreadTotal && unreadConv) {
                    // već na tom threadu — poruke se dohvate ispod
                }

                if (activeStaffConversationId) {
                    const data = await apiFetch(`/api/chat/staff/${activeStaffConversationId}/`);
                    data.messages.forEach((message) => {
                        renderMessage(message, staffMessages, {
                            interactiveOffers: false,
                            viewer: 'staff',
                            customerName: activeStaffCustomerName,
                        });
                    });
                }
                lastStaffUnreadTotal = onlineUnread;
            } else {
                setBadge(staffBadge, onlineUnread);
                setStaffPulse(onlineUnread > 0);
                if (staffBtn) staffBtn.hidden = false;
                await maybeAutoOpenOnCustomerMessage(onlineUnread, all);
            }
        } catch (error) {
            console.error(error);
        }
    }

    function startStaffTimers() {
        clearInterval(staffPollTimer);
        clearInterval(staffPingTimer);
        // Brzi poll (1.5–2s) — chat iskače superuseru bez refresha
        const interval = staffOpen ? 2000 : 1500;
        staffPollTimer = setInterval(pollStaffInbox, interval);
    }

    staffShowOffline?.addEventListener('change', () => {
        showOfflineChats = Boolean(staffShowOffline.checked);
        try {
            sessionStorage.setItem(STORAGE_SHOW_OFFLINE, showOfflineChats ? '1' : '0');
        } catch (e) { /* ignore */ }
        loadStaffInbox().catch(() => {});
    });

    async function searchStaffProducts(q) {
        if (!staffProductResults) return;
        if (!q || q.length < 2) {
            staffProductResults.innerHTML = '';
            staffProductResults.hidden = true;
            return;
        }
        try {
            const data = await apiFetch(`/api/chat/staff/products/?q=${encodeURIComponent(q)}`);
            staffProductResults.innerHTML = '';
            if (!data.results?.length) {
                staffProductResults.innerHTML = '<p class="site-chat-product-empty">Nema rezultata.</p>';
                staffProductResults.hidden = false;
                return;
            }
            data.results.forEach((item) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'site-chat-product-result';
                btn.innerHTML = `
                    ${item.image ? `<img src="${item.image}" alt="" width="36" height="36">` : '<span class="site-chat-product-result__ph"></span>'}
                    <span class="site-chat-product-result__txt">
                        <strong>${item.label}</strong>
                        <span>${item.sifra ? item.sifra + ' · ' : ''}${item.price} KM</span>
                    </span>
                `;
                btn.addEventListener('click', () => {
                    selectedProduct = item;
                    if (staffProductSearch) staffProductSearch.value = item.label;
                    if (staffProductSend) staffProductSend.disabled = false;
                    staffProductResults.hidden = true;
                });
                staffProductResults.appendChild(btn);
            });
            staffProductResults.hidden = false;
        } catch (error) {
            console.error(error);
        }
    }

    function setProductExpand(open) {
        if (!staffProductExpand || !staffProductToggle) return;
        staffProductExpand.hidden = !open;
        staffProductToggle.setAttribute('aria-expanded', open ? 'true' : 'false');
        staffProductToggle.classList.toggle('is-open', open);
        if (open) {
            safeFocus(staffProductSearch);
        } else {
            selectedProduct = null;
            if (staffProductSearch) staffProductSearch.value = '';
            if (staffProductDiscount) staffProductDiscount.value = '';
            if (staffProductResults) {
                staffProductResults.innerHTML = '';
                staffProductResults.hidden = true;
            }
            if (staffProductSend) staffProductSend.disabled = true;
        }
    }

    staffProductToggle?.addEventListener('click', () => {
        const open = staffProductToggle.getAttribute('aria-expanded') !== 'true';
        setProductExpand(open);
    });

    staffProductSearch?.addEventListener('input', () => {
        selectedProduct = null;
        if (staffProductSend) staffProductSend.disabled = true;
        clearTimeout(productSearchTimer);
        const q = staffProductSearch.value.trim();
        productSearchTimer = setTimeout(() => searchStaffProducts(q), 220);
    });

    staffProductSend?.addEventListener('click', async () => {
        if (!activeStaffConversationId || !selectedProduct) return;
        const pctRaw = (staffProductDiscount?.value || '').trim();
        const payload = {
            product_id: selectedProduct.id,
        };
        if (pctRaw !== '') {
            payload.popust_postotak = pctRaw;
        }
        staffProductSend.disabled = true;
        try {
            const data = await apiFetch(`/api/chat/staff/${activeStaffConversationId}/send-product/`, {
                method: 'POST',
                body: JSON.stringify(payload),
            });
            renderMessage(data.message, staffMessages, {
                interactiveOffers: false,
                viewer: 'staff',
                customerName: activeStaffCustomerName,
            });
            selectedProduct = null;
            if (staffProductSearch) staffProductSearch.value = '';
            if (staffProductDiscount) staffProductDiscount.value = '';
            setProductExpand(false);
            setBadge(staffBadge, data.unread_conversations);
            await loadStaffInbox();
        } catch (error) {
            alert(error.message);
            staffProductSend.disabled = false;
        }
    });

    staffBtn?.addEventListener('click', async () => {
        if (staffOpen) {
            staffOpen = false;
            togglePanel(staffPanel, false);
            syncLauncherState();
            startStaffTimers();
            return;
        }
        await openStaffPanel();
    });

    staffClose?.addEventListener('click', () => {
        staffOpen = false;
        togglePanel(staffPanel, false);
        syncLauncherState();
        startStaffTimers();
    });

    staffForm?.addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!activeStaffConversationId) return;
        const body = staffInput.value.trim();
        if (!body) return;
        try {
            const data = await apiFetch(`/api/chat/staff/${activeStaffConversationId}/send/`, {
                method: 'POST',
                body: JSON.stringify({ body }),
            });
            renderMessage(data.message, staffMessages, {
                interactiveOffers: false,
                viewer: 'staff',
                customerName: activeStaffCustomerName,
            });
            staffInput.value = '';
            autoResize(staffInput);
            setBadge(staffBadge, data.unread_conversations);
            await loadStaffInbox();
        } catch (error) {
            alert(error.message);
        }
    });

    staffInput?.addEventListener('input', () => autoResize(staffInput));
    staffInput?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            staffForm?.requestSubmit();
        }
    });

    async function pollCustomerUnread() {
        if (!config.chatEnabled || customerOpen) return;
        // Badge i kad ikona još nije “reveal” (registrovan s istorijom)
        if (!customerChatRevealed && !config.isAuthenticated) return;
        try {
            const data = await apiFetch('/api/chat/badge/');
            const unread = data.customer_unread_count || 0;
            if (data.has_history || data.has_conversation || unread > 0) {
                revealCustomerLauncher();
            }
            setBadge(customerBadge, unread);
            // Gost: closed = ne može pisati; registrovan: uvijek može
            if (data.status === 'closed' && !config.isAuthenticated) {
                conversationClosed = true;
            }
        } catch (error) {
            /* tiho */
        }
    }

    /**
     * Registrovan korisnik: chat ikona + istorija uvijek dostupni.
     * Badge/unread se učitava odmah.
     */
    async function initRegisteredCustomerChat() {
        if (!config.chatEnabled || config.isStaff || !config.isAuthenticated) return false;
        revealCustomerLauncher();
        try {
            const data = await apiFetch('/api/chat/badge/');
            const unread = data.customer_unread_count || 0;
            setBadge(customerBadge, unread);
            sessionStorage.setItem(STORAGE_VISIBLE, '1');
            sessionStorage.setItem(STORAGE_PROACTIVE, '1');
            const panelPref = sessionStorage.getItem(STORAGE_PANEL_OPEN);
            if (unread > 0) {
                // Nepročitano → otvori odmah da vidi poruku
                await openCustomerChat({ proactive: false });
            } else if (panelPref === '1') {
                await openCustomerChat({ proactive: false });
            } else {
                // Minimiziran — ikona + brzi badge poll; istorija na klik
                startMinimizedCustomerPolling();
            }
            return true;
        } catch (e) {
            // Fallback: ipak pokaži launcher
            startMinimizedCustomerPolling();
            return true;
        }
    }

    /* —— Proaktivni chat: NIKAKAV UI dok ne istekne delay (gosti) —— */
    async function scheduleProactiveChat() {
        if (!config.chatEnabled) return;
        if (config.isStaff) return; // staff ne dobija auto popup kao kupac

        // Registrovan: uvijek ima chat + istoriju (bez čekanja delay-a)
        if (config.isAuthenticated) {
            await initRegisteredCustomerChat();
            return;
        }

        // Chat već aktivan u ovoj sesiji (navigacija kategorija/stranica)
        const alreadyActive = sessionStorage.getItem(STORAGE_VISIBLE) === '1'
            || sessionStorage.getItem(STORAGE_PROACTIVE) === '1';
        if (alreadyActive) {
            revealCustomerLauncher();
            // Jednom kad iskoci — ostaje: panel otvoren ili minimiziran (ikona)
            // Default: ako je ikad bio aktivan, vrati panel (osim ako je eksplicitno smanjen)
            const panelPref = sessionStorage.getItem(STORAGE_PANEL_OPEN);
            if (panelPref === '0') {
                startMinimizedCustomerPolling();
            } else {
                // panelPref === '1' ili null (stari state) → otvori
                await openCustomerChat({ proactive: false });
            }
            return;
        }

        // Pokušaj sync delay iz servera
        try {
            const remote = await apiFetch('/api/chat/config/');
            if (remote.enabled === false) return;
            if (remote.delay_ms) PROACTIVE_DELAY = Number(remote.delay_ms);
        } catch (e) { /* koristi config iz HTML */ }

        let enterAt = Number(sessionStorage.getItem(STORAGE_ENTER) || 0);
        if (!enterAt) {
            enterAt = Date.now();
            sessionStorage.setItem(STORAGE_ENTER, String(enterAt));
        }

        const elapsed = Date.now() - enterAt;
        const wait = Math.max(0, PROACTIVE_DELAY - elapsed);

        clearTimeout(proactiveTimer);
        proactiveTimer = setTimeout(async () => {
            if (sessionStorage.getItem(STORAGE_PROACTIVE) === '1') {
                revealCustomerLauncher();
                if (sessionStorage.getItem(STORAGE_PANEL_OPEN) === '1') {
                    await openCustomerChat({ proactive: false });
                }
                return;
            }
            if (document.hidden) {
                const onVis = () => {
                    if (document.visibilityState === 'visible') {
                        document.removeEventListener('visibilitychange', onVis);
                        scheduleProactiveChat();
                    }
                };
                document.addEventListener('visibilitychange', onVis);
                return;
            }
            sessionStorage.setItem(STORAGE_PROACTIVE, '1');
            // Prvo pojavljivanje: zvuk + popup
            await openCustomerChat({ proactive: true, announce: true });
        }, wait);
    }

    /* —— Navigacija po sajtu: chat se NE gasi —— */
    function markInternalNavigation() {
        try {
            sessionStorage.setItem(STORAGE_INTERNAL_NAV, '1');
            // Sačuvaj da je chat aktivan i prije pagehide
            if (customerChatRevealed || sessionStorage.getItem(STORAGE_PROACTIVE) === '1') {
                sessionStorage.setItem(STORAGE_VISIBLE, '1');
                sessionStorage.setItem(STORAGE_PROACTIVE, '1');
                if (customerOpen) {
                    sessionStorage.setItem(STORAGE_PANEL_OPEN, '1');
                }
            }
        } catch (e) { /* ignore */ }
    }

    document.addEventListener('click', (e) => {
        const a = e.target.closest?.('a[href]');
        if (!a) return;
        const href = a.getAttribute('href') || '';
        if (!href || href.startsWith('#') || href.startsWith('javascript:')) return;
        if (a.target === '_blank' || a.hasAttribute('download')) return;
        try {
            const url = new URL(a.href, window.location.origin);
            if (url.origin === window.location.origin) {
                markInternalNavigation();
            }
        } catch (err) { /* ignore */ }
    }, true);

    document.addEventListener('submit', () => {
        markInternalNavigation();
    }, true);

    /**
     * pagehide se pali i pri navigaciji (kategorija, korpa…).
     * NE brišemo sessionStorage i NE zatvaramo razgovor.
     * Soft leave na serveru je no-op (razgovor ostaje OPEN).
     */
    function sendChatLeave() {
        try {
            // Uvijek sačuvaj stanje chata prije navigacije
            if (customerOpen) {
                sessionStorage.setItem(STORAGE_PANEL_OPEN, '1');
                sessionStorage.setItem(STORAGE_VISIBLE, '1');
                sessionStorage.setItem(STORAGE_PROACTIVE, '1');
            } else if (customerChatRevealed || sessionStorage.getItem(STORAGE_VISIBLE) === '1') {
                sessionStorage.setItem(STORAGE_VISIBLE, '1');
                sessionStorage.setItem(STORAGE_PROACTIVE, '1');
            }
        } catch (e) { /* ignore */ }

        // Soft leave — ne zatvara razgovor (vidi views_chat.chat_leave)
        const url = config.leaveUrl || '/api/chat/leave/';
        try {
            if (navigator.sendBeacon) {
                const blob = new Blob(['{}'], { type: 'application/json' });
                navigator.sendBeacon(url, blob);
            }
        } catch (e) { /* ignore */ }
    }

    window.addEventListener('pagehide', sendChatLeave);

    /** Toast + zvuk za superusera kad stigne poruka */
    function showStaffNewMessageAlert(name) {
        const label = name || 'Kupac';
        let toast = document.getElementById('staffChatToast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'staffChatToast';
            toast.className = 'site-chat-staff-toast';
            toast.setAttribute('role', 'status');
            document.body.appendChild(toast);
        }
        toast.innerHTML = `<strong>Nova poruka</strong><span>${label} ti je napisao/la u chatu</span>`;
        toast.classList.add('is-visible');
        clearTimeout(showStaffNewMessageAlert._t);
        showStaffNewMessageAlert._t = setTimeout(() => {
            toast.classList.remove('is-visible');
        }, 6000);

        // Kratki bip (Web Audio) — tih
        try {
            const Ctx = window.AudioContext || window.webkitAudioContext;
            if (!Ctx) return;
            const ctx = new Ctx();
            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.type = 'sine';
            o.frequency.value = 880;
            g.gain.value = 0.04;
            o.connect(g);
            g.connect(ctx.destination);
            o.start();
            setTimeout(() => {
                o.stop();
                ctx.close();
            }, 160);
        } catch (e) { /* ignore */ }

        // Browser notification (ako je dozvoljeno)
        try {
            if (window.Notification && Notification.permission === 'granted') {
                // eslint-disable-next-line no-new
                new Notification('Nova chat poruka', {
                    body: `${label} ti je napisao/la`,
                    tag: 'site-chat-staff',
                });
            } else if (window.Notification && Notification.permission === 'default') {
                Notification.requestPermission().catch(() => {});
            }
        } catch (e) { /* ignore */ }
    }

    /* init — staff: samo chat sa kupcima; kupac: proaktivni chat */
    if (customerBtn) customerBtn.hidden = true;

    if (config.isStaff) {
        if (staffBtn) staffBtn.hidden = false;
        lastStaffUnreadTotal = -1; // forsira prvi auto-open ako ima unread
        startStaffTimers();
        // Odmah provjeri ima li nepročitanih — otvori ako kupac piše (bez refresha)
        pollStaffInbox().catch((error) => console.error(error));
    } else if (config.chatEnabled) {
        scheduleProactiveChat();
        // Brži badge poll — nepročitano odmah (posebno registrovani)
        setInterval(pollCustomerUnread, config.isAuthenticated ? 4000 : 12000);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState !== 'visible') return;
        if (config.isStaff) {
            pollStaffInbox();
        } else if (config.chatEnabled) {
            pollCustomerUnread();
        }
    });
});
