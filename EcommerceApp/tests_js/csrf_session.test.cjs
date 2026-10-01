const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/csrf-session.js'), 'utf8');

function setup(withForm = false, fetchImpl) {
    const windowEvents = {}, documentEvents = {}, formEvents = {};
    const input = { value: 'stale' }, meta = { content: 'stale' };
    const button = { disabled: false };
    const errors = [];
    const form = {
        dataset: { csrfRefreshUrl: '/prijava/csrf/' },
        appendChild(node) { errors.push(node); },
        addEventListener(name, callback) { formEvents[name] = callback; },
        requestSubmit(submitter) {
            const event = { submitter, preventDefault() { throw Error('unexpected replay block'); } };
            documentEvents.submit();
            formEvents.submit(event);
            this.submitted = true;
        }
    };
    const document = {
        cookie: 'csrftoken=current', currentScript: { dataset: { cookieName: 'csrftoken' } },
        querySelectorAll(selector) { return selector.startsWith('input') ? [input] : (withForm ? [form] : []); },
        querySelector() { return meta; },
        createElement() { return { setAttribute() {} }; },
        addEventListener(name, callback) { documentEvents[name] = callback; }
    };
    const fetch = (...args) => fetchImpl(document, ...args);
    vm.runInNewContext(source, { document, fetch, window: {
        addEventListener(name, callback) { windowEvents[name] = callback; }
    }});
    return { document, windowEvents, documentEvents, formEvents, input, meta, form, button, errors };
}

test('another tab rotates cookie: focus and submit update hidden and meta tokens', () => {
    const state = setup();
    assert.equal(state.input.value, 'current');
    state.document.cookie = 'csrftoken=rotated';
    state.windowEvents.focus();
    assert.equal(state.meta.content, 'rotated');
    state.document.cookie = 'csrftoken=latest';
    state.documentEvents.submit();
    assert.equal(state.input.value, 'latest');
});

test('auth waits for uncached same-origin token before submitting once', async () => {
    let calls = 0;
    const state = setup(true, async (document, url, options) => {
        calls++;
        assert.equal(url, '/prijava/csrf/');
        assert.equal(options.cache, 'no-store');
        assert.equal(options.credentials, 'same-origin');
        document.cookie = 'csrftoken=latest';
        return { ok: true, json: async () => ({ csrfToken: 'masked-latest' }) };
    });
    let prevented = false;
    await state.formEvents.submit({ submitter: state.button, preventDefault() { prevented = true; } });
    assert.ok(prevented);
    assert.equal(calls, 1);
    assert.ok(state.form.submitted);
    assert.equal(state.input.value, 'latest');
    assert.equal(state.button.disabled, false);
});

test('failed refresh keeps credentials on page and allows retry without sending POST', async () => {
    const state = setup(true, async () => { throw Error('offline'); });
    await state.formEvents.submit({ submitter: state.button, preventDefault() {} });
    assert.equal(state.form.submitted, undefined);
    assert.equal(state.errors[0].hidden, false);
    assert.equal(state.button.disabled, false);
});

test('unreadable CSRF cookie preserves server rendered masked token', () => {
    const state = setup();
    state.document.cookie = '';
    state.input.value = 'masked';
    state.meta.content = 'masked';
    state.windowEvents.pageshow();
    assert.equal(state.input.value, 'masked');
    assert.equal(state.meta.content, 'masked');
});
