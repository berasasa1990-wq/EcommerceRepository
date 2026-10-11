(function () {
    'use strict';

    const root = document.getElementById('setBuilder');
    if (!root) return;

    const recommendUrl = root.getAttribute('data-recommend-url');
    const swapUrl = root.getAttribute('data-swap-url');
    const cartUrl = root.getAttribute('data-cart-url');
    const budgetInput = document.getElementById('setbBudget');
    const budgetLabel = document.getElementById('setbBudgetLabel');
    const resultEl = document.getElementById('setbResult');
    const resultMeta = document.getElementById('setbResultMeta');
    const totalWrap = document.getElementById('setbTotal');
    const totalValue = document.getElementById('setbTotalValue');
    const leftoverEl = document.getElementById('setbLeftover');
    const findBtn = document.getElementById('setbFind');
    const addBtn = document.getElementById('setbAddCart');
    const buyBtn = document.getElementById('setbBuyNow');
    const backBtn = document.getElementById('setbBack');
    const slotsNext = document.getElementById('setbSlotsNext');

    const state = {
        step: 1,
        fish: '',
        tier: 'preporuka',
        budget: parseInt(budgetInput && budgetInput.value, 10) || 300,
        items: [],
    };

    function csrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function post(url, payload) {
        return fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrf(),
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(payload),
        }).then(function (res) { return res.json(); });
    }

    function selectedSlots() {
        return Array.prototype.map.call(
            root.querySelectorAll('input[name="slot"]:checked'),
            function (el) { return el.value; }
        );
    }

    function showStep(step) {
        state.step = step;
        root.querySelectorAll('.setb-col').forEach(function (col) {
            const n = parseInt(col.getAttribute('data-step'), 10);
            const open = n === step;
            col.classList.toggle('is-open', open);
            col.hidden = !open;
        });
        root.querySelectorAll('[data-step-dot]').forEach(function (dot) {
            const n = parseInt(dot.getAttribute('data-step-dot'), 10);
            dot.classList.toggle('is-active', n === step);
            dot.classList.toggle('is-done', n < step);
        });
        if (backBtn) backBtn.hidden = step <= 1;
        root.classList.toggle('is-start', step <= 1);
        root.classList.toggle('is-result', step === 5);
        window.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function setBudget(value) {
        const n = Math.max(50, Math.min(1500, parseInt(value, 10) || 300));
        state.budget = n;
        if (budgetInput) budgetInput.value = String(n);
        if (budgetLabel) budgetLabel.textContent = String(n);
        root.querySelectorAll('.setb-preset').forEach(function (btn) {
            const bv = parseInt(btn.getAttribute('data-budget'), 10);
            btn.classList.toggle('is-picked', bv === n || (bv >= 1000 && n >= 1000));
        });
    }

    function renderItems(items) {
        state.items = items || [];
        resultEl.innerHTML = '';
        if (!state.items.length) {
            resultEl.innerHTML = '<p class="setb-empty">Nema artikala za ovaj odabir. Promijeni budžet ili slotove.</p>';
            addBtn.disabled = true;
            buyBtn.disabled = true;
            totalWrap.hidden = true;
            return;
        }
        state.items.forEach(function (item, index) {
            const row = document.createElement('article');
            row.className = 'setb-item';
            row.innerHTML =
                '<img src="' + (item.image || '') + '" alt="">' +
                '<div class="setb-item__body">' +
                '<div class="setb-item__name">' + escapeHtml(item.name) + '</div>' +
                '<div class="setb-item__price">' + escapeHtml(item.price_display) + '</div>' +
                '<button type="button" class="setb-item__swap" data-index="' + index + '">' +
                '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M4 7h11l-2-2M20 17H9l2 2M20 7v3M4 17v-3"/></svg>' +
                'Izmijeni artikal</button></div>';
            resultEl.appendChild(row);
        });
        addBtn.disabled = false;
        buyBtn.disabled = false;
        totalWrap.hidden = false;
    }

    function escapeHtml(value) {
        return String(value || '').replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }

    function findSet() {
        if (!state.fish) return;
        findBtn.disabled = true;
        post(recommendUrl, {
            fish: state.fish,
            tier: state.tier,
            slots: selectedSlots(),
            budget: state.budget,
        }).then(function (data) {
            findBtn.disabled = false;
            if (!data || !data.ok) return;
            showStep(5);
            resultMeta.textContent = (data.title || '') + (data.budget_display ? ' • Budžet: do ' + data.budget_display : '');
            renderItems(data.items || []);
            totalValue.textContent = data.total_display || '';
            leftoverEl.textContent = data.leftover && parseFloat(data.leftover) > 0
                ? ('Ušteda u odnosu na budžet: ' + data.leftover_display)
                : '';
        }).catch(function () {
            findBtn.disabled = false;
        });
    }

    function closePops() {
        root.querySelectorAll('.setb-alts, .setb-alts__hint, .setb-pop').forEach(function (el) {
            el.remove();
        });
        root.querySelectorAll('.setb-item.is-picked').forEach(function (el) {
            el.classList.remove('is-picked');
        });
    }

    function openSwap(index, btn) {
        const item = state.items[index];
        if (!item) return;
        const host = btn.closest('.setb-item');
        if (host.classList.contains('is-picked')) {
            closePops();
            return;
        }
        closePops();
        host.classList.add('is-picked');
        const hint = document.createElement('p');
        hint.className = 'setb-alts__hint';
        hint.textContent = 'Odaberi zamjenu — prevuci lijevo-desno';
        const strip = document.createElement('div');
        strip.className = 'setb-alts';
        strip.innerHTML = '<span class="setb-alts__empty">Učitavanje…</span>';
        host.appendChild(hint);
        host.appendChild(strip);
        post(swapUrl, {
            slot: item.slot,
            product_id: item.id,
            fish: state.fish,
            budget: state.budget,
            exclude: state.items.map(function (it) { return it.id; }),
        }).then(function (data) {
            if (!host.isConnected) return;
            const rows = (data && data.items) || [];
            strip.innerHTML = '';
            if (!rows.length) {
                strip.innerHTML = '<span class="setb-alts__empty">Nema drugih opcija.</span>';
                return;
            }
            rows.forEach(function (alt) {
                const b = document.createElement('button');
                b.type = 'button';
                b.className = 'setb-alts__card';
                b.innerHTML =
                    '<img src="' + (alt.image || '') + '" alt="">' +
                    '<strong>' + escapeHtml(alt.name) + '</strong>' +
                    '<span>' + escapeHtml(alt.price_display) + '</span>';
                b.addEventListener('click', function () {
                    state.items[index] = alt;
                    closePops();
                    renderItems(state.items);
                    recalcLocal();
                });
                strip.appendChild(b);
            });
        });
    }

    function recalcLocal() {
        let total = 0;
        state.items.forEach(function (it) {
            total += parseFloat(it.price || 0) * (it.quantity || 1);
        });
        totalValue.textContent = total.toFixed(2).replace('.', ',') + ' KM';
        const leftover = Math.max(0, state.budget - total);
        leftoverEl.textContent = leftover > 0
            ? ('Ušteda u odnosu na budžet: ' + leftover.toFixed(2).replace('.', ',') + ' KM')
            : '';
    }

    function addSet(buyNow) {
        const ids = state.items.map(function (it) { return it.id; });
        if (!ids.length) return;
        addBtn.disabled = true;
        buyBtn.disabled = true;
        post(cartUrl, { product_ids: ids, buy_now: buyNow ? 1 : 0 }).then(function (data) {
            addBtn.disabled = false;
            buyBtn.disabled = false;
            if (!data || !data.ok) {
                window.alert((data && data.message) || 'Set se nije mogao dodati.');
                return;
            }
            if (data.redirect) {
                window.location.href = data.redirect;
            }
        }).catch(function () {
            addBtn.disabled = false;
            buyBtn.disabled = false;
        });
    }

    root.querySelectorAll('[data-fish]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            state.fish = btn.getAttribute('data-fish');
            root.querySelectorAll('[data-fish]').forEach(function (el) {
                el.classList.toggle('is-picked', el === btn);
            });
            showStep(2);
        });
    });

    root.querySelectorAll('[data-tier]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            state.tier = btn.getAttribute('data-tier');
            root.querySelectorAll('[data-tier]').forEach(function (el) {
                el.classList.toggle('is-picked', el === btn);
            });
            showStep(3);
        });
    });

    if (slotsNext) {
        slotsNext.addEventListener('click', function () {
            if (!selectedSlots().length) {
                window.alert('Označi barem jednu stavku za set.');
                return;
            }
            showStep(4);
        });
    }

    if (budgetInput) {
        budgetInput.addEventListener('input', function () {
            setBudget(budgetInput.value);
        });
    }
    root.querySelectorAll('.setb-preset').forEach(function (btn) {
        btn.addEventListener('click', function () {
            setBudget(btn.getAttribute('data-budget'));
        });
    });
    if (findBtn) findBtn.addEventListener('click', findSet);
    if (addBtn) addBtn.addEventListener('click', function () { addSet(false); });
    if (buyBtn) buyBtn.addEventListener('click', function () { addSet(true); });
    resultEl.addEventListener('click', function (ev) {
        const btn = ev.target.closest('.setb-item__swap');
        if (!btn) return;
        openSwap(parseInt(btn.getAttribute('data-index'), 10), btn);
    });

    document.addEventListener('click', function (ev) {
        if (!ev.target.closest('.setb-item')) closePops();
    });

    if (backBtn) {
        backBtn.addEventListener('click', function () {
            if (state.step > 1) showStep(state.step - 1);
        });
    }

    setBudget(state.budget);
    showStep(1);
})();
