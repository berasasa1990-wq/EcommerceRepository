// Comparable medians: three mobile and desktop audits per frozen local version.
import fs from 'node:fs';
import {pathToFileURL} from 'node:url';
const {default:lighthouse}=await import(process.env.LIGHTHOUSE_MODULE || '/private/tmp/carp-mobile-lighthouse/node_modules/lighthouse/core/index.js');
const {launch}=await import(process.env.CHROME_LAUNCHER_MODULE || '/private/tmp/carp-mobile-lighthouse/node_modules/chrome-launcher/dist/index.js');
const out=process.env.CARP_LIGHTHOUSE_OUT || 'reports/performance/speed-optimization/lighthouse';fs.mkdirSync(out,{recursive:true});const results=[];
const desktopConfig=(await import(new URL('./config/desktop-config.js', (process.env.LIGHTHOUSE_MODULE || '').startsWith('file:') ? process.env.LIGHTHOUSE_MODULE : pathToFileURL(process.env.LIGHTHOUSE_MODULE || '/private/tmp/carp-mobile-lighthouse/node_modules/lighthouse/core/index.js')))).default;
const beforeUrl=process.env.CARP_BEFORE_URL || 'http://127.0.0.1:8133/';
const afterUrl=process.env.CARP_AFTER_URL || 'http://127.0.0.1:8134/';
for(const url of [beforeUrl,afterUrl])if(!['127.0.0.1','localhost'].includes(new URL(url).hostname))throw Error('Use isolated local QA servers only');
for(const formFactor of ['mobile','desktop'])for(let sample=0;sample<3;sample++)for(const [mode,url] of [['before',beforeUrl],['after',afterUrl]]){
 const chrome=await launch({chromePath:process.env.BROWSER_EXECUTABLE || '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',chromeFlags:['--headless','--disable-gpu','--no-first-run','--no-default-browser-check']});
 try{const options={port:chrome.port,onlyCategories:['performance','accessibility','best-practices','seo'],logLevel:'error',output:'json',formFactor,throttlingMethod:'simulate',screenEmulation:formFactor==='desktop'?{mobile:false,width:1440,height:900,deviceScaleFactor:1,disabled:false}:{mobile:true,width:390,height:844,deviceScaleFactor:1,disabled:false}};
 const {lhr}=await lighthouse(url,options,formFactor==='desktop'?desktopConfig:undefined);if(process.env.CARP_SAVE_LIGHTHOUSE_DETAIL==='1')fs.writeFileSync(`${out}/${mode}-${formFactor}-${sample}.json`,JSON.stringify(lhr));const metric=id=>lhr.audits[id]?.numericValue;const r={mode,formFactor,sample,score:lhr.categories.performance.score*100,accessibility:lhr.categories.accessibility.score*100,bestPractices:lhr.categories['best-practices'].score*100,seo:lhr.categories.seo.score*100,fcp:metric('first-contentful-paint'),lcp:metric('largest-contentful-paint'),tbt:metric('total-blocking-time'),cls:metric('cumulative-layout-shift'),error:lhr.runtimeError};results.push(r);fs.writeFileSync(`${out}/summary.json`,JSON.stringify(results,null,2));console.log(JSON.stringify(r));}finally{await chrome.kill();}}
