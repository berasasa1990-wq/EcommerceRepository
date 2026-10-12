(() => {
 const dialog = document.getElementById('wmsProductHistory');
 if (!dialog) return;
 const content = dialog.querySelector('[data-history-content]');
 let currentUrl = '', controller;
 const load = async url => {
  controller?.abort();
  const request = controller = new AbortController();
  content.textContent = 'Učitavanje istorije…';
  try {
   const response = await fetch(url, {signal: request.signal, credentials: 'same-origin'});
   if (!response.ok || response.redirected) throw new Error();
   const html = await response.text();
   if (request.signal.aborted) return;
   content.innerHTML = html;
   content.scrollTop = 0;
  } catch (error) {
   if (error.name !== 'AbortError') content.textContent = 'Istorija nije dostupna. Zatvorite prozor i pokušajte ponovo.';
  }
 };
 document.addEventListener('click', event => {
  const button = event.target.closest('[data-product-history]');
  if (!button) return;
  currentUrl = button.dataset.productHistory;
  if (!dialog.open) dialog.showModal();
  load(currentUrl);
 });
 dialog.querySelector('[data-history-close]').addEventListener('click', () => dialog.close());
 dialog.addEventListener('close', () => controller?.abort());
 content.addEventListener('click', event => {
  const link = event.target.closest('[data-history-page]');
  if (!link) return;
  event.preventDefault();
  const url = new URL(currentUrl, location.origin);
  url.search = new URL(link.getAttribute('href'), url).search;
  load(url);
 });
 dialog.addEventListener('click', event => {
  if (event.target !== dialog) return;
  const bounds = dialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
 });
})();
