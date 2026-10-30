(() => {
    if (!document.body.classList.contains('product-mobile-reference')) return;
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

// Share the current article, with a copy-link fallback on desktop.
document.querySelector('[data-product-share]')?.addEventListener('click', async () => {
    const status = document.querySelector('.pd-reference-share-status');
    const title = document.querySelector('.product-detail-title-row h1')?.textContent.trim() || document.title;
    try {
        if (navigator.share) {
            await navigator.share({ title, url: location.href });
        } else {
            await navigator.clipboard.writeText(location.href);
            if (status) { status.textContent = 'Link artikla je kopiran.'; status.hidden = false; }
        }
    } catch (error) {
        if (error.name !== 'AbortError' && status) {
            status.textContent = 'Link artikla: ' + location.href;
            status.hidden = false;
        }
    }
});
