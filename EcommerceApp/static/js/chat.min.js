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

    function autoResize(textarea) {
        if (!textarea) return;
        textarea.style.height = 'auto';
        textarea.style.height = `${Math.min(textarea.scrollHeight, 110)}px`;
    }

    function revealCustomerLauncher() {
        customerChatRevealed = true;
        try {
            sessionStorage.setItem(STORAGE_VISIBLE, '1');
        } catch (e) { /* ignore */ }
        if (customerBtn) customerBtn.hidden = false;
        launchers?.classList.remove('is-customer-hidden');
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
                ? (message.staff_name || 'Podrška')
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
                body.textContent = message.body;
                item.appendChild(body);
            }
        } else {
            const body = document.createElement('div');
            body.className = 'site-chat-message-body';
            body.textContent = message.body || '';
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
        if (count > 0) {
            el.textContent = count > 9 ? '9+' : String(count);
            el.hidden = false;
        } else {
            el.hidden = true;
        }
    }

    function syncLauncherState() {
        launchers?.classList.toggle('is-customer-open', customerOpen);
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
        if (!customerMessages || customerMessages.querySelector('.site-chat-closed-note')) return;
        const note = document.createElement('div');
        note.className = 'site-chat-closed-note';
        note.textContent = 'Chat je zatvoren jer ste napustili sajt. Osvežite stranicu za novi razgovor.';
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

    async function openCustomerChat({ proactive = false } = {}) {
        if (!customerPanel) return;
        revealCustomerLauncher();
        customerOpen = true;
        if (staffOpen) {
            staffOpen = false;
            togglePanel(staffPanel, false);
        }
        togglePanel(customerPanel, true);
        try {
            await loadCustomerChat({ proactive });
            startCustomerPolling();
            customerInput?.focus({ preventScroll: true });
        } catch (error) {
            console.error(error);
        }
    }

    function closeCustomerChat() {
        customerOpen = false;
        togglePanel(customerPanel, false);
        stopCustomerPolling();
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
            closeCustomerChat();
            return;
        }
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
        const list = conversations || [];
        if (showOfflineChats) return list;
        return list.filter((c) => c.is_online);
    }

    function renderInboxItem(conversation) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'site-chat-inbox-item';
        if (conversation.staff_unread_count > 0) button.classList.add('is-unread');
        if (conversation.id === activeStaffConversationId) button.classList.add('is-active');
        if (!conversation.is_online) button.classList.add('is-offline');

        const avatarWrap = document.createElement('span');
        avatarWrap.className = 'site-chat-inbox-avatar-wrap';
        const pulse = document.createElement('span');
        pulse.className = 'site-chat-inbox-pulse';
        pulse.setAttribute('aria-hidden', 'true');
        const avatar = document.createElement('span');
        avatar.className = 'site-chat-inbox-avatar';
        avatar.textContent = initialsFromName(conversation.display_name);
        avatarWrap.append(pulse, avatar);

        // Online zeleni / offline crveni krug
        const statusDot = document.createElement('span');
        statusDot.className = conversation.is_online
            ? 'site-chat-inbox-status site-chat-inbox-status--online'
            : 'site-chat-inbox-status site-chat-inbox-status--offline';
        statusDot.title = conversation.is_online ? 'Online' : 'Offline';
        statusDot.setAttribute('aria-label', conversation.is_online ? 'Online' : 'Offline');
        avatarWrap.appendChild(statusDot);

        const title = document.createElement('strong');
        title.textContent = conversation.display_name || 'Gost';

        const preview = document.createElement('span');
        preview.className = 'site-chat-inbox-preview';
        const offlineTag = conversation.is_online ? '' : ' · offline';
        preview.textContent = (conversation.preview || 'Novi razgovor') + offlineTag;

        const meta = document.createElement('span');
        meta.className = 'site-chat-inbox-meta';
        if (conversation.staff_unread_count > 0) {
            const badge = document.createElement('span');
            badge.className = 'site-chat-inbox-badge';
            badge.textContent = conversation.staff_unread_count > 9
                ? '9+'
                : String(conversation.staff_unread_count);
            meta.appendChild(badge);
        } else if (conversation.is_online) {
            const live = document.createElement('span');
            live.className = 'site-chat-inbox-dot-live';
            live.title = 'Online';
            meta.appendChild(live);
        }

        button.append(avatarWrap, title, preview, meta);
        button.addEventListener('click', () => openStaffConversation(conversation.id));
        return button;
    }

    function paintStaffInboxList(conversations) {
        const listRoot = staffInboxList || staffInbox;
        if (!listRoot) return;
        listRoot.querySelectorAll('.site-chat-inbox-item').forEach((item) => item.remove());

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
                    ? 'Nema aktivnih razgovora.'
                    : 'Nema online kupaca. Uključi „Offline chat” da vidiš offline.';
                if (!staffInboxEmpty.parentElement) listRoot.appendChild(staffInboxEmpty);
            }
            return;
        }

        if (staffInboxEmpty) staffInboxEmpty.hidden = true;
        visible.forEach((conversation) => {
            listRoot.appendChild(renderInboxItem(conversation));
        });
    }

    async function loadStaffInbox() {
        if (!staffInbox) return;
        const data = await apiFetch('/api/chat/staff/inbox/');
        const all = data.conversations || [];
        const onlineUnread = data.online_unread_conversations
            ?? all.filter((c) => c.is_online && (c.staff_unread_count || 0) > 0).length;
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
        staffInput?.focus({ preventScroll: true });
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
        const rose = unread > lastStaffUnreadTotal;
        lastStaffUnreadTotal = unread;

        if (!rose || unread <= 0) return;

        staffAutoOpenBusy = true;
        try {
            if (!staffOpen) {
                await openStaffPanel();
            }
            // Odaberi online razgovor s nepročitanim (offline se ne otvara automatski)
            let list = conversations;
            if (!list) {
                const inbox = await apiFetch('/api/chat/staff/inbox/');
                list = inbox.conversations || [];
                const onlineUnread = inbox.online_unread_conversations
                    ?? list.filter((c) => c.is_online && (c.staff_unread_count || 0) > 0).length;
                setBadge(staffBadge, onlineUnread);
            }
            const onlineList = (list || []).filter((c) => c.is_online);
            const target = onlineList.find((c) => (c.staff_unread_count || 0) > 0)
                || onlineList[0];
            if (!target) return;
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
            if (staffOpen) {
                const inbox = await loadStaffInbox();
                const all = (inbox && inbox.conversations) || [];
                const onlineUnread = inbox?.online_unread_conversations
                    ?? all.filter((c) => c.is_online && (c.staff_unread_count || 0) > 0).length;
                lastStaffUnreadTotal = onlineUnread;

                // Nova poruka od online kupca → prebaci na taj thread
                const unreadConv = all.find(
                    (c) => c.is_online && (c.staff_unread_count || 0) > 0,
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
            } else {
                // Brzi ping + full inbox radi is_online (za auto-open)
                const inbox = await apiFetch('/api/chat/staff/inbox/');
                const all = inbox.conversations || [];
                const onlineUnread = inbox.online_unread_conversations
                    ?? all.filter((c) => c.is_online && (c.staff_unread_count || 0) > 0).length;
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
        // Brži poll kad je panel zatvoren — da chat odmah iskoči na poruku kupca
        const interval = staffOpen ? 3500 : 4000;
        staffPollTimer = setInterval(pollStaffInbox, interval);
        staffPingTimer = setInterval(async () => {
            if (!config.isStaff || staffOpen) return;
            try {
                await pollStaffInbox();
            } catch (error) {
                console.error(error);
            }
        }, 20000);
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
            staffProductSearch?.focus({ preventScroll: true });
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
        if (!config.chatEnabled || customerOpen || !customerChatRevealed) return;
        try {
            const data = await apiFetch('/api/chat/badge/');
            setBadge(customerBadge, data.customer_unread_count);
            if (data.status === 'closed') conversationClosed = true;
        } catch (error) {
            /* tiho */
        }
    }

    /* —— Proaktivni chat: NIKAKAV UI dok ne istekne delay —— */
    async function scheduleProactiveChat() {
        if (!config.chatEnabled) return;
        if (config.isStaff) return; // staff ne dobija auto popup kao kupac

        // Ako je u ovoj sesiji već otvoren — samo pokaži launcher
        if (sessionStorage.getItem(STORAGE_VISIBLE) === '1' || sessionStorage.getItem(STORAGE_PROACTIVE) === '1') {
            revealCustomerLauncher();
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
            await openCustomerChat({ proactive: true });
        }, wait);
    }

    /* —— Zatvaranje kad napusti sajt —— */
    function markInternalNavigation() {
        try {
            sessionStorage.setItem(STORAGE_INTERNAL_NAV, '1');
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

    function sendChatLeave() {
        if (leaveSent) return;
        try {
            if (sessionStorage.getItem(STORAGE_INTERNAL_NAV) === '1') {
                sessionStorage.removeItem(STORAGE_INTERNAL_NAV);
                return;
            }
        } catch (e) { /* ignore */ }

        leaveSent = true;
        const url = config.leaveUrl || '/api/chat/leave/';
        try {
            if (navigator.sendBeacon) {
                const blob = new Blob(['{}'], { type: 'application/json' });
                navigator.sendBeacon(url, blob);
            } else {
                fetch(url, {
                    method: 'POST',
                    body: '{}',
                    headers: { 'Content-Type': 'application/json' },
                    keepalive: true,
                    credentials: 'same-origin',
                }).catch(() => {});
            }
        } catch (e) { /* ignore */ }
        try {
            sessionStorage.removeItem(STORAGE_PROACTIVE);
            sessionStorage.removeItem(STORAGE_ENTER);
            sessionStorage.removeItem(STORAGE_VISIBLE);
        } catch (e) { /* ignore */ }
    }

    window.addEventListener('pagehide', sendChatLeave);
    window.addEventListener('beforeunload', sendChatLeave);

    /* init — staff: samo chat sa kupcima; kupac: proaktivni chat */
    if (customerBtn) customerBtn.hidden = true;

    if (config.isStaff) {
        if (staffBtn) staffBtn.hidden = false;
        startStaffTimers();
        // Odmah provjeri ima li nepročitanih — otvori ako kupac već piše
        (async () => {
            try {
                lastStaffUnreadTotal = 0; // forsira auto-open ako već ima online unread
                await pollStaffInbox();
            } catch (error) {
                console.error(error);
            }
        })();
    } else if (config.chatEnabled) {
        scheduleProactiveChat();
        setInterval(pollCustomerUnread, 20000);
    }

    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible' && config.isStaff) {
            pollStaffInbox();
        }
    });
});
