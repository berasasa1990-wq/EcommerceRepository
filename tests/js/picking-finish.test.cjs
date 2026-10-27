const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('EcommerceApp/static/js/staff-magacin.js', 'utf8');
function functionSource(name) {
    const start = source.indexOf('    function ' + name + '(');
    const end = source.indexOf('\n    function ', start + 1);
    return source.slice(start, end);
}
test('3/4 picked with a zero shortage allows finishing and submits actual quantities', () => {
    const queue = [{ key: 'a', item_id: 1, need: 3, ordered: 3 }, { key: 'b', item_id: 2, need: 1, ordered: 1 }];
    const state = { a: { got: 3, done: true }, b: { got: 0, done: false } };
    const context = { queue, state, draftQuantities: {}, editingKey: '',
        root: { classList: { toggle() {} } },
        els: { form: {}, valid: {}, validDone: {} },
        itemState: item => state[item.key], doneCount: () => 1 };
    vm.createContext(context);
    for (const name of ['syncValidateGate', 'pickedQuantity', 'pickQtySummary', 'pickPayload']) {
        vm.runInContext(functionSource(name), context);
    }
    context.syncValidateGate();
    assert.equal(context.els.form.hidden, false);
    assert.equal(context.els.valid.disabled, false);
    assert.equal(context.pickQtySummary().missing, 1);
    assert.equal(context.pickPayload()[1].got, 0);
    // A direct quantity edit must also reach the final confirmation and server.
    context.draftQuantities.a = 2;
    assert.equal(context.pickQtySummary().picked, 2);
    assert.equal(context.pickPayload()[0].got, 2);
});

test('picked quantity can exceed ordered quantity in summary and payload', () => {
    const item = { key: 'a', item_id: 1, need: 15, ordered: 15 };
    const context = { queue: [item], draftQuantities: { a: 18 }, itemState: () => ({ got: 12 }) };
    vm.createContext(context);
    for (const name of ['pickedQuantity', 'pickQtySummary', 'pickPayload']) vm.runInContext(functionSource(name), context);
    assert.equal(context.pickQtySummary().picked, 18);
    assert.equal(context.pickPayload()[0].got, 18);
    assert.equal(context.pickQtySummary().missing, 0);
});

test('confirming zero, less, or more records quantity directly without a cleaning request', () => {
    const calls = [];
    const context = { setGot: (...args) => calls.push(args) };
    vm.createContext(context);
    vm.runInContext(functionSource('confirmShortQuantity'), context);
    const item = { key: 'a', need: 15 };
    for (const qty of [0, 12, 18]) context.confirmShortQuantity(item, qty);
    assert.deepEqual(calls.map(call => call.slice(1)), [[0, true], [12, true], [18, true]]);
});
