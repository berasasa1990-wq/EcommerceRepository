const fs = require('node:fs');
const {chromium} = require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async () => {
 const browser = await chromium.launch({headless:true, executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const session = JSON.parse(fs.readFileSync('/private/tmp/ecommerce-transfer-qa-session.json')).session;
 const results=[];
 try {
  for (const width of [1440,390]) {
   const context = await browser.newContext({viewport:{width,height:900}});
   await context.route('**/*', route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
   await context.addCookies([{name:'sessionid',value:session,domain:'127.0.0.1',path:'/'}]);
   const page=await context.newPage();
   await page.goto('http://127.0.0.1:8011/wms/lokacije/?q=QA-SCROLL',{waitUntil:'networkidle'});
   const metrics=await page.locator('.wms-locations-scroll').evaluate(el=>{
    const rows=[...el.querySelectorAll('tbody tr')];
    const box=el.getBoundingClientRect();
    const visible=rows.filter(row=>row.getBoundingClientRect().bottom<=box.bottom+1).length;
    return {rows:rows.length,visible,scrollable:el.scrollHeight>el.clientHeight};
   });
   if(metrics.rows!==16||metrics.visible!==10||!metrics.scrollable) throw Error(JSON.stringify({width,...metrics}));
   await page.locator('.wms-locations-scroll').evaluate(el=>el.scrollTop=el.scrollHeight);
   await page.locator('#wmsLocationQuery').fill('QA-SCROLL-12');
   await page.getByRole('button',{name:'Pretraži',exact:true}).click();
   await page.waitForLoadState('networkidle');
   if(await page.locator('.wms-locations-scroll tbody tr').count()!==1) throw Error('Search failed');
   results.push({width,...metrics,search:true});
   await context.close();
  }
  fs.writeFileSync('reports/wms_locations_browser_qa.json',JSON.stringify(results,null,2));
  console.log(JSON.stringify(results));
 } finally {await browser.close();}
})().catch(error=>{console.error(error);process.exit(1);});
