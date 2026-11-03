const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/wms-picking-alternatives.js'), 'utf8');
function setup() {
    const inputs = [0, 0].map(() => ({value: '0', events: {}, validity: '',
        addEventListener(name, fn) {this.events[name] = fn;}, setCustomValidity(value) {this.validity = value;}}));
    const total = {}, events = {};
    let opened = 0, skipped = 0;
    const dialog = {dataset: {remaining: '4'}, querySelectorAll: () => inputs,
        querySelector: selector => selector === '[data-alternative-total]' ? total : {click() {skipped++;}},
        addEventListener: (name, fn) => events[name] = fn, removeAttribute() {}, showModal() {opened++;}};
    vm.runInNewContext(source, {document: {querySelector: () => dialog}});
    return {inputs, total, events, opened, skipped: () => skipped};
}

test('popup opens and checks the sum across selected locations', () => {
    const state = setup();
    assert.equal(state.opened, 1);
    state.inputs[0].value = '3'; state.inputs[1].value = '2';
    state.inputs[0].events.input();
    assert.match(state.total.textContent, /5 \/ 4/);
    assert.ok(state.inputs[0].validity);
    state.inputs[1].value = '1'; state.inputs[1].events.input();
    assert.match(state.total.textContent, /4 \/ 4/);
    assert.equal(state.inputs[0].validity, '');
});

test('closing popup uses explicit skip and never validates the order', () => {
    const state = setup(); let prevented = false;
    state.events.cancel({preventDefault() {prevented = true;}});
    assert.equal(prevented, true);
    assert.equal(state.skipped(), 1);
});
