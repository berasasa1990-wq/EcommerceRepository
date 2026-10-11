// Measure each unique logo once; retain the original if its CDN disallows canvas access.
(() => {
    const measurements = new Map();
    function measure(url) {
        if (measurements.has(url)) return measurements.get(url);
        const promise = new Promise(resolve => {
            const source = new Image();
            source.crossOrigin = 'anonymous';
            source.onload = () => {
                try {
                    const scale = Math.min(1, 400 / Math.max(source.naturalWidth, source.naturalHeight));
                    const canvas = document.createElement('canvas');
                    canvas.width = Math.max(1, Math.round(source.naturalWidth * scale));
                    canvas.height = Math.max(1, Math.round(source.naturalHeight * scale));
                    const ctx = canvas.getContext('2d', {willReadFrequently: true});
                    ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
                    const pixels = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
                    let left = canvas.width, right = -1, top = canvas.height, bottom = -1;
                    for (let y = 0; y < canvas.height; y++) {
                        for (let x = 0; x < canvas.width; x++) {
                            const i = (y * canvas.width + x) * 4;
                            if (pixels[i + 3] < 24) continue;
                            if (pixels[i] > 245 && pixels[i + 1] > 245 && pixels[i + 2] > 245) continue;
                            left = Math.min(left, x); right = Math.max(right, x);
                            top = Math.min(top, y); bottom = Math.max(bottom, y);
                        }
                    }
                    resolve(right >= left ? {source, left: left / scale, top: top / scale,
                        width: (right - left + 1) / scale, height: (bottom - top + 1) / scale} : null);
                } catch (_) { resolve(null); }
            };
            source.onerror = () => resolve(null);
            source.src = url;
        });
        measurements.set(url, promise);
        return promise;
    }
    async function normalize(img) {
        if (img.dataset.brandMeasured) return;
        img.dataset.brandMeasured = '1';
        const bounds = await measure(img.currentSrc || img.src);
        if (!bounds || !img.isConnected) return;
        const canvas = document.createElement('canvas');
        canvas.className = 'brand-logo-normalized';
        canvas.setAttribute('role', 'img');
        canvas.setAttribute('aria-label', img.alt);
        const outputScale = Math.min(1, 400 / Math.max(bounds.width, bounds.height));
        canvas.width = Math.max(1, Math.ceil(bounds.width * outputScale));
        canvas.height = Math.max(1, Math.ceil(bounds.height * outputScale));
        canvas.getContext('2d').drawImage(bounds.source, bounds.left, bounds.top,
            bounds.width, bounds.height, 0, 0, canvas.width, canvas.height);
        img.after(canvas);
        img.hidden = true;
        img.classList.add('brand-logo-original-hidden');
    }
    const observer = 'IntersectionObserver' in window ? new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (entry.isIntersecting) { observer.unobserve(entry.target); normalize(entry.target); }
        });
    }, {rootMargin: '100px'}) : null;
    document.querySelectorAll('.product-brand-under-price img').forEach(img => {
        if (observer) observer.observe(img); else normalize(img);
    });
})();
