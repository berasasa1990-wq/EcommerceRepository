(() => {
    const dialog = document.querySelector('[data-alternative-dialog]');
    if (!dialog) return;
    const inputs = [...dialog.querySelectorAll('[data-alternative-quantity]')];
    const remaining = Number(dialog.dataset.remaining);
    const total = dialog.querySelector('[data-alternative-total]');
    function update() {
        const count = inputs.reduce((sum, input) => sum + (Number(input.value) || 0), 0);
        total.textContent = `Odabrano: ${count} / ${remaining} kom.`;
        inputs.forEach(input => input.setCustomValidity(count > remaining ? `Nedostaje još ${remaining} kom. Smanji odabrane količine.` : ''));
    }
    inputs.forEach(input => input.addEventListener('input', update));
    dialog.addEventListener('cancel', event => {
        event.preventDefault();
        dialog.querySelector('[data-skip-alternatives]').click();
    });
    if (typeof dialog.showModal === 'function') {
        dialog.removeAttribute('open');
        dialog.showModal();
    }
    update();
})();
