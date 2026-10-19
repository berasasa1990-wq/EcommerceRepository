(() => {
    document.querySelectorAll('[data-hero-slider]').forEach(root => {
        const slides = [...root.querySelectorAll('[data-hero-slide]')];
        if (slides.length < 2) return;
        const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');
        let index = 0;
        let paused = reducedMotion.matches;
        let timer;
        let hovered = false;
        const show = next => {
            index = (next + slides.length) % slides.length;
            slides.forEach((slide, i) => { slide.hidden = i !== index; });
        };
        const schedule = () => {
            clearInterval(timer);
            if (!paused && !hovered && !document.hidden && !root.contains(document.activeElement)) {
                timer = setInterval(() => show(index + 1), 3000);
            }
        };
        root.addEventListener('mouseenter', () => { hovered = true; schedule(); });
        root.addEventListener('mouseleave', () => { hovered = false; schedule(); });
        root.addEventListener('focusin', schedule);
        root.addEventListener('focusout', () => setTimeout(schedule, 0));
        document.addEventListener('visibilitychange', schedule);
        reducedMotion.addEventListener('change', () => { paused = reducedMotion.matches; schedule(); });
        schedule();
    });
})();
