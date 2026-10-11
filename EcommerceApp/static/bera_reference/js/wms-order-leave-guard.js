(() => {
    const form = document.getElementById('onlineOrderForm');
    if (!form) return;
    let changed = false;
    let leaving = false;
    const snapshot = () => JSON.stringify([...new FormData(form).entries()]
        .filter(([name]) => name !== 'csrfmiddlewaretoken'));
    const initial = snapshot();
    const hasChanges = () => changed || snapshot() !== initial;
    const warning = 'Želite li izaći? Svi nespremljeni podaci narudžbe bit će izgubljeni.';

    document.addEventListener('input', event => {
        if (form.contains(event.target) || event.target.closest('#orderCatalog, #orderCustomerDialog')) {
            changed = true;
        }
    });
    window.addEventListener('beforeunload', event => {
        if (leaving || !hasChanges()) return;
        event.preventDefault();
        event.returnValue = warning;
    });
    document.addEventListener('click', event => {
        const link = event.target.closest('a[href]');
        if (!link || event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey ||
            event.shiftKey || event.altKey || link.target === '_blank' || link.hasAttribute('download')) return;
        const url = new URL(link.href, location.href);
        if (!['http:', 'https:'].includes(url.protocol)) return;
        if (url.pathname === location.pathname && url.search === location.search && url.hash) return;
        if (!hasChanges()) return;
        if (!window.confirm(warning)) {
            event.preventDefault();
        } else {
            leaving = true;
        }
    });
    // The order form's validation runs before this bubbling submit listener.
    document.addEventListener('submit', event => {
        if (event.target === form && !event.defaultPrevented) leaving = true;
    });
    window.addEventListener('pageshow', () => { leaving = false; });
})();
