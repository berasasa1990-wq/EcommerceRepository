(function () {
    var overlay = document.getElementById('stockNotifyOverlay');
    if (!overlay) return;

    var form = document.getElementById('stockNotifyForm');
    var emailInput = document.getElementById('stockNotifyEmail');
    var errorEl = document.getElementById('stockNotifyError');
    var nameEl = document.getElementById('stockNotifyProductName');
    var submitBtn = document.getElementById('stockNotifySubmit');
    var loggedIn = document.body.getAttribute('data-stock-notify-logged') === '1';
    var activeUrl = '';
    var activeBtn = null;

    function csrfToken() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return match ? decodeURIComponent(match[1]) : '';
    }

    function showError(msg) {
        if (!errorEl) return;
        errorEl.hidden = !msg;
        errorEl.textContent = msg || '';
    }

    function openPopup(name) {
        if (nameEl) nameEl.textContent = name || 'artikal';
        showError('');
        if (emailInput) emailInput.value = '';
        overlay.hidden = false;
        document.body.classList.add('popup-open');
        if (emailInput) emailInput.focus();
    }

    function closePopup() {
        overlay.hidden = true;
        document.body.classList.remove('popup-open');
        activeUrl = '';
        activeBtn = null;
        showError('');
    }

    function markDone(btn, message) {
        if (!btn) return;
        btn.classList.add('is-subscribed');
        btn.disabled = true;
        var label = btn.querySelector('[data-stock-notify-label]');
        if (label) label.textContent = 'Javit ćemo ti na email';
        else btn.textContent = 'Javit ćemo ti na email';
        if (message) btn.setAttribute('title', message);
    }

    function postNotify(url, email, btn) {
        var body = new URLSearchParams();
        if (email) body.set('email', email);
        return fetch(url, {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
                'X-CSRFToken': csrfToken(),
                'X-Requested-With': 'XMLHttpRequest',
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            },
            body: body.toString(),
        }).then(function (res) {
            return res.json().then(function (data) {
                data._status = res.status;
                return data;
            });
        }).then(function (data) {
            if (data && data.ok) {
                markDone(btn, data.message);
                closePopup();
                return;
            }
            var err = (data && data.error) || 'Pokušaj ponovo.';
            if (overlay.hidden === false) showError(err);
            else window.alert(err);
        }).catch(function () {
            var err = 'Mreža nije dostupna. Pokušaj ponovo.';
            if (overlay.hidden === false) showError(err);
            else window.alert(err);
        });
    }

    document.addEventListener('click', function (ev) {
        var close = ev.target.closest('[data-stock-notify-close]');
        if (close) {
            ev.preventDefault();
            closePopup();
            return;
        }
        var btn = ev.target.closest('[data-stock-notify]');
        if (!btn || btn.disabled) return;
        ev.preventDefault();
        var url = btn.getAttribute('data-notify-url') || '';
        if (!url) return;
        var name = btn.getAttribute('data-product-name') || '';
        if (loggedIn) {
            btn.disabled = true;
            postNotify(url, '', btn).finally(function () {
                if (!btn.classList.contains('is-subscribed')) btn.disabled = false;
            });
            return;
        }
        activeUrl = url;
        activeBtn = btn;
        openPopup(name);
    });

    if (form) {
        form.addEventListener('submit', function (ev) {
            ev.preventDefault();
            var email = (emailInput && emailInput.value || '').trim();
            if (!email || email.indexOf('@') < 1) {
                showError('Unesi ispravan email.');
                if (emailInput) emailInput.focus();
                return;
            }
            if (!activeUrl) return;
            if (submitBtn) submitBtn.disabled = true;
            postNotify(activeUrl, email, activeBtn).finally(function () {
                if (submitBtn) submitBtn.disabled = false;
            });
        });
    }

    document.addEventListener('keydown', function (ev) {
        if (ev.key === 'Escape' && overlay.hidden === false) closePopup();
    });
})();
