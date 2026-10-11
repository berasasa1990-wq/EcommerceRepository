const fs=require('node:fs');
const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const results=[];fs.mkdirSync('reports/bera-admin-visual',{recursive:true});
 try{for(const width of [1440,390]){
  const context=await browser.newContext({viewport:{width,height:900}});
  await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
  await context.addCookies(JSON.parse(fs.readFileSync('/private/tmp/bera-account-cookie.json','utf8')));
  const page=await context.newPage();let errors=[],failed=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('response',r=>{if(r.status()>=400)failed.push({path:new URL(r.url()).pathname,status:r.status()});});
  for(const [name,path] of [['index','/admin/'],['products','/admin/EcommerceApp/product/'],['product-add','/admin/EcommerceApp/product/add/'],['offers','/admin/EcommerceApp/akcija/'],['offer-add','/admin/EcommerceApp/akcija/add/'],['b2b','/admin/EcommerceApp/b2bsettings/']]){
   errors=[];failed=[];const r=await page.goto('http://127.0.0.1:8011'+path,{waitUntil:'networkidle'});await page.screenshot({path:`reports/bera-admin-visual/${name}-${width}.png`,fullPage:true});results.push({width,name,status:r.status(),errors:[...errors],failed:[...failed]});
  }await context.close();
 }}finally{await browser.close();}
 fs.writeFileSync('reports/bera-admin-visual/results.json',JSON.stringify(results,null,2));for(const r of results)console.log(JSON.stringify(r));
})().catch(e=>{console.error(e);process.exit(1);});
