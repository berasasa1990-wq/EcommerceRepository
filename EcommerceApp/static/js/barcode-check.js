(function () {
    'use strict';
    function setup() {
        document.querySelectorAll('[data-barcode-check]').forEach(function (input) {
            var form = input.form;
            if (!form || input.dataset.barcodeReady) return;
            input.dataset.barcodeReady = '1';
            var version = 0;
            var lastChecked = null;
            var message = document.createElement('p');
            message.setAttribute('role', 'alert');
            message.style.color = '#b42318';
            input.insertAdjacentElement('afterend', message);
            input.addEventListener('input', function () {
                version += 1;
                lastChecked = null;
                input.setCustomValidity('');
                message.textContent = '';
            });
            input.addEventListener('change', async function () {
                var value = input.value.trim();
                if (!value || value === lastChecked) return;
                var current = ++version;
                var csrf = form.querySelector('[name="csrfmiddlewaretoken"]');
                if (!csrf) return;
                var body = new URLSearchParams();
                body.set('barkod', value);
                body.set('product_id', input.dataset.productId || '');
                ['naziv', 'sifra'].forEach(function (name) {
                    var field = form.querySelector('[name="' + name + '"]');
                    body.set(name, field ? field.value : '');
                });
                try {
                    var response = await fetch(input.dataset.barcodeCheck, {method: 'POST',
                        credentials: 'same-origin', headers: {'X-CSRFToken': csrf.value}, body: body});
                    var result = await response.json();
                    if (current !== version || input.value.trim() !== value) return;
                    lastChecked = value;
                    if (result.ok) {
                        input.setCustomValidity('');
                        message.textContent = '';
                    } else if (result.message) {
                        input.setCustomValidity(result.message);
                        message.textContent = result.message;
                        window.alert(result.message);
                    }
                } catch (error) {
                    // Server validation also runs when the form is saved.
                    lastChecked = null;
                }
            });
        });
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', setup);
    else setup();
})();
