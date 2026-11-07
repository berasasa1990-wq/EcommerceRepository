(() => {
  document.querySelectorAll('[data-home-banner-slider]').forEach(root => {
    if (root.dataset.sliderReady) return;
    root.dataset.sliderReady = '1';
    const slides = [...root.querySelectorAll(':scope > .home-uploaded-banner')];
    const controls = root.querySelector('.home-banner-slider__controls');
    if (slides.length < 2) { if (controls) controls.hidden = true; return; }
    const interval = Number(root.dataset.slideInterval) || 6000;
    let index = 0, timer, paused = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const pause = root.querySelector('[data-slide-pause]');
    const schedule = () => {
      clearTimeout(timer);
      if (!paused && !document.hidden && (!controls || !root.matches(':hover')) && !root.contains(document.activeElement)) timer = setTimeout(() => show(index + 1), interval);
    };
    let generation = 0;
    async function show(next) {
      const attempt = ++generation;
      next = (next + slides.length) % slides.length;
      const target = slides[next];
      const image = target.querySelector('img[data-slide-src]');
      if (image) {
        image.src = image.dataset.slideSrc;
        try { await image.decode(); } catch (_) { schedule(); return; }
        delete image.dataset.slideSrc;
      }
      if (attempt !== generation) return;
      slides.forEach((slide, i) => {
        slide.hidden = i !== next;
        if (i !== next) slide.querySelectorAll('video').forEach(video => video.pause());
      });
      index = next;
      slides[index].querySelectorAll('video').forEach(video => {
        if (video.currentSrc && video.getBoundingClientRect().width) video.play().catch(() => {});
      });
      schedule();
    }
    root.querySelector('[data-slide-prev]')?.addEventListener('click', () => show(index - 1));
    root.querySelector('[data-slide-next]')?.addEventListener('click', () => show(index + 1));
    const label = () => { if (!pause) return; pause.textContent = paused ? 'Pokreni' : 'Pauza'; pause.setAttribute('aria-label', paused ? 'Pokreni smjenjivanje' : 'Pauziraj smjenjivanje'); };
    pause?.addEventListener('click', () => { paused = !paused; label(); schedule(); });
    if (controls) root.addEventListener('mouseenter', () => clearTimeout(timer));
    root.addEventListener('mouseleave', schedule);
    root.addEventListener('focusin', () => clearTimeout(timer));
    root.addEventListener('focusout', () => setTimeout(schedule, 0));
    document.addEventListener('visibilitychange', schedule);
    label(); schedule();
  });
})();
