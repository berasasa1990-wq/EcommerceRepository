(function () {
    const mobile = window.matchMedia('(max-width: 767px)');
    const selector = '.product-card button[data-stock-notify]';
    function fit(button) {
        const label = button.querySelector('[data-stock-notify-label]');
        if (!label) return;
        label.style.removeProperty('font-size');
        if (!mobile.matches || !button.clientWidth) return;
        const style = getComputedStyle(button);
        const available = button.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
        if (available <= 0) return;
        // Measure the complete label instead of clipping it at the button edge.
        label.style.setProperty('font-size', '12px', 'important');
        let size = 12;
        const text = label.querySelector('.stock-notify-label--mobile') || label;
        while (Math.max(label.scrollWidth, text.getBoundingClientRect().width) > Math.floor(available) && size > 8) {
            size -= 0.25;
            label.style.setProperty('font-size', size + 'px', 'important');
        }
    }
    const observer = new ResizeObserver(entries => entries.forEach(entry => fit(entry.target)));
    const bound = new WeakSet();
    function bind() {
        document.querySelectorAll(selector).forEach(button => {
            if (bound.has(button)) return;
            bound.add(button);
            observer.observe(button);
            new MutationObserver(() => fit(button)).observe(button, { childList: true, characterData: true, subtree: true });
            fit(button);
        });
    }
    bind();
    mobile.addEventListener('change', () => document.querySelectorAll(selector).forEach(fit));
    document.fonts?.ready.then(() => document.querySelectorAll(selector).forEach(fit));
    new MutationObserver(bind).observe(document.body, { childList: true, subtree: true });
})();
