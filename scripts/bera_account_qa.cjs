const fs=require('node:fs');
const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const results=[];fs.mkdirSync('reports/bera-account-visual',{recursive:true});
 try{
 for(const width of [1440,390]) for(const [label,port] of [['source',8012],['destination',8011]]){
  const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
  await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await context.addCookies(JSON.parse(fs.readFileSync(label==='source'?'/private/tmp/bera-account-source-cookie.json':'/private/tmp/bera-account-cookie.json','utf8')));
  const page=await context.newPage();const errors=[],failed=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)failed.push({url:new URL(r.url()).pathname,status:r.status()});});
  const base=`http://127.0.0.1:${port}`;
  for(const [name,path] of [['account','/nalog/']]){
   const response=await page.goto(base+path,{waitUntil:'networkidle'});
   if(response.status()!==200||new URL(page.url()).pathname!==path.split('?')[0])throw Error(name+' failed '+page.url());
   await page.evaluate(async()=>{for(let y=0;y<document.body.scrollHeight;y+=600){window.scrollTo(0,y);await new Promise(r=>setTimeout(r,40));}window.scrollTo(0,0);await document.fonts.ready;});
   await page.waitForTimeout(300);
   await page.screenshot({path:`reports/bera-account-visual/${label}-${name}-${width}.png`,fullPage:true,animations:'disabled'});
   const layout=await page.evaluate(()=>[...document.querySelectorAll('#header,.acc-dash,.acc-dash-hero,.acc-dash-nav,.acc-card,.bera-footer')].map(el=>{const b=el.getBoundingClientRect();return {class:el.id||el.className,x:b.x,y:b.y,width:b.width,height:b.height};}));
   for(const section of ['narudzbe','postavke','adrese','obavjestenja','kuponi','reklamacije']){await page.evaluate(id=>{location.hash=id;},section);await page.waitForTimeout(100);if(!await page.locator('[data-acc-panel="'+section+'"]').isVisible())throw Error('Section not visible '+section);}
   results.push({width,label,name,layout,errors:[...errors],failed:[...failed]});
  }
  await context.close();
 }
 }finally{await browser.close();}
 fs.writeFileSync('reports/bera-account-visual/results.json',JSON.stringify(results,null,2));
 for(const width of [1440,390])for(const name of ['account']){const a=results.find(x=>x.label==='source'&&x.width===width&&x.name===name),b=results.find(x=>x.label==='destination'&&x.width===width&&x.name===name);console.log(JSON.stringify({width,name,sameLayout:JSON.stringify(a.layout)===JSON.stringify(b.layout),errors:b.errors,failed:b.failed}));}
})().catch(e=>{console.error(e);process.exit(1);});
