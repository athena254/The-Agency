import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const staticDir = new URL('../src/agency/prototype/static/', import.meta.url);
const html = readFileSync(new URL('pilot.html', staticDir), 'utf8');
await import(new URL('pilot.js', staticDir));
const ui = globalThis.__pilotUI;

// Minimal DOM fixture; real controller, real markup ids, deterministic HTTP boundary.
class Element {
  constructor() { this.children = []; this.listeners = {}; this.attributes = {}; this.value = ''; this.hidden = false; this.disabled = false; this._text = ''; }
  get textContent() { return this.children.length ? this.children.map(x => x.textContent).join(' ') : this._text; }
  set textContent(value) { this.children = []; this._text = String(value); }
  get firstChild() { return this.children[0] || null; }
  appendChild(child) { this.children.push(child); return child; }
  removeChild(child) { this.children.splice(this.children.indexOf(child), 1); }
  setAttribute(name, value) { this.attributes[name] = value; }
  addEventListener(name, listener) { (this.listeners[name] ||= []).push(listener); }
  focus() {}
  click() { for (const fn of this.listeners.click || []) fn({preventDefault() {}}); }
}
const spendingURL = '/api/pilot/spending';
const reply = (payload, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => payload});
const configuredSpending = () => ({
  enabled: true, currency: 'USD', limit_usd: '0.50', reserved_usd: '0.015',
  reported_cost_usd: '0.000003', prompt_tokens: 123, completion_tokens: 7,
  unresolved_calls: 1, pricing_source: 'Offline test rates — not a provider price',
});
function setup(spendingReply = () => reply(configuredSpending())) {
  const byId = new Map([...html.matchAll(/id="([^"]+)"/g)].map(x => [x[1], new Element()]));
  const document = {getElementById: id => byId.get(id) || null, createElement: () => new Element(), addEventListener() {}};
  const calls = [];
  const fetch = async (url, init) => {
    calls.push({url, init});
    if (url === spendingURL) return spendingReply();
    if (url === '/api/session') return reply({csrf_token: 'synthetic-csrf'});
    if (url === '/api/pilot/capabilities') return reply({
      enabled: true, ready: true, mode: 'single_owner_pilot', tools: ['calculator', 'utc_time', 'web_search'],
      disabled: ['shell', 'governance'], provider_ready: true, uncertainty_blocked: false,
      limits: {requests_per_hour: 12, requests_per_day: 50, model_calls_per_hour: 40, model_calls_per_day: 100, model_calls_per_request: 8, tool_calls_per_request: 4},
    });
    if (url === '/api/pilot/threads') return reply({threads: []});
    if (url === '/api/pilot/tasks?limit=50') return reply({tasks: []});
    throw new Error('Unexpected route');
  };
  const controller = ui.createController({document, fetch, pollIntervalMs: 0});
  return {byId, calls, controller};
}

test('unlock preserves existing reads; explicit spending refresh reads a separate authenticated snapshot and labels it honestly', async () => {
  const {byId, calls, controller} = setup();
  await controller.init();
  assert.equal(calls.some(x => x.url === spendingURL), false, 'locked UI must not fetch spending');
  assert.equal(await controller.unlock('synthetic-owner'), true);
  assert.equal(calls.some(x => x.url === spendingURL), false, 'unlock request sequence remains unchanged');
  assert.equal(byId.get('pilot-refresh-spending').disabled, false);
  assert.match(byId.get('pilot-spending-note').textContent, /not read/i);
  byId.get('pilot-refresh-spending').click();
  await controller.settled();
  const call = calls.find(x => x.url === spendingURL);
  assert.ok(call, 'unlock must read the separate spending endpoint');
  assert.equal(call.init.method, 'GET');
  assert.equal(call.init.headers.Authorization, 'Bearer synthetic-owner');
  assert.equal(call.init.credentials, 'same-origin');
  const details = byId.get('pilot-spending-details').textContent;
  for (const value of ['0.50', '0.015', '0.000003', '123', '7', '1', 'Offline test rates']) assert.ok(details.includes(value), value);
  assert.match(details, /Configured ceiling/);
  assert.match(details, /Reserved/);
  assert.match(details, /Provider-reported prompt tokens/);
  assert.match(byId.get('pilot-spending-note').textContent, /not.*invoice/i);
  assert.match(byId.get('pilot-spending-note').textContent, /refresh/i);
  controller.lock();
  assert.equal(byId.get('pilot-spending-details').textContent, '');
  assert.doesNotMatch(byId.get('pilot-spending-note').textContent, /0\.50|Offline test rates/);
  assert.equal(byId.get('pilot-refresh-spending').disabled, true);
});

test('malformed spending fails closed without guessing values or changing strict task/capabilities contracts', async () => {
  const malformed = [
    {...configuredSpending(), extra: true}, {}, [], null,
    {...configuredSpending(), enabled: 'true'}, {...configuredSpending(), currency: 'EUR'},
    {...configuredSpending(), limit_usd: 0.5}, {...configuredSpending(), limit_usd: null},
    {...configuredSpending(), reserved_usd: '-1'}, {...configuredSpending(), reserved_usd: 'NaN'},
    {...configuredSpending(), reported_cost_usd: '1e-6'}, {...configuredSpending(), reported_cost_usd: false},
    {...configuredSpending(), prompt_tokens: true}, {...configuredSpending(), prompt_tokens: 1.1},
    {...configuredSpending(), prompt_tokens: Number.MAX_SAFE_INTEGER + 1},
    {...configuredSpending(), completion_tokens: -1}, {...configuredSpending(), unresolved_calls: null},
    {...configuredSpending(), pricing_source: ''}, {...configuredSpending(), pricing_source: '  '},
    {...configuredSpending(), enabled: false},
  ];
  for (const payload of malformed) {
    const {byId, controller} = setup(() => reply(payload));
    assert.equal(await controller.unlock('synthetic-owner'), true, 'spending must not extend capabilities schema');
    await controller.refreshSpending();
    assert.equal(byId.get('pilot-spending-details').textContent, '', JSON.stringify(payload));
    assert.match(byId.get('pilot-spending-note').textContent, /unavailable/i);
    assert.match(byId.get('pilot-spending-note').textContent, /no monetary limit or zero cost can be inferred/i);
  }
});

test('configured zero ceiling and exact tiny decimals remain literal strings and pricing text is not HTML', async () => {
  const {byId, controller} = setup(() => reply({...configuredSpending(), limit_usd: '0',
    reported_cost_usd: '0.0000000000000000000000001', pricing_source: '<img src=x onerror=alert(1)>'}));
  await controller.unlock('synthetic-owner');
  await controller.refreshSpending();
  const details = byId.get('pilot-spending-details');
  assert.match(details.textContent, /Configured ceiling \(USD\) 0/);
  assert.match(details.textContent, /0\.0000000000000000000000001/);
  assert.match(details.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(details.children.at(-1).children.at(-1).children.length, 0);
});

test('newer spending read wins and lock prevents late reads from repainting private data', async () => {
  let next = () => reply(configuredSpending());
  const {byId, controller} = setup(() => next());
  await controller.unlock('synthetic-owner');
  await controller.refreshSpending();
  let release;
  next = () => new Promise(resolve => {release = resolve;});
  const older = controller.refreshSpending();
  next = () => reply({...configuredSpending(), reserved_usd: '0.04'});
  await controller.refreshSpending();
  release(reply({...configuredSpending(), reserved_usd: '0.03'}));
  await older;
  assert.match(byId.get('pilot-spending-details').textContent, /0\.04/);
  assert.doesNotMatch(byId.get('pilot-spending-details').textContent, /0\.03/);
  next = () => new Promise(resolve => {release = resolve;});
  const late = controller.refreshSpending();
  controller.lock();
  const lockedNote = byId.get('pilot-spending-note').textContent;
  release(reply(configuredSpending()));
  await late;
  assert.equal(byId.get('pilot-spending-details').textContent, '');
  assert.equal(byId.get('pilot-spending-note').textContent, lockedNote);
});

test('manual refresh is GET-only, failure clears stale amounts and no request is retried', async () => {
  let next = () => reply(configuredSpending());
  const {byId, calls, controller} = setup(() => next());
  await controller.unlock('synthetic-owner');
  await controller.refreshSpending();
  for (const failure of [() => reply({}, 503), () => {throw new Error('offline');}, () => ({ok:true, status:200, json:async()=>{throw new Error('invalid JSON');}})]) {
    next = failure;
    const before = calls.length;
    byId.get('pilot-refresh-spending').click();
    await controller.settled();
    assert.equal(calls.length, before + 1);
    assert.equal(calls.at(-1).url, spendingURL);
    assert.equal(calls.at(-1).init.method, 'GET');
    assert.equal(byId.get('pilot-spending-details').textContent, '');
    assert.match(byId.get('pilot-spending-note').textContent, /unavailable/);
    assert.equal(controller.state().locked, false);
  }
  next = () => reply({}, 401);
  await controller.refreshSpending();
  assert.equal(controller.state().locked, true);
  assert.equal(controller.state().hasBearer, false);
  assert.equal(byId.get('pilot-spending-details').textContent, '');
});

test('disabled accounting is not displayed as a zero-dollar ceiling or zero-cost promise', async () => {
  const {byId, controller} = setup(() => reply({...configuredSpending(), enabled: false, limit_usd: null, pricing_source: null,
    reserved_usd: '0', reported_cost_usd: '0', prompt_tokens: 0, completion_tokens: 0, unresolved_calls: 0}));
  await controller.unlock('synthetic-owner');
  await controller.refreshSpending();
  assert.match(byId.get('pilot-spending-note').textContent, /accounting.*disabled/i);
  assert.match(byId.get('pilot-spending-note').textContent, /no configured monetary ceiling/i);
  assert.equal(byId.get('pilot-spending-details').textContent, '');
  assert.doesNotMatch(byId.get('pilot-spending-note').textContent, /free|\$0|unlimited/i);
});
