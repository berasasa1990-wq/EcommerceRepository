// Run with: node scripts/test_scratch_game_ui.cjs
// Exercise the actual browser script with a scratched card and controlled HTTP replies.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

async function game(addResponse) {
  const nodes = new Map();
  function element(id) {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, { hidden: true, disabled: false, textContent: id, style: {}, dataset: {},
        classList: { add: x => classes.add(x), contains: x => classes.has(x) },
        querySelector: selector => element(id + selector), listeners: {},
        addEventListener(name, callback) { this.listeners[name] = callback; },
      });
    }
    return nodes.get(id);
  }
  const modal = element('scratchGame');
  modal.dataset = { statusUrl: '/status', claimUrl: '/claim', eventUrl: '/event', addToOrderUrl: '/add' };
  const canvas = element('scratchGameCanvas');
  canvas.getBoundingClientRect = () => ({ width: 180, height: 180, left: 0, top: 0 });
  canvas.getContext = () => new Proxy({}, { get: (_, key) => key === 'createLinearGradient'
    ? () => ({ addColorStop() {} }) : () => {} });
  let start, addCalls = 0, redirected;
  const sandbox = {
    document: { getElementById: element, body: element('body'), querySelector: () => ({ content: 'csrf' }) },
    window: { devicePixelRatio: 1, location: { assign: url => { redirected = url; } } },
    URLSearchParams, setTimeout: callback => { start = callback; },
    fetch: async url => {
      if (url === '/status') return { json: async () => ({ eligible: true }) };
      if (url === '/claim') return { json: async () => ({ ok: true, won: true, product_offer: true,
        claim_id: 1, product_name: 'Nagrada', product_discount: '50', product_price: '10', product_regular_price: '20' }) };
      if (url === '/add') { addCalls++; return addResponse(); }
      return { json: async () => ({ ok: true }) };
    },
  };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../EcommerceApp/static/js/scratch-game.js'), 'utf8'), sandbox);
  await start();
  for (let y = 9; y < 180; y += 18) {
    for (let x = 9; x < 180; x += 18) {
      canvas.listeners.pointerdown({ pointerId: 1, clientX: x, clientY: y, preventDefault() {} });
    }
  }
  const button = element('scratchAddToOrder'), result = element('scratchGameResult');
  assert.equal(typeof button.onclick, 'function', 'scratching exposes the real add handler');
  assert.equal(result.hidden, true);
  return { button, result, calls: () => addCalls, redirected: () => redirected };
}

(async () => {
  const rejected = await game(async () => ({ ok: false, json: async () => ({ ok: false, detail: 'Nema na lageru.' }) }));
  await rejected.button.onclick();
  assert.equal(rejected.result.hidden, false);
  assert.equal(rejected.result.textContent, 'Nema na lageru.');
  assert.equal(rejected.button.disabled, false);

  for (const response of [async () => { throw new Error('offline'); },
    async () => ({ ok: false, json: async () => { throw new Error('HTML response'); } })]) {
    const failed = await game(response);
    await failed.button.onclick();
    assert.equal(failed.result.hidden, false);
    assert.match(failed.result.textContent, /Provjerite vezu/);
    assert.equal(failed.button.disabled, false);
  }

  let finish;
  const pending = await game(() => new Promise(resolve => { finish = resolve; }));
  const first = pending.button.onclick();
  await pending.button.onclick();
  assert.equal(pending.calls(), 1, 'double clicks cannot issue concurrent additions');
  finish({ ok: true, json: async () => ({ ok: true }) });
  await first;
  assert.equal(pending.redirected(), '/');
  console.log('Scratch UI: rejection, network failure, non-JSON reply and double-click/success PASS');
})().catch(error => { console.error(error); process.exitCode = 1; });
