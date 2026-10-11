const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
const fs=require('node:fs');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});const results=[];
 try{for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900}});
 await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:8011';await page.goto(base+'/',{waitUntil:'networkidle'});
 const heart=page.locator('[data-favorite-id]').first();
 await heart.click();await page.waitForFunction(()=>document.querySelector('[data-favorite-id]').getAttribute('aria-pressed')==='true');
 const add=page.locator('[data-catalog-add]').first();
 await Promise.all([page.waitForResponse(r=>r.url().includes('/dodaj/')&&r.request().method()==='POST'),add.click()]);
 await page.goto(base+'/korpa/',{waitUntil:'networkidle'});
 if(await page.locator('.cart-item').count()<1)throw Error('Cart item missing');
 await page.goto(base+'/',{waitUntil:'networkidle'});
 await page.locator('#footerNewsletterEmail').fill(`bera-home-${width}-${Date.now()}@example.com`);
 await Promise.all([page.waitForResponse(r=>r.url().includes('/api/newsletter/')&&r.request().method()==='POST'),page.locator('#footerNewsletterForm button').click()]);
 await page.locator('#footerNewsletterMsg').waitFor({state:'visible'});
 if(!(await page.locator('#footerNewsletterMsg').textContent()).includes('Uspješno'))throw Error('Newsletter failed');
 await page.goto(base+'/?q=QA',{waitUntil:'networkidle'});
 if(await page.locator('.product-card').count()<1)throw Error('Search empty');
 if(errors.length)throw Error(JSON.stringify(errors));
 results.push({width,addToCart:true,cart:true,wishlist:true,newsletter:true,search:true,errors});await context.close();
 }}finally{await browser.close();}
 fs.writeFileSync('reports/bera-home-visual/interactions.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1);});
