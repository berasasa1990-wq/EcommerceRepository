(() => {
    const menu = document.getElementById('catsDropdown');
    const button = document.getElementById('catsDropdownBtn');
    const panel = document.getElementById('catsDropdownPanel');
    if (!menu || !button || !panel || menu.dataset.categoryMenuReady) return;
    menu.dataset.categoryMenuReady = 'true';

    function setOpen(open) {
        menu.classList.toggle('is-open', open);
        button.setAttribute('aria-expanded', String(open));
        panel.hidden = !open;
        document.body.classList.toggle('cats-sidebar-open', open && window.innerWidth > 1024);
    }

    // Categories can always be closed, including pages rendered with an open sidebar.
    menu.classList.remove('cats-dropdown--locked');
    button.removeAttribute('aria-disabled');
    setOpen(!panel.hidden);

    button.addEventListener('click', event => {
        event.preventDefault();
        setOpen(panel.hidden);
    });

    menu.addEventListener('click', event => {
        const toggle = event.target.closest('[data-cats-toggle]');
        if (!toggle || !menu.contains(toggle) || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const item = toggle.closest('.cats-dropdown__item--has-sub');
        if (!item) return;
        event.preventDefault();
        const open = !item.classList.contains('is-open');
        menu.querySelectorAll('.cats-dropdown__item--has-sub').forEach(other => {
            const expanded = other === item && open;
            other.classList.toggle('is-open', expanded);
            other.querySelector('[data-cats-toggle]')?.setAttribute('aria-expanded', String(expanded));
        });
    });

    document.addEventListener('click', event => {
        if (!menu.contains(event.target)) setOpen(false);
    });
    document.addEventListener('keydown', event => {
        if (event.key === 'Escape' && !panel.hidden) {
            setOpen(false);
            button.focus();
        }
    });
    window.addEventListener('resize', () => {
        if (window.innerWidth <= 1024) setOpen(false);
    });
})();
