document.addEventListener('DOMContentLoaded', () => {
    const button = document.querySelector('.pd-mobile-zoom');
    const main = document.getElementById('mainProductImage');
    if (!button || !main) return;
    const dialog = document.createElement('dialog');
    dialog.className = 'pd-mobile-lightbox';
    dialog.setAttribute('aria-label', 'Uvećana slika proizvoda');
    const close = document.createElement('button');
    close.type = 'button';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Zatvori uvećanu sliku');
    const image = document.createElement('img');
    image.alt = main.alt;
    dialog.append(close, image);
    document.body.append(dialog);
    button.addEventListener('click', () => {
        image.src = main.dataset.fullSrc || main.currentSrc || main.src;
        dialog.showModal();
    });
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => {
        if (event.target === dialog) dialog.close();
    });
    dialog.addEventListener('close', () => button.focus());
});
