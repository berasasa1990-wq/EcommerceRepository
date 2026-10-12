(() => {
    const desktop = matchMedia('(min-width: 1025px)');
    const mainCategories = [...document.querySelectorAll('.bera-category-menu > div > .bera-category-branch')];
    mainCategories.forEach(category => {
        category.addEventListener('toggle', () => {
            if (!desktop.matches || !category.open) return;
            mainCategories.forEach(other => { if (other !== category) other.open = false; });
        });
    });
    desktop.addEventListener('change', () => {
        if (!desktop.matches) return;
        const firstOpen = mainCategories.find(category => category.open);
        mainCategories.forEach(category => { if (category !== firstOpen) category.open = false; });
    });
    const dropdowns = [...document.querySelectorAll('.desktop-category-dropdown')];
    dropdowns.forEach(dropdown => {
        dropdown.addEventListener('toggle', () => {
            if (dropdown.open) dropdowns.forEach(other => { if (other !== dropdown) other.open = false; });
        });
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape') dropdowns.forEach(dropdown => {
            if (dropdown.open) { dropdown.open = false; dropdown.querySelector('summary').focus(); }
        });
    });
    document.addEventListener('click', event => {
        dropdowns.forEach(dropdown => { if (!dropdown.contains(event.target)) dropdown.open = false; });
        if (!matchMedia('(min-width: 1025px)').matches) return;
        if (event.target.closest('.mobile-home-heading [data-mobile-home-menu]')) {
            const nav = document.querySelector('.desktop-category-nav');
            if (nav) { nav.scrollIntoView({behavior:'smooth',block:'center'}); nav.querySelector('summary, a')?.focus({preventScroll:true}); return; }
            const toggle = document.getElementById('catsDropdownBtn');
            if (toggle) {
                toggle.scrollIntoView({ behavior: 'smooth', block: 'center' });
                if (toggle.getAttribute('aria-expanded') !== 'true') toggle.click();
                toggle.focus({ preventScroll: true });
            }
        }
    });
})();
