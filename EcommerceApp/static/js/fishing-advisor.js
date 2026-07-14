(function () {
    'use strict';

    const root = document.getElementById('fishAdvisor');
    if (!root) return;

    const url = root.getAttribute('data-advisor-url') || '/savjetnik/';
    const launcher = document.getElementById('fishAdvisorLauncher');
    const panel = document.getElementById('fishAdvisorPanel');
    const closeBtn = document.getElementById('fishAdvisorClose');
    const messagesEl = document.getElementById('fishAdvisorMessages');
    const optionsEl = document.getElementById('fishAdvisorOptions');
    const productsEl = document.getElementById('fishAdvisorProducts');

    let open = false;
    let step = 'start';
    let state = {};
    let busy = false;
    let started = false;

    function csrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function setOpen(next) {
        open = !!next;
        if (!panel || !launcher) return;
        if (open) {
            panel.hidden = false;
            panel.classList.add('is-open');
            launcher.setAttribute('aria-expanded', 'true');
            launcher.classList.add('is-open');
            document.body.classList.add('fish-advisor-open');
            if (!started) {
                started = true;
                sendStep('start', '');
            }
        } else {
            panel.classList.remove('is-open');
            panel.hidden = true;
            launcher.setAttribute('aria-expanded', 'false');
            launcher.classList.remove('is-open');
            document.body.classList.remove('fish-advisor-open');
        }
    }

    function scrollMessages() {
        if (!messagesEl) return;
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function appendMessage(role, text) {
        if (!messagesEl || !text) return;
        const row = document.createElement('div');
        row.className = 'fish-advisor__msg fish-advisor__msg--' + role;
        const bubble = document.createElement('div');
        bubble.className = 'fish-advisor__bubble';
        // Preserve line breaks
        String(text).split('\n').forEach((line, i) => {
            if (i > 0) bubble.appendChild(document.createElement('br'));
            bubble.appendChild(document.createTextNode(line));
        });
        row.appendChild(bubble);
        messagesEl.appendChild(row);
        scrollMessages();
    }

    function appendUserChoice(label) {
        appendMessage('user', label);
    }

    function showTyping(on) {
        if (!messagesEl) return;
        let el = messagesEl.querySelector('.fish-advisor__typing');
        if (on) {
            if (el) return;
            el = document.createElement('div');
            el.className = 'fish-advisor__msg fish-advisor__msg--bot fish-advisor__typing';
            el.innerHTML = '<div class="fish-advisor__bubble"><span></span><span></span><span></span></div>';
            messagesEl.appendChild(el);
            scrollMessages();
        } else if (el) {
            el.remove();
        }
    }

    function clearOptions() {
        if (optionsEl) optionsEl.innerHTML = '';
    }

    function renderOptions(options) {
        clearOptions();
        if (!optionsEl || !options || !options.length) return;
        options.forEach((opt) => {
            if (!opt) return;
            if (opt.url) {
                const a = document.createElement('a');
                a.className = 'fish-advisor__option fish-advisor__option--link';
                a.href = opt.url;
                a.textContent = opt.label || opt.id;
                optionsEl.appendChild(a);
                return;
            }
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'fish-advisor__option';
            btn.textContent = opt.label || opt.id;
            btn.dataset.answer = opt.id || '';
            btn.addEventListener('click', () => {
                if (busy) return;
                const ans = btn.dataset.answer;
                const label = btn.textContent;
                if (ans === 'again' || ans === 'reset') {
                    messagesEl.innerHTML = '';
                    if (productsEl) {
                        productsEl.hidden = true;
                        productsEl.innerHTML = '';
                    }
                    state = {};
                    step = 'start';
                    sendStep('start', '');
                    return;
                }
                appendUserChoice(label);
                sendStep(step, ans);
            });
            optionsEl.appendChild(btn);
        });
    }

    function roleLabel(role) {
        if (role === 'stap') return 'Štap';
        if (role === 'masinica') return 'Mašinica';
        if (role === 'najlon') return 'Najlon';
        if (role === 'hranilica' || role === 'hranilice') return 'Hranilica';
        if (role === 'set') return 'Set';
        return 'Oprema';
    }

    function renderProducts(rec) {
        if (!productsEl) return;
        productsEl.innerHTML = '';
        if (!rec || !rec.products || !rec.products.length) {
            productsEl.hidden = true;
            return;
        }
        productsEl.hidden = false;

        const head = document.createElement('div');
        head.className = 'fish-advisor__products-head';
        let right = rec.total_display || '';
        if (rec.budget) {
            right = (rec.total_display || '') + ' / do ' + rec.budget + ' KM';
        } else if (rec.diameter) {
            right = '~' + String(rec.diameter).replace('.', ',') + ' mm';
        } else if (rec.weight_g) {
            right = '~' + rec.weight_g + ' g';
        }
        const title = rec.item_label || rec.style_label || 'Preporuka';
        head.innerHTML = '<strong>' + title + '</strong><span>' + right + '</span>';
        productsEl.appendChild(head);

        const list = document.createElement('div');
        list.className = 'fish-advisor__product-list';

        rec.products.forEach((p) => {
            const card = document.createElement('a');
            card.className = 'fish-advisor__product' + (p.in_stock === false ? ' is-oos' : '');
            card.href = p.url || '#';
            card.target = '_blank';
            card.rel = 'noopener';

            const imgWrap = document.createElement('div');
            imgWrap.className = 'fish-advisor__product-img';
            if (p.image) {
                const img = document.createElement('img');
                img.src = p.image;
                img.alt = p.name || '';
                img.loading = 'lazy';
                imgWrap.appendChild(img);
            } else {
                imgWrap.textContent = '🎣';
            }

            const meta = document.createElement('div');
            meta.className = 'fish-advisor__product-meta';
            const badge = document.createElement('span');
            badge.className = 'fish-advisor__product-role';
            badge.textContent = roleLabel(p.role) + (p.in_stock === false ? ' · rasprodato' : '');
            const name = document.createElement('strong');
            name.textContent = p.name || '';
            const price = document.createElement('span');
            price.className = 'fish-advisor__product-price';
            price.textContent = p.price_display || (p.price + ' KM');
            meta.append(badge, name, price);
            if (p.note) {
                const note = document.createElement('em');
                note.className = 'fish-advisor__product-note';
                note.textContent = p.note;
                meta.appendChild(note);
            }

            card.append(imgWrap, meta);
            list.appendChild(card);
        });

        productsEl.appendChild(list);
        scrollMessages();
    }

    async function sendStep(nextStep, answer) {
        if (busy) return;
        busy = true;
        clearOptions();
        showTyping(true);
        try {
            const token = csrf();
            const res = await fetch(url, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    Accept: 'application/json',
                    'X-CSRFToken': token,
                    'X-Requested-With': 'XMLHttpRequest',
                },
                credentials: 'same-origin',
                body: JSON.stringify({
                    step: nextStep,
                    answer: answer,
                    state: state,
                }),
            });
            const data = await res.json().catch(() => ({}));
            showTyping(false);
            if (!res.ok || !data.ok) {
                appendMessage('bot', 'Ups, nešto nije uredu. Pokušaj ponovo.');
                renderOptions([{ id: 'reset', label: '🔄 Ponovo' }]);
                step = 'start';
                return;
            }
            state = data.state || state;
            step = data.step || nextStep;
            (data.messages || []).forEach((m) => {
                if (m && m.text) appendMessage(m.role || 'bot', m.text);
            });
            if (data.recommendation) {
                renderProducts(data.recommendation);
            }
            renderOptions(data.options || []);
        } catch (err) {
            showTyping(false);
            appendMessage('bot', 'Konekcija nije uspjela. Provjeri internet i pokušaj opet.');
            renderOptions([{ id: 'reset', label: '🔄 Ponovo' }]);
        } finally {
            busy = false;
        }
    }

    launcher &&
        launcher.addEventListener('click', () => {
            setOpen(!open);
        });
    closeBtn &&
        closeBtn.addEventListener('click', () => {
            setOpen(false);
        });

    // Soft invite after 12s (once per session tab)
    try {
        if (!sessionStorage.getItem('fish_advisor_nudge')) {
            window.setTimeout(() => {
                if (!open && launcher) {
                    launcher.classList.add('is-nudge');
                    sessionStorage.setItem('fish_advisor_nudge', '1');
                    window.setTimeout(() => launcher.classList.remove('is-nudge'), 6000);
                }
            }, 12000);
        }
    } catch (e) {}
})();
