(function ($) {
    'use strict';

    var BUNDLE_TIP = 'bundle';
    var QTY_DEAL_TIP = 'qty_deal';
    var PONUDA_TIP = 'ponuda';
    var AI_PRODAJA_TIP = 'ai_prodaja';

    /** Polja dozvoljena po tipu (sve ostalo se sakriva). */
    var FIELDS_BY_TIP = {};
    FIELDS_BY_TIP[BUNDLE_TIP] = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        popust_postotak: 1,
        bundle_trigger: 1,
        // artikal / kategorija ovisno o triggeru
        tekst_dugmeta: 1,
        boja_dugmeta: 1,
        boja_opisa: 1,
        popup_delay_seconds: 1,
        za_prijavljene: 1,
        za_neprijavljene: 1,
        ponovo_poslije_dana: 1,
    };
    FIELDS_BY_TIP[QTY_DEAL_TIP] = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        artikal: 1,
        qty_2_popust: 1,
        qty_3_popust: 1,
        qty_4_popust: 1,
        qty_5_popust: 1,
        qty_6_popust: 1,
        tekst_dugmeta: 1,
        boja_dugmeta: 1,
        boja_opisa: 1,
        za_prijavljene: 1,
        za_neprijavljene: 1,
        ponovo_poslije_dana: 1,
    };
    FIELDS_BY_TIP[PONUDA_TIP] = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        artikal: 1,
        popust_postotak: 1,
        gratis_artikal: 1,
    };
    FIELDS_BY_TIP[AI_PRODAJA_TIP] = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        browse_interest_popup_aktivan: 1,
        browse_interest_popust: 1,
        product_dwell_popup_aktivan: 1,
        product_dwell_popust: 1,
        product_dwell_flash_seconds: 1,
        product_dwell_sale_pulse: 1,
        product_dwell_tag_text: 1,
        product_dwell_timer_label: 1,
        product_dwell_catalog_label: 1,
        product_dwell_boja_box: 1,
        product_dwell_boja_box2: 1,
        product_dwell_boja_border: 1,
        product_dwell_boja_accent: 1,
        product_dwell_boja_tag_tekst: 1,
        product_dwell_boja_tag_bg: 1,
        product_dwell_boja_timer_label: 1,
        product_dwell_boja_timer_bg: 1,
        product_dwell_boja_timer_tekst: 1,
        product_dwell_boja_stara_cijena: 1,
        product_dwell_boja_nova_cijena: 1,
        product_dwell_boja_nova_cijena_pulse: 1,
        product_dwell_boja_badge_bg: 1,
        product_dwell_boja_badge_tekst: 1,
        product_dwell_boja_kartica_bg: 1,
        product_dwell_boja_kartica_bg2: 1,
        product_dwell_boja_kartica_border: 1,
        product_dwell_boja_kartica_stara: 1,
        product_dwell_boja_kartica_nova: 1,
        product_dwell_boja_kartica_badge_bg: 1,
        product_dwell_boja_kartica_badge_tekst: 1,
        product_dwell_boja_kartica_label: 1,
    };

    var AI_FIELD = {
        browse_interest_popup_aktivan: 1,
        browse_interest_popust: 1,
        product_dwell_popup_aktivan: 1,
        product_dwell_popust: 1,
        product_dwell_flash_seconds: 1,
        product_dwell_sale_pulse: 1,
        product_dwell_tag_text: 1,
        product_dwell_timer_label: 1,
        product_dwell_catalog_label: 1,
        product_dwell_boja_box: 1,
        product_dwell_boja_box2: 1,
        product_dwell_boja_border: 1,
        product_dwell_boja_accent: 1,
        product_dwell_boja_tag_tekst: 1,
        product_dwell_boja_tag_bg: 1,
        product_dwell_boja_timer_label: 1,
        product_dwell_boja_timer_bg: 1,
        product_dwell_boja_timer_tekst: 1,
        product_dwell_boja_stara_cijena: 1,
        product_dwell_boja_nova_cijena: 1,
        product_dwell_boja_nova_cijena_pulse: 1,
        product_dwell_boja_badge_bg: 1,
        product_dwell_boja_badge_tekst: 1,
        product_dwell_boja_kartica_bg: 1,
        product_dwell_boja_kartica_bg2: 1,
        product_dwell_boja_kartica_border: 1,
        product_dwell_boja_kartica_stara: 1,
        product_dwell_boja_kartica_nova: 1,
        product_dwell_boja_kartica_badge_bg: 1,
        product_dwell_boja_kartica_badge_tekst: 1,
        product_dwell_boja_kartica_label: 1,
    };

    function tipVal() {
        return ($('#id_tip').val() || '').toString();
    }

    function triggerVal() {
        return ($('#id_bundle_trigger').val() || 'delay').toString();
    }

    function rowFieldName($el) {
        var $row = $el.hasClass('form-row') ? $el : $el.closest('.form-row');
        if (!$row.length) {
            $row = $el;
        }
        var cls = (($row.attr('class') || '') + ' ' + ($el.attr('class') || '')).toString();
        var m = cls.match(/(?:^|\s)field-([a-z0-9_]+)(?:\s|$)/i);
        if (m) {
            return m[1];
        }
        // Fallback: id inputa id_FIELD
        var $input = $row.find('[id^="id_"]').first();
        if ($input.length) {
            var id = $input.attr('id') || '';
            if (id.indexOf('id_') === 0) {
                return id.slice(3).split('-')[0];
            }
        }
        return '';
    }

    function setHidden($el, hide) {
        if (!$el || !$el.length) {
            return;
        }
        if (hide) {
            $el.addClass('akcija-section-hidden').attr('hidden', 'hidden');
            $el.css('display', 'none');
        } else {
            $el.removeClass('akcija-section-hidden').removeAttr('hidden');
            $el.css('display', '');
        }
    }

    function allowedFieldsForTip(tip) {
        var base = $.extend({}, FIELDS_BY_TIP[tip] || {
            naziv: 1,
            tip: 1,
            aktivan: 1,
            redoslijed: 1,
        });
        if (tip === BUNDLE_TIP) {
            var tr = triggerVal();
            if (tr === 'trigger_product') {
                base.artikal = 1;
            }
            if (tr === 'category') {
                base.kategorija = 1;
            }
        }
        return base;
    }

    function formRoot() {
        return $('#akcija_form, form#akcija_form, #content-main form').first();
    }

    function allFormRows() {
        return formRoot().find('.form-row');
    }

    function allFieldsets() {
        return formRoot().find('fieldset');
    }

    function allInlines() {
        // Django tabular/stacked + custom wrappers
        return formRoot().find(
            '.inline-group, .js-inline-admin-formset, ' +
            '[id*="bundle_line"], [id*="dwell"], [id*="productdwell"], [id*="qty_tier"]'
        ).filter(function () {
            // samo top-level grupe
            return $(this).closest('.inline-group').length === 0 || $(this).hasClass('inline-group') || $(this).hasClass('js-inline-admin-formset');
        });
    }

    function isBundleInline($el) {
        var s = (($el.attr('id') || '') + ' ' + ($el.attr('class') || '') + ' ' + $el.text().slice(0, 200)).toLowerCase();
        return s.indexOf('bundle') !== -1 || s.indexOf('stavk') !== -1;
    }

    function isDwellInline($el) {
        var s = (($el.attr('id') || '') + ' ' + ($el.attr('class') || '') + ' ' + $el.find('h2, h3, caption').first().text()).toLowerCase();
        return (
            s.indexOf('dwell') !== -1 ||
            s.indexOf('ai dwell') !== -1 ||
            s.indexOf('productdwell') !== -1 ||
            s.indexOf('dwell artikal') !== -1
        );
    }

    function isQtyInline($el) {
        var s = (($el.attr('id') || '') + ' ' + ($el.attr('class') || '')).toLowerCase();
        return s.indexOf('qty') !== -1 || s.indexOf('tier') !== -1;
    }

    function toggleAkcijaFields() {
        var tip = tipVal();
        var allowed = allowedFieldsForTip(tip);
        var isBundle = tip === BUNDLE_TIP;
        var isQty = tip === QTY_DEAL_TIP;
        var isPonuda = tip === PONUDA_TIP;
        var isAi = tip === AI_PRODAJA_TIP;

        // 1) Polja — samo dozvoljena
        allFormRows().each(function () {
            var $row = $(this);
            var name = rowFieldName($row);
            if (!name) {
                return;
            }
            // Nikad AI polja van AI tipa
            if (!isAi && AI_FIELD[name]) {
                setHidden($row, true);
                return;
            }
            setHidden($row, !allowed[name]);
        });

        // 2) Fieldseti — sakrij ako nema nijednog dozvoljenog polja
        allFieldsets().each(function () {
            var $fs = $(this);
            var anyAllowed = false;
            var anyAi = false;
            $fs.find('.form-row').each(function () {
                var n = rowFieldName($(this));
                if (!n) {
                    return;
                }
                if (AI_FIELD[n]) {
                    anyAi = true;
                }
                if (allowed[n]) {
                    anyAllowed = true;
                }
            });
            // Fieldset samo s AI poljima van AI moda → sakrij
            if (!isAi && anyAi && !anyAllowed) {
                setHidden($fs, true);
                return;
            }
            // Prazan fieldset (sva polja sakrivena) → sakrij
            setHidden($fs, !anyAllowed);
        });

        // 3) Inline tabele
        // Pronađi sve inline grupe u formi
        var $inlines = formRoot().find('.inline-group, .js-inline-admin-formset');
        if (!$inlines.length) {
            $inlines = $('.inline-group, .js-inline-admin-formset');
        }
        $inlines.each(function () {
            var $g = $(this);
            var show = false;
            if (isBundle && isBundleInline($g)) {
                show = true;
            } else if (isAi && isDwellInline($g)) {
                show = true;
            } else if (isQty && isQtyInline($g)) {
                show = true;
            } else if (!isBundle && !isAi && !isQty) {
                show = false;
            } else {
                // Nepoznat inline — sakrij van bundlea
                show = false;
            }
            // + Ponuda i ostalo: nikad dwell
            if (isPonuda || isQty) {
                if (isDwellInline($g) || isBundleInline($g)) {
                    show = false;
                }
            }
            if (isBundle && isDwellInline($g)) {
                show = false;
            }
            if (isAi && (isBundleInline($g) || isQtyInline($g))) {
                show = false;
            }
            setHidden($g, !show);
        });

        // 4) Dodatno: sakrij bilo šta s "AI dwell" u naslovu van AI moda
        if (!isAi) {
            formRoot().find('fieldset, .module, .inline-group').each(function () {
                var t = (
                    $(this).find('h2, h3, .inline-heading, caption, legend').first().text() || ''
                ).toLowerCase();
                if (
                    t.indexOf('ai dwell') !== -1 ||
                    t.indexOf('ai prodaja') !== -1 ||
                    t.indexOf('dwell') !== -1
                ) {
                    // Ne diraj + Ponuda sekciju koja spominje "AI dwell stil" u description
                    var hasOnlyDesc =
                        $(this).find('.form-row').length === 0 ||
                        $(this).find('.form-row.field-product_dwell_popup_aktivan, .form-row.field-browse_interest_popup_aktivan').length > 0 ||
                        isDwellInline($(this));
                    if (hasOnlyDesc || t.indexOf('ai dwell') === 0 || t.indexOf('ai prodaja') === 0) {
                        setHidden($(this), true);
                    }
                    if (t.indexOf('ai dwell') !== -1 && t.indexOf('unesi') === -1) {
                        setHidden($(this), true);
                    }
                    if (t.indexOf('ai prodaja') !== -1) {
                        setHidden($(this), true);
                    }
                }
            });
        }

        // 5) Osiguraj da su dozvoljena polja stvarno vidljiva (fieldset + row)
        Object.keys(allowed).forEach(function (name) {
            var $row = formRoot().find('.form-row.field-' + name + ', .field-' + name);
            if ($row.length) {
                setHidden($row, false);
                setHidden($row.closest('fieldset'), false);
            }
        });
    }

    function bind() {
        if (!$('#id_tip').length) {
            return;
        }
        $(document)
            .off('change.akcijaTip', '#id_tip')
            .on('change.akcijaTip', '#id_tip', toggleAkcijaFields);
        $(document)
            .off('change.akcijaTip', '#id_bundle_trigger')
            .on('change.akcijaTip', '#id_bundle_trigger', toggleAkcijaFields);

        toggleAkcijaFields();
        // Autocomplete / related widgets render late
        [50, 150, 400, 800, 1500].forEach(function (ms) {
            window.setTimeout(toggleAkcijaFields, ms);
        });
    }

    $(bind);
    $(window).on('load', function () {
        window.setTimeout(toggleAkcijaFields, 50);
        window.setTimeout(toggleAkcijaFields, 300);
    });
})(django.jQuery || window.jQuery);
