const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function element() {
    return { hidden: false, handlers: {}, attrs: {},
        addEventListener(name, fn) { this.handlers[name] = fn; },
        setAttribute(name, value) { this.attrs[name] = value; },
        removeAttribute(name) { delete this.attrs[name]; },
    };
}
const slides = [element(), element(), element()];
slides[1].hidden = slides[2].hidden = true;
const root = element();
root.querySelectorAll = () => slides;
root.contains = () => false;
const document = element();
document.querySelectorAll = () => [root];
document.hidden = false;
const motion = element(); motion.matches = false;
const timers = new Map(); let id = 0;
vm.runInNewContext(fs.readFileSync('EcommerceApp/static/js/hero-slider.js', 'utf8'), {
    document, window: { matchMedia: () => motion },
    setInterval(fn, delay) { assert.equal(delay, 3000); timers.set(++id, fn); return id; },
    clearInterval(key) { timers.delete(key); }, setTimeout(fn) { fn(); },
});
const visible = index => {
    assert.deepEqual(slides.map(slide => slide.hidden), slides.map((_, i) => i !== index));
};
assert.equal(timers.size, 1);
[...timers.values()][0](); visible(1);
[...timers.values()][0](); visible(2);
[...timers.values()][0](); visible(0);
document.hidden = true; document.handlers.visibilitychange(); assert.equal(timers.size, 0);
document.hidden = false; document.handlers.visibilitychange(); assert.equal(timers.size, 1);
root.handlers.mouseenter(); assert.equal(timers.size, 0);
root.handlers.mouseleave(); assert.equal(timers.size, 1);
motion.matches = true; motion.handlers.change(); assert.equal(timers.size, 0);
console.log('PASS: 3-second interval, wraparound, autoplay without controls, background tab, hover and reduced motion');
