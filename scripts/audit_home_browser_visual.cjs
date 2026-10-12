// GET-only frozen HTML/image comparison. Requires SNAPSHOT_HTML, BASELINE_LOGO_SCRIPT.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright'), fs=require('fs');
(async()=>{
const browser=await chromium.launch({executablePath:process.env.BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',headless:true});
const assets=new Map(),html=fs.readFileSync(process.env.SNAPSHOT_HTML,'utf8'),results=[];
for(const width of [1365,390])for(const mode of ['before','after']){
const context=await browser.newContext({viewport:{width,height:900},isMobile:width===390}); const page=await context.newPage();
await page.addInitScript(()=>{window.__alignReads=0;const original=CanvasRenderingContext2D.prototype.getImageData;CanvasRenderingContext2D.prototype.getImageData=function(...args){if(new Error().stack.includes('bera-brand-logo-align'))window.__alignReads++;return original.apply(this,args);};});
await page.route('**/*',async r=>{
const url=r.request().url();
if(r.request().isNavigationRequest())return r.fulfill({contentType:'text/html',body:html});
if(url.includes('/bera-brand-logo-align.'))return r.fulfill({contentType:'application/javascript',body:fs.readFileSync(mode==='before'?process.env.BASELINE_LOGO_SCRIPT:`${process.cwd()}/EcommerceApp/static/bera_reference/js/bera-brand-logo-align.js`)});
// Freeze images so repeated runs compare the exact same pixels.
if(r.request().resourceType()==='image'){
if(!assets.has(url)) assets.set(url,(async()=>{const res=await r.fetch();return {status:res.status(),headers:res.headers(),body:await res.body()};})());
return r.fulfill(await assets.get(url));
}
return r.continue();
});
await page.goto('https://carpologijabh.ba/',{waitUntil:'load',timeout:60000});await page.waitForTimeout(2000);
await page.addStyleTag({content: '* {animation:none!important;transition:none!important;}'});
for(const y of [0,1800]){
await page.evaluate(y=>window.scrollTo(0,y),y);await page.waitForTimeout(1000);
await page.screenshot({path:`${process.cwd()}/reports/performance/optimization-2/${mode}-${width}-${y}.png`});
}
results.push({width,mode,pixelReads:await page.evaluate(()=>window.__alignReads),hero:await page.locator('#heroCarousel img').first().evaluate(e=>({src:e.currentSrc,priority:e.fetchPriority,loading:e.loading})),logos:await page.locator('.product-brand-under-price img').evaluateAll(imgs=>imgs.map(e=>({src:e.currentSrc,style:e.getAttribute('style'),wrapper:e.parentElement.getAttribute('style')})))});
await context.close();
}
await browser.close();fs.writeFileSync(`${process.cwd()}/reports/performance/optimization-2/visual.json`,JSON.stringify(results,null,2));console.log('Completed frozen HTML/image desktop and mobile comparison');
})().catch(e=>{console.error(e);process.exit(1)});
