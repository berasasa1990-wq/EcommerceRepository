document.addEventListener('DOMContentLoaded', () => {
    const gallery = document.getElementById('mainProductImageWrap');
    const main = document.getElementById('mainProductImage');
    const thumbnails = [...document.querySelectorAll('#productThumbnails .product-thumbnail')];
    if (!gallery || main?.tagName !== 'IMG' || thumbnails.length < 2) return;
    document.querySelectorAll('.pd-gallery-bar [data-gallery-step]').forEach((button) => {
        button.hidden = false;
        button.addEventListener('click', () => {
            const current = Math.max(0, thumbnails.findIndex((thumb) => thumb.classList.contains('active')));
            const next = (current + Number(button.dataset.galleryStep) + thumbnails.length) % thumbnails.length;
            thumbnails[next].click();
        });
    });
});
