const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const tick = () => new Promise(resolve => setImmediate(resolve));
function extract(source, name, next) {
  const start = source.indexOf('    function ' + name + '(');
  assert(start >= 0, name);
  return source.slice(start, source.indexOf('    function ' + next + '(', start));
}
(async () => {
  for (const file of ['staff-popis-test.js', 'staff-magacin.js']) {
    const fullSource = fs.readFileSync('EcommerceApp/static/js/' + file, 'utf8');
    const source = fullSource.slice(fullSource.indexOf("var root = document.getElementById('ptApp')"));
    const pending = [], started = [];
    const context = vm.createContext({Promise, mutationQueue: Promise.resolve(), sendPost(action) {
      started.push(action);
      return new Promise((resolve, reject) => pending.push({resolve, reject}));
    }});
    vm.runInContext(extract(source, 'post', 'sendPost'), context);
    const first = context.post('first');
    const second = context.post('second');
    const third = context.post('third');
    await tick();
    assert.deepEqual(started, ['first']);
    pending[0].resolve(1); await first; await tick();
    assert.deepEqual(started, ['first', 'second']);
    const handled = second.catch(() => null);
    pending[1].reject(new Error('network')); await handled; await tick();
    assert.deepEqual(started, ['first', 'second', 'third']);
    pending[2].resolve(3); assert.equal(await third, 3);

    const requests = [], shown = [], picked = [];
    const searchContext = vm.createContext({AbortController, searchVersion: 0, searchAbort: null,
      duplicateDialog: null, lookupUrl: '/lookup', encodeURIComponent,
      flattenLookup: rows => rows, isExactMatch: (row, q) => row.code === q,
      hideSuggest() {}, showSuggest: rows => shown.push(rows), showToast() {},
      pickItem: item => picked.push(item),
      fetch(url, options) { return new Promise(resolve => requests.push({options, resolve})); }
    });
    vm.runInContext(extract(source, 'searchArticles', 'scan'), searchContext);
    searchContext.searchArticles('old', false);
    searchContext.searchArticles('new', false);
    assert.equal(requests[0].options.signal.aborted, true);
    requests[1].resolve({json: async () => ({results: [{code: 'new'}]})});
    await tick();
    requests[0].resolve({json: async () => ({results: [{code: 'old'}]})});
    await tick();
    assert.equal(shown.length, 1); assert.equal(shown[0][0].code, 'new');
    searchContext.searchArticles('scan1', true);
    searchContext.searchArticles('scan2', true);
    assert.equal(requests[2].options.signal, undefined);
    assert.equal(requests[3].options.signal, undefined);
    requests[3].resolve({json: async () => ({results: [{code: 'scan2'}]})});
    requests[2].resolve({json: async () => ({results: [{code: 'scan1'}]})});
    await tick();
    assert.deepEqual(picked.map(item => item.code).sort(), ['scan1', 'scan2']);
    console.log(file + ': sequential writes, failure recovery, stale search and rapid scans passed');
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
