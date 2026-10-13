// Read-only frozen HTML/static servers only; no Django/database initialization.
// Hold the old critical CSS response to verify that a downloaded/preloaded
// product image cannot be painted while a following inline script stalls parsing.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE ||
    '/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const urls = {before: process.env.CARP_CARD_BEFORE_URL, after: process.env.CARP_CARD_AFTER_URL};
for (const url of Object.values(urls)) {
    if (!url || !['127.0.0.1', 'localhost'].includes(new URL(url).hostname)) {
        throw Error('Provide localhost frozen before/after snapshot URLs');
    }
}

(async () => {
    const browser = await chromium.launch({headless: true, executablePath:
        process.env.BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
    const results = [];
    try {
        for (const [mode, base] of Object.entries(urls)) {
            const context = await browser.newContext({viewport: {width: 390, height: 844}});
            const page = await context.newPage();
            let releaseCSS, cssHeld = false;
            const cssGate = new Promise(resolve => { releaseCSS = resolve; });
            let imageDownloaded, preloadReceived = false;
            const imageFinished = new Promise(resolve => { imageDownloaded = resolve; });
            page.on('response', response => {
                if (!preloadReceived && response.ok() && new URL(response.url()).pathname.startsWith('/media/products/')) {
                    preloadReceived = true;
                    response.finished().then(() => imageDownloaded(response.url()), () => {});
                }
            });
            await context.route('**/*', async route => {
                const request = route.request(), url = new URL(request.url());
                if (url.origin !== new URL(base).origin || request.method() !== 'GET') {
                    return route.abort();
                }
                if (/home-first-screen\.bundle(?:\.[a-f0-9]{12})?\.css$/.test(url.pathname)) {
                    cssHeld = true;
                    await cssGate;
                }
                return route.continue();
            });
            await page.addInitScript(() => {
                window.cardPaints = [];
                new PerformanceObserver(list => {
                    for (const entry of list.getEntries()) {
                        const card = entry.element?.closest('[data-product-card]');
                        if (card && entry.element.tagName === 'IMG' && entry.url.includes('/media/products/')) {
                            window.cardPaints.push({time: entry.startTime, cardId: card.dataset.productId, image: entry.url});
                        }
                    }
                }).observe({type: 'largest-contentful-paint', buffered: true});
            });
            try {
                await page.goto(base, {waitUntil: 'commit'});
                let timeout;
                let downloadedImage;
                try {
                    downloadedImage = await Promise.race([
                        imageFinished,
                        new Promise((_, reject) => {
                            timeout = setTimeout(() => reject(Error('Product preload did not finish')), 10000);
                        }),
                    ]);
                } finally { clearTimeout(timeout); }
                await page.waitForTimeout(1000);
                const state = await page.evaluate(() => ({
                    cardsInDOM: document.querySelectorAll('[data-product-card]').length,
                    cardPaints: window.cardPaints,
                }));
                if (mode === 'before') {
                    assert.equal(cssHeld, true);
                    assert.equal(state.cardsInDOM, 0, 'Expected parser stalled behind critical CSS and inline script');
                    assert.equal(state.cardPaints.length, 0);
                } else {
                    assert.equal(cssHeld, false, 'Optimized page should not need the external critical CSS');
                    assert.ok(state.cardsInDOM > 0);
                    assert.ok(state.cardPaints.length > 0, 'Downloaded image must paint without the held CSS');
                }
                results.push({mode, cssHeld, downloadedImage, ...state});
            } finally {
                releaseCSS();
                await page.waitForLoadState('load');
                await context.close();
            }
        }
    } finally { await browser.close(); }
    if (process.env.CARP_CARD_PAINT_OUT) {
        fs.writeFileSync(process.env.CARP_CARD_PAINT_OUT, JSON.stringify(results, null, 2));
    }
    console.log(JSON.stringify(results));
})().catch(error => { console.error(error); process.exit(1); });
