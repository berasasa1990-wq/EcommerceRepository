// Align visible logo artwork, including logos saved on a padded canvas.
(() => {
    const measurements = new Map();
    document.querySelectorAll('.product-brand-under-price img').forEach((img) => {
        const align = () => {
            const key = `${img.currentSrc || img.src}|${img.naturalWidth}x${img.naturalHeight}|${img.crossOrigin || ''}`;
            try {
                let bounds = measurements.get(key);
                if (!measurements.has(key)) {
                    const canvas = document.createElement('canvas');
                    canvas.width = img.naturalWidth;
                    canvas.height = img.naturalHeight;
                    const context = canvas.getContext('2d', { willReadFrequently: true });
                    context.drawImage(img, 0, 0);
                    const pixels = context.getImageData(0, 0, canvas.width, canvas.height).data;
                    let left = canvas.width, top = canvas.height, right = -1, bottom = -1;
                    for (let y = 0; y < canvas.height; y++) {
                        for (let x = 0; x < canvas.width; x++) {
                            const i = (y * canvas.width + x) * 4;
                            if (pixels[i + 3] > 30 && Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) < 240) {
                                left = Math.min(left, x); right = Math.max(right, x);
                                top = Math.min(top, y); bottom = Math.max(bottom, y);
                            }
                        }
                    }
                    bounds = right < left ? null : {left, top, width: right - left + 1, height: bottom - top + 1};
                    measurements.set(key, bounds);
                }
                if (!bounds) return;
                const {left, top, width, height} = bounds;
                const scale = Math.min(72 / width, 20 / height);
                const wrapper = img.parentElement;
                wrapper.style.setProperty('width', `${width * scale}px`);
                wrapper.style.height = `${height * scale}px`;
                wrapper.style.overflow = 'hidden';
                img.style.setProperty('width', `${img.naturalWidth * scale}px`, 'important');
                img.style.setProperty('max-width', 'none', 'important');
                img.style.setProperty('height', `${img.naturalHeight * scale}px`, 'important');
                img.style.transform = `translate(${-left * scale}px, ${-top * scale}px)`;
            } catch (error) {
                // The same source and CORS mode cannot become readable on another card.
                if (error.name === 'SecurityError') measurements.set(key, null);
                // Cross-origin images retain the ordinary, left-aligned presentation.
            }
        };
        if (img.complete && img.naturalWidth) align();
        else img.addEventListener('load', align, { once: true });
    });

})();
