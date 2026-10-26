const pointsPanel = document.querySelector('[data-points-preview-url]');
if (pointsPanel) {
  const input = document.querySelector('#loyalty-points');
  const form = input.form;
  const submit = form.querySelector('button[type="submit"]');
  const feedback = pointsPanel.querySelector('.checkout-points-feedback');
  const maxButton = pointsPanel.querySelector('[data-points-max]');
  maxButton.addEventListener('click', () => {
    input.value = input.max;
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  const discountRow = document.querySelector('#checkout-points-discount');
  const money = value => Number(value).toLocaleString('bs-BA', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + ' KM';
  let timer;
  let controller;
  let version = 0;
  input.addEventListener('input', () => {
    clearTimeout(timer);
    if (controller) controller.abort();
    const currentVersion = ++version;
    submit.disabled = true;
    feedback.hidden = false;
    feedback.textContent = 'Obračun popusta…';
    timer = setTimeout(async () => {
      controller = new AbortController();
      const url = new URL(pointsPanel.dataset.pointsPreviewUrl, window.location.origin);
      url.searchParams.set('points_preview', '1');
      url.searchParams.set('points', input.value || '0');
      try {
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', mode: 'same-origin', signal: controller.signal });
        if (!response.ok || response.redirected) throw new Error('Quote failed');
        const quote = await response.json();
        if (currentVersion !== version) return;
        input.value = quote.points_used;
        input.max = quote.points_allowed;
        maxButton.disabled = quote.points_allowed === 0;
        pointsPanel.querySelector('[data-points-max-count]').textContent = quote.points_allowed;
        discountRow.hidden = Number(quote.points_discount) === 0;
        document.querySelector('[data-points-used]').textContent = quote.points_used;
        document.querySelector('[data-points-discount]').textContent = '−' + money(quote.points_discount);
        document.querySelector('[data-checkout-delivery]').textContent = Number(quote.delivery_price) === 0 ? 'Besplatna dostava' : money(quote.delivery_price);
        document.querySelector('[data-checkout-total]').textContent = money(quote.grand_total);
        feedback.textContent = quote.points_used > 0 ? 'Popust za ' + quote.points_used + ' bodova: ' + money(quote.points_discount) : 'Bodovi nisu primijenjeni.';
        submit.disabled = false;
      } catch (error) {
        if (error.name !== 'AbortError' && currentVersion === version) {
          feedback.textContent = 'Obračun nije dostupan. Promijenite unos bodova da pokušate ponovo.';
        }
      }
    }, 180);
  });
}
