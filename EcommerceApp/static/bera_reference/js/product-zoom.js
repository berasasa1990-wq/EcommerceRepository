(() => {
    const button = document.getElementById('productZoomOpen');
    const dialog = document.getElementById('productZoomDialog');
    const image = document.getElementById('productZoomImage');
    if (!button || !dialog || !image) return;
    button.addEventListener('click', () => {
        const main = document.getElementById('mainProductImage');
        if (!main || main.tagName !== 'IMG') return;
        image.src = main.dataset.fullSrc || main.currentSrc || main.src;
        image.alt = main.alt;
        dialog.showModal();
        document.documentElement.classList.add('product-zoom-open');
    });
    dialog.querySelector('.pd-zoom-close').addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', (event) => {
        const rect = dialog.getBoundingClientRect();
        if (event.target === dialog && (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom)) dialog.close();
    });
    dialog.addEventListener('close', () => {
        document.documentElement.classList.remove('product-zoom-open');
        button.focus({ preventScroll: true });
    });
})();
