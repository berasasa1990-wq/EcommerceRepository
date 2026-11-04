const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname, '../static/js/product-image-recovery.js'), 'utf8');
function setup(broken = true) {
    const events = {};
    const thumbs = ['/missing.avif', '/gallery-1.avif', '/gallery-2.avif'].map(src => ({
        dataset: {src, full: src}, hidden: false, classList: {toggle() {}}, setAttribute() {}
    }));
    const image = {tagName: 'IMG', complete: true, naturalWidth: broken ? 0 : 600, src: '/missing.avif',
        dataset: {}, srcset: '/missing.avif 600w', removeAttribute(name) {delete this[name];},
        addEventListener(name, fn) {events[name] = fn;}};
    vm.runInNewContext(source, {document: {baseURI: 'https://shop.example/artikal/test/',
        getElementById: () => image, querySelectorAll: () => thumbs}, URL, Set});
    return {image, thumbs, events};
}
test('already failed main image uses first gallery image and clears broken srcset', () => {
    const {image, thumbs} = setup();
    assert.equal(image.src, '/gallery-1.avif');
    assert.equal(image.dataset.fullSrc, '/gallery-1.avif');
    assert.equal(image.srcset, undefined);
    assert.equal(thumbs[0].hidden, true);
});
test('later errors move to another image and stop after all candidates fail', () => {
    const {image, events} = setup();
    events.error();
    assert.equal(image.src, '/gallery-2.avif');
    events.error(); events.error();
    assert.equal(image.src, '/gallery-2.avif');
});
test('successfully loaded main image stays unchanged', () => {
    const {image, thumbs} = setup(false);
    assert.equal(image.src, '/missing.avif');
    assert.equal(thumbs.every(thumb => !thumb.hidden), true);
});
