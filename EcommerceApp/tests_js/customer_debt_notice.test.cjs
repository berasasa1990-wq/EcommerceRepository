const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/js/staff-magacin.js'), 'utf8');
const start = source.indexOf('    function showCustomerExcess(');
const end = source.indexOf('    function refreshOrderTotal()', start);

function setup(editing = false) {
    const nodes = {};
    function element() {
        return {
            children: [], style: {}, attributes: {}, textContent: '',
            setAttribute(key, value) { this.attributes[key] = value; },
            appendChild(child) { this.children.push(child); if (child.id) nodes[child.id] = child; },
            replaceChildren() { this.children = []; },
            remove() { delete nodes[this.id]; },
            cloneNode() { return Object.assign(element(), { textContent: this.textContent }); },
            addEventListener() {}, focus() {},
        };
    }
    const form = element();
    form.querySelector = () => editing ? {} : null;
    const context = {
        form, refreshOrderTotal() {}, Number, Array,
        window: { setTimeout(callback) { callback(); } },
        document: { getElementById(id) { return nodes[id]; }, createElement: element, body: element() }
    };
    vm.runInNewContext(source.slice(start, end), context);
    return { nodes, form, show: context.showCustomerExcess };
}

test('debt selection opens a warning and keeps debt separate from goods', () => {
    const state = setup();
    state.show([{ name: 'Raniji višak', quantity: 1, amount: '20.00' }], [], '75.50');
    assert.equal(state.form.attributes['data-auto-debt-total'], '75.50');
    assert.equal(state.form.attributes['data-auto-excess-total'], '20.00');
    assert.match(state.nodes.mgAutoExcessPreview.children[0].textContent, /Kupac ima dug 75.50 KM/);
    assert.ok(state.nodes.mgAutoExcessNotice);
});

test('switching to a customer without debt clears old warning and charge', () => {
    const state = setup();
    state.show([], [], '100');
    state.show([], [], '0');
    assert.equal(state.form.attributes['data-auto-debt-total'], '0.00');
    assert.equal(state.nodes.mgAutoExcessNotice, undefined);
    assert.equal(state.nodes.mgAutoExcessPreview.hidden, true);
});

test('editing an existing order does not append its debt again', () => {
    const state = setup(true);
    state.show([], [], '100');
    assert.equal(state.form.attributes['data-auto-debt-total'], undefined);
    assert.equal(state.nodes.mgAutoExcessNotice, undefined);
});
