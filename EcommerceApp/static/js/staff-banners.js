(() => {
    document.querySelectorAll('[data-banner-upload]').forEach(card => {
        const file = card.querySelector('[data-banner-file]');
        const preview = card.querySelector('[data-banner-preview]');
        const empty = card.querySelector('[data-banner-empty]');
        const filename = card.querySelector('[data-banner-filename]');
        const clear = card.querySelector('[data-banner-clear]');
        const initialSrc = preview.getAttribute('src') || '';
        const initialEmpty = empty.textContent;
        let objectUrl = null;
        const release = () => { if (objectUrl) URL.revokeObjectURL(objectUrl); objectUrl = null; };
        file.addEventListener('change', () => {
            release();
            const selected = file.files[0];
            if (clear) clear.checked = false;
            if (selected) {
                objectUrl = URL.createObjectURL(selected);
                preview.src = objectUrl;
                preview.hidden = false;
                empty.hidden = true;
                filename.textContent = selected.name;
            } else {
                if (initialSrc) preview.src = initialSrc;
                preview.hidden = !initialSrc;
                empty.hidden = Boolean(initialSrc);
                empty.textContent = initialEmpty;
                filename.textContent = initialSrc ? 'Slika je postavljena.' : 'Slika još nije postavljena.';
            }
        });
        clear?.addEventListener('change', () => {
            if (clear.checked) {
                file.value = '';
                release();
                preview.hidden = true;
                empty.hidden = false;
                empty.textContent = 'Slika će biti uklonjena nakon čuvanja.';
            } else {
                if (initialSrc) preview.src = initialSrc;
                preview.hidden = !initialSrc;
                empty.hidden = Boolean(initialSrc);
                empty.textContent = initialEmpty;
            }
        });
        window.addEventListener('pagehide', release, {once: true});
    });
})();
