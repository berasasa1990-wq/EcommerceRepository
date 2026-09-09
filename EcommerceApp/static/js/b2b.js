document.querySelectorAll('[data-b2b-slider]').forEach((slider) => {
  const slides = [...slider.querySelectorAll('[data-slide]')];
  if (slides.length < 2) return;
  const controls = slider.querySelector('.slider-controls');
  const dots = [...slider.querySelectorAll('[data-slide-to]')];
  const pauseButton = slider.querySelector('[data-pause]');
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
  let index = 0;
  let paused = reducedMotion.matches;
  let timer;
  const show = (next) => {
    index = (next + slides.length) % slides.length;
    slides.forEach((slide, i) => { slide.hidden = i !== index; });
    dots.forEach((dot, i) => dot.setAttribute('aria-pressed', String(i === index)));
  };
  const stop = () => { clearTimeout(timer); };
  const start = () => {
    stop();
    if (!paused && !document.hidden && !slides.some((slide) => slide.contains(document.activeElement))) {
      timer = setTimeout(() => { show(index + 1); start(); }, 3000);
    }
  };
  const updatePause = () => {
    pauseButton.textContent = paused ? '▶' : 'Ⅱ';
    pauseButton.setAttribute('aria-label', paused ? 'Pokreni smjenjivanje bannera' : 'Pauziraj smjenjivanje bannera');
  };
  slider.querySelector('[data-prev]').addEventListener('click', () => { show(index - 1); start(); });
  slider.querySelector('[data-next]').addEventListener('click', () => { show(index + 1); start(); });
  dots.forEach((dot, i) => dot.addEventListener('click', () => { show(i); start(); }));
  pauseButton.addEventListener('click', () => { paused = !paused; updatePause(); start(); });
  slider.addEventListener('focusin', start);
  slider.addEventListener('focusout', () => setTimeout(start, 0));
  document.addEventListener('visibilitychange', start);
  reducedMotion.addEventListener('change', () => { paused = reducedMotion.matches; updatePause(); start(); });
  controls.hidden = false;
  updatePause();
  start();
});

const checkoutForm = document.querySelector('[data-b2b-checkout]');
if (checkoutForm) {
  const dialog = checkoutForm.querySelector('.b2b-payment-dialog');
  const cancel = checkoutForm.querySelector('[data-close-payment]');
  const openButton = checkoutForm.querySelector('[data-open-payment]');
  let sending = false;
  const openPayment = () => {
    if (dialog.open) dialog.close();
    dialog.showModal();
  };
  cancel.hidden = false;
  checkoutForm.addEventListener('submit', (event) => {
    if (sending) { event.preventDefault(); return; }
    if (event.submitter?.name !== 'payment') {
      event.preventDefault();
      openPayment();
      return;
    }
    sending = true;
    checkoutForm.setAttribute('aria-busy', 'true');
    // Keep the clicked submitter enabled so its payment value reaches the server.
    dialog.querySelectorAll('button').forEach((button) => button.setAttribute('aria-disabled', 'true'));
  });
  cancel.addEventListener('click', () => {
    if (!sending) { dialog.close(); openButton.focus(); }
  });
  dialog.addEventListener('cancel', (event) => { if (sending) event.preventDefault(); });
  if (dialog.open) openPayment();
  window.addEventListener('pageshow', () => {
    sending = false;
    checkoutForm.removeAttribute('aria-busy');
    dialog.querySelectorAll('button').forEach((button) => button.removeAttribute('aria-disabled'));
  });
}

// Serialize mutations to avoid overlapping writes to the same session cart.
let b2bCartQueue = Promise.resolve();
let b2bToastTimer;
const showCartMessage = (message, error = false) => {
  const toast = document.querySelector('.b2b-cart-toast');
  if (!toast) return;
  clearTimeout(b2bToastTimer);
  toast.textContent = message;
  toast.classList.toggle('is-error', error);
  toast.hidden = false;
  b2bToastTimer = setTimeout(() => { toast.hidden = true; }, error ? 8000 : 3500);
};
const quantityDialog = document.querySelector('.b2b-quantity-dialog');
const askQuantity = (form) => new Promise((resolve) => {
  const input = quantityDialog.querySelector('input');
  input.value = '1';
  input.max = form.dataset.maxQuantity;
  quantityDialog.querySelector('[data-quantity-product]').textContent = form.dataset.productName;
  quantityDialog.returnValue = '';
  quantityDialog.addEventListener('close', () => {
    const value = quantityDialog.returnValue;
    form.querySelector('button').focus({ preventScroll: true });
    resolve(value ? Number(value) : null);
  }, { once: true });
  quantityDialog.showModal();
  input.select();
});
if (quantityDialog) {
  quantityDialog.querySelector('form').addEventListener('submit', (event) => {
    event.preventDefault();
    quantityDialog.close(quantityDialog.querySelector('input').value);
  });
  quantityDialog.querySelector('[data-quantity-cancel]').addEventListener('click', () => quantityDialog.close(''));
  quantityDialog.addEventListener('cancel', (event) => { event.preventDefault(); quantityDialog.close(''); });
}
document.querySelectorAll('.add-form').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (form.dataset.pending === 'true') return;
    form.dataset.pending = 'true';
    const quantity = await askQuantity(form);
    if (quantity === null) { delete form.dataset.pending; return; }
    const data = new FormData(form);
    data.set('quantity', String(quantity));
    const button = form.querySelector('button[type="submit"], button:not([type])');
    form.dataset.pending = 'true';
    form.setAttribute('aria-busy', 'true');
    button.setAttribute('aria-disabled', 'true');
    b2bCartQueue = b2bCartQueue.then(async () => {
      try {
        const response = await fetch(form.action, {
          method: 'POST', body: data, credentials: 'same-origin',
          headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
        });
        if (!response.headers.get('content-type')?.includes('application/json')) {
          throw new Error('Nije moguće potvrditi dodavanje. Provjerite korpu prije ponovnog pokušaja.');
        }
        const result = await response.json();
        if (result.b2b_cart_count !== undefined) {
          document.querySelectorAll('[data-b2b-cart-count]').forEach((node) => { node.textContent = result.b2b_cart_count; });
          const total = new Intl.NumberFormat('bs-BA', {minimumFractionDigits: 2, maximumFractionDigits: 2}).format(Number(result.b2b_cart_total));
          document.querySelectorAll('[data-b2b-cart-total]').forEach((node) => { node.textContent = `${total} KM`; });
        }
        showCartMessage(result.message || 'Dodavanje nije uspjelo.', !response.ok || !result.ok);
      } catch (error) {
        showCartMessage(error.message || 'Provjerite vezu i sadržaj korpe.', true);
      } finally {
        delete form.dataset.pending;
        form.removeAttribute('aria-busy');
        button.removeAttribute('aria-disabled');
      }
    });
  });
});

const imageDialog = document.querySelector('.b2b-image-dialog');
if (imageDialog) {
  const fullImage = imageDialog.querySelector('.b2b-image-full');
  const caption = imageDialog.querySelector('.b2b-image-caption');
  let imageTrigger;
  document.addEventListener('click', (event) => {
    const trigger = event.target.closest('[data-image-zoom]');
    if (!trigger || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    imageTrigger = trigger;
    fullImage.src = trigger.href;
    fullImage.alt = trigger.querySelector('img').alt;
    caption.textContent = trigger.getAttribute('aria-label').replace(/^Uvećaj sliku: /, '');
    imageDialog.showModal();
  });
  imageDialog.querySelector('.b2b-image-close').addEventListener('click', () => imageDialog.close());
  imageDialog.addEventListener('click', (event) => {
    if (event.target === imageDialog) imageDialog.close();
  });
  imageDialog.addEventListener('close', () => {
    fullImage.removeAttribute('src');
    if (imageTrigger?.isConnected) imageTrigger.focus({ preventScroll: true });
  });
}
