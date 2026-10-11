(function () {
    'use strict';

    const root = document.getElementById('publicOnlineRoot');
    if (!root) return;

    const pollUrl = root.getAttribute('data-poll-url') || '/uzivo/javno/';
    const toggle = document.getElementById('publicOnlineToggle');
    const panel = document.getElementById('publicOnlinePanel');
    const closeBtn = document.getElementById('publicOnlineClose');
    const countEl = document.getElementById('publicOnlineCount');
    const listEl = document.getElementById('publicOnlineList');
    const emptyEl = document.getElementById('publicOnlineEmpty');

    let open = false;
    let timer = null;

    function setOpen(next) {
        open = !!next;
        if (!panel || !toggle) return;
        if (open) {
            panel.hidden = false;
            toggle.setAttribute('aria-expanded', 'true');
            root.classList.add('is-open');
        } else {
            panel.hidden = true;
            toggle.setAttribute('aria-expanded', 'false');
            root.classList.remove('is-open');
        }
    }

    function render(data) {
        const items = (data && data.items) || [];
        const count = typeof data.count === 'number' ? data.count : items.length;
        if (countEl) countEl.textContent = String(count);
        if (!listEl) return;
        listEl.innerHTML = '';
        if (!items.length) {
            if (emptyEl) emptyEl.hidden = false;
            return;
        }
        if (emptyEl) emptyEl.hidden = true;
        items.forEach(function (item) {
            const li = document.createElement('li');
            li.className = 'public-online__item';
            const main = document.createElement('span');
            main.className = 'public-online__item-main';
            main.textContent = item.label || item.role || 'Gost';
            li.appendChild(main);
            if (item.looking) {
                const sub = document.createElement('span');
                sub.className = 'public-online__item-looking';
                sub.textContent = item.looking;
                li.appendChild(sub);
            }
            listEl.appendChild(li);
        });
    }

    async function poll() {
        try {
            const res = await fetch(pollUrl, {
                method: 'GET',
                headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                credentials: 'same-origin',
                cache: 'no-store',
            });
            if (res.status === 404) {
                root.hidden = true;
                if (timer) {
                    clearInterval(timer);
                    timer = null;
                }
                return;
            }
            const data = await res.json().catch(function () { return {}; });
            if (!res.ok || data.disabled) {
                root.hidden = true;
                return;
            }
            root.hidden = false;
            render(data);
        } catch (e) {
            // tiho — ne spamaj greške posjetiocu
        }
    }

    toggle && toggle.addEventListener('click', function () {
        setOpen(!open);
        if (open) poll();
    });
    closeBtn && closeBtn.addEventListener('click', function () {
        setOpen(false);
    });

    document.addEventListener('click', function (e) {
        if (!open || !root) return;
        if (!root.contains(e.target)) setOpen(false);
    });

    // prvi poll odmah, pa svakih 20 s
    poll();
    timer = window.setInterval(poll, 20000);
})();
