const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
(async () => {
 for (const path of ['EcommerceApp/static/js/cart-stock.js', 'EcommerceApp/static/bera_reference/js/cart-stock.js']) {
  let requests = 0, intervals = 0, nextTimer = 0;
  const timers = new Map(), docEvents = {}, winEvents = {};
  const root = {dataset: {}, getAttribute: () => '/stock'};
  const sandbox = {Map, clearTimeout: id => timers.delete(id),
   document: {hidden: false, querySelector: s => s === '[data-cart-stock-url]' ? root : null,
    querySelectorAll: () => [], getElementById: () => null, addEventListener: (n, f) => docEvents[n] = f},
   window: {setInterval: () => intervals++, setTimeout: f => {timers.set(++nextTimer, f); return nextTimer;},
    addEventListener: (n, f) => winEvents[n] = f},
   fetch: async () => {requests++; return {ok: true, json: async () => ({items: []})};}};
  vm.runInNewContext(fs.readFileSync(path, 'utf8'), sandbox);
  docEvents.DOMContentLoaded(); docEvents.DOMContentLoaded();
  await new Promise(setImmediate);
  assert.equal(requests, 1); assert.equal(intervals, 0);
  winEvents.focus(); docEvents.visibilitychange();
  assert.equal(timers.size, 1);
  for (const f of timers.values()) f(); timers.clear();
  await new Promise(setImmediate);
  assert.equal(requests, 2);
  sandbox.document.hidden = true; docEvents.visibilitychange();
  for (const f of timers.values()) f(); timers.clear();
  await new Promise(setImmediate);
  assert.equal(requests, 2);
  sandbox.document.hidden = false; winEvents.pageshow({persisted: true});
  for (const f of timers.values()) f();
  await new Promise(setImmediate);
  assert.equal(requests, 3);
  console.log(path + ': PASS no interval; duplicate initialization and focus/visibility requests coalesced; cached-page restore refreshes stock');
 }
})().catch(e => {console.error(e); process.exitCode = 1;});
