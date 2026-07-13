(function ($) {
    'use strict';

    var BUNDLE_TIP = 'bundle';
    var GRATIS_TIP = 'gratis';
    var QTY_DEAL_TIP = 'qty_deal';

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

    // Nikad za bundle (ostale vrste akcija)
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

    var GRATIS_FIELDS = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        artikal: 1,
        gratis_artikal: 1,
        popust_postotak: 1,
        gratis_popup: 1,
        tekst_dugmeta: 1,
        boja_dugmeta: 1,
        boja_opisa: 1,
        popup_delay_seconds: 1,
        za_prijavljene: 1,
        za_neprijavljene: 1,
        ponovo_poslije_dana: 1,
    };

    // Kupi više (količinski %): artikal + polja 2/3/4/5/6 kom → %
    // Bez popup_delay — prikaz samo na stranici artikla, ne nakon kašnjenja širom sajta
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

    function toggleAkcijaFields() {
        var tip = tipVal();
        var isBundle = tip === BUNDLE_TIP;
        var isGratis = tip === GRATIS_TIP;
        var isQtyDeal = tip === QTY_DEAL_TIP;
        var trigger = triggerVal();
        var showGratisPopup = isGratis && $('#id_gratis_popup').is(':checked');

        $('#content-main .form-row, #akcija_form .form-row, form .aligned .form-row').each(
            function () {
                var $row = $(this);
                var name = rowFieldName($row);
                if (!name) {
                    return;
                }

                if (isBundle) {
                    setVisible($row, bundleAllows(name, trigger));
                    return;
                }

                if (isGratis) {
                    var gShow = !!GRATIS_FIELDS[name];
                    if (
                        name === 'popup_delay_seconds' ||
                        name === 'za_prijavljene' ||
                        name === 'za_neprijavljene' ||
                        name === 'ponovo_poslije_dana' ||
                        name === 'tekst_dugmeta' ||
                        name === 'boja_dugmeta' ||
                        name === 'boja_opisa'
                    ) {
                        gShow = showGratisPopup;
                    }
                    if (
                        name === 'bundle_artikli' ||
                        name === 'bundle_trigger'
                    ) {
                        gShow = false;
                    }
                    setVisible($row, gShow);
                    return;
                }

                if (isQtyDeal) {
                    setVisible($row, !!QTY_DEAL_FIELDS[name]);
                    return;
                }

                if (
                    name === 'bundle_artikli' ||
                    name === 'bundle_trigger' ||
                    name === 'gratis_artikal' ||
                    name === 'gratis_popup'
                ) {
                    setVisible($row, false);
                    return;
                }
                setVisible($row, true);
            },
        );

        var $bundleInline = findInline('bundle_line', 'akcija-inline-bundle-lines');
        // Stari qty-tier inline (ako postoji u kešu) — sakrij uvijek
        var $qtyInline = findInline('qty_tier', 'akcija-inline-qty-tiers');
        $qtyInline.hide();

        // Fieldset „Kupi više — količina i popust”
        var $qtyFieldset = $('fieldset').filter(function () {
            var t = $(this).find('h2, .fieldset-heading, legend').first().text() || '';
            return t.toLowerCase().indexOf('kupi više') !== -1 || t.toLowerCase().indexOf('kolicina') !== -1;
        });
        if (!$qtyFieldset.length) {
            $qtyFieldset = $('.form-row.field-qty_2_popust').closest('fieldset');
        }

        if (isBundle) {
            BUNDLE_FORBIDDEN.forEach(hideField);
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);
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
            $qtyFieldset.hide();
        } else if (isQtyDeal) {
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            hideField('popust_postotak');
            hideField('gratis_artikal');
            hideField('gratis_popup');
            hideField('kategorija');
            hideField('slika');
            hideField('preview_slika');
            hideField('prag_korpe_km');
            hideField('deal_vrsta');
            hideField('pocetak');
            hideField('trajanje_sati');
            hideField('link_dugmeta');
            showField('artikal');
            showField('qty_2_popust');
            showField('qty_3_popust');
            showField('qty_4_popust');
            showField('qty_5_popust');
            showField('qty_6_popust');
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
            $bundleInline.hide();
            $qtyFieldset.show().css('display', '');
            $qtyFieldset.find('.form-row').show();
            // Highlight polja da se odmah vide
            if (!$qtyFieldset.data('qty-hinted')) {
                $qtyFieldset.css('outline', '2px solid #5BB805');
                window.setTimeout(function () {
                    $qtyFieldset.css('outline', '');
                }, 2500);
                $qtyFieldset.data('qty-hinted', 1);
            }
        } else {
            hideField('bundle_artikli');
            hideField('bundle_trigger');
            ['qty_2_popust', 'qty_3_popust', 'qty_4_popust', 'qty_5_popust', 'qty_6_popust'].forEach(hideField);
            $bundleInline.hide();
            $qtyFieldset.hide();
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
            .off('change.akcijaBundle', '#id_gratis_popup')
            .on('change.akcijaBundle', '#id_gratis_popup', toggleAkcijaFields);
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
