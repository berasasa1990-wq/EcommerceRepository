/* Shared dependency-free, progressively loaded 360 degree viewer. */
(() => {
  window.BeraProduct360 = (root, urls) => {
    root.innerHTML = '<img class="spin-image" alt="360° prikaz artikla" draggable="false"><div class="spin-status" role="status" aria-live="polite"></div><div class="spin-controls"><span>360° — Povuci za rotaciju</span><button type="button">Automatska rotacija</button></div>';
    root.tabIndex = 0;
    root.setAttribute('aria-label', '360° prikaz. Koristite strelice ili povucite sliku.');
    const picture = root.querySelector('img'), status = root.querySelector('.spin-status'), button = root.querySelector('button');
    const cache = new Map(); let index = 0, running = false, timer, disposed = false, pointer = null, startX = 0, base = 0;
    const show = value => {
      const next = ((value % urls.length) + urls.length) % urls.length;
      if (!cache.has(next)) return;
      index = next; picture.src = cache.get(next).src;
    };
    const load = async n => {
      const frame = new Image(); frame.src = urls[n];
      try { await frame.decode(); if (!disposed) cache.set(n, frame); } catch (_) { /* Keep available frames usable. */ }
    };
    const stop = () => { running = false; clearInterval(timer); button.textContent = 'Automatska rotacija'; button.setAttribute('aria-pressed', 'false'); };
    button.setAttribute('aria-pressed', 'false');
    button.onclick = () => {
      if (running) return stop();
      running = true; button.textContent = 'Pauziraj'; button.setAttribute('aria-pressed', 'true');
      timer = setInterval(() => show(index + 1), 110);
    };
    root.addEventListener('pointerdown', event => {
      if (event.target.closest('button') || (event.pointerType === 'mouse' && event.button !== 0)) return;
      event.stopPropagation(); stop(); pointer = event.pointerId; startX = event.clientX; base = index; root.setPointerCapture(pointer);
    });
    root.addEventListener('pointermove', event => {
      if (pointer !== event.pointerId) return;
      show(base + Math.trunc((startX - event.clientX) / 9));
    });
    const release = event => { event.stopPropagation(); pointer = null; };
    root.addEventListener('lostpointercapture', () => { pointer = null; });
    root.addEventListener('pointerup', release); root.addEventListener('pointercancel', release);
    root.addEventListener('keydown', event => {
      if (event.target.closest('button')) return;
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); stop(); show(index + (event.key === 'ArrowRight' ? 1 : -1)); }
    });
    status.textContent = 'Učitavanje 360° prikaza…';
    (async () => {
      await load(0); if (disposed) return; show(0);
      let cursor = 1;
      await Promise.all(Array.from({length: 3}, async () => {
        while (!disposed && cursor < urls.length) { await load(cursor++); status.textContent = `Učitano ${cache.size} / ${urls.length}`; }
      }));
      if (!disposed) status.textContent = cache.size === urls.length ? '' : 'Neki kadrovi nisu dostupni. Pokušajte ponovo.';
    })();
    return {stop, destroy: () => { disposed = true; stop(); root.replaceChildren(); }};
  };
})();
