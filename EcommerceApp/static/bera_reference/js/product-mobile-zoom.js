// Preserve the source layout while making zoom available by tapping the image.
(() => {
    const gallery = document.getElementById('mainProductImageWrap');
    const button = document.getElementById('productZoomOpen');
    if (!gallery || !button) return;
    let start = null;
    gallery.addEventListener('pointerdown', event => {
        if (!event.isPrimary || event.button !== 0 || event.target.id !== 'mainProductImage') return;
        start = {x: event.clientX, y: event.clientY, id: event.pointerId};
    });
    gallery.addEventListener('pointerup', event => {
        const origin = start;
        start = null;
        if (!origin || origin.id !== event.pointerId || !window.matchMedia('(max-width: 1024px)').matches) return;
        if (Math.hypot(event.clientX - origin.x, event.clientY - origin.y) < 10) button.click();
    });
    gallery.addEventListener('pointercancel', () => { start = null; });
})();
