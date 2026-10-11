const fs=require('node:fs');
const {chromium}=require('/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});const results=[];
 try{for(const width of [1440,390]){
 const context=await browser.newContext({viewport:{width,height:900}});
 await context.route('**/*',r=>new URL(r.request().url()).hostname==='127.0.0.1'?r.continue():r.abort());
 console.log('width',width); const page=await context.newPage(); page.setDefaultTimeout(15000); page.setDefaultNavigationTimeout(30000);const errors=[];page.on('pageerror',e=>errors.push(e.message));
 const base='http://127.0.0.1:8011';await page.goto(base+'/artikal/qa-artikal-1/',{waitUntil:'load'});
 console.log('zoom',width); await page.locator(width===390?'#mainProductImage':'#productZoomOpen').click();await page.locator('#productZoomDialog[open]').waitFor();await page.locator('.pd-zoom-close').click();
 await page.locator('#bera-tab-specs').click();await page.locator('#bera-panel-specs').waitFor({state:'visible'});
 const form=page.locator('form[action$="/dodaj/"]').filter({has:page.locator('.product-qty-selector')}).first();
 await form.locator('.product-qty-btn--plus').click();
 if(await form.locator('[name=quantity]').inputValue()!=='2')throw Error('Product qty failed');
 console.log('add',width); await Promise.all([page.waitForResponse(r=>r.url().includes('/dodaj/')&&r.request().method()==='POST'),form.locator('#mainAddToCartBtn').click()]);
 console.log('cart',width); await page.goto(base+'/korpa/',{waitUntil:'load'});
 const input=page.locator('.cart-item input[type=number]').first();
 const before=Number(await input.inputValue());
 await Promise.all([page.waitForResponse(r=>r.url().includes('/azuriraj/')&&r.request().method()==='POST'),page.locator('[data-qty-delta="1"]').first().click()]);
 await page.waitForLoadState('load');
 if(Number(await page.locator('.cart-item input[type=number]').first().inputValue())!==before+1)throw Error('Cart qty failed');
 await page.goto(base+'/narudzba/',{waitUntil:'load'});
 if(await page.locator('#checkout-form').count()!==1)throw Error('Checkout missing');
 await page.locator('#id_ime_prezime').fill('QA kupac');await page.locator('#id_telefon').fill('061234567');
 console.log('cart',width); await page.goto(base+'/korpa/',{waitUntil:'load'});
 await Promise.all([page.waitForResponse(r=>r.url().includes('/ukloni/')&&r.request().method()==='POST'),page.locator('.cart-item-remove').first().click()]);
 await page.waitForLoadState('load');if(await page.locator('.cart-item').count())throw Error('Remove failed');
 if(errors.length)throw Error(JSON.stringify(errors));
 results.push({width,zoom:true,tabs:true,productQuantity:true,addToCart:true,cartQuantity:true,checkoutForm:true,remove:true,errors});await context.close();
 }}finally{await browser.close();}
 fs.writeFileSync('reports/bera-storefront-visual/interactions.json',JSON.stringify(results,null,2));console.log(JSON.stringify(results));
})().catch(e=>{console.error(e);process.exit(1);});
