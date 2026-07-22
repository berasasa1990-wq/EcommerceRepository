/**
 * Edit mode: klik na natpis/dugme → popover (tekst + boje).
 * Trajno snima u SiteSettings. Nema stalne palete.
 * U edit modu linkovi na artikle su blokirani da klik ne „prodje” ispod.
 */
(function () {
    'use strict';

    function init() {
        const root = document.getElementById('staffSiteEditorRoot');
        const popover = document.getElementById('staffEditPopover');
        if (!root || !popover) return;

        document.body.classList.add('staff-edit-mode-on');

        const saveUrl = root.getAttribute('data-save-url') || '/nalog/site-edit/';
        const popTitle = document.getElementById('staffEditPopoverTitle');
        const popBody = document.getElementById('staffEditPopoverBody');
        const popStatus = document.getElementById('staffEditPopoverStatus');
        const btnSave = document.getElementById('staffEditPopoverSave');
        const btnCancel = document.getElementById('staffEditPopoverCancel');
        const btnClose = document.getElementById('staffEditPopoverClose');

        let activeEl = null;
        let activeKind = null;
        let activeContact = null;
        let open = false;

        function ds(name, fallback) {
            const camel = name.replace(/-([a-z])/g, function (_, c) {
                return c.toUpperCase();
            });
            return root.dataset[camel] || root.getAttribute('data-' + name) || fallback || '';
        }

        function getCsrf() {
            const m = document.cookie.match(/csrftoken=([^;]+)/);
            if (m) return decodeURIComponent(m[1]);
            const meta = document.querySelector('meta[name="csrf-token"]');
            return meta ? meta.getAttribute('content') || '' : '';
        }

        function setStatus(msg, isError) {
            if (!popStatus) return;
            if (!msg) {
                popStatus.hidden = true;
                popStatus.textContent = '';
                return;
            }
            popStatus.hidden = false;
            popStatus.textContent = msg;
            popStatus.classList.toggle('is-error', !!isError);
            popStatus.classList.toggle('is-ok', !isError);
        }

        function escapeAttr(s) {
            return String(s)
                .replace(/&/g, '&amp;')
                .replace(/"/g, '&quot;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }

        function normalizeHex(v) {
            v = String(v || '').trim();
            if (/^#[0-9A-Fa-f]{6}$/.test(v)) return v;
            if (/^#[0-9A-Fa-f]{3}$/.test(v)) {
                return '#' + v[1] + v[1] + v[2] + v[2] + v[3] + v[3];
            }
            return '#5BB805';
        }

        function fieldRow(label, html) {
            return (
                '<label class="staff-edit-field">' +
                '<span class="staff-edit-field-label">' +
                label +
                '</span>' +
                html +
                '</label>'
            );
        }

        function displayText(el, value) {
            const v = value == null ? '' : String(value);
            el.textContent = v;
            el.classList.toggle('is-empty-edit', !v.trim());
            // Ne sakrivaj prazan naslov u edit modu
            el.hidden = false;
        }

        function markEmptyPlaceholders() {
            document.querySelectorAll('[data-site-edit]').forEach(function (el) {
                const t = (el.textContent || '').trim();
                el.classList.toggle('is-empty-edit', !t);
            });
        }

        async function saveUpdates(updates) {
            const body = new URLSearchParams({
                updates_json: JSON.stringify(updates),
            });
            const res = await fetch(saveUrl, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': getCsrf(),
                    'X-Requested-With': 'XMLHttpRequest',
                },
                body: body.toString(),
                credentials: 'same-origin',
            });
            let data = {};
            try {
                data = await res.json();
            } catch (e) {
                throw new Error('Neispravan odgovor servera (' + res.status + ').');
            }
            if (!res.ok || !data.ok) {
                throw new Error(data.message || 'Snimanje nije uspjelo.');
            }
            if (data.theme_css) {
                let style = document.getElementById('site-theme');
                if (!style) {
                    style = document.createElement('style');
                    style.id = 'site-theme';
                    document.head.appendChild(style);
                }
                style.textContent = ':root{ ' + data.theme_css + ' }';
            }
            return data;
        }

        function markTargets() {
            document.querySelectorAll('[data-site-edit]').forEach(function (el) {
                el.classList.add('site-edit-target');
                el.setAttribute('title', 'Klikni za uređivanje natpisa');
            });

            document.querySelectorAll('.product-card-add-btn, .btn-add-to-bag').forEach(function (el) {
                if (el.disabled) return;
                if (el.classList.contains('btn-cart-checkout')) return;
                const t = (el.textContent || '').toLowerCase();
                if (t.indexOf('narudžbu') !== -1 || t.indexOf('narudzbu') !== -1) return;
                if (t.indexOf('pošalji') !== -1 || t.indexOf('posalji') !== -1) return;
                el.classList.add('site-edit-target', 'site-edit-target--btn');
                el.setAttribute('data-site-edit-kind', 'cart');
                el.setAttribute('title', 'Klikni za uređivanje dugmeta');
            });

            document.querySelectorAll('.btn-banner').forEach(function (el) {
                el.classList.add('site-edit-target', 'site-edit-target--btn');
                el.setAttribute('data-site-edit-kind', 'banner');
                el.setAttribute('title', 'Klikni za boju banner dugmeta');
            });

            [
                ['.contact-float--whatsapp', 'whatsapp'],
                ['.contact-float--viber', 'viber'],
                ['.contact-float--messenger', 'messenger'],
            ].forEach(function (pair) {
                document.querySelectorAll(pair[0]).forEach(function (el) {
                    el.classList.add('site-edit-target', 'site-edit-target--btn');
                    el.setAttribute('data-site-edit-kind', 'contact');
                    el.setAttribute('data-site-edit-contact', pair[1]);
                });
            });

            markEmptyPlaceholders();
        }

        function closePopover() {
            open = false;
            popover.hidden = true;
            popover.style.display = 'none';
            if (activeEl) activeEl.classList.remove('is-editing');
            activeEl = null;
            activeKind = null;
            activeContact = null;
            if (popBody) popBody.innerHTML = '';
            setStatus('');
        }

        function positionPopover(anchor) {
            popover.style.display = 'block';
            popover.hidden = false;
            // force layout
            void popover.offsetWidth;
            const rect = anchor.getBoundingClientRect();
            const pad = 12;
            const pw = popover.offsetWidth || 300;
            const ph = popover.offsetHeight || 220;
            let top = rect.bottom + pad;
            let left = rect.left;
            if (left + pw > window.innerWidth - 12) left = window.innerWidth - pw - 12;
            if (left < 12) left = 12;
            if (top + ph > window.innerHeight - 12) {
                top = Math.max(12, rect.top - ph - pad);
            }
            if (top < 12) top = 12;
            popover.style.top = top + 'px';
            popover.style.left = left + 'px';
        }

        function openTextPopover(el) {
            const field = el.getAttribute('data-site-edit');
            if (!field) return;
            activeEl = el;
            activeKind = 'text';
            el.classList.add('is-editing');
            // stvarni tekst (ne CSS placeholder)
            const current = (el.textContent || '').trim();
            const ph = el.getAttribute('data-site-edit-placeholder') || '';
            if (popTitle) popTitle.textContent = 'Uredi natpis';
            popBody.innerHTML =
                fieldRow(
                    'Tekst',
                    '<input type="text" class="staff-edit-input" id="staffEditText" value="' +
                        escapeAttr(current) +
                        '" maxlength="200" placeholder="' +
                        escapeAttr(ph) +
                        '">'
                ) +
                '<p class="staff-edit-help">Ostavi prazno da sakriješ natpis. Sačuvaj da se trajno upiše.</p>';
            open = true;
            positionPopover(el);
            const input = document.getElementById('staffEditText');
            if (input) {
                input.focus();
                input.select();
            }
        }

        function buttonVisibleText(el) {
            const clone = el.cloneNode(true);
            clone.querySelectorAll('svg, .product-card-add-btn__icon').forEach(function (n) {
                n.remove();
            });
            return (clone.textContent || '').trim();
        }

        function openCartPopover(el) {
            activeEl = el;
            activeKind = 'cart';
            el.classList.add('is-editing');
            const text = buttonVisibleText(el) || ds('tekst-korpa', 'Dodaj u korpu');
            const color = normalizeHex(ds('boja-korpa', '#5BB805'));
            const hover = normalizeHex(ds('boja-korpa-hover', '#4fa104'));
            if (popTitle) popTitle.textContent = 'Uredi dugme';
            popBody.innerHTML =
                fieldRow(
                    'Naziv dugmeta',
                    '<input type="text" class="staff-edit-input" id="staffEditText" value="' +
                        escapeAttr(text) +
                        '" maxlength="40">'
                ) +
                fieldRow(
                    'Boja',
                    '<input type="color" class="staff-edit-color" id="staffEditColor" value="' +
                        escapeAttr(color) +
                        '">'
                ) +
                fieldRow(
                    'Boja (hover)',
                    '<input type="color" class="staff-edit-color" id="staffEditColorHover" value="' +
                        escapeAttr(hover) +
                        '">'
                );
            open = true;
            positionPopover(el);
            document.getElementById('staffEditText')?.focus();
        }

        function openBannerPopover(el) {
            activeEl = el;
            activeKind = 'banner';
            el.classList.add('is-editing');
            const color = normalizeHex(ds('boja-banner', '#ff9500'));
            const hover = normalizeHex(ds('boja-banner-hover', '#e68600'));
            if (popTitle) popTitle.textContent = 'Uredi banner dugme';
            popBody.innerHTML =
                fieldRow(
                    'Boja',
                    '<input type="color" class="staff-edit-color" id="staffEditColor" value="' +
                        escapeAttr(color) +
                        '">'
                ) +
                fieldRow(
                    'Boja (hover)',
                    '<input type="color" class="staff-edit-color" id="staffEditColorHover" value="' +
                        escapeAttr(hover) +
                        '">'
                );
            open = true;
            positionPopover(el);
        }

        function openContactPopover(el) {
            activeEl = el;
            activeKind = 'contact';
            activeContact = el.getAttribute('data-site-edit-contact') || 'whatsapp';
            el.classList.add('is-editing');
            const map = {
                whatsapp: {
                    label: 'WhatsApp',
                    ds: 'boja-wa',
                    field: 'kontakt_boja_whatsapp',
                    def: '#25d366',
                },
                viber: {
                    label: 'Viber',
                    ds: 'boja-viber',
                    field: 'kontakt_boja_viber',
                    def: '#665cac',
                },
                messenger: {
                    label: 'Messenger',
                    ds: 'boja-msg',
                    field: 'kontakt_boja_messenger',
                    def: '#0084ff',
                },
            };
            const meta = map[activeContact] || map.whatsapp;
            const color = normalizeHex(ds(meta.ds, meta.def));
            if (popTitle) popTitle.textContent = 'Uredi ' + meta.label;
            popBody.innerHTML = fieldRow(
                'Boja dugmeta',
                '<input type="color" class="staff-edit-color" id="staffEditColor" value="' +
                    escapeAttr(color) +
                    '" data-field="' +
                    meta.field +
                    '">'
            );
            open = true;
            positionPopover(el);
        }

        function applyCartText(text) {
            root.setAttribute('data-tekst-korpa', text);
            root.dataset.tekstKorpa = text;
            document.querySelectorAll('.product-card-add-btn:not(:disabled)').forEach(function (btn) {
                let textSpan = null;
                btn.querySelectorAll('span').forEach(function (s) {
                    if (!s.classList.contains('product-card-add-btn__icon')) textSpan = s;
                });
                if (textSpan) textSpan.textContent = text;
            });
            document.querySelectorAll('#mainAddToCartBtn').forEach(function (btn) {
                btn.textContent = text;
            });
        }

        async function onSave(ev) {
            if (ev) {
                ev.preventDefault();
                ev.stopPropagation();
            }
            if (!activeKind || !activeEl) return;
            try {
                setStatus('Snimam…');
                if (btnSave) btnSave.disabled = true;
                const updates = {};
                if (activeKind === 'text') {
                    const field = activeEl.getAttribute('data-site-edit');
                    // dozvoli prazan string
                    const val = document.getElementById('staffEditText')
                        ? document.getElementById('staffEditText').value
                        : '';
                    updates[field] = val; // ne .trim() prije slanja — trim u backendu ok
                    updates[field] = String(val);
                } else if (activeKind === 'cart') {
                    let val = document.getElementById('staffEditText')
                        ? document.getElementById('staffEditText').value.trim()
                        : '';
                    if (!val) val = 'Dodaj u korpu';
                    const c = document.getElementById('staffEditColor')?.value;
                    const h = document.getElementById('staffEditColorHover')?.value;
                    updates.tekst_dugme_korpa = val;
                    if (c) updates.boja_dugme_korpa = c;
                    if (h) updates.boja_dugme_korpa_hover = h;
                } else if (activeKind === 'banner') {
                    const c = document.getElementById('staffEditColor')?.value;
                    const h = document.getElementById('staffEditColorHover')?.value;
                    if (c) updates.boja_dugme_banner = c;
                    if (h) updates.boja_dugme_banner_hover = h;
                } else if (activeKind === 'contact') {
                    const input = document.getElementById('staffEditColor');
                    const field = input && input.getAttribute('data-field');
                    if (field && input) updates[field] = input.value;
                }

                const data = await saveUpdates(updates);
                const saved = data.saved || updates;

                if (Object.prototype.hasOwnProperty.call(saved, 'tekst_dugme_korpa')) {
                    applyCartText(saved.tekst_dugme_korpa);
                }
                if (saved.boja_dugme_korpa) {
                    root.setAttribute('data-boja-korpa', saved.boja_dugme_korpa);
                    root.dataset.bojaKorpa = saved.boja_dugme_korpa;
                }
                if (saved.boja_dugme_korpa_hover) {
                    root.setAttribute('data-boja-korpa-hover', saved.boja_dugme_korpa_hover);
                    root.dataset.bojaKorpaHover = saved.boja_dugme_korpa_hover;
                }
                if (saved.boja_dugme_banner) {
                    root.setAttribute('data-boja-banner', saved.boja_dugme_banner);
                    root.dataset.bojaBanner = saved.boja_dugme_banner;
                }
                if (saved.boja_dugme_banner_hover) {
                    root.setAttribute('data-boja-banner-hover', saved.boja_dugme_banner_hover);
                    root.dataset.bojaBannerHover = saved.boja_dugme_banner_hover;
                }
                if (saved.kontakt_boja_whatsapp) {
                    root.setAttribute('data-boja-wa', saved.kontakt_boja_whatsapp);
                    root.dataset.bojaWa = saved.kontakt_boja_whatsapp;
                }
                if (saved.kontakt_boja_viber) {
                    root.setAttribute('data-boja-viber', saved.kontakt_boja_viber);
                    root.dataset.bojaViber = saved.kontakt_boja_viber;
                }
                if (saved.kontakt_boja_messenger) {
                    root.setAttribute('data-boja-msg', saved.kontakt_boja_messenger);
                    root.dataset.bojaMsg = saved.kontakt_boja_messenger;
                }

                Object.keys(saved).forEach(function (field) {
                    if (field.indexOf('boja') !== -1) return;
                    if (field.indexOf('tekst_dugme') === 0) return;
                    document.querySelectorAll('[data-site-edit="' + field + '"]').forEach(function (node) {
                        displayText(node, saved[field]);
                    });
                });

                setStatus('Sačuvano ✓');
                // Odgodi zatvaranje da se ne „propusti” klik na artikal ispod
                setTimeout(function () {
                    closePopover();
                }, 500);
            } catch (err) {
                setStatus(err.message || 'Greška pri snimanju', true);
            } finally {
                if (btnSave) btnSave.disabled = false;
            }
        }

        function eventElement(ev) {
            let t = ev.target;
            if (!t) return null;
            if (t.nodeType === 3) t = t.parentElement;
            return t;
        }

        // Blokiraj navigaciju na artikle u edit modu
        document.addEventListener(
            'click',
            function (ev) {
                if (!document.body.classList.contains('staff-edit-mode-on')) return;
                const t = eventElement(ev);
                if (!t || !t.closest) return;

                // unutar popovera — OK
                if (popover.contains(t)) return;

                const kindEl = t.closest('[data-site-edit-kind]');
                const textEl = t.closest('[data-site-edit]');

                if (kindEl) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
                    closePopover();
                    const kind = kindEl.getAttribute('data-site-edit-kind');
                    if (kind === 'cart') openCartPopover(kindEl);
                    else if (kind === 'banner') openBannerPopover(kindEl);
                    else if (kind === 'contact') openContactPopover(kindEl);
                    return;
                }

                if (textEl) {
                    ev.preventDefault();
                    ev.stopPropagation();
                    if (ev.stopImmediatePropagation) ev.stopImmediatePropagation();
                    closePopover();
                    openTextPopover(textEl);
                    return;
                }

                // Artikli / brendovi / vlogovi — normalan ulazak (edit alati su na detail stranici)
                if (open && !popover.contains(t)) {
                    closePopover();
                }
            },
            true
        );

        // Blokiraj pointer na karticama (backup)
        document.addEventListener(
            'submit',
            function (ev) {
                if (!document.body.classList.contains('staff-edit-mode-on')) return;
                const form = ev.target;
                if (
                    form &&
                    (form.classList.contains('add-to-cart-form') ||
                        form.classList.contains('product-detail-variation-form'))
                ) {
                    ev.preventDefault();
                    ev.stopPropagation();
                }
            },
            true
        );

        if (btnSave) {
            btnSave.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                onSave(e);
            });
        }
        if (btnCancel) {
            btnCancel.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                closePopover();
            });
        }
        if (btnClose) {
            btnClose.addEventListener('click', function (e) {
                e.preventDefault();
                e.stopPropagation();
                closePopover();
            });
        }

        // Enter u inputu = save
        popover.addEventListener('keydown', function (e) {
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                onSave(e);
            } else if (e.key === 'Escape') {
                e.preventDefault();
                closePopover();
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && open) closePopover();
        });

        window.addEventListener(
            'resize',
            function () {
                if (open && activeEl) positionPopover(activeEl);
            },
            { passive: true }
        );
        window.addEventListener(
            'scroll',
            function () {
                if (open && activeEl) positionPopover(activeEl);
            },
            { passive: true, capture: true }
        );

        markTargets();
        [200, 600, 1500, 3000].forEach(function (ms) {
            setTimeout(markTargets, ms);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
