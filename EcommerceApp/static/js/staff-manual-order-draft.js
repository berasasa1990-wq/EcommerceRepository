(function () {
    'use strict';
    var form = document.getElementById('mgOrderForm');
    var token = document.getElementById('mgDraftToken');
    if (!form || !token) return;
    var owner = document.currentScript.dataset.user;
    var version = document.getElementById('mgDraftVersion');
    var status = document.getElementById('mgDraftStatus');
    var ui = document.getElementById('mgDraftUI');
    var tab = crypto.randomUUID();
    try {
        tab = sessionStorage.getItem('warehouse.draft.tab') || tab;
        sessionStorage.setItem('warehouse.draft.tab', tab);
    } catch (error) {}
    var key = 'warehouse.manual-order.database.' + owner + '.' + token.value + '.' + tab;
    var server = JSON.parse(document.getElementById('mgDraftPayload').textContent || '{}');
    var queue = [];
    var sending = false;
    var blocked = false;
    var submitting = false;
    var last = '';
    var localSaved = true;
    var recoveredKey = '';
    var recoveredRaw = '';
    try {
        var raw = localStorage.getItem(key);
        if (!raw) {
            var prefix = 'warehouse.manual-order.database.' + owner + '.' + token.value + '.';
            recoveredKey = Object.keys(localStorage).find(function (candidate) {
                if (candidate.indexOf(prefix) !== 0) return false;
                var saved = JSON.parse(localStorage.getItem(candidate) || '{}');
                return saved.entries && saved.entries.length;
            }) || '';
            if (recoveredKey) raw = recoveredRaw = localStorage.getItem(recoveredKey);
        }
        var saved = JSON.parse(raw || '{}');
        queue = Array.isArray(saved) ? saved : (saved.entries || []);
        if (queue.length && saved.version !== undefined) version.value = saved.version;
    } catch (error) {}
    var restored = queue.length ? queue[queue.length - 1].payload : server;
    if (restored.fields) {
        var grouped = {};
        restored.fields.forEach(function (pair) { (grouped[pair[0]] || (grouped[pair[0]] = [])).push(pair[1]); });
        // Server renders product rows. Offline row changes are uploaded before reloading.
        Object.keys(grouped).forEach(function (name) {
            var fields = Array.from(form.elements).filter(function (el) { return el.name === name; });
            fields.forEach(function (el, index) {
                if (el.type === 'radio' || el.type === 'checkbox') el.checked = grouped[name].indexOf(el.value) !== -1;
                else if (grouped[name][index] !== undefined) el.value = grouped[name][index];
            });
        });
    }
    Object.keys(restored.inputs || {}).forEach(function (id) {
        var el = document.getElementById(id);
        if (!el || el.name || !el.matches('input,textarea,select')) return;
        el.value = restored.inputs[id].value;
        if (el.type === 'checkbox') el.checked = restored.inputs[id].checked;
    });
    if (typeof MutationObserver !== 'undefined') {
        ['mgCustomerModal', 'mgSpareModal', 'mgOrderBulkModal'].forEach(function (id) {
            var modal = document.getElementById(id);
            if (!modal) return;
            var observer = new MutationObserver(function () {
                if (modal.hidden) return;
                modal.querySelectorAll('input[id],textarea[id]').forEach(function (el) {
                    var saved = (restored.inputs || {})[el.id];
                    if (!saved) return;
                    el.value = saved.value;
                    if (el.type === 'checkbox') el.checked = saved.checked;
                });
                observer.disconnect();
            });
            observer.observe(modal, {attributes: true, attributeFilter: ['hidden']});
        });
    }
    var recovering = queue.length > 0;
    if (recovering) { form.inert = true; status.textContent = 'Vraćanje prethodnog unosa u bazu…'; }
    function persist() {
        try { localStorage.setItem(key, JSON.stringify({version: Number(version.value), entries: queue})); localSaved = true; return true; }
        catch (error) { localSaved = false; status.textContent = 'Lokalna kopija nije dostupna. Ne zatvaraj stranicu dok unos ne bude sačuvan u bazi.'; return false; }
    }
    function snapshot() {
        var inputs = {};
        document.querySelectorAll('#mgOrderForm input[id], #mgOrderForm textarea[id], .mg-modal input[id], .mg-modal textarea[id]').forEach(function (el) {
            if (!el.name && el.type !== 'password') inputs[el.id] = {value: el.value, checked: el.checked};
        });
        var payload = {
            fields: Array.from(new FormData(form).entries()).filter(function (pair) {
                return ['csrfmiddlewaretoken', 'action', 'order_broj', 'draft_token', 'draft_version', 'draft_ui'].indexOf(pair[0]) === -1;
            }),
            inputs: inputs,
            lines: Array.from(form.querySelectorAll('tr[data-line]')).map(function (row) { return row.innerText; })
        };
        ui.value = JSON.stringify({inputs: inputs, lines: payload.lines});
        return payload;
    }
    function record() {
        if (submitting || recovering) return;
        var payload = snapshot();
        var serialized = JSON.stringify(payload);
        if (serialized === last) return;
        last = serialized;
        queue.push({payload: payload, event: 'save'});
        persist();
        status.textContent = 'Čuvanje u bazu…';
        flush();
    }
    async function flush() {
        if (sending || !queue.length) return;
        sending = true;
        var entry = queue[0];
        try {
            var body = JSON.stringify({token: token.value, version: Number(version.value), payload: entry.payload, event: entry.event});
            var response = await fetch(token.dataset.saveUrl, {
                method: 'POST', credentials: 'same-origin', keepalive: body.length < 18000,
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value},
                body: body
            });
            var result = await response.json();
            if (response.status === 409) {
                blocked = true;
                status.textContent = result.error + ' Otvori Historiju unosa prije nastavka.';
            } else {
                if (!response.ok || !result.ok) throw new Error('save');
                version.value = result.version;
                status.textContent = blocked ? 'Tvoje verzije su u historiji; postoji noviji unos u drugom prozoru.' :
                    (queue.length > 1 ? 'Čuvanje u bazu…' : 'Sve promjene su sačuvane u bazi.');
            }
            queue.shift();
            persist();
            if (!queue.length && recoveredKey) {
                // Remove an old browser queue only after acknowledgement, and only
                // if another tab has not added any more changes to that queue.
                try {
                    if (localStorage.getItem(recoveredKey) === recoveredRaw) localStorage.removeItem(recoveredKey);
                } catch (error) {}
                recoveredKey = '';
            }
            if (entry.event === 'cancel' && !blocked) {
                window.location.assign(form.action);
                return;
            }
            if (recovering && !queue.length && !blocked) { window.location.reload(); return; }
        } catch (error) {
            status.textContent = localSaved ? 'Unos još nije potvrđen u bazi. Kopija čeka ponovno povezivanje; ne briši podatke preglednika.' : 'Unos nije sačuvan ni lokalno ni u bazi. Ne zatvaraj ovu stranicu.';
        } finally {
            sending = false;
        }
        if (queue.length) setTimeout(flush, 1000);
    }
    document.addEventListener('input', record);
    document.addEventListener('change', record);
    document.addEventListener('click', function () { setTimeout(record, 0); });
    setInterval(function () { record(); flush(); }, 1000);
    window.addEventListener('online', flush);
    window.addEventListener('pagehide', function () { record(); persist(); });
    document.addEventListener('visibilitychange', function () { if (document.hidden) { record(); flush(); } });
    // Let customer/stock/payment validation run first, then drain writes before submitting.
    setTimeout(function () { form.addEventListener('submit', function (event) {
        if (event.defaultPrevented) return;
        var submitter = event.submitter;
        setTimeout(async function () {
            record();
            while ((queue.length || sending) && !blocked) {
                if (sending) {
                    await new Promise(function (resolve) { setTimeout(resolve, 50); });
                    continue;
                }
                var count = queue.length;
                await flush();
                if (queue.length >= count && !sending) break;
            }
            if (queue.length || blocked) {
                status.textContent = 'Prvo sačekaj potvrdu čuvanja u bazi. Unos ostaje sačuvan za ponovni pokušaj.';
                return;
            }
            submitting = true;
            if (submitter && submitter.name) {
                var action = document.createElement('input');
                action.type = 'hidden'; action.name = submitter.name; action.value = submitter.value;
                form.appendChild(action);
            }
            snapshot();
            HTMLFormElement.prototype.submit.call(form);
        }, 0);
        event.preventDefault();
    }); }, 0);
    if (!form.elements.order_broj) {
        document.getElementById('mgOrderClear').addEventListener('click', function (event) {
            event.preventDefault(); event.stopImmediatePropagation();
            if (!window.confirm('Otkazati ovu narudžbu? Unosi će ostati u historiji.')) return;
            record();
            queue.push({payload: snapshot(), event: 'cancel'});
            submitting = true;
            persist(); flush();
        });
    }
    var legacyKey = 'warehouse.manual-order.v1.' + owner;
    try {
        var legacy = JSON.parse(localStorage.getItem(legacyKey) || 'null');
        if (legacy) {
            fetch(token.dataset.importUrl, {
                method: 'POST', credentials: 'same-origin',
                headers: {'Content-Type': 'application/json', 'X-CSRFToken': form.elements.csrfmiddlewaretoken.value},
                body: JSON.stringify(legacy)
            }).then(function (response) { if (!response.ok) throw new Error('import'); return response.json(); })
              .then(function (result) {
                  if (result.ok) {
                      localStorage.removeItem(legacyKey);
                      status.textContent = 'Prethodni lokalni nacrt je prenesen u bazu. Dostupan je u Historiji unosa.';
                  }
              }).catch(function () { status.textContent = 'Prethodni nacrt čeka prenos u bazu; lokalna kopija je zadržana.'; });
        }
    } catch (error) {}
    if (recovering) flush();
})();
