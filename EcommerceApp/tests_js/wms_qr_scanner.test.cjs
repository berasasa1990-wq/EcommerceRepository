const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/staff-wms-qr.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function setup({denied = false} = {}) {
    const element = () => ({hidden: true, disabled: false, events: {}, attrs: {},
        addEventListener(name, fn) {this.events[name] = fn;},
        setAttribute(name, value) {this.attrs[name] = value;}, focus() {}, scrollIntoView() {}});
    const trigger = element(), close = element(), retry = element(), status = element(), panel = element();
    const events = {}, windowEvents = {}, navigations = [];
    const cameras = [];
    panel.dataset = {validationTemplate: '/nalog/magacin/wms/validacija/PICKINGTOKEN/'};
    panel.querySelector = selector => ({'[data-scan-close]': close, '[data-scan-retry]': retry, '[data-scan-status]': status})[selector];
    class Scanner {
        constructor() {this.stops = 0; cameras.push(this);}
        start(device, options, onCode) {this.onCode = onCode; return denied ? Promise.reject(new Error('denied')) : Promise.resolve();}
        stop() {this.stops++; return Promise.resolve();}
        clear() {}
    }
    const window = {Html5Qrcode: Scanner, location: {origin: 'https://shop.example', assign: url => navigations.push(url)},
        addEventListener: (name, fn) => windowEvents[name] = fn};
    const document = {querySelector: () => trigger, getElementById: () => panel,
        addEventListener: (name, fn) => events[name] = fn};
    vm.runInNewContext(source, {window, document, URL, Promise});
    return {trigger, close, retry, panel, status, cameras, navigations, events, windowEvents};
}

test('camera starts only on request; scanning opens next order without submitting validation', async () => {
    const state = setup();
    assert.equal(state.cameras.length, 0);
    await state.trigger.events.click();
    assert.equal(state.panel.hidden, false);
    assert.equal(state.cameras.length, 1);
    await state.cameras[0].onCode('https://shop.example/nalog/magacin/wms/validacija/abc:time:signature/');
    assert.deepEqual(state.navigations, ['https://shop.example/nalog/magacin/wms/validacija/abc:time:signature/']);
    assert.equal(state.cameras[0].stops, 1);
    await state.cameras[0].onCode('https://shop.example/nalog/magacin/wms/validacija/other/');
    assert.equal(state.navigations.length, 1);
});

test('rejects external URLs, non-picking routes and actions in query strings', async () => {
    const state = setup();
    await state.trigger.events.click();
    for (const code of ['https://other.example/nalog/magacin/wms/validacija/a/',
        'https://shop.example/other/', 'javascript:alert(1)',
        'https://shop.example/nalog/magacin/wms/validacija/a/?action=all']) {
        await state.cameras[0].onCode(code);
    }
    assert.equal(state.navigations.length, 0);
    assert.match(state.status.textContent, /nije picking QR/);
});

test('closing scanner releases camera and allows scanning again', async () => {
    const state = setup();
    await state.trigger.events.click();
    state.close.events.click();
    await flush();
    assert.equal(state.panel.hidden, true);
    assert.equal(state.trigger.disabled, false);
    assert.equal(state.cameras[0].stops, 1);
    await state.trigger.events.click();
    assert.equal(state.cameras.length, 2);
    state.windowEvents.pagehide();
    await flush();
    assert.equal(state.cameras[1].stops, 1);
});

test('camera permission failure offers retry and keeps order page usable', async () => {
    const state = setup({denied: true});
    await state.trigger.events.click();
    assert.equal(state.retry.hidden, false);
    assert.equal(state.trigger.disabled, false);
    assert.match(state.status.textContent, /Dozvoli pristup/);
    assert.equal(state.navigations.length, 0);
});
