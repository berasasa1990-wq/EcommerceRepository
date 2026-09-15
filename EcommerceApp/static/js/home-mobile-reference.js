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
