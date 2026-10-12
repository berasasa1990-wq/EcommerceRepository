const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
(async () => {
 for (const path of ['EcommerceApp/static/js/cart-stock.js','EcommerceApp/static/bera_reference/js/cart-stock.js']) {
  const source = fs.readFileSync(path, 'utf8');
  async function measure(code) {
   let queries=0, reads=0;
   const rows=Array.from({length:40},(_,i)=>({
    label:{style:{},setAttribute(name,value){this[name]=value;}}, qty:{}, remove:{}, controls:[{},{}],
    getAttribute(){reads++;return 'key-'+i;},
    querySelector(selector){return selector==='[data-stock-label]'?this.label:selector==='[data-stock-remove]'?this.remove:this.qty;},
    querySelectorAll(){return this.controls;}
   }));
   const events={};
   const sandbox={Map,document:{hidden:false,addEventListener(n,fn){events[n]=fn;},getElementById(){return null;},querySelector(selector){return selector==='[data-cart-stock-url]'?{getAttribute(){return '/stock';}}:null;},querySelectorAll(){queries++;return rows;}},window:{setInterval(){},addEventListener(){}},fetch:async()=>({ok:true,json:async()=>({items:rows.map((_,i)=>({key:'key-'+i,sold_out:i%2===0,quantity:i+1}))})})};
   vm.runInNewContext(code,sandbox);events.DOMContentLoaded();
   await new Promise(resolve=>setImmediate(resolve));
   return {queries,reads,state:rows.map(row=>({sold:row.label['data-sold-out'],qty:row.qty.max,remove:row.remove.hidden,disabled:row.controls.map(c=>c.disabled)}))};
  }
  const old=source.replace(/                var rowsByKey = new Map\(\);[\s\S]*?                data.items.forEach\(function \(item\) \{\n                    \(rowsByKey.get\(item.key\) \|\| \[\]\).forEach\(function \(row\) \{/, "                data.items.forEach(function (item) {\n                    document.querySelectorAll('[data-cart-key]').forEach(function (row) {\n                        if (row.getAttribute('data-cart-key') !== item.key) return;");
  const before=await measure(old),after=await measure(source);
  assert.deepEqual(after.state,before.state);
  assert.equal(before.queries,40);assert.equal(after.queries,1);
  assert.equal(before.reads,1600);assert.equal(after.reads,40);
  console.log(path+': PASS identical stock/quantity/controls; DOM queries 40 -> 1; row scans 1600 -> 40');
 }
})().catch(error=>{console.error(error);process.exitCode=1;});
