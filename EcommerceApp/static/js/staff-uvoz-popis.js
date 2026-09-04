(function () {
    var root = document.getElementById('upApp');
    if (!root) return;
    var payloadEl = document.getElementById('upPayload');
    var state;
    try {
        state = JSON.parse(payloadEl ? payloadEl.textContent : '{}');
    } catch (err) {
        state = {};
    }
    var csrf = (root.querySelector('[name=csrfmiddlewaretoken]') || {}).value || '';
    var url = root.getAttribute('data-url');
    var printUrl = root.getAttribute('data-print');
    var filter = 'svi';
    var query = '';
    var errorEl = document.getElementById('upError');
    var currentEl = document.getElementById('upCurrent');
    var rowsEl = document.getElementById('upRows');
    var statsEl = document.getElementById('upStats');
    var scan = document.getElementById('upScan');
    var finished = !!state.zavrsen;

    function qty(value) {
        if (value === null || value === undefined || value === '') return '—';
        return String(value);
    }
    function deltaClass(value) {
        if (value === null || value === undefined) return '';
        if (value > 0) return 'is-up';
        if (value < 0) return 'is-down';
        return 'is-zero';
    }
    function deltaText(value) {
        if (value === null || value === undefined) return '—';
        if (value > 0) return '+' + value;
        return String(value);
    }
    function statusLabel(item) {
        return item.status_label || item.status || '';
    }
    function showError(msg) {
        if (!errorEl) return;
        if (!msg) {
            errorEl.hidden = true;
            errorEl.textContent = '';
            return;
        }
        errorEl.hidden = false;
        errorEl.textContent = msg;
    }
    function post(action, extra) {
        var body = new URLSearchParams();
        body.set('action', action);
        body.set('csrfmiddlewaretoken', csrf);
        Object.keys(extra || {}).forEach(function (key) {
            if (extra[key] !== undefined && extra[key] !== null) body.set(key, extra[key]);
        });
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrf,
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            },
            body: body.toString(),
            credentials: 'same-origin',
        }).then(function (res) {
            return res.text().then(function (text) {
                var data;
                try { data = JSON.parse(text); } catch (err) { data = null; }
                if (!data) throw new Error('Zahtjev nije uspio.');
                if (!res.ok || data.ok === false) throw new Error(data.error || 'Zahtjev nije uspio.');
                return data;
            });
        });
    }
    function apply(data) {
        if (!data || !data.ok) return;
        state = data;
        finished = !!data.zavrsen;
        if (data.redirect) {
            window.location.href = data.redirect;
            return;
        }
        render();
        if (scan && !finished) scan.focus();
    }
    function currentItem() {
        var id = state.current_id;
        return (state.items || []).find(function (row) { return row.id === id; }) || state.current || null;
    }
    function renderCurrent() {
        var item = currentItem();
        if (!item) {
            currentEl.hidden = true;
            currentEl.innerHTML = '';
            return;
        }
        currentEl.hidden = false;
        var disabled = finished ? ' disabled' : '';
        currentEl.innerHTML =
            '<div class="up-card">' +
                '<div class="up-card-img">' +
                    (item.slika ? '<img src="' + item.slika + '" alt="">' : '<span></span>') +
                '</div>' +
                '<div class="up-card-info">' +
                    (item.sifra ? '<div class="up-sifra">Šifra: ' + item.sifra + '</div>' : '') +
                    '<strong>' + item.naziv + '</strong>' +
                    (item.barkod ? '<div class="up-barkod">Barkod: ' + item.barkod + '</div>' : '') +
                    (item.status !== 'nije' ? '<span class="up-pill is-' + item.status + '">' + statusLabel(item) + '</span>' : '') +
                '</div>' +
                '<div class="up-card-qty">' +
                    '<div class="up-box"><span>U UVOZU</span><b>' + qty(item.ocekivano) + '</b><small>kom</small></div>' +
                    '<span class="up-arrow">→</span>' +
                    '<div class="up-box is-count">' +
                        '<span>POPISANO</span>' +
                        '<div class="up-step">' +
                            '<button type="button" data-act="minus"' + disabled + '>−</button>' +
                            '<input id="upQty" class="up-qty-input" type="text" inputmode="numeric" pattern="[0-9]*" enterkeyhint="done" autocomplete="off" autocorrect="off" spellcheck="false" value="' + (item.popisano == null ? '' : item.popisano) + '" placeholder="0"' + disabled + '>' +
                            '<button type="button" data-act="plus"' + disabled + '>+</button>' +
                        '</div>' +
                        '<small>kom</small>' +
                    '</div>' +
                    '<div class="up-box is-diff ' + deltaClass(item.razlika) + '"><span>RAZLIKA</span><b>' + deltaText(item.razlika) + '</b><small>kom</small></div>' +
                '</div>' +
            '</div>' +
            '<div class="up-card-actions">' +
                '<button type="button" class="up-quick" data-act="brzi" data-delta="1"' + disabled + '>+1</button>' +
                '<button type="button" class="up-quick" data-act="brzi" data-delta="5"' + disabled + '>+5</button>' +
                '<button type="button" class="up-quick" data-act="brzi" data-delta="10"' + disabled + '>+10</button>' +
                '<button type="button" class="mg-btn mg-btn-primary" data-act="confirm"' + disabled + '>Potvrdi količinu</button>' +
            '</div>';
    }
    function renderStats() {
        var s = state.stats || {};
        statsEl.innerHTML =
            '<div class="up-stat is-pop"><span>POPISANO</span><b>' + (s.popisano_artikala || 0) + '</b><small>od ' + (s.artikala || 0) + ' artikala</small></div>' +
            '<div class="up-stat is-ok"><span>TAČNO</span><b>' + (s.tacno || 0) + '</b><small>artikla</small></div>' +
            '<div class="up-stat is-up"><span>VIŠAK</span><b>' + (s.visak || 0) + '</b><small>artikla</small></div>' +
            '<div class="up-stat is-down"><span>MANJAK</span><b>' + (s.manjak || 0) + '</b><small>artikla</small></div>' +
            '<div class="up-stat is-wait"><span>NIJE POPISANO</span><b>' + (s.nije || 0) + '</b><small>artikla</small></div>';
        var done = document.getElementById('upProgDone');
        var all = document.getElementById('upProgAll');
        var bar = document.getElementById('upProgBar');
        var pct = document.getElementById('upProgPct');
        if (done) done.textContent = s.popisano_artikala || 0;
        if (all) all.textContent = s.artikala || 0;
        if (bar) bar.style.width = (s.pct || 0) + '%';
        if (pct) pct.textContent = (s.pct || 0) + '% završeno';
        var map = { Svi: 'artikala', Tacno: 'tacno', Visak: 'visak', Manjak: 'manjak', Nije: 'nije' };
        Object.keys(map).forEach(function (name) {
            var el = document.getElementById('upTab' + name);
            if (el) el.textContent = s[map[name]] || 0;
        });
        var art = document.getElementById('upArtikala');
        var kom = document.getElementById('upKomada');
        if (art) art.textContent = s.artikala || 0;
        if (kom) kom.textContent = s.ocekivano_kom || 0;
    }
    function visibleItems() {
        var q = (query || '').trim().toLowerCase();
        return (state.items || []).filter(function (item) {
            if (filter !== 'svi' && item.status !== filter) return false;
            if (!q) return true;
            return [item.naziv, item.sifra, item.barkod].join(' ').toLowerCase().indexOf(q) !== -1;
        });
    }
    function renderRows() {
        var items = visibleItems();
        if (!items.length) {
            rowsEl.innerHTML = '<tr><td colspan="7" class="up-empty">Nema artikala za ovaj filter.</td></tr>';
            return;
        }
        rowsEl.innerHTML = items.map(function (item) {
            var active = item.id === state.current_id ? ' is-active' : '';
            return '<tr class="is-clickable' + active + '" data-id="' + item.id + '">' +
                '<td>' + (item.sifra || '—') + '</td>' +
                '<td><strong>' + item.naziv + '</strong></td>' +
                '<td>' + (item.barkod || '—') + '</td>' +
                '<td class="num">' + qty(item.ocekivano) + '</td>' +
                '<td class="num">' + qty(item.popisano) + '</td>' +
                '<td class="num ' + deltaClass(item.razlika) + '">' + deltaText(item.razlika) + '</td>' +
                '<td><span class="up-pill is-' + item.status + '">' + statusLabel(item) + '</span></td>' +
            '</tr>';
        }).join('');
    }
    function render() {
        showError('');
        renderCurrent();
        renderStats();
        renderRows();
    }
    function run(action, extra) {
        showError('');
        return post(action, extra).then(apply).catch(function (err) {
            showError(err.message || 'Zahtjev nije uspio.');
        });
    }

    document.getElementById('upScanForm').addEventListener('submit', function (event) {
        event.preventDefault();
        if (finished) return;
        var value = (scan.value || '').trim();
        if (!value) return;
        run('scan', { q: value }).then(function () {
            scan.value = '';
            if (scan && !finished) scan.focus();
        });
    });
    currentEl.addEventListener('click', function (event) {
        var qtyBox = event.target.closest('.up-box.is-count');
        if (qtyBox && !event.target.closest('button') && !finished) {
            var qtyField = document.getElementById('upQty');
            if (qtyField && event.target !== qtyField) {
                qtyField.focus();
                qtyField.select();
            }
        }
        var btn = event.target.closest('[data-act]');
        if (!btn || finished) return;
        var item = currentItem();
        if (!item) return;
        var act = btn.getAttribute('data-act');
        if (act === 'confirm') {
            var input = document.getElementById('upQty');
            run('confirm', { stavka_id: item.id, kolicina: (input && input.value !== '') ? input.value : '0' });
            return;
        }
        var extra = { stavka_id: item.id };
        if (act === 'brzi') extra.delta = btn.getAttribute('data-delta') || '1';
        if (act === 'set_qty') extra.kolicina = (document.getElementById('upQty') || {}).value;
        run(act, extra);
    });
    currentEl.addEventListener('focusin', function (event) {
        if (event.target.id !== 'upQty' || finished) return;
        event.target.select();
    });
    currentEl.addEventListener('keydown', function (event) {
        if (event.target.id !== 'upQty' || finished) return;
        if (event.key !== 'Enter') return;
        event.preventDefault();
        var item = currentItem();
        if (!item) return;
        run('confirm', { stavka_id: item.id, kolicina: event.target.value || '0' });
    });
    currentEl.addEventListener('change', function (event) {
        if (event.target.id !== 'upQty' || finished) return;
        var item = currentItem();
        if (!item) return;
        run('set_qty', { stavka_id: item.id, kolicina: event.target.value || '0' });
    });
    rowsEl.addEventListener('click', function (event) {
        var row = event.target.closest('tr[data-id]');
        if (!row) return;
        run('select', { stavka_id: row.getAttribute('data-id') });
    });
    document.getElementById('upTabs').addEventListener('click', function (event) {
        var btn = event.target.closest('button[data-filter]');
        if (!btn) return;
        filter = btn.getAttribute('data-filter');
        Array.prototype.forEach.call(document.getElementById('upTabs').querySelectorAll('button'), function (el) {
            el.classList.toggle('is-active', el === btn);
        });
        renderRows();
    });
    document.getElementById('upFilterQ').addEventListener('input', function () {
        query = this.value || '';
        renderRows();
    });
    var finish = document.getElementById('upFinish');
    if (finish) {
        finish.addEventListener('click', function () {
            var s = state.stats || {};
            var msg = 'Završiti popis? Nepopisani artikli idu kao 0. Popisane količine idu na lokaciju Uvoz.';
            if (s.nije) msg = 'Nije popisano ' + s.nije + ' artikala — treirat će se kao 0. ' + msg;
            if (!window.confirm(msg)) return;
            run('zavrsi');
        });
    }
    render();
    if (scan && !finished) scan.focus();
})();
