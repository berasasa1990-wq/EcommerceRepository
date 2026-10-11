(function ($) {
    function selectedProducts(element) {
        if (element.name === 'noviteti') return $(element).val() || [];
        return [...document.querySelectorAll('#offer_items-group select[name$="-product"]')]
            .filter(select => select !== element && !select.name.includes('__prefix__') &&
                !select.closest('tr')?.querySelector('input[name$="-DELETE"]')?.checked)
            .map(select => select.value).filter(Boolean);
    }
    function configure(element) {
        if (element.dataset.b2bConfigured || element.name.includes('__prefix__')) return;
        const widget = $(element).data('select2');
        if (!widget) return;
        const ajax = widget.options.get('ajax');
        const original = ajax.data;
        const data = params => ({...original(params), b2b_exclude: selectedProducts(element).join(',')});
        ajax.data = data;
        widget.dataAdapter.ajaxOptions.data = data;
        element.dataset.b2bConfigured = 'true';
    }
    function configureAll() {
        document.querySelectorAll('#id_noviteti, #offer_items-group select.admin-autocomplete').forEach(configure);
    }
    $(function () { setTimeout(configureAll, 0); });
    document.addEventListener('formset:added', () => setTimeout(configureAll, 0));
})(django.jQuery);
