document.addEventListener('DOMContentLoaded', () => {
    const gallery = document.getElementById('mainProductImageWrap');
    const main = document.getElementById('mainProductImage');
    const thumbnails = [...document.querySelectorAll('#productThumbnails .product-thumbnail')];
    if (!gallery || main?.tagName !== 'IMG' || thumbnails.length < 2) return;
    document.querySelectorAll('.pd-gallery-bar [data-gallery-step]').forEach((button) => {
        button.hidden = false;
        button.addEventListener('dblclick', (event) => event.preventDefault());
        // Handle each tap ourselves on Safari; cancel its double-tap zoom gesture
        // and synthetic click so one tap always advances exactly one image.
        let touchStart = null;
        button.addEventListener('touchstart', (event) => {
            const touch = event.touches.length === 1 ? event.touches[0] : null;
            touchStart = touch ? {x: touch.clientX, y: touch.clientY} : null;
        }, {passive: true});
        button.addEventListener('touchcancel', () => { touchStart = null; }, {passive: true});
        button.addEventListener('touchend', (event) => {
            const start = touchStart;
            touchStart = null;
            const touch = event.changedTouches[0];
            if (!start || !touch || event.touches.length || !event.cancelable) return;
            if (Math.abs(touch.clientX - start.x) > 10 || Math.abs(touch.clientY - start.y) > 10) return;
            event.preventDefault();
            button.click();
        }, {passive: false});
        button.addEventListener('click', () => {
            const current = Math.max(0, thumbnails.findIndex((thumb) => thumb.classList.contains('active')));
            const next = (current + Number(button.dataset.galleryStep) + thumbnails.length) % thumbnails.length;
            thumbnails[next].click();
        });
    });
});
