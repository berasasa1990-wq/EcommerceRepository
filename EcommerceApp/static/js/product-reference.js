(() => {
    if (!document.body.classList.contains('product-mobile-reference')) return;
    const actions = document.querySelector('.header .nav-actions');
    if (actions) {
        const search = document.createElement('button');
        search.type = 'button'; search.className = 'nav-action-link pd-reference-search';
        search.setAttribute('aria-label', 'Pretraga'); search.setAttribute('data-mobile-home-search', '');
        search.innerHTML = document.querySelector('.header-search-icon')?.innerHTML || '⌕';
        actions.prepend(search);
    }
    const thumbs = document.getElementById('productThumbnails');
    if (thumbs) {
        const controls = document.createElement('div');
        controls.className = 'pd-thumbnail-scroll-controls';
        controls.hidden = true;
        const up = document.createElement('button');
        const down = document.createElement('button');
        up.type = down.type = 'button';
        up.textContent = '↑';
        down.textContent = '↓';
        up.setAttribute('aria-label', 'Prethodne dodatne slike');
        down.setAttribute('aria-label', 'Sljedeće dodatne slike');
        up.setAttribute('aria-controls', thumbs.id);
        down.setAttribute('aria-controls', thumbs.id);
        controls.append(up, down);
        thumbs.after(controls);
        const updateScrollControls = () => {
            controls.hidden = thumbs.scrollHeight <= thumbs.clientHeight + 1;
            up.disabled = thumbs.scrollTop <= 1;
            down.disabled = thumbs.scrollTop + thumbs.clientHeight >= thumbs.scrollHeight - 1;
        };
        up.addEventListener('click', () => thumbs.scrollBy({ top: -86, behavior: 'smooth' }));
        down.addEventListener('click', () => thumbs.scrollBy({ top: 86, behavior: 'smooth' }));
        thumbs.addEventListener('scroll', updateScrollControls, { passive: true });
        new ResizeObserver(updateScrollControls).observe(thumbs);
        updateScrollControls();
    }
    if (gallery && thumbs) {
        const counter = document.createElement('span'); counter.className = 'pd-reference-counter';
        gallery.append(counter);
        const dots = document.getElementById('pdDesktopDots');
        const allThumbs = () => [...thumbs.querySelectorAll('.product-thumbnail')];
        const update = () => {
            const all = allThumbs();
            const active = Math.max(0, all.findIndex(item => item.classList.contains('active')));
            counter.textContent = `${all.length ? active + 1 : 0} / ${all.length}`;
            if (!dots) return;
            if (all.length < 2) {
                dots.hidden = true;
                dots.replaceChildren();
                return;
            }
            dots.hidden = false;
            if (dots.childElementCount !== all.length) {
                dots.replaceChildren(...all.map((_, index) => {
                    const dot = document.createElement('button');
                    dot.type = 'button';
                    dot.className = 'pd-desktop-dot';
                    dot.setAttribute('aria-label', `Prikaži sliku ${index + 1}`);
                    dot.addEventListener('click', () => allThumbs()[index]?.click());
                    return dot;
                }));
            }
            [...dots.children].forEach((dot, index) => dot.classList.toggle('is-active', index === active));
        };
        update(); new MutationObserver(update).observe(thumbs, { subtree: true, attributes: true, attributeFilter: ['class'] });
    }
    const options = document.getElementById('productOtherOptionsBtn');
    const priceArea = document.querySelector('.pd-options-price__price');
    if (options && priceArea) {
        const origin = document.createComment('options original position');
        options.before(origin);
        const mobile = window.matchMedia('(max-width: 767px)');
        function placeOptions() {
            if (!mobile.matches) {
                if (options.previousSibling !== origin) origin.after(options);
                return;
            }
            const visiblePrice = [...priceArea.children].find(node => node !== options && !node.hidden && getComputedStyle(node).display !== 'none');
            const target = visiblePrice?.querySelector('.card-dwell-prices') || visiblePrice || priceArea;
            if (options.parentElement !== target) target.append(options);
        }
        placeOptions();
        mobile.addEventListener('change', placeOptions);
        new MutationObserver(placeOptions).observe(priceArea, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden', 'class'] });
    }
})();

/* Align the desktop gallery bottom with the delivery information. */
(() => {
    const image = document.getElementById('mainProductImageWrap');
    const delivery = document.querySelector('.pd-desktop-trust li');
    const info = document.getElementById('productDetailInfo');
    if (!image || !delivery || !info) return;
    const desktop = matchMedia('(min-width:1025px)');
    let frame;
    const update = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
            if (!desktop.matches) {
                image.style.removeProperty('--desktop-gallery-height');
                return;
            }
            const height = Math.round(delivery.getBoundingClientRect().bottom - image.getBoundingClientRect().top);
            if (height >= 180) image.style.setProperty('--desktop-gallery-height', height + 'px');
        });
    };
    new ResizeObserver(update).observe(info);
    desktop.addEventListener('change', update);
    window.addEventListener('resize', update, {passive:true});
    document.fonts?.ready.then(update);
    update();
})();

/* Existing rich-text descriptions keep their markup and gain feature styling. */
document.querySelectorAll('.pd-description-formatted p').forEach(paragraph => {
    const text = paragraph.textContent.trim();
    if (text && text.length <= 120 && !paragraph.closest('li') && !paragraph.querySelector('img,video,table,br')) {
        paragraph.classList.add('pd-description-feature');
    }
});
