/**
 * Browser-JS tests for the local Remex prototype UI (no external deps).
 *
 * Covers the brief's verification list:
 *  - node --check on app.js
 *  - static asset references, no external resources/scripts/inline handlers
 *  - no innerHTML for user/model text, no storage of private messages
 *  - behaviour with a tiny DOM fake: session-first CSRF, busy/failed states,
 *    honest HTTP/network errors, Enter/Shift+Enter, safe text rendering.
 *
 * Run: node --test tests/prototype_ui.test.mjs
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(here, '..');
const staticDir = path.join(rootDir, 'src', 'agency', 'prototype', 'static');
const htmlPath = path.join(staticDir, 'index.html');
const cssPath = path.join(staticDir, 'style.css');
const jsPath = path.join(staticDir, 'app.js');

const html = readFileSync(htmlPath, 'utf8');
const css = readFileSync(cssPath, 'utf8');
const js = readFileSync(jsPath, 'utf8');

// Load the production script (CommonJS in Node; document is undefined there,
// so it exports its test seam instead of booting).
await import(pathToFileURL(jsPath).href);
const ui = globalThis.__remexUI;

/* ------------------------- tiny DOM fake ------------------------- */

class FakeElement {
  constructor(tagName) {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.listeners = new Map();
    this.attributes = {};
    this.className = '';
    this.hidden = false;
    this.disabled = false;
    this.value = '';
    this.style = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.focused = false;
    this._text = '';
  }

  get textContent() {
    if (this.children.length > 0) {
      return this.children.map((child) => child.textContent).join('');
    }
    return this._text;
  }

  set textContent(value) {
    this.children = [];
    this._text = String(value);
  }

  get firstChild() {
    return this.children[0] || null;
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  removeChild(child) {
    const index = this.children.indexOf(child);
    if (index >= 0) this.children.splice(index, 1);
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }

  getAttribute(name) {
    return name in this.attributes ? this.attributes[name] : null;
  }

  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(fn);
  }

  removeEventListener(type, fn) {
    const fns = this.listeners.get(type) || [];
    const index = fns.indexOf(fn);
    if (index >= 0) fns.splice(index, 1);
  }

  dispatch(type, event = {}) {
    for (const fn of this.listeners.get(type) || []) fn(event);
  }

  focus() {
    this.focused = true;
  }
}

/** Build a fake document whose ids come from the real index.html markup. */
function buildDoc() {
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  const byId = new Map(ids.map((id) => [id, new FakeElement('div')]));
  byId.get('message-input').tagName = 'TEXTAREA';
  byId.get('send-button').tagName = 'BUTTON';
  byId.get('notice').hidden = true; // mirrors the hidden attribute in markup
  const created = [];
  const document = {
    readyState: 'complete',
    hidden: false,
    getElementById(id) {
      return byId.get(id) || null;
    },
    createElement(tag) {
      const el = new FakeElement(tag);
      created.push(el);
      return el;
    },
    addEventListener() {},
  };
  return { document, byId, created };
}

function jsonResponse(data, { ok = true, status = 200, statusText = '' } = {}) {
  return { ok, status, statusText, json: async () => data };
}

function makeFetch(routes) {
  const calls = [];
  const fetchFn = async (url, init = {}) => {
    calls.push({ url, init });
    const route = routes[url];
    if (!route) throw new Error('Unexpected fetch: ' + url);
    return typeof route === 'function' ? route(init) : route;
  };
  return { fetchFn, calls };
}

function setup(routes) {
  const { document, byId, created } = buildDoc();
  const { fetchFn, calls } = makeFetch(routes);
  const controller = ui.createController({ document, fetch: fetchFn, pollIntervalMs: 0 });
  return { document, byId, created, calls, controller };
}

const happyRoutes = () => ({
  '/api/session': jsonResponse({ csrf_token: 'tok-123' }),
  '/api/health': jsonResponse({ status: 'ok', butler: 'ready', agents: 2 }),
  '/api/agents': jsonResponse({
    agents: [{ id: 'r1', name: 'Remex', domain: 'operations' }],
  }),
  '/api/chat': jsonResponse({ response: 'Echo: hello' }),
});

function lastArticle(byId) {
  const children = byId.get('transcript').children;
  return children[children.length - 1];
}

function messageBody(article) {
  return article.children[1]; // header, body
}

/* ------------------------- static safety ------------------------- */

test('app.js passes node --check', () => {
  execFileSync(process.execPath, ['--check', jsPath], { stdio: 'pipe' });
});

test('index.html references only the local /assets files, with no external resources', () => {
  assert.match(html, /src="\/assets\/app\.js"/);
  assert.match(html, /href="\/assets\/style\.css"/);
  assert.ok(existsSync(path.join(staticDir, 'app.js')), 'app.js lives in static/');
  assert.ok(existsSync(path.join(staticDir, 'style.css')), 'style.css lives in static/');

  const combined = `${html}\n${css}\n${js}`;
  assert.doesNotMatch(combined, /https?:\/\//i, 'no remote URLs anywhere');
  assert.doesNotMatch(combined, /unpkg|jsdelivr|cdn\.|googleapis|fonts\./i);

  const scripts = html.match(/<script\b[^>]*>/gi) || [];
  assert.ok(scripts.length > 0, 'the page loads app.js');
  for (const tag of scripts) {
    assert.match(tag, /\bsrc=/i, 'every script tag must have a src (no inline scripts)');
  }
  assert.doesNotMatch(html, /<style\b/i, 'no inline stylesheet');
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i, 'no inline event handlers');
  assert.doesNotMatch(html, /<img\b/i, 'no images');
  assert.doesNotMatch(css, /@import/, 'no CSS imports');
});

test('app.js renders safely: textContent only, no storage, no credentials', () => {
  assert.doesNotMatch(js, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB|document\.cookie/);
  assert.doesNotMatch(js, /Authorization|Bearer |api[_-]?key/i);
  assert.match(js, /X-Prototype-CSRF/);

  // Only the four documented same-origin endpoints are ever requested.
  const endpoints = [...js.matchAll(/'(\/api\/[a-z]+)'/g)].map((m) => m[1]);
  assert.deepEqual(
    [...new Set(endpoints)].sort(),
    ['/api/agents', '/api/chat', '/api/health', '/api/session'],
  );
});

test('the page labels itself local prototype, not beta ready, with no fake agents', () => {
  assert.match(html, /local prototype/i);
  assert.match(html, /not beta ready/i);
  // Agent entries exist only inside the JS renderer, never prefilled in HTML.
  assert.doesNotMatch(html, /class="agent\b/);
  assert.doesNotMatch(html, /Remex|Butler \d/);
});

/* ------------------------- pure helpers ------------------------- */

test('healthView reports honest badge states', () => {
  const online = ui.healthView({ status: 'ok', butler: 'ready', agents: 3 });
  assert.equal(online.state, 'online');
  assert.equal(online.label, 'Local service online');
  assert.match(online.detail, /Butler: ready/);
  assert.match(online.detail, /Agents: 3/);

  const odd = ui.healthView({ status: 'degraded', butler: 'warming', agents: 0 });
  assert.equal(odd.state, 'unknown');
  assert.equal(odd.label, 'Status: degraded');

  const missing = ui.healthView(null);
  assert.equal(missing.state, 'offline');
  assert.equal(missing.label, 'Status unavailable');

  // Missing fields render an em dash, never an invented count.
  const empty = ui.healthView({});
  assert.equal(empty.agentsText, '\u2014');
  assert.equal(empty.butlerText, '\u2014');
});

test('normalizeAgents only returns validated server entries', () => {
  const list = ui.normalizeAgents({
    agents: [
      { id: 'a1', name: 'Research', domain: 'research' },
      { id: 'a2' },
      'junk',
      null,
      { name: '', id: '' },
    ],
  });
  assert.equal(list.length, 2);
  assert.deepEqual(list[0], { id: 'a1', name: 'Research', domain: 'research' });
  assert.deepEqual(list[1], { id: 'a2', name: 'a2', domain: '' });

  assert.deepEqual(ui.normalizeAgents({}), []);
  assert.deepEqual(ui.normalizeAgents(null), []);
  assert.deepEqual(ui.normalizeAgents({ agents: 'nope' }), []);
});

test('describeHttpError prefers the sanitized server error and stays honest', () => {
  assert.equal(ui.describeHttpError(500, 'boom', 'Internal Server Error'), 'boom');
  assert.match(ui.describeHttpError(503, '', 'Service Unavailable'), /HTTP 503/);
  assert.equal(ui.describeHttpError(undefined, undefined, undefined), 'Request failed.');
});

test('session initializes first; chat sends CSRF and renders untrusted reply as plain text', async () => {
  const routes = happyRoutes();
  routes['/api/chat'] = jsonResponse({ response: '<img src=x onerror=alert(1)>' });
  const { byId, created, calls, controller } = setup(routes);
  await controller.init();
  assert.equal(calls[0].url, '/api/session');
  assert.equal(byId.get('status-badge').textContent, 'Local service online');
  assert.equal(byId.get('agents-list').children.length, 1);
  assert.equal(await controller.submitMessage('hello'), true);
  const sent = calls.filter(({ url }) => url === '/api/chat');
  assert.equal(sent.length, 1);
  assert.equal(sent[0].init.headers['X-Prototype-CSRF'], 'tok-123');
  assert.deepEqual(JSON.parse(sent[0].init.body), { message: 'hello' });
  assert.equal(messageBody(lastArticle(byId)).textContent, '<img src=x onerror=alert(1)>');
  assert.equal(lastArticle(byId).attributes['data-state'], 'delivered');
  assert.equal(created.filter((node) => node.tagName === 'IMG').length, 0);
  controller.stop();
});

test('HTTP failure is visible, original input retained, and no success is implied', async () => {
  const routes = happyRoutes();
  routes['/api/chat'] = jsonResponse({ error: 'Butler unavailable' }, { ok: false, status: 503 });
  const { byId, controller } = setup(routes);
  await controller.init();
  byId.get('message-input').value = 'my text';
  assert.equal(await controller.submitMessage('my text'), false);
  assert.equal(byId.get('message-input').value, 'my text');
  assert.equal(lastArticle(byId).attributes['data-state'], 'failed');
  assert.equal(messageBody(lastArticle(byId)).textContent, 'Butler unavailable');
  controller.stop();
});

test('without a session, nothing is sent or shown as delivered', async () => {
  const routes = happyRoutes();
  routes['/api/session'] = jsonResponse({ error: 'No session' }, { ok: false, status: 403 });
  const { byId, calls, controller } = setup(routes);
  await controller.init();
  assert.equal(await controller.submitMessage('private message'), false);
  assert.equal(calls.filter(({ url }) => url === '/api/chat').length, 0);
  assert.match(byId.get('notice-text').textContent, /No local session yet/);
  controller.stop();
});

test('busy state prevents a second concurrent send', async () => {
  let release;
  const routes = happyRoutes();
  routes['/api/chat'] = () => new Promise((resolve) => { release = resolve; });
  const { byId, calls, controller } = setup(routes);
  await controller.init();
  const first = controller.submitMessage('one');
  assert.equal(controller.state().busy, true);
  assert.equal(await controller.submitMessage('two'), false);
  release(jsonResponse({ response: 'done' }));
  assert.equal(await first, true);
  assert.equal(controller.state().busy, false);
  assert.equal(calls.filter(({ url }) => url === '/api/chat').length, 1);
  assert.equal(byId.get('transcript').children.length, 2);
  controller.stop();
});

test('Enter sends, Shift+Enter does not, and a network error stays visibly failed', async () => {
  const routes = happyRoutes();
  routes['/api/chat'] = () => { throw new Error('connection closed'); };
  const { byId, calls, controller } = setup(routes);
  await controller.init();
  const input = byId.get('message-input');
  input.value = 'hello';
  let prevented = false;
  input.dispatch('keydown', { key: 'Enter', shiftKey: true, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(calls.filter(({ url }) => url === '/api/chat').length, 0);
  input.dispatch('keydown', { key: 'Enter', shiftKey: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(await controller.settled(), false);
  assert.equal(calls.filter(({ url }) => url === '/api/chat').length, 1);
  assert.equal(lastArticle(byId).attributes['data-state'], 'failed');
  assert.match(messageBody(lastArticle(byId)).textContent, /connection closed/);
  assert.equal(input.value, 'hello');
  controller.stop();
});
