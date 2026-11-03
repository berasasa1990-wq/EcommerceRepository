// Scanning only opens the next picking page. Validation always needs confirmation.
(() => {
    'use strict';
    const trigger = document.querySelector('[data-scan-new]');
    const panel = document.getElementById('pickingScanner');
    if (!trigger || !panel) return;
    const close = panel.querySelector('[data-scan-close]');
    const retry = panel.querySelector('[data-scan-retry]');
    const status = panel.querySelector('[data-scan-status]');
    const [prefix, suffix] = panel.dataset.validationTemplate.split('PICKINGTOKEN');
    let library;
    let scanner;
    let starting;
    let session = 0;
    let handled = false;

    function loadLibrary() {
        if (window.Html5Qrcode) return Promise.resolve(window.Html5Qrcode);
        if (!library) {
            library = new Promise((resolve, reject) => {
                const script = document.createElement('script');
                script.src = 'https://unpkg.com/html5-qrcode@2.3.8/html5-qrcode.min.js';
                script.onload = () => window.Html5Qrcode ? resolve(window.Html5Qrcode) : reject(new Error('library'));
                script.onerror = () => { script.remove(); reject(new Error('library')); };
                document.head.appendChild(script);
            }).catch(error => { library = null; throw error; });
        }
        return library;
    }

    async function stop() {
        const current = scanner;
        if (!current) return;
        scanner = null;
        try {
            await starting;
            await current.stop();
        } catch (_) { /* Already stopped or camera permission was denied. */ }
        try { current.clear(); } catch (_) { /* No active preview. */ }
    }

    function pickingUrl(text) {
        try {
            const url = new URL(text, window.location.origin);
            if (url.origin !== window.location.origin || url.search || url.hash
                || !url.pathname.startsWith(prefix) || !url.pathname.endsWith(suffix)) return null;
            const token = decodeURIComponent(url.pathname.slice(prefix.length, suffix ? -suffix.length : undefined));
            return /^[A-Za-z0-9_.:-]+$/.test(token) ? url.href : null;
        } catch (_) { return null; }
    }

    async function start() {
        const currentSession = ++session;
        trigger.disabled = true;
        trigger.setAttribute('aria-expanded', 'true');
        panel.hidden = false;
        retry.hidden = true;
        status.textContent = 'Pokrećem kameru…';
        panel.scrollIntoView({behavior: 'smooth', block: 'nearest'});
        await stop();
        if (currentSession !== session) return;
        handled = false;
        try {
            const QrScanner = await loadLibrary();
            if (currentSession !== session) return;
            scanner = new QrScanner('pickingQrReader');
            starting = scanner.start({facingMode: 'environment'}, {
                fps: 10,
                qrbox: (width, height) => {
                    const side = Math.floor(Math.min(width, height) * 0.75);
                    return {width: side, height: side};
                }
            }, async text => {
                if (handled || currentSession !== session) return;
                const next = pickingUrl(text);
                if (!next) {
                    status.textContent = 'Ovo nije picking QR kod ovog sajta. Skeniraj kod s picking liste.';
                    return;
                }
                handled = true;
                status.textContent = 'Kod je očitan. Otvaram sljedeću narudžbu…';
                await stop();
                if (currentSession === session) window.location.assign(next);
            }, () => { /* Frames without a QR code are normal. */ });
            await starting;
            if (currentSession !== session) return;
            status.textContent = 'Kamera je spremna. Usmjeri je na picking QR kod.';
        } catch (error) {
            if (currentSession !== session) return;
            await stop();
            status.textContent = error?.message === 'library'
                ? 'Skener nije učitan. Provjeri internet i pokušaj ponovo.'
                : 'Kamera nije dostupna. Dozvoli pristup kameri u pregledniku i pokušaj ponovo.';
            retry.hidden = false;
            trigger.disabled = false;
        }
    }

    function cancel() {
        session++;
        stop();
        panel.hidden = true;
        trigger.disabled = false;
        trigger.setAttribute('aria-expanded', 'false');
    }
    trigger.addEventListener('click', start);
    retry.addEventListener('click', start);
    close.addEventListener('click', () => { cancel(); trigger.focus(); });
    document.addEventListener('keydown', event => { if (event.key === 'Escape' && !panel.hidden) cancel(); });
    window.addEventListener('pagehide', cancel);
})();
