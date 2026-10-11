document.querySelectorAll('.bera-popular-brands__carousel').forEach((carousel) => {
    const row = carousel.querySelector('.bera-popular-brands__row');
    carousel.querySelectorAll('[data-brand-direction]').forEach((button) => {
        button.addEventListener('click', () => {
            row.scrollBy({
                left: Number(button.dataset.brandDirection) * row.clientWidth * 0.8,
                behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth',
            });
        });
    });
});
