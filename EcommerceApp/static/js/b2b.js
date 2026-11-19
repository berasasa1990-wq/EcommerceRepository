document.querySelectorAll('[data-brand-scroll]').forEach((button) => {
  button.addEventListener('click', (event) => {
    event.preventDefault();
    const track = button.closest('.brands')?.querySelector('[data-brand-track]');
    if (!track) return;
    track.scrollBy({ left: Number(button.dataset.brandScroll) * Math.max(180, track.clientWidth * 0.7), behavior: 'smooth' });
  });
});
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
const quantityDialog = document.querySelector('dialog[aria-labelledby="quantity-title"]');
const addButton = (form) => form.querySelector('.add-to-cart, button[type="submit"]');
const rowQuantity = (form) => form.closest('tr')?.querySelector('input[name="quantity"]')
  || form.querySelector('input[name="quantity"]');
const askQuantity = (form) => new Promise((resolve) => {
  const input = quantityDialog.querySelector('input');
  input.value = '1';
  input.max = form.dataset.maxQuantity;
  quantityDialog.querySelector('[data-quantity-product]').textContent = form.dataset.productName;
  quantityDialog.returnValue = '';
  quantityDialog.addEventListener('close', () => {
    const value = quantityDialog.returnValue;
    addButton(form)?.focus({ preventScroll: true });
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
const catsPanel = document.querySelector('.cats-panel');
const isMobileCatalog = () => window.matchMedia('(max-width: 900px)').matches;
if (catsPanel && isMobileCatalog()) catsPanel.open = false;
if (isMobileCatalog()) {
  document.querySelectorAll('.product-table tbody tr').forEach((row) => {
    row.addEventListener('click', (event) => {
      if (event.target.closest('[data-image-zoom], a, button, input, label')) return;
      const form = row.querySelector('.add-form');
      const button = form?.querySelector('.add-to-cart');
      if (!form || button?.disabled) return;
      form.requestSubmit();
    });
  });
}
document.querySelectorAll('.add-form').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (form.dataset.pending === 'true') return;
    form.dataset.pending = 'true';
    const qtyInput = rowQuantity(form);
    const quantity = qtyInput ? Math.max(1, Number(qtyInput.value || 1)) : await askQuantity(form);
    if (quantity === null || Number.isNaN(quantity)) { delete form.dataset.pending; return; }
    const data = new FormData(form);
    data.set('quantity', String(quantity));
    const button = addButton(form);
    form.dataset.pending = 'true';
    form.setAttribute('aria-busy', 'true');
    if (button) button.setAttribute('aria-disabled', 'true');
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
        if (button) button.removeAttribute('aria-disabled');
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

document.querySelectorAll('[data-b2b-control]').forEach((controlDialog) => {
  const collection = controlDialog.dataset.b2bControl;
  const sale = collection === 'akcijska';
  const searchForm = controlDialog.querySelector('[data-b2b-control-search]');
  const query = searchForm.elements.q;
  const status = controlDialog.querySelector('[data-b2b-control-status]');
  const matches = controlDialog.querySelector('[data-b2b-control-matches]');
  const selected = controlDialog.querySelector('[data-b2b-control-selected]');
  let activeQuery = '';
  let busy = false;

  const renderProducts = (list, products, action) => {
    list.replaceChildren();
    for (const product of products) {
      const row = document.createElement('li');
      const label = document.createElement('span');
      label.textContent = product.sifra ? product.naziv + ' · ' + product.sifra : product.naziv;
      const button = document.createElement('button');
      button.type = 'button';
      button.textContent = action === 'add' ? 'Dodaj' : 'Ukloni';
      button.setAttribute('aria-label', button.textContent + ' ' + label.textContent);
      button.addEventListener('click', () => updateProducts(action, product.pk));
      row.append(label);
      if (sale) {
        const regular = document.createElement('small');
        regular.textContent = 'Redovna VPC ' + product.regular_price + ' KM';
        row.append(regular);
        const discountForm = document.createElement('form');
        const discount = document.createElement('input');
        discount.type = 'text';
        discount.inputMode = 'decimal';
        discount.required = true;
        discount.maxLength = 14;
        discount.placeholder = 'Sniženje';
        discount.setAttribute('aria-label', 'Postotak sniženja za ' + product.naziv);
        if (action === 'remove') discount.value = product.discount_percent;
        const save = document.createElement('button');
        save.type = 'submit';
        save.textContent = action === 'add' ? 'Dodaj' : 'Sačuvaj';
        const unit = document.createElement('small');
        unit.textContent = '%';
        unit.setAttribute('aria-hidden', 'true');
        discountForm.append(discount, unit, save);
        discountForm.addEventListener('submit', (event) => {
          event.preventDefault();
          updateProducts('add', product.pk, discount.value);
        });
        row.append(discountForm);
      }
      if (!sale || action === 'remove') row.append(button);
      list.append(row);
    }
  };

  async function updateProducts(action, recordId, discount) {
    if (busy) return;
    busy = true;
    status.textContent = 'Učitavanje…';
    try {
      let importCount;
      if (action) {
        const body = new FormData();
        body.set('csrfmiddlewaretoken', searchForm.elements.csrfmiddlewaretoken.value);
        body.set('action', action);
        body.set(action === 'import' ? 'import_id' : 'product_id', recordId);
        if (discount !== undefined) body.set('discount_percent', discount);
        const saved = await fetch(searchForm.action, { method: 'POST', body });
        if (saved.status === 400 && saved.headers.get('Content-Type')?.includes('application/json')) {
          status.textContent = (await saved.json()).error;
          return;
        }
        if (!saved.ok) throw new Error('Spremanje nije uspjelo');
        if (action === 'import') importCount = (await saved.json()).import_count;
      }
      const url = new URL(searchForm.action);
      url.searchParams.set('q', activeQuery);
      const response = await fetch(url);
      if (!response.ok) throw new Error('Učitavanje nije uspjelo');
      const products = await response.json();
      renderProducts(matches, products.matches, 'add');
      renderProducts(selected, products.selected, 'remove');
      const feedback = [];
      if (action === 'import') {
        feedback.push(`Broj artikala iz odabranog uvoza u Novitetima: ${importCount}.`);
      } else if (action) {
        feedback.push('Promjena je sačuvana.');
      }
      if (activeQuery && !products.matches.length) feedback.push('Nema dodatnih artikala za ovu pretragu.');
      if (!products.selected.length) feedback.push('Nema odabranih artikala.');
      status.textContent = feedback.join(' ');
    } catch {
      status.textContent = 'Došlo je do greške. Pokušajte ponovo.';
    } finally {
      busy = false;
    }
  }

  document.querySelector(`[data-b2b-control-open="${collection}"]`).addEventListener('click', () => {
    controlDialog.showModal();
    updateProducts();
  });
  controlDialog.querySelector('[data-b2b-control-close]').addEventListener('click', () => controlDialog.close());
  searchForm.addEventListener('submit', (event) => {
    event.preventDefault();
    if (busy) return;
    activeQuery = query.value.trim();
    updateProducts();
  });

  const importPanel = controlDialog.querySelector('[data-b2b-import]');
  if (importPanel) {
    const importForm = importPanel.querySelector('form');
    const importSelect = importForm.querySelector('select');
    const importButton = importForm.querySelector('button');
    const importStatus = importPanel.querySelector('[data-b2b-import-status]');
    const dateFormat = new Intl.DateTimeFormat('bs-BA', { dateStyle: 'short', timeStyle: 'short' });

    importPanel.addEventListener('toggle', async () => {
      if (!importPanel.open) return;
      importSelect.disabled = true;
      importButton.disabled = true;
      importStatus.textContent = 'Učitavanje uvoza…';
      try {
        const url = new URL(searchForm.action);
        url.searchParams.set('uvozi', '1');
        const response = await fetch(url);
        if (!response.ok) throw new Error();
        const imports = (await response.json()).uvozi;
        importSelect.replaceChildren(new Option('Odaberi uvoz…', ''));
        for (const entry of imports) {
          const date = dateFormat.format(new Date(entry.kreiran));
          importSelect.add(new Option(entry.naziv + ' · ' + date, entry.pk));
        }
        importSelect.disabled = imports.length === 0;
        importStatus.textContent = imports.length ? '' : 'Nema uvoza s dostupnim B2B artiklima.';
      } catch {
        importStatus.textContent = 'Učitavanje uvoza nije uspjelo. Ponovo otvori opciju Uvoz.';
      }
    });
    importSelect.addEventListener('change', () => { importButton.disabled = !importSelect.value; });
    importForm.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (busy || !importSelect.value) return;
      importButton.disabled = true;
      importSelect.disabled = true;
      try {
        await updateProducts('import', importSelect.value);
      } finally {
        importSelect.disabled = false;
        importButton.disabled = !importSelect.value;
      }
    });
  }
});

const adminDialog = document.querySelector('[data-b2b-admin]');
if (adminDialog) {
  const detailDialog = document.querySelector('[data-b2b-admin-detail]');
  const tabs = [...adminDialog.querySelectorAll('[data-admin-section]')];
  const detailClose = detailDialog.querySelector('[data-admin-close]');
  let listUrl;
  let saving = false;
  const requests = new WeakMap();

  async function loadAdminContent(dialog, url, body) {
    requests.get(dialog)?.abort();
    const controller = new AbortController();
    requests.set(dialog, controller);
    const status = dialog.querySelector('[data-admin-status]');
    const content = dialog.querySelector('[data-admin-content]');
    if (!body) content.replaceChildren();
    status.textContent = body ? 'Spremanje…' : 'Učitavanje…';
    try {
      const response = await fetch(url, {
        method: body ? 'POST' : 'GET',
        body,
        signal: controller.signal,
      });
      if (!response.ok || response.redirected) throw new Error();
      const html = await response.text();
      if (controller.signal.aborted) return;
      content.innerHTML = html;
      status.textContent = '';
      if (dialog === detailDialog) {
        const focusTarget = content.querySelector('.errorlist:not(:empty), h2');
        if (focusTarget) {
          focusTarget.tabIndex = -1;
          focusTarget.focus({ preventScroll: true });
        }
      }
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = 'Došlo je do greške. Pokušajte ponovo.';
    }
  }

  function loadList(url) {
    listUrl = new URL(url, window.location.origin);
    for (const tab of tabs) {
      tab.setAttribute('aria-pressed', String(tab.dataset.adminSection === listUrl.pathname));
    }
    loadAdminContent(adminDialog, listUrl);
  }

  document.querySelector('[data-admin-open]').addEventListener('click', () => {
    adminDialog.showModal();
    loadList(tabs[0].dataset.adminSection);
  });
  adminDialog.querySelector('[data-admin-close]').addEventListener('click', () => adminDialog.close());
  detailClose.addEventListener('click', () => detailDialog.close());
  detailDialog.addEventListener('cancel', (event) => {
    if (saving) event.preventDefault();
  });
  for (const dialog of [adminDialog, detailDialog]) {
    dialog.addEventListener('close', () => {
      requests.get(dialog)?.abort();
      dialog.querySelector('[data-admin-content]').replaceChildren();
    });
  }

  for (const tab of tabs) {
    tab.addEventListener('click', () => loadList(tab.dataset.adminSection));
  }
  adminDialog.addEventListener('click', (event) => {
    const page = event.target.closest('[data-admin-page]');
    const edit = event.target.closest('[data-admin-edit]');
    const add = event.target.closest('[data-admin-open-record]');
    if (page) {
      event.preventDefault();
      loadList(new URL(page.getAttribute('href'), listUrl));
    }
    if (edit || add) {
      event.preventDefault();
      detailDialog.showModal();
      loadAdminContent(detailDialog, edit ? edit.href : add.dataset.adminOpenRecord);
    }
  });
  adminDialog.addEventListener('submit', (event) => {
    const form = event.target.closest('[data-admin-search]');
    if (!form) return;
    event.preventDefault();
    const url = new URL(form.action);
    url.searchParams.set('q', form.elements.q.value);
    loadList(url);
  });

  detailDialog.addEventListener('click', (event) => {
    const add = event.target.closest('[data-rabat-add]');
    if (!add || saving) return;
    const fieldset = add.closest('fieldset');
    const total = fieldset.querySelector('input[name="brand_rabats-TOTAL_FORMS"]');
    const index = Number(total.value);
    const row = fieldset.querySelector('[data-rabat-template]').content.cloneNode(true);
    for (const element of row.querySelectorAll('[name], [id], [for], [aria-describedby]')) {
      for (const attribute of ['name', 'id', 'for', 'aria-describedby']) {
        const value = element.getAttribute(attribute);
        if (value?.includes('__prefix__')) {
          element.setAttribute(attribute, value.replaceAll('__prefix__', String(index)));
        }
      }
    }
    const brand = row.querySelector('select');
    fieldset.querySelector('[data-rabat-rows]').append(row);
    total.value = index + 1;
    brand.focus();
  });

  detailDialog.addEventListener('submit', async (event) => {
    const form = event.target.closest('[data-admin-save]');
    if (!form) return;
    event.preventDefault();
    if (saving) return;
    saving = true;
    const submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    detailClose.disabled = true;
    try {
      await loadAdminContent(detailDialog, form.action, new FormData(form));
      loadList(listUrl);
    } finally {
      saving = false;
      submit.disabled = false;
      detailClose.disabled = false;
    }
  });
}
