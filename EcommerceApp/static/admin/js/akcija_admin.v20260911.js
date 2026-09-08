(function ($) {
    'use strict';

    var BUNDLE_TIP = 'bundle';
    var QTY_DEAL_TIP = 'qty_deal';
    var PONUDA_TIP = 'ponuda';
    var AKCIJSKA_TIP = 'akcijska';
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
    FIELDS_BY_TIP[AKCIJSKA_TIP] = {
        naziv: 1,
        tip: 1,
        aktivan: 1,
        redoslijed: 1,
        popust_postotak: 1,
        pocetak: 1,
        trajanje_sati: 1,
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
        naziv: 1, tip: 1, browse_interest_popup_aktivan: 1,
        browse_interest_mode: 1, browse_interest_source: 1, browse_interest_popust: 1,
    };

    function update() {
        var $form = $('#akcija_form');
        if (!$form.length) return;
        var tip = $('#id_tip').val();
        var allowed = $.extend({}, FIELDS_BY_TIP[tip] || {});
        var trigger = $('#id_bundle_trigger').val();
        if (tip === BUNDLE_TIP) {
            allowed.artikal = trigger === 'trigger_product';
            allowed.kategorija = trigger === 'category';
            allowed.bundle_artikli = true;
        }
        $form.find('.form-row').each(function () {
            if ($(this).closest('.inline-group').length) return;
            var match = this.className.match(/(?:^|\s)field-([a-z0-9_]+)/i);
            if (match) this.hidden = !allowed[match[1]];
        });
        $form.find('fieldset.module').each(function () {
            if ($(this).closest('.inline-group, .form-row').length) return;
            this.hidden = !$(this).find('.form-row').toArray().some(function (row) { return !row.hidden; });
        });
        $form.find('.inline-group').each(function () {
            this.hidden = !(
                (tip === BUNDLE_TIP && $(this).find('.akcija-inline-bundle-lines').addBack('.akcija-inline-bundle-lines').length) ||
                (tip === AKCIJSKA_TIP && $(this).find('.akcija-inline-flash-lines').addBack('.akcija-inline-flash-lines').length) ||
                (tip === AI_PRODAJA_TIP && $(this).find('.akcija-inline-dwell-items').addBack('.akcija-inline-dwell-items').length)
            );
        });
        // Browser ne smije zaustaviti slanje zbog nevidljivih obaveznih polja.
        $form.find('input, select, textarea').each(function () {
            if (this.dataset.akcijaRequired === undefined) this.dataset.akcijaRequired = this.required ? '1' : '0';
            this.required = this.dataset.akcijaRequired === '1' && !$(this).closest('[hidden]').length;
        });
        var guides = {
            ponuda: '+ Ponuda: odaberi artikal koji kupac dodaje u korpu, zatim drugi artikal koji će mu se ponuditi. Popust je opcionalan.',
            qty_deal: 'Količinski popust: odaberi artikal i upiši popust uz željenu količinu. Na primjer, 2 komada → 10%. Ostale količine ostavi prazne.',
            bundle: 'Bundle: odaberi gdje se set prikazuje, zatim u tabelu dodaj artikle i količine. Set treba ukupno najmanje 2 komada. Popust može važiti za cijeli set ili za pojedinu stavku.',
            akcijska: 'Akcijska ponuda: unesi popust i trajanje, pa odaberi artikle u tabeli. Samo oni dobijaju sniženje na redovnim karticama, stranici artikla i u korpi.'
        };
        var $guide = $('#akcija-quick-guide');
        if (!$guide.length) $guide = $('<p id="akcija-quick-guide" role="status">').css({padding:'16px', background:'#e5e5e2', color:'#292929', border:'1px solid #999', borderRadius:'6px', lineHeight:'1.6'}).prependTo($form);
        $guide.text(guides[tip] || '').prop('hidden', !guides[tip]);
        var labels = {
            ponuda: ['Artikal koji kupac dodaje u korpu', 'Popust na ponuđeni artikal (%)', 'Artikal koji nudimo kupcu'],
            qty_deal: ['Artikal za količinski popust'],
            bundle: ['Artikal na čijoj stranici se prikazuje set', 'Popust na cijeli set (%)'],
            akcijska: ['Artikal na čijoj stranici se prikazuje ponuda', 'Popust na ponudu (%)']
        };
        ['artikal', 'popust_postotak', 'gratis_artikal'].forEach(function (name, i) {
            var label = (labels[tip] || [])[i];
            if (label) $form.find('.field-' + name + ' label').first().text(label + ':');
        });
    }
    $(function () {
        if (!$('#akcija_form').length) return;
        $(document).on('change.akcija', '#id_tip, #id_bundle_trigger', update);
        document.addEventListener('formset:added', update);
        update();
    });
})(django.jQuery || window.jQuery);
