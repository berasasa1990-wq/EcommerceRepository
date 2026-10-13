// Frozen synthetic product snapshots must contain 400/800/1200 width candidates.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const output = process.env.CARP_FRONTEND_OUT || '/private/tmp/carp-frontend-audit';
fs.mkdirSync(output, {recursive: true});
(async () => {
    const browser = await chromium.launch({headless: true,
        executablePath: process.env.BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
    const results = [];
    try {
        for (const width of [390, 1440]) {
            const modes = {};
            for (const mode of ['before', 'after']) {
                const base = process.env[mode === 'before' ? 'CARP_FRONTEND_BEFORE_URL' : 'CARP_FRONTEND_AFTER_URL']
                    || 'http://127.0.0.1:' + (mode === 'before' ? 8153 : 8154);
                if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Local synthetic QA only');
                const context = await browser.newContext({viewport: {width, height: 844}, deviceScaleFactor: 1});
                await context.route('**/*', route => ['127.0.0.1', 'localhost'].includes(new URL(route.request().url()).hostname)
                    && route.request().method() === 'GET' ? route.continue() : route.abort());
                const page = await context.newPage();
                const requests = [];
                page.on('request', request => {
                    if (request.url().includes('/frontend-qa/product-')) requests.push(new URL(request.url()).pathname);
                });
                await page.goto(base + '/product', {waitUntil: 'networkidle'});
                modes[mode] = await page.evaluate(() => {
                    const image = document.getElementById('mainProductImage');
                    const rect = image.getBoundingClientRect();
                    return {currentSrc: new URL(image.currentSrc).pathname,
                        geometry: {x: rect.x, y: rect.y, width: rect.width, height: rect.height}};
                });
                modes[mode].requests = requests;
                await context.close();
            }
            console.log(JSON.stringify({width, ...modes}));
            assert.deepEqual(modes.after.geometry, modes.before.geometry);
            assert.equal(modes.after.currentSrc, '/frontend-qa/product-' + (width === 390 ? 400 : 800) + '.jpg');
            assert.equal(modes.before.requests.length, 2, 'Baseline must reproduce duplicate original preload');
            assert.deepEqual(modes.after.requests, [modes.after.currentSrc], 'Preload and main image should share one request');
            results.push({width, ...modes});
        }
    } finally { await browser.close(); }
    fs.writeFileSync(output + '/product-preload-requests.json', JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results));
})().catch(error => {console.error(error); process.exit(1);});
