(function () {
    'use strict';
    var config = document.getElementById('permanentInputConfig');
    if (!config || document.getElementById('mgOrderForm')) return;
    var status = document.getElementById('permanentInputStatus');
    var prefix = 'permanent-form-event.' + config.dataset.user + '.';
    var queue = [];
    var sending = false;
    var flushTimer = null;
    var localSaved = true;
    var latest = new Map();
    var secret = /password|passwd|lozinka|csrf|token|secret|api.?key|card.?number|cvv|cvc/i;
    try {
        Object.keys(localStorage).filter(function (key) { return key.indexOf(prefix) === 0; }).forEach(function (key) {
            queue.push(JSON.parse(localStorage.getItem(key)));
        });
    } catch (error) {}
    function persist(event) {
        try { (event ? [event] : queue).forEach(function (event) { localStorage.setItem(prefix + event.event_id, JSON.stringify(event)); }); localSaved = true; return true; }
        catch (error) { localSaved = false; status.textContent = 'Lokalno čuvanje nije dostupno. Sačekaj potvrdu baze prije izlaska.'; return false; }
    }
    function capture(target) {
        if (!target.matches('input,textarea,select') || target.closest('#permanentInputConfig')) return;
        if (target.type === 'password' || target.type === 'file' || secret.test(target.name + ' ' + target.id)) return;
        var form = target.form || target.closest('[role="dialog"],.mg-modal') || target.parentElement;
        if (form.id === 'mgOrderForm') return;
        var fields = Array.from(form.querySelectorAll('input,textarea,select')).filter(function (el) {
            return !['password', 'file', 'submit', 'button'].includes(el.type) &&
                !secret.test(el.name + ' ' + el.id) && (el.name || el.id);
        }).map(function (el) {
            return {name: el.name || el.id, id: el.id, type: el.type, value: el.value,
                checked: el.checked, selected: el.multiple ? Array.from(el.selectedOptions).map(function (option) { return option.value; }) : undefined};
        });
        var serialized = JSON.stringify(fields);
        if (serialized === latest.get(form)) return;
        latest.set(form, serialized);
        queue.push({owner: config.dataset.user, event_id: crypto.randomUUID(), path: location.pathname + location.search,
            form_key: form.id || form.getAttribute('action') || 'unos', payload: fields});
        persist(queue[queue.length - 1]);
        status.textContent = 'Čuvanje unosa u bazu…';
        if (flushTimer === null) flushTimer = setTimeout(flush, 400);
    }
    async function flush() {
        clearTimeout(flushTimer);
        flushTimer = null;
        if (sending || !queue.length) return;
        sending = true;
        try {
            var batch = [];
            var bytes = 0;
            for (var event of queue.slice(0, 50)) {
                var size = JSON.stringify(event).length;
                if (batch.length && bytes + size > 100000) break;
                batch.push(event); bytes += size;
            }
            var body = JSON.stringify({events: batch});
            var response = await fetch(config.dataset.url, {
                method: 'POST', credentials: 'same-origin', keepalive: body.length < 18000,
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': config.querySelector('[name=csrfmiddlewaretoken]').value},
                body: body
            });
            if (!response.ok || !(await response.json()).ok) throw new Error('save');
            queue.splice(0, batch.length);
            batch.forEach(function (saved) {
                try { localStorage.removeItem(prefix + saved.event_id); } catch (error) {}
            });
            status.textContent = queue.length ? 'Čuvanje unosa u bazu…' : 'Unos je sačuvan u bazi.';
        } catch (error) {
            status.textContent = localSaved ? 'Unos čeka vezu s bazom. Ne briši podatke preglednika dok čuvanje ne bude potvrđeno.' : 'Unos nije sačuvan ni lokalno ni u bazi. Ne zatvaraj ovu stranicu.';
        } finally { sending = false; }
        if (queue.length) setTimeout(flush, 1000);
    }
    document.addEventListener('input', function (event) { capture(event.target); });
    document.addEventListener('change', function (event) { capture(event.target); });
    document.addEventListener('submit', function (event) {
        var first = event.target.querySelector('input:not([type=hidden]),textarea,select');
        if (first) capture(first);
    }, true);
    window.addEventListener('online', flush);
    window.addEventListener('pagehide', function () { persist(); flush(); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) flush(); });
    setInterval(flush, 3000);
    flush();
})();
