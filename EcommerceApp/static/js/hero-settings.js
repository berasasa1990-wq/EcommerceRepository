(() => {
    const root = document.getElementById('heroSettingsForm');
    if (!root) return;
    const list = document.getElementById('heroBannerForms');
    const add = document.getElementById('addHeroBanner');
    const total = document.getElementById('id_banners-TOTAL_FORMS');
    const max = Number(document.getElementById('id_banners-MAX_NUM_FORMS').value);
    const activeCount = () => list.querySelectorAll('[data-hero-banner-form]:not([hidden])').length;
    const refresh = () => { add.disabled = activeCount() >= max; };
    add.addEventListener('click', () => {
        const index = Number(total.value);
        if (activeCount() >= max) return;
        list.insertAdjacentHTML('beforeend', document.getElementById('heroBannerEmpty').innerHTML.replaceAll('__prefix__', String(index)));
        list.lastElementChild.querySelector('[name$="-redoslijed"]').value = index + 1;
        total.value = index + 1;
        refresh();
        list.lastElementChild.querySelector('textarea').focus();
    });
    root.addEventListener('click', event => {
        const button = event.target.closest('[data-remove-banner]');
        if (!button) return;
        const card = button.closest('[data-hero-banner-form]');
        card.querySelector('[name$="-DELETE"]').checked = true;
        card.querySelectorAll('[required]').forEach(input => { input.required = false; });
        card.hidden = true;
        refresh();
    });
    const previews = new WeakMap();
    root.addEventListener('change', event => {
        const input = event.target;
        if (input.type !== 'file' || !input.files[0]) return;
        const preview = input.closest('.hero-settings__card').querySelector('.hero-settings__preview');
        if (previews.has(preview)) URL.revokeObjectURL(previews.get(preview));
        const url = URL.createObjectURL(input.files[0]);
        previews.set(preview, url);
        const image = document.createElement('img');
        image.src = url;
        image.alt = 'Pregled odabrane slike';
        preview.replaceChildren(image);
    });
    refresh();
})();
