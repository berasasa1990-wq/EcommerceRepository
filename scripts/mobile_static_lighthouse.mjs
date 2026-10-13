// True Lighthouse performance audits against DB-free local snapshot servers.
// Dependencies must be installed in /tmp; this does not change project packages.
import fs from 'node:fs';
const {default:lighthouse}=await import(process.env.LIGHTHOUSE_MODULE||'/private/tmp/carp-mobile-lighthouse/node_modules/lighthouse/core/index.js');
const {launch}=await import(process.env.CHROME_LAUNCHER_MODULE||'/private/tmp/carp-mobile-lighthouse/node_modules/chrome-launcher/dist/index.js');
const out='reports/performance/static-delivery';fs.mkdirSync(out,{recursive:true});const results=[];
for(let sample=0;sample<3;sample++)for(const [mode,port] of [['raw',8093],['compressed',8094]]){
const chrome=await launch({chromePath:process.env.BROWSER_EXECUTABLE||'/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',chromeFlags:['--headless','--disable-gpu','--no-first-run','--no-default-browser-check']});
try{const {lhr}=await lighthouse(`http://127.0.0.1:${port}/`,{port:chrome.port,onlyCategories:['performance'],logLevel:'error',output:'json',formFactor:'mobile',throttlingMethod:'simulate'});fs.writeFileSync(`${out}/lighthouse-${mode}-${sample}.json`,JSON.stringify(lhr,null,2));const metric=id=>lhr.audits[id]?.numericValue;const result={mode,sample,version:lhr.lighthouseVersion,score:lhr.categories.performance.score*100,fcp:metric('first-contentful-paint'),lcp:metric('largest-contentful-paint'),tbt:metric('total-blocking-time'),cls:metric('cumulative-layout-shift'),speedIndex:metric('speed-index'),warnings:lhr.runWarnings,error:lhr.runtimeError};results.push(result);fs.writeFileSync(`${out}/lighthouse-summary.json`,JSON.stringify(results,null,2));console.log(JSON.stringify(result));}finally{await chrome.kill()}}
