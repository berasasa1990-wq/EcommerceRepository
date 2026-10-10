// Align visible logo artwork, including logos saved on a padded canvas.
document.querySelectorAll('.product-brand-under-price img').forEach((img) => {
    const align = () => {
        try {
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
            if (right < left) return;
            const width = right - left + 1, height = bottom - top + 1;
            const scale = Math.min(72 / width, 20 / height);
            const wrapper = img.parentElement;
            wrapper.style.setProperty('width', `${width * scale}px`);
            wrapper.style.height = `${height * scale}px`;
            wrapper.style.overflow = 'hidden';
            img.style.setProperty('width', `${canvas.width * scale}px`, 'important');
            img.style.setProperty('max-width', 'none', 'important');
            img.style.setProperty('height', `${canvas.height * scale}px`, 'important');
            img.style.transform = `translate(${-left * scale}px, ${-top * scale}px)`;
        } catch (_) {
            // Cross-origin images retain the ordinary, left-aligned presentation.
        }
    };
    if (img.complete && img.naturalWidth) align();
    else img.addEventListener('load', align, { once: true });
});
