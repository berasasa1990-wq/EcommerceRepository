/* Browser actions use only the isolated QA servers and synthetic customer data. */
const fs=require('node:fs');
const {chromium}=require(process.env.TRANSFER_PLAYWRIGHT || '/Users/bera1990/Library/Caches/ms-playwright-go/1.57.0/package');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.TRANSFER_BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser'});
 const results=[];
 try {
  for(const width of [1440,390]) for(const [label,port] of [['reference',8012],['destination',8011]]) {
   const context=await browser.newContext({viewport:{width,height:900},reducedMotion:'reduce'});
   await context.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
   const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
   const base=`http://127.0.0.1:${port}`; const record={width,label,checks:[],errors};
   const check=(name,ok)=>{if(!ok) throw new Error(`${label} ${width}: ${name}`);record.checks.push(name);};
   await page.goto(base+'/panel',{waitUntil:'networkidle'});check('anonymous panel denied',!new URL(page.url()).pathname.startsWith('/panel'));
   await page.goto(base+'/artikal/qa-artikal-1/',{waitUntil:'networkidle'});
   if (width < 1025) { await page.locator('#productZoomOpen').focus(); await page.keyboard.press('Enter'); } else { await page.locator('#productZoomOpen').click(); } check('gallery zoom opens',await page.locator('#productZoomDialog').evaluate(e=>e.open));
   await page.keyboard.press('Escape');check('gallery zoom closes',!(await page.locator('#productZoomDialog').evaluate(e=>e.open)));
   await page.locator('#mainAddToCartForm .product-qty-btn--plus').click();check('product quantity increases',await page.locator('#mainAddToCartForm input[name="quantity"]').inputValue()==='2');
   await Promise.all([page.waitForResponse(r=>r.url().includes('/artikal/qa-artikal-1/dodaj/')&&r.request().method()==='POST'),page.locator('#mainAddToCartBtn').click()]);
   await page.goto(base+'/korpa/',{waitUntil:'networkidle'});check('added item quantity is 2',await page.locator('.cart-item-qty input').first().inputValue()==='2');
   await Promise.all([page.waitForResponse(r=>r.url().includes('/korpa/azuriraj/')&&r.request().method()==='POST'),page.locator('.cart-qty-btn[data-qty-delta="1"]').first().click()]);
   await page.waitForLoadState('networkidle');check('cart quantity persists as 3',await page.locator('.cart-item-qty input').first().inputValue()==='3');
   await page.reload({waitUntil:'networkidle'});check('cart quantity survives reload',await page.locator('.cart-item-qty input').first().inputValue()==='3');
   await Promise.all([page.waitForResponse(r=>r.url().includes('/korpa/ukloni/')&&r.request().method()==='POST'),page.locator('.cart-item-remove').first().click()]);
   await page.waitForLoadState('networkidle');check('remove empties cart',await page.locator('.cart-item-qty input').count()===0);
   await page.goto(base+'/artikal/qa-artikal-1/',{waitUntil:'networkidle'});
   await Promise.all([page.waitForResponse(r=>r.url().includes('/artikal/qa-artikal-1/dodaj/')&&r.request().method()==='POST'),page.locator('#mainAddToCartBtn').click()]);
   await page.goto(base+'/narudzba/',{waitUntil:'networkidle'});
   await page.locator('#checkout-form button[type="submit"]').click();check('empty checkout rejected',new URL(page.url()).pathname==='/narudzba/');
   for(const [name,value] of Object.entries({ime_prezime:'QA Kupac',telefon:'061000001',email:'qa-buyer@example.invalid',adresa:'QA Ulica 1',grad:'Sarajevo',postanski_broj:'71000'})) await page.locator(`#checkout-form [name="${name}"]`).fill(value);
   await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname==='/narudzba/'&&r.request().method()==='POST'),page.locator('#checkout-form button[type="submit"]').click()]);
   await page.waitForLoadState('networkidle');check('COD order reaches success',new URL(page.url()).pathname.startsWith('/narudzba/uspjeh/'));
   results.push(record);console.log(`${label} ${width}: ${record.checks.length} interaction checks passed`);await context.close();
  }
 } finally {await browser.close();fs.writeFileSync('reports/transfer-visual/interactions.json',JSON.stringify(results,null,2)+'\n');}
})().catch(e=>{console.error(e);process.exit(1)});
