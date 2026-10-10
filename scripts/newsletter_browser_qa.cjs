const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 try {
  for(const width of [1440,390]) {
   const context=await browser.newContext({viewport:{width,height:900}});
   await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
   const page=await context.newPage();
   await page.goto('http://127.0.0.1:8011/',{waitUntil:'networkidle'});
   const email=`newsletter-browser-${width}-${Date.now()}@example.com`;
   for(let attempt=0;attempt<2;attempt++) {
    await page.locator('#footerNewsletterEmail').fill(email);
    await Promise.all([page.waitForResponse(r=>r.url().includes('/api/newsletter/')&&r.request().method()==='POST'),page.locator('#footerNewsletterForm button').click()]);
    await page.locator('#footerNewsletterMsg').waitFor({state:'visible'});
    const message=await page.locator('#footerNewsletterMsg').textContent();
    if(!message.includes(attempt?'Već ste prijavljeni':'Uspješno ste se prijavili')) throw Error(message);
    if(new URL(page.url()).pathname!=='/') throw Error('Navigated away');
   }
   console.log(`${width}: subscription and repeated subscription work without navigation`);
   await context.close();
  }
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
