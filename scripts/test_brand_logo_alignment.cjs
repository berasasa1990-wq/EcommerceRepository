// Run with node scripts/test_brand_logo_alignment.cjs [original-script-path].
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {performance} = require('node:perf_hooks');
const current = 'EcommerceApp/static/bera_reference/js/bera-brand-logo-align.js';
function run(file, blocked = false) {
    let reads = 0;
    const pixels = new Uint8ClampedArray(1000 * 500 * 4);
    for (let y = 100; y < 300; y++) for (let x = 200; x < 800; x++) {
        const i = (y * 1000 + x) * 4;
        pixels[i] = 10; pixels[i + 3] = 255;
    }
    const style = () => ({setProperty(key, value, priority) { this[key] = value; if (priority) this[`${key}:priority`] = priority; }});
    const images = Array.from({length:18}, (_, i) => ({
        currentSrc:`https://example.test/logo-${i % 6}.png`, naturalWidth:1000, naturalHeight:500,
        complete:true, style:style(), parentElement:{style:style()}
    }));
    const document = {querySelectorAll:() => images, createElement:() => ({getContext:() => ({
        drawImage() {}, getImageData() { reads++; if (blocked) { const error = new Error('Canvas is tainted'); error.name = 'SecurityError'; throw error; } return {data:pixels}; }
    })})};
    const start = performance.now();
    vm.runInNewContext(fs.readFileSync(file, 'utf8'), {document});
    const ms = performance.now() - start;
    for (const img of images) {
        if (blocked) { assert.equal(img.style.width, undefined); assert.equal(img.parentElement.style.width, undefined); continue; }
        assert.equal(img.parentElement.style.width, '60px');
        assert.equal(img.parentElement.style.height, '20px');
        assert.equal(img.style.width, '100px');
        assert.equal(img.style.height, '50px');
        assert.equal(img.style.transform, 'translate(-20px, -10px)');
    }
    return {reads, ms, styles:images.map(img => [img.style, img.parentElement.style])};
}
const optimized = run(current);
assert.equal(run(current, true).reads, 6, 'Repeated CORS failures retain fallback without rescanning');
assert.equal(optimized.reads, 6, 'One pixel analysis per unique source and dimensions');
if (process.argv[2]) {
    const baseline = run(process.argv[2]);
    assert.equal(baseline.reads, 18);
    assert.deepEqual(JSON.parse(JSON.stringify(optimized.styles)), JSON.parse(JSON.stringify(baseline.styles)));
    console.log(JSON.stringify({baseline:{reads:baseline.reads, ms:baseline.ms}, optimized:{reads:optimized.reads, ms:optimized.ms}}, null, 2));
} else console.log('PASS: exact artwork bounds and 18 logos require only 6 pixel reads.');
