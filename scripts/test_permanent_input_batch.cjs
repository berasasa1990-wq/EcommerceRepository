// Retry a failed network batch without dropping any locally retained version.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const prefix = 'permanent-form-event.7.';
const storage = {};
for (let i = 0; i < 60; i++) storage[prefix + i] = JSON.stringify({event_id: String(i), owner: '7', path: '/nalog/', payload: []});
Object.defineProperties(storage, {
  getItem: {value: key => storage[key]},
  setItem: {value: (key, value) => { storage[key] = value; }},
  removeItem: {value: key => { delete storage[key]; }},
});
const config = {dataset: {user: '7', url: '/save'}, querySelector: () => ({value: 'csrf'})};
const status = {};
const callbacks = {};
const requests = [];
let fail = true;
vm.runInNewContext(fs.readFileSync('EcommerceApp/static/js/permanent-form-inputs.js', 'utf8'), {
  document: {getElementById: id => ({permanentInputConfig: config, permanentInputStatus: status}[id]), addEventListener() {}},
  window: {addEventListener: (name, callback) => { callbacks[name] = callback; }},
  localStorage: storage, setTimeout: () => 1, clearTimeout() {}, setInterval() {},
  fetch: async (_url, options) => {
    const events = JSON.parse(options.body).events;
    requests.push(events);
    return {ok: !fail, json: async () => ({ok: true, saved: events.map(event => event.event_id)})};
  },
});
(async () => {
  await new Promise(setImmediate);
  assert.equal(Object.keys(storage).length, 60);
  assert.equal(requests[0].length, 50);
  fail = false;
  await callbacks.online();
  assert.deepEqual(requests[1], requests[0]);
  assert.equal(Object.keys(storage).length, 10);
  await callbacks.online();
  assert.equal(Object.keys(storage).length, 0);
  assert.equal(new Set(requests.slice(1).flat().map(event => event.event_id)).size, 60);
  console.log('PASS: 60 versions retained across failure, retry and acknowledgement');
})().catch(error => { console.error(error); process.exitCode = 1; });
