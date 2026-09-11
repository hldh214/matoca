const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Read the real shell: missing controls must never become invented fixture nodes.
class Element {
  constructor(tagName) {
    Object.assign(this, {tagName, children: [], attributes: {}, dataset: {}, listeners: {}, parentElement: null,
      value: '', disabled: false, hidden: false, open: false, _text: ''});
    this.classList = {
      contains: (value) => this.className.split(/\s+/).includes(value),
      toggle: (value, force) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (force ?? !names.has(value)) names.add(value); else names.delete(value);
        this.className = [...names].join(' ');
      },
    };
  }
  get className() { return this.attributes.class || ''; }
  set className(value) { this.attributes.class = value; }
  get id() { return this.attributes.id; }
  set id(value) { this.attributes.id = value; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(''); }
  set textContent(value) { this._text = String(value ?? ''); this.children = []; }
  set innerHTML(_) { throw new Error('Dynamic HTML must not be used'); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name.startsWith('data-')) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())] = String(value);
    if (name === 'value') this.value = String(value);
    if (name === 'disabled' || name === 'hidden') this[name] = true;
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  append(...children) {
    for (let child of children) {
      if (typeof child === 'string') { const node = new Element('#text'); node.textContent = child; child = node; }
      child.parentElement = this; this.children.push(child);
    }
  }
  appendChild(child) { this.append(child); return child; }
  replaceChildren(...children) { this.children = []; this._text = ''; this.append(...children); }
  matches(selector) {
    const excluded = selector.match(/:not\(([^)]+)\)/);
    if (excluded && this.matches(excluded[1])) return false;
    selector = selector.replace(/:not\([^)]+\)/g, '');
    if (selector.includes(':disabled') && !this.disabled) return false;
    selector = selector.replace(':disabled', '');
    const tag = selector.match(/^[a-z]+/);
    if (tag && this.tagName !== tag[0]) return false;
    const id = selector.match(/#([\w-]+)/);
    if (id && this.id !== id[1]) return false;
    for (const match of selector.matchAll(/\.([\w-]+)/g)) if (!this.classList.contains(match[1])) return false;
    for (const match of selector.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)) {
      const actual = match[1].startsWith('data-')
        ? this.dataset[match[1].slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase())]
        : this.attributes[match[1]];
      if (actual === undefined || (match[2] !== undefined && actual !== match[2])) return false;
    }
    return true;
  }
  querySelectorAll(selector) {
    const selectors = selector.split(',').map((item) => item.trim()), nodes = [];
    const visit = (node) => { for (const child of node.children) { if (selectors.some((part) => child.matches(part))) nodes.push(child); visit(child); } };
    visit(this); return nodes;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return this.matches(selector) ? this : this.parentElement?.closest(selector) || null; }
  addEventListener(name, callback) { (this.listeners[name] ||= []).push(callback); }
  async emit(name) {
    if (name === 'click' && this.disabled) return;
    for (const callback of this.listeners[name] || []) await callback({preventDefault() {}, currentTarget: this, target: this});
  }
  showModal() { this.open = true; }
  close() { this.open = false; return this.emit('close'); }
}

function templateDocument() {
  const document = new Element('document'), stack = [document];
  const source = fs.readFileSync(path.join(path.dirname(process.argv[2]), '../templates/merchant.html'), 'utf8').replace(/{{.*?}}/g, '加盟店');
  for (const token of source.match(/<[^>]+>|[^<]+/g)) {
    if (token.startsWith('</')) { stack.pop(); continue; }
    if (token.startsWith('<!')) continue;
    if (!token.startsWith('<')) { stack.at(-1)._text += token; continue; }
    const tag = token.match(/^<([a-z0-9-]+)/)[1], node = new Element(tag);
    for (const attribute of token.slice(tag.length + 1, -1).matchAll(/([\w-]+)(?:="([^"]*)")?/g)) node.setAttribute(attribute[1], attribute[2] ?? '');
    stack.at(-1).append(node);
    if (!['meta', 'link', 'input', 'img', 'br'].includes(tag)) stack.push(node);
  }
  document.body = document.querySelector('body'); document.body.dataset.merchantKey = 'sawayaka'; document.hidden = false;
  document.createElement = (tag) => new Element(tag);
  return document;
}

const copy = (value) => structuredClone(value);
const response = (value, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => copy(value)});
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return {promise, resolve}; };
const flush = async () => { for (let round = 0; round < 4; round++) await new Promise(setImmediate); };
const confirmation = (options = {}) => ({title: '呼び出し時に不在の場合', enable: true,
  sub_items: [{sub_item_index: 0, text: '無効な選択肢', enable: true, disabled: true}, {sub_item_index: 2, text: '了承しました', enable: true, disabled: false}], ...options});
const forms = (overrides = {}) => ({is_ticketing_only: false, is_confirm_tel: false,
  min_adult: 1, max_adult: 8, default_value_adult: 1, min_child: 0, max_child: 4, default_value_child: 0,
  is_confirm_child: true, confirm_items: [confirmation()], ...overrides});
const availableShop = {id: 1, name: '加盟店', sub_name: '本店', address: '静岡県', image_url: null,
  current_waiting: 3, official_waiting_minutes: 25, official_waiting_is_more: false,
  status: 'available', status_label: '受付可能', can_join: true, stale: false,
  updated_at: '2026-09-11T00:00:00Z', forms: forms()};
const initialConsole = {merchant: {key: 'sawayaka', name: '加盟店', cover_image_url: null}, updated_at: '2026-09-11T00:00:00Z',
  stale: false, available_count: 1, total_count: 3, shops: [availableShop,
    {...availableShop, id: 2, sub_name: '駅前店', can_join: false, status: 'stale', status_label: '更新待ち', stale: true, official_waiting_minutes: null, is_issuable: true, is_open: true},
    {...availableShop, id: 3, sub_name: '海岸店', can_join: false, status: 'closed', status_label: '営業時間外'}]};
const waitingItem = {id: 42, shop_id: 1, number: 7, count: 3, adult_count: 2, child_count: 0, status: 'waiting'};

async function setup(overrides = {}) {
  const document = templateDocument();
  const state = {catalog: copy(initialConsole), active: [], preferences: {default_adult_count: 2, default_child_count: 0},
    detail: {id: 1, name: '加盟店', sub_name: '本店', current_waiting: 4, waiting_time: {minutes: 30, is_more: false}, forms: forms(),
      is_open: true, is_issuable: true, is_holiday: false, is_suspended: false},
    failWaiting: false, failConsole: false, failDetail: false, pendingWaiting: null, pendingDetail: null, calls: [], timers: [], ...overrides};
  const context = vm.createContext({document, Intl, URL, setInterval: (callback, delay) => state.timers.push({callback, delay}), fetch: async (url, options = {}) => {
    state.calls.push({url, ...options}); const method = options.method || 'GET';
    if (url.endsWith('/console')) return response(state.catalog, state.failConsole ? 503 : 200);
    if (url.endsWith('/refresh')) return response({waiting: [], shops: []});
    if (url === '/api/preferences') {
      if (method === 'PUT') state.preferences = JSON.parse(options.body);
      if (method === 'GET' && state.pendingPreferences) {
        const pending = state.pendingPreferences; state.pendingPreferences = null; return pending;
      }
      return response(state.preferences);
    }
    if (url.startsWith('/api/shops/')) return state.pendingDetail || response(state.detail, state.failDetail ? 503 : 200);
    if (url.endsWith('/waiting') && method === 'GET') return state.pendingWaiting || response(state.active, state.failWaiting ? 503 : 200);
    if (url.endsWith('/waiting') && method === 'POST') {
      if (state.createError) return response(state.createError, 409);
      state.active = [copy(waitingItem)]; return response(state.active[0]);
    }
    if (url.endsWith('/waiting/42') && method === 'DELETE') { state.active = []; return response(null, 204); }
    throw new Error(`Unexpected synthetic request ${method} ${url}`);
  }});
  const modules = new Map();
  async function load(filename) {
    if (modules.has(filename)) return modules.get(filename);
    assert.ok(fs.existsSync(filename), `Required ES module missing: ${filename}`);
    const module = new vm.SourceTextModule(fs.readFileSync(filename, 'utf8'), {context, identifier: filename});
    modules.set(filename, module);
    await module.link((specifier, referring) => load(path.resolve(path.dirname(referring.identifier), specifier)));
    return module;
  }
  const entry = await load(path.resolve(process.argv[2])); await entry.evaluate(); await flush();
  const get = (selector) => { const node = document.querySelector(selector); assert.ok(node, `Missing real control: ${selector}`); return node; };
  return {document, state, get, modules,
    async click(selector) { await get(selector).emit('click'); await flush(); },
    async submit(selector) { await get(selector).emit('submit'); await flush(); },
    async tick() { for (const timer of state.timers) await timer.callback(); await flush(); }};
}

const cases = {
  async initial_filter_counts() {
    const app = await setup();
    assert.equal(app.get('#available-count').textContent, '1'); assert.equal(app.get('#total-count').textContent, '3');
    assert.equal(app.document.querySelectorAll('.shop-row').length, 1);
    assert.equal(app.get('[data-filter="available"]').classList.contains('is-active'), true);
    assert.equal(app.get('.join-button').disabled, false);
    await app.click('[data-filter="all"]');
    assert.equal(app.document.querySelectorAll('.shop-row').length, 3);
    assert.equal(app.document.querySelectorAll('.join-button').filter((button) => !button.disabled).length, 1);
    assert.match(app.get('#shop-list').textContent, /更新待ち/); assert.match(app.get('#shop-list').textContent, /約25分/);
    app.get('#shop-search').value = '海岸'; await app.get('#shop-search').emit('input');
    assert.equal(app.document.querySelectorAll('.shop-row').length, 1);
    app.state.catalog.available_count = 0; app.state.catalog.shops[0].can_join = false; await app.tick();
    assert.equal(app.get('#available-count').textContent, '0'); assert.equal(app.get('#total-count').textContent, '3');
  },
  async unknown_queue_blocks_join() {
    const app = await setup({failWaiting: true}); await app.click('[data-filter="all"]');
    assert.ok(app.document.querySelectorAll('.join-button').every((button) => button.disabled));
    assert.match(app.get('#current-queue').textContent, /順番待ちを確認できませんでした/);
    await app.click('.join-button'); assert.equal(app.get('#join-dialog').open, false);
    assert.equal(app.state.calls.filter((call) => call.url.startsWith('/api/shops/')).length, 0);
  },
  async catalog_preserves_queue() {
    const app = await setup(); await app.click('.join-button'); await app.submit('#join-form');
    assert.equal(app.document.querySelectorAll('.active-queue').length, 1);
    assert.match(app.get('#current-queue').textContent, /受付番号7/); assert.match(app.get('#current-queue').textContent, /公式目安/);
    app.state.failWaiting = true; await app.click('#refresh-button'); await app.tick();
    assert.equal(app.document.querySelectorAll('.active-queue').length, 1); assert.equal(app.get('.join-button').disabled, true);
    assert.equal(app.state.calls.filter((call) => call.url.endsWith('/refresh')).length, 1);
  },
  async stale_waiting_cannot_restore_cancelled_queue() {
    const app = await setup({active: [copy(waitingItem)]}), old = deferred(); app.state.pendingWaiting = old.promise;
    const ticking = app.tick(); await flush(); app.state.pendingWaiting = null;
    await app.click('#cancel-button'); assert.equal(app.get('#cancel-dialog').open, true);
    await app.submit('#cancel-form'); old.resolve(response([waitingItem])); await ticking;
    assert.equal(app.get('#current-queue').textContent, '現在の順番待ちはありません');
    assert.equal(app.get('.join-button').disabled, false); assert.equal(app.get('#cancel-dialog').open, false);
  },
  async settings_only_change_next_dialog() {
    const app = await setup(); await app.click('.join-button');
    assert.equal(app.get('#adult-count').textContent, '2'); assert.equal(app.get('#child-count').textContent, '0');
    await app.click('#settings-button'); assert.equal(app.get('#settings-dialog').open, true);
    assert.equal(app.get('#settings-adult-count').textContent, '2'); assert.equal(app.get('#settings-child-count').textContent, '0');
    await app.click('[data-preference-step="adult"][data-delta="1"]'); await app.click('[data-preference-step="child"][data-delta="1"]');
    await app.submit('#settings-form');
    assert.deepEqual(app.state.preferences, {default_adult_count: 3, default_child_count: 1});
    assert.equal(app.get('#settings-dialog').open, false);
    assert.equal(app.get('#adult-count').textContent, '2'); assert.equal(app.get('#child-count').textContent, '0');
    await app.get('#join-dialog').close(); await app.click('.join-button');
    assert.equal(app.get('#adult-count').textContent, '3'); assert.equal(app.get('#child-count').textContent, '1');
    await app.click('[data-step="adult"][data-delta="-1"]'); assert.equal(app.get('#adult-count').textContent, '2');
    assert.equal(app.state.preferences.default_adult_count, 3);
  },
  async live_detail_clamps_and_hides_child() {
    const app = await setup({preferences: {default_adult_count: 8, default_child_count: 3}});
    app.state.detail.forms = forms({min_adult: 1, max_adult: 3, max_child: 0, is_confirm_child: false}); await app.click('.join-button');
    assert.equal(app.get('#adult-count').textContent, '3'); assert.equal(app.get('#child-count').textContent, '0');
    assert.equal(app.get('#child-count').closest('.counter-row').hidden, true);
    await app.click('[data-step="adult"][data-delta="1"]'); assert.equal(app.get('#adult-count').textContent, '3');
    for (let count = 0; count < 4; count++) await app.click('[data-step="adult"][data-delta="-1"]');
    assert.equal(app.get('#adult-count').textContent, '1'); await app.submit('#join-form');
    const creation = app.state.calls.find((call) => call.method === 'POST' && call.url.endsWith('/waiting'));
    assert.deepEqual(JSON.parse(creation.body), {shop_id: 1, adult_count: 1, child_count: 0, answer1: 2, answer2: null, in_advance_information: ''});
    assert.ok(app.state.calls.some((call) => call.url === '/api/shops/1?merchant=sawayaka'));
  },
  async sole_enabled_confirmation_is_selected() {
    const app = await setup(); await app.click('.join-button'); const select = app.get('[data-answer="1"]');
    assert.equal(select.value, '2'); assert.match(select.textContent, /了承しました/); assert.doesNotMatch(select.textContent, /無効な選択肢/);
    assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, false); await app.submit('#join-form');
    assert.equal(JSON.parse(app.state.calls.find((call) => call.method === 'POST').body).answer1, 2);
  },
  async unsupported_confirmations_block_submit() {
    const app = await setup(); app.state.detail.forms.confirm_items = [confirmation(), confirmation(), confirmation()]; await app.click('.join-button');
    assert.equal(app.get('#join-error').textContent, '選択内容の確認が必要です');
    assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, true); await app.submit('#join-form');
    assert.equal(app.state.calls.filter((call) => call.method === 'POST').length, 0);
  },
  async representable_choices_require_selection() {
    const app = await setup(); app.state.detail.forms.confirm_items = [confirmation({sub_items: [
      {sub_item_index: 1, text: 'テーブル', enable: true, disabled: false}, {sub_item_index: 2, text: 'カウンター', enable: true, disabled: false}]})];
    await app.click('.join-button'); assert.equal(app.get('[data-answer="1"]').value, '');
    assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, true);
    app.get('[data-answer="1"]').value = '2'; await app.get('[data-answer="1"]').emit('change');
    assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, false); await app.submit('#join-form');
    assert.equal(JSON.parse(app.state.calls.find((call) => call.method === 'POST').body).answer1, 2);
  },
  async polling_and_transport_are_safe() {
    const app = await setup(); assert.ok(app.state.timers.length > 0); assert.ok(app.state.timers.every((timer) => timer.delay === 30000));
    const before = app.state.calls.length; app.document.hidden = true; await app.tick(); assert.equal(app.state.calls.length, before);
    app.document.hidden = false; await app.document.emit('visibilitychange'); await flush();
    for (const suffix of ['/console', '/waiting']) assert.ok(app.state.calls.filter((call) => call.url.endsWith(suffix)).length >= 2);
    assert.equal(app.state.calls.filter((call) => call.url.endsWith('/refresh')).length, 0); await app.click('#refresh-button');
    assert.equal(app.state.calls.filter((call) => call.url.endsWith('/refresh')).length, 1); assert.ok(app.state.calls.at(-1).url.endsWith('/console'));
    for (const call of app.state.calls) {
      assert.ok(call.headers['X-Timezone']); assert.equal(call.mode, 'same-origin'); assert.equal(call.credentials, 'same-origin'); assert.equal(call.headers.Origin, undefined);
    }
  },
  async upstream_text_and_errors_are_safe() {
    const app = await setup(); app.state.catalog.shops[0].sub_name = '<img src=x onerror=alert(1)>';
    app.state.catalog.shops[0].address = '<script>悪意のある文字列</script>'; app.state.catalog.shops[0].image_url = 'https://example.test/photo?quote=" onerror="';
    await app.tick(); assert.match(app.get('#shop-list').textContent, /<img src=x onerror=alert\(1\)>/);
    assert.equal(app.document.querySelectorAll('script').length, 1); assert.equal(app.get('#shop-list').querySelectorAll('img').length, 1);
    app.state.createError = {detail: '受付状況が変更されました'}; await app.click('.join-button'); await app.submit('#join-form');
    assert.equal(app.get('#join-error').textContent, '受付状況が変更されました'); assert.equal(app.get('#join-dialog').open, true);
    app.state.createError = {detail: 'Internal server error'}; await app.submit('#join-form');
    assert.doesNotMatch(app.get('#join-error').textContent, /Internal|Error|Failed/); assert.match(app.get('#join-error').textContent, /[ぁ-んァ-ヶ一-龠]/);
  },
  async detail_failure_never_submits_cached_form() {
    const app = await setup({failDetail: true}); await app.click('.join-button');
    assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, true); assert.match(app.get('#join-error').textContent, /[ぁ-んァ-ヶ一-龠]/);
    await app.submit('#join-form'); assert.equal(app.state.calls.filter((call) => call.method === 'POST').length, 0);
  },
  async stale_preferences_cannot_overwrite_saved_defaults() {
    const app = await setup(), old = deferred(); app.state.pendingPreferences = old.promise;
    const opening = app.click('.join-button'); await flush();
    await app.click('#settings-button');
    await app.click('[data-preference-step="adult"][data-delta="1"]');
    await app.click('[data-preference-step="child"][data-delta="1"]');
    await app.submit('#settings-form');
    old.resolve(response({default_adult_count: 2, default_child_count: 0})); await opening;
    assert.equal(app.get('#adult-count').textContent, '2');
    await app.get('#join-dialog').close(); await app.click('.join-button');
    assert.equal(app.get('#adult-count').textContent, '3');
    assert.equal(app.get('#child-count').textContent, '1');
  },
  async malformed_confirmations_are_not_silently_ignored() {
    const app = await setup();
    for (const items of [[null], [confirmation({title: null})], [confirmation({sub_items: [
      {sub_item_index: 1, text: null, enable: true, disabled: false}]})]]) {
      app.state.detail.forms.confirm_items = items; await app.click('.join-button');
      assert.equal(app.get('#join-error').textContent, '選択内容の確認が必要です');
      assert.equal(app.get('#join-form').querySelector('[type="submit"]').disabled, true);
      await app.submit('#join-form'); await app.get('#join-dialog').close();
    }
    assert.equal(app.state.calls.filter((call) => call.method === 'POST').length, 0);
  },
};

(async () => {
  const selected = process.argv[3]; assert.ok(cases[selected], `Unknown behavioral case: ${selected}`);
  await cases[selected](); console.log(`PASS ${selected}`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
