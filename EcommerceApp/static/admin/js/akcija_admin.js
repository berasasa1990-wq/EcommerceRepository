(function ($) {
    const GRATIS_TIP = 'gratis';
    const GRATIS_MAIN_FIELDS = ['naziv', 'tip', 'aktivan', 'redoslijed'];
    const GRATIS_CONTENT_FIELDS = ['artikal', 'gratis_artikal'];

    function toggleGratisFields() {
        const isGratis = $('#id_tip').val() === GRATIS_TIP;

        $('fieldset.module').each(function () {
            const $fieldset = $(this);
            const hasMainFields = $fieldset.find('.field-naziv').length > 0;
            const hasContentFields = $fieldset.find('.field-artikal').length > 0;
            const isPopupFieldset = $fieldset.find('.field-popup_delay_seconds').length > 0;

            if (isPopupFieldset) {
                $fieldset.toggle(!isGratis);
                return;
            }

            $fieldset.find('.form-row').each(function () {
                const $row = $(this);
                const fieldClass = ($row.attr('class') || '').match(/field-([a-z_]+)/);
                const fieldName = fieldClass ? fieldClass[1] : '';

                if (!isGratis) {
                    $row.toggle(fieldName !== 'gratis_artikal');
                    return;
                }

                if (hasMainFields) {
                    $row.toggle(GRATIS_MAIN_FIELDS.includes(fieldName));
                } else if (hasContentFields) {
                    $row.toggle(GRATIS_CONTENT_FIELDS.includes(fieldName));
                } else {
                    $row.hide();
                }
            });
        });
    }

    $(function () {
        const $tip = $('#id_tip');
        if (!$tip.length) {
            return;
        }
        $tip.on('change', toggleGratisFields);
        toggleGratisFields();
    });
})(django.jQuery);