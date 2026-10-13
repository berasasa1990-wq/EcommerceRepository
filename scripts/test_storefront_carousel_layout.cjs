// Compare local frozen HTML while serving each version's actual JavaScript.
// The snapshot servers must contain synthetic fixtures only.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const {chromium} = require(process.env.PLAYWRIGHT_MODULE || '/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const before = process.env.CARP_FRONTEND_BEFORE_URL || 'http://127.0.0.1:8153';
const after = process.env.CARP_FRONTEND_AFTER_URL || 'http://127.0.0.1:8154';
for (const base of [before, after]) {
    if (!['127.0.0.1', 'localhost'].includes(new URL(base).hostname)) throw Error('Local synthetic QA only');
}
const output = process.env.CARP_FRONTEND_OUT || '/private/tmp/carp-frontend-audit';
fs.mkdirSync(output, {recursive: true});

async function inspect(browser, base, width) {
    const context = await browser.newContext({viewport: {width, height: 844}, reducedMotion: 'reduce'});
    await context.route('**/*', route => {
        const request = route.request();
        return ['127.0.0.1', 'localhost'].includes(new URL(request.url()).hostname) && request.method() === 'GET'
            ? route.continue() : route.abort();
    });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    await page.addInitScript(() => {
        const original = window.getComputedStyle;
        window.carouselStyleReads = 0;
        window.getComputedStyle = function (element, pseudo) {
            if (element.classList?.contains('home-product-carousel')) window.carouselStyleReads++;
            return original.call(this, element, pseudo);
        };
    });
    await page.goto(base + '/', {waitUntil: 'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    const read = () => page.evaluate(() => [...document.querySelectorAll('.home-product-carousel')].map(root => {
        const track = root.querySelector('.home-product-carousel__track');
        const viewport = root.querySelector('.home-product-carousel__viewport');
        const slide = root.querySelector('.home-product-carousel__slide');
        return {
            width: root.style.getPropertyValue('--carousel-slide-width'),
            transform: track.style.transform,
            viewportWidth: viewport.clientWidth,
            slideWidth: slide.getBoundingClientRect().width,
            previousDisabled: root.querySelector('.home-product-carousel__btn--prev')?.disabled,
            nextDisabled: root.querySelector('.home-product-carousel__btn--next')?.disabled,
        };
    }));
    const states = {initial: await read()};
    for (const direction of ['next', 'prev', 'prev', 'next']) {
        await page.evaluate(direction => document.querySelectorAll('.home-product-carousel__btn--' + direction)
            .forEach(button => { if (!button.disabled) button.click(); }), direction);
        await page.waitForTimeout(450);
        states[direction + Object.keys(states).length] = await read();
    }
    await page.setViewportSize({width: width === 390 ? 1440 : 390, height: 844});
    await page.waitForTimeout(450);
    states.resized = await read();
    const styleReads = await page.evaluate(() => window.carouselStyleReads);
    await context.close();
    return {width, states, styleReads, errors};
}

(async () => {
    const browser = await chromium.launch({headless: true,
        executablePath: process.env.BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
    const results = [];
    try {
        for (const width of [390, 1440]) {
            const baseline = await inspect(browser, before, width);
            const optimized = await inspect(browser, after, width);
            assert.deepEqual(optimized.states, baseline.states, 'Carousel geometry changed');
            assert.deepEqual(baseline.errors, []);
            assert.deepEqual(optimized.errors, []);
            assert.ok(optimized.styleReads < baseline.styleReads, 'Expected fewer computed-style reads');
            results.push({width, beforeStyleReads: baseline.styleReads, afterStyleReads: optimized.styleReads,
                unchangedGeometry: true, states: optimized.states});
        }
    } finally { await browser.close(); }
    fs.writeFileSync(output + '/carousel-layout.json', JSON.stringify(results, null, 2));
    console.log(JSON.stringify(results.map(({states, ...result}) => result)));
})().catch(error => {console.error(error); process.exit(1);});
