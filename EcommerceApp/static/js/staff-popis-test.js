(function () {
    'use strict';
    var root = document.getElementById('ptApp');
    if (!root) return;
    var url = root.getAttribute('data-url') || '';
    var csrf = (document.querySelector('#ptApp [name=csrfmiddlewaretoken]') || {}).value || '';
    var query = document.getElementById('ptQuery');
    var toast = document.getElementById('ptToast');
    var currentWrap = document.getElementById('ptCurrent');
    var listEl = document.getElementById('ptList');
    var colsEl = document.getElementById('ptCols');
    var countEl = document.getElementById('ptCount');
    var showAllBtn = document.getElementById('ptShowAll');
    var showAll = false;
    var listPreview = 3;
    var currentKey = '';

    function csrfToken() {
        if (csrf) return csrf;
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function showToast(msg, ok) {
        if (!toast) return;
        if (!msg) {
            toast.hidden = true;
            toast.textContent = '';
            return;
        }
        toast.hidden = false;
        toast.textContent = msg;
        toast.classList.toggle('is-ok', !!ok);
    }

    function photoHtml(src) {
        if (!src) return '';
        return '<img src="' + src + '" alt="">';
    }

    function signed(n) {
        n = parseInt(n, 10) || 0;
        return (n > 0 ? '+' : '') + n;
    }

    function diffClass(n) {
        n = parseInt(n, 10) || 0;
        if (n < 0) return ' is-low';
        if (n > 0) return ' is-ok';
        return '';
    }

    function qtyClass(popisano, sistem) {
        popisano = parseInt(popisano, 10) || 0;
        sistem = parseInt(sistem, 10) || 0;
        if (popisano === sistem) return ' is-ok';
        if (popisano < sistem) return ' is-low';
        return '';
    }

    function renderCurrent(item) {
        if (!currentWrap) return;
        if (!item) {
            currentWrap.hidden = true;
            currentKey = '';
            return;
        }
        currentWrap.hidden = false;
        currentKey = item.key || '';
        var photo = document.getElementById('ptPhoto');
        if (photo) photo.innerHTML = photoHtml(item.slika || '');
        function set(id, val) {
            var el = document.getElementById(id);
            if (el) el.textContent = val == null || val === '' ? '—' : String(val);
        }
        set('ptName', item.naziv);
        set('ptSifra', item.sifra || '—');
        set('ptBarkod', item.barkod || '—');
        set('ptBrend', item.brend || '—');
        set('ptKat', item.kategorija || '—');
        var sistem = parseInt(item.sistem, 10) || 0;
        var popisano = parseInt(item.popisano, 10) || 0;
        var razlika = parseInt(item.razlika, 10);
        if (isNaN(razlika)) razlika = popisano - sistem;
        set('ptSistem', sistem);
        set('ptPopisano', popisano);
        set('ptSumSistem', sistem + ' kom');
        set('ptSumPopisano', popisano + ' kom');
        set('ptSumDiff', signed(razlika) + ' kom');
        var stock = document.getElementById('ptStock');
        var stockLabel = document.getElementById('ptStockLabel');
        if (stock) stock.classList.toggle('is-out', !item.na_stanju);
        if (stockLabel) stockLabel.textContent = item.na_stanju ? 'Na stanju' : 'Nije na stanju';
        var diffBox = document.getElementById('ptSumDiffBox');
        if (diffBox) {
            diffBox.classList.toggle('is-diff', razlika !== 0);
            diffBox.classList.toggle('is-zero', razlika === 0);
        }
    }

    function renderList(items, current) {
        items = items || [];
        if (countEl) countEl.textContent = String(items.length);
        if (colsEl) colsEl.hidden = !items.length;
        if (showAllBtn) showAllBtn.hidden = items.length <= listPreview;
        if (!listEl) return;
        if (!items.length) {
            listEl.innerHTML = '<p class="pt-empty" id="ptEmpty">Još nema skeniranih artikala.</p>';
            return;
        }
        var curKey = (current && current.key) || '';
        listEl.innerHTML = items.map(function (row, index) {
            var hidden = !showAll && index >= listPreview ? ' is-hidden' : '';
            var on = row.key === curKey ? ' is-on' : '';
            var thumb = row.slika
                ? '<img src="' + row.slika + '" alt="">'
                : '';
            return (
                '<button type="button" class="pt-row' + on + hidden + '" data-key="' + row.key + '">' +
                '<span class="pt-row-info"><span class="pt-thumb">' + thumb + '</span><span>' +
                '<strong></strong><em></em></span></span>' +
                '<span class="pt-row-num"></span>' +
                '<span class="pt-row-num' + qtyClass(row.popisano, row.sistem) + '"></span>' +
                '<span class="pt-row-num' + diffClass(row.razlika) + '"></span>' +
                '<span class="pt-chev" aria-hidden="true">›</span></button>'
            );
        }).join('');
        Array.prototype.forEach.call(listEl.querySelectorAll('.pt-row'), function (btn, index) {
            var row = items[index];
            if (!row) return;
            var name = btn.querySelector('.pt-row-info strong');
            var bar = btn.querySelector('.pt-row-info em');
            if (name) name.textContent = row.naziv || '';
            if (bar) bar.textContent = 'Barkod: ' + (row.barkod || '—');
            var nums = btn.querySelectorAll('.pt-row-num');
            if (nums[0]) nums[0].textContent = String(row.sistem || 0);
            if (nums[1]) nums[1].textContent = String(row.popisano || 0);
            if (nums[2]) nums[2].textContent = signed(row.razlika || 0);
        });
    }

    function applyPayload(data) {
        if (!data || !data.ok) return;
        if (data.cleared || (root.getAttribute('data-has-location') === '1' && !data.location)) {
            window.location.reload();
            return;
        }
        renderCurrent(data.current);
        renderList(data.items || [], data.current);
        if (data.message) showToast(data.message, true);
    }

    function post(action, extra) {
        var body = new URLSearchParams();
        body.set('action', action);
        extra = extra || {};
        Object.keys(extra).forEach(function (k) {
            if (extra[k] != null && extra[k] !== '') body.set(k, String(extra[k]));
        });
        return fetch(url, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken(),
                'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8',
            },
            credentials: 'same-origin',
            body: body.toString(),
        }).then(function (res) {
            return res.json().then(function (data) {
                return { ok: res.ok, data: data };
            }, function () {
                return { ok: false, data: { error: 'Zahtjev nije uspio.' } };
            });
        }).then(function (result) {
            if (!result.data || result.data.ok === false || !result.ok) {
                showToast((result.data && result.data.error) || 'Greška na popisu.', false);
                return null;
            }
            showToast('');
            applyPayload(result.data);
            return result.data;
        }).catch(function () {
            showToast('Greška na popisu.', false);
            return null;
        });
    }

    function scan() {
        var q = (query && query.value || '').trim();
        if (!q) {
            showToast('Unesi ili skeniraj barkod.', false);
            return;
        }
        post('scan', { q: q }).then(function (data) {
            if (data && query) query.value = '';
        });
    }

    if (query) {
        query.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            scan();
        });
        query.addEventListener('mg-scanned', function (event) {
            var code = (event.detail && event.detail.code) || query.value;
            if (code) {
                query.value = code;
                scan();
            }
        });
    }
    var scanBtn = document.getElementById('ptScanBtn');
    if (scanBtn) {
        scanBtn.addEventListener('click', function (event) {
            if ((query && query.value || '').trim()) {
                event.preventDefault();
                event.stopImmediatePropagation();
                scan();
            }
        }, true);
    }
    var minus = document.getElementById('ptMinus');
    var plus = document.getElementById('ptPlus');
    var quick = document.getElementById('ptQuick');
    if (minus) minus.addEventListener('click', function () { post('minus', { key: currentKey }); });
    if (plus) plus.addEventListener('click', function () { post('plus', { key: currentKey }); });
    if (quick) quick.addEventListener('click', function () { post('brzi', { key: currentKey }); });
    if (listEl) {
        listEl.addEventListener('click', function (event) {
            var row = event.target.closest('[data-key]');
            if (!row) return;
            post('select', { key: row.getAttribute('data-key') });
        });
    }
    if (showAllBtn) {
        showAllBtn.addEventListener('click', function () {
            showAll = !showAll;
            showAllBtn.textContent = showAll ? 'Sakrij' : 'Prikaži sve';
            Array.prototype.forEach.call(listEl.querySelectorAll('.pt-row'), function (row, index) {
                row.classList.toggle('is-hidden', !showAll && index >= listPreview);
            });
        });
    }
    var finishBtn = document.getElementById('ptFinish');
    if (finishBtn) {
        finishBtn.addEventListener('click', function () {
            if (!window.confirm('Završiti popis? Popisane količine će se upisati na ovu lokaciju.')) return;
            post('zavrsi');
        });
    }
})();
