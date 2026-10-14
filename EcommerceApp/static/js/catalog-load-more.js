(() => {
  document.querySelectorAll('[data-catalog-more]').forEach((controls) => {
    if (controls.dataset.bound) return;
    controls.dataset.bound = '1';
    const catalog = controls.closest('.product-catalog');
    const grid = catalog?.querySelector('[data-catalog-grid]');
    const button = controls.querySelector('[data-catalog-next]');
    const status = controls.querySelector('[data-catalog-progress]');
    if (!grid || !button) return;
    button.addEventListener('click', async () => {
      if (button.disabled) return;
      button.disabled = true;
      button.textContent = 'Učitavanje…';
      grid.setAttribute('aria-busy', 'true');
      try {
        const response = await fetch(button.dataset.catalogNext, { credentials: 'same-origin' });
        if (!response.ok) throw new Error('Failed to load products');
        const parsed = new DOMParser().parseFromString(await response.text(), 'text/html');
        const nextGrid = parsed.querySelector('[data-catalog-grid]');
        const nextControls = nextGrid?.closest('.product-catalog').querySelector('[data-catalog-more]');
        if (!nextGrid || !nextControls) throw new Error('Missing catalog');
        const cards = [...nextGrid.children];
        if (!cards.length) throw new Error('Empty batch');
        // HTML responses contain unbound controls; bind only after inserting them.
        cards.forEach((card) => card.querySelectorAll('[data-catalog-add-bound]').forEach((el) => delete el.dataset.catalogAddBound));
        grid.append(...cards);
        document.dispatchEvent(new CustomEvent('catalog:loaded', { detail: { root: grid } }));
        status.textContent = `Prikazano ${grid.children.length} od ${controls.dataset.total} artikala`;
        const next = nextControls.querySelector('[data-catalog-next]');
        if (next) button.dataset.catalogNext = next.dataset.catalogNext;
        else button.hidden = true;
      } catch (error) {
        status.textContent = 'Učitavanje nije uspjelo. Pokušajte ponovo.';
      } finally {
        grid.removeAttribute('aria-busy');
        button.disabled = false;
        button.textContent = 'Prikaži još';
      }
    });
  });
})();
