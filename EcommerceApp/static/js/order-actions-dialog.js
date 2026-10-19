(() => {
  document.querySelectorAll('[data-order-dialog]').forEach(trigger => {
    trigger.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const dialog = document.getElementById(trigger.dataset.orderDialog);
      if (!dialog) return;
      dialog.querySelector('[data-order-options]').hidden = false;
      dialog.querySelector('[data-order-exchange-form]').hidden = true;
      dialog.showModal();
    });
  });
  document.querySelectorAll('.order-actions-dialog').forEach(dialog => {
    const options = dialog.querySelector('[data-order-options]');
    const form = dialog.querySelector('[data-order-exchange-form]');
    const error = dialog.querySelector('[role="alert"]');
    dialog.querySelector('[data-order-close]').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
      if (event.target !== dialog) return;
      const bounds = dialog.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
    });
    dialog.querySelector('[data-order-exchange]')?.addEventListener('click', () => {
      options.hidden = true;
      form.hidden = false;
      error.hidden = true;
      form.querySelector('input[type="checkbox"]')?.focus();
    });
    dialog.querySelector('[data-order-back]').addEventListener('click', () => {
      form.hidden = true;
      options.hidden = false;
      dialog.querySelector('[data-order-exchange]')?.focus();
    });
    form.querySelectorAll('input[type="checkbox"]').forEach(checkbox => {
      checkbox.addEventListener('change', () => {
        checkbox.closest('.order-actions-dialog__item').querySelector('input[type="number"]').disabled = !checkbox.checked;
        error.hidden = true;
      });
    });
    form.addEventListener('submit', event => {
      if (!form.querySelector('input[name="item_id"]:checked')) {
        event.preventDefault();
        error.hidden = false;
        return;
      }
      form.querySelector('button[type="submit"]').disabled = true;
      form.querySelector('button[type="submit"]').textContent = 'Kreiram zamjenu…';
    });
  });
})();
