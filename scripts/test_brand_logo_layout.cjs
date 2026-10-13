// Isolated browser regression: real storefront styles, delayed logos, no database.
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const { chromium } = require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const root = path.resolve('EcommerceApp/static');
const logo = '<svg xmlns="http://www.w3.org/2000/svg" width="WIDTH" height="HEIGHT"><rect width="100%" height="100%" fill="#edf3fa"/><text x="5" y="18" font-size="16">BRAND</text></svg>';
const frames = [
 ['product', '<div class="product-card"><div class="product-brand-under-price">IMAGE</div></div>',110,30],
 ['detail', '<div class="product-detail-info"><div class="product-brand-under-price">IMAGE</div></div>',110,30],
 ['cart', '<div class="cart-item"><div class="product-brand-under-price">IMAGE</div></div>',110,30],
 ['legacy','<div class="product-brand">IMAGE</div>',200,48],
 ['carousel','<a class="showcase-brand-link">IMAGE</a>',120,30],
 ['showcase','<a class="home-brand-showcase-logo">IMAGE</a>',200,64],
 ['brands','<div class="brands-card__logo">IMAGE</div>',160,64],
 ['popular','<div class="bera-popular-brands__row"><a>IMAGE</a></div>',120,40],
];
function html(css, images) {
 return `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">${css.map(f=>`<link rel="stylesheet" href="/${f}">`).join('')}<style>body{padding:10px}section{margin:8px 0}.showcase-brand-link{display:flex;align-items:center;justify-content:center}</style></head><body class="bera-storefront-header bera-home bera-product-reference"><main>${frames.map(([name,wrapper,w,h])=>`<section data-case="${name}">${wrapper.replace('IMAGE',`<img class="carp-brand-logo ${name==='legacy'?'brand-logo':''}" src="/logo-${name}.svg" width="${w}" height="${h}" loading="lazy" alt="Brand">`)}</section>`).join('')}<img id="product-control" width="80" height="70" src="/control.svg"><img id="banner-control" width="200" height="50" src="/control.svg"><p id="sentinel">Content below brands</p></main><script>window.shifts=[];new PerformanceObserver(list=>{for(const e of list.getEntries())if(!e.hadRecentInput)shifts.push(e.value)}).observe({type:'layout-shift',buffered:true})</script></body></html>`;
}
const sheets=['bera_reference/css/style.min.css','bera_reference/css/bera-home.min.css','bera_reference/css/home-mobile-reference.css','bera_reference/css/bera-product.css'];
let release, allow = false;
const server=http.createServer((req,res)=>{
 if(req.url.startsWith('/logo-')){const respond=()=>{if(res.writableEnded)return;const i=frames.findIndex(f=>req.url===`/logo-${f[0]}.svg`);res.setHeader('Content-Type','image/svg+xml');res.end(logo.replace('WIDTH',i%2?'40':'400').replace('HEIGHT',i%2?'100':'40'));};if(allow)respond();else release.push(respond);return;}
 if(req.url==='/control.svg'){res.setHeader('Content-Type','image/svg+xml');res.end(logo.replace('WIDTH','100').replace('HEIGHT','100'));return;}
 if(req.url.startsWith('/fixture')){res.end(html(req.url.includes('baseline')?sheets:[...sheets,'css/brand-logo-layout.css']));return;}
 const file=path.join(root,req.url);if(!fs.existsSync(file)){res.writeHead(404);res.end();return;}res.setHeader('Content-Type','text/css');res.end(fs.readFileSync(file));
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const results=[];
 try{for(const width of [1440,390]){
 release=[];allow=false;
 const page=await browser.newPage({viewport:{width,height:1000}});
 const measure=()=>page.evaluate(()=>({logos:[...document.querySelectorAll('.carp-brand-logo')].map(el=>{const r=el.getBoundingClientRect(),s=getComputedStyle(el);return {case:el.closest('section').dataset.case,x:r.x,y:r.y,width:r.width,height:r.height,fit:s.objectFit,position:s.objectPosition};}),controls:['product-control','banner-control','sentinel'].map(id=>{const r=document.getElementById(id).getBoundingClientRect();return {id,x:r.x,y:r.y,width:r.width,height:r.height};})}));
 await page.goto(`http://127.0.0.1:${server.address().port}/fixture`,{waitUntil:'domcontentloaded'});
 await page.waitForTimeout(200);
 await page.evaluate(()=>{shifts=[]});const before=await measure();allow=true;release.forEach(r=>r());
 await page.waitForFunction(()=>[...document.images].every(i=>i.complete&&i.naturalWidth));
 await page.waitForTimeout(200);const after=await measure();const cls=await page.evaluate(()=>shifts.reduce((a,b)=>a+b,0));
 if(JSON.stringify(before)!==JSON.stringify(after)||cls!==0)throw Error(`Layout moved at ${width}: CLS ${cls} ${JSON.stringify({before,after})}`);
 if(after.logos.some(l=>l.fit!=='contain'||l.position!=='50% 50%'||l.height<=0||l.width<=0))throw Error('Invalid logo box '+JSON.stringify(after.logos));
 const controls=after.controls.slice(0,2).map(({id,width,height})=>({id,width,height}));
 release=[];allow=false;await page.goto(`http://127.0.0.1:${server.address().port}/fixture-baseline`,{waitUntil:'domcontentloaded'});await page.waitForTimeout(100);
 const baseline=(await measure()).controls.slice(0,2).map(({id,width,height})=>({id,width,height}));allow=true;release.forEach(r=>r());
 if(JSON.stringify(controls)!==JSON.stringify(baseline))throw Error('Non-brand image dimensions changed');
 await page.goto(`http://127.0.0.1:${server.address().port}/fixture`,{waitUntil:'domcontentloaded'});allow=true;release.forEach(r=>r());await page.waitForTimeout(200);
 await page.screenshot({path:`reports/brand-logo-layout/${width}.png`,fullPage:true});results.push({width,cls,stable:true,otherImagesUnchanged:true,logos:after.logos});await page.close();
 }}finally{await browser.close();server.close();}
 fs.writeFileSync('reports/brand-logo-layout/results.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results,null,2));
})().catch(e=>{console.error(e);server.close();process.exit(1)});
