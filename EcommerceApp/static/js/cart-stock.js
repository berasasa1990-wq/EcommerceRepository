document.addEventListener('DOMContentLoaded', function () {
    var root = document.querySelector('[data-cart-stock-url]');
    if (!root) return;
    var busy = false;
    var soldOut = !!document.querySelector('[data-stock-label][data-sold-out="true"]');
    var checkoutLink = document.querySelector('.btn-cart-checkout');
    var checkoutForm = document.getElementById('checkout-form');
    if (checkoutLink) checkoutLink.addEventListener('click', function (event) {
        if (soldOut) { event.preventDefault(); var label = document.querySelector('[data-sold-out="true"]'); if (label) label.scrollIntoView({block: 'center'}); }
    });
    if (checkoutForm) checkoutForm.addEventListener('submit', function (event) {
        if (soldOut) event.preventDefault();
    });
    function refresh() {
        if (busy || document.hidden) return;
        busy = true;
        fetch(root.getAttribute('data-cart-stock-url'), {credentials: 'same-origin', cache: 'no-store'})
            .then(function (response) { if (!response.ok) throw new Error('Stock unavailable'); return response.json(); })
            .then(function (data) {
                soldOut = data.items.some(function (item) { return item.sold_out; });
                data.items.forEach(function (item) {
                    document.querySelectorAll('[data-cart-key]').forEach(function (row) {
                        if (row.getAttribute('data-cart-key') !== item.key) return;
                        var label = row.querySelector('[data-stock-label]');
                        if (label) {
                            label.textContent = item.sold_out ? 'Rasprodat — uklonite iz korpe' : '✓ Na stanju';
                            label.style.color = item.sold_out ? '#b91c1c' : '';
                            label.setAttribute('data-sold-out', item.sold_out ? 'true' : 'false');
                        }
                        row.querySelectorAll('.cart-qty-btn, .form-input--qty').forEach(function (control) { control.disabled = item.sold_out; });
                        var qty = row.querySelector('.form-input--qty');
                        if (qty) qty.max = item.quantity;
                        var removeLink = row.querySelector('[data-stock-remove]');
                        if (removeLink) removeLink.hidden = !item.sold_out;
                    });
                });
                if (checkoutLink) checkoutLink.setAttribute('aria-disabled', String(soldOut));
                if (checkoutForm) checkoutForm.querySelectorAll('[type="submit"]').forEach(function (button) { button.disabled = soldOut; });
            }).catch(function () { /* Checkout always verifies stock on the server. */ })
            .finally(function () { busy = false; });
    }
    refresh();
    window.setInterval(refresh, 15000);
    window.addEventListener('focus', refresh);
    document.addEventListener('visibilitychange', refresh);
});
