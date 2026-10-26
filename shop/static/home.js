const notice = document.querySelector('#notice');
function showNotice(message) { notice.querySelector('p').textContent = message; notice.showModal(); }
document.querySelectorAll('[data-info]').forEach(button => button.addEventListener('click', () => showNotice(button.dataset.info)));
notice.querySelector('button').addEventListener('click', () => notice.close());
document.querySelector('[data-newsletter]')?.addEventListener('submit', event => {
  event.preventDefault();
  showNotice('Newsletter još nije povezan s ovim projektom. E-mail adresa nije spremljena.');
});
document.querySelectorAll('[data-brand-direction]').forEach(button => {
  button.addEventListener('click', () => {
    const row = button.closest('.bera-popular-brands__carousel').querySelector('.bera-popular-brands__row');
    row.scrollBy({left: Number(button.dataset.brandDirection) * row.clientWidth / 2, behavior: 'smooth'});
  });
});

// Keep the same product selected across collections and page reloads.
const wishlistKey = 'shop-wishlist';
let wishlist = new Set();
try {
  const savedWishlist = JSON.parse(localStorage.getItem(wishlistKey) || '[]');
  if (Array.isArray(savedWishlist)) wishlist = new Set(savedWishlist.map(String));
} catch {}
const wishlistButtons = document.querySelectorAll('[data-wishlist-id]');
function renderWishlist() {
  wishlistButtons.forEach(button => {
    const selected = wishlist.has(button.dataset.wishlistId);
    button.setAttribute('aria-pressed', String(selected));
    button.setAttribute('aria-label', `${selected ? 'Ukloni sa liste želja' : 'Dodaj na listu želja'}: ${button.dataset.productName}`);
  });
  const count = document.querySelector('.nav-actions [data-wishlist-open] .action-count');
  if (count) {
    count.textContent = wishlist.size;
    count.setAttribute('aria-label', `${wishlist.size} artikala`);
  }
}
wishlistButtons.forEach(button => button.addEventListener('click', () => {
  const id = button.dataset.wishlistId;
  if (wishlist.has(id)) wishlist.delete(id);
  else wishlist.add(id);
  try { localStorage.setItem(wishlistKey, JSON.stringify([...wishlist])); } catch {}
  renderWishlist();
  renderWishlistPage();
}));
renderWishlist();

function renderWishlistPage() {
  const page = document.querySelector('[data-wishlist-page]');
  if (!page) return;
  let visible = 0;
  page.querySelectorAll('.product-card--home-reference').forEach(card => {
    const id = card.querySelector('[data-wishlist-id]').dataset.wishlistId;
    card.hidden = !wishlist.has(id);
    if (!card.hidden) visible++;
  });
  page.querySelector('[data-wishlist-empty]').hidden = visible > 0;
  page.querySelector('[data-wishlist-total]').textContent = visible;
}
const wishlistPage = document.querySelector('[data-wishlist-page]');
if (wishlistPage) {
  const url = new URL(window.location.href);
  const ids = [...wishlist].slice(0, 500).join(',');
  if ((url.searchParams.get('ids') || '') !== ids) {
    url.searchParams.set('ids', ids);
    window.location.replace(url);
  } else {
    // Discard saved references to products no longer offered in the store.
    wishlist = new Set([...wishlistPage.querySelectorAll('[data-wishlist-id]')].map(button => button.dataset.wishlistId));
    try { localStorage.setItem(wishlistKey, JSON.stringify([...wishlist])); } catch {}
    renderWishlist();
    renderWishlistPage();
  }
}
document.querySelectorAll('[data-wishlist-open]').forEach(button => {
  button.addEventListener('click', event => {
    event.preventDefault();
    const url = new URL(button.dataset.wishlistOpen, window.location.origin);
    url.searchParams.set('ids', [...wishlist].slice(0, 500).join(','));
    window.location.href = url;
  });
});
window.addEventListener('storage', event => {
  if (event.key !== wishlistKey) return;
  if (wishlistPage) { window.location.reload(); return; }
  try {
    const saved = JSON.parse(event.newValue || '[]');
    wishlist = new Set(Array.isArray(saved) ? saved.map(String) : []);
    renderWishlist();
  } catch {}
});

const mobileMenu = document.querySelector('#mobileMenu');
const mobileMenuOpen = document.querySelector('#mobileMenuOpen');
const mobileMenuClose = document.querySelector('#mobileMenuClose');
if (mobileMenu && mobileMenuOpen && mobileMenuClose) {
  mobileMenuOpen.addEventListener('click', () => {
    mobileMenu.showModal();
    mobileMenuOpen.setAttribute('aria-expanded', 'true');
    document.documentElement.classList.add('mobile-menu-is-open');
  });
  mobileMenuClose.addEventListener('click', () => mobileMenu.close());
  mobileMenu.addEventListener('close', () => {
    mobileMenuOpen.setAttribute('aria-expanded', 'false');
    document.documentElement.classList.remove('mobile-menu-is-open');
  });
  mobileMenu.querySelectorAll('a').forEach(link => link.addEventListener('click', () => mobileMenu.close()));
  mobileMenu.addEventListener('click', event => {
    if (event.target !== mobileMenu) return;
    const bounds = mobileMenu.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) mobileMenu.close();
  });
  const mobileViewport = window.matchMedia('(max-width: 900px)');
  mobileViewport.addEventListener('change', event => {
    if (!event.matches && mobileMenu.open) mobileMenu.close();
  });
}


document.querySelector('#mobileSearchOpen')?.addEventListener('click', () => {
  const search = document.querySelector('.header-search input[type="search"]');
  if (search) {
    search.focus({ preventScroll: true });
  }
});

const searchBox = document.querySelector('[data-suggestions-url]');
if (searchBox) {
  const input = searchBox.querySelector('input[type="search"]');
  const popup = searchBox.querySelector('#searchSuggestions');
  const list = popup.querySelector('ul');
  const status = popup.querySelector('[role="status"]');
  let timer;
  let controller;
  let generation = 0;
  // Sticky inputs can make the browser reveal their original layout position.
  // Restore only the scroll offset captured for this keystroke, never on a timer.
  let typingPosition = null;
  let restoreFrame = null;
  input.addEventListener('beforeinput', () => {
    typingPosition = {left: window.scrollX, top: window.scrollY};
  });
  const cancelTypingRestore = () => {
    typingPosition = null;
    if (restoreFrame !== null) cancelAnimationFrame(restoreFrame);
  };
  window.addEventListener('wheel', cancelTypingRestore, {passive: true});
  window.addEventListener('touchmove', cancelTypingRestore, {passive: true});
  input.addEventListener('blur', cancelTypingRestore);
  const preserveTypingPosition = () => {
    if (!typingPosition) return;
    const position = typingPosition;
    typingPosition = null;
    if (restoreFrame !== null) cancelAnimationFrame(restoreFrame);
    window.scrollTo({...position, behavior: 'instant'});
    restoreFrame = requestAnimationFrame(() => {
      window.scrollTo({...position, behavior: 'instant'});
      restoreFrame = null;
    });
  };
  const closeSuggestions = () => {
    popup.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  };
  const openSuggestions = () => {
    popup.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };
  input.addEventListener('input', () => {
    clearTimeout(timer);
    controller?.abort();
    const current = ++generation;
    const query = input.value.trim();
    list.replaceChildren();
    preserveTypingPosition();
    if (!query) { closeSuggestions(); return; }
    status.textContent = 'Pretraživanje…';
    openSuggestions();
    timer = setTimeout(async () => {
      controller = new AbortController();
      const url = new URL(searchBox.dataset.suggestionsUrl, window.location.origin);
      url.searchParams.set('q', query);
      for (const key of ['category', 'brand', 'sale', 'bestselling']) {
        if (searchBox.dataset[key]) url.searchParams.set(key, searchBox.dataset[key]);
      }
      try {
        const response = await fetch(url, {signal: controller.signal});
        if (!response.ok) throw new Error('Search failed');
        const data = await response.json();
        if (current !== generation) return;
        status.textContent = data.products.length ? 'Prijedlozi artikala' : 'Nema pronađenih artikala.';
        list.replaceChildren();
        data.products.forEach(product => {
          const item = document.createElement('li');
          const link = document.createElement('a');
          link.href = product.url;
          const thumbnail = document.createElement('span');
          thumbnail.className = 'search-thumbnail';
          if (product.image) {
            const image = document.createElement('img');
            image.src = product.image;
            image.alt = '';
            thumbnail.append(image);
          }
          const name = document.createElement('span');
          name.className = 'search-product-name';
          name.textContent = product.name;
          const price = document.createElement('strong');
          price.textContent = product.price;
          link.append(thumbnail, name, price);
          item.append(link);
          list.append(item);
        });
      } catch (error) {
        if (error.name === 'AbortError' || current !== generation) return;
        status.textContent = 'Prijedlozi trenutno nisu dostupni. Pritisnite Enter za pretragu.';
      }
    }, 200);
  });
  searchBox.addEventListener('keydown', event => {
    const links = [...list.querySelectorAll('a')];
    if (event.key === 'Escape') {
      generation++;
      clearTimeout(timer);
      controller?.abort();
      closeSuggestions();
      input.focus({ preventScroll: true });
    } else if (event.key === 'ArrowDown' && !popup.hidden && links.length) {
      event.preventDefault();
      links[(links.indexOf(document.activeElement) + 1) % links.length].focus({ preventScroll: true });
    } else if (event.key === 'ArrowUp' && !popup.hidden && links.length) {
      event.preventDefault();
      const index = links.indexOf(document.activeElement);
      if (index <= 0) input.focus({ preventScroll: true }); else links[index - 1].focus({ preventScroll: true });
    }
  });
  searchBox.addEventListener('focusout', event => {
    if (!searchBox.contains(event.relatedTarget)) closeSuggestions();
  });
  input.addEventListener('focus', () => {
    if (input.value.trim() && list.children.length) openSuggestions();
  });
  document.addEventListener('click', event => {
    if (!searchBox.contains(event.target)) closeSuggestions();
  });
}

// Session cart: prices and availability are checked by the server.
document.querySelectorAll('[data-cart-open]').forEach(button => {
  button.addEventListener('click', () => { window.location.href = button.dataset.cartOpen; });
});
let cartToastTimer;
document.addEventListener('click', async event => {
    const button = event.target.closest('[data-cart-add]');
    if (!button || button.disabled) return;
    event.preventDefault();
    button.disabled = true;
    try {
      const response = await fetch(button.dataset.cartAdd, {
        method: 'POST',
        headers: {'X-CSRFToken': document.querySelector('#cartCsrf input').value, 'X-Requested-With': 'XMLHttpRequest'},
        body: new URLSearchParams({action: 'add', quantity: button.dataset.cartQuantity || '1'}),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Dodavanje u korpu nije uspjelo.');
      document.querySelectorAll('[data-cart-count]').forEach(count => {
        count.textContent = data.count;
        count.setAttribute('aria-label', `${data.count} artikala`);
      });
      let toast = document.querySelector('.cart-toast');
      if (!toast) {
        toast = document.createElement('p');
        toast.className = 'cart-toast';
        toast.setAttribute('role', 'status');
        document.body.append(toast);
      }
      document.querySelector('#quantityOfferConfirm')?.close();
      document.querySelector('#articlePlusOffer')?.close();
      if (data.offer) showArticlePlusOffer(data.offer);
      if (button.dataset.refreshCart === 'true') { window.location.reload(); return; }
      toast.textContent = data.message || 'Artikal je dodan u korpu.';
      toast.hidden = false;
      clearTimeout(cartToastTimer);
      cartToastTimer = setTimeout(() => { toast.hidden = true; }, 2500);
    } catch (error) {
      showNotice(error.message || 'Korpa trenutno nije dostupna. Pokušajte ponovo.');
    } finally {
      button.disabled = false;
    }
});

const categoryMenu = document.querySelector('.bera-category-menu');
if (categoryMenu) {
  document.addEventListener('click', event => {
    if (!categoryMenu.contains(event.target)) categoryMenu.open = false;
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && categoryMenu.open) {
      categoryMenu.open = false;
      categoryMenu.querySelector('summary').focus();
    }
  });
}

document.querySelectorAll('[data-account-open]').forEach(button => {
  button.addEventListener('click', () => { window.location.href = button.dataset.accountOpen; });
});


function showArticlePlusOffer(offer) {
  let dialog = document.querySelector('#articlePlusOffer');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'articlePlusOffer';
    dialog.className = 'article-plus-offer';
    dialog.setAttribute('aria-labelledby', 'articlePlusOfferTitle');
    document.body.append(dialog);
  }
  dialog.replaceChildren();
  const heading = document.createElement('h2');
  heading.id = 'articlePlusOfferTitle';
  heading.textContent = `−${offer.percent}%`;
  const message = document.createElement('p');
  message.className = 'article-plus-offer-message';
  message.textContent = `Uz ${offer.first_name} kupite i ovaj artikal uz ${offer.percent}% popusta:`;
  const link = document.createElement('a');
  link.href = offer.url;
  const name = document.createElement('span');
  name.textContent = offer.name;
  link.append(name);
  if (offer.image) {
    const image = document.createElement('img');
    image.src = offer.image;
    image.alt = offer.name;
    link.prepend(image);
  }
  const prices = document.createElement('p');
  prices.className = 'article-plus-offer-prices';
  const original = document.createElement('s');
  original.textContent = `${offer.regular_price} KM`;
  const price = document.createElement('strong');
  price.textContent = ` ${offer.price} KM`;
  prices.append(original, price);
  const add = document.createElement('button');
  add.type = 'button';
  add.autofocus = true;
  add.dataset.cartAdd = offer.add_url;
  add.dataset.refreshCart = String(window.location.pathname === '/korpa/');
  add.textContent = `Dodaj uz ${offer.percent}% popusta`;
  const close = document.createElement('button');
  close.type = 'button';
  close.className = 'article-plus-offer-close';
  close.textContent = 'Nastavi bez ovog artikla';
  close.addEventListener('click', () => dialog.close());
  dialog.append(heading, message, link, prices, add, close);
  dialog.showModal();
}


document.addEventListener('click', event => {
  const offer = event.target.closest('[data-quantity-offer]');
  if (!offer) return;
  let dialog = document.querySelector('#quantityOfferConfirm');
  if (!dialog) {
    dialog = document.createElement('dialog');
    dialog.id = 'quantityOfferConfirm';
    dialog.className = 'quantity-offer-confirm';
    dialog.setAttribute('aria-labelledby', 'quantityOfferConfirmTitle');
    document.body.append(dialog);
  }
  dialog.replaceChildren();
  const title = document.createElement('h2');
  title.id = 'quantityOfferConfirmTitle';
  title.textContent = 'Dodati u korpu?';
  const name = document.createElement('p');
  name.className = 'quantity-confirm-name';
  name.textContent = offer.dataset.productName;
  const details = document.createElement('p');
  details.textContent = `${offer.dataset.quantity} kom · −${offer.dataset.percent}%`;
  const total = document.createElement('strong');
  total.className = 'quantity-confirm-total';
  total.textContent = `${offer.dataset.total} KM`;
  const confirm = document.createElement('button');
  confirm.type = 'button';
  confirm.autofocus = true;
  confirm.dataset.cartAdd = offer.dataset.addUrl;
  confirm.dataset.cartQuantity = offer.dataset.quantity;
  confirm.textContent = `Da, dodaj ${offer.dataset.quantity} kom`;
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.className = 'quantity-confirm-cancel';
  cancel.textContent = 'Odustani';
  cancel.addEventListener('click', () => dialog.close());
  dialog.append(title, name, details, total, confirm, cancel);
  dialog.showModal();
});


document.addEventListener('click', event => {
  const close = event.target.closest('[data-close-quantity-offer]');
  if (close) close.closest('.cart-quantity-offers')?.remove();
});

// Preview the linked Ponuda + product without leaving the current product page.
document.addEventListener('click', event => {
  if (event.target.closest('[data-plus-preview-open]')) {
    document.querySelector('#productPlusPreview')?.showModal();
  }
  if (event.target.closest('[data-plus-preview-close]')) {
    document.querySelector('#productPlusPreview')?.close();
  }
});

const brandsDialog = document.querySelector('#brandsDialog');
document.querySelectorAll('[data-brands-open]').forEach(button => {
  button.addEventListener('click', () => brandsDialog?.showModal());
});
brandsDialog?.addEventListener('click', event => {
  if (event.target !== brandsDialog) return;
  const bounds = brandsDialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) brandsDialog.close();
});

// One shared clock for all visible promotion countdowns.
const saleCountdowns = [...document.querySelectorAll('[data-sale-countdown]')];
function updateSaleCountdowns() {
  let expired = false;
  const now = Date.now();
  saleCountdowns.forEach(timer => {
    const end = Date.parse(timer.dataset.saleCountdown);
    if (!Number.isFinite(end)) { timer.hidden = true; return; }
    const seconds = Math.max(0, Math.ceil((end - now) / 1000));
    if (!seconds) {
      timer.querySelector('[data-countdown-value]').textContent = 'Istekla';
      expired = true;
      return;
    }
    const days = Math.floor(seconds / 86400);
    const hours = Math.floor(seconds % 86400 / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    const pad = value => String(value).padStart(2, '0');
    timer.querySelector('[data-countdown-value]').textContent = `${days ? days + 'd ' : ''}${pad(hours)}:${pad(minutes)}:${pad(seconds % 60)}`;
  });
  if (expired) { clearInterval(saleCountdownInterval); window.location.reload(); }
}
let saleCountdownInterval;
if (saleCountdowns.length) {
  saleCountdownInterval = setInterval(updateSaleCountdowns, 1000);
  updateSaleCountdowns();
}

const productZoomDialog = document.querySelector('#productZoomDialog');
document.querySelector('[data-product-zoom]')?.addEventListener('click', () => productZoomDialog?.showModal());
productZoomDialog?.addEventListener('click', event => {
  if (event.target !== productZoomDialog) return;
  const bounds = productZoomDialog.getBoundingClientRect();
  if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) productZoomDialog.close();
});
