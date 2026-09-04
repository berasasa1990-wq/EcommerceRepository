document.addEventListener('DOMContentLoaded', function () {
    var submitTimer = null;
    function money(n) {
        return (Math.round((Number(n) || 0) * 100) / 100).toFixed(2);
    }
    function updateLineTotal(item, qty) {
        if (!item) return;
        var unit = parseFloat(item.getAttribute('data-unit-price'));
        if (!isFinite(unit)) return;
        var totalEl = item.querySelector('.cart-item-total .price-deal, .cart-item-total .price-discounted, .cart-item-total p');
        if (!totalEl) return;
        var label = money(unit * qty) + ' KM';
        if (totalEl.tagName === 'P' && !totalEl.querySelector('span')) totalEl.textContent = label;
        else totalEl.textContent = label;
    }
    function submitQty(form) {
        if (!form) return;
        window.clearTimeout(submitTimer);
        submitTimer = window.setTimeout(function () {
            if (typeof form.requestSubmit === 'function') form.requestSubmit();
            else form.submit();
        }, 220);
    }
    document.querySelectorAll('.cart-item-qty input[type="number"]').forEach(function (input) {
        input.addEventListener('change', function () {
            var item = input.closest('.cart-item');
            var qty = parseInt(input.value, 10) || 1;
            updateLineTotal(item, qty);
            submitQty(input.closest('form'));
        });
    });
    document.querySelectorAll('.cart-qty-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            var wrap = btn.closest('.cart-item-qty');
            var input = wrap && wrap.querySelector('input[type="number"]');
            if (!input) return;
            var delta = parseInt(btn.getAttribute('data-qty-delta'), 10) || 0;
            var min = parseInt(input.min, 10) || 1;
            var max = parseInt(input.max, 10) || 99;
            var current = parseInt(input.value, 10) || min;
            var next = Math.min(max, Math.max(min, current + delta));
            if (String(next) === String(input.value)) return;
            input.value = String(next);
            updateLineTotal(input.closest('.cart-item'), next);
            submitQty(input.closest('form'));
        });
    });
});
