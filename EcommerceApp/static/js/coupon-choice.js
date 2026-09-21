(function () {
    var openButton = document.querySelector('[data-coupon-choice-open]');
    var dialog = document.getElementById('couponChoiceDialog');
    if (!openButton || !dialog) return;
    var previousOverflow = '';

    function close() {
        dialog.hidden = true;
        document.body.style.overflow = previousOverflow;
        openButton.focus();
    }

    openButton.addEventListener('click', function (event) {
        event.preventDefault();
        dialog.hidden = false;
        previousOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        var closeButton = dialog.querySelector('.coupon-choice-close');
        if (closeButton) closeButton.focus();
    });

    dialog.querySelectorAll('[data-coupon-choice-close]').forEach(function (button) {
        button.addEventListener('click', close);
    });
    dialog.addEventListener('keydown', function (event) {
        if (event.key === 'Escape') {
            close();
        } else if (event.key === 'Tab') {
            var focusable = dialog.querySelectorAll('button:not([disabled])');
            var first = focusable[0];
            var last = focusable[focusable.length - 1];
            if (event.shiftKey && document.activeElement === first) {
                event.preventDefault();
                last.focus();
            } else if (!event.shiftKey && document.activeElement === last) {
                event.preventDefault();
                first.focus();
            }
        }
    });
})();
