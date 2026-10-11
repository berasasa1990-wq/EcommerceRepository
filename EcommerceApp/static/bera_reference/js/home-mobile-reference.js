(() => {
    if (!document.body.classList.contains('home-mobile-reference')) return;
    const actions = document.querySelector('.header .nav-actions');
    if (actions) {
        const search = document.createElement('button');
        search.type = 'button';
        search.className = 'nav-action-link mobile-home-search-action';
        search.setAttribute('aria-label', 'Pretraga');
        search.setAttribute('data-mobile-home-search', '');
        search.innerHTML = document.querySelector('.header-search-icon')?.innerHTML || '⌕';
        actions.prepend(search);
    }
    const input = document.getElementById('searchInput');
    if (input && window.matchMedia('(max-width: 767px)').matches) {
        input.placeholder = 'Pretraži proizvode i brendove';
    }
})();

(() => {
    const track = document.getElementById('homeCategoryTrack');
    if (!track) return;
    track.querySelectorAll('.mobile-home-category-icon').forEach(image => {
        const fallback = image.parentElement.querySelector('[data-category-icon-fallback]');
        let retries = 0;
        let retryTimer = null;
        const loaded = () => {
            clearTimeout(retryTimer);
            retryTimer = null;
            image.hidden = false;
            if (fallback) fallback.hidden = true;
        };
        const failed = () => {
            image.hidden = true;
            if (fallback) fallback.hidden = false;
            if (retries >= 2 || retryTimer !== null) return;
            retries += 1;
            retryTimer = setTimeout(() => {
                retryTimer = null;
                image.src = image.getAttribute('src');
            }, retries * 1000);
        };
        image.addEventListener('load', loaded);
        image.addEventListener('error', failed);
        if (image.complete) {
            if (image.naturalWidth > 0) loaded();
            else failed();
        }
    });
    const buttons = document.querySelectorAll('[data-category-scroll]');
    const update = () => {
        const max = track.scrollWidth - track.clientWidth;
        buttons.forEach(button => {
            button.disabled = Number(button.dataset.categoryScroll) < 0
                ? track.scrollLeft <= 1 : track.scrollLeft >= max - 1;
        });
    };
    buttons.forEach(button => button.addEventListener('click', () => {
        track.scrollBy({
            left: Number(button.dataset.categoryScroll) * track.clientWidth,
            behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'
        });
    }));
    track.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update, { passive: true });
    if (window.ResizeObserver) new ResizeObserver(update).observe(track);
    update();
})();
