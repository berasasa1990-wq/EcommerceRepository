/* Event regressions for the header scripts, without a browser dependency. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
class Classes {
    constructor() { this.values = new Set(); }
    contains(value) { return this.values.has(value); }
    add(...values) { values.forEach(value => this.values.add(value)); }
    remove(...values) { values.forEach(value => this.values.delete(value)); }
    toggle(value, force = !this.contains(value)) { force ? this.add(value) : this.remove(value); return force; }
}
class Element {
    constructor(parent = null) {
        this.parent = parent; this.classList = new Classes(); this.dataset = {};
        this.style = {removeProperty() {}}; this.listeners = {}; this.attributes = {};
        this.hidden = false; this.value = ''; this.innerHTML = '';
    }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    setAttribute(key, value) { this.attributes[key] = value; }
    getAttribute(key) { return this.attributes[key] ?? null; }
    removeAttribute(key) { delete this.attributes[key]; }
    contains(node) { return node === this || Boolean(node?.parent && this.contains(node.parent)); }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    closest(selector) {
        if (selector === '[data-cats-toggle]' && this.dataset.catsToggle !== undefined) return this;
        if (selector === '.cats-dropdown__item--has-sub' && this.classList.contains('cats-dropdown__item--has-sub')) return this;
        return this.parent?.closest(selector) || null;
    }
}
const document = new Element();
document.body = new Element(); document.documentElement = new Element();
const header = new Element(document);
const search = new Element(header), input = new Element(search), suggestions = new Element(search);
suggestions.hidden = true;
const menu = new Element(header), button = new Element(menu), panel = new Element(menu);
panel.hidden = true;
const item = new Element(menu); item.classList.add('cats-dropdown__item--has-sub');
const toggle = new Element(item); toggle.dataset.catsToggle = '';
menu.querySelectorAll = () => [item]; item.querySelector = () => toggle;
const elements = {header, headerSearch:search, searchInput:input, searchSuggestions:suggestions,
    searchForm:new Element(search), catsDropdown:menu, catsDropdownBtn:button, catsDropdownPanel:panel};
document.getElementById = id => elements[id] || null;
document.querySelector = selector => selector === '.search-combobox' ? new Element(search) : null;
const window = new Element();
Object.assign(window, {innerWidth:1280, scrollY:0, setTimeout, clearTimeout,
    matchMedia:() => ({matches:false}), location:{pathname:'/', search:'', hash:''}});
const pending = [];
const context = vm.createContext({document, window, setTimeout, clearTimeout, AbortController,
    requestAnimationFrame:fn => fn(), fetch:url => new Promise(resolve => pending.push({url, resolve})), console});
function run(file) { vm.runInContext(fs.readFileSync(path.join(root, file), 'utf8'), context); }
// Execute the real header initialization, ending before unrelated catalog code.
const source = fs.readFileSync(path.join(root, 'EcommerceApp/static/js/main.js'), 'utf8');
const headerSource = source.slice(0, source.indexOf('    const urlParams =')) + '\n});';
vm.runInContext(headerSource, context);
vm.runInContext(headerSource, context);
run('EcommerceApp/static/js/category-menu.js');
run('EcommerceApp/static/js/category-menu.js'); // Cached/duplicate inclusion stays harmless.
document.listeners.DOMContentLoaded.forEach(fn => fn());
function dispatch(target, type) {
    const event = {target, key:'', preventDefault() {}, stopPropagation() { this.stopped = true; }};
    for (let node = target; node; node = node.parent) {
        (node.listeners[type] || []).forEach(fn => fn(event));
        if (event.stopped) break;
    }
}
const pause = () => new Promise(resolve => setTimeout(resolve, 180));
const flush = () => new Promise(resolve => setImmediate(resolve));
function respond(request, query, results) {
    request.resolve({ok:true, json:async () => ({query, results, has_more:true})});
}
const product = name => ({naziv:name, url:'/proizvod/test/', price:'12.00', image:'', pack:''});
(async () => {
    dispatch(button, 'click'); assert.equal(panel.hidden, false, 'One click must open categories');
    dispatch(toggle, 'click'); assert.equal(item.classList.contains('is-open'), true, 'One click must open a subcategory');
    dispatch(toggle, 'click'); assert.equal(item.classList.contains('is-open'), false);
    dispatch(button, 'click'); assert.equal(panel.hidden, true, 'Second click must close categories');
    input.value = 'cizme'; dispatch(input, 'input'); await pause();
    assert.equal(pending.length, 1, 'Duplicate initialization must not send duplicate searches');
    assert.equal(suggestions.hidden, false, 'Loading panel stays visible');
    respond(pending.shift(), 'cizme', Array.from({length:7}, (_, i) => product(`Čizme ${i}`)));
    await flush();
    assert.equal(suggestions.hidden, false, 'Results must replace loading without hiding');
    assert.equal((suggestions.innerHTML.match(/role="option"/g) || []).length, 5);
    assert.match(suggestions.innerHTML, /Prikaži više/);
    assert.equal(suggestions.style.position, 'absolute', 'Suggestions stay anchored to the search field');
    const previous = suggestions.innerHTML;
    input.value = 'zelene'; dispatch(input, 'input'); await pause();
    assert.equal(suggestions.innerHTML, previous, 'Keep results visible while the next request is pending');
    const stale = pending.shift();
    input.value = 'eva cizme'; dispatch(input, 'input'); await pause();
    respond(stale, 'zelene', [product('Stari odgovor')]); await flush();
    assert.equal(suggestions.innerHTML, previous, 'Late responses cannot replace current results');
    respond(pending.shift(), 'eva cizme', [product('EVA čizme')]); await flush();
    assert.match(suggestions.innerHTML, /EVA čizme/);
    dispatch(suggestions, 'click'); assert.equal(suggestions.hidden, false);
    dispatch(button, 'click'); assert.equal(panel.hidden, false); assert.equal(suggestions.hidden, true);
    input.value = 'nema'; dispatch(input, 'input'); await pause();
    respond(pending.shift(), 'nema', []); await flush();
    assert.equal(suggestions.hidden, false); assert.match(suggestions.innerHTML, /Nema artikala/);
    input.value = 'x'; dispatch(input, 'input'); assert.equal(suggestions.hidden, true);
    console.log('Header interactions passed: categories, subcategories, loading/results, five suggestions, stale requests, empty results, outside clicks.');
})().catch(error => { console.error(error); process.exitCode = 1; });
