(function ($) {
    'use strict';

    var BUNDLE_TIP = 'bundle';
    var QTY_DEAL_TIP = 'qty_deal';
    var PONUDA_TIP = 'ponuda';
    var AI_PRODAJA_TIP = 'ai_prodaja';

    // Dozvoljena polja za Pop-up bundle
    var BUNDLE_ALWAYS = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        bundle_artikli: 1,
        popust_postotak: 1,
        bundle_trigger: 1,
        tekst_dugmeta: 1,
        boja_dugmeta: 1,
        boja_opisa: 1,
        popup_delay_seconds: 1,
        za_prijavljene: 1,
        za_neprijavljene: 1,
        ponovo_poslije_dana: 1,
    };

    var BUNDLE_FORBIDDEN = [
        'slika',
        'preview_slika',
        'gratis_artikal',
        'gratis_popup',
        'prag_korpe_km',
        'deal_vrsta',
        'pocetak',
        'trajanje_sati',
        'link_dugmeta',
    ];

    // Kupi više (količinski %): artikal + polja 2/3/4/5/6 kom → %
    var QTY_DEAL_FIELDS = {
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

    // + Ponuda: samo trigger + opcionalni % + ponuda artikal
    var PONUDA_FIELDS = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        artikal: 1,
        gratis_artikal: 1,
        popust_postotak: 1,
    };

    // Osnovna + sva AI polja (kao stari zasebni meni)
    var AI_BASE = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
    };
    var AI_FIELDS = {
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
        return m ? m[1] : '';
    }

    function setVisible($el, show) {
        if (!$el || !$el.length) {
            return;
        }
        if (show) {
            $el.show().css('display', '');
            $el.removeClass('akcija-field-hidden');
        } else {
            $el.hide().css('display', 'none');
            $el.addClass('akcija-field-hidden');
        }
    }

    function hideField(name) {
        setVisible($('.form-row.field-' + name + ', .field-' + name), false);
    }

    function showField(name) {
        setVisible($('.form-row.field-' + name + ', .field-' + name), true);
    }

    function bundleAllows(name, trigger) {
        if (!name) {
            return false;
        }
        if (BUNDLE_FORBIDDEN.indexOf(name) !== -1) {
            return false;
        }
        if (BUNDLE_ALWAYS[name]) {
            return true;
        }
        if (name === 'artikal') {
            return trigger === 'trigger_product';
        }
        if (name === 'kategorija') {
            return trigger === 'category';
        }
        return false;
    }

    function findInline(substr, className) {
        var $g = $();
        if (className) {
            $g = $('.inline-group.' + className + ', .inline-group .' + className).closest('.inline-group');
            if (!$g.length) {
                $g = $('.' + className).closest('.inline-group, .js-inline-admin-formset');
            }
            if (!$g.length) {
                $g = $('.' + className);
            }
        }
        if (!$g.length) {
            $g = $('.inline-group').filter(function () {
                var id = (this.id || '') + ' ' + ($(this).attr('class') || '');
                return id.toLowerCase().indexOf(substr) !== -1;
            });
        }
        if (!$g.length) {
            $g = $('.inline-group[id*="' + substr + '"]');
        }
        return $g;
    }

    function fieldsetByHeading(needles) {
        return $('fieldset').filter(function () {
            var t = ($(this).find('h2, .fieldset-heading, legend').first().text() || '').toLowerCase();
            for (var i = 0; i < needles.length; i++) {
                if (t.indexOf(needles[i]) !== -1) {
                    return true;
                }
            }
            return false;
        });
    }

    function toggleAkcijaFields() {
        var tip = tipVal();
        var isBundle = tip === BUNDLE_TIP;
        var isQtyDeal = tip === QTY_DEAL_TIP;
        var isPonuda = tip === PONUDA_TIP;
        var isAi = tip === AI_PRODAJA_TIP;
        var trigger = triggerVal();

        $('#content-main .form-row, #akcija_form .form-row, form .aligned .form-row').each(
            function () {
                var $row = $(this);
                var name = rowFieldName($row);
                if (!name) {
                    return;
                }

                if (isBundle) {
                    // Sakrij AI polja u bundle modu
                    if (AI_FIELDS[name]) {
                        setVisible($row, false);
                        return;
                    }
                    setVisible($row, bundleAllows(name, trigger));
                    return;
                }

                if (isQtyDeal) {
                    if (AI_FIELDS[name]) {
                        setVisible($row, false);
                        return;
                    }
                    setVisible($row, !!QTY_DEAL_FIELDS[name]);
                    return;
                }

                if (isPonuda) {
                    if (AI_FIELDS[name]) {
                        setVisible($row, false);
                        return;
                    }
                    setVisible($row, !!PONUDA_FIELDS[name]);
                    return;
                }

                if (isAi) {
                    setVisible($row, !!(AI_BASE[name] || AI_FIELDS[name]));
                    return;
                }

                // Nepoznat tip — samo osnovna
                setVisible($row, name === 'naziv' || name === 'tip' || name === 'aktivan' || name === 'redoslijed');
            },
        );

        var $bundleInline = findInline('bundle_line', 'akcija-inline-bundle-lines');
        var $dwellInline = findInline('dwell', 'akcija-inline-dwell-items');
        if (!$dwellInline.length) {
            $dwellInline = findInline('productdwellitem', null);
        }
        var $qtyInline = findInline('qty_tier', 'akcija-inline-qty-tiers');
        $qtyInline.hide();

        var $qtyFieldset = fieldsetByHeading(['kupi više', 'kolicina', 'količina']);
        if (!$qtyFieldset.length) {
            $qtyFieldset = $('.form-row.field-qty_2_popust').closest('fieldset');
        }

        var $aiFieldsets = fieldsetByHeading([
            'ai prodaja',
            'ai dwell',
        ]);
        // Nova sekcija „+ Ponuda — unesi ovo” (i stari „Sadržaj” fallback)
        var $ponudaFieldset = fieldsetByHeading(['+ ponuda', 'ponuda — unesi', 'unesi ovo']);
        if (!$ponudaFieldset.length) {
            $ponudaFieldset = fieldsetByHeading(['sadržaj', 'sadrzaj']);
        }
        var $bundleExtraFieldset = fieldsetByHeading(['pop-up bundle', 'bundle — dodatno', 'bundle dodatno']);
        var $popupFieldset = fieldsetByHeading(['pop-up ponašanje', 'popup ponašanje']);
        var $legacyFieldset = fieldsetByHeading(['legacy']);

        if (isBundle) {
            BUNDLE_FORBIDDEN.forEach(hideField);
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);
            Object.keys(AI_FIELDS).forEach(hideField);
            hideField('gratis_artikal');
            if (trigger === 'trigger_product') {
                showField('artikal');
            } else {
                hideField('artikal');
            }
            if (trigger === 'category') {
                showField('kategorija');
            } else {
                hideField('kategorija');
            }
            hideField('bundle_artikli');
            showField('bundle_trigger');
            showField('popust_postotak');
            showField('tekst_dugmeta');
            showField('boja_dugmeta');
            showField('boja_opisa');
            showField('popup_delay_seconds');
            showField('za_prijavljene');
            showField('za_neprijavljene');
            showField('ponovo_poslije_dana');
            showField('naziv');
            showField('tip');
            showField('aktivan');
            showField('redoslijed');
            $bundleInline.show();
            $dwellInline.hide();
            $qtyFieldset.hide();
            $aiFieldsets.hide();
            $ponudaFieldset.show();
            $bundleExtraFieldset.show();
            $popupFieldset.show();
            $legacyFieldset.hide();
        } else if (isQtyDeal) {
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            hideField('popust_postotak');
            hideField('gratis_artikal');
            hideField('kategorija');
            hideField('popup_delay_seconds');
            Object.keys(AI_FIELDS).forEach(hideField);
            showField('artikal');
            showField('qty_2_popust');
            showField('qty_3_popust');
            showField('qty_4_popust');
            showField('qty_5_popust');
            showField('qty_6_popust');
            showField('tekst_dugmeta');
            showField('boja_dugmeta');
            showField('boja_opisa');
            showField('za_prijavljene');
            showField('za_neprijavljene');
            showField('ponovo_poslije_dana');
            showField('naziv');
            showField('tip');
            showField('aktivan');
            showField('redoslijed');
            $bundleInline.hide();
            $dwellInline.hide();
            $qtyFieldset.show().css('display', '');
            $qtyFieldset.find('.form-row').show();
            $aiFieldsets.hide();
            $ponudaFieldset.show();
            $bundleExtraFieldset.hide();
            $popupFieldset.show();
            $legacyFieldset.hide();
            if (!$qtyFieldset.data('qty-hinted')) {
                $qtyFieldset.css('outline', '2px solid #5BB805');
                window.setTimeout(function () {
                    $qtyFieldset.css('outline', '');
                }, 2500);
                $qtyFieldset.data('qty-hinted', 1);
            }
        } else if (isPonuda) {
            // Samo: tip + 1) trigger  2) %  3) ponuda artikal
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            hideField('kategorija');
            hideField('popup_delay_seconds');
            hideField('tekst_dugmeta');
            hideField('boja_dugmeta');
            hideField('boja_opisa');
            hideField('ponovo_poslije_dana');
            hideField('za_prijavljene');
            hideField('za_neprijavljene');
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);
            Object.keys(AI_FIELDS).forEach(hideField);
            showField('naziv');
            showField('tip');
            showField('aktivan');
            showField('redoslijed');
            showField('artikal');
            showField('popust_postotak');
            showField('gratis_artikal');
            $bundleInline.hide();
            $dwellInline.hide();
            $qtyFieldset.hide();
            $aiFieldsets.hide();
            $ponudaFieldset.show().css('display', '');
            $ponudaFieldset.find('.form-row').show().css('display', '');
            $bundleExtraFieldset.hide();
            $popupFieldset.hide();
            $legacyFieldset.hide();
            // Naglasi sekciju za unos
            if (!$ponudaFieldset.data('ponuda-hinted')) {
                $ponudaFieldset.css('outline', '2px solid #5BB805');
                window.setTimeout(function () {
                    $ponudaFieldset.css('outline', '');
                }, 2500);
                $ponudaFieldset.data('ponuda-hinted', 1);
            }
        } else if (isAi) {
            // Samo osnovna + AI opcije (sve što je bilo u starom meniju)
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            hideField('popust_postotak');
            hideField('artikal');
            hideField('gratis_artikal');
            hideField('kategorija');
            hideField('tekst_dugmeta');
            hideField('boja_dugmeta');
            hideField('boja_opisa');
            hideField('popup_delay_seconds');
            hideField('za_prijavljene');
            hideField('za_neprijavljene');
            hideField('ponovo_poslije_dana');
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);

            showField('naziv');
            showField('tip');
            showField('aktivan');
            showField('redoslijed');
            Object.keys(AI_FIELDS).forEach(showField);

            $bundleInline.hide();
            $dwellInline.show().css('display', '');
            $qtyFieldset.hide();
            $aiFieldsets.show().css('display', '');
            $aiFieldsets.find('.form-row').show().css('display', '');
            $ponudaFieldset.hide();
            $bundleExtraFieldset.hide();
            $popupFieldset.hide();
            $legacyFieldset.hide();
        } else {
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            hideField('gratis_artikal');
            Object.keys(AI_FIELDS).forEach(hideField);
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);
            $bundleInline.hide();
            $dwellInline.hide();
            $qtyFieldset.hide();
            $aiFieldsets.hide();
            $bundleExtraFieldset.hide();
        }
    }

    function bind() {
        if (!$('#id_tip').length) {
            return;
        }
        $(document)
            .off('change.akcijaBundle', '#id_tip')
            .on('change.akcijaBundle', '#id_tip', toggleAkcijaFields);
        $(document)
            .off('change.akcijaBundle', '#id_bundle_trigger')
            .on('change.akcijaBundle', '#id_bundle_trigger', toggleAkcijaFields);

        toggleAkcijaFields();
        window.setTimeout(toggleAkcijaFields, 50);
        window.setTimeout(toggleAkcijaFields, 300);
        window.setTimeout(toggleAkcijaFields, 1000);
    }

    $(bind);
    $(window).on('load', function () {
        window.setTimeout(toggleAkcijaFields, 50);
    });
})(django.jQuery || window.jQuery);
