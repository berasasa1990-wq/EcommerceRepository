const fs=require('node:fs');
const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const results=[];fs.mkdirSync('reports/bera-home-visual',{recursive:true});
 try{
 for(const width of [1440,390]) for(const [label,port] of [['source',8012],['destination',8011]]){
 const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
 await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
 const page=await context.newPage();const errors=[],failed=[];
 page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)failed.push({url:new URL(r.url()).pathname,status:r.status()});});
 await page.goto(`http://127.0.0.1:${port}/`,{waitUntil:'networkidle'});
 await page.evaluate(async()=>{for(let y=0;y<document.body.scrollHeight;y+=600){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,30));}window.scrollTo(0,0);await document.fonts.ready;});
 await page.waitForTimeout(300);
 const layout=await page.evaluate(()=>[...document.querySelectorAll('#header,.hero-carousel,.bera-hero,.home-products,.product-card,.bera-offer-banners,.bera-wide-promo,.bera-newsletter,.bera-footer')].map(el=>{const b=el.getBoundingClientRect();return {class:el.id||el.className,x:b.x,y:b.y,width:b.width,height:b.height};}));
 await page.screenshot({path:`reports/bera-home-visual/${label}-${width}.png`,fullPage:true,animations:'disabled'});
 results.push({width,label,layout,errors,failed});await context.close();
 }
 fs.writeFileSync('reports/bera-home-visual/results.json',JSON.stringify(results,null,2));
 for(const width of [1440,390]) {const a=results.find(x=>x.label==='source'&&x.width===width),b=results.find(x=>x.label==='destination'&&x.width===width);console.log(JSON.stringify({width,sameLayout:JSON.stringify(a.layout)===JSON.stringify(b.layout),sourceSections:a.layout.length,destinationSections:b.layout.length,errors:b.errors,failed:b.failed}));}
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
