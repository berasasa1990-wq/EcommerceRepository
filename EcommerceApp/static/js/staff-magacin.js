(function () {
    var app = document.getElementById('mgApp');
    var navBtn = document.getElementById('mgNavToggle');
    var navClose = document.getElementById('mgNavClose');
    var navBackdrop = document.getElementById('mgNavBackdrop');
    function setMagacinNav(open) {
        if (!app) return;
        app.classList.toggle('nav-open', !!open);
        if (navBackdrop) navBackdrop.hidden = !open;
        if (navBtn) navBtn.setAttribute('aria-expanded', open ? 'true' : 'false');
        if (navClose) navClose.hidden = !open;
        document.body.classList.toggle('mg-nav-lock', !!open);
    }
    if (navBtn && app) {
        navBtn.setAttribute('aria-expanded', 'false');
        navBtn.addEventListener('click', function () {
            setMagacinNav(!app.classList.contains('nav-open'));
        });
        app.querySelectorAll('.mg-nav a').forEach(function (link) {
            link.addEventListener('click', function () { setMagacinNav(false); });
        });
        var isPhone = window.matchMedia('(max-width: 860px)').matches;
        var fromMagacin = (document.referrer || '').indexOf('/nalog/magacin/') !== -1;
        if (isPhone && !fromMagacin && !document.body.classList.contains('is-pick')) {
            setMagacinNav(true);
        }
    }
    if (navBackdrop) {
        navBackdrop.addEventListener('click', function () { setMagacinNav(false); });
    }
    if (navClose) {
        navClose.addEventListener('click', function () { setMagacinNav(false); });
    }

    var user = document.getElementById('mgUser');
    if (user) {
        user.addEventListener('click', function (event) {
            event.stopPropagation();
            user.classList.toggle('is-open');
        });
        document.addEventListener('click', function () {
            user.classList.remove('is-open');
        });
    }

    var syncForm = document.getElementById('mgSyncContinue');
    var syncCancel = document.getElementById('mgSyncCancel');
    var syncTimer = null;
    if (syncForm) {
        syncTimer = window.setTimeout(function () { syncForm.submit(); }, 400);
    }
    if (syncCancel) {
        syncCancel.addEventListener('submit', function () {
            if (syncTimer) window.clearTimeout(syncTimer);
        });
    }

    try { initZaliheMenu(); } catch (err) {}
    initCustomerPicker();
    initManualOrderForm();
    initPonudaArticlePicker();
    initOrderBulkPrint();
    initTransferPage();
    initArticleScanner();
    initPopisPage();
    initFaliPrenos();

    var modal = document.getElementById('mgMoveModal');
    if (!modal) return;

    var modeInput = document.getElementById('mgLocMode');
    var title = document.getElementById('mgMoveTitle');
    var updateField = document.getElementById('mgLocUpdateField');
    var addField = document.getElementById('mgLocAddField');
    var locSelect = document.getElementById('mgMoveLocation');
    var addSelect = document.getElementById('mgAddLocation');
    var addQuery = document.getElementById('mgAddLocationQuery');
    var addList = document.getElementById('mgAddLocationList');
    var qtyInput = document.getElementById('mgQtyInput');
    var qtyHint = document.getElementById('mgQtyHint');
    var updateLabel = document.getElementById('mgLocUpdateLabel');
    var addLabel = document.getElementById('mgLocAddLabel');
    var toLocation = document.getElementById('mgToLocation');

    function resetAddSearch() {
        if (addSelect) addSelect.value = '';
        if (toLocation) toLocation.value = '';
        if (addQuery) addQuery.value = '';
        filterAddLocations('');
        if (addList) addList.hidden = true;
    }

    function filterAddLocations(query) {
        if (!addList) return 0;
        var q = (query || '').trim().toLowerCase();
        var shown = 0;
        addList.querySelectorAll('li').forEach(function (item) {
            if (item.getAttribute('data-empty')) {
                item.hidden = !!q;
                return;
            }
            var label = (item.getAttribute('data-label') || item.textContent || '').toLowerCase();
            var match = !q || label.indexOf(q) !== -1;
            item.hidden = !match;
            item.classList.remove('is-active');
            if (match) shown += 1;
        });
        addList.hidden = false;
        return shown;
    }

    function pickAddLocation(item) {
        if (!item || item.getAttribute('data-empty')) return;
        if (addSelect) addSelect.value = item.getAttribute('data-id') || '';
        if (toLocation) toLocation.value = item.getAttribute('data-id') || '';
        if (addQuery) addQuery.value = item.getAttribute('data-label') || item.textContent || '';
        if (addList) addList.hidden = true;
    }

    if (addQuery) {
        addQuery.addEventListener('focus', function () { filterAddLocations(addQuery.value); });
        addQuery.addEventListener('input', function () {
            if (addSelect) addSelect.value = '';
            filterAddLocations(addQuery.value);
        });
        addQuery.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                var first = addList && addList.querySelector('li:not([hidden]):not([data-empty])');
                if (first) {
                    event.preventDefault();
                    pickAddLocation(first);
                }
            }
        });
    }
    if (addList) {
        addList.addEventListener('mousedown', function (event) {
            var item = event.target.closest('li');
            if (item) pickAddLocation(item);
        });
    }

    function fillUpdateQty() {
        if (!qtyInput || !locSelect) return;
        var opt = locSelect.options[locSelect.selectedIndex];
        var qty = opt && opt.getAttribute('data-qty');
        if (qty != null) qtyInput.value = qty;
    }

    function syncMode(mode, meta) {
        var isAdd = mode === 'add';
        var isTransfer = mode === 'transfer';
        var isMp = mode === 'mp';
        var isRemove = mode === 'remove';
        var locLabel = (meta && meta.label) || '';
        var available = meta && meta.available != null && meta.available !== ''
            ? Number(meta.available)
            : null;
        if (modeInput) {
            modeInput.value = isRemove
                ? 'remove'
                : (isMp ? 'mp' : (isTransfer ? 'transfer' : (isAdd ? 'add' : 'update')));
        }
        if (title) {
            title.textContent = isRemove
                ? ('Skini s ' + (locLabel || 'lokacije'))
                : (isMp
                    ? 'Prenos u MP'
                    : (isTransfer
                        ? 'Transfer'
                        : (isAdd ? 'Dodaj u novu lokaciju' : 'Zalihe')));
        }
        if (updateField) updateField.hidden = isAdd || isMp || isRemove;
        if (addField) addField.hidden = !(isAdd || isTransfer);
        if (updateLabel) updateLabel.textContent = isTransfer ? 'Sa lokacije' : 'Lokacija';
        if (addLabel) addLabel.textContent = isTransfer ? 'Na lokaciju' : 'Lokacija';
        if (locSelect) locSelect.required = !(isAdd || isMp || isRemove);
        if (addSelect) addSelect.required = isAdd || isTransfer;
        if (qtyHint) {
            if (isRemove) {
                qtyHint.hidden = false;
                qtyHint.textContent = available != null
                    ? ('Dostupno ' + available + ' kom. Unesi koliko skidaš.')
                    : 'Unesi koliko skidaš s ove lokacije.';
            } else {
                qtyHint.hidden = true;
                qtyHint.textContent = '';
            }
        }
        if (qtyInput) {
            qtyInput.min = (isTransfer || isMp || isRemove) ? '1' : '0';
            if (isRemove && available != null && available >= 0) {
                qtyInput.max = String(Math.max(0, available));
            } else {
                qtyInput.removeAttribute('max');
            }
        }
        if (isMp) {
            resetAddSearch();
            if (qtyInput) qtyInput.value = '1';
        } else if (isRemove) {
            resetAddSearch();
            if (qtyInput) qtyInput.value = '1';
        } else if (isAdd) {
            resetAddSearch();
            if (qtyInput) qtyInput.value = '';
        } else if (isTransfer) {
            resetAddSearch();
            fillUpdateQty();
            if (qtyInput) qtyInput.value = '';
        } else {
            fillUpdateQty();
        }
    }

    if (locSelect) locSelect.addEventListener('change', fillUpdateQty);

    function openModal(locationId, mode, meta) {
        syncMode(mode || 'update', meta || {});
        if (locationId && locSelect && mode !== 'add') {
            locSelect.value = String(locationId);
            if (mode !== 'remove' && mode !== 'mp' && mode !== 'transfer') fillUpdateQty();
        }
        modal.classList.toggle('is-qty-only', mode === 'mp' || mode === 'remove');
        modal.hidden = false;
        if ((mode === 'remove' || mode === 'mp') && qtyInput) {
            window.setTimeout(function () {
                qtyInput.focus();
                qtyInput.select();
            }, 40);
        }
    }
    function closeModal() {
        modal.hidden = true;
    }

    document.querySelectorAll('[data-mg-open-move]').forEach(function (el) {
        el.addEventListener('click', function () {
            if (el.disabled) return;
            openModal(
                el.getAttribute('data-location-id'),
                el.getAttribute('data-mode') || 'update',
                {
                    label: el.getAttribute('data-location-label') || '',
                    available: el.getAttribute('data-available') || '',
                    qty: el.getAttribute('data-qty') || '',
                }
            );
        });
    });
    document.querySelectorAll('[data-mg-close]').forEach(function (el) {
        el.addEventListener('click', closeModal);
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') closeModal();
    });
    var form = modal.querySelector('form');
    if (form) {
        form.addEventListener('submit', function (event) {
            var needsDest = modeInput && (modeInput.value === 'add' || modeInput.value === 'transfer');
            if (needsDest && addSelect && !addSelect.value) {
                event.preventDefault();
                if (addQuery) addQuery.focus();
                filterAddLocations(addQuery ? addQuery.value : '');
                return;
            }
            if (modeInput && modeInput.value === 'remove' && qtyInput) {
                var take = parseInt(qtyInput.value, 10) || 0;
                var max = parseInt(qtyInput.getAttribute('max'), 10);
                if (take < 1) {
                    event.preventDefault();
                    qtyInput.focus();
                    return;
                }
                if (!isNaN(max) && take > max) {
                    event.preventDefault();
                    window.alert('Možeš skinuti najviše ' + max + ' kom s ove lokacije.');
                    qtyInput.focus();
                    qtyInput.select();
                }
            }
        });
    }
})();

function initZaliheMenu() {
    var btn = document.getElementById('mgZaliheBtn');
    var modal = document.getElementById('mgZaliheModal');
    if (!btn || !modal) return;
    function openChooser() { modal.hidden = false; }
    function closeChooser() { modal.hidden = true; }
    btn.addEventListener('click', function (event) {
        event.preventDefault();
        openChooser();
    });
    modal.querySelectorAll('[data-mg-zalihe-close]').forEach(function (el) {
        el.addEventListener('click', closeChooser);
    });
    modal.querySelectorAll('[data-mg-open-move]').forEach(function (el) {
        el.addEventListener('click', closeChooser);
    });
    document.addEventListener('keydown', function (event) {
        if (event.key === 'Escape' && !modal.hidden) closeChooser();
    });
}

function initCustomerPicker() {
    var search = document.getElementById('mgCustomerSearch');
    var list = document.getElementById('mgCustomerSuggest');
    var addBtn = document.getElementById('mgCustomerAdd');
    var locked = document.getElementById('mgCustomerLocked');
    var wrap = document.getElementById('mgCustomerSearchWrap');
    var nameEl = document.getElementById('mgCustomerLockedName');
    var metaEl = document.getElementById('mgCustomerLockedMeta');
    var changeBtn = document.getElementById('mgCustomerChange');
    var editBtn = document.getElementById('mgCustomerEdit');
    var noteWrap = document.getElementById('mgOrderNoteWrap');
    var idInput = document.getElementById('id_customer_id');
    var imeInput = document.getElementById('id_ime_prezime');
    var telInput = document.getElementById('id_telefon');
    var gradInput = document.getElementById('id_grad');
    var adresaInput = document.getElementById('id_adresa');
    var emailInput = document.getElementById('id_email');
    var postInput = document.getElementById('id_postanski_broj');
    var modal = document.getElementById('mgCustomerModal');
    var modalTitle = document.getElementById('mgCustomerModalTitle');
    var editId = document.getElementById('mgEditCustomerId');
    var newIme = document.getElementById('mgNewIme');
    var newTel = document.getElementById('mgNewTelefon');
    var newAdresa = document.getElementById('mgNewAdresa');
    var newGrad = document.getElementById('mgNewGrad');
    var newPost = document.getElementById('mgNewPost');
    var newEmail = document.getElementById('mgNewEmail');
    var modalHint = document.getElementById('mgCustomerModalHint');
    var saveBtn = document.getElementById('mgCustomerSave');
    var form = document.getElementById('mgOrderForm');
    var page = document.getElementById('mgOrderPage');
    var customerCard = document.getElementById('mgCustomerCard');
    var orderBuild = document.getElementById('mgOrderBuild');
    var intro = document.getElementById('mgOrderIntro');
    if (!search || !form) return;
    var lookupUrl = form.getAttribute('data-customer-lookup') || '';
    var saveUrl = form.getAttribute('data-customer-save') || '';
    var timer = null;
    var lastResults = [];
    var saving = false;

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function showHint(text) {
        if (!modalHint) return;
        modalHint.hidden = !text;
        modalHint.textContent = text || '';
    }
    function openAddModal() {
        var ime = (search.value || '').trim();
        if (editId) editId.value = '';
        if (modalTitle) modalTitle.textContent = 'Dodaj kupca';
        if (newIme) newIme.value = ime;
        if (newTel) newTel.value = '';
        if (newAdresa) newAdresa.value = '';
        if (newGrad) newGrad.value = '';
        if (newPost) newPost.value = '';
        if (newEmail) newEmail.value = '';
        showHint('');
        if (list) list.hidden = true;
        if (modal) modal.hidden = false;
        if (ime && newTel) newTel.focus();
        else if (newIme) newIme.focus();
    }
    function openEditModal() {
        if (editId) editId.value = (idInput && idInput.value) || '';
        if (modalTitle) modalTitle.textContent = 'Izmijeni kupca';
        if (newIme) newIme.value = (imeInput && imeInput.value) || '';
        if (newTel) newTel.value = (telInput && telInput.value) || '';
        if (newAdresa) newAdresa.value = (adresaInput && adresaInput.value) || '';
        if (newGrad) newGrad.value = (gradInput && gradInput.value) || '';
        if (newPost) newPost.value = (postInput && postInput.value) || '';
        if (newEmail) newEmail.value = (emailInput && emailInput.value) || '';
        showHint('');
        if (modal) modal.hidden = false;
        if (newIme) newIme.focus();
    }
    function closeAddModal() {
        if (modal) modal.hidden = true;
        showHint('');
    }
    function lockCustomer(data) {
        var ime = (data.ime_prezime || '').trim();
        if (!ime) {
            search.focus();
            return;
        }
        if (imeInput) imeInput.value = ime;
        if (idInput) idInput.value = data.id || '';
        if (telInput) telInput.value = data.telefon || '';
        if (gradInput) gradInput.value = data.grad || '';
        if (adresaInput) adresaInput.value = data.adresa || '';
        if (emailInput) emailInput.value = data.email || '';
        if (postInput) postInput.value = data.postanski_broj || '';
        if (nameEl) nameEl.textContent = ime;
        if (metaEl) {
            var bits = [data.telefon, data.grad, data.adresa].filter(Boolean);
            metaEl.textContent = bits.join(' · ');
        }
        if (wrap) wrap.hidden = true;
        if (locked) locked.hidden = false;
        if (noteWrap) noteWrap.hidden = true;
        if (list) list.hidden = true;
        if (customerCard) customerCard.hidden = false;
        if (orderBuild) orderBuild.hidden = false;
        if (page) {
            page.classList.remove('is-pick-customer');
            page.classList.add('is-ready');
        }
        if (intro) intro.textContent = 'Kreiraj narudžbu dodavanjem kupca i artikla.';
        closeAddModal();
        var articleSearch = document.getElementById('mgOrderSearch');
        if (articleSearch) window.setTimeout(function () { articleSearch.focus(); }, 40);
    }
    function unlockCustomer() {
        if (wrap) wrap.hidden = false;
        if (locked) locked.hidden = true;
        if (noteWrap) noteWrap.hidden = true;
        if (customerCard) customerCard.hidden = false;
        if (orderBuild) orderBuild.hidden = false;
        if (page) {
            page.classList.remove('is-pick-customer');
            page.classList.remove('is-ready');
        }
        if (intro) intro.textContent = 'Kreiraj narudžbu dodavanjem kupca i artikla.';
        if (idInput) idInput.value = '';
        if (search) {
            search.value = (imeInput && imeInput.value) || search.value || '';
            search.focus();
        }
    }
    function renderList(rows) {
        lastResults = rows || [];
        if (!list) return;
        list.innerHTML = '';
        if (!lastResults.length) {
            list.innerHTML = '<li class="is-empty">Nema sačuvanog kupca. Klikni Dodaj kupca.</li>';
            list.hidden = false;
            return;
        }
        lastResults.forEach(function (row) {
            var li = document.createElement('li');
            li.innerHTML = '<strong>' + escapeHtml(row.ime_prezime) + '</strong><span>' +
                escapeHtml([row.telefon, row.grad].filter(Boolean).join(' · ')) + '</span>';
            li.addEventListener('mousedown', function (event) {
                event.preventDefault();
                lockCustomer(row);
            });
            list.appendChild(li);
        });
        list.hidden = false;
    }
    function searchCustomers(query) {
        if (!lookupUrl) return;
        var q = (query || '').trim();
        fetch(lookupUrl + (q ? '?q=' + encodeURIComponent(q) : ''), {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        })
            .then(function (res) { return res.json(); })
            .then(function (data) { renderList(data.results || []); })
            .catch(function () {
                if (list) {
                    list.innerHTML = '<li class="is-empty">Pretraga nije uspjela.</li>';
                    list.hidden = false;
                }
            });
    }

    search.addEventListener('input', function () {
        window.clearTimeout(timer);
        timer = window.setTimeout(function () { searchCustomers(search.value); }, 160);
    });
    search.addEventListener('focus', function () {
        if (lastResults.length && !(search.value || '').trim()) renderList(lastResults);
        else searchCustomers(search.value);
    });
    if (addBtn) addBtn.addEventListener('click', openAddModal);
    if (editBtn) editBtn.addEventListener('click', openEditModal);
    if (saveBtn) {
        saveBtn.addEventListener('click', function () {
            var ime = (newIme && newIme.value || '').trim();
            var tel = (newTel && newTel.value || '').trim();
            if (!ime) {
                showHint('Unesi ime i prezime.');
                if (newIme) newIme.focus();
                return;
            }
            if (!tel) {
                showHint('Unesi telefon.');
                if (newTel) newTel.focus();
                return;
            }
            if (saving) return;
            var csrf = form.querySelector('[name=csrfmiddlewaretoken]');
            var body = new URLSearchParams();
            body.set('ime_prezime', ime);
            body.set('telefon', tel);
            body.set('adresa', newAdresa ? newAdresa.value.trim() : '');
            body.set('grad', newGrad ? newGrad.value.trim() : '');
            body.set('email', newEmail ? newEmail.value.trim() : '');
            body.set('postanski_broj', newPost ? newPost.value.trim() : '');
            if (editId && editId.value) body.set('customer_id', editId.value);
            saving = true;
            saveBtn.disabled = true;
            showHint('');
            fetch(saveUrl, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': csrf ? csrf.value : '',
                    'Content-Type': 'application/x-www-form-urlencoded',
                },
                credentials: 'same-origin',
                body: body.toString(),
            })
                .then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
                .then(function (result) {
                    saving = false;
                    saveBtn.disabled = false;
                    if (!result.data || !result.data.ok || !result.data.customer) {
                        showHint((result.data && result.data.error) || 'Kupac nije sačuvan.');
                        return;
                    }
                    lockCustomer(result.data.customer);
                })
                .catch(function () {
                    saving = false;
                    saveBtn.disabled = false;
                    showHint('Kupac nije sačuvan. Pokušaj ponovo.');
                });
        });
    }
    if (modal) {
        modal.querySelectorAll('[data-customer-close]').forEach(function (el) {
            el.addEventListener('click', closeAddModal);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && modal && !modal.hidden) closeAddModal();
        });
    }
    if (changeBtn) changeBtn.addEventListener('click', unlockCustomer);
    document.addEventListener('click', function (event) {
        if (wrap && !wrap.contains(event.target) && list) list.hidden = true;
    });
    form.addEventListener('submit', function (event) {
        var submitter = event.submitter;
        if (submitter && submitter.getAttribute('name') === 'action' && submitter.value === 'otkazi') {
            if (!window.confirm('Otkazati narudžbu i vratiti rezervaciju na lokacije?')) {
                event.preventDefault();
            }
            return;
        }
        if (locked && locked.hidden) {
            event.preventDefault();
            if (search) search.focus();
            return;
        }
        if (imeInput && !imeInput.value.trim()) {
            event.preventDefault();
            if (search) search.focus();
        }
    });
    if ((imeInput && imeInput.value) && (telInput && telInput.value)) {
        lockCustomer({
            id: idInput ? idInput.value : '',
            ime_prezime: imeInput.value,
            telefon: telInput.value,
            grad: gradInput ? gradInput.value : '',
            adresa: adresaInput ? adresaInput.value : '',
            email: emailInput ? emailInput.value : '',
            postanski_broj: postInput ? postInput.value : '',
        });
    }
}

function initPonudaArticlePicker() {
    var root = document.getElementById('pnApp');
    if (!root) return;
    var search = document.getElementById('pnQuery');
    var list = document.getElementById('pnSuggest');
    var catalog = document.getElementById('pnCatalog');
    var catalogBtn = document.getElementById('pnCatalogBtn');
    var qtyHint = document.getElementById('pnQtyHint');
    var qtyModal = document.getElementById('pnQtyModal');
    var qtyName = document.getElementById('pnQtyName');
    var qtyMeta = document.getElementById('pnQtyMeta');
    var qtyInput = document.getElementById('pnQtyInput');
    var qtyAddBtn = document.getElementById('pnQtyAdd');
    var addForm = document.getElementById('pnAddForm');
    var pid = document.getElementById('pnProductId');
    var vid = document.getElementById('pnVariationId');
    var addQty = document.getElementById('pnAddQty');
    var qtyForm = document.getElementById('pnQtyForm');
    var qtyStavkaId = document.getElementById('pnQtyStavkaId');
    var qtyValue = document.getElementById('pnQtyValue');
    var removeForm = document.getElementById('pnRemoveForm');
    var removeStavkaId = document.getElementById('pnRemoveStavkaId');
    var lookupUrl = root.getAttribute('data-lookup') || '';
    var timer = null;
    var pick = null;
    var lastResults = [];

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function catalogOpen() {
        return !!(catalog && !catalog.hidden);
    }
    function showHint(text, isWarn) {
        if (!qtyHint) return;
        if (!text) {
            qtyHint.hidden = true;
            qtyHint.textContent = '';
            qtyHint.classList.remove('is-warn');
            return;
        }
        qtyHint.hidden = false;
        qtyHint.textContent = text;
        qtyHint.classList.toggle('is-warn', !!isWarn);
    }
    function closeQty() {
        if (qtyModal) qtyModal.hidden = true;
    }
    function lineInfo(pidVal, vidVal) {
        var el = root.querySelector(
            'li[data-pid="' + pidVal + '"][data-vid="' + (vidVal || '') + '"]'
        );
        if (!el) return { qty: 0, sid: '' };
        return {
            qty: parseInt(el.getAttribute('data-qty'), 10) || 0,
            sid: el.getAttribute('data-sid') || '',
        };
    }
    function flatten(data) {
        var rows = [];
        (data.results || []).forEach(function (prod) {
            var vars = prod.varijacije || [];
            var parentQty = Number(prod.dostupno) || 0;
            var varRows = (vars || []).map(function (v) {
                return {
                    item: prod,
                    variation: v,
                    naziv: (prod.naziv || '') + (v.naziv ? ' ' + v.naziv : ''),
                    sifra: v.sifra || prod.sifra || '',
                    barkod: prod.barkod || '',
                    cijena: v.cijena || prod.cijena || '',
                    dostupno: Number(v.na_stanju != null ? v.na_stanju : 0) || 0,
                };
            });
            var varQty = varRows.reduce(function (sum, row) { return sum + (row.dostupno || 0); }, 0);
            if (vars.length > 1 && varQty > 0) {
                varRows.forEach(function (row) { rows.push(row); });
            } else if (vars.length === 1 && varQty > 0) {
                rows.push(varRows[0]);
            } else {
                rows.push({
                    item: prod,
                    variation: null,
                    naziv: prod.naziv,
                    sifra: prod.sifra || '',
                    barkod: prod.barkod || '',
                    cijena: prod.cijena || '',
                    dostupno: parentQty,
                });
            }
        });
        return rows;
    }
    function norm(value) {
        return String(value || '').replace(/\s+/g, '').toLowerCase();
    }
    function looksLikeBarcode(query) {
        return /^\d{8,14}$/.test(String(query || '').replace(/\s+/g, ''));
    }
    function isBarcodeHit(item, query) {
        var q = norm(query);
        return !!q && norm(item.barkod) === q;
    }
    function isPrefixOfLongerBarcode(query, rows, hit) {
        var q = norm(query);
        if (!q) return false;
        return rows.some(function (row) {
            if (row === hit) return false;
            var barkod = norm(row.barkod);
            return barkod.length > q.length && barkod.indexOf(q) === 0;
        });
    }
    function pickBarcode(rows, query) {
        var hit = rows.filter(function (row) { return isBarcodeHit(row, query); })[0];
        if (hit && !isPrefixOfLongerBarcode(query, rows, hit)) return hit;
        if (looksLikeBarcode(query) && rows.length === 1) return rows[0];
        return null;
    }
    function money(n) {
        var x = (Math.round((Number(n) || 0) * 100) / 100).toFixed(2);
        return x + ' KM';
    }
    function csrfToken() {
        var el = addForm && addForm.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }
    function postAction(body, afterAdd) {
        body = body || {};
        var params = new URLSearchParams();
        Object.keys(body).forEach(function (key) { params.set(key, body[key]); });
        params.set('csrfmiddlewaretoken', csrfToken());
        return fetch(window.location.pathname, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken(),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            credentials: 'same-origin',
            body: params.toString(),
        }).then(function (res) {
            return res.json().then(function (data) {
                return { ok: res.ok, data: data };
            }, function () {
                return { ok: false, data: { ok: false, error: 'Nije sačuvano.' } };
            });
        }).then(function (result) {
              if (!result.data || !result.data.ok) {
                  showHint((result.data && result.data.error) || 'Nije sačuvano.', true);
                  return null;
              }
              applyPayload(result.data);
              if (afterAdd) readyNext();
              return result.data;
          }).catch(function () {
              showHint('Nije sačuvano. Pokušaj ponovo.', true);
              return null;
          });
    }
    function applyPayload(data) {
        renderLines(data.stavke || []);
        renderTotals(data.totals || {});
        if (catalogOpen()) renderCatalog(lastResults);
    }
    function renderTotals(totals) {
        var box = document.getElementById('pnSum');
        if (!box) return;
        function set(name, value, prefix) {
            var el = box.querySelector('[data-sum="' + name + '"]');
            if (el) el.textContent = (prefix || '') + money(value);
        }
        set('osnova', totals.osnova);
        set('popust', totals.popust, '−');
        set('net', totals.net);
        set('pdv', totals.pdv);
        set('gross', totals.ukupno_sa_pdv);
        var disc = box.querySelector('.is-disc');
        if (disc) disc.hidden = !(Number(totals.popust) > 0);
    }
    function renderLines(rows) {
        var wrap = document.getElementById('pnLines');
        if (!wrap) return;
        wrap.innerHTML = '';
        if (!rows.length) {
            wrap.innerHTML = '<li class="is-empty">Nema stavki. Dodaj iz kataloga ili ručno.</li>';
            return;
        }
        rows.forEach(function (row) {
            var li = document.createElement('li');
            li.setAttribute('data-pid', String(row.product_id || ''));
            li.setAttribute('data-vid', String(row.variation_id || ''));
            li.setAttribute('data-sid', String(row.pk));
            li.setAttribute('data-qty', String(row.kolicina));
            li.innerHTML =
                '<span class="vp-name"></span>' +
                '<span class="vp-code"></span>' +
                '<input class="vp-qty" data-line-qty type="number" min="1" step="1" aria-label="Količina">' +
                '<input class="vp-qty" data-line-cijena inputmode="decimal" aria-label="Cijena sa PDV" style="width:88px;">' +
                '<button type="button" class="mg-btn vp-remove" data-line-remove>Ukloni</button>';
            var nameEl = li.querySelector('.vp-name');
            nameEl.textContent = row.naziv || '';
            if (row.manuelno) {
                var mark = document.createElement('i');
                mark.className = 'vp-mp';
                mark.textContent = 'Ručno';
                nameEl.appendChild(document.createTextNode(' '));
                nameEl.appendChild(mark);
            }
            li.querySelector('.vp-code').textContent = row.sifra || '—';
            li.querySelector('[data-line-qty]').value = String(row.kolicina);
            li.querySelector('[data-line-cijena]').value = String(row.cijena);
            wrap.appendChild(li);
        });
    }
    function readyNext() {
        pick = null;
        closeQty();
        showHint('');
        if (list) list.hidden = true;
        if (search) {
            search.value = '';
            window.setTimeout(function () {
                if (!search) return;
                if (typeof search.focus === 'function') {
                    try { search.focus({ preventScroll: true }); }
                    catch (err) { search.focus(); }
                }
            }, 40);
        }
    }
    function submitAdd(item, variation, qty, afterAdd) {
        return postAction({
            action: 'dodaj',
            product_id: String(item.id || ''),
            variation_id: variation && variation.id ? String(variation.id) : '',
            kolicina: String(qty || 1),
        }, !!afterAdd);
    }
    function submitQty(sid, qty) {
        return postAction({
            action: 'kolicina',
            stavka_id: String(sid),
            kolicina: String(qty),
        }, false);
    }
    function submitRemove(sid) {
        return postAction({
            action: 'ukloni',
            stavka_id: String(sid),
        }, false);
    }
    function submitCijena(sid, cijena) {
        return postAction({
            action: 'cijena',
            stavka_id: String(sid),
            cijena: String(cijena),
        }, false);
    }
    function openQty(row) {
        if (!row) return;
        pick = { item: row.item, variation: row.variation };
        if (list) list.hidden = true;
        if (qtyName) qtyName.textContent = row.naziv || 'Artikal';
        if (qtyMeta) {
            qtyMeta.textContent = [
                row.sifra,
                row.cijena ? row.cijena + ' KM' : '',
                (row.dostupno || 0) + ' na stanju',
            ].filter(Boolean).join(' · ');
        }
        if (qtyInput) qtyInput.value = '1';
        if (qtyModal) qtyModal.hidden = false;
        showHint('');
        window.setTimeout(function () {
            if (!qtyInput) return;
            qtyInput.focus();
            qtyInput.select();
        }, 40);
    }
    function commitPick() {
        if (!pick) return;
        var qty = parseInt(qtyInput && qtyInput.value, 10) || 0;
        if (qty < 1) {
            showHint('Unesi količinu.');
            if (qtyInput) qtyInput.focus();
            return;
        }
        var item = pick.item;
        var variation = pick.variation;
        readyNext();
        submitAdd(item, variation, qty, false);
    }
    function setCatalog(on) {
        if (!catalog || !catalogBtn) return;
        catalog.hidden = !on;
        catalogBtn.classList.toggle('is-on', on);
        catalogBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        if (list) list.hidden = true;
        if (on) {
            if ((search && search.value || '').trim()) searchProducts({});
            else renderCatalog([]);
        }
    }
    function renderCatalog(rows) {
        if (!catalog) return;
        catalog.innerHTML = '';
        if (!rows || !rows.length) {
            catalog.innerHTML = '<p class="mg-empty">Ukucaj naziv, šifru ili barkod — izlistaće se artikli.</p>';
            return;
        }
        rows.forEach(function (row) {
            var pidVal = String(row.item.id);
            var vidVal = row.variation ? String(row.variation.id) : '';
            var info = lineInfo(pidVal, vidVal);
            var qty = info.qty;
            var card = document.createElement('article');
            card.className = 'mg-catalog-item' + (qty ? ' is-added' : '') + ((row.dostupno || 0) <= 0 ? ' is-out' : '');
            card.innerHTML =
                '<strong></strong>' +
                (row.variation && row.variation.naziv ? '<span class="sub"></span>' : '') +
                '<p class="mg-catalog-meta"></p>' +
                '<div class="mg-catalog-qty">' +
                '<button type="button" class="mg-btn" data-cat-minus aria-label="Manje">−</button>' +
                '<em data-cat-qty></em>' +
                '<button type="button" class="mg-btn" data-cat-plus aria-label="Više">+</button>' +
                '</div>';
            card.querySelector('strong').textContent = row.item.naziv || row.naziv || '';
            var sub = card.querySelector('.sub');
            if (sub) sub.textContent = row.variation.naziv;
            card.querySelector('.mg-catalog-meta').textContent = [
                row.sifra,
                row.cijena ? row.cijena + ' KM' : '',
                (row.dostupno || 0) + ' na stanju',
            ].filter(Boolean).join(' · ');
            card.querySelector('[data-cat-qty]').textContent = String(qty);
            var minus = card.querySelector('[data-cat-minus]');
            minus.disabled = qty <= 0;
            minus.addEventListener('click', function (event) {
                event.preventDefault();
                if (!info.sid) return;
                if (qty <= 1) submitRemove(info.sid);
                else submitQty(info.sid, qty - 1);
            });
            card.querySelector('[data-cat-plus]').addEventListener('click', function (event) {
                event.preventDefault();
                submitAdd(row.item, row.variation, 1);
            });
            catalog.appendChild(card);
        });
    }
    function renderSuggest(items) {
        lastResults = items || [];
        if (!list) return;
        list.innerHTML = '';
        if (!lastResults.length) {
            list.innerHTML = '<li class="is-empty">Nema rezultata.</li>';
            list.hidden = false;
            return;
        }
        lastResults.forEach(function (row) {
            var li = document.createElement('li');
            li.innerHTML = '<span class="vp-name"></span><span></span>';
            li.querySelector('.vp-name').textContent = row.naziv || '';
            li.querySelector('span:last-child').textContent =
                [row.sifra, row.cijena ? row.cijena + ' KM' : '', (row.dostupno || 0) + ' na stanju'].filter(Boolean).join(' · ');
            li.addEventListener('mousedown', function (event) {
                event.preventDefault();
                openQty(row);
            });
            list.appendChild(li);
        });
        list.hidden = false;
    }
    function searchProducts(opts) {
        opts = opts || {};
        var q = (search && search.value || '').trim();
        if (q.length < 1) {
            if (list) list.hidden = true;
            lastResults = [];
            if (catalogOpen()) renderCatalog([]);
            return Promise.resolve(null);
        }
        var limit = catalogOpen() ? 80 : 20;
        return fetch(lookupUrl + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1&limit=' + limit, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        }).then(function (res) { return res.json(); }).then(function (data) {
            var rows = flatten(data);
            lastResults = rows;
            var hit = pickBarcode(rows, q);
            if (hit && (opts.fromScan || looksLikeBarcode(q) || isBarcodeHit(hit, q))) {
                openQty(hit);
                return hit;
            }
            if (opts.takeFirst && rows.length && !catalogOpen()) {
                openQty(rows[0]);
                return rows[0];
            }
            if (catalogOpen()) {
                if (list) list.hidden = true;
                renderCatalog(rows);
                return null;
            }
            renderSuggest(rows);
            return null;
        }).catch(function () {
            if (list) {
                list.innerHTML = '<li class="is-empty">Pretraga nije uspjela.</li>';
                list.hidden = false;
            }
            return null;
        });
    }

    if (search) {
        search.addEventListener('input', function () {
            pick = null;
            showHint('');
            window.clearTimeout(timer);
            timer = window.setTimeout(function () { searchProducts({}); }, 160);
        });
        search.addEventListener('keydown', function (event) {
            if (event.key === 'Tab' && !event.shiftKey) {
                event.preventDefault();
                window.clearTimeout(timer);
                if (lastResults.length) {
                    openQty(lastResults[0]);
                    return;
                }
                searchProducts({ takeFirst: true });
                return;
            }
            if (event.key !== 'Enter') return;
            event.preventDefault();
            window.clearTimeout(timer);
            searchProducts({ fromScan: looksLikeBarcode(search.value) });
        });
        search.addEventListener('mg-scanned', function () {
            window.clearTimeout(timer);
            searchProducts({ fromScan: true });
        });
    }
    if (catalogBtn) {
        catalogBtn.addEventListener('click', function () {
            setCatalog(!catalogOpen());
            if (search) search.focus();
        });
    }
    if (qtyInput) {
        qtyInput.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                closeQty();
                if (search) search.focus();
                return;
            }
            if (event.key !== 'Enter' && event.key !== 'Tab') return;
            event.preventDefault();
            commitPick();
        });
    }
    if (qtyAddBtn) qtyAddBtn.addEventListener('click', function () { commitPick(); });
    if (qtyModal) {
        qtyModal.querySelectorAll('[data-pn-qty-close]').forEach(function (el) {
            el.addEventListener('click', function () {
                closeQty();
                if (search) search.focus();
            });
        });
    }
    document.addEventListener('click', function (event) {
        var box = document.getElementById('pnSearchBox');
        if (box && !box.contains(event.target) && list) list.hidden = true;
    });
    var lines = document.getElementById('pnLines');
    if (lines) {
        lines.addEventListener('change', function (event) {
            var row = event.target.closest('li[data-sid]');
            if (!row) return;
            var sid = row.getAttribute('data-sid');
            if (event.target.matches('[data-line-qty]')) {
                submitQty(sid, event.target.value);
            } else if (event.target.matches('[data-line-cijena]')) {
                submitCijena(sid, event.target.value);
            }
        });
        lines.addEventListener('click', function (event) {
            var btn = event.target.closest('[data-line-remove]');
            if (!btn) return;
            var row = btn.closest('li[data-sid]');
            if (row) submitRemove(row.getAttribute('data-sid'));
        });
    }
    var discForm = document.getElementById('pnDiscForm');
    if (discForm) {
        discForm.addEventListener('submit', function (event) {
            event.preventDefault();
            postAction({
                action: 'popust',
                popust_postotak: (document.getElementById('pnPct') || {}).value || '',
                popust_iznos: (document.getElementById('pnKm') || {}).value || '0',
            }, false);
        });
    }
    var manual = root.querySelector('form.pn-manual');
    if (manual) {
        manual.addEventListener('submit', function (event) {
            event.preventDefault();
            postAction({
                action: 'dodaj_rucno',
                naziv: (document.getElementById('pnManNaziv') || {}).value || '',
                sifra: (document.getElementById('pnManSifra') || {}).value || '',
                kolicina: (document.getElementById('pnManQty') || {}).value || '1',
                cijena: (document.getElementById('pnManCijena') || {}).value || '',
            }, true).then(function (data) {
                if (!data) return;
                if (document.getElementById('pnManNaziv')) document.getElementById('pnManNaziv').value = '';
                if (document.getElementById('pnManSifra')) document.getElementById('pnManSifra').value = '';
                if (document.getElementById('pnManQty')) document.getElementById('pnManQty').value = '1';
                if (document.getElementById('pnManCijena')) document.getElementById('pnManCijena').value = '';
            });
        });
    }
}

function initManualOrderForm() {
    var form = document.getElementById('mgOrderForm');
    if (!form) return;
    var page = document.getElementById('mgOrderPage');

    var search = document.getElementById('mgOrderSearch');
    var qtyInput = document.getElementById('mgOrderQty');
    var qtyHint = document.getElementById('mgOrderQtyHint');
    var list = document.getElementById('mgOrderSuggest');
    var qtyModal = document.getElementById('mgOrderQtyModal');
    var qtyName = document.getElementById('mgOrderQtyName');
    var qtyMeta = document.getElementById('mgOrderQtyMeta');
    var qtyAddBtn = document.getElementById('mgOrderQtyAdd');
    var addedCount = document.getElementById('mgOrderAddedCount');
    var body = document.getElementById('mgOrderLines');
    var empty = document.getElementById('mgOrderEmpty');
    var totalEl = document.getElementById('mgOrderTotal');
    var mpModal = document.getElementById('mgMpModal');
    var mpText = document.getElementById('mgMpText');
    var catalog = document.getElementById('mgOrderCatalog');
    var catalogBtn = document.getElementById('mgOrderCatalogBtn');
    var lookupUrl = form.getAttribute('data-lookup-url') || '';
    var shipFee = parseFloat(form.getAttribute('data-dostava-cijena')) || 11;
    var shipFreeFrom = parseFloat(form.getAttribute('data-besplatna-od')) || 250;
    var shipEl = document.getElementById('mgOrderShip');
    var discInput = document.getElementById('mgOrderDisc');
    var discAmt = document.getElementById('mgOrderDiscAmt');
    var noShipInput = document.getElementById('mgOrderNoShip');
    var noShipBtn = document.getElementById('mgOrderNoShipBtn');
    var timer = null;
    var pending = null;
    var pick = null;
    var lastResults = [];

    function money(n) {
        return (Math.round((Number(n) || 0) * 100) / 100).toFixed(2);
    }
    function discountPct() {
        if (!discInput) return 0;
        var raw = String(discInput.value || '').replace(',', '.').replace(/[^\d.]/g, '');
        var pct = parseFloat(raw);
        if (!isFinite(pct) || pct < 0) return 0;
        if (pct > 100) return 100;
        return pct;
    }
    function shipWaived() {
        return !!(noShipInput && noShipInput.value === '1');
    }
    function payByCard() {
        var el = form.querySelector('input[name="placanje"]:checked');
        return !!(el && el.value === 'kartica');
    }
    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function lineCount() {
        return body ? body.querySelectorAll('tr[data-line]').length : 0;
    }
    function refreshTotal() {
        var sum = 0;
        if (!body) return;
        body.querySelectorAll('tr[data-line]').forEach(function (row) {
            var qty = parseInt(row.querySelector('[name="kolicina"]').value, 10) || 0;
            var price = parseFloat(row.getAttribute('data-cijena')) || 0;
            var lineTotal = qty * price;
            var cell = row.querySelector('[data-line-total]');
            if (cell) cell.textContent = money(lineTotal) + ' KM';
            sum += lineTotal;
        });
        var card = payByCard();
        var ship = (sum > 0 && sum < shipFreeFrom) ? shipFee : 0;
        if (shipWaived() || card) ship = 0;
        var pct = card ? 100 : discountPct();
        var discount = card ? sum : (sum * pct / 100);
        if (page) page.classList.toggle('is-card-pay', card);
        if (shipEl) {
            if ((shipWaived() || card) && sum > 0) {
                shipEl.textContent = 'Dostava: skinuta';
            } else {
                shipEl.textContent = ship > 0
                    ? ('Dostava: ' + money(ship) + ' KM')
                    : (sum > 0 ? 'Dostava: besplatna' : ('Dostava: ' + money(shipFee) + ' KM'));
            }
        }
        if (noShipBtn) {
            noShipBtn.disabled = card;
            noShipBtn.classList.toggle('is-on', shipWaived() || card);
            noShipBtn.textContent = (shipWaived() || card) ? 'Vrati dostavu' : 'Skini dostavu';
        }
        if (discInput) discInput.disabled = card;
        if (discAmt) {
            discAmt.hidden = !(pct > 0 && sum > 0);
            discAmt.textContent = card
                ? ('Kartica: −' + money(discount) + ' KM')
                : ('Popust: −' + money(discount) + ' KM');
        }
        if (totalEl) totalEl.textContent = money(sum - discount + ship) + ' KM';
        if (empty) empty.hidden = lineCount() > 0;
        if (addedCount) {
            var n = lineCount();
            addedCount.textContent = n === 1 ? '1 stavka' : (n + ' stavki');
        }
    }
    function availableOf(item, variation) {
        if (variation) return Number(variation.na_stanju) || 0;
        return Number(item.dostupno != null ? item.dostupno : item.na_stanju) || 0;
    }
    function findRow(pid, vid) {
        return body.querySelector('tr[data-pid="' + pid + '"][data-vid="' + (vid || '') + '"]');
    }
    function catalogOpen() {
        return !!(catalog && !catalog.hidden);
    }
    function lineQty(pid, vid) {
        var row = findRow(pid, vid);
        if (!row) return 0;
        return parseInt(row.querySelector('[name="kolicina"]').value, 10) || 0;
    }
    function pickName(item, variation) {
        return item.naziv + (variation ? ' — ' + variation.naziv : '');
    }
    function stockHtml(qty) {
        var n = Number(qty) || 0;
        var cls = n > 0 ? 'mg-stock' : 'mg-stock is-zero';
        return '<span class="' + cls + '">' + n + ' na stanju</span>';
    }
    function showHint(text, isWarn) {
        if (!qtyHint) return;
        if (!text) {
            qtyHint.hidden = true;
            qtyHint.textContent = '';
            qtyHint.classList.remove('is-warn');
            return;
        }
        qtyHint.hidden = false;
        qtyHint.textContent = text;
        qtyHint.classList.toggle('is-warn', !!isWarn);
    }
    function closeQty() {
        if (qtyModal) qtyModal.hidden = true;
    }
    function resetPicker(keepHint) {
        pick = null;
        if (qtyInput) qtyInput.value = '1';
        if (!keepHint) showHint('');
        if (list) list.hidden = true;
        closeQty();
        if (search) {
            search.value = '';
            search.focus();
        }
    }
    function askMp(opts) {
        pending = opts || null;
        if (!pending) return;
        var name = pending.name || pickName(pending.item, pending.variation);
        var available = pending.available;
        if (mpText) {
            mpText.innerHTML =
                '„' + escapeHtml(name) + '” nema dostupnog artikla' +
                (available != null ? ' (' + available + ' kom).' : '.') +
                ' Nema ga ni na jednoj lokaciji.';
        }
        if (mpModal) mpModal.hidden = false;
    }
    function hideMp() {
        pending = null;
        if (mpModal) mpModal.hidden = true;
    }
    function rowIsMp(row) {
        var mp = row && row.querySelector('[name="mp_ok"]');
        return !!(mp && mp.value === '1');
    }
    function addLine(item, variation, mpOk, qty) {
        qty = parseInt(qty, 10) || 1;
        if (qty < 1) qty = 1;
        var pid = String(item.id);
        var vid = variation ? String(variation.id) : '';
        var existing = findRow(pid, vid);
        if (existing) {
            var existingQty = existing.querySelector('[name="kolicina"]');
            existingQty.value = (parseInt(existingQty.value, 10) || 0) + qty;
            existingQty.setAttribute('data-prev', existingQty.value);
            if (mpOk) {
                existing.querySelector('[name="mp_ok"]').value = '1';
                if (!existing.querySelector('.mg-pill.is-manual')) {
                    var existingName = existing.querySelector('td');
                    var existingMark = document.createElement('span');
                    existingMark.className = 'mg-pill is-manual';
                    existingMark.textContent = 'Nije popisan';
                    if (existingName) existingName.appendChild(existingMark);
                }
            }
            refreshTotal();
            return;
        }
        var available = availableOf(item, variation);
        var cijena = variation ? variation.cijena : item.cijena;
        var naziv = item.naziv;
        var varNaziv = variation ? variation.naziv : '';
        var sifra = (variation && variation.sifra) ? variation.sifra : (item.sifra || '—');
        var tr = document.createElement('tr');
        tr.setAttribute('data-line', '1');
        tr.setAttribute('data-pid', pid);
        tr.setAttribute('data-vid', vid);
        tr.setAttribute('data-cijena', cijena);
        tr.setAttribute('data-available', available);
        if (available <= 0) tr.classList.add('is-out-row');
        tr.innerHTML =
            '<td><strong>' + escapeHtml(naziv) + '</strong>' +
            (varNaziv ? '<span class="sub">' + escapeHtml(varNaziv) + '</span>' : '') +
            (mpOk ? '<span class="mg-pill is-manual">Nije popisan</span>' : '') +
            '<input type="hidden" name="product_id" value="' + escapeHtml(pid) + '">' +
            '<input type="hidden" name="variation_id" value="' + escapeHtml(vid) + '">' +
            '<input type="hidden" name="mp_ok" value="' + (mpOk ? '1' : '0') + '">' +
            '<input type="hidden" name="rezervni" value="0">' +
            '<input type="hidden" name="spare_naziv" value="">' +
            '<input type="hidden" name="spare_cijena" value="">' +
            '</td>' +
            '<td>' + escapeHtml(sifra) + '</td>' +
            '<td class="num ' + (available <= 0 ? 'num-out' : 'num-ok') + '">' + available + '</td>' +
            '<td class="num"><input class="mg-qty-input" name="kolicina" type="number" min="1" step="1" value="' + qty + '" data-prev="' + qty + '" required></td>' +
            '<td class="num">' + escapeHtml(cijena) + ' KM</td>' +
            '<td class="num" data-line-total>' + money(cijena * qty) + ' KM</td>' +
            '<td class="num"><button type="button" class="mg-btn mg-btn-danger" data-remove-line>Ukloni</button></td>';
        body.appendChild(tr);
        refreshTotal();
    }
    function addSpareLine(naziv, qty, cijena) {
        naziv = String(naziv || '').trim();
        qty = parseInt(qty, 10) || 1;
        if (qty < 1) qty = 1;
        cijena = money(cijena);
        var existing = null;
        if (body) {
            body.querySelectorAll('tr[data-rezervni="1"]').forEach(function (row) {
                var n = (row.querySelector('[name="spare_naziv"]') || {}).value || '';
                if (n.trim().toLowerCase() === naziv.toLowerCase()) existing = row;
            });
        }
        if (existing) {
            var existingQty = existing.querySelector('[name="kolicina"]');
            existingQty.value = (parseInt(existingQty.value, 10) || 0) + qty;
            existingQty.setAttribute('data-prev', existingQty.value);
            refreshTotal();
            return;
        }
        var tr = document.createElement('tr');
        tr.setAttribute('data-line', '1');
        tr.setAttribute('data-pid', '');
        tr.setAttribute('data-vid', '');
        tr.setAttribute('data-cijena', cijena);
        tr.setAttribute('data-available', '0');
        tr.setAttribute('data-rezervni', '1');
        tr.className = 'is-spare-row';
        tr.innerHTML =
            '<td><strong>' + escapeHtml(naziv) + '</strong>' +
            '<span class="mg-pill is-spare">Rezervni dio</span>' +
            '<input type="hidden" name="product_id" value="">' +
            '<input type="hidden" name="variation_id" value="">' +
            '<input type="hidden" name="mp_ok" value="0">' +
            '<input type="hidden" name="rezervni" value="1">' +
            '<input type="hidden" name="spare_naziv" value="' + escapeHtml(naziv) + '">' +
            '<input type="hidden" name="spare_cijena" value="' + escapeHtml(cijena) + '">' +
            '</td>' +
            '<td>REZERVNI</td>' +
            '<td class="num">—</td>' +
            '<td class="num"><input class="mg-qty-input" name="kolicina" type="number" min="1" step="1" value="' + qty + '" data-prev="' + qty + '" required></td>' +
            '<td class="num">' + escapeHtml(cijena) + ' KM</td>' +
            '<td class="num" data-line-total>' + money(Number(cijena) * qty) + ' KM</td>' +
            '<td class="num"><button type="button" class="mg-btn mg-btn-danger" data-remove-line>Ukloni</button></td>';
        body.appendChild(tr);
        refreshTotal();
    }
    function norm(value) {
        return String(value || '').replace(/\s+/g, '').toLowerCase();
    }
    function looksLikeBarcode(query) {
        return /^\d{8,14}$/.test(String(query || '').replace(/\s+/g, ''));
    }
    function isBarcodeHit(item, query) {
        var q = norm(query);
        return !!q && norm(item.barkod) === q;
    }
    function isPrefixOfLongerBarcode(query, rows, hit) {
        var q = norm(query);
        if (!q) return false;
        return rows.some(function (row) {
            if (row === hit) return false;
            var barkod = norm(row.barkod);
            return barkod.length > q.length && barkod.indexOf(q) === 0;
        });
    }
    function flatten(data) {
        var rows = [];
        (data.results || []).forEach(function (prod) {
            var vars = prod.varijacije || [];
            var parentQty = Number(prod.dostupno) || 0;
            var varRows = (vars || []).map(function (v) {
                return {
                    item: prod,
                    variation: v,
                    naziv: (prod.naziv || '') + (v.naziv ? ' ' + v.naziv : ''),
                    sifra: v.sifra || prod.sifra || '',
                    barkod: prod.barkod || '',
                    cijena: v.cijena || prod.cijena || '',
                    dostupno: Number(v.na_stanju != null ? v.na_stanju : 0) || 0,
                };
            });
            var varQty = varRows.reduce(function (sum, row) { return sum + (row.dostupno || 0); }, 0);
            if (vars.length > 1 && varQty > 0) {
                varRows.forEach(function (row) { rows.push(row); });
            } else if (vars.length === 1 && varQty > 0) {
                rows.push(varRows[0]);
            } else {
                rows.push({
                    item: prod,
                    variation: null,
                    naziv: prod.naziv,
                    sifra: prod.sifra || '',
                    barkod: prod.barkod || '',
                    cijena: prod.cijena || '',
                    dostupno: parentQty,
                });
            }
        });
        return rows;
    }
    function pickBarcode(rows, query) {
        var hit = rows.filter(function (row) { return isBarcodeHit(row, query); })[0];
        if (hit && !isPrefixOfLongerBarcode(query, rows, hit)) return hit;
        if (looksLikeBarcode(query) && rows.length === 1) return rows[0];
        return null;
    }
    function openQty(row) {
        if (!row) return;
        pick = { item: row.item, variation: row.variation };
        if (list) list.hidden = true;
        if (qtyName) qtyName.textContent = row.naziv || 'Artikal';
        if (qtyMeta) {
            qtyMeta.textContent = [
                row.sifra,
                row.cijena ? row.cijena + ' KM' : '',
                (row.dostupno || 0) + ' na stanju',
            ].filter(Boolean).join(' · ');
        }
        if (qtyInput) qtyInput.value = '1';
        if (qtyModal) qtyModal.hidden = false;
        showHint('');
        window.setTimeout(function () {
            if (!qtyInput) return;
            qtyInput.focus();
            qtyInput.select();
        }, 40);
    }
    function commitPick() {
        if (!pick) return;
        var qty = parseInt(qtyInput && qtyInput.value, 10) || 0;
        if (qty < 1) {
            showHint('Unesi količinu.');
            if (qtyInput) qtyInput.focus();
            return;
        }
        var available = availableOf(pick.item, pick.variation);
        var existing = findRow(String(pick.item.id), pick.variation ? String(pick.variation.id) : '');
        var already = existing ? (parseInt(existing.querySelector('[name="kolicina"]').value, 10) || 0) : 0;
        var over = already + qty > available;
        var existingMp = existing && rowIsMp(existing);
        if (over && !existingMp) {
            askMp({
                type: 'add',
                item: pick.item,
                variation: pick.variation,
                qty: qty,
                available: available,
                name: pickName(pick.item, pick.variation),
            });
            pick = null;
            closeQty();
            if (search) search.value = '';
            if (list) list.hidden = true;
            return;
        }
        addLine(pick.item, pick.variation, existingMp, qty);
        if (catalogOpen()) {
            renderCatalog(lastResults);
            pick = null;
            if (qtyInput) qtyInput.value = '1';
            closeQty();
            return;
        }
        resetPicker();
    }
    function setCatalog(on) {
        if (!catalog || !catalogBtn) return;
        catalog.hidden = !on;
        catalogBtn.classList.toggle('is-on', on);
        catalogBtn.setAttribute('aria-pressed', on ? 'true' : 'false');
        if (list) list.hidden = true;
        if (on) {
            if ((search && search.value || '').trim()) searchProducts({});
            else renderCatalog([]);
        }
    }
    function renderCatalog(rows) {
        if (!catalog) return;
        catalog.innerHTML = '';
        if (!rows || !rows.length) {
            catalog.innerHTML = '<p class="mg-empty">Ukucaj naziv, šifru ili barkod — izlistaće se artikli.</p>';
            return;
        }
        rows.forEach(function (row) {
            var pid = String(row.item.id);
            var vid = row.variation ? String(row.variation.id) : '';
            var qty = lineQty(pid, vid);
            var card = document.createElement('article');
            card.className = 'mg-catalog-item' + (qty ? ' is-added' : '') + ((row.dostupno || 0) <= 0 ? ' is-out' : '');
            card.innerHTML =
                '<strong></strong>' +
                (row.variation && row.variation.naziv ? '<span class="sub"></span>' : '') +
                '<p class="mg-catalog-meta"></p>' +
                '<div class="mg-catalog-qty">' +
                '<button type="button" class="mg-btn" data-cat-minus aria-label="Manje">−</button>' +
                '<em data-cat-qty></em>' +
                '<button type="button" class="mg-btn" data-cat-plus aria-label="Više">+</button>' +
                '</div>';
            card.querySelector('strong').textContent = row.item.naziv || row.naziv || '';
            var sub = card.querySelector('.sub');
            if (sub) sub.textContent = row.variation.naziv;
            card.querySelector('.mg-catalog-meta').textContent = [
                row.sifra,
                row.cijena ? row.cijena + ' KM' : '',
                (row.dostupno || 0) + ' na stanju',
            ].filter(Boolean).join(' · ');
            card.querySelector('[data-cat-qty]').textContent = String(qty);
            var minus = card.querySelector('[data-cat-minus]');
            minus.disabled = qty <= 0;
            minus.addEventListener('click', function (event) {
                event.preventDefault();
                shiftCatalogQty(row, -1);
            });
            card.querySelector('[data-cat-plus]').addEventListener('click', function (event) {
                event.preventDefault();
                shiftCatalogQty(row, 1);
            });
            catalog.appendChild(card);
        });
    }
    function shiftCatalogQty(row, delta) {
        var pid = String(row.item.id);
        var vid = row.variation ? String(row.variation.id) : '';
        var current = lineQty(pid, vid);
        var next = current + delta;
        if (next < 0) next = 0;
        if (delta > 0) {
            pick = { item: row.item, variation: row.variation };
            if (qtyInput) qtyInput.value = '1';
            commitPick();
            return;
        }
        var existing = findRow(pid, vid);
        if (!existing) {
            renderCatalog(lastResults);
            return;
        }
        if (next <= 0) existing.remove();
        else {
            var field = existing.querySelector('[name="kolicina"]');
            field.value = String(next);
            field.setAttribute('data-prev', String(next));
        }
        refreshTotal();
        renderCatalog(lastResults);
    }
    function renderSuggest(items) {
        lastResults = items || [];
        if (!list) return;
        list.innerHTML = '';
        if (!lastResults.length) {
            list.innerHTML = '<li class="is-empty">Nema rezultata.</li>';
            list.hidden = false;
            return;
        }
        lastResults.forEach(function (row) {
            var li = document.createElement('li');
            li.innerHTML = '<span class="vp-name"></span><span></span>';
            li.querySelector('.vp-name').textContent = row.naziv || '';
            li.querySelector('span:last-child').textContent =
                [row.sifra, row.cijena ? row.cijena + ' KM' : '', (row.dostupno || 0) + ' na stanju'].filter(Boolean).join(' · ');
            li.addEventListener('mousedown', function (event) {
                event.preventDefault();
                openQty(row);
            });
            list.appendChild(li);
        });
        list.hidden = false;
    }
    function searchProducts(opts) {
        opts = opts || {};
        var q = (search && search.value || '').trim();
        if (q.length < 1) {
            if (list) list.hidden = true;
            lastResults = [];
            if (catalogOpen()) renderCatalog([]);
            return Promise.resolve(null);
        }
        var limit = catalogOpen() ? 80 : 20;
        return fetch(lookupUrl + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1&limit=' + limit, {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        }).then(function (res) { return res.json(); }).then(function (data) {
            var rows = flatten(data);
            lastResults = rows;
            var hit = pickBarcode(rows, q);
            if (hit && (opts.fromScan || looksLikeBarcode(q) || isBarcodeHit(hit, q))) {
                openQty(hit);
                return hit;
            }
            if (opts.takeFirst && rows.length && !catalogOpen()) {
                openQty(rows[0]);
                return rows[0];
            }
            if (catalogOpen()) {
                if (list) list.hidden = true;
                renderCatalog(rows);
                return null;
            }
            renderSuggest(rows);
            return null;
        }).catch(function () {
            if (list) {
                list.innerHTML = '<li class="is-empty">Pretraga nije uspjela.</li>';
                list.hidden = false;
            }
            return null;
        });
    }

    if (search) {
        search.addEventListener('input', function () {
            pick = null;
            showHint('');
            window.clearTimeout(timer);
            timer = window.setTimeout(function () { searchProducts({}); }, 160);
        });
        search.addEventListener('keydown', function (event) {
            if (event.key === 'Tab' && !event.shiftKey) {
                event.preventDefault();
                window.clearTimeout(timer);
                if (lastResults.length) {
                    openQty(lastResults[0]);
                    return;
                }
                searchProducts({ takeFirst: true });
                return;
            }
            if (event.key !== 'Enter') return;
            event.preventDefault();
            window.clearTimeout(timer);
            searchProducts({ fromScan: looksLikeBarcode(search.value) });
        });
        search.addEventListener('mg-scanned', function () {
            window.clearTimeout(timer);
            searchProducts({ fromScan: true });
        });
    }
    if (catalogBtn) {
        catalogBtn.addEventListener('click', function () {
            setCatalog(!catalogOpen());
            if (search) search.focus();
        });
    }
    if (qtyInput) {
        qtyInput.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                event.preventDefault();
                resetPicker();
                return;
            }
            if (event.key === 'Tab' && event.shiftKey) return;
            if (event.key !== 'Enter' && event.key !== 'Tab') return;
            event.preventDefault();
            commitPick();
        });
    }
    if (qtyAddBtn) qtyAddBtn.addEventListener('click', function () { commitPick(); });
    if (qtyModal) {
        qtyModal.querySelectorAll('[data-order-qty-close]').forEach(function (el) {
            el.addEventListener('click', function () {
                closeQty();
                if (search) search.focus();
            });
        });
    }
    document.addEventListener('click', function (event) {
        var box = document.getElementById('mgOrderSearchBox');
        if (box && !box.contains(event.target) && list) list.hidden = true;
    });
    if (body) {
        body.addEventListener('click', function (event) {
            var btn = event.target.closest('[data-remove-line]');
            if (!btn) return;
            var row = btn.closest('tr');
            if (row) row.remove();
            refreshTotal();
            if (catalogOpen()) renderCatalog(lastResults);
        });
        body.addEventListener('change', function (event) {
            var input = event.target.closest('[name="kolicina"]');
            if (!input) return;
            var row = input.closest('tr');
            var qty = parseInt(input.value, 10) || 0;
            var available = parseInt(row.getAttribute('data-available'), 10) || 0;
            var prev = parseInt(input.getAttribute('data-prev') || '1', 10) || 1;
            if (qty > available && !rowIsMp(row)) {
                input.value = prev;
                var nameEl = row.querySelector('strong');
                askMp({
                    type: 'qty',
                    row: row,
                    input: input,
                    next: qty,
                    prev: prev,
                    available: available,
                    name: nameEl ? nameEl.textContent : 'Artikal',
                });
                return;
            }
            if (qty < 1) {
                input.value = prev;
                return;
            }
            input.setAttribute('data-prev', String(qty));
            refreshTotal();
        });
    }
    form.addEventListener('submit', function (event) {
        var submitter = event.submitter;
        if (submitter && submitter.getAttribute('name') === 'action' && submitter.value === 'otkazi') {
            return;
        }
        if (pick) {
            event.preventDefault();
            commitPick();
            return;
        }
        if (!lineCount()) {
            event.preventDefault();
            if (search) search.focus();
        }
    });
    if (mpModal) {
        document.getElementById('mgMpAdd').addEventListener('click', function () {
            if (!pending) {
                hideMp();
                return;
            }
            if (pending.type === 'add') {
                addLine(pending.item, pending.variation, true, pending.qty || 1);
                if (catalogOpen()) renderCatalog(lastResults);
            } else if (pending.type === 'qty' && pending.row && pending.input) {
                pending.input.value = pending.next;
                pending.input.setAttribute('data-prev', String(pending.next));
                pending.row.querySelector('[name="mp_ok"]').value = '1';
                if (!pending.row.querySelector('.mg-pill.is-manual')) {
                    var td = pending.row.querySelector('td');
                    var mark = document.createElement('span');
                    mark.className = 'mg-pill is-manual';
                    mark.textContent = 'Nije popisan';
                    if (td) td.appendChild(mark);
                }
                refreshTotal();
            }
            hideMp();
            if (search) search.focus();
        });
        function skipMp() {
            if (pending && pending.type === 'qty' && pending.input) {
                pending.input.value = pending.prev || 1;
                refreshTotal();
            }
            hideMp();
            if (search) search.focus();
        }
        document.getElementById('mgMpSkip').addEventListener('click', skipMp);
        mpModal.querySelectorAll('[data-mp-skip]').forEach(function (el) {
            el.addEventListener('click', skipMp);
        });
        document.addEventListener('keydown', function (event) {
            if (!mpModal || mpModal.hidden) return;
            if (event.key === 'Tab') {
                event.preventDefault();
                return;
            }
            if (event.key === 'Escape') {
                event.preventDefault();
                skipMp();
            }
        }, true);
    }
    if (discInput) {
        discInput.addEventListener('input', function () {
            var cleaned = String(discInput.value || '').replace(/[^\d]/g, '');
            if (cleaned !== discInput.value) discInput.value = cleaned;
            if (parseInt(cleaned, 10) > 100) discInput.value = '100';
            refreshTotal();
        });
    }
    if (noShipBtn && noShipInput) {
        noShipBtn.addEventListener('click', function () {
            if (payByCard()) return;
            noShipInput.value = shipWaived() ? '' : '1';
            refreshTotal();
        });
    }
    form.querySelectorAll('input[name="placanje"]').forEach(function (el) {
        el.addEventListener('change', refreshTotal);
    });
    var clearBtn = document.getElementById('mgOrderClear');
    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            if (body) body.innerHTML = '';
            refreshTotal();
            var change = document.getElementById('mgCustomerChange');
            if (change) change.click();
        });
    }
    var spareBtn = document.getElementById('mgSpareBtn');
    var spareModal = document.getElementById('mgSpareModal');
    var spareNaziv = document.getElementById('mgSpareNaziv');
    var spareQty = document.getElementById('mgSpareQty');
    var spareCijena = document.getElementById('mgSpareCijena');
    var spareHint = document.getElementById('mgSpareHint');
    var spareAdd = document.getElementById('mgSpareAdd');
    function closeSpare() {
        if (spareModal) spareModal.hidden = true;
        if (spareHint) { spareHint.hidden = true; spareHint.textContent = ''; }
    }
    function openSpare() {
        if (!spareModal) return;
        if (spareNaziv) spareNaziv.value = '';
        if (spareQty) spareQty.value = '1';
        if (spareCijena) spareCijena.value = '';
        if (spareHint) { spareHint.hidden = true; spareHint.textContent = ''; }
        spareModal.hidden = false;
        window.setTimeout(function () { if (spareNaziv) spareNaziv.focus(); }, 40);
    }
    function submitSpare() {
        var naziv = spareNaziv ? spareNaziv.value.trim() : '';
        var qty = spareQty ? spareQty.value : '1';
        var cijena = spareCijena ? spareCijena.value : '';
        if (!naziv) {
            if (spareHint) { spareHint.hidden = false; spareHint.textContent = 'Unesi naziv rezervnog dijela.'; }
            if (spareNaziv) spareNaziv.focus();
            return;
        }
        if (!cijena || parseFloat(String(cijena).replace(',', '.')) < 0 || isNaN(parseFloat(String(cijena).replace(',', '.')))) {
            if (spareHint) { spareHint.hidden = false; spareHint.textContent = 'Unesi naplatu.'; }
            if (spareCijena) spareCijena.focus();
            return;
        }
        addSpareLine(naziv, qty, cijena);
        closeSpare();
    }
    if (spareBtn) spareBtn.addEventListener('click', openSpare);
    if (spareAdd) spareAdd.addEventListener('click', submitSpare);
    if (spareModal) {
        spareModal.querySelectorAll('[data-spare-close]').forEach(function (el) {
            el.addEventListener('click', closeSpare);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && spareModal && !spareModal.hidden) closeSpare();
        });
    }
    [spareNaziv, spareQty, spareCijena].forEach(function (el) {
        if (!el) return;
        el.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            submitSpare();
        });
    });
    refreshTotal();
}

function initOrderBulkPrint() {
    var form = document.getElementById('mgPrintForm');
    if (!form) return;
    var selectAll = document.getElementById('mgSelectAll');
    var printBtn = document.getElementById('mgPrintSelected');
    var qtyPrintBtn = document.getElementById('mgPrintQtySelected');
    var validateBtn = document.getElementById('mgValidateSelected');
    var validateForm = document.getElementById('mgValidateForm');
    var countEl = document.getElementById('mgSelectedCount');
    var checks = function () {
        return Array.prototype.slice.call(form.querySelectorAll('.mg-order-check'));
    };

    function sync() {
        var selected = checks().filter(function (box) { return box.checked; });
        if (printBtn) printBtn.disabled = selected.length === 0;
        if (qtyPrintBtn) qtyPrintBtn.disabled = selected.length === 0;
        if (validateBtn) validateBtn.disabled = selected.length === 0;
        if (countEl) countEl.textContent = selected.length + ' odabrano';
        if (selectAll) {
            var all = checks();
            selectAll.checked = all.length > 0 && selected.length === all.length;
            selectAll.indeterminate = selected.length > 0 && selected.length < all.length;
        }
    }

    if (selectAll) {
        selectAll.addEventListener('change', function () {
            checks().forEach(function (box) { box.checked = selectAll.checked; });
            sync();
        });
    }
    form.addEventListener('change', function (event) {
        if (event.target.classList.contains('mg-order-check')) sync();
    });
    form.addEventListener('submit', function (event) {
        var selected = checks().filter(function (box) { return box.checked; });
        if (!selected.length) {
            event.preventDefault();
            return;
        }
        var locked = selected.find(function (box) {
            return box.getAttribute('data-mp') === '1' ||
                (box.closest('tr') && box.closest('tr').getAttribute('data-mp') === '1');
        });
        if (locked) {
            event.preventDefault();
            var row = locked.closest('tr');
            var url = row && row.getAttribute('data-order-url');
            if (url) window.location.href = url;
        }
    });
    if (validateBtn && validateForm) {
        validateBtn.addEventListener('click', function () {
            var selected = checks().filter(function (box) { return box.checked; });
            if (!selected.length) return;
            if (!window.confirm('Validirati ' + selected.length + ' označen' + (selected.length === 1 ? 'u narudžbu' : 'e narudžbe') + '?')) {
                return;
            }
            validateForm.querySelectorAll('input[name="b"]').forEach(function (el) { el.remove(); });
            selected.forEach(function (box) {
                var hidden = document.createElement('input');
                hidden.type = 'hidden';
                hidden.name = 'b';
                hidden.value = box.value;
                validateForm.appendChild(hidden);
            });
            validateForm.submit();
        });
    }
    var packingBtn = document.getElementById('mgPackingSelected');
    if (packingBtn) {
        packingBtn.addEventListener('click', function (event) {
            var count = parseInt(packingBtn.getAttribute('data-packing-count') || '0', 10) || 0;
            if (count > 0) {
                var msg = count === 1
                    ? 'Štampati packing za 1 narudžbu?'
                    : ('Štampati packing za ' + count + ' narudžbe?');
                if (!window.confirm(msg)) event.preventDefault();
                return;
            }
            event.preventDefault();
            var password = window.prompt(
                'Packing je već odštampan. Lozinka za reprint — zatim biraš datum i koje pošiljke da štampaš:'
            );
            if (password === null) return;
            if (String(password).trim() !== 'admin') {
                window.alert('Pogrešna lozinka.');
                return;
            }
            var reprintForm = document.getElementById('mgPackingReprintForm');
            if (!reprintForm) return;
            var field = reprintForm.querySelector('input[name="lozinka"]');
            if (field) field.value = String(password).trim();
            reprintForm.submit();
            if (field) field.value = '';
        });
    }
    form.querySelectorAll('[data-stop-row]').forEach(function (cell) {
        cell.addEventListener('click', function (event) { event.stopPropagation(); });
    });
    form.querySelectorAll('tr[data-order-url]').forEach(function (row) {
        row.addEventListener('click', function () {
            window.location.href = row.getAttribute('data-order-url');
        });
    });
    sync();
}

function initTransferPage() {
    var insertPanel = document.getElementById('mgInsertPanel');
    var movePanel = document.getElementById('mgMovePanel');
    if (!insertPanel && !movePanel) return;

    function escapeHtml(value) {
        return String(value || '')
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function bindSuggest(input, list, fetchItems, onPick, minLen) {
        minLen = minLen == null ? 1 : minLen;
        var timer = null;
        var last = [];

        function firstItem() {
            return list ? list.querySelector('li[data-pick]:not([hidden])') : null;
        }

        function render(items) {
            last = items || [];
            if (!list) return;
            list.innerHTML = '';
            if (!last.length) {
                list.innerHTML = '<li class="is-empty">Nema rezultata.</li>';
                list.hidden = false;
                return;
            }
            last.forEach(function (item, index) {
                var li = document.createElement('li');
                li.setAttribute('data-pick', '1');
                li.setAttribute('data-index', String(index));
                li.innerHTML = item.html || ('<strong>' + escapeHtml(item.label) + '</strong>');
                li.addEventListener('mousedown', function (event) {
                    event.preventDefault();
                    onPick(item);
                    list.hidden = true;
                });
                list.appendChild(li);
            });
            list.hidden = false;
            var first = firstItem();
            if (first) first.classList.add('is-active');
        }

        function search() {
            var q = (input.value || '').trim();
            if (q.length < minLen) {
                list.hidden = true;
                return;
            }
            Promise.resolve(fetchItems(q)).then(render).catch(function () {
                list.innerHTML = '<li class="is-empty">Pretraga nije uspjela.</li>';
                list.hidden = false;
            });
        }

        input.addEventListener('input', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(search, 160);
        });
        input.addEventListener('focus', function () {
            if ((input.value || '').trim().length >= minLen && last.length) render(last);
            else if ((input.value || '').trim().length >= minLen) search();
            else if (minLen === 0) search();
        });
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape') {
                list.hidden = true;
                return;
            }
            if (event.key === 'Tab' || event.key === 'Enter') {
                var first = firstItem();
                if (first) {
                    event.preventDefault();
                    var idx = parseInt(first.getAttribute('data-index'), 10) || 0;
                    onPick(last[idx]);
                    list.hidden = true;
                }
            }
        });
        document.addEventListener('click', function (event) {
            if (!input.contains(event.target) && !list.contains(event.target)) list.hidden = true;
        });
        return { search: search, fetchItems: fetchItems, onPick: onPick };
    }

    function bindBarcodePick(input, fetchItems, onPick) {
        if (!input) return;
        input.addEventListener('mg-scanned', function (event) {
            var q = String((event.detail && event.detail.code) || input.value || '').trim();
            if (!q) return;
            Promise.resolve(fetchItems(q)).then(function (items) {
                if (items && items.length) onPick(items[0]);
            });
        });
    }

    function fetchJson(url) {
        return fetch(url, { headers: { 'X-Requested-With': 'XMLHttpRequest' } }).then(function (res) {
            return res.json();
        });
    }

    function locItem(row) {
        var extra = row.kolicina ? (' · ' + row.kolicina + ' kom') : '';
        return {
            id: row.id,
            label: row.label,
            kolicina: row.kolicina || 0,
            dostupno: row.dostupno || 0,
            html: '<strong>' + escapeHtml(row.label) + '</strong><span>' + escapeHtml(row.sifra || '') + extra + '</span>',
        };
    }

    function productItems(results) {
        var items = [];
        (results || []).forEach(function (item) {
            var vars = item.varijacije || [];
            var varQty = (vars || []).reduce(function (sum, v) {
                return sum + (Number(v.na_stanju != null ? v.na_stanju : 0) || 0);
            }, 0);
            if (vars.length > 1 && varQty > 0) {
                vars.forEach(function (variation) {
                    items.push({
                        id: item.id,
                        vid: variation.id,
                        naziv: item.naziv,
                        varNaziv: variation.naziv,
                        sifra: variation.sifra || item.sifra || '',
                        label: item.naziv + ' — ' + variation.naziv,
                        html:
                            '<strong>' + escapeHtml(item.naziv) + ' — ' + escapeHtml(variation.naziv) + '</strong>' +
                            '<span>' + escapeHtml(variation.sifra || item.sifra || '') + '</span>',
                    });
                });
            } else {
                items.push({
                    id: item.id,
                    vid: (vars.length === 1 && varQty > 0) ? vars[0].id : '',
                    naziv: item.naziv,
                    varNaziv: '',
                    sifra: item.sifra || '',
                    label: item.naziv,
                    html:
                        '<strong>' + escapeHtml(item.naziv) + '</strong>' +
                        '<span>' + escapeHtml(item.sifra || '') + '</span>',
                });
            }
        });
        return items;
    }

    if (insertPanel) {
        var locUrl = insertPanel.getAttribute('data-loc-lookup-url') || '';
        var artUrl = insertPanel.getAttribute('data-lookup-url') || '';
        var locId = document.getElementById('mgInsertLocId');
        var locSearch = document.getElementById('mgInsertLocSearch');
        var locSuggest = document.getElementById('mgInsertLocSuggest');
        var locWrap = document.getElementById('mgInsertLocSearchWrap');
        var locLocked = document.getElementById('mgInsertLocLocked');
        var locLabel = document.getElementById('mgInsertLocLabel');
        var locChange = document.getElementById('mgInsertLocChange');
        var artWrap = document.getElementById('mgInsertArtWrap');
        var artSearch = document.getElementById('mgInsertSearch');
        var artSuggest = document.getElementById('mgInsertSuggest');
        var artQty = document.getElementById('mgInsertQty');
        var artPickLabel = document.getElementById('mgInsertPickLabel');
        var lines = document.getElementById('mgInsertLines');
        var table = document.getElementById('mgInsertTable');
        var empty = document.getElementById('mgInsertEmpty');
        var submit = document.getElementById('mgInsertSubmit');
        var form = document.getElementById('mgInsertForm');
        var pending = null;

        function lockLocation(item) {
            locId.value = item.id;
            locLabel.textContent = item.label;
            locWrap.hidden = true;
            locLocked.hidden = false;
            artWrap.hidden = false;
            empty.hidden = false;
            if (artSearch) artSearch.focus();
        }

        function unlockLocation() {
            locId.value = '';
            locSearch.value = '';
            locWrap.hidden = false;
            locLocked.hidden = true;
            artWrap.hidden = true;
            table.hidden = true;
            empty.hidden = true;
            if (lines) lines.innerHTML = '';
            syncInsert();
            locSearch.focus();
        }

        function syncInsert() {
            var count = lines ? lines.querySelectorAll('tr[data-line]').length : 0;
            if (table) table.hidden = count === 0;
            if (empty) empty.hidden = !locId.value || count > 0;
            if (submit) submit.disabled = !locId.value || count === 0;
        }

        function addInsertLine(item, qty) {
            qty = parseInt(qty, 10) || 1;
            if (qty < 1) qty = 1;
            var existing = lines.querySelector('tr[data-pid="' + item.id + '"][data-vid="' + (item.vid || '') + '"]');
            if (existing) {
                var input = existing.querySelector('[name="kolicina"]');
                input.value = (parseInt(input.value, 10) || 0) + qty;
                return;
            }
            var tr = document.createElement('tr');
            tr.setAttribute('data-line', '1');
            tr.setAttribute('data-pid', item.id);
            tr.setAttribute('data-vid', item.vid || '');
            tr.innerHTML =
                '<td><strong>' + escapeHtml(item.naziv) + '</strong>' +
                (item.varNaziv ? '<span class="sub">' + escapeHtml(item.varNaziv) + '</span>' : '') +
                '<input type="hidden" name="product_id" value="' + item.id + '">' +
                '<input type="hidden" name="variation_id" value="' + escapeHtml(item.vid || '') + '"></td>' +
                '<td>' + escapeHtml(item.sifra || '—') + '</td>' +
                '<td class="num"><input class="mg-qty-input" name="kolicina" type="number" min="1" step="1" value="' + qty + '" required></td>' +
                '<td class="num"><button type="button" class="mg-btn mg-btn-danger" data-remove-line>Ukloni</button></td>';
            lines.appendChild(tr);
        }

        function fetchLocs(q) {
            return fetchJson(locUrl + '?q=' + encodeURIComponent(q)).then(function (data) {
                return (data.results || []).map(locItem);
            });
        }
        function fetchArts(q) {
            return fetchJson(artUrl + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1').then(function (data) {
                return productItems(data.results || []);
            });
        }
        function pickInsertArticle(item) {
            pending = item;
            artSuggest.hidden = true;
            artSearch.value = item.label;
            if (artPickLabel) {
                artPickLabel.hidden = false;
                artPickLabel.textContent = item.label;
            }
            artQty.hidden = false;
            artQty.value = '1';
            artQty.focus();
            artQty.select();
        }
        bindSuggest(locSearch, locSuggest, fetchLocs, lockLocation, 1);
        bindBarcodePick(locSearch, fetchLocs, lockLocation);
        bindSuggest(artSearch, artSuggest, fetchArts, pickInsertArticle, 2);
        bindBarcodePick(artSearch, fetchArts, pickInsertArticle);

        if (artQty) {
            artQty.addEventListener('keydown', function (event) {
                if (event.key === 'Tab' || event.key === 'Enter') {
                    event.preventDefault();
                    if (!pending) return;
                    addInsertLine(pending, artQty.value);
                    pending = null;
                    artQty.hidden = true;
                    artQty.value = '';
                    artSearch.value = '';
                    if (artPickLabel) artPickLabel.hidden = true;
                    syncInsert();
                    artSearch.focus();
                }
            });
        }
        if (lines) {
            lines.addEventListener('click', function (event) {
                var btn = event.target.closest('[data-remove-line]');
                if (!btn) return;
                var row = btn.closest('tr');
                if (row) row.remove();
                syncInsert();
            });
        }
        if (locChange) locChange.addEventListener('click', unlockLocation);
        if (form) {
            form.addEventListener('submit', function (event) {
                if (pending) {
                    event.preventDefault();
                    addInsertLine(pending, artQty.value);
                    pending = null;
                    syncInsert();
                    return;
                }
                if (!locId.value || !lines.querySelector('tr[data-line]')) {
                    event.preventDefault();
                }
            });
        }
        syncInsert();
    }

    if (movePanel) {
        var locUrl2 = movePanel.getAttribute('data-loc-lookup-url') || '';
        var artUrl2 = movePanel.getAttribute('data-lookup-url') || '';
        var pid = document.getElementById('mgMovePid');
        var vid = document.getElementById('mgMoveVid');
        var fromId = document.getElementById('mgMoveFromId');
        var toId = document.getElementById('mgMoveToId');
        var search = document.getElementById('mgMoveSearch');
        var suggest = document.getElementById('mgMoveSuggest');
        var pickLabel = document.getElementById('mgMovePickLabel');
        var fromWrap = document.getElementById('mgMoveFromWrap');
        var fromList = document.getElementById('mgMoveFromList');
        var fromEmpty = document.getElementById('mgMoveFromEmpty');
        var qtyWrap = document.getElementById('mgMoveQtyWrap');
        var qty = document.getElementById('mgMoveQty');
        var toWrap = document.getElementById('mgMoveToWrap');
        var toSearch = document.getElementById('mgMoveToSearch');
        var toSuggest = document.getElementById('mgMoveToSuggest');
        var submit = document.getElementById('mgMoveSubmit');
        var form = document.getElementById('mgMoveForm');
        var fromQty = 0;

        function syncMove() {
            if (submit) submit.disabled = !(pid.value && fromId.value && toId.value && (parseInt(qty.value, 10) || 0) > 0);
        }

        function pickFrom(item) {
            fromId.value = item.id;
            fromQty = parseInt(item.kolicina, 10) || 0;
            if (fromList) {
                fromList.querySelectorAll('[data-from-loc]').forEach(function (btn) {
                    btn.classList.toggle('is-active', btn.getAttribute('data-from-loc') === String(item.id));
                });
            }
            if (qty) {
                qty.max = fromQty || '';
                if (!qty.value || parseInt(qty.value, 10) > fromQty) qty.value = fromQty > 0 ? '1' : '';
            }
            qtyWrap.hidden = fromQty <= 0;
            toWrap.hidden = fromQty <= 0;
            syncMove();
            if (fromQty > 0 && qty) {
                qty.focus();
                qty.select();
            }
        }

        function renderFromLocs(rows) {
            if (!fromList) return;
            fromList.innerHTML = '';
            var items = (rows || []).filter(function (row) { return (row.kolicina || 0) > 0; });
            if (fromEmpty) fromEmpty.hidden = items.length > 0;
            items.forEach(function (row) {
                var btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'mg-from-loc';
                btn.setAttribute('data-from-loc', String(row.id));
                btn.innerHTML =
                    '<strong>' + escapeHtml(row.label) + '</strong>' +
                    '<span>' + escapeHtml(String(row.kolicina)) + ' kom</span>';
                btn.addEventListener('click', function () { pickFrom(row); });
                fromList.appendChild(btn);
            });
        }

        function loadFromLocs() {
            var url = locUrl2 + '?sa_zalihom=1&product_id=' + encodeURIComponent(pid.value);
            if (vid.value) url += '&variation_id=' + encodeURIComponent(vid.value);
            return fetchJson(url).then(function (data) {
                renderFromLocs(data.results || []);
            }).catch(function () {
                renderFromLocs([]);
            });
        }

        function pickArticle(item) {
            pid.value = item.id;
            vid.value = item.vid || '';
            search.value = item.label;
            if (pickLabel) {
                pickLabel.hidden = false;
                pickLabel.textContent = item.label;
            }
            fromId.value = '';
            toId.value = '';
            toSearch.value = '';
            if (qty) qty.value = '';
            fromWrap.hidden = false;
            qtyWrap.hidden = true;
            toWrap.hidden = true;
            if (fromList) fromList.innerHTML = '';
            if (fromEmpty) fromEmpty.hidden = true;
            syncMove();
            loadFromLocs();
        }

        function pickTo(item) {
            toId.value = item.id;
            toSearch.value = item.label;
            syncMove();
        }

        function fetchMoveArts(q) {
            return fetchJson(artUrl2 + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1').then(function (data) {
                return productItems(data.results || []);
            });
        }
        function fetchMoveLocs(q) {
            return fetchJson(locUrl2 + '?q=' + encodeURIComponent(q)).then(function (data) {
                return (data.results || []).map(locItem);
            });
        }
        bindSuggest(search, suggest, fetchMoveArts, pickArticle, 2);
        bindBarcodePick(search, fetchMoveArts, pickArticle);
        bindSuggest(toSearch, toSuggest, fetchMoveLocs, pickTo, 1);
        bindBarcodePick(toSearch, fetchMoveLocs, pickTo);

        if (qty) qty.addEventListener('input', syncMove);
        if (form) {
            form.addEventListener('submit', function (event) {
                if (submit && submit.disabled) event.preventDefault();
            });
        }
        syncMove();
    }
}

function initArticleScanner() {
    var openBtn = document.getElementById('mgArticleScanBtn');
    var extraBtns = document.querySelectorAll('[data-mg-scan-target]');
    var modal = document.getElementById('mgScanModal');
    var statusEl = document.getElementById('mgScanStatus');
    var fileInput = document.getElementById('mgBarcodeFile');
    var searchInput = document.getElementById('mgArticleSearch');
    var searchForm = document.getElementById('mgArticleSearchForm');
    var video = document.getElementById('mgScanVideo');
    if ((!openBtn && !extraBtns.length) || !modal || !video) return;
    var targetInput = searchInput;
    var submitAfterScan = true;

    var stream = null;
    var running = false;
    var handled = false;
    var loopTimer = 0;
    var detector = null;
    var zxing = null;
    var zxingReader = null;
    var zxingHints = null;
    var fullCanvas = document.createElement('canvas');
    var bandCanvas = document.createElement('canvas');
    var ONED = ['ean_13', 'ean_8', 'code_128', 'code_39', 'upc_a', 'upc_e', 'itf', 'codabar'];

    function setStatus(msg) {
        if (statusEl) statusEl.textContent = msg || '';
    }

    function applyCode(code) {
        var value = String(code || '').replace(/[\r\n\t]+/g, '').trim();
        if (!value || handled) return;
        handled = true;
        if (targetInput) {
            targetInput.value = value;
            targetInput.dispatchEvent(new Event('input', { bubbles: true }));
            targetInput.dispatchEvent(new CustomEvent('mg-scanned', { bubbles: true, detail: { code: value } }));
            targetInput.focus();
        }
        setStatus('Skenirano: ' + value);
        stopScanner();
        modal.hidden = true;
        if (submitAfterScan && searchForm && targetInput === searchInput) searchForm.submit();
    }

    function stopScanner() {
        running = false;
        if (loopTimer) {
            window.clearTimeout(loopTimer);
            loopTimer = 0;
        }
        if (stream) {
            stream.getTracks().forEach(function (track) { track.stop(); });
            stream = null;
        }
        video.srcObject = null;
    }

    function loadScript(src) {
        return new Promise(function (resolve, reject) {
            var s = document.createElement('script');
            s.src = src;
            s.async = true;
            s.onload = function () { resolve(); };
            s.onerror = function () { reject(new Error('Učitavanje čitača nije uspjelo.')); };
            document.head.appendChild(s);
        });
    }

    function loadZXing() {
        if (window.ZXing && window.ZXing.BrowserMultiFormatReader) {
            return Promise.resolve(window.ZXing);
        }
        return loadScript('https://cdn.jsdelivr.net/npm/@zxing/library@0.21.3/umd/index.min.js').catch(function () {
            return loadScript('https://unpkg.com/@zxing/library@0.21.3/umd/index.min.js');
        }).then(function () {
            if (!window.ZXing || !window.ZXing.BrowserMultiFormatReader) {
                throw new Error('Čitač barkoda nije dostupan.');
            }
            return window.ZXing;
        });
    }

    function setupZXing(ZXingLib) {
        zxing = ZXingLib;
        try {
            zxingHints = new Map();
            zxingHints.set(ZXingLib.DecodeHintType.TRY_HARDER, true);
            if (ZXingLib.DecodeHintType.ALSO_INVERTED != null) {
                zxingHints.set(ZXingLib.DecodeHintType.ALSO_INVERTED, true);
            }
            zxingHints.set(ZXingLib.DecodeHintType.POSSIBLE_FORMATS, [
                ZXingLib.BarcodeFormat.EAN_13,
                ZXingLib.BarcodeFormat.EAN_8,
                ZXingLib.BarcodeFormat.CODE_128,
                ZXingLib.BarcodeFormat.CODE_39,
                ZXingLib.BarcodeFormat.UPC_A,
                ZXingLib.BarcodeFormat.UPC_E,
                ZXingLib.BarcodeFormat.ITF,
                ZXingLib.BarcodeFormat.CODABAR,
                ZXingLib.BarcodeFormat.QR_CODE,
            ]);
        } catch (e) {
            zxingHints = undefined;
        }
        zxingReader = new ZXingLib.BrowserMultiFormatReader(zxingHints, 50);
    }

    function prepareDetector() {
        if (!('BarcodeDetector' in window)) return Promise.resolve(null);
        var supportedP = window.BarcodeDetector.getSupportedFormats
            ? window.BarcodeDetector.getSupportedFormats()
            : Promise.resolve(ONED);
        return supportedP.then(function (supported) {
            var has1d = ONED.some(function (fmt) { return supported.indexOf(fmt) !== -1; });
            if (!has1d) return null;
            var formats = ONED.filter(function (fmt) { return supported.indexOf(fmt) !== -1; });
            formats.push('qr_code');
            try { return new window.BarcodeDetector({ formats: formats }); }
            catch (e) { return null; }
        }).catch(function () { return null; });
    }

    function drawBand() {
        var w = video.videoWidth;
        var h = video.videoHeight;
        if (!w || !h) return null;
        var bandH = Math.max(80, Math.floor(h * 0.38));
        var y = Math.floor((h - bandH) / 2);
        bandCanvas.width = w;
        bandCanvas.height = bandH;
        bandCanvas.getContext('2d', { willReadFrequently: true }).drawImage(
            video, 0, y, w, bandH, 0, 0, w, bandH
        );
        return bandCanvas;
    }

    function drawFull() {
        var w = video.videoWidth;
        var h = video.videoHeight;
        if (!w || !h) return null;
        var scale = w > 1280 ? 1280 / w : 1;
        fullCanvas.width = Math.floor(w * scale);
        fullCanvas.height = Math.floor(h * scale);
        fullCanvas.getContext('2d', { willReadFrequently: true }).drawImage(
            video, 0, 0, fullCanvas.width, fullCanvas.height
        );
        return fullCanvas;
    }

    function decodeZxingCanvas(canvas) {
        if (!canvas || !zxingReader) return Promise.resolve(null);
        if (zxingReader.decodeFromCanvas) {
            return zxingReader.decodeFromCanvas(canvas).then(function (result) {
                return result && result.getText ? result.getText() : null;
            }).catch(function () { return null; });
        }
        try {
            var source = new zxing.HTMLCanvasElementLuminanceSource(canvas);
            var bitmap = new zxing.BinaryBitmap(new zxing.HybridBinarizer(source));
            var reader = new zxing.MultiFormatReader();
            if (zxingHints) reader.setHints(zxingHints);
            var result = reader.decode(bitmap);
            return Promise.resolve(result && result.getText ? result.getText() : null);
        } catch (e) {
            return Promise.resolve(null);
        }
    }

    function decodeDetectorCanvas(canvas) {
        if (!canvas || !detector) return Promise.resolve(null);
        return detector.detect(canvas).then(function (codes) {
            return codes && codes[0] && codes[0].rawValue ? codes[0].rawValue : null;
        }).catch(function () { return null; });
    }

    function scanTick() {
        if (!running) return;
        if (video.readyState < 2) {
            loopTimer = window.setTimeout(scanTick, 50);
            return;
        }
        var band = drawBand();
        var full = drawFull();
        Promise.all([
            decodeZxingCanvas(band),
            decodeDetectorCanvas(band),
            decodeZxingCanvas(full),
            decodeDetectorCanvas(full),
        ]).then(function (found) {
            var code = found.filter(Boolean)[0];
            if (code) {
                applyCode(code);
                return;
            }
            if (running) loopTimer = window.setTimeout(scanTick, 60);
        }).catch(function () {
            if (running) loopTimer = window.setTimeout(scanTick, 80);
        });
    }

    function pickBackCamera() {
        return navigator.mediaDevices.enumerateDevices().then(function (devices) {
            var cams = devices.filter(function (d) { return d.kind === 'videoinput'; });
            var back = cams.find(function (d) { return /back|rear|environment/i.test(d.label || ''); });
            return back ? { deviceId: { exact: back.deviceId } } : { facingMode: { ideal: 'environment' } };
        }).catch(function () {
            return { facingMode: { ideal: 'environment' } };
        });
    }

    function startCamera() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            setStatus('Preglednik ne podržava kameru. Koristi HTTPS ili učitaj sliku.');
            return;
        }
        handled = false;
        setStatus('Pokrećem kameru i čitač…');
        Promise.all([
            pickBackCamera(),
            loadZXing(),
            prepareDetector(),
        ]).then(function (parts) {
            detector = parts[2];
            setupZXing(parts[1]);
            var videoSource = parts[0];
            return navigator.mediaDevices.getUserMedia({
                audio: false,
                video: Object.assign({
                    width: { ideal: 1280 },
                    height: { ideal: 720 },
                    focusMode: 'continuous',
                }, videoSource),
            }).catch(function () {
                return navigator.mediaDevices.getUserMedia({
                    audio: false,
                    video: { facingMode: { ideal: 'environment' } },
                });
            });
        }).then(function (media) {
            stream = media;
            video.srcObject = stream;
            video.setAttribute('playsinline', 'true');
            video.muted = true;
            return video.play();
        }).then(function () {
            running = true;
            setStatus('Drži barkod vodoravno u bijelom okviru, 10–20 cm od kamere.');
            scanTick();
        }).catch(function (err) {
            setStatus((err && err.message) || 'Kamera nije dostupna. Dozvoli pristup ili učitaj sliku.');
        });
    }

    function decodeFile(file) {
        setStatus('Čitam sliku…');
        Promise.all([loadZXing(), prepareDetector()]).then(function (parts) {
            setupZXing(parts[0]);
            detector = parts[1];
            return createImageBitmap(file);
        }).then(function (bitmap) {
            fullCanvas.width = bitmap.width;
            fullCanvas.height = bitmap.height;
            fullCanvas.getContext('2d', { willReadFrequently: true }).drawImage(bitmap, 0, 0);
            return Promise.all([
                decodeZxingCanvas(fullCanvas),
                decodeDetectorCanvas(fullCanvas),
            ]);
        }).then(function (found) {
            var code = found.filter(Boolean)[0];
            if (code) applyCode(code);
            else setStatus('Barkod nije pročitan sa slike. Probaj bliže i ravnije, uz više svjetla.');
        }).catch(function () {
            setStatus('Barkod nije pročitan sa slike.');
        });
    }

    function openScanner(input, shouldSubmit) {
        targetInput = input || searchInput;
        submitAfterScan = !!shouldSubmit;
        handled = false;
        modal.hidden = false;
        window.setTimeout(startCamera, 80);
    }

    if (openBtn) {
        openBtn.addEventListener('click', function () {
            openScanner(searchInput, true);
        });
    }
    extraBtns.forEach(function (btn) {
        btn.addEventListener('click', function () {
            var input = document.getElementById(btn.getAttribute('data-mg-scan-target') || '');
            if (!input) return;
            var title = btn.getAttribute('data-mg-scan-title') || '';
            var titleEl = document.getElementById('mgScanTitle');
            if (title && titleEl) titleEl.textContent = title;
            openScanner(input, false);
        });
    });
    modal.querySelectorAll('[data-scan-close]').forEach(function (el) {
        el.addEventListener('click', function () {
            stopScanner();
            modal.hidden = true;
            setStatus('');
        });
    });
    if (fileInput) {
        fileInput.addEventListener('change', function () {
            var file = fileInput.files && fileInput.files[0];
            fileInput.value = '';
            if (file) decodeFile(file);
        });
    }
}

(function initPickList() {
    var root = document.getElementById('pkListApp');
    if (!root) return;
    var input = document.getElementById('pkListSearch');
    var scanBtn = document.getElementById('pkOrderScan');
    var scanUrl = (input && input.getAttribute('data-scan-url')) || '';

    function openFromScan(raw) {
        var q = (raw || '').trim();
        if (!q || !scanUrl) return false;
        window.location.href = scanUrl + '?q=' + encodeURIComponent(q);
        return true;
    }
    if (input) {
        input.addEventListener('mg-scanned', function (event) {
            openFromScan((event.detail && event.detail.code) || input.value);
        });
        input.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            openFromScan(input.value);
        });
        if (root.classList.contains('pk-scan-only')) input.focus();
    }
    var numForm = document.getElementById('pkOpenNum');
    var numInput = document.getElementById('pkOrderNo');
    function keepHashPrefix() {
        if (!numInput) return;
        var digits = String(numInput.value || '').replace(/\D/g, '');
        numInput.value = '#' + digits;
    }
    if (numInput) {
        keepHashPrefix();
        numInput.addEventListener('input', keepHashPrefix);
        numInput.addEventListener('focus', function () {
            keepHashPrefix();
            try { numInput.setSelectionRange(numInput.value.length, numInput.value.length); } catch (e) {}
        });
    }
    if (numForm) {
        numForm.addEventListener('submit', function (event) {
            keepHashPrefix();
            var digits = String(numInput && numInput.value || '').replace(/\D/g, '');
            if (!digits) {
                event.preventDefault();
                if (numInput) numInput.focus();
                return;
            }
            if (numInput) numInput.value = '#' + digits;
        });
    }
})();

(function initPickEngine() {
    var root = document.getElementById('pkPickApp');
    if (!root) return;
    var raw = document.getElementById('pkQueue');
    var queue = [];
    try { queue = JSON.parse(raw ? raw.textContent : '[]') || []; } catch (err) { queue = []; }
    var broj = root.getAttribute('data-broj') || '';
    var storageKey = 'mg-pick-' + broj;
    var localState = {};
    var serverState = {};
    try { localState = JSON.parse(window.localStorage.getItem(storageKey) || '{}') || {}; } catch (err) { localState = {}; }
    try { serverState = JSON.parse((document.getElementById('pkState') || {}).textContent || '{}') || {}; } catch (err) { serverState = {}; }

    function pickScore(row) {
        if (!row || typeof row !== 'object') return 0;
        return (row.done ? 100000 : 0) + (parseInt(row.got, 10) || 0);
    }
    function mergePickState(local, server) {
        var out = {};
        Object.keys(local || {}).forEach(function (key) { out[key] = local[key]; });
        Object.keys(server || {}).forEach(function (key) {
            if (pickScore(server[key]) >= pickScore(out[key])) out[key] = server[key];
        });
        return out;
    }
    function findSaved(item, bag) {
        if (!item) return null;
        if (bag[item.key]) return bag[item.key];
        var found = null;
        Object.keys(bag).forEach(function (key) {
            var row = bag[key];
            if (!row || typeof row !== 'object') return;
            if (item.item_id && String(row.item_id) === String(item.item_id)) found = row;
        });
        return found;
    }

    var state = mergePickState(localState, serverState);
    var current = 0;
    var editingKey = '';
    queue.forEach(function (item, idx) {
        var prev = findSaved(item, state);
        if (prev === true) prev = { got: item.need, done: true };
        if (prev && prev !== state[item.key]) state[item.key] = prev;
        if (!state[item.key]) state[item.key] = { got: 0, done: false, item_id: item.item_id };
        if (state[item.key] === true) state[item.key] = { got: item.need, done: true, item_id: item.item_id };
        if (!state[item.key].done && current === 0) current = idx;
    });
    if (queue.length && queue.every(function (item) { return state[item.key] && state[item.key].done; })) {
        current = queue.length;
    }

    var els = {
        card: document.getElementById('pkCard'),
        doneAll: document.getElementById('pkDoneAll'),
        empty: document.getElementById('pkEmpty'),
        loc: document.getElementById('pkLoc'),
        locKicker: document.getElementById('pkLocKicker'),
        locMeta: document.getElementById('pkLocMeta'),
        locPath: document.getElementById('pkLocPath'),
        name: document.getElementById('pkName'),
        sku: document.getElementById('pkSku'),
        brand: document.getElementById('pkBrand'),
        img: document.getElementById('pkImg'),
        got: document.getElementById('pkGot'),
        gotView: document.getElementById('pkGotView'),
        need: document.getElementById('pkNeed'),
        barFill: document.getElementById('pkBarFill'),
        barPct: document.getElementById('pkBarPct'),
        addQty: document.getElementById('pkAddQty'),
        progress: document.getElementById('pkProgress'),
        todoN: document.getElementById('pkTodoN'),
        doneN: document.getElementById('pkDoneN'),
        nowN: document.getElementById('pkNowN'),
        todoList: document.getElementById('pkTodoList'),
        doneList: document.getElementById('pkDoneList'),
        viewNow: document.getElementById('pkViewNow'),
        viewTodo: document.getElementById('pkViewTodo'),
        viewDone: document.getElementById('pkViewDone'),
        todoEmpty: document.getElementById('pkTodoEmpty'),
        doneEmpty: document.getElementById('pkDoneEmpty'),
        scan: document.getElementById('pkScanInput'),
        msg: document.getElementById('pkMsg'),
        valid: document.getElementById('pkValidBtn'),
        form: document.getElementById('mgPackValidateForm'),
        validDone: document.getElementById('pkValidDoneBtn'),
        takeLess: document.getElementById('pkTakeLess'),
        takeAll: document.getElementById('pkTakeAll'),
        pickJson: document.getElementById('pkPickJson'),
        itemList: document.getElementById('pkItemList'),
        openHead: document.getElementById('pkOpenHead'),
        openEmpty: document.getElementById('pkOpenEmpty'),
        dockGot: document.getElementById('pkDockGot'),
        dockNeed: document.getElementById('pkDockNeed'),
        statusPill: document.getElementById('pkStatusPill'),
    };
    var isPrenosMp = root.getAttribute('data-prenos-mp') === '1';
    var pickView = 'now';

    function syncValidateGate() {
        if (els.validDone) els.validDone.hidden = false;
        if (els.form) els.form.hidden = true;
    }

    function persist() {
        try { window.localStorage.setItem(storageKey, JSON.stringify(state)); } catch (err) {}
    }
    function itemState(item) {
        return state[item.key] || { got: 0, done: false };
    }
    function doneCount() {
        return queue.filter(function (item) { return itemState(item).done; }).length;
    }
    function showMsg(text, ok) {
        if (!els.msg) return;
        if (!text) { els.msg.hidden = true; return; }
        els.msg.hidden = false;
        els.msg.textContent = text;
        els.msg.classList.toggle('is-ok', !!ok);
    }
    function norm(value) {
        return String(value || '').replace(/\s+/g, '').toLowerCase();
    }
    function codesOf(item) {
        return (item.codes || []).map(norm).filter(Boolean);
    }
    function firstOpenIndex() {
        for (var i = 0; i < queue.length; i += 1) {
            if (!itemState(queue[i]).done) return i;
        }
        return queue.length;
    }

    function renderLine(item, into, fromDone) {
        var st = itemState(item);
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pk-line' + (item.rezervni ? ' is-spare' : '');
        btn.innerHTML = '<b>' + (item.loc || '') + '</b><span>' + (item.naziv || '') + '</span><em>' + st.got + '/' + item.need + '</em>';
        btn.addEventListener('click', function () {
            if (fromDone) {
                if (!window.confirm('Želiš li promijeniti pokupljenu količinu?')) return;
                editingKey = item.key;
            } else {
                editingKey = '';
            }
            current = queue.indexOf(item);
            setPickView('now', true);
        });
        into.appendChild(btn);
    }

    function renderLists() {
        if (els.todoList) els.todoList.innerHTML = '';
        if (els.doneList) els.doneList.innerHTML = '';
        var todoN = 0;
        var doneN = 0;
        queue.forEach(function (item) {
            if (itemState(item).done) {
                doneN += 1;
                if (els.doneList) renderLine(item, els.doneList, true);
            } else {
                todoN += 1;
                if (els.todoList) renderLine(item, els.todoList, false);
            }
        });
        if (els.todoEmpty) els.todoEmpty.hidden = todoN > 0;
        if (els.doneEmpty) els.doneEmpty.hidden = doneN > 0;
    }

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    function totals() {
        var need = 0;
        var got = 0;
        queue.forEach(function (item) {
            need += item.need || 0;
            got += itemState(item).got || 0;
        });
        return { need: need, got: got, items: queue.length, done: doneCount() };
    }

    function appendItemCard(into, item, idx, number, isNow) {
        var st = itemState(item);
        var loc = item.is_mp ? 'MP' : (item.loc || '—');
        var locPath = item.is_mp ? 'Maloprodaja' : (item.loc_path || '');
        var sku = item.sifra || item.barkod || '—';
        var art = document.createElement('article');
        art.className = 'pk-item' + (st.done ? ' is-done' : '') + (isNow ? ' is-now' : '') + (item.rezervni ? ' is-spare' : '');
        art.innerHTML =
            (item.rezervni ? '<div class="pk-spare-tag">REZERVNI DIO</div>' : '') +
            (isNow
                ? '<div class="pk-now-go"><span>' + (item.rezervni ? 'Rezervni dio' : 'Uzmi sa lokacije') + '</span><b>' + escapeHtml(loc) + '</b><small>' + escapeHtml(locPath || (item.rezervni ? 'Slanje rezervnog dijela' : 'Magacin')) + '</small></div>'
                : '') +
            '<div class="pk-item-step">' +
                '<button type="button" data-pk-minus aria-label="Manje">−</button>' +
                '<div class="pk-step-val"><b>' + st.got + ' / ' + item.need + '</b><small>kom</small></div>' +
                '<button type="button" data-pk-plus aria-label="Više">+</button>' +
                (st.done
                    ? '<span class="pk-odvojeno-tag">Odvojeno</span>'
                    : '<button type="button" class="pk-all" data-pk-all>Pokupi sve</button>') +
            '</div>' +
            '<div class="pk-item-top">' +
                '<em class="pk-item-n">' + number + '</em>' +
                '<div class="pk-item-img"' + (item.slika ? ' style="background-image:url(\'' + escapeHtml(item.slika) + '\')"' : '') + '></div>' +
                '<div class="pk-item-info">' +
                    '<strong>' + escapeHtml(sku) + '</strong>' +
                    '<p>' + escapeHtml(item.naziv || '—') + '</p>' +
                    '<span>Šifra: ' + escapeHtml(sku) + '</span>' +
                '</div>' +
            '</div>' +
            (st.done
                ? ''
                : '<div class="pk-less-row">' +
                    '<input type="number" inputmode="numeric" min="0" max="' + item.need + '" step="1" placeholder="0 = nema" data-pk-less-qty aria-label="Količina">' +
                    '<button type="button" data-pk-less>Pokupi manje</button>' +
                  '</div>') +
            (isNow && !st.done && !item.is_mp && !item.rezervni && !item.nije_popisan && item.loc
                ? '<button type="button" class="pk-clear-loc" data-pk-clear-loc>Očisti lokaciju</button>'
                : '');
        art.querySelector('[data-pk-minus]').addEventListener('click', function () {
            current = idx;
            setGot(item, itemState(item).got - 1);
        });
        art.querySelector('[data-pk-plus]').addEventListener('click', function () {
            current = idx;
            setGot(item, itemState(item).got + 1);
        });
        var allBtn = art.querySelector('[data-pk-all]');
        if (allBtn) {
            allBtn.addEventListener('click', function () {
                current = idx;
                setGot(item, item.need);
            });
        }
        var lessBtn = art.querySelector('[data-pk-less]');
        var lessQty = art.querySelector('[data-pk-less-qty]');
        if (lessBtn && lessQty) {
            function pickLess() {
                current = idx;
                var raw = parseInt(lessQty.value, 10);
                if (isNaN(raw) || raw < 0) {
                    showMsg('Unesi količinu koju imaš (0 = nema).');
                    lessQty.focus();
                    return;
                }
                if (raw === 0) {
                    setGot(item, 0, true);
                    dropMissing(item);
                    return;
                }
                if (raw >= item.need) {
                    setGot(item, item.need);
                    return;
                }
                setGot(item, raw, true);
            }
            lessBtn.addEventListener('click', pickLess);
            lessQty.addEventListener('keydown', function (event) {
                if (event.key !== 'Enter') return;
                event.preventDefault();
                pickLess();
            });
        }
        var clearBtn = art.querySelector('[data-pk-clear-loc]');
        if (clearBtn) {
            clearBtn.addEventListener('click', function () {
                current = idx;
                clearLocation(item, clearBtn);
            });
        }
        into.appendChild(art);
    }

    function renderItems() {
        if (!els.itemList) return;
        els.itemList.innerHTML = '';
        var doneN = doneCount();
        var remaining = queue.length - doneN;
        var focusIdx = -1;
        if (editingKey) {
            for (var i = 0; i < queue.length; i += 1) {
                if (queue[i].key === editingKey) {
                    focusIdx = i;
                    break;
                }
            }
        }
        if (focusIdx < 0 && queue[current] && !itemState(queue[current]).done) {
            focusIdx = current;
        }
        if (focusIdx < 0) {
            focusIdx = firstOpenIndex();
            current = focusIdx;
        }
        if (focusIdx < queue.length && queue[focusIdx]) {
            appendItemCard(els.itemList, queue[focusIdx], focusIdx, focusIdx + 1, true);
        }
        if (els.openHead) {
            els.openHead.hidden = remaining === 0 && !editingKey;
            if (!els.openHead.hidden) {
                els.openHead.textContent = editingKey
                    ? 'Izmjena stavke'
                    : ('Stavka ' + (focusIdx + 1) + ' / ' + queue.length);
            }
        }
        if (els.openEmpty) els.openEmpty.hidden = !(remaining === 0 && doneN > 0 && !editingKey);
    }

    function syncPickView() {
        root.querySelectorAll('[data-pk-view]').forEach(function (btn) {
            btn.classList.toggle('is-on', btn.getAttribute('data-pk-view') === pickView);
        });
        if (els.viewNow) els.viewNow.hidden = pickView !== 'now';
        if (els.viewTodo) els.viewTodo.hidden = pickView !== 'todo';
        if (els.viewDone) els.viewDone.hidden = pickView !== 'done';
    }

    function setPickView(name, keepCurrent) {
        pickView = name === 'todo' || name === 'done' ? name : 'now';
        if (pickView === 'now' && !keepCurrent) {
            editingKey = '';
            current = firstOpenIndex();
        }
        syncPickView();
        render();
    }

    function render() {
        var finished = doneCount();
        var tot = totals();
        var pct = tot.need > 0 ? Math.round((tot.got / tot.need) * 100) : 0;
        if (els.progress) els.progress.textContent = finished + '/' + queue.length;
        if (els.todoN) els.todoN.textContent = String(queue.length - finished);
        if (els.doneN) els.doneN.textContent = String(finished);
        if (els.valid) els.valid.disabled = false;
        if (els.nowN) {
            els.nowN.textContent = queue.length
                ? (Math.min(finished + (finished === queue.length ? 0 : 1), queue.length) + '/' + queue.length)
                : '0';
        }
        if (els.dockGot) els.dockGot.textContent = String(tot.got);
        if (els.dockNeed) els.dockNeed.textContent = String(tot.need);
        if (els.statusPill) {
            els.statusPill.textContent = tot.got <= 0 ? 'Čeka picking' : (finished === queue.length && queue.length ? 'Gotovo' : 'U toku');
        }
        if (!editingKey && (current >= queue.length || (queue[current] && itemState(queue[current]).done))) {
            current = firstOpenIndex();
        }
        syncValidateGate();
        syncPickView();
        renderLists();
        renderItems();

        if (!queue.length) {
            if (els.card) els.card.hidden = true;
            if (els.doneAll) els.doneAll.hidden = true;
            if (els.empty) els.empty.hidden = false;
            return;
        }
        if (els.empty) els.empty.hidden = true;
        if (els.doneAll) els.doneAll.hidden = true;
        if (els.card) els.card.hidden = false;
        var customer = document.getElementById('pkCustomer');
        if (customer) customer.hidden = false;
        if (els.locKicker) {
            var cur = queue[current];
            els.locKicker.textContent = cur && cur.nije_popisan
                ? 'Nije popisan'
                : (cur && cur.is_mp ? 'Uzmi iz maloprodaje' : 'Lokacija (odakle se uzima)');
        }
        if (pickView === 'now') showMsg('');
        if (els.scan && pickView === 'now') {
            window.setTimeout(function () { els.scan.focus(); }, 40);
        }
        syncPickJson();
    }

    function pickPayload() {
        return queue.map(function (item) {
            var st = itemState(item);
            return {
                key: item.key,
                item_id: item.item_id,
                loc: item.loc || '',
                got: st.got,
                need: item.need,
                done: !!st.done,
            };
        });
    }
    function syncPickJson() {
        if (els.pickJson) els.pickJson.value = JSON.stringify(pickPayload());
    }
    var saveTimer = 0;
    function saveServer() {
        syncPickJson();
        var csrf = root.querySelector('[name=csrfmiddlewaretoken]');
        var body = new URLSearchParams();
        body.set('action', 'pick_save');
        body.set('pick_json', JSON.stringify(pickPayload()));
        if (csrf) body.set('csrfmiddlewaretoken', csrf.value);
        fetch(window.location.pathname, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: body,
            credentials: 'same-origin',
        }).catch(function () {});
    }
    function saveSoon() {
        persist();
        window.clearTimeout(saveTimer);
        saveTimer = window.setTimeout(saveServer, 250);
    }
    function clearLocation(item, btn) {
        if (!item) return;
        var loc = item.loc || '';
        if (!loc) return;
        var password = window.prompt(
            'Artikal fizički nema na lokaciji ' + loc + '.\n\n' +
            'Očistiti lokaciju — količine ovog artikla na TOJ lokaciji idu na 0. ' +
            'Druge lokacije se ne diraju. Sa sajta ide tek ako nema ništa nigdje.\n' +
            'Unesi šifru:'
        );
        if (password === null) return;
        if (String(password).trim() !== 'admin') {
            window.alert('Pogrešna šifra.');
            return;
        }
        if (btn) btn.disabled = true;
        var csrf = root.querySelector('[name=csrfmiddlewaretoken]');
        var body = new URLSearchParams();
        body.set('action', 'pick_ocisti');
        body.set('item_id', String(item.item_id || ''));
        body.set('loc', loc);
        body.set('key', item.key || '');
        body.set('lozinka', String(password).trim());
        if (csrf) body.set('csrfmiddlewaretoken', csrf.value);
        fetch(window.location.pathname, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrf ? csrf.value : '',
            },
            body: body,
            credentials: 'same-origin',
        }).then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
          .then(function (result) {
              if (!result.data || !result.data.ok) {
                  if (btn) btn.disabled = false;
                  window.alert((result.data && result.data.error) || 'Lokacija nije očišćena.');
                  return;
              }
              window.location.reload();
          }).catch(function () {
              if (btn) btn.disabled = false;
              window.alert('Lokacija nije očišćena.');
          });
    }

    function dropMissing(item) {
        if (!item) return;
        if (!window.confirm('Artikal nema na ovoj lokaciji. Skinuti s narudžbe i s lokacije?')) return;
        var csrf = root.querySelector('[name=csrfmiddlewaretoken]');
        var body = new URLSearchParams();
        body.set('action', 'pick_nema');
        body.set('item_id', String(item.item_id || ''));
        body.set('loc', item.loc || '');
        body.set('need', String(item.need || 0));
        body.set('key', item.key || '');
        if (csrf) body.set('csrfmiddlewaretoken', csrf.value);
        fetch(window.location.pathname, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrf ? csrf.value : '',
            },
            body: body,
            credentials: 'same-origin',
        }).then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
          .then(function (result) {
              if (!result.data || !result.data.ok) {
                  window.alert((result.data && result.data.error) || 'Stavka nije skinuta.');
                  return;
              }
              if (result.data.redirect) {
                  window.location.href = result.data.redirect;
                  return;
              }
              window.location.reload();
          }).catch(function () {
              window.alert('Stavka nije skinuta.');
          });
    }

    function setGot(item, got, forceDone) {
        var next = Math.max(0, Math.min(item.need, got));
        var exact = next >= item.need && item.need > 0;
        var missing = forceDone && next === 0 && item.need > 0;
        var done = exact || !!(forceDone && next > 0) || missing;
        state[item.key] = { got: next, done: done, item_id: item.item_id };
        persist();
        if (done && editingKey === item.key) editingKey = '';
        if (done) current = firstOpenIndex();
        render();
        saveSoon();
    }

    function applyScan(code) {
        var value = norm(code);
        if (!value || !queue.length) return;
        var item = queue[current];
        if (!item || itemState(item).done) {
            current = firstOpenIndex();
            item = queue[current];
        }
        if (!item) return;
        if (codesOf(item).indexOf(value) === -1) {
            showMsg('Pogrešan artikal. Sada pickuj: ' + (item.naziv || 'ovu stavku') + ' @ ' + (item.loc || '—'));
            return;
        }
        var st = itemState(item);
        setGot(item, st.got + 1);
        showMsg(itemState(item).done ? 'Pokupljeno. Sljedeća stavka.' : 'Sken OK', true);
    }

    function currentItem() {
        return queue[current];
    }

    var minus = document.getElementById('pkMinus');
    var plus = document.getElementById('pkPlus');
    if (minus) minus.addEventListener('click', function () {
        var item = currentItem();
        if (item) setGot(item, itemState(item).got - 1);
    });
    if (plus) plus.addEventListener('click', function () {
        var item = currentItem();
        if (item) setGot(item, itemState(item).got + 1);
    });
    if (els.takeLess) els.takeLess.addEventListener('click', function () {
        var item = currentItem();
        if (!item) return;
        var got = itemState(item).got;
        if (got > 0 && got < item.need) setGot(item, got, true);
    });
    if (els.takeAll) els.takeAll.addEventListener('click', function () {
        var item = currentItem();
        if (item) setGot(item, item.need);
    });
    var addBtn = document.getElementById('pkAddQtyBtn');
    if (addBtn) addBtn.addEventListener('click', function () {
        var item = currentItem();
        if (!item || !els.addQty) return;
        var raw = parseInt(els.addQty.value, 10);
        if (isNaN(raw) || raw <= 0) return;
        setGot(item, itemState(item).got + raw);
        els.addQty.value = '0';
    });
    var resetBtn = document.getElementById('pkReset');
    if (resetBtn) resetBtn.addEventListener('click', function () {
        var allDone = queue.length > 0 && doneCount() === queue.length && !editingKey;
        if (allDone) {
            if (!window.confirm('Poništiti cijeli picking?')) return;
            queue.forEach(function (item) {
                state[item.key] = { got: 0, done: false, item_id: item.item_id };
            });
            editingKey = '';
            current = 0;
            persist();
            render();
            saveSoon();
            return;
        }
        var item = currentItem();
        if (!item) return;
        if (!window.confirm('Poništiti pokupljenu količinu za ovu stavku?')) return;
        editingKey = item.key;
        setGot(item, 0);
    });
    if (els.got) {
        function applyTypedQty(asDone) {
            var item = currentItem();
            if (!item) return;
            var raw = parseInt(els.got.value, 10);
            if (isNaN(raw)) raw = 0;
            if (asDone && raw > 0 && raw < item.need) setGot(item, raw, true);
            else setGot(item, raw);
        }
        els.got.addEventListener('change', function () { applyTypedQty(false); });
        els.got.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            applyTypedQty(true);
            els.got.blur();
        });
    }
    if (els.scan) {
        els.scan.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            applyScan(els.scan.value);
            els.scan.value = '';
        });
        els.scan.addEventListener('change', function () {
            if (!els.scan.value) return;
            applyScan(els.scan.value);
            els.scan.value = '';
        });
        els.scan.addEventListener('input', function () {
            var value = els.scan.value.trim();
            if (value.length < 3) return;
            window.clearTimeout(els.scan._pkTimer);
            els.scan._pkTimer = window.setTimeout(function () {
                if (els.scan.value.trim() !== value) return;
                applyScan(value);
                els.scan.value = '';
            }, 60);
        });
    }

    root.querySelectorAll('[data-pk-view]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            setPickView(btn.getAttribute('data-pk-view'));
        });
    });
    root.querySelectorAll('[data-pk-tab]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            setTab(btn.getAttribute('data-pk-tab'));
        });
    });
    function setTab(name) {
        if (name !== 'pick') editingKey = '';
        root.querySelectorAll('[data-pk-tab]').forEach(function (btn) {
            btn.classList.toggle('is-on', btn.getAttribute('data-pk-tab') === name);
        });
        root.querySelectorAll('[data-pk-panel]').forEach(function (panel) {
            panel.hidden = panel.getAttribute('data-pk-panel') !== name;
        });
        if (name === 'pick' && els.scan) els.scan.focus();
    }

    function clickValidate() {
        if (els.valid) els.valid.click();
    }
    if (els.validDone) els.validDone.addEventListener('click', clickValidate);
    var validDone2 = document.getElementById('pkValidDoneBtn2');
    if (validDone2) validDone2.addEventListener('click', clickValidate);
    var cancelForm = document.getElementById('pkCancelForm');
    if (cancelForm) {
        cancelForm.addEventListener('submit', function (event) {
            var cancelMsg = isPrenosMp
                ? 'Otkazati prenos u MP? Artikli ostaju na lokacijama, ništa se ne prenosi.'
                : 'Otkazati narudžbu i vratiti rezervaciju na lokacije?';
            if (!window.confirm(cancelMsg)) {
                event.preventDefault();
            }
        });
    }
    if (els.form) {
        els.form.addEventListener('submit', function (event) {
            syncPickJson();
            var broj = root.getAttribute('data-broj') || '';
            var msg = isPrenosMp
                ? 'Želiš li validatovati prenos u MP #' + broj + '? Skida se sa stanja.'
                : 'Želiš li završiti picking #' + broj + '?';
            var hasZero = queue.some(function (item) {
                return (itemState(item).got || 0) === 0 && (item.need || 0) > 0;
            });
            if (!isPrenosMp && (hasZero || (queue.length && doneCount() < queue.length))) {
                msg = 'Artikli s 0 kom se skidaju s narudžbe. Završiti picking #' + broj + '?';
            }
            if (!window.confirm(msg)) event.preventDefault();
            else {
                try { window.localStorage.removeItem(storageKey); } catch (err) {}
            }
        });
    }

    window.addEventListener('pagehide', function () { persist(); saveServer(); });
    persist();
    if (Object.keys(state).length) saveSoon();
    render();
})();

(function initPickEdit() {
    var root = document.getElementById('pkPickApp');
    if (!root || root.getAttribute('data-edit') !== '1') return;
    var lookup = root.getAttribute('data-lookup') || '';
    var isVp = root.getAttribute('data-vp') === '1';
    var query = document.getElementById('pkEditQuery');
    var list = document.getElementById('pkEditSuggest');
    var lines = document.getElementById('pkEditLines');
    var totalEl = document.getElementById('pkEditTotal');
    var addForm = document.getElementById('pkEditAddForm');
    var productId = document.getElementById('pkEditProductId');
    var variationId = document.getElementById('pkEditVariationId');
    var qtyHidden = document.getElementById('pkEditAddQty');
    var mpOk = document.getElementById('pkEditMpOk');
    var qtyModal = document.getElementById('pkEditQtyModal');
    var qtyName = document.getElementById('pkEditQtyName');
    var qtyMeta = document.getElementById('pkEditQtyMeta');
    var qtyInput = document.getElementById('pkEditQtyInput');
    var qtyAdd = document.getElementById('pkEditQtyAdd');
    var mpModal = document.getElementById('pkEditMpModal');
    var mpText = document.getElementById('pkEditMpText');
    var timer = null;
    var pending = null;
    var pendingMp = null;
    var busy = false;

    function csrfToken() {
        var el = root.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : '';
    }
    function priceOf(item) {
        return item && item.cijena ? item.cijena : '';
    }
    function flatten(data) {
        var rows = [];
        (data.results || []).forEach(function (prod) {
            var vars = prod.varijacije || [];
            var parentQty = Number(prod.dostupno) || 0;
            var varRows = (vars || []).map(function (v) {
                return {
                    id: prod.id,
                    variation_id: v.id,
                    naziv: (prod.naziv || '') + ' ' + (v.naziv || ''),
                    sifra: v.sifra || prod.sifra || '',
                    cijena: v.cijena || prod.cijena || '',
                    dostupno: Number(v.na_stanju != null ? v.na_stanju : 0) || 0,
                };
            });
            var varQty = varRows.reduce(function (sum, row) { return sum + (row.dostupno || 0); }, 0);
            if (vars.length > 1 && varQty > 0) {
                varRows.forEach(function (row) { rows.push(row); });
            } else {
                rows.push({
                    id: prod.id,
                    variation_id: (vars.length === 1 && varQty > 0) ? vars[0].id : '',
                    naziv: prod.naziv,
                    sifra: prod.sifra,
                    cijena: prod.cijena || '',
                    dostupno: parentQty,
                });
            }
        });
        return rows;
    }
    function looksLikeBarcode(q) {
        return /^[0-9]{6,}$/.test(String(q || '').trim());
    }
    function closeQty() { if (qtyModal) qtyModal.hidden = true; }
    function closeMp() { pendingMp = null; if (mpModal) mpModal.hidden = true; }
    function openQty(item) {
        pending = item;
        if (qtyName) qtyName.textContent = item.naziv || 'Artikal';
        if (qtyMeta) qtyMeta.textContent = [item.sifra, priceOf(item) ? priceOf(item) + ' KM' : ''].filter(Boolean).join(' · ');
        if (qtyInput) qtyInput.value = '1';
        if (qtyModal) qtyModal.hidden = false;
        window.setTimeout(function () {
            if (!qtyInput) return;
            qtyInput.focus();
            qtyInput.select();
        }, 40);
    }
    function renderSuggest(items) {
        if (!list) return;
        list.innerHTML = '';
        if (!items.length) {
            list.innerHTML = '<li class="is-empty">Nema rezultata.</li>';
            list.hidden = false;
            return;
        }
        items.forEach(function (item) {
            var li = document.createElement('li');
            li.innerHTML = '<strong></strong><span></span>';
            li.querySelector('strong').textContent = item.naziv || '';
            li.querySelector('span').textContent = [item.sifra, priceOf(item) ? priceOf(item) + ' KM' : ''].filter(Boolean).join(' · ');
            li.addEventListener('mousedown', function (event) {
                event.preventDefault();
                openQty(item);
            });
            list.appendChild(li);
        });
        list.hidden = false;
    }
    function search(q) {
        var value = (q || '').trim();
        if (!value || !lookup) {
            if (list) list.hidden = true;
            return;
        }
        fetch(lookup + '?q=' + encodeURIComponent(value) + '&bez_zalihe=1', {
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            credentials: 'same-origin',
        }).then(function (res) { return res.json(); }).then(function (data) {
            var rows = flatten(data);
            if (looksLikeBarcode(value) && rows.length === 1) {
                openQty(rows[0]);
                if (list) list.hidden = true;
                return;
            }
            renderSuggest(rows);
        }).catch(function () {
            if (list) {
                list.innerHTML = '<li class="is-empty">Pretraga nije uspjela.</li>';
                list.hidden = false;
            }
        });
    }
    function postAction(body) {
        if (busy) return;
        busy = true;
        fetch(window.location.pathname, {
            method: 'POST',
            headers: {
                'X-Requested-With': 'XMLHttpRequest',
                'X-CSRFToken': csrfToken(),
                'Content-Type': 'application/x-www-form-urlencoded',
            },
            credentials: 'same-origin',
            body: body.toString(),
        }).then(function (res) { return res.json().then(function (data) { return { ok: res.ok, status: res.status, data: data }; }); })
          .then(function (result) {
              busy = false;
              if (result.data && result.data.need_mp) {
                  closeQty();
                  pendingMp = result.data;
                  if (mpText) {
                      mpText.textContent = result.data.error || 'Artikal nije na stanju u magacinu.';
                  }
                  if (mpModal) mpModal.hidden = false;
                  return;
              }
              if (!result.data || !result.data.ok) {
                  window.alert((result.data && result.data.error) || 'Narudžba nije izmijenjena.');
                  return;
              }
              window.location.reload();
          }).catch(function () {
              busy = false;
              window.alert('Narudžba nije izmijenjena.');
          });
    }
    function submitAdd(item, qty, fromMp) {
        if (!item) return;
        var body = new URLSearchParams();
        body.set('action', 'dodaj');
        body.set('product_id', item.id || item.product_id);
        body.set('variation_id', item.variation_id || '');
        body.set('kolicina', String(qty || 1));
        body.set('mp_ok', fromMp ? '1' : '0');
        if (productId) productId.value = item.id || '';
        if (variationId) variationId.value = item.variation_id || '';
        if (qtyHidden) qtyHidden.value = String(qty || 1);
        if (mpOk) mpOk.value = fromMp ? '1' : '0';
        postAction(body);
    }
    if (query) {
        query.addEventListener('input', function () {
            window.clearTimeout(timer);
            timer = window.setTimeout(function () { search(query.value); }, 160);
        });
        query.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            search(query.value);
        });
        query.addEventListener('mg-scanned', function (event) {
            var code = (event.detail && event.detail.code) || query.value;
            search(code);
        });
    }
    if (qtyAdd) {
        qtyAdd.addEventListener('click', function () {
            submitAdd(pending, qtyInput ? qtyInput.value : 1, false);
            closeQty();
        });
    }
    if (qtyInput) {
        qtyInput.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            submitAdd(pending, qtyInput.value, false);
            closeQty();
        });
    }
    root.querySelectorAll('[data-pk-edit-qty-close]').forEach(function (el) {
        el.addEventListener('click', closeQty);
    });
    root.querySelectorAll('[data-pk-edit-mp-skip]').forEach(function (el) {
        el.addEventListener('click', closeMp);
    });
    var mpSkip = document.getElementById('pkEditMpSkip');
    var mpAdd = document.getElementById('pkEditMpAdd');
    if (mpSkip) mpSkip.addEventListener('click', closeMp);
    if (mpAdd) {
        mpAdd.addEventListener('click', function () {
            if (!pendingMp) { closeMp(); return; }
            submitAdd({
                id: pendingMp.product_id,
                variation_id: pendingMp.variation_id || '',
            }, pendingMp.kolicina || 1, true);
            closeMp();
        });
    }
    if (lines) {
        lines.querySelectorAll('.pk-edit-qty-form').forEach(function (form) {
            var field = form.querySelector('.pk-edit-qty');
            if (!field) return;
            field.addEventListener('change', function () {
                var body = new URLSearchParams();
                body.set('action', 'kolicina');
                body.set('stavka_id', form.querySelector('[name=stavka_id]').value);
                body.set('kolicina', field.value || '1');
                postAction(body);
            });
        });
        lines.querySelectorAll('.pk-edit-remove-form').forEach(function (form) {
            form.addEventListener('submit', function (event) {
                event.preventDefault();
                if (!window.confirm('Ukloniti artikal s narudžbe i računa?')) return;
                var body = new URLSearchParams();
                body.set('action', 'ukloni');
                body.set('stavka_id', form.querySelector('[name=stavka_id]').value);
                postAction(body);
            });
        });
    }
    if (addForm) addForm.addEventListener('submit', function (event) { event.preventDefault(); });
    if (isVp && totalEl) totalEl.setAttribute('data-vp', '1');
})();

function initFaliPrenos() {
    var root = document.getElementById('mgFaliPage');
    if (!root) return;
    root.querySelectorAll('[data-fali-prenos]').forEach(function (form) {
        var loc = form.querySelector('[data-fali-loc]');
        var qty = form.querySelector('[data-fali-qty]');
        function syncMax() {
            if (!loc || !qty) return;
            var opt = loc.options[loc.selectedIndex];
            var max = opt ? parseInt(opt.getAttribute('data-max'), 10) : 0;
            if (!max || max < 1) max = 1;
            qty.max = String(max);
            var n = parseInt(qty.value, 10) || 1;
            if (n > max) qty.value = String(max);
            if (n < 1) qty.value = '1';
        }
        if (loc) loc.addEventListener('change', syncMax);
        syncMax();
        form.addEventListener('submit', function (event) {
            syncMax();
            var max = parseInt(qty && qty.max, 10) || 0;
            var n = parseInt(qty && qty.value, 10) || 0;
            if (n < 1 || (max && n > max)) {
                event.preventDefault();
                if (qty) {
                    qty.focus();
                    qty.select();
                }
                window.alert(max ? ('Možeš prenijeti najviše ' + max + ' kom s te lokacije.') : 'Unesi količinu.');
                return;
            }
            if (!window.confirm('Prenijeti ' + n + ' kom u maloprodaju? Stavka ide na Picking.')) {
                event.preventDefault();
            }
        });
    });
}

function initPopisPage() {
    var root = document.getElementById('ppApp');
    if (!root) return;
    var live = root.getAttribute('data-live') === '1';
    var lookupUrl = root.getAttribute('data-lookup') || '';
    var input = document.getElementById('ppQuery');
    var list = document.getElementById('ppList');
    var suggest = document.getElementById('ppSuggest');
    var toast = document.getElementById('ppToast');
    var countEl = document.getElementById('ppCount');
    var totalEl = document.getElementById('ppTotal');
    var addForm = document.getElementById('ppAddForm');
    var modal = document.getElementById('ppQtyModal');
    var qtyInput = document.getElementById('ppQty');
    var qtyName = document.getElementById('ppQtyName');
    var qtySifra = document.getElementById('ppQtySifra');
    var qtySave = document.getElementById('ppQtySave');
    var qtyExpected = document.getElementById('ppQtyExpected');
    var csrfEl = addForm && addForm.querySelector('[name=csrfmiddlewaretoken]');
    var csrf = csrfEl ? csrfEl.value : '';
    var popisIdEl = addForm && addForm.querySelector('[name=popis_id]');
    var bootEl = document.getElementById('ppBoot');
    var boot = { stavke: [], count: 0, total_qty: 0 };
    try {
        if (bootEl) boot = JSON.parse(bootEl.textContent || '{}') || boot;
    } catch (err) {}
    var stavke = boot.stavke || [];
    var timer = 0;
    var pending = Promise.resolve();
    var toastTimer = 0;
    var editId = 0;
    var pendingItem = null;

    function csrfToken() {
        var el = root.querySelector('[name=csrfmiddlewaretoken]');
        return el ? el.value : csrf;
    }
    function currentPopisId() {
        return (popisIdEl && popisIdEl.value) || root.getAttribute('data-popis-id') || '';
    }
    function toggleCheck(id, checked) {
        var body = new URLSearchParams();
        body.set('action', 'cekiraj');
        body.set('csrfmiddlewaretoken', csrfToken());
        if (currentPopisId()) body.set('popis_id', currentPopisId());
        body.set('stavka_id', String(id));
        body.set('cekirano', checked ? '1' : '0');
        fetch(window.location.pathname, {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
            body: body,
            credentials: 'same-origin',
        }).then(function (res) { return res.json(); }).then(function (data) {
            var ready = !!(data && data.all_checked);
            var btn = document.getElementById('ppPrintBtn');
            var hint = document.getElementById('ppPrintHint');
            if (btn) {
                btn.setAttribute('aria-disabled', ready ? 'false' : 'true');
                btn.classList.toggle('is-disabled', !ready);
            }
            if (hint) hint.hidden = ready;
        }).catch(function () {});
    }
    if (list) {
        list.addEventListener('change', function (event) {
            var box = event.target.closest('[data-pp-check]');
            if (!box) return;
            var rowEl = box.closest('li[data-id]');
            if (!rowEl) return;
            toggleCheck(rowEl.getAttribute('data-id'), box.checked);
            rowEl.classList.toggle('is-checked', box.checked);
        });
        list.addEventListener('click', function (event) {
            var btn = event.target.closest('#ppPrintBtn, a[aria-disabled="true"]');
            if (!btn || btn.id !== 'ppPrintBtn') return;
            if (btn.getAttribute('aria-disabled') === 'true') event.preventDefault();
        });
    }
    var printBtn = document.getElementById('ppPrintBtn');
    if (printBtn) {
        printBtn.addEventListener('click', function (event) {
            if (printBtn.getAttribute('aria-disabled') === 'true') event.preventDefault();
        });
    }

    var pause = document.getElementById('ppPauseForm');
    if (pause) {
        pause.addEventListener('submit', function (event) {
            if (!window.confirm('Pauzirati popis? Možeš ga kasnije nastaviti.')) event.preventDefault();
        });
    }
    var finish = document.getElementById('ppFinishForm');
    if (finish) {
        finish.addEventListener('submit', function (event) {
            if (!window.confirm('Završiti popis? Količine na odabranoj lokaciji postavit će se na popisane. Ako se poklapaju, ostaje tačna količina.')) event.preventDefault();
        });
    }
    var del = document.getElementById('ppDeleteForm');
    if (del) {
        del.addEventListener('submit', function (event) {
            if (!window.confirm('Obrisati cijeli popis?')) event.preventDefault();
        });
    }
    root.querySelectorAll('.pp-print-form').forEach(function (form) {
        form.addEventListener('submit', function (event) {
            if (!window.confirm('Štampati popis? Nakon potvrde bit će označen kao završen.')) {
                event.preventDefault();
                return;
            }
            window.setTimeout(function () {
                window.location.reload();
            }, 350);
        });
    });
    if (!live || !input || !list) return;

    function beep(ok) {
        try {
            var Ctx = window.AudioContext || window.webkitAudioContext;
            if (Ctx) {
                var ctx = new Ctx();
                var osc = ctx.createOscillator();
                var gain = ctx.createGain();
                osc.type = 'sine';
                osc.frequency.value = ok ? 880 : 240;
                gain.gain.value = 0.06;
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start();
                osc.stop(ctx.currentTime + 0.09);
                window.setTimeout(function () {
                    try { ctx.close(); } catch (e) {}
                }, 200);
            }
        } catch (e) {}
        if (ok && navigator.vibrate) {
            try { navigator.vibrate(30); } catch (e2) {}
        }
    }

    function showToast(msg, isError) {
        if (!toast) return;
        toast.textContent = msg || '';
        toast.hidden = !msg;
        toast.classList.toggle('is-error', !!isError);
        window.clearTimeout(toastTimer);
        if (msg) {
            toastTimer = window.setTimeout(function () {
                toast.hidden = true;
            }, 1800);
        }
    }

    function focusQuery(immediate) {
        if (!input) return;
        input.value = '';
        hideSuggest();
        function go() {
            try {
                input.focus({ preventScroll: false });
                input.select();
            } catch (e) {
                try { input.focus(); } catch (e2) {}
            }
        }
        if (immediate) go();
        else window.setTimeout(go, 30);
    }

    function esc(text) {
        return String(text || '').replace(/[&<>"']/g, function (ch) {
            return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[ch];
        });
    }

    function fmtDiff(n) {
        var v = Number(n) || 0;
        return (v > 0 ? '+' : '') + String(v);
    }

    function render(highlightId) {
        if (!stavke.length) {
            list.innerHTML = '<li class="is-empty">Još nema stavki. Skeniraj barkod ili unesi artikal.</li>';
        } else {
            list.innerHTML = stavke.map(function (row) {
                var flash = highlightId && Number(row.id) === Number(highlightId) ? ' is-flash' : '';
                var expected = Number(row.ocekivano) || 0;
                var counted = Number(row.kolicina) || 0;
                var diff = row.razlika != null ? Number(row.razlika) : (counted - expected);
                var tone = diff === 0 ? ' is-match' : (diff > 0 ? ' is-over' : ' is-under');
                var checked = row.cekirano ? ' is-checked' : '';
                var diffCls = diff === 0 ? 'is-ok' : 'is-diff';
                return '<li class="' + flash + tone + checked + '" data-id="' + row.id + '" data-qty="' + counted + '">' +
                    '<label class="pp-check"><input type="checkbox" data-pp-check' + (row.cekirano ? ' checked' : '') + ' aria-label="Čekiraj"></label>' +
                    '<div class="pp-line-info"><strong>' + esc(row.naziv) + '</strong><span>' + esc(row.sifra || '—') + '</span></div>' +
                    '<div class="pp-counts">' +
                    '<span class="pp-count-box">Na stanju <b>' + expected + '</b></span>' +
                    '<span class="pp-count-box is-got">Popisano <b>' + counted + '</b></span>' +
                    '<span class="pp-count-box ' + diffCls + '">Razlika <b>' + fmtDiff(diff) + '</b></span>' +
                    '</div>' +
                    '<div class="pp-step">' +
                    '<button type="button" class="pp-step-btn" data-pp-delta="-1" aria-label="Smanji">−</button>' +
                    '<button type="button" class="pp-step-qty" data-pp-edit>' + esc(counted) + '</button>' +
                    '<button type="button" class="pp-step-btn" data-pp-delta="1" aria-label="Povećaj">+</button>' +
                    '</div></li>';
            }).join('');
        }
        if (countEl) countEl.textContent = String(stavke.length);
        if (totalEl) {
            var sum = 0;
            stavke.forEach(function (row) { sum += Number(row.kolicina) || 0; });
            totalEl.textContent = String(sum);
        }
    }

    function applyPayload(data, addedLabel) {
        stavke = data.stavke || [];
        render(data.added_id);
        if (addedLabel) {
            showToast(addedLabel, false);
            beep(true);
        }
        if (document.activeElement !== input) focusQuery(true);
        else {
            input.value = '';
            hideSuggest();
        }
    }

    function post(action, extra) {
        pending = pending.then(function () {
            var body = new URLSearchParams();
            body.set('action', action);
            if (csrf) body.set('csrfmiddlewaretoken', csrf);
            if (popisIdEl && popisIdEl.value) body.set('popis_id', popisIdEl.value);
            Object.keys(extra || {}).forEach(function (key) {
                if (extra[key] != null) body.set(key, extra[key]);
            });
            return fetch(window.location.pathname, {
                method: 'POST',
                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                body: body,
                credentials: 'same-origin',
            }).then(function (res) {
                return res.json().then(function (data) {
                    return { ok: res.ok && data && data.ok !== false, data: data || {} };
                });
            }).then(function (result) {
                if (!result.ok) {
                    beep(false);
                    showToast((result.data && result.data.error) || 'Greška na popisu.', true);
                    return null;
                }
                return result.data;
            }).catch(function () {
                beep(false);
                showToast('Nema veze. Pokušaj ponovo.', true);
                return null;
            });
        });
        return pending;
    }

    function addItem(item, qty) {
        if (!item || !item.id) return;
        var amount = Math.max(1, parseInt(qty, 10) || 1);
        post('dodaj', {
            product_id: item.id,
            variation_id: item.variation_id || '',
            kolicina: String(amount),
        }).then(function (data) {
            if (!data) return;
            applyPayload(data, 'Dodano: ' + (item.naziv || '') + ' × ' + amount);
        });
    }

    function setQty(id, qty) {
        post('set_qty', { stavka_id: String(id), kolicina: String(qty) }).then(function (data) {
            if (!data) return;
            applyPayload(data);
        });
    }

    function hideSuggest() {
        if (suggest) suggest.hidden = true;
    }

    function norm(value) {
        return String(value || '').replace(/\s+/g, '').toLowerCase();
    }

    function flatten(results) {
        var rows = [];
        (results || []).forEach(function (prod) {
            var vars = prod.varijacije || [];
            var varQty = (vars || []).reduce(function (sum, v) {
                return sum + (Number(v.na_stanju != null ? v.na_stanju : 0) || 0);
            }, 0);
            if (vars.length > 1 && varQty > 0) {
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
                    variation_id: (vars.length === 1 && varQty > 0) ? vars[0].id : '',
                    naziv: prod.naziv,
                    sifra: prod.sifra,
                    barkod: prod.barkod || '',
                });
            }
        });
        return rows;
    }

    function isExact(item, query) {
        var q = norm(query);
        if (!q) return false;
        return norm(item.sifra) === q || norm(item.barkod) === q;
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
            li.querySelector('span').textContent = item.sifra || '';
            li.addEventListener('click', function () {
                hideSuggest();
                askQty(item);
            });
            suggest.appendChild(li);
        });
        suggest.hidden = false;
    }

    function searchArticles(query, commit) {
        var q = String(query || '').trim();
        if (!q) {
            hideSuggest();
            return;
        }
        if (!lookupUrl) {
            showToast('Pretraga nije spremna. Osvježi stranicu.', true);
            return;
        }
        fetch(lookupUrl + '?q=' + encodeURIComponent(q) + '&bez_zalihe=1&limit=20', {
            credentials: 'same-origin',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        }).then(function (res) { return res.json(); }).then(function (data) {
            var rows = flatten(data.results || []);
            var hit = rows.filter(function (row) { return isExact(row, q); })[0];
            if (commit) {
                if (hit || (data.exact && rows.length === 1)) {
                    hideSuggest();
                    askQty(hit || rows[0]);
                    return;
                }
                if (rows.length === 1) {
                    hideSuggest();
                    askQty(rows[0]);
                    return;
                }
                if (!rows.length) {
                    showSuggest([]);
                    beep(false);
                    showToast('Artikal nije pronađen.', true);
                    return;
                }
                showSuggest(rows);
                return;
            }
            showSuggest(rows);
        }).catch(function () {
            showToast('Pretraga nije uspjela.', true);
        });
    }

    function closeQty(opts) {
        if (modal) modal.hidden = true;
        document.body.classList.remove('pp-qty-open');
        editId = 0;
        pendingItem = null;
        if (!(opts && opts.keepFocus)) focusQuery(true);
    }

    function showQtyModal(name, sifra, qty, saveLabel, expected) {
        if (!modal) return;
        if (qtyName) qtyName.textContent = name || 'Količina';
        if (qtySifra) qtySifra.textContent = sifra || '';
        if (qtyExpected) {
            if (expected == null || expected === '') {
                qtyExpected.hidden = true;
                qtyExpected.textContent = '';
            } else {
                qtyExpected.hidden = false;
                qtyExpected.innerHTML = 'Na stanju <b>' + esc(expected) + '</b>';
            }
        }
        if (qtyInput) {
            qtyInput.min = pendingItem ? '1' : '0';
            qtyInput.value = pendingItem ? '' : String(qty == null ? 1 : qty);
        }
        if (qtySave) qtySave.textContent = saveLabel || 'Ubaci';
        modal.hidden = false;
        document.body.classList.add('pp-qty-open');
        window.setTimeout(function () {
            if (!qtyInput) return;
            qtyInput.focus();
            qtyInput.select();
            try { qtyInput.click(); } catch (e) {}
        }, 40);
    }

    function askQty(item) {
        if (!item) return;
        pendingItem = item;
        editId = 0;
        var existing = stavke.filter(function (row) {
            return Number(row.id) && item.sifra && String(row.sifra || '') === String(item.sifra || '');
        })[0];
        var expected = existing ? existing.ocekivano : null;
        showQtyModal(item.naziv, item.sifra, 1, 'Ubaci', expected);
    }

    function openQty(row) {
        if (!row) return;
        pendingItem = null;
        editId = row.id;
        showQtyModal(row.naziv, row.sifra, row.kolicina || 1, 'Sačuvaj', row.ocekivano);
    }

    function readModalQty() {
        var raw = qtyInput ? qtyInput.value : '1';
        var n = parseInt(raw, 10);
        if (isNaN(n)) n = 1;
        return n;
    }

    function bumpModalQty(delta) {
        if (!qtyInput) return;
        var min = pendingItem ? 1 : 0;
        qtyInput.value = String(Math.max(min, readModalQty() + delta));
        qtyInput.focus();
        qtyInput.select();
    }

    function saveQty() {
        var qty = readModalQty();
        if (pendingItem) {
            var item = pendingItem;
            closeQty({ keepFocus: true });
            if (qtyInput) try { qtyInput.blur(); } catch (e) {}
            focusQuery(true);
            addItem(item, qty);
            return;
        }
        if (editId) {
            var id = editId;
            closeQty({ keepFocus: true });
            if (qtyInput) try { qtyInput.blur(); } catch (e) {}
            focusQuery(true);
            setQty(id, qty);
        }
    }

    input.addEventListener('input', function () {
        window.clearTimeout(timer);
        var q = (input.value || '').trim();
        if (q.length < 2) {
            hideSuggest();
            return;
        }
        timer = window.setTimeout(function () { searchArticles(q, false); }, 220);
    });
    input.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter') return;
        event.preventDefault();
        window.clearTimeout(timer);
        searchArticles(input.value, true);
    });
    input.addEventListener('mg-scanned', function (event) {
        window.clearTimeout(timer);
        var code = (event.detail && event.detail.code) || input.value;
        searchArticles(code, true);
    });

    list.addEventListener('click', function (event) {
        var btn = event.target.closest('[data-pp-delta], [data-pp-edit]');
        if (!btn) return;
        var rowEl = btn.closest('li[data-id]');
        if (!rowEl) return;
        var id = Number(rowEl.getAttribute('data-id'));
        var row = stavke.filter(function (item) { return Number(item.id) === id; })[0];
        if (!row) return;
        if (btn.hasAttribute('data-pp-edit')) {
            openQty(row);
            return;
        }
        var next = (Number(row.kolicina) || 0) + Number(btn.getAttribute('data-pp-delta') || 0);
        setQty(id, next);
    });

    if (modal) {
        modal.querySelectorAll('[data-pp-close]').forEach(function (el) {
            el.addEventListener('click', closeQty);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && modal && !modal.hidden) closeQty();
        });
    }
    if (qtySave) {
        qtySave.addEventListener('click', function () {
            saveQty();
        });
    }
    if (qtyInput) {
        qtyInput.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            saveQty();
        });
    }
    if (modal) {
        modal.querySelectorAll('[data-pp-modal-delta]').forEach(function (btn) {
            btn.addEventListener('click', function () {
                bumpModalQty(Number(btn.getAttribute('data-pp-modal-delta') || 0));
            });
        });
    }

    focusQuery();
}

(function initPrenosPick() {
    var root = document.getElementById('pkPrenosApp');
    if (!root) return;
    var need = parseInt(root.getAttribute('data-need') || '0', 10) || 0;
    var itemId = root.getAttribute('data-item-id') || '';
    var loc = root.getAttribute('data-loc') || '';
    var codes = [];
    try {
        codes = JSON.parse((document.getElementById('pkPrenosCodes') || {}).textContent || '[]') || [];
    } catch (err) { codes = []; }
    codes = codes.map(function (c) { return String(c || '').replace(/\s+/g, '').toLowerCase(); }).filter(Boolean);
    var gotEl = document.getElementById('pkPrenosGot');
    var viewEl = document.getElementById('pkPrenosQtyView');
    var ofEl = document.getElementById('pkPrenosQtyOf');
    var pickJson = document.getElementById('pkPrenosPickJson');
    var form = document.getElementById('pkPrenosForm');
    var validBtn = document.getElementById('pkPrenosValid');
    var scan = document.getElementById('pkPrenosScan');
    var msg = document.getElementById('pkPrenosMsg');
    var minus = document.getElementById('pkPrenosMinus');
    var plus = document.getElementById('pkPrenosPlus');
    var clearBtn = document.getElementById('pkPrenosClear');
    var got = need;

    function showMsg(text, ok) {
        if (!msg) return;
        if (!text) { msg.hidden = true; return; }
        msg.hidden = false;
        msg.textContent = text;
        msg.classList.toggle('is-ok', !!ok);
    }
    function clampGot(value) {
        var n = parseInt(value, 10);
        if (isNaN(n)) n = 0;
        return Math.max(0, Math.min(need, n));
    }
    function payload() {
        return [{
            key: itemId ? (itemId + ':' + loc) : loc,
            item_id: itemId,
            loc: loc,
            got: got,
            need: need,
            done: got > 0,
        }];
    }
    function sync() {
        if (gotEl) gotEl.value = String(got);
        if (viewEl) viewEl.textContent = String(got);
        if (ofEl) ofEl.textContent = need ? ('od ' + need) : '';
        if (pickJson) pickJson.value = JSON.stringify(payload());
        if (validBtn) validBtn.disabled = got < 1;
        if (gotEl) {
            gotEl.min = need ? '1' : '0';
            gotEl.max = String(need || 0);
        }
    }
    function setGot(value, fromScan) {
        got = clampGot(value);
        sync();
        if (fromScan) showMsg(got >= need && need > 0 ? 'Sve pokupljeno.' : 'Sken OK', true);
    }
    function applyScan(code) {
        var value = String(code || '').replace(/\s+/g, '').toLowerCase();
        if (!value) return;
        if (!codes.length) {
            showMsg('Artikal nema šifru/barkod za sken.');
            return;
        }
        if (codes.indexOf(value) === -1) {
            showMsg('Pogrešan artikal.');
            return;
        }
        if (got >= need && need > 0) {
            showMsg('Sve pokupljeno.', true);
            return;
        }
        setGot(got + 1, true);
    }

    if (minus) minus.addEventListener('click', function () { setGot(got - 1); showMsg(''); });
    if (plus) plus.addEventListener('click', function () { setGot(got + 1); showMsg(''); });
    if (gotEl) {
        gotEl.addEventListener('change', function () { setGot(gotEl.value); });
        gotEl.addEventListener('input', function () { setGot(gotEl.value); });
    }
    if (scan) {
        function consumeScan() {
            var value = (scan.value || '').trim();
            if (!value) return;
            applyScan(value);
            scan.value = '';
        }
        scan.addEventListener('keydown', function (event) {
            if (event.key !== 'Enter') return;
            event.preventDefault();
            consumeScan();
        });
        scan.addEventListener('change', consumeScan);
        scan.addEventListener('mg-scanned', function (event) {
            applyScan((event.detail && event.detail.code) || scan.value);
            scan.value = '';
        });
        scan.addEventListener('input', function () {
            var value = scan.value.trim();
            if (value.length < 3) return;
            window.clearTimeout(scan._pkTimer);
            scan._pkTimer = window.setTimeout(function () {
                if (scan.value.trim() !== value) return;
                applyScan(value);
                scan.value = '';
            }, 60);
        });
    }
    if (form) {
        form.addEventListener('submit', function (event) {
            sync();
            if (got < 1) {
                event.preventDefault();
                showMsg('Unesi količinu za prenos ili ukloni iz lokacije.');
                return;
            }
            var msgText = got < need
                ? 'Prenijeti ' + got + ' od ' + need + ' kom? Višak ostaje na lokaciji.'
                : 'Validatovati prenos u MP? Skida se sa stanja.';
            if (!window.confirm(msgText)) event.preventDefault();
        });
    }
    var cancelForm = document.getElementById('pkPrenosCancelForm');
    if (cancelForm) {
        cancelForm.addEventListener('submit', function (event) {
            if (!window.confirm('Otkazati prenos u MP? Artikal ostaje na lokaciji, ništa se ne prenosi.')) {
                event.preventDefault();
            }
        });
    }
    if (clearBtn) {
        clearBtn.addEventListener('click', function () {
            if (!itemId || !loc) return;
            var password = window.prompt(
                'Artikal fizički nema na lokaciji ' + loc + '.\n\n' +
                'Ukloniti iz lokacije — količine ovog artikla na TOJ lokaciji idu na 0. ' +
                'Druge lokacije se ne diraju. Sa sajta ide tek ako nema ništa nigdje.\n' +
                'Unesi šifru:'
            );
            if (password === null) return;
            if (String(password).trim() !== 'admin') {
                window.alert('Pogrešna šifra.');
                return;
            }
            clearBtn.disabled = true;
            var csrf = root.querySelector('[name=csrfmiddlewaretoken]');
            var body = new URLSearchParams();
            body.set('action', 'pick_ocisti');
            body.set('item_id', String(itemId));
            body.set('loc', loc);
            body.set('lozinka', String(password).trim());
            if (csrf) body.set('csrfmiddlewaretoken', csrf.value);
            fetch(window.location.pathname, {
                method: 'POST',
                headers: {
                    'X-Requested-With': 'XMLHttpRequest',
                    'X-CSRFToken': csrf ? csrf.value : '',
                },
                body: body,
                credentials: 'same-origin',
            }).then(function (res) { return res.json().then(function (data) { return { ok: res.ok, data: data }; }); })
              .then(function (result) {
                  if (!result.data || !result.data.ok) {
                      clearBtn.disabled = false;
                      window.alert((result.data && result.data.error) || 'Lokacija nije očišćena.');
                      return;
                  }
                  if (result.data.redirect) {
                      window.location.href = result.data.redirect;
                      return;
                  }
                  window.location.reload();
              }).catch(function () {
                  clearBtn.disabled = false;
                  window.alert('Lokacija nije očišćena.');
              });
        });
    }
    sync();
    if (scan) window.setTimeout(function () { scan.focus(); }, 40);
})();
