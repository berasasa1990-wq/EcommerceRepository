const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('EcommerceApp/static/js/warehouse-article-bulk.js', 'utf8');

async function preview(response) {
    const elements = {};
    for (const id of ['bulkPrepareForm', 'bulkNames', 'bulkSaveForm', 'bulkSaveButton', 'bulkPreviewRows', 'bulkError', 'bulkPreviewStatus', 'bulkBrands', 'bulkSourceText']) {
        elements[id] = { textContent: '', listeners: {}, addEventListener(event, fn) { this.listeners[event] = fn; }, replaceChildren() {} };
    }
    elements.bulkNames.value = 'FOX jakna';
    elements.bulkBrands.textContent = '[]';
    // A named input shadows the native form.action property in the browser.
    elements.bulkPrepareForm.action = { value: 'preview' };
    elements.bulkPrepareForm.getAttribute = () => '/nalog/magacin/novi-artikli-uvoz/bulk/';
    let requestedUrl;
    vm.runInNewContext(source, {
        document: { getElementById: id => elements[id] },
        window: { location: { href: 'https://example.test/fallback/' } },
        FormData: class {}, clearTimeout, setTimeout,
        fetch: async url => { requestedUrl = url; return response; }
    });
    elements.bulkPrepareForm.listeners.submit({ preventDefault() {} });
    await new Promise(resolve => setImmediate(resolve));
    return { elements, requestedUrl };
}

test('preview uses the URL attribute even with an input named action', async () => {
    const result = await preview({ ok: true, status: 200, headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ({ rows: [] }) });
    assert.equal(result.requestedUrl, '/nalog/magacin/novi-artikli-uvoz/bulk/');
    assert.equal(result.elements.bulkError.textContent, '');
    assert.equal(result.elements.bulkPreviewStatus.textContent, 'Pripremljeno artikala: 0.');
});

for (const status of [403, 404, 500]) {
    test(`HTML HTTP ${status} produces a readable error without parsing HTML as JSON`, async () => {
        const result = await preview({ status, headers: new Headers({ 'Content-Type': 'text/html' }), json: () => { throw Error('Must not parse HTML'); } });
        assert.ok(result.elements.bulkError.textContent.length > 0);
        assert.equal(result.elements.bulkSaveForm.hidden, true);
        assert.equal(result.elements.bulkSaveButton.disabled, true);
        assert.doesNotMatch(result.elements.bulkError.textContent, /Unexpected token|Must not parse/);
    });
}

test('login redirects ask the user to sign in again', async () => {
    const result = await preview({ redirected: true });
    assert.match(result.elements.bulkError.textContent, /prijavi se ponovo/);
    assert.equal(result.elements.bulkSaveForm.hidden, true);
});
