// Recover a missing gallery image without retry loops or storage requests during rendering.
(() => {
    const image = document.getElementById('mainProductImage');
    if (!image || image.tagName !== 'IMG') return;
    const thumbnails = [...document.querySelectorAll('#productThumbnails .product-thumbnail')];
    const failed = new Set();
    function absolute(url) {
        try { return new URL(url, document.baseURI).href; } catch (_) { return url; }
    }
    function recover() {
        const broken = absolute(image.currentSrc || image.src);
        failed.add(broken);
        // A failed responsive candidate must not be selected again for this image.
        if (image.src) failed.add(absolute(image.src));
        thumbnails.forEach(thumb => {
            if (failed.has(absolute(thumb.dataset.src || ''))) thumb.hidden = true;
        });
        const next = thumbnails.find(thumb => thumb.dataset.src && !failed.has(absolute(thumb.dataset.src)));
        if (!next) return;
        image.removeAttribute('srcset');
        image.dataset.fullSrc = next.dataset.full || next.dataset.src;
        thumbnails.forEach(thumb => {
            thumb.classList.toggle('active', thumb === next);
            thumb.setAttribute('aria-pressed', thumb === next ? 'true' : 'false');
        });
        image.src = next.dataset.src;
    }
    image.addEventListener('error', recover);
    // The initial request can fail before this deferred script is evaluated.
    if (image.complete && image.naturalWidth === 0) recover();
})();
