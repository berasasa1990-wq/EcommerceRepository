(function () {
    'use strict';
    var config = document.currentScript;
    var cookieName = config.dataset.cookieName || 'csrftoken';

    function updateTokens(token) {
        if (!token) return;
        document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(function (input) {
            input.value = token;
        });
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (meta) meta.content = token;
    }

    function syncCookie() {
        var prefix = cookieName + '=';
        var cookie = document.cookie.split(';').map(function (part) { return part.trim(); })
            .find(function (part) { return part.indexOf(prefix) === 0; });
        if (cookie) updateTokens(decodeURIComponent(cookie.slice(prefix.length)));
    }

    // A login in another tab rotates the cookie while this document stays open.
    window.addEventListener('pageshow', syncCookie);
    window.addEventListener('focus', syncCookie);
    document.addEventListener('visibilitychange', function () {
        if (!document.hidden) syncCookie();
    });
    document.addEventListener('submit', syncCookie, true);
    syncCookie();

    document.querySelectorAll('form[data-csrf-refresh-url]').forEach(function (form) {
        var pending = false;
        var ready = false;
        var error = document.createElement('p');
        error.className = 'form-error';
        error.setAttribute('role', 'alert');
        error.hidden = true;
        form.appendChild(error);

        form.addEventListener('submit', async function (event) {
            if (ready) {
                ready = false;
                return;
            }
            event.preventDefault();
            if (pending) return;
            pending = true;
            error.hidden = true;
            var button = event.submitter;
            if (button) button.disabled = true;
            try {
                var response = await fetch(form.dataset.csrfRefreshUrl, {
                    credentials: 'same-origin', cache: 'no-store',
                    headers: { 'Accept': 'application/json' }
                });
                if (!response.ok) throw new Error('token');
                var data = await response.json();
                if (!data.csrfToken) throw new Error('token');
                updateTokens(data.csrfToken);
                if (button) button.disabled = false;
                ready = true;
                form.requestSubmit(button || undefined);
            } catch (err) {
                error.textContent = 'Zahtjev trenutno nije poslan. Provjerite vezu i pokušajte ponovo.';
                error.hidden = false;
                if (button) button.disabled = false;
            } finally {
                ready = false;
                pending = false;
            }
        });
    });
})();
