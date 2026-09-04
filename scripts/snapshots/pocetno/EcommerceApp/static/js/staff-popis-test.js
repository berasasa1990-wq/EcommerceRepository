(function () {
    'use strict';
    var root = document.getElementById('ptApp');
    if (!root) return;
    var url = root.getAttribute('data-url') || '';
    var lookupUrl = root.getAttribute('data-lookup') || '';
    var csrf = (document.querySelector('#ptApp [name=csrfmiddlewaretoken]') || {}).value || '';
    var query = document.getElementById('ptQuery');
    var suggest = document.getElementById('ptSuggest');
    var toast = document.getElementById('ptToast');
    var searchTimer = 0;
    var currentWrap = document.getElementById('ptCurrent');
    var listEl = document.getElementById('ptList');
    var listWrap = document.getElementById('ptListWrap');
    var colsEl = document.getElementById('ptCols');
    var countEl = document.getElementById('ptCount');
    var showAllBtn = document.getElementById('ptShowAll');
    var showAll = false;
    var listPreview = 3;
    var currentKey = '';
    var qtyTouched = false;

    function csrfToken() {
        if (csrf) return csrf;
        var m = document.cookie.match(/csrftoken=([^;]+)/);
        return m ? decodeURIComponent(m[1]) : '';
    }

    function showToast(msg, ok, isDup) {
        if (!toast) return;
        if (!msg) {
            toast.hidden = true;
            toast.textContent = '';
            toast.classList.remove('is-ok', 'is-dup');
            return;
        }
        toast.hidden = false;
        toast.textContent = msg;
        toast.classList.toggle('is-ok', !!ok && !isDup);
        toast.classList.toggle('is-dup', !!isDup);
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
            markRepeat(false);
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
        var qtyInpRender = document.getElementById('ptQtyInput');
        if (qtyInpRender) {
            if (!(document.activeElement === qtyInpRender && qtyTouched)) {
                qtyInpRender.value = String(popisano);
            }
            qtyInpRender.setAttribute('data-key', currentKey);
        }
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

    function alarmDuplicate() {
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (Ctx) {
                var ctx = new Ctx();
                function tone(freq, start, dur) {
                    var osc = ctx.createOscillator();
                    var gain = ctx.createGain();
                    osc.type = 'square';
                    osc.frequency.value = freq;
                    gain.gain.setValueAtTime(0.0001, ctx.currentTime + start);
                    gain.gain.exponentialRampToValueAtTime(0.18, ctx.currentTime + start + 0.015);
                    gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + start + dur);
                    osc.connect(gain);
                    gain.connect(ctx.destination);
                    osc.start(ctx.currentTime + start);
                    osc.stop(ctx.currentTime + start + dur + 0.02);
                }
                tone(1400, 0, 0.14);
                tone(520, 0.15, 0.16);
                tone(1400, 0.33, 0.14);
                tone(520, 0.48, 0.16);
                tone(1600, 0.68, 0.14);
                tone(380, 0.84, 0.28);
                window.setTimeout(function () { try { ctx.close(); } catch (e) {} }, 1300);
            }
        } catch (err) {}
        if (navigator.vibrate) {
            try { navigator.vibrate([220, 60, 220, 60, 220, 60, 400]); } catch (err2) {}
        }
    }

    function markRepeat(on) {
        var panel = document.getElementById('ptQtyPanel');
        if (!panel) return;
        panel.classList.remove('is-repeat');
        void panel.offsetWidth;
        panel.classList.toggle('is-repeat', !!on);
    }

    function renderList(items, current, isDup) {
        items = items || [];
        if (listWrap) listWrap.hidden = !items.length;
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
                '<strong></strong><em></em><b class="pt-row-stock"></b></span></span>' +
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
            var stock = btn.querySelector('.pt-row-stock');
            if (name) name.textContent = row.naziv || '';
            if (bar) bar.textContent = 'Barkod: ' + (row.barkod || '—');
            if (stock) stock.textContent = 'Ukupno stanje: ' + (parseInt(row.sistem, 10) || 0) + ' kom';
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
        markRepeat(!!data.already_on_list);
        renderList(data.items || [], data.current);
        if (data.already_on_list) {
            alarmDuplicate();
            return;
        }
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

    function hideSuggest() {
        if (suggest) suggest.hidden = true;
    }

    function flattenLookup(results) {
        var rows = [];
        (results || []).forEach(function (prod) {
            var vars = prod.varijacije || [];
            if (vars.length > 1) {
                vars.forEach(function (v) {
                    rows.push({
                        id: prod.id,
                        variation_id: v.id,
                        naziv: (prod.naziv || '') + ' ' + (v.naziv || ''),
                        sifra: v.sifra || prod.sifra || '',
                        barkod: prod.barkod || '',
                    });
                });
            } else {
                rows.push({
                    id: prod.id,
                    variation_id: vars.length === 1 ? vars[0].id : '',
                    naziv: prod.naziv,
                    sifra: (vars.length === 1 && vars[0].sifra) ? vars[0].sifra : (prod.sifra || ''),
                    barkod: prod.barkod || '',
                });
            }
        });
        return rows;
    }

    function isExactMatch(item, value) {
        var q = String(value || '').replace(/\s+/g, '').toLowerCase();
        if (!q) return false;
        return String(item.sifra || '').replace(/\s+/g, '').toLowerCase() === q
            || String(item.barkod || '').replace(/\s+/g, '').toLowerCase() === q;
    }

    function showSuggest(items) {
        if (!suggest) return;
        suggest.innerHTML = '';
        if (!items.length) {
            var empty = document.createElement('li');
            empty.className = 'is-empty';
            empty.textContent = 'Nema rezultata.';
            suggest.appendChild(empty);
            suggest.hidden = false;
            return;
        }
        items.forEach(function (item) {
            var li = document.createElement('li');
            li.innerHTML = '<strong></strong><span></span>';
            li.querySelector('strong').textContent = item.naziv || '';
            var meta = [];
            if (item.sifra) meta.push(item.sifra);
            if (item.barkod) meta.push(item.barkod);
            li.querySelector('span').textContent = meta.join(' · ');
            li.addEventListener('click', function () {
                hideSuggest();
                pickItem(item);
            });
            suggest.appendChild(li);
        });
        suggest.hidden = false;
    }

    function afterScan(data) {
        if (!data) {
            if (!currentKey && currentWrap) currentWrap.hidden = true;
            return;
        }
        if (query) query.value = '';
        hideSuggest();
        var qtyPanel = document.getElementById('ptQtyPanel');
        if (qtyPanel && typeof qtyPanel.scrollIntoView === 'function') {
            qtyPanel.scrollIntoView({ block: 'nearest', behavior: 'instant' });
        }
        if (!qtyTouched) focusQty();
    }

    function pickItem(item) {
        if (!item || !item.id) return;
        focusQty();
        var extra = { product_id: item.id };
        if (item.variation_id) extra.variation_id = item.variation_id;
        post('scan', extra).then(afterScan);
    }

    function searchArticles(value, commit) {
        var q = String(value || '').trim();
        if (!q) {
            hideSuggest();
            if (commit) showToast('Unesi naziv, šifru ili barkod.', false);
            return;
        }
        if (!lookupUrl) {
            if (commit) {
                focusQty();
                post('scan', { q: q }).then(afterScan);
            }
            return;
        }
        fetch(lookupUrl + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1&limit=20', {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        }).then(function (res) { return res.json(); }).then(function (data) {
            var rows = flattenLookup(data.results || []);
            var exactRows = rows.filter(function (row) { return isExactMatch(row, q); });
            if (commit) {
                if (exactRows.length === 1) {
                    hideSuggest();
                    pickItem(exactRows[0]);
                    return;
                }
                if (exactRows.length > 1) {
                    showSuggest(exactRows);
                    return;
                }
                if (rows.length === 1) {
                    hideSuggest();
                    pickItem(rows[0]);
                    return;
                }
                if (!rows.length) {
                    showSuggest([]);
                    showToast('Artikal nije pronađen.', false);
                    return;
                }
                showSuggest(rows);
                return;
            }
            showSuggest(rows);
        }).catch(function () {
            if (commit) showToast('Pretraga nije uspjela.', false);
        });
    }

    function scan() {
        searchArticles(query && query.value, true);
    }

    function focusQty() {
        var inp = document.getElementById('ptQtyInput');
        if (!inp) return;
        if (currentWrap) {
            currentWrap.hidden = false;
            void currentWrap.offsetHeight;
        }
        qtyTouched = false;
        inp.focus();
        try {
            inp.select();
            if (typeof inp.setSelectionRange === 'function') {
                inp.setSelectionRange(0, String(inp.value || '').length);
            }
        } catch (err) {}
    }

    function focusQuery() {
        if (!query) return;
        query.focus();
        try {
            if (typeof query.select === 'function') query.select();
            if (typeof query.setSelectionRange === 'function') {
                query.setSelectionRange(0, String(query.value || '').length);
            }
        } catch (err) {}
    }

    function goNext() {
        var inp = document.getElementById('ptQtyInput');
        var raw = inp ? String(inp.value || '').trim() : '';
        var key = currentKey;
        if (query) query.value = '';
        hideSuggest();
        renderCurrent(null);
        focusQuery();
        if (!key) return;
        var extra = { key: key };
        if (raw !== '') extra.kolicina = raw;
        post('next', extra);
    }

    if (query) {
        query.addEventListener('input', function () {
            window.clearTimeout(searchTimer);
            var q = (query.value || '').trim();
            if (!q) {
                hideSuggest();
                return;
            }
            searchTimer = window.setTimeout(function () { searchArticles(q, false); }, 180);
        });
        query.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                hideSuggest();
                return;
            }
            if (event.key !== 'Enter') return;
            event.preventDefault();
            window.clearTimeout(searchTimer);
            scan();
        });
        query.addEventListener('mg-scanned', function (event) {
            var code = (event.detail && event.detail.code) || query.value;
            if (code) {
                query.value = code;
                window.clearTimeout(searchTimer);
                scan();
            }
        });
    }
    document.addEventListener('click', function (event) {
        if (!suggest || suggest.hidden) return;
        if (suggest.contains(event.target) || (query && query.contains(event.target))) return;
        hideSuggest();
    });
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
    var qtyInp = document.getElementById('ptQtyInput');
    var nextBtn = document.getElementById('ptNext');
    if (minus) minus.addEventListener('click', function () { post('minus', { key: currentKey }); });
    if (plus) plus.addEventListener('click', function () { post('plus', { key: currentKey }); });
    if (nextBtn) nextBtn.addEventListener('click', function () { goNext(); });
    if (qtyInp) {
        qtyInp.addEventListener('input', function () { qtyTouched = true; });
        qtyInp.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            goNext();
        });
    }
    if (listEl) {
        listEl.addEventListener('click', function (event) {
            var row = event.target.closest('[data-key]');
            if (!row) return;
            focusQty();
            post('select', { key: row.getAttribute('data-key') }).then(function (data) {
                if (data && !qtyTouched) focusQty();
            });
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
    var cancelBtn = document.getElementById('ptCancel');
    if (cancelBtn) {
        cancelBtn.addEventListener('click', function () {
            if (!window.confirm('Otkazati popis? Popisane količine se neće upisati na lokaciju.')) return;
            post('otkazi');
        });
    }
})();
