const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.innerHTML = ''; this.textContent = ''; this.value = ''; this.dataset = {}; this.listeners = {}; }
  addEventListener(name, fn) { this.listeners[name] = fn; }
  querySelectorAll() { return []; }
  setAttribute() {}
  close() {}
  showModal() {}
}
const elements = new Map();
const element = (key) => {
  if (!elements.has(key)) elements.set(key, new Element());
  return elements.get(key);
};
const document = {
  body: {dataset: {merchantKey: 'sawayaka'}}, hidden: false,
  querySelector: element, querySelectorAll: () => [],
  createElement: () => new Element(), addEventListener() {},
};
const shop = {id: 1, name: '店舗', is_open: true, is_issuable: true, current_waiting: 2};
let active = [], waitingReads = 0, failWaiting = false;
let pendingWaiting = null;
const context = vm.createContext({document, Intl, setInterval() {}, fetch: async (path, options = {}) => {
  if (path.endsWith('/snapshot') || path.endsWith('/refresh')) {
    return {ok: true, json: async () => ({shops: [shop], waiting: [], refreshed_at: '2026-09-11T00:00:00Z'})};
  }
  if (options.method === 'POST') {
    active = [{id: 42, shop_id: 1, number: 7, count: 3}];
    return {ok: true, json: async () => active[0]};
  }
  if (options.method === 'DELETE') {
    active = [];
    return {ok: true};
  }
  waitingReads++;
  if (pendingWaiting) return pendingWaiting;
  return {ok: !failWaiting, json: async () => structuredClone(active)};
}});
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const run = (source) => vm.runInContext(source, context);
const flush = () => new Promise((resolve) => setImmediate(resolve));

(async () => {
  await flush();
  assert.equal(waitingReads, 1, 'initial load must explicitly refresh queue state');
  run('openJoin(1)');
  const joinForm = element('#join-form');
  await joinForm.listeners.submit({preventDefault() {}, currentTarget: joinForm});
  assert.ok(element('#current-queue').innerHTML.includes('active-queue'));
  await run('refresh()');
  assert.ok(element('#current-queue').innerHTML.includes('active-queue'), 'snapshot must preserve active queue');
  assert.ok(element('#shop-list').innerHTML.includes('disabled'), 'snapshot must not enable another join');

  failWaiting = true;
  await run('refreshWaiting()');
  await run('refresh()');
  assert.ok(element('#shop-list').innerHTML.includes('disabled'), 'failed queue reads must preserve active state');
  failWaiting = false;

  // A read begun before cancellation must not restore the cancelled queue.
  let resolveOld;
  pendingWaiting = new Promise((resolve) => { resolveOld = resolve; });
  const oldRead = run('refreshWaiting()');
  await flush();
  pendingWaiting = null;
  element('#cancel-dialog').dataset.waitingId = 42;
  const cancelForm = element('#cancel-form');
  await cancelForm.listeners.submit({preventDefault() {}, currentTarget: cancelForm});
  resolveOld({ok: true, json: async () => [{id: 42, shop_id: 1, number: 7}]});
  await oldRead;
  await run('refresh()');
  assert.equal(element('#current-queue').textContent, '現在の順番待ちはありません');
  assert.ok(!element('#shop-list').innerHTML.includes('disabled'), 'cancelled queue must allow joining again');
  console.log('merchant queue ownership integration passed');
})().catch((error) => { console.error(error); process.exitCode = 1; });
