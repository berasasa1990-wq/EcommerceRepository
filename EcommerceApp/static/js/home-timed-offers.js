(() => {
    const offers = [...document.querySelectorAll('[data-home-timed-offer]')].map(section => {
        const clock = section.querySelector('[data-home-offer-clock]');
        return { section, parts: [...clock.querySelectorAll('[data-clock-part]')], endsAt: Date.parse(clock.dateTime) };
    });
    const pad = number => String(number).padStart(2, '0');

    function updateClocks() {
        const now = Date.now();
        let running = false;
        for (const { section, parts, endsAt } of offers) {
            const seconds = Math.ceil((endsAt - now) / 1000);
            if (seconds <= 0) {
                section.hidden = true;
                continue;
            }
            running = true;
            const days = Math.floor(seconds / 86400);
            const hours = Math.floor(seconds / 3600) % 24;
            const duration = [days, hours, Math.floor(seconds / 60) % 60, seconds % 60];
            parts.forEach((part, index) => {
                const value = pad(duration[index]);
                if (part.textContent !== value) part.textContent = value;
            });
        }
        if (running) window.setTimeout(updateClocks, 1000);
    }

    updateClocks();
})();
