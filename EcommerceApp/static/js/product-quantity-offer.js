(function () {
    const offer = document.querySelector('.pd-inline-qty');
    if (!offer) return;
    const choices = [...offer.querySelectorAll('[data-qty-choice]')];
    const quantities = [...document.querySelectorAll('.add-to-cart-form input[name="quantity"]')];
    let explicitlyChosen = false;
    function sync(value) {
        if (explicitlyChosen) {
            quantities.forEach(input => {
                const form = input.closest('form');
                let action = form.querySelector('input[name="akcija_id"]');
                if (!action) {
                    action = document.createElement('input');
                    action.type = 'hidden';
                    action.name = 'akcija_id';
                    form.append(action);
                }
                action.value = offer.dataset.qtyAction;
                // Let the server resolve the tier from the current quantity.
                form.querySelector('input[name="tier_id"]')?.remove();
            });
        }
        const match = choices.filter(choice => choice.dataset.open === 'true'
            ? value >= Number(choice.dataset.qtyChoice)
            : value === Number(choice.dataset.qtyChoice)).pop();
        choices.forEach(choice => {
            const selected = choice === match;
            choice.setAttribute('aria-pressed', String(selected));
            choice.closest('.pd-inline-qty__card').classList.toggle('is-selected', selected);
        });
    }
    choices.forEach(choice => choice.addEventListener('click', event => {
        if (!quantities.length) return;
        event.preventDefault();
        explicitlyChosen = true;
        const value = Number(choice.dataset.qtyChoice);
        quantities.forEach(input => {
            input.value = value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        sync(value);
    }));
    quantities.forEach(input => {
        input.closest('form').querySelectorAll('.product-qty-btn').forEach(button => {
            button.addEventListener('click', () => queueMicrotask(() => sync(Number(input.value))));
        });
        input.addEventListener('input', () => sync(Number(input.value)));
        input.addEventListener('change', () => sync(Number(input.value)));
    });
    if (quantities.length) sync(Number(quantities[0].value));
})();
