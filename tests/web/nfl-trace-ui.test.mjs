import { after, test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import inspector from 'node:inspector';

// Dependency-free functional DOM contract tests, NOT browser/layout acceptance.
const ROOT = fileURLToPath(new URL('../../', import.meta.url));
const TRACE_PATH = ROOT + 'public/nfl-trace.js';
const TRACE = readFileSync(TRACE_PATH, 'utf8');
const APP = readFileSync(ROOT + 'public/app.js', 'utf8');
const HTML = readFileSync(ROOT + 'public/index.html', 'utf8');
const CSS = readFileSync(ROOT + 'public/style.css', 'utf8');
const sourceHash = createHash('sha256').update(TRACE).digest('hex');
const profile = new inspector.Session();
profile.connect();
const post = (method, params = {}) => new Promise((resolve, reject) => {
  profile.post(method, params, (error, value) => error ? reject(error) : resolve(value));
});
await post('Profiler.enable');
await post('Profiler.startPreciseCoverage', { callCount: true, detailed: true });

class Element {
  constructor(tag, owner) {
    this.tagName = tag.toUpperCase(); this.owner = owner; this.children = [];
    this._text = ''; this.value = ''; this.disabled = false; this.hidden = false;
    this.dataset = {}; this.listeners = new Map(); this.className = ''; this.resetCount = 0;
    this.classList = { toggle() {} };
  }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; }
  set innerHTML(_) { throw new Error('HTML interpolation is forbidden in these functional tests'); }
  get innerHTML() { throw new Error('HTML parsing is not implemented by the fake DOM'); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ''; this.children = [...nodes]; }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
  async emit(type, extra = {}) {
    const event = { type, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; }, ...extra };
    await Promise.all((this.listeners.get(type) || []).map(listener => listener(event)));
    return event;
  }
  reset() {
    this.resetCount++;
    const fields = { 'trace-form': ['trace-query'], 'access-form': ['access-key'],
      'record-form': ['record-json'], 'upload-form': ['upload-file'] };
    for (const id of fields[this.id] || []) this.owner.getElementById(id).value = '';
  }
}

function fakeDocument() {
  const nodes = [], ids = new Map();
  const document = {
    createElement(tag) { const element = new Element(tag, document); nodes.push(element); return element; },
    getElementById(id) { assert.ok(ids.has(id), `Real HTML must define ${id}`); return ids.get(id); },
    querySelectorAll(selector) {
      assert.equal(selector, '[data-private]');
      return nodes.filter(node => Object.hasOwn(node.dataset, 'private'));
    },
    nodes,
  };
  // Only materialize attributes required by the tested contracts; no HTML engine.
  for (const match of HTML.matchAll(/<([a-z][a-z0-9]*)\b([^>]*)>/gi)) {
    const [, tag, attrs] = match, id = /\bid="([^"]+)"/.exec(attrs)?.[1];
    if (!id && !/\bdata-private\b/.test(attrs)) continue;
    const node = document.createElement(tag);
    if (id) { node.id = id; ids.set(id, node); }
    if (/\bdata-private\b/.test(attrs)) node.dataset.private = '';
    node.disabled = /\bdisabled\b/.test(attrs); node.hidden = /\bhidden\b/.test(attrs);
  }
  return document;
}

function deferred() {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function setup() {
  const document = fakeDocument(), calls = [];
  const state = { epoch: 1, unlocked: true };
  const request = (...args) => { const wait = deferred(); calls.push({ args, ...wait }); return wait.promise; };
  const sandbox = { document };
  for (const name of ['localStorage', 'sessionStorage', 'indexedDB', 'fetch']) {
    Object.defineProperty(sandbox, name, { get() { throw new Error(`Forbidden component access: ${name}`); } });
  }
  const context = vm.createContext(sandbox);
  new vm.Script(TRACE, { filename: TRACE_PATH }).runInContext(context);
  const ui = context.createNFLTraceUI({ document, request, epoch: () => state.epoch, unlocked: () => state.unlocked });
  const $ = id => document.getElementById(id);
  function lock() {
    state.unlocked = false; state.epoch++;
    document.querySelectorAll('[data-private]').forEach(node => { node.disabled = true; });
    ui.reset();
  }
  return { document, $, calls, state, ui, lock };
}

const ID = 'sha256:' + 'a'.repeat(64);
function record(overrides = {}) {
  return { receipt_id: ID, event_id: 'nfl:2026:401873003', market: 'rb_rush_yds', line: 59.5,
    outcome_state: 'pending', ...overrides };
}
function catalog(records = [record()], overrides = {}) {
  return { version: 1, kind: 'nfl.forecast_catalog.v1', read_only: true, total_forecasts: records.length,
    matched_forecasts: records.length, watermark: '23', records, ...overrides };
}
function trace(overrides = {}) {
  return { version: 1, kind: 'nfl.forecast_trace.v1', read_only: true, watermark: '23',
    forecast: record(), outcome_revisions: [], source_freshness: 'unverified',
    model_adoption: 'unverified', ...overrides };
}
const inspectButton = h => h.$('trace-list').children[0].children.find(node => node.tagName === 'BUTTON');
async function loaded(h, value = catalog()) {
  const work = h.ui.load(); h.calls.at(-1).resolve(value); await work;
}
async function flush() { for (let n = 0; n < 8; n++) await Promise.resolve(); }

test('catalog and original pending/VOID trace are read-only text, with honest source/model/publication limits', async () => {
  const h = setup(); h.$('trace-query').value = '  contract:one /?&  ';
  const work = h.ui.load(); assert.equal(h.$('trace-search').disabled, true);
  assert.deepEqual(h.calls[0].args, ['/api/v1/nfl/forecasts?query=contract%3Aone%20%2F%3F%26&limit=25']);
  h.calls[0].resolve(catalog([record(), record({ receipt_id: 'void-id', outcome_state: 'void' })])); await work;
  assert.equal(h.$('trace-search').disabled, false); assert.equal(h.$('trace-list').children.length, 2);
  assert.match(h.$('trace-list').textContent, /pending/); assert.match(h.$('trace-list').textContent, /void/);
  assert.match(h.$('trace-state').textContent, /2 forecasts; 2 matching; watermark 23/);
  assert.match(h.$('trace-state').textContent, /Production serving and learning are not verified/);
  const open = inspectButton(h); assert.equal(open.type, 'button'); assert.ok(Object.hasOwn(open.dataset, 'private'));
  const pending = open.emit('click'); assert.deepEqual(h.calls[1].args, ['/api/v1/nfl/forecast-trace/' + encodeURIComponent(ID)]);
  h.calls[1].resolve(trace()); await pending;
  assert.match(h.$('trace-detail').textContent, /Pending means no final in this snapshot/);
  assert.match(h.$('trace-detail').textContent, /Public availability, source freshness and model adoption remain unverified/);
  assert.match(h.$('trace-detail').textContent, /"outcome_state": "pending"/);
  const second = h.$('trace-list').children[1].children.find(node => node.tagName === 'BUTTON').emit('click');
  h.calls[2].resolve(trace({ forecast: record({ outcome_state: 'void' }), outcome_revisions: [{ status: 'VOID', hit: null }] })); await second;
  assert.match(h.$('trace-detail').textContent, /"status": "VOID"/);
  assert.match(h.$('trace-detail').textContent, /"hit": null/);
  assert.ok(h.calls.every(call => call.args.length === 1)); // No write options or credentials in this module.
});

test('empty, failed and unsupported catalogs fail honestly and never retain partial/private rows', async () => {
  const h = setup(); await loaded(h, catalog([]));
  assert.match(h.$('trace-list').textContent, /No matching internal forecast receipts/);
  const unversioned = catalog([]); delete unversioned.version;
  for (const bad of [null, {}, catalog([], { kind: 'other' }), catalog([], { read_only: false }),
    catalog([], { records: {} }), catalog([record(), null]), catalog([], { version: 2 }),
    catalog([], { version: true }), unversioned]) {
    await loaded(h); const work = h.ui.load(); h.calls.at(-1).resolve(bad); await work;
    assert.equal(h.$('trace-list').children.length, 0);
    assert.match(h.$('trace-state').textContent, /unavailable or unsupported/);
    assert.equal(h.$('trace-search').disabled, false);
  }
  const work = h.ui.load(); h.calls.at(-1).reject(new Error('<secret backend error>')); await work;
  assert.match(h.$('trace-state').textContent, /no freshness or accuracy is inferred/);
  assert.doesNotMatch(h.$('trace-state').textContent, /secret backend/);
});

test('unsupported/missing detail and backend errors clear prior evidence without inferring a final', async () => {
  const h = setup(); await loaded(h);
  const unversioned = trace(); delete unversioned.version;
  for (const bad of [null, {}, trace({ kind: 'nfl.forecast_trace.v2' }), trace({ read_only: false }),
    trace({ version: 2 }), trace({ version: true }), unversioned]) {
    const work = inspectButton(h).emit('click'); h.calls.at(-1).resolve(bad); await work;
    assert.match(h.$('trace-detail').textContent, /Trace unavailable/);
    assert.match(h.$('trace-detail').textContent, /no final outcome or active model is inferred/);
  }
  const work = inspectButton(h).emit('click'); h.calls.at(-1).reject(new Error('private error')); await work;
  assert.doesNotMatch(h.$('trace-detail').textContent, /private error/);
});

test('untrusted catalog fields and JSON detail use textContent, never HTML or executable links', async () => {
  const h = setup(), attack = '<img src=x onerror="globalThis.pwned=true"><script>alert(1)</script>';
  await loaded(h, catalog([record({ receipt_id: attack, market: attack, event_id: attack })], { watermark: attack }));
  assert.ok(h.$('trace-list').textContent.includes(attack)); assert.ok(h.$('trace-state').textContent.includes(attack));
  const work = inspectButton(h).emit('click');
  assert.equal(h.calls.at(-1).args[0], '/api/v1/nfl/forecast-trace/' + encodeURIComponent(attack));
  h.calls.at(-1).resolve(trace({ injected: attack })); await work;
  const pre = h.$('trace-detail').children.find(node => node.tagName === 'PRE');
  assert.equal(JSON.parse(pre.textContent).injected, attack);
  assert.ok(h.document.nodes.every(node => !['SCRIPT', 'IMG', 'A', 'IFRAME'].includes(node.tagName)));
});

test('newer searches win; old success/error/finally cannot replace results or enable an active newer request', async () => {
  const h = setup(); h.$('trace-query').value = 'old'; const old = h.ui.load();
  h.$('trace-query').value = 'new'; const newer = h.ui.load();
  h.calls[0].resolve(catalog([record({ event_id: 'OLD-PRIVATE-EVENT' })])); await old;
  assert.equal(h.$('trace-search').disabled, true); assert.doesNotMatch(h.$('trace-list').textContent, /OLD/);
  h.calls[1].resolve(catalog([record({ event_id: 'NEW-EVENT' })])); await newer;
  assert.match(h.$('trace-list').textContent, /NEW-EVENT/);
  const stale = h.ui.load(), current = h.ui.load();
  h.calls.at(-1).resolve(catalog([record({ event_id: 'CURRENT' })])); await current;
  h.calls.at(-2).reject(new Error('stale failure')); await stale;
  assert.match(h.$('trace-list').textContent, /CURRENT/); assert.doesNotMatch(h.$('trace-state').textContent, /unavailable/);
});

test('new search/new inspection invalidates old details; stale detail errors cannot overwrite the latest evidence', async () => {
  const h = setup(); await loaded(h);
  const old = inspectButton(h).emit('click'); const oldCall = h.calls.at(-1);
  const newerSearch = h.ui.load(); oldCall.resolve(trace({ marker: 'OLD-DETAIL' })); await old;
  assert.doesNotMatch(h.$('trace-detail').textContent, /OLD-DETAIL/);
  h.calls.at(-1).resolve(catalog()); await newerSearch;
  const first = inspectButton(h).emit('click'); const firstCall = h.calls.at(-1);
  const second = inspectButton(h).emit('click'); h.calls.at(-1).resolve(trace({ marker: 'LATEST-DETAIL' })); await second;
  firstCall.reject(new Error('old detail failure')); await first;
  assert.match(h.$('trace-detail').textContent, /LATEST-DETAIL/);
});

test('lock/reset clears private query/results and ignores pending search/detail responses across auth epochs', async () => {
  const h = setup(); await loaded(h); h.$('trace-query').value = 'private-contract';
  const detail = inspectButton(h).emit('click'); const detailCall = h.calls.at(-1);
  const search = h.ui.load(); const searchCall = h.calls.at(-1); h.lock();
  assert.equal(h.$('trace-query').value, ''); assert.equal(h.$('trace-list').textContent, '');
  assert.match(h.$('trace-state').textContent, /^Locked/); assert.match(h.$('trace-detail').textContent, /after unlocking/);
  detailCall.resolve(trace({ marker: 'OLD-AUTH-DETAIL' })); searchCall.resolve(catalog([record({ event_id: 'OLD-AUTH-EVENT' })]));
  await Promise.all([detail, search]);
  assert.equal(h.$('trace-search').disabled, true); assert.equal(h.$('trace-query').disabled, true);
  assert.doesNotMatch(h.$('trace-detail').textContent + h.$('trace-list').textContent, /OLD-AUTH/);
  h.state.unlocked = true; h.state.epoch++; const changedAuth = h.ui.load(); const call = h.calls.at(-1);
  h.state.epoch++; h.$('trace-state').textContent = 'NEW-AUTH-STATE'; call.reject(new Error('old auth')); await changedAuth;
  assert.equal(h.$('trace-state').textContent, 'NEW-AUTH-STATE'); assert.equal(h.$('trace-search').disabled, true);
});

test('auth epoch alone fences successful catalog/detail and failed details without relying on reset/run counters', async () => {
  const h = setup(); const search = h.ui.load(); h.state.epoch++;
  h.$('trace-state').textContent = 'NEW-EPOCH-CATALOG';
  h.calls.at(-1).resolve(catalog([record({ event_id: 'OLD-EPOCH-EVENT' })])); await search;
  assert.equal(h.$('trace-state').textContent, 'NEW-EPOCH-CATALOG');
  assert.equal(h.$('trace-list').textContent, ''); assert.equal(h.$('trace-search').disabled, true);
  await loaded(h);
  const detail = inspectButton(h).emit('click'); h.state.epoch++;
  h.$('trace-detail').textContent = 'NEW-EPOCH-DETAIL';
  h.calls.at(-1).resolve(trace({ marker: 'OLD-EPOCH-DETAIL' })); await detail;
  assert.equal(h.$('trace-detail').textContent, 'NEW-EPOCH-DETAIL');
  const failed = inspectButton(h).emit('click'); h.state.epoch++;
  h.$('trace-detail').textContent = 'NEW-EPOCH-AFTER-ERROR';
  h.calls.at(-1).reject(new Error('old epoch failure')); await failed;
  assert.equal(h.$('trace-detail').textContent, 'NEW-EPOCH-AFTER-ERROR');
});

test('form submission prevents navigation; reset alone invalidates old calls and current completion respects locked state', async () => {
  const h = setup(); const event = await h.$('trace-form').emit('submit');
  assert.equal(event.defaultPrevented, true); assert.equal(h.calls.length, 1);
  h.ui.reset(); h.calls[0].resolve(catalog([record({ event_id: 'RESET-STALE' })])); await flush();
  assert.equal(h.$('trace-list').textContent, ''); assert.match(h.$('trace-state').textContent, /^Locked/);
  const work = h.ui.load(); h.state.unlocked = false; h.calls.at(-1).resolve(catalog([])); await work;
  assert.equal(h.$('trace-search').disabled, true);
});

function applicationHarness() {
  const document = fakeDocument(), fetches = [], windowEvents = new Map();
  const window = { addEventListener(type, handler) { windowEvents.set(type, handler); } };
  const sandbox = { document, window, location: { origin: 'https://control.example.invalid' },
    AbortSignal: { timeout(ms) { assert.equal(ms, 45000); return { timeout: ms }; } },
    fetch(path, options) { const wait = deferred(); fetches.push({ path, options, ...wait }); return wait.promise; } };
  for (const name of ['localStorage', 'sessionStorage', 'indexedDB']) {
    Object.defineProperty(sandbox, name, { get() { throw new Error(`No credential storage: ${name}`); } });
  }
  const context = vm.createContext(sandbox);
  new vm.Script(TRACE, { filename: TRACE_PATH }).runInContext(context);
  new vm.Script(APP, { filename: ROOT + 'public/app.js' }).runInContext(context);
  const $ = id => document.getElementById(id);
  const response = (value, status = 200) => ({ ok: status >= 200 && status < 300, status, async json() { return value; } });
  const answer = (call, value, status = 200) => call.resolve(response(value, status));
  return { document, $, fetches, windowEvents, answer };
}

test('actual app unlocks NFL trace despite legacy upload outage; owner key stays in headers/memory and lock clears trace', async () => {
  const h = applicationHarness(), key = 'synthetic-owner-fixture-' + 'x'.repeat(40);
  h.$('access-key').value = key; const unlock = h.$('access-form').emit('submit');
  assert.equal(h.$('access-key').value, '');
  h.answer(h.fetches[0], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[1], { records: [] }); h.answer(h.fetches[2], { detail: 'bucket unavailable' }, 503);
  await unlock; assert.equal(h.$('access-form').hidden, true); assert.equal(h.$('connected').hidden, false);
  assert.match(h.$('uploads').textContent, /Private uploads unavailable/);
  assert.equal(h.fetches[3].path, '/api/v1/nfl/forecasts?query=&limit=25');
  h.answer(h.fetches[3], catalog()); await flush(); assert.match(h.$('trace-list').textContent, /rb_rush_yds/);
  const pending = inspectButton(h).emit('click'); const old = h.fetches.at(-1);
  h.$('trace-query').value = 'PRIVATE-SEARCH'; await h.$('lock').emit('click');
  h.answer(old, trace({ marker: 'DO-NOT-RESTORE' })); await pending;
  assert.equal(h.$('trace-query').value, ''); assert.equal(h.$('trace-list').textContent, '');
  assert.match(h.$('trace-detail').textContent, /after unlocking/);
  assert.equal(h.$('trace-search').disabled, true); assert.equal(h.$('trace-query').disabled, true);
  assert.ok(h.fetches.every(call => !call.path.includes(key) && call.options.headers.Authorization === 'Bearer ' + key));
  assert.ok(h.fetches.every(call => call.options.credentials === 'omit' && call.options.cache === 'no-store' && !call.options.method));
  assert.doesNotMatch(h.document.nodes.map(node => node.textContent).join(''), /synthetic-owner-fixture/);
});

test('actual app pagehide locks pending catalog and refresh launches a fresh bounded trace search', async () => {
  const h = applicationHarness(); h.$('access-key').value = 'synthetic-' + 'z'.repeat(40);
  const unlock = h.$('access-form').emit('submit');
  h.answer(h.fetches[0], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[1], { records: [] }); h.answer(h.fetches[2], { assets: [] }); await unlock;
  h.answer(h.fetches[3], catalog([])); await flush();
  h.$('trace-query').value = 'refresh-this'; const refresh = h.$('refresh').emit('click');
  h.answer(h.fetches[4], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[5], { records: [] }); h.answer(h.fetches[6], { assets: [] }); await refresh;
  const pending = h.fetches[7]; assert.equal(pending.path, '/api/v1/nfl/forecasts?query=refresh-this&limit=25');
  h.windowEvents.get('pagehide')(); h.answer(pending, catalog([record({ event_id: 'STALE-PAGEHIDE' })])); await flush();
  assert.equal(h.$('trace-query').value, ''); assert.equal(h.$('trace-list').textContent, '');
  assert.match(h.$('trace-state').textContent, /^Locked/); assert.equal(h.$('trace-search').disabled, true);
});

test('actual app stale unlock rejection and finally cannot lock or re-enable a newer pending session', async () => {
  const h = applicationHarness(); h.$('access-key').value = 'synthetic-old-owner';
  const old = h.$('access-form').emit('submit');
  h.answer(h.fetches[0], { detail: 'OLD-A-ERROR' }, 503);
  h.answer(h.fetches[1], { records: [] }); h.answer(h.fetches[2], { assets: [] });
  // A has rejected refresh after its inner epoch check, but its outer catch has
  // not run. Exercise the real async continuation boundary, not mocked APP code.
  for (let n = 0; n < 8; n++) await Promise.resolve();
  h.$('access-key').value = 'synthetic-new-owner';
  const current = h.$('access-form').emit('submit');
  await old;
  assert.equal(h.$('unlock').disabled, true, 'Stale finally must not enable B while B is checking');
  assert.equal(h.$('notice').textContent, 'Checking private workspace…');
  h.answer(h.fetches[3], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[4], { records: [] }); h.answer(h.fetches[5], { assets: [] });
  await current;
  assert.equal(h.$('connected').hidden, false); assert.equal(h.$('access-form').hidden, true);
  assert.equal(h.$('unlock').disabled, false);
  assert.doesNotMatch(h.$('notice').textContent, /OLD-A-ERROR|locked/);
  assert.equal(h.fetches[6].options.headers.Authorization, 'Bearer synthetic-new-owner');
  h.answer(h.fetches[6], catalog([])); await flush();
});

test('actual app lock between successful refresh and outer unlock continuation remains locked', async () => {
  const h = applicationHarness(); h.$('access-key').value = 'synthetic-owner';
  const work = h.$('access-form').emit('submit');
  h.answer(h.fetches[0], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[1], { records: [] }); h.answer(h.fetches[2], { assets: [] });
  for (let n = 0; n < 7; n++) await Promise.resolve();
  await h.$('lock').emit('click'); await work;
  assert.equal(h.$('connected').hidden, true); assert.equal(h.$('access-form').hidden, false);
  assert.equal(h.$('unlock').disabled, false);
  assert.ok(h.document.querySelectorAll('[data-private]').every(node => node.disabled));
  assert.match(h.$('notice').textContent, /^Workspace locked/);
  assert.equal(h.fetches.length, 3, 'Stale success must not launch an authenticated trace search');
});

test('actual app old 401 cannot lock or replace a newer successful owner session', async () => {
  const h = applicationHarness(); h.$('access-key').value = 'synthetic-old-owner';
  const old = h.$('access-form').emit('submit');
  h.$('access-key').value = 'synthetic-new-owner'; const current = h.$('access-form').emit('submit');
  h.answer(h.fetches[3], { store: { predictions: 0, outcomes: 0, models: 0 } });
  h.answer(h.fetches[4], { records: [] }); h.answer(h.fetches[5], { assets: [] }); await current;
  h.answer(h.fetches[6], catalog([record({ event_id: 'NEW-OWNER-FORECAST' })])); await flush();
  h.answer(h.fetches[0], { detail: 'OLD-401-ERROR' }, 401);
  h.answer(h.fetches[1], { records: [] }); h.answer(h.fetches[2], { assets: [] }); await old;
  assert.equal(h.$('connected').hidden, false); assert.equal(h.$('access-form').hidden, true);
  assert.match(h.$('trace-list').textContent, /NEW-OWNER-FORECAST/);
  assert.doesNotMatch(h.$('notice').textContent, /OLD-401-ERROR|locked/);
  assert.equal(h.fetches[0].options.headers.Authorization, 'Bearer synthetic-old-owner');
  assert.equal(h.fetches[6].options.headers.Authorization, 'Bearer synthetic-new-owner');
});

test('real HTML uses CSP-compatible deferred external scripts, accessible locked controls and bounded responsive CSS (not layout acceptance)', () => {
  const scripts = [...HTML.matchAll(/<script\b([^>]*)>([\s\S]*?)<\/script>/gi)];
  assert.deepEqual(scripts.map(match => /\bsrc="([^"]+)"/.exec(match[1])?.[1]), ['/nfl-trace.js', '/app.js']);
  assert.ok(scripts.every(match => /\bdefer\b/.test(match[1]) && !match[2].trim()));
  assert.doesNotMatch(HTML, /\son[a-z]+\s*=|javascript:/i);
  assert.match(HTML, /<meta name="viewport" content="width=device-width, initial-scale=1">/);
  assert.match(HTML, /<input id="trace-query"[^>]*maxlength="128"[^>]*disabled data-private/);
  assert.match(HTML, /<button id="trace-search"[^>]*disabled data-private/);
  assert.match(HTML, /<p id="trace-state"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(HTML, /<div id="trace-detail"[^>]*aria-live="polite"/);
  assert.match(HTML, /Pending means no accepted final in the pinned journal snapshot/);
  assert.match(CSS, /\.trace-detail pre\{[^}]*white-space:pre-wrap[^}]*max-height:480px[^}]*overflow:auto/);
  assert.match(CSS, /#trace-list \.record\{[^}]*min-width:0/);
  assert.match(CSS, /@media\(max-width:760px\)/); assert.match(CSS, /@media\(prefers-reduced-motion:reduce\)/);
  const config = JSON.parse(readFileSync(ROOT + 'vercel.json', 'utf8'));
  const csp = config.headers.flatMap(row => row.headers).find(header => header.key === 'Content-Security-Policy').value;
  assert.match(csp, /script-src 'self'/); assert.match(csp, /style-src 'self'/);
  assert.doesNotMatch(csp, /unsafe-inline|unsafe-eval/);
  assert.doesNotMatch(TRACE, /localStorage|sessionStorage|indexedDB|document\.cookie|Authorization|Bearer|ownerKey/i);
  assert.doesNotMatch(TRACE, /method\s*:\s*['"](?:POST|PUT|PATCH|DELETE)['"]|\/api\/(?:approve|promote)/i);
  assert.doesNotMatch(TRACE, /innerHTML|insertAdjacentHTML|\beval\(|new Function\(/);
});

after(async () => {
  const result = await post('Profiler.takePreciseCoverage');
  await post('Profiler.stopPreciseCoverage'); profile.disconnect();
  const ranges = new Map(), functions = new Map();
  for (const script of result.result.filter(row => row.url === TRACE_PATH || row.url === 'file://' + TRACE_PATH)) {
    for (const fn of script.functions) {
      const first = fn.ranges[0], key = `${fn.functionName}:${first.startOffset}:${first.endOffset}`;
      functions.set(key, (functions.get(key) || false) || first.count > 0);
      for (const range of fn.ranges) {
        const rk = `${range.startOffset}:${range.endOffset}`;
        ranges.set(rk, (ranges.get(rk) || false) || range.count > 0);
      }
    }
  }
  assert.equal(createHash('sha256').update(readFileSync(TRACE_PATH)).digest('hex'), sourceHash, 'Trace source changed during measurement');
  assert.ok(functions.size > 0 && ranges.size > 0, 'V8 must report actual trace-module execution');
  const hit = [...ranges.values()].filter(Boolean).length;
  console.log(`NFL trace UI source sha256: ${sourceHash}`);
  console.log(`NFL trace UI V8 functions: ${[...functions.values()].filter(Boolean).length}/${functions.size}; block ranges: ${hit}/${ranges.size} (${(100 * hit / ranges.size).toFixed(4)}%); no source exclusions`);
  assert.ok(hit / ranges.size >= .90, 'Actual trace-module V8 block coverage must be at least90%');
});
