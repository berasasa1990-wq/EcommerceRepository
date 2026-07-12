(function ($) {
    'use strict';

    var BUNDLE_TIP = 'bundle';
    var GRATIS_TIP = 'gratis';

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

    function toggleAkcijaFields() {
        var tip = tipVal();
        var isBundle = tip === BUNDLE_TIP;
        var isGratis = tip === GRATIS_TIP;
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
                    if (name === 'bundle_artikli' || name === 'bundle_trigger') {
                        gShow = false;
                    }
                    setVisible($row, gShow);
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

        if (isBundle) {
            // Eksplicitno sakrij sve tuđe tipove
            BUNDLE_FORBIDDEN.forEach(hideField);
            // Trigger-ovisno
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
            // Bundle polja uvijek
            showField('bundle_artikli');
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
        } else {
            hideField('bundle_artikli');
            hideField('bundle_trigger');
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
