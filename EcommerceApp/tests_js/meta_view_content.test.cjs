const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '../template/product_detail.html'), 'utf8');
const section = template.split('{% if meta_pixel_id and meta_view_content_event_id %}').at(-1);
const script = section.match(/<script data-cfasync="false">([\s\S]*?)<\/script>/)[1];

function runViewContent({ ready = false } = {}) {
    const calls = [];
    const dataset = {
        contentId: 'SKU-7', contentName: 'Test product', contentValue: '12',
        eventId: 'viewcontent-7-existingid',
    };
    let now = 0;
    let interval;
    let cleared = false;
    const context = {
        document: { querySelector: () => ({ dataset }) },
        Date: { now: () => now },
        setInterval: (callback, delay) => {
            assert.equal(delay, 50);
            interval = callback;
            return 1;
        },
        clearInterval: (id) => {
            assert.equal(id, 1);
            cleared = true;
        },
    };
    if (ready) context.fbq = (...args) => calls.push(args);
    vm.runInNewContext(script, context);
    return {
        calls,
        context,
        get interval() { return interval; },
        get cleared() { return cleared; },
        advance(ms) { now += ms; if (interval && !cleared) interval(); },
    };
}

function assertExistingEvent(call) {
    assert.equal(call[0], 'track');
    assert.equal(call[1], 'ViewContent');
    assert.equal(call[2].content_ids[0], 'SKU-7');
    assert.equal(call[2].content_type, 'product');
    assert.equal(call[2].content_name, 'Test product');
    assert.equal(call[2].value, 12);
    assert.equal(call[2].currency, 'BAM');
    assert.equal(call[3].eventID, 'viewcontent-7-existingid');
    assert.match(template, /data-event-id="{{ meta_view_content_event_id }}"/);
}

test('ready fbq sends ViewContent once with the server-provided event ID', () => {
    const result = runViewContent({ ready: true });
    assert.equal(result.calls.length, 1);
    assert.equal(result.interval, undefined);
    assertExistingEvent(result.calls[0]);
});

test('delayed fbq sends once and ignores later interval callbacks', () => {
    const result = runViewContent();
    assert.equal(result.calls.length, 0);
    result.advance(100);
    assert.equal(result.calls.length, 0);
    result.context.fbq = (...args) => result.calls.push(args);
    result.advance(50);
    assert.equal(result.calls.length, 1);
    assert.equal(result.cleared, true);
    assertExistingEvent(result.calls[0]);
    result.interval();
    assert.equal(result.calls.length, 1);
});

test('retry stops after five seconds if fbq never becomes available', () => {
    const result = runViewContent();
    result.advance(5000);
    assert.equal(result.cleared, true);
    result.context.fbq = (...args) => result.calls.push(args);
    result.interval();
    assert.equal(result.calls.length, 0);
});
