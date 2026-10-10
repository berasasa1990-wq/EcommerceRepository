document.addEventListener('DOMContentLoaded', () => {
    const list = document.querySelector('.bera-cart-scroll');
    if (!list) return;
    const rows = Array.from(list.querySelectorAll('.cart-item'));
    const layout = document.querySelector('.cart-layout');
    const summary = document.querySelector('.cart-summary');
    const trust = document.querySelector('.cart-trust');
    const fit = () => {
        if (rows.length > 4) {
            const height = rows.slice(0, 4).reduce((sum, row) => sum + row.getBoundingClientRect().height, 0);
            if (height > 0) list.style.setProperty('--cart-visible-height', `${height}px`);
        }
        if (layout && summary && trust && rows.length) {
            const row = rows[Math.min(3, rows.length - 1)];
            const target = row.getBoundingClientRect().top - layout.getBoundingClientRect().top;
            const offset = Math.max(target, summary.getBoundingClientRect().height + 12);
            layout.style.setProperty('--cart-trust-offset', `${offset}px`);
        }
    };
    fit();
    if ('ResizeObserver' in window) {
        const observer = new ResizeObserver(fit);
        rows.slice(0, 4).forEach(row => observer.observe(row));
        if (summary) observer.observe(summary);
    } else window.addEventListener('resize', fit);
});
