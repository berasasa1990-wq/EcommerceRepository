(function () {
    'use strict';

    const root = document.getElementById('fishAdvisor');
    if (!root) return;

    const url = root.getAttribute('data-advisor-url') || '/savjetnik/';
    const buySetUrl = root.getAttribute('data-buy-set-url') || '/savjetnik/kupi-set/';
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

    /** Samo trenutno pitanje — bez historije chata (bez spama). */
    function showOnlyQuestion(text) {
        if (!messagesEl) return;
        messagesEl.innerHTML = '';
        if (!text) return;
        const row = document.createElement('div');
        row.className = 'fish-advisor__msg fish-advisor__msg--bot fish-advisor__msg--current';
        const bubble = document.createElement('div');
        bubble.className = 'fish-advisor__bubble';
        String(text).split('\n').forEach((line, i) => {
            if (i > 0) bubble.appendChild(document.createElement('br'));
            bubble.appendChild(document.createTextNode(line));
        });
        row.appendChild(bubble);
        messagesEl.appendChild(row);
    }

    function showTyping(on) {
        if (!messagesEl) return;
        let el = messagesEl.querySelector('.fish-advisor__typing');
        if (on) {
            messagesEl.innerHTML = '';
            el = document.createElement('div');
            el.className = 'fish-advisor__msg fish-advisor__msg--bot fish-advisor__typing';
            el.innerHTML = '<div class="fish-advisor__bubble"><span></span><span></span><span></span></div>';
            messagesEl.appendChild(el);
        } else if (el) {
            el.remove();
        }
    }

    function clearOptions() {
        if (optionsEl) optionsEl.innerHTML = '';
    }

    function clearProducts() {
        if (!productsEl) return;
        productsEl.hidden = true;
        productsEl.innerHTML = '';
    }

    function restartAdvisor() {
        if (messagesEl) messagesEl.innerHTML = '';
        clearProducts();
        state = {};
        step = 'start';
        sendStep('start', '');
    }

    function toast(msg) {
        if (typeof window.showCartToast === 'function') {
            window.showCartToast(msg);
            return;
        }
        let el = panel && panel.querySelector('.fish-advisor__toast');
        if (!el && panel) {
            el = document.createElement('div');
            el.className = 'fish-advisor__toast';
            panel.appendChild(el);
        }
        if (!el) return;
        el.textContent = msg;
        el.classList.add('is-visible');
        window.setTimeout(() => el.classList.remove('is-visible'), 2200);
    }

    function updateCartBadge(count) {
        const n = Math.max(0, parseInt(count, 10) || 0);
        const cartBtn = document.querySelector('.cart-btn');
        if (!cartBtn) return;
        let badge = cartBtn.querySelector('.cart-badge');
        if (n > 0) {
            cartBtn.classList.add('cart-btn--has-items');
            if (!badge) {
                badge = document.createElement('span');
                badge.className = 'cart-badge';
                cartBtn.appendChild(badge);
            }
            badge.textContent = String(n);
        } else {
            cartBtn.classList.remove('cart-btn--has-items');
            if (badge) badge.remove();
        }
        cartBtn.dataset.cartCount = String(n);
    }

    async function addProductToCart(slug, button) {
        if (!slug) {
            toast('Artikal nije dostupan.');
            return;
        }
        const token = csrf();
        if (!token) {
            toast('CSRF token nedostaje — osvježi stranicu.');
            return;
        }
        if (button) {
            button.disabled = true;
            button.classList.add('is-loading');
        }
        try {
            const body = new URLSearchParams({ quantity: '1', stay: '1' });
            const res = await fetch('/artikal/' + encodeURIComponent(slug) + '/dodaj/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': token,
                    'X-Requested-With': 'XMLHttpRequest',
                    Accept: 'application/json',
                },
                credentials: 'same-origin',
                body: body.toString(),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                throw new Error(data.message || 'Dodavanje u korpu nije uspjelo.');
            }
            if (data.cart_count != null) updateCartBadge(data.cart_count);
            toast(data.message || 'Dodano u korpu.');
            if (button) {
                button.textContent = '✓ U korpi';
                button.classList.add('is-added');
                window.setTimeout(() => {
                    if (button) {
                        button.textContent = 'Kupi';
                        button.classList.remove('is-added');
                        button.disabled = false;
                        button.classList.remove('is-loading');
                    }
                }, 1600);
                return;
            }
        } catch (err) {
            toast(err.message || 'Greška pri dodavanju.');
            if (button) {
                button.disabled = false;
                button.classList.remove('is-loading');
            }
        }
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
            if (opt.id === 'continue' || opt.id === 'view_kit' || opt.id === 'accessories_yes') {
                btn.classList.add('fish-advisor__option--primary');
            }
            if (opt.id === 'again' || opt.id === 'reset' || opt.id === 'no_kit') {
                btn.classList.add('fish-advisor__option--ghost');
            }
            btn.textContent = opt.label || opt.id;
            btn.dataset.answer = opt.id || '';
            btn.addEventListener('click', () => {
                if (busy) return;
                const ans = btn.dataset.answer;

                if (ans === 'again' || ans === 'reset') {
                    restartAdvisor();
                    return;
                }

                // Bez prikaza odabranog odgovora — odmah sljedeće pitanje
                if (ans === 'continue') {
                    clearProducts();
                }
                sendStep(step, ans);
            });
            optionsEl.appendChild(btn);
        });
    }

    function roleLabel(role) {
        if (role === 'stap') return 'Štap';
        if (role === 'masinica') return 'Mašinica';
        if (role === 'najlon') return 'Najlon';
        if (role === 'varalice') return 'Varalica';
        if (role === 'signalizatori') return 'Signalizator';
        if (role === 'komplet') return 'U setu';
        if (role === 'pribor') return 'Pribor';
        if (role === 'hranilica' || role === 'hranilice') return 'Hranilica';
        return 'Oprema';
    }

    function renderProductCard(p) {
        const card = document.createElement('div');
        card.className = 'fish-advisor__product' + (p.in_stock === false ? ' is-oos' : '');

        const link = document.createElement('a');
        link.className = 'fish-advisor__product-main';
        link.href = p.url || '#';
        link.target = '_blank';
        link.rel = 'noopener';

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

        link.append(imgWrap, meta);

        const buyBtn = document.createElement('button');
        buyBtn.type = 'button';
        buyBtn.className = 'fish-advisor__buy';
        buyBtn.textContent = p.in_stock === false ? 'Nedostupno' : 'Kupi';
        buyBtn.disabled = p.in_stock === false || !p.slug;
        buyBtn.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            if (buyBtn.disabled) return;
            addProductToCart(p.slug, buyBtn);
        });

        card.append(link, buyBtn);
        return card;
    }

    async function buyWholeSet(setId, button) {
        if (!setId) return;
        const token = csrf();
        if (!token) {
            toast('CSRF token nedostaje — osvježi stranicu.');
            return;
        }
        if (button) {
            button.disabled = true;
            button.classList.add('is-loading');
        }
        try {
            const body = new URLSearchParams({ set_id: String(setId) });
            const res = await fetch(buySetUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': token,
                    'X-Requested-With': 'XMLHttpRequest',
                    Accept: 'application/json',
                },
                credentials: 'same-origin',
                body: body.toString(),
            });
            const data = await res.json().catch(() => ({}));
            if (!res.ok || !data.ok) {
                throw new Error(data.message || 'Set nije dodan.');
            }
            if (data.cart_count != null) updateCartBadge(data.cart_count);
            toast(data.message || 'Set dodan u korpu.');
            if (button) {
                button.innerHTML = '✓ Set je u korpi';
                button.classList.add('is-added');
                window.setTimeout(() => {
                    button.textContent = button.dataset.label || '🎣 Kupi set';
                    button.classList.remove('is-added');
                    button.disabled = false;
                    button.classList.remove('is-loading');
                }, 2000);
                return;
            }
        } catch (err) {
            toast(err.message || 'Greška.');
            if (button) {
                button.disabled = false;
                button.classList.remove('is-loading');
            }
        }
    }

    function renderProducts(rec, kits) {
        if (!productsEl) return;
        productsEl.innerHTML = '';

        const kitList = (kits && kits.length) ? kits : (rec && rec.kits) || null;
        const flat = (rec && rec.products) || [];

        if ((!kitList || !kitList.length) && !flat.length) {
            productsEl.hidden = true;
            return;
        }
        productsEl.hidden = false;

        if (kitList && kitList.length) {
            kitList.forEach((kit, idx) => {
                const block = document.createElement('div');
                block.className = 'fish-advisor__kit fish-advisor__kit--featured' + (idx === 0 ? ' is-top' : '');

                const badge = document.createElement('div');
                badge.className = 'fish-advisor__kit-badge';
                badge.textContent = idx === 0 ? '★ Preporučeno' : 'Komplet';
                block.appendChild(badge);

                const head = document.createElement('div');
                head.className = 'fish-advisor__kit-title';
                head.innerHTML =
                    '<span class="fish-advisor__kit-emoji">' +
                    (kit.emoji || '🎣') +
                    '</span><h3>' +
                    (kit.label || 'Komplet') +
                    '</h3>';
                block.appendChild(head);

                const priceRow = document.createElement('div');
                priceRow.className = 'fish-advisor__kit-price-row';
                if (kit.has_discount && kit.regular_total_display) {
                    priceRow.innerHTML =
                        '<del class="fish-advisor__kit-was">' +
                        kit.regular_total_display +
                        '</del>' +
                        '<span class="fish-advisor__kit-now">' +
                        (kit.total_display || '') +
                        '</span>' +
                        (kit.discount_percent
                            ? '<span class="fish-advisor__kit-pct">−' + kit.discount_percent + '%</span>'
                            : '');
                } else {
                    priceRow.innerHTML =
                        '<span class="fish-advisor__kit-now">' +
                        (kit.total_display || '') +
                        '</span>';
                }
                block.appendChild(priceRow);

                const list = document.createElement('div');
                list.className = 'fish-advisor__product-list';
                (kit.products || []).forEach((p) => {
                    list.appendChild(renderProductCard(p));
                });
                block.appendChild(list);

                const setId = kit.db_id || (String(kit.id || '').match(/^\d+$/) ? kit.id : null);
                if (setId && (kit.products || []).length) {
                    const buySet = document.createElement('button');
                    buySet.type = 'button';
                    buySet.className = 'fish-advisor__buy-set';
                    const label = kit.has_discount && kit.discount_percent
                        ? '🎣 Kupi set (−' + kit.discount_percent + '%)'
                        : '🎣 Kupi cijeli set';
                    buySet.textContent = label;
                    buySet.dataset.label = label;
                    buySet.addEventListener('click', () => buyWholeSet(setId, buySet));
                    block.appendChild(buySet);
                }

                productsEl.appendChild(block);
            });
        } else {
            const head = document.createElement('div');
            head.className = 'fish-advisor__products-head';
            const title = (rec && (rec.item_label || rec.style_label)) || 'Preporuka';
            head.innerHTML = '<strong>' + title + '</strong>';
            productsEl.appendChild(head);

            const list = document.createElement('div');
            list.className = 'fish-advisor__product-list';
            flat.forEach((p) => list.appendChild(renderProductCard(p)));
            productsEl.appendChild(list);
        }
    }

    async function sendStep(nextStep, answer) {
        if (busy) return;
        busy = true;
        clearOptions();
        // pitanja: ne zadržavaj stare poruke
        const keepProducts = nextStep === 'results' || step === 'results' || step === 'post' || step === 'single';
        if (!keepProducts) {
            clearProducts();
        }
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
                showOnlyQuestion('Ups, nešto nije uredu. Pokušaj ponovo.');
                renderOptions([{ id: 'reset', label: '🔄 Ispočetka' }]);
                step = 'start';
                return;
            }
            state = data.state || state;
            step = data.step || nextStep;

            // Samo NOVO pitanje / poruka — bez historije
            const msgText = (data.messages || [])
                .map((m) => (m && m.text) || '')
                .filter(Boolean)
                .join('\n\n');
            showOnlyQuestion(msgText);

            if (data.recommendation || data.kits) {
                renderProducts(
                    data.recommendation || {},
                    data.kits || (data.recommendation && data.recommendation.kits),
                );
            } else if (
                answer === 'continue' ||
                answer === 'no_kit' ||
                data.step === 'experience' ||
                data.step === 'fish' ||
                data.step === 'water' ||
                data.step === 'budget' ||
                data.step === 'technique' ||
                data.step === 'kit_level' ||
                data.step === 'owned'
            ) {
                clearProducts();
            }
            renderOptions(data.options || []);
        } catch (err) {
            showTyping(false);
            showOnlyQuestion('Konekcija nije uspjela. Pokušaj opet.');
            renderOptions([{ id: 'reset', label: '🔄 Ispočetka' }]);
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
