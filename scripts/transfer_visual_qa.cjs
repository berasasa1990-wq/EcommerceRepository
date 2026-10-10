/* Local-only visual parity check against the read-only reference application. */
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.TRANSFER_PLAYWRIGHT || '/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const out = path.resolve(process.env.TRANSFER_VISUAL_OUTPUT || 'reports/transfer-visual');
fs.mkdirSync(out, {recursive: true});
const routes = ['/', '/artikal/qa-artikal-1/', '/artikal/qa-artikal-2/', '/korpa/', '/narudzba/', '/panel', '/panel/podesavanja', '/panel/podesavanja/sajt/', '/panel/sekcija/product/', '/panel/b2b', '/panel/loyalty', '/panel/greb-greb', '/nalog/online-narudzbe/', '/wms/', '/wms/lokacije/', '/wms/narudzbe/', '/wms/podesavanje/', '/panel/podesavanja/sekcije/EcommerceApp/product/1/change/', '/panel/podesavanja/sekcije/EcommerceApp/product/add/'];
if (process.env.TRANSFER_QA_PANEL_LINKS === '1') routes.splice(0, routes.length, ...JSON.parse(fs.readFileSync('reports/transfer-visual/discovered-panel-links.json')));
(async () => {
 const browser = await chromium.launch({headless: true, executablePath: process.env.TRANSFER_BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const session = JSON.parse(fs.readFileSync('/private/tmp/ecommerce-transfer-qa-session.json')).session;
 const results = [];
 try {
 for (const width of [1440, 390]) {
  for (const [label, port] of [['reference', 8012], ['destination', 8011]]) {
   const context = await browser.newContext({viewport: {width, height: 900}, deviceScaleFactor: 1, reducedMotion: 'reduce'});
   await context.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1' ? route.continue() : route.abort());
   await context.addCookies([{name: 'sessionid', value: session, domain: '127.0.0.1', path: '/', httpOnly: true, sameSite: 'Lax'}]);
   const page = await context.newPage();
   for (let index = 0; index < routes.length; index++) {
    const errors = [], failed = [];
    const listener = error => errors.push(error.stack || error.message);
    const responseListener = response => {if (response.status() >= 400) failed.push({url: response.url().replace(/:801[12]/, ':PORT'), status: response.status()});};
    page.on('pageerror', listener); page.on('response', responseListener);
    const response = await page.goto(`http://127.0.0.1:${port}${routes[index]}`, {waitUntil: 'networkidle'});
    await page.evaluate(() => document.fonts.ready);
    await page.addStyleTag({content: '* { animation: none !important; transition: none !important; caret-color: transparent !important; }'});
    const file = `${width}-${String(index).padStart(2, '0')}-${label}.png`;
    await page.screenshot({path: path.join(out, file), fullPage: true, animations: 'disabled'});
    const layout = await page.evaluate(() => ({title: document.title, width: document.documentElement.clientWidth, scrollWidth: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight, boxes: ['header', 'main', 'footer', '.product-gallery', '.cart-page', '.checkout-page', '.staff-admin-page'].map(selector => {const element=document.querySelector(selector);if (!element) return null;const r=element.getBoundingClientRect(),s=getComputedStyle(element);return {selector,x:r.x,y:r.y,width:r.width,height:r.height,font:s.fontFamily,color:s.color,background:s.backgroundColor};}).filter(Boolean)}));
    results.push({width, label, route: routes[index], status: response.status(), finalPath: new URL(page.url()).pathname, file, layout, errors, failed});
    page.off('pageerror',listener);page.off('response',responseListener);
    console.log(`${width} ${label} ${routes[index]} ${response.status()}`);
   }
   await context.close();
  }
 }
 } finally {await browser.close(); fs.writeFileSync(path.join(out,'browser-results.json'),JSON.stringify(results,null,2)+'\n');}
})().catch(error => {console.error(error);process.exit(1);});
