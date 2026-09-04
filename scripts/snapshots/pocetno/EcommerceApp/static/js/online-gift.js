(function () {
    'use strict';

    var overlay = document.getElementById('onlineGiftOverlay');
    if (!overlay) return;

    var dismissUrl = overlay.getAttribute('data-dismiss-url') || '/online-nagrada/zatvori/';
    var giftId = overlay.getAttribute('data-id') || '0';
    var delay = Math.max(0, parseInt(overlay.getAttribute('data-delay') || '0', 10) || 0);
    var showNow = overlay.getAttribute('data-show-now') === '1';
    var forceShow = overlay.getAttribute('data-force-show') === '1';
    var closedKey = 'og_closed_' + giftId;
    var isOpen = false;
    var form = document.getElementById('lcRegisterForm');
    var ime = document.getElementById('lcIme');
    var prezime = document.getElementById('lcPrezime');
    var imePrezime = document.getElementById('lcImePrezime');
    var terms = document.getElementById('lcTerms');

    function isClosed() {
        try { return sessionStorage.getItem(closedKey) === '1'; } catch (e) { return false; }
    }
    function markClosed() {
        try { sessionStorage.setItem(closedKey, '1'); } catch (e) {}
    }
    function csrf() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        var m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }
    function postDismiss() {
        var t = csrf();
        if (!t) return;
        var body = new URLSearchParams();
        body.set('csrfmiddlewaretoken', t);
        body.set('reason', 'register');
        fetch(dismissUrl, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded',
                'X-CSRFToken': t,
                'X-Requested-With': 'XMLHttpRequest',
                'Accept': 'application/json',
            },
            credentials: 'same-origin',
            body: body.toString(),
            keepalive: true,
        }).catch(function () {});
    }
    function open() {
        if (isOpen) return;
        if (!forceShow && isClosed()) return;
        overlay.hidden = false;
        overlay.removeAttribute('hidden');
        overlay.classList.remove('og-side');
        overlay.classList.add('lc-overlay');
        void overlay.offsetWidth;
        overlay.classList.add('is-open');
        document.body.classList.add('og-open');
        isOpen = true;
    }
    function close() {
        markClosed();
        overlay.classList.remove('is-open');
        document.body.classList.remove('og-open');
        isOpen = false;
        overlay.hidden = true;
        overlay.setAttribute('hidden', '');
        postDismiss();
    }

    overlay.querySelectorAll('[data-og-close]').forEach(function (btn) {
        btn.addEventListener('click', function (e) {
            e.preventDefault();
            close();
        });
    });
    overlay.addEventListener('click', function (e) {
        if (e.target === overlay) close();
    });
    document.addEventListener('keydown', function (e) {
        if (e.key === 'Escape' && isOpen) close();
    });

    overlay.querySelectorAll('.lc-login-link, .lc-login a').forEach(function (link) {
        link.addEventListener('click', function (e) {
            e.stopPropagation();
            markClosed();
            postDismiss();
            var href = link.getAttribute('href') || '/prijava/?next=/';
            if (!href) {
                e.preventDefault();
                window.location.href = '/prijava/?next=/';
            }
        });
    });

    if (form) {
        form.addEventListener('submit', function (e) {
            var first = (ime && ime.value || '').trim();
            var last = (prezime && prezime.value || '').trim();
            if (!first || !last) {
                e.preventDefault();
                if (ime) ime.focus();
                return;
            }
            if (terms && !terms.checked) {
                e.preventDefault();
                terms.focus();
                return;
            }
            if (imePrezime) imePrezime.value = (first + ' ' + last).trim();
        });
    }

    if (forceShow || showNow) {
        open();
    } else if (delay <= 0) {
        open();
    } else {
        window.setTimeout(open, delay * 1000);
    }
})();
