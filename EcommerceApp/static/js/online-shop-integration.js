(() => {
  if (!document.body.classList.contains('online-shop-theme')) return;
  document.querySelectorAll('[data-account-open], [data-wishlist-open], [data-cart-open]').forEach(button => {
    button.addEventListener('click', () => {
      const target = button.dataset.accountOpen || button.dataset.wishlistOpen || button.dataset.cartOpen;
      if (target) window.location.assign(target);
    });
  });
  const brands = document.getElementById('brandsDialog');
  document.querySelector('[data-brands-open]')?.addEventListener('click', () => brands?.showModal());
  brands?.addEventListener('click', event => {
    if (event.target !== brands) return;
    const rect = brands.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) brands.close();
  });
  document.querySelectorAll('[data-brand-direction]').forEach(button => {
    button.addEventListener('click', () => {
      const row = button.closest('.bera-popular-brands__carousel')?.querySelector('.bera-popular-brands__row');
      row?.scrollBy({left: Number(button.dataset.brandDirection) * row.clientWidth / 2, behavior: 'smooth'});
    });
  });
  const form = document.getElementById('onlineShopNewsletter');
  form?.addEventListener('submit', async event => {
    event.preventDefault();
    const status = document.querySelector('[data-newsletter-status]');
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const response = await fetch(form.action, {method: 'POST', body: new FormData(form), credentials: 'same-origin', headers: {'X-Requested-With': 'XMLHttpRequest'}});
      const result = await response.json();
      status.textContent = result.message || result.error || (response.ok ? 'Prijava je sačuvana.' : 'Prijava nije uspjela. Pokušajte ponovo.');
      if (response.ok) form.reset();
    } catch {
      status.textContent = 'Prijava trenutno nije dostupna. Pokušajte ponovo.';
    } finally {
      button.disabled = false;
    }
  });
})();
