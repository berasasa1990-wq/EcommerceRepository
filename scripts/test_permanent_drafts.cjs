const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('EcommerceApp/static/js/staff-manual-order-draft.js', 'utf8');
const wait = () => new Promise(resolve => setTimeout(resolve, 100));
function setup({fail = false, conflict = false} = {}) {
  const listeners = {}, formListeners = {}, clearListeners = {};
  const storage = new Map();
  let submits = 0, requests = 0;
  const status = {textContent: ''};
  const form = {
    action: '/new/', elements: {csrfmiddlewaretoken: {value: 'csrf'}},
    querySelectorAll: () => [], appendChild() {},
    addEventListener(name, fn) { (formListeners[name] ||= []).push(fn); }
  };
  const nodes = {
    mgOrderForm: form, mgDraftToken: {value: 'draft', dataset: {saveUrl: '/save', importUrl: '/import'}},
    mgDraftVersion: {value: '0'}, mgDraftStatus: status, mgDraftUI: {value: ''},
    mgDraftPayload: {textContent: '{}'},
    mgOrderClear: {addEventListener(name, fn) { clearListeners[name] = fn; }}
  };
  const document = {
    currentScript: {dataset: {user: '1'}}, getElementById: id => nodes[id],
    querySelectorAll: () => [], createElement: () => ({}),
    addEventListener(name, fn) { (listeners[name] ||= []).push(fn); }
  };
  const context = {
    document, JSON, Number, Array, Object, Promise, Math, Date, crypto: {randomUUID: () => 'tab'},
    localStorage: {getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key)},
    sessionStorage: {getItem: () => 'tab', setItem() {}},
    FormData: class {entries() { return [['napomena', 'Važan unos']][Symbol.iterator](); }},
    HTMLFormElement: {prototype: {submit() { submits++; }}},
    setInterval() {}, setTimeout(fn, delay) { if (delay < 1000) return setTimeout(fn, delay); },
    fetch: async () => { requests++; if (fail) throw Error('offline'); return {status: conflict ? 409 : 200, ok: !conflict, json: async () => conflict ? {error: 'Konflikt', version: 2} : {ok: true, version: 1}}; },
    window: {addEventListener() {}, location: {assign() {}, reload() {}}, confirm: () => true},
  };
  vm.runInNewContext(source, context);
  return {formListeners, listeners, storage, status, counts: () => ({submits, requests})};
}
function submit(env) {
  const event = {defaultPrevented: false, submitter: {name: 'action', value: 'sacuvaj'}, preventDefault() { this.defaultPrevented = true; }};
  for (const listener of env.formListeners.submit) listener(event);
}
(async () => {
  const valid = setup(); await wait(); submit(valid); await wait();
  assert.equal(valid.counts().submits, 1, 'submit after database acknowledgement');
  const invalid = setup();
  (invalid.formListeners.submit ||= []).push(event => event.preventDefault());
  await wait(); submit(invalid); await wait();
  assert.equal(invalid.counts().submits, 0, 'existing validation must veto submission');
  assert.equal(invalid.counts().requests, 0);
  const offline = setup({fail: true}); await wait(); submit(offline); await wait();
  assert.equal(offline.counts().submits, 0, 'offline submit must retain draft');
  assert([...offline.storage.values()].some(value => value.includes('Važan unos')));
  const conflict = setup({conflict: true}); await wait(); submit(conflict); await wait();
  assert.equal(conflict.counts().submits, 0, 'stale tab must not submit over newer values');
  assert.match(conflict.status.textContent, /potvrdu|historiji|noviji/);
  console.log('Draft client: database acknowledgement, validation, offline retention and conflicts passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });
