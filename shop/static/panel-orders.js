const orderDialog = document.querySelector('#orderPreviewDialog');
const orderContent = document.querySelector('#orderPreviewContent');
let orderRequest;
document.querySelectorAll('[data-order-preview]').forEach(button => {
  button.addEventListener('click', async () => {
    if (orderRequest) orderRequest.abort();
    const request = new AbortController();
    orderRequest = request;
    const title = button.dataset.customerOnly ? 'Podaci kupca' : 'Pregled narudžbe';
    orderContent.innerHTML = '<h2 id="orderPreviewTitle">' + title + '</h2><p>Učitavanje…</p>';
    orderDialog.showModal();
    const url = new URL(window.location.href);
    url.searchParams.set('preview', button.dataset.orderPreview);
    url.searchParams.delete('customer_only');
    if (button.dataset.customerOnly) url.searchParams.set('customer_only', '1');
    try {
      const response = await fetch(url, { credentials: 'same-origin', cache: 'no-store', mode: 'same-origin', signal: request.signal });
      if (!response.ok || response.redirected) throw new Error('Preview failed');
      const html = await response.text();
      if (!request.signal.aborted) orderContent.innerHTML = html;
    } catch (error) {
      if (error.name !== 'AbortError') orderContent.innerHTML = '<h2 id="orderPreviewTitle">' + title + '</h2><p>Nije moguće učitati podatke. Zatvorite pregled i pokušajte ponovo.</p>';
    }
  });
});
orderDialog.addEventListener('close', () => { if (orderRequest) orderRequest.abort(); });
