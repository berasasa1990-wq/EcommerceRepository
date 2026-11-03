(() => {
    const videos = [...document.querySelectorAll('[data-home-autoplay]')];
    if (!videos.length) return;
    const mobile = window.matchMedia('(max-width: 767px)');
    const desktop = window.matchMedia('(min-width: 1025px)');
    const pending = new WeakSet();
    const timers = new WeakMap();

    function visible(video) {
        const rect = video.getBoundingClientRect();
        const deviceMatches = video.dataset.homeAutoplay === 'desktop' ? desktop.matches : mobile.matches;
        return deviceMatches && !document.hidden && rect.width > 0 && rect.height > 0
            && rect.bottom > 0 && rect.top < window.innerHeight;
    }

    function play(video) {
        if (!visible(video) || pending.has(video)) return;
        // Set the properties as well as the attributes for mobile Safari.
        video.muted = true;
        video.defaultMuted = true;
        video.playsInline = true;
        video.controls = false;
        const source = video.querySelector('source[data-src]');
        if (source && !source.hasAttribute('src')) {
            source.src = source.dataset.src;
            video.load();
        }
        pending.add(video);
        try {
            Promise.resolve(video.play()).catch(() => {
                // A later ready event or user gesture retries a blocked play request.
            }).finally(() => pending.delete(video));
        } catch (_) {
            pending.delete(video);
        }
    }

    function refresh() {
        videos.forEach(video => {
            if (visible(video)) play(video);
            else video.pause();
        });
    }

    videos.forEach(video => {
        ['loadeddata', 'canplay'].forEach(event => video.addEventListener(event, () => play(video)));
        ['stalled', 'waiting', 'suspend'].forEach(event => video.addEventListener(event, () => {
            clearTimeout(timers.get(video));
            timers.set(video, setTimeout(() => play(video), 1000));
        }));
    });
    if ('IntersectionObserver' in window) {
        const observer = new IntersectionObserver(refresh, { threshold: [0, 0.05] });
        videos.forEach(video => observer.observe(video));
    } else {
        window.addEventListener('scroll', refresh, { passive: true });
    }
    // Restore playback after returning to the page or rotating the phone.
    window.addEventListener('pageshow', refresh);
    window.addEventListener('resize', refresh);
    document.addEventListener('visibilitychange', refresh);
    // Retry within a user gesture when the browser initially blocks autoplay.
    document.addEventListener('touchend', refresh, { passive: true });
    document.addEventListener('pointerup', refresh, { passive: true });
    refresh();
    [300, 1000, 2500].forEach(delay => setTimeout(refresh, delay));
})();
