(function () {
    'use strict';

    const gate = document.getElementById('setBuilderGate');
    if (!gate) return;

    const form = document.getElementById('setBuilderGateForm');
    const input = document.getElementById('setBuilderGatePassword');
    const errorEl = document.getElementById('setBuilderGateError');
    const unlockUrl = gate.getAttribute('data-unlock-url');
    const targetUrl = gate.getAttribute('data-target-url') || '/kreiraj-set/';
    const autoOpen = gate.getAttribute('data-auto-open') === '1';

    function unlocked() {
        return gate.getAttribute('data-unlocked') === '1';
    }

    function openGate() {
        gate.hidden = false;
        document.body.classList.add('setb-gate-open');
        if (input) {
            input.value = '';
            window.setTimeout(function () { input.focus(); }, 50);
        }
        if (errorEl) {
            errorEl.hidden = true;
            errorEl.textContent = '';
        }
    }

    function closeGate() {
        gate.hidden = true;
        document.body.classList.remove('setb-gate-open');
    }

    function csrf() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const field = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (field && field.value) return field.value;
        const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    document.querySelectorAll('[data-set-builder-gate]').forEach(function (link) {
        link.addEventListener('click', function (ev) {
            if (unlocked()) return;
            ev.preventDefault();
            openGate();
        });
    });

    gate.querySelectorAll('[data-set-builder-close]').forEach(function (el) {
        el.addEventListener('click', closeGate);
    });

    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && !gate.hidden) closeGate();
    });

    if (form) {
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            const password = (input && input.value) || '';
            fetch(unlockUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrf(),
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: JSON.stringify({ password: password }),
            }).then(function (res) { return res.json().then(function (data) { return { res: res, data: data }; }); })
                .then(function (pack) {
                    if (pack.data && pack.data.ok) {
                        gate.setAttribute('data-unlocked', '1');
                        window.location.href = pack.data.redirect || targetUrl;
                        return;
                    }
                    if (errorEl) {
                        errorEl.hidden = false;
                        errorEl.textContent = (pack.data && pack.data.message) || 'Pogrešna lozinka.';
                    }
                }).catch(function () {
                    if (errorEl) {
                        errorEl.hidden = false;
                        errorEl.textContent = 'Greška. Pokušaj ponovo.';
                    }
                });
        });
    }

    if (autoOpen && !unlocked()) openGate();
})();
