const productGallery = document.querySelector('.bera-product-reference .product-gallery');
const productInfo = document.querySelector('.bera-product-reference .product-detail-info');
const productTrust = productInfo?.querySelector('.pd-desktop-trust');
if (productGallery && productInfo && productTrust) {
  const desktopGallery = window.matchMedia('(min-width: 1025px)');
  const alignGallery = () => {
    if (!desktopGallery.matches) {
      productGallery.style.removeProperty('--bera-gallery-height');
      return;
    }
    const height = productTrust.getBoundingClientRect().bottom - productGallery.getBoundingClientRect().top;
    if (height > 0) productGallery.style.setProperty('--bera-gallery-height', `${Math.round(height)}px`);
  };
  new ResizeObserver(alignGallery).observe(productInfo);
  desktopGallery.addEventListener('change', alignGallery);
  window.addEventListener('resize', alignGallery);
  document.fonts?.ready.then(alignGallery);
  alignGallery();
}

document.querySelectorAll('.product-detail-variations-block').forEach((block) => {
  const choices = block.querySelector('.bera-variant-choices');
  const buttons = [...block.querySelectorAll('[data-bera-variant]')];
  const rows = [...block.querySelectorAll('.product-detail-variation-row')];
  if (!choices || !buttons.length || buttons.length !== rows.length) return;
  let selectedIndex = 0;
  const select = (index) => {
    selectedIndex = index;
    rows.forEach((row, i) => { row.hidden = i !== index; });
    buttons.forEach((button, i) => { button.setAttribute('aria-pressed', String(i === index)); });
    const image = document.getElementById('mainProductImage');
    const source = buttons[index].dataset.image;
    if (image?.tagName === 'IMG' && source) {
      image.src = source;
      image.removeAttribute('srcset');
      image.dataset.fullSrc = source;
    }
  };
  buttons.forEach((button, index) => button.addEventListener('click', () => select(index)));
  block.querySelector('#detailVariations')?.addEventListener('mouseleave', () => {
    queueMicrotask(() => select(selectedIndex));
  });
  block.classList.add('bera-variants-ready');
  choices.hidden = false;
  select(0);
});

document.querySelector('.bera-product-read-more')?.addEventListener('click', (event) => {
  const description = document.getElementById('bera-panel-description');
  const tab = document.getElementById('bera-tab-description');
  if (!description || !tab) return;
  event.preventDefault();
  tab.click();
  description.scrollIntoView({
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'start',
  });
  description.focus({ preventScroll: true });
});

document.querySelectorAll('.bera-product-tabs').forEach((list) => {
  const tabs = [...list.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    tabs.forEach((item) => {
      const active = item === tab;
      item.setAttribute('aria-selected', String(active));
      item.tabIndex = active ? 0 : -1;
      document.getElementById(item.getAttribute('aria-controls')).hidden = !active;
    });
  };
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => select(tab));
    tab.addEventListener('keydown', (event) => {
      let next;
      if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
      if (event.key === 'ArrowLeft') next = tabs[(index + tabs.length - 1) % tabs.length];
      if (event.key === 'Home') next = tabs[0];
      if (event.key === 'End') next = tabs[tabs.length - 1];
      if (!next) return;
      event.preventDefault();
      select(next);
      next.focus();
    });
  });
});
