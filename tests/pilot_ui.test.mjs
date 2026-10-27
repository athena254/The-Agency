/**
 * Browser-JS tests for the owner-only limited-autonomy pilot UI (no deps).
 *
 * The controller is driven through the real pilot.html ids with a tiny DOM fake
 * and a same-origin fetch fake. No Python, backend, provider, or network is
 * involved. Run:
 *
 *   node --test tests/pilot_ui.test.mjs
 */
import test from 'node:test';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(here, '..');
const staticDir = path.join(rootDir, 'src', 'agency', 'prototype', 'static');
const htmlPath = path.join(staticDir, 'pilot.html');
const cssPath = path.join(staticDir, 'pilot.css');
const jsPath = path.join(staticDir, 'pilot.js');

const html = readFileSync(htmlPath, 'utf8');
const css = readFileSync(cssPath, 'utf8');
const js = readFileSync(jsPath, 'utf8');

await import(pathToFileURL(jsPath).href);
const ui = globalThis.__pilotUI;

const ENDPOINTS = {
  session: '/api/session',
  capabilities: '/api/pilot/capabilities',
  threads: '/api/pilot/threads',
  tasks: '/api/pilot/tasks',
};

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
    if (name === 'hidden') this.hidden = true;
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

/** Build a fake document whose ids come from the real pilot.html markup. */
function buildDoc() {
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  const byId = new Map(ids.map((id) => [id, new FakeElement('div')]));
  for (const id of ids) {
    if (/input|title/.test(id)) byId.get(id).tagName = 'INPUT';
  }
  byId.get('pilot-message-input').tagName = 'TEXTAREA';
  byId.get('pilot-result-body').tagName = 'PRE';
  byId.get('pilot-lock').hidden = false;
  byId.get('pilot-workspace').hidden = true;
  byId.get('pilot-result').hidden = true;
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
    return typeof route === 'function' ? route(init, calls) : route;
  };
  return { fetchFn, calls };
}

function setup(routes) {
  const { document, byId, created } = buildDoc();
  const { fetchFn, calls } = makeFetch(routes);
  const controller = ui.createController({
    document,
    fetch: fetchFn,
    pollIntervalMs: 0,
    maxPollMs: 1000,
  });
  return { document, byId, created, calls, controller };
}

function sequence(...responses) {
  let index = 0;
  return () => responses[Math.min(index++, responses.length - 1)];
}

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

/* ------------------------- fixtures ------------------------- */

const NOW = '2026-10-19T12:00:00Z';

function capabilities(overrides = {}) {
  return {
    enabled: true,
    ready: true,
    mode: 'single_owner_pilot',
    tools: ['calculator', 'utc_time', 'web_search'],
    disabled: ['shell', 'fetch', 'governance'],
    limits: {
      requests_per_hour: 12,
      requests_per_day: 50,
      model_calls_per_hour: 40,
      model_calls_per_day: 100,
      model_calls_per_request: 8,
      tool_calls_per_request: 4,
    },
    provider_ready: true,
    uncertainty_blocked: false,
    ...overrides,
  };
}

function thread(id = 'thread-1', title = 'First conversation', overrides = {}) {
  return {
    id,
    title,
    parent_thread_id: null,
    state: 'active',
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function message(id, threadId, role, content, overrides = {}) {
  return { id, thread_id: threadId, role, content, created_at: NOW, ...overrides };
}

function taskView(overrides = {}) {
  return {
    request_id: 'req-1',
    task_id: 'task-1',
    thread_id: 'thread-1',
    state: 'RUNNING',
    replayed: false,
    cancel_requested: false,
    response: null,
    assessment: null,
    error_code: null,
    uncertain: false,
    usage: { model_attempts: 1, tool_attempts: 0 },
    created_at: NOW,
    updated_at: NOW,
    ...overrides,
  };
}

function toolReport(taskId, body) {
  const digest = createHash('sha256').update(body, 'utf8').digest('hex');
  return {
    schema_version: 'a2.v1',
    task_id: taskId,
    execution_status: 'completed',
    output_sha256: digest,
    verification: 'not_performed',
    risk_status: 'not_assessed',
    independently_verified: false,
    subtasks: [{
      subtask_id: 'tool_loop',
      path: 'tool',
      execution_status: 'completed',
      output_sha256: digest,
      verification: 'not_performed',
      checks: ['completeness', 'accuracy', 'safety'].map((criterion) => ({
        criterion,
        status: 'not_performed',
        method: 'not_performed',
        reason: 'tool_path_unchecked',
        score: null,
      })),
      risk: { status: 'not_assessed', method: 'not_performed', category: null, finding_id: null },
    }],
  };
}

function baseRoutes(overrides = {}) {
  return {
    [ENDPOINTS.session]: jsonResponse({ csrf_token: 'csrf-test-token' }),
    [ENDPOINTS.capabilities]: jsonResponse(capabilities()),
    [ENDPOINTS.threads]: jsonResponse({ threads: [thread()] }),
    [`${ENDPOINTS.threads}/thread-1/messages`]: jsonResponse({
      messages: [message('m1', 'thread-1', 'user', 'hello')],
    }),
    [`${ENDPOINTS.tasks}?limit=50`]: jsonResponse({ tasks: [] }),
    ...overrides,
  };
}

async function unlocked(routes, token = 'owner-secret-token') {
  const ctx = setup(routes);
  await ctx.controller.init();
  const ok = await ctx.controller.unlock(token);
  assert.equal(ok, true, 'unlock should succeed for the happy routes');
  return ctx;
}

function messagesText(byId) {
  return byId.get('pilot-messages').textContent;
}

function domText(byId) {
  return [...byId.values()].map((el) => el.textContent).join('\n');
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
}

function admitRoute(init) {
  const sent = JSON.parse(init.body);
  return jsonResponse(taskView({ request_id: sent.request_id, thread_id: sent.thread_id }), { status: 202 });
}

test('R1 stale write finally cannot clear a newer session write owner', async () => {
  const oldReply = deferred();
  const newReply = deferred();
  const routes = baseRoutes({ [ENDPOINTS.tasks]: () => oldReply.promise });
  const { controller, byId } = await unlocked(routes);
  await controller.selectThread('thread-1');
  const oldWrite = controller.submitTask('old');
  await flush();
  controller.lock();
  await controller.unlock('new-owner-token');
  await controller.selectThread('thread-1');
  routes[ENDPOINTS.tasks] = () => newReply.promise;
  const newWrite = controller.submitTask('new');
  await flush();
  oldReply.resolve(jsonResponse(taskView()));
  await oldWrite;
  assert.equal(controller.state().busy, true);
  assert.equal(byId.get('pilot-send-task').disabled, true);
  newReply.resolve(jsonResponse({}, { ok: false, status: 503 }));
  await newWrite;
  assert.equal(controller.state().busy, false);
});

test('R1 obsolete bootstrap and unlock cannot mutate a new epoch or clear its busy owner', async () => {
  for (const stage of ['bootstrap', 'capabilities', 'lists']) {
    const oldReply = deferred();
    const newReply = deferred();
    const routes = baseRoutes();
    const { controller, byId, calls } = setup(routes);
    await controller.init();
    if (stage === 'bootstrap') {
      controller.lock();
      routes[ENDPOINTS.session] = () => oldReply.promise;
    } else if (stage === 'capabilities') {
      routes[ENDPOINTS.capabilities] = () => oldReply.promise;
    } else {
      routes[ENDPOINTS.threads] = () => oldReply.promise;
    }
    const oldUnlock = controller.unlock('old-owner-token');
    await flush();
    controller.lock();
    routes[ENDPOINTS.session] = jsonResponse({ csrf_token: 'NEW-CSRF' });
    routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread()] });
    routes[ENDPOINTS.capabilities] = () => newReply.promise;
    const newUnlock = controller.unlock('new-owner-token');
    await flush();
    const count = calls.length;
    oldReply.resolve(stage === 'bootstrap' ? jsonResponse({ csrf_token: 'OLD-CSRF' })
      : stage === 'lists' ? jsonResponse({ threads: [] }) : jsonResponse(capabilities()));
    await flush();
    assert.equal(calls.length, count, 'obsolete unlock dispatches nothing after recovery');
    assert.equal(await oldUnlock, false);
    assert.equal(controller.state().busy, true, `${stage}: new unlock retains ownership`);
    assert.equal(byId.get('pilot-unlock-button').disabled, true);
    newReply.resolve(jsonResponse(capabilities()));
    assert.equal(await newUnlock, true);
    assert.equal(controller.state().hasBearer, true);
  }
});

test('R1 old 401 cannot lock a newly authenticated session', async () => {
  const held = deferred();
  const routes = baseRoutes();
  const { controller } = await unlocked(routes);
  routes[ENDPOINTS.threads] = () => held.promise;
  const oldRead = controller.refreshThreads();
  controller.lock();
  routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread()] });
  await controller.unlock('new-owner-token');
  held.resolve(jsonResponse({}, { ok: false, status: 401 }));
  await oldRead;
  assert.equal(controller.state().locked, false);
  assert.equal(controller.state().hasBearer, true);
});

test('R2 author stylesheet explicitly preserves hidden over workspace display (static only)', () => {
  assert.match(css, /\[hidden\]\s*\{\s*display:\s*none\s*!important\s*;/);
});

test('R3 CSRF recovery keeps the original thread and reserves a single write before awaiting', async () => {
  const csrf = deferred();
  const routes = baseRoutes({
    [ENDPOINTS.session]: jsonResponse({ csrf_token: '' }),
    [ENDPOINTS.tasks]: admitRoute,
    [`${ENDPOINTS.threads}/thread-2/messages`]: jsonResponse({ messages: [] }),
  });
  const { controller, byId, calls } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'PRIVATE-FOR-THREAD-1';
  routes[ENDPOINTS.session] = () => csrf.promise;
  const first = controller.submitTask('PRIVATE-FOR-THREAD-1');
  await flush();
  await controller.selectThread('thread-2');
  const second = controller.submitTask('SECOND-WRITE');
  csrf.resolve(jsonResponse({ csrf_token: 'RECOVERED' }));
  await Promise.all([first, second]);
  const posts = calls.filter((call) => call.init.method === 'POST');
  assert.equal(posts.length, 1, 'recovery owns the write slot');
  assert.deepEqual(JSON.parse(posts[0].init.body).thread_id, 'thread-1');
  assert.equal(JSON.parse(posts[0].init.body).message, 'PRIVATE-FOR-THREAD-1');
  assert.equal(byId.get('pilot-message-input').value, 'PRIVATE-FOR-THREAD-1', 'do not clear a different conversation draft');
});

test('R3 no write or CSRF state survives Lock during protection recovery', async () => {
  for (const operation of ['submit', 'create', 'cancel']) {
    const csrf = deferred();
    const routes = baseRoutes({ [ENDPOINTS.session]: jsonResponse({ csrf_token: '' }) });
    const { controller, byId, calls } = await unlocked(routes);
    await controller.selectThread('thread-1');
    if (operation === 'cancel') {
      routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(taskView());
      await controller.refreshTask('task-1');
    }
    routes[ENDPOINTS.session] = () => csrf.promise;
    byId.get('pilot-thread-title').value = 'PRIVATE';
    const writing = operation === 'submit' ? controller.submitTask('PRIVATE')
      : operation === 'create' ? controller.createThread() : controller.cancelTask();
    await flush();
    controller.lock();
    const snapshot = domText(byId);
    csrf.resolve(jsonResponse({ csrf_token: 'OLD-CSRF' }));
    await writing;
    assert.equal(calls.filter((call) => call.init.method === 'POST').length, 0, operation);
    assert.equal(controller.state().csrfReady, false);
    assert.equal(domText(byId), snapshot, 'old recovery cannot overwrite lock notices');
    assert.equal(controller.state().busy, false);
  }
});

test('R4 delayed RUNNING GET cannot reverse confirmed cancellation or a newer admitted task', async () => {
  for (const next of ['cancel', 'submit']) {
    const held = deferred();
    let taskId = 'task-1';
    const routes = baseRoutes({
      [ENDPOINTS.tasks]: (init) => {
        const sent = JSON.parse(init.body);
        return jsonResponse(taskView({ task_id: taskId, request_id: sent.request_id }));
      },
      [`${ENDPOINTS.tasks}/task-1`]: () => held.promise,
      [`${ENDPOINTS.tasks}/task-1/cancel`]: jsonResponse({ task: taskView({ state: 'CANCELLED', cancel_requested: true }) }),
    });
    const { controller, byId } = await unlocked(routes);
    await controller.selectThread('thread-1');
    await controller.submitTask('first');
    const oldGet = controller.refreshTask('task-1');
    await flush();
    if (next === 'cancel') await controller.cancelTask();
    else {
      taskId = 'task-2';
      await controller.submitTask('second');
    }
    const state = byId.get('pilot-task-state').textContent;
    held.resolve(jsonResponse(taskView()));
    await oldGet;
    assert.equal(byId.get('pilot-task-state').textContent, state, next);
    assert.equal(controller.state().activeTaskId, next === 'cancel' ? 'task-1' : 'task-2');
    assert.equal(byId.get('pilot-cancel-task').disabled, next === 'cancel');
  }
});

test('R4 terminal snapshots and server timestamps cannot regress through later GETs', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: admitRoute,
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({ state: 'CANCELLED', updated_at: '2026-10-19T12:00:02Z' })),
  });
  const { controller, byId } = await unlocked(routes);
  await controller.selectThread('thread-1');
  await controller.submitTask('run');
  await controller.refreshTask('task-1');
  routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(taskView({ updated_at: '2026-10-19T12:00:01Z' }));
  await controller.refreshTask('task-1');
  assert.match(byId.get('pilot-task-state').textContent, /^CANCELLED/);
});

test('R4 deferred digest cannot mutate a newer task snapshot or a re-unlocked session', async () => {
  const subtle = globalThis.crypto.subtle;
  const original = subtle.digest;
  try {
    for (const relock of [false, true]) {
      const digest = deferred();
      let nextId = 'task-1';
      const routes = baseRoutes({
        [ENDPOINTS.tasks]: (init) => jsonResponse(taskView({
          request_id: JSON.parse(init.body).request_id, task_id: nextId,
        })),
        [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({ state: 'COMPLETED', response: 'OLD-OUTPUT', assessment: toolReport('task-1', 'OLD-OUTPUT') })),
      });
      const { controller, byId } = await unlocked(routes);
      await controller.selectThread('thread-1');
      await controller.submitTask('first');
      subtle.digest = () => digest.promise;
      const oldGet = controller.refreshTask('task-1');
      await flush();
      if (relock) {
        controller.lock();
        await controller.unlock('new-owner-token');
        await controller.selectThread('thread-1');
      } else nextId = 'task-2';
      await controller.submitTask('new work');
      const snapshot = domText(byId);
      assert.equal(controller.state().polling, true);
      digest.resolve(Uint8Array.from(createHash('sha256').update('OLD-OUTPUT').digest()).buffer);
      await oldGet;
      assert.equal(domText(byId), snapshot, 'obsolete digest must not overwrite notes or result');
      assert.equal(controller.state().polling, true, 'obsolete terminal continuation must not stop a new poll');
      assert.equal(byId.get('pilot-result-body').textContent, '');
    }
  } finally {
    subtle.digest = original;
  }
});

test('R4 cancellation validates the exact task and thread before accepting its receipt', async () => {
  for (const wrong of [{ task_id: 'task-other' }, { thread_id: 'thread-other' }]) {
    const routes = baseRoutes({
      [ENDPOINTS.tasks]: admitRoute,
      [`${ENDPOINTS.tasks}/task-1/cancel`]: jsonResponse({ task: taskView({ state: 'CANCELLED', ...wrong }) }),
    });
    const { controller, byId } = await unlocked(routes);
    await controller.selectThread('thread-1');
    await controller.submitTask('first');
    assert.equal(await controller.cancelTask(), null);
    assert.equal(controller.state().activeTaskId, 'task-1');
    assert.match(byId.get('pilot-task-state').textContent, /^RUNNING/);
    assert.match(byId.get('pilot-cancel-note').textContent, /malformed|match/i);
  }
});

test('R5 obsolete ABA selections and overlapping message refreshes cannot erase committed messages', async () => {
  for (const aba of [false, true]) {
    const held = deferred();
    const routes = baseRoutes({ [`${ENDPOINTS.threads}/thread-2/messages`]: jsonResponse({ messages: [] }) });
    const { controller, byId } = await unlocked(routes);
    await controller.selectThread('thread-1');
    routes[`${ENDPOINTS.threads}/thread-1/messages`] = () => held.promise;
    const old = aba ? controller.selectThread('thread-1') : controller.refreshMessages('thread-1');
    await flush();
    if (aba) await controller.selectThread('thread-2');
    routes[`${ENDPOINTS.threads}/thread-1/messages`] = jsonResponse({ messages: [message('new', 'thread-1', 'assistant', 'NEW-COMMITTED-ANSWER')] });
    if (aba) await controller.selectThread('thread-1');
    else await controller.refreshMessages('thread-1');
    byId.get('pilot-messages').focused = false;
    const note = byId.get('pilot-messages-note').textContent;
    held.resolve(jsonResponse({ messages: [message('old', 'thread-1', 'assistant', 'OLD-SNAPSHOT')] }));
    await old;
    assert.match(messagesText(byId), /NEW-COMMITTED-ANSWER/);
    assert.doesNotMatch(messagesText(byId), /OLD-SNAPSHOT/);
    assert.equal(byId.get('pilot-messages-note').textContent, note);
    assert.equal(byId.get('pilot-messages').focused, false, 'obsolete selection cannot steal focus');
  }
});

test('R6 durable task history offers native button selection with fresh GET, refresh and active-only cancel', async () => {
  for (const state of ['ADMITTED', 'RUNNING', 'UNCERTAIN', 'COMPLETED', 'FAILED', 'DENIED', 'CANCELLED']) {
    const body = 'DURABLE-RESULT';
    const durable = taskView({
      state, uncertain: state === 'UNCERTAIN',
      response: state === 'COMPLETED' ? body : null,
      assessment: state === 'COMPLETED' ? toolReport('task-1', body) : null,
    });
    const get = deferred();
    const routes = baseRoutes({
      [`${ENDPOINTS.tasks}?limit=50`]: jsonResponse({ tasks: [durable] }),
      [`${ENDPOINTS.tasks}/task-1`]: () => get.promise,
      [`${ENDPOINTS.tasks}/task-1/cancel`]: jsonResponse({ task: taskView({ state: 'CANCELLED', cancel_requested: true }) }),
    });
    const { controller, byId, calls } = await unlocked(routes);
    controller.lock();
    await controller.unlock('new-owner-token');
    const button = byId.get('pilot-task-list').children[0].children.find((el) => el.tagName === 'BUTTON');
    assert.ok(button, 'durable task has a keyboard-activatable native button');
    assert.equal(button.getAttribute('type'), 'button');
    assert.match(button.textContent, /task-1/);
    assert.match(button.textContent, /thread-1/);
    button.dispatch('click'); // Native keyboard activation produces click; not a browser keyboard test.
    await flush();
    assert.equal(controller.state().activeTaskId, null, 'history alone cannot enable cancellation');
    assert.equal(byId.get('pilot-cancel-task').disabled, true);
    get.resolve(jsonResponse(durable));
    await controller.settled();
    assert.equal(controller.state().activeTaskId, 'task-1');
    assert.equal(byId.get('pilot-refresh-task').disabled, false);
    assert.equal(byId.get('pilot-cancel-task').disabled, !['ADMITTED', 'RUNNING'].includes(state));
    assert.equal(controller.state().polling, ['ADMITTED', 'RUNNING'].includes(state));
    assert.equal(byId.get('pilot-task-list').children[0].children[0].getAttribute('aria-pressed'), 'true');
    byId.get('pilot-refresh-task').dispatch('click');
    await controller.settled();
    await controller.cancelTask();
    const writes = calls.filter((call) => call.init.method === 'POST');
    assert.equal(writes.length, ['ADMITTED', 'RUNNING'].includes(state) ? 1 : 0);
    assert.ok(writes.every((call) => call.url.endsWith('/task-1/cancel')));
    assert.equal(calls.filter((call) => call.url === `${ENDPOINTS.tasks}/task-1`).length, 2);
    assert.equal(byId.get('pilot-result-body').textContent, state === 'COMPLETED' ? body : '');
  }
});

test('R7 classic assessed risk accepts only the actual RiskCategory wire enum', async () => {
  const body = 'CLASSIC-RESULT';
  const task = taskView({ state: 'COMPLETED', response: body });
  function classicReport(category) {
    const report = toolReport('task-1', body);
    report.verification = 'needs_review';
    report.risk_status = 'assessed';
    const subtask = report.subtasks[0];
    subtask.subtask_id = 'classic-1';
    subtask.path = 'classic';
    subtask.verification = 'needs_review';
    subtask.risk = { status: 'assessed', method: 'legacy_risk_engine/v1', category, finding_id: 'finding-1' };
    subtask.checks = subtask.checks.map((check) => check.criterion === 'accuracy'
      ? { ...check, reason: 'no_trusted_accuracy_oracle' }
      : { ...check, status: 'passed', method: check.criterion === 'safety' ? 'legacy_denylist/v1' : 'legacy_keyword_coverage/v1', reason: 'heuristic_passed', score: 1 });
    return report;
  }
  for (const category of ['critical', 'high', 'medium', 'low', 'negligible']) {
    assert.equal((await ui.bindAssessment(classicReport(category), task, body)).bound, true, category);
  }
  for (const category of ['NOT-A-RISK-ENUM', 'toString', '__proto__', 'constructor', 'HIGH', '', null, 1]) {
    assert.equal((await ui.bindAssessment(classicReport(category), task, body)).bound, false, String(category));
  }
});

test('R4 overlapping same-task GETs cannot restore an obsolete cancellation flag', async () => {
  const held = deferred();
  const routes = baseRoutes({ [ENDPOINTS.tasks]: admitRoute });
  const { controller, byId } = await unlocked(routes);
  await controller.selectThread('thread-1');
  await controller.submitTask('run');
  routes[`${ENDPOINTS.tasks}/task-1`] = () => held.promise;
  const old = controller.refreshTask('task-1');
  routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(taskView({ cancel_requested: true }));
  await controller.refreshTask('task-1');
  held.resolve(jsonResponse(taskView()));
  await old;
  assert.match(byId.get('pilot-task-state').textContent, /cancellation requested/);
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
});

test('R3 cancellation recovery cannot dispatch after its captured task becomes terminal', async () => {
  const csrf = deferred();
  const routes = baseRoutes({
    [ENDPOINTS.session]: jsonResponse({ csrf_token: '' }),
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView()),
    [`${ENDPOINTS.tasks}/task-1/cancel`]: jsonResponse({ task: taskView() }),
  });
  const { controller, calls } = await unlocked(routes);
  await controller.refreshTask('task-1');
  routes[ENDPOINTS.session] = () => csrf.promise;
  const cancelling = controller.cancelTask();
  await flush();
  routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(taskView({ state: 'CANCELLED' }));
  await controller.refreshTask('task-1');
  csrf.resolve(jsonResponse({ csrf_token: 'RECOVERED' }));
  await cancelling;
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 0);
});

test('R4 a delayed cancel receipt cannot contradict a newer terminal status', async () => {
  const held = deferred();
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: admitRoute,
    [`${ENDPOINTS.tasks}/task-1/cancel`]: () => held.promise,
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({ state: 'CANCELLED' })),
  });
  const { controller, byId } = await unlocked(routes);
  await controller.selectThread('thread-1');
  await controller.submitTask('run');
  const cancelling = controller.cancelTask();
  await flush();
  await controller.refreshTask('task-1');
  held.resolve(jsonResponse({ task: taskView({ cancel_requested: true }) }));
  assert.equal(await cancelling, 'CANCELLED');
  assert.match(byId.get('pilot-cancel-note').textContent, /CANCELLED/);
  assert.match(byId.get('pilot-task-state').textContent, /CANCELLED/);
});

test('R4 late task lists cannot restore obsolete history after cancellation or new admission', async () => {
  for (const next of ['cancel', 'submit']) {
    const held = deferred();
    let id = 'task-1';
    const routes = baseRoutes({
      [ENDPOINTS.tasks]: (init) => jsonResponse(taskView({ task_id: id, request_id: JSON.parse(init.body).request_id })),
      [`${ENDPOINTS.tasks}/task-1/cancel`]: jsonResponse({ task: taskView({ state: 'CANCELLED' }) }),
    });
    const { controller, byId } = await unlocked(routes);
    await controller.selectThread('thread-1');
    await controller.submitTask('first');
    routes[`${ENDPOINTS.tasks}?limit=50`] = () => held.promise;
    const listing = controller.refreshTasks();
    if (next === 'cancel') await controller.cancelTask();
    else { id = 'task-2'; await controller.submitTask('second'); }
    const snapshot = byId.get('pilot-task-list').textContent;
    held.resolve(jsonResponse({ tasks: [taskView()] }));
    await listing;
    assert.equal(byId.get('pilot-task-list').textContent, snapshot, next);
  }
});

test('R1 Lock aborts outstanding bootstrap and pilot transports without relying on abort for isolation', async () => {
  const held = deferred();
  const routes = baseRoutes();
  const { controller, calls } = await unlocked(routes);
  routes[ENDPOINTS.threads] = () => held.promise;
  const old = controller.refreshThreads();
  const signal = calls.at(-1).init.signal;
  assert.ok(signal, 'pilot transport carries the session AbortSignal');
  assert.equal(signal.aborted, false);
  controller.lock();
  assert.equal(signal.aborted, true);
  routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread()] });
  await controller.unlock('new-owner-token');
  assert.equal(calls.at(-1).init.signal.aborted, false);
  // The HTTP fake ignores abort deliberately: epochs must still reject success.
  held.resolve(jsonResponse({ threads: [thread('SECRET', 'SECRET')] }));
  await old;
  assert.equal(controller.state().locked, false);
  const csrfSignal = calls.findLast((call) => call.url === ENDPOINTS.session).init.signal;
  controller.lock();
  assert.equal(csrfSignal.aborted, true);
});

test('R5 obsolete conversation lists cannot clear a newer selected conversation', async () => {
  const held = deferred();
  const routes = baseRoutes({ [`${ENDPOINTS.threads}/thread-2/messages`]: jsonResponse({ messages: [message('new', 'thread-2', 'assistant', 'CURRENT')] }) });
  const { controller, byId } = await unlocked(routes);
  routes[ENDPOINTS.threads] = () => held.promise;
  const old = controller.refreshThreads();
  routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread('thread-2', 'New conversation')] });
  await controller.refreshThreads();
  await controller.selectThread('thread-2');
  held.resolve(jsonResponse({ threads: [] }));
  await old;
  assert.equal(controller.state().selectedThreadId, 'thread-2');
  assert.match(messagesText(byId), /CURRENT/);
});

test('R3 task admission receipt must match the captured conversation as well as request ID', async () => {
  const routes = baseRoutes({ [ENDPOINTS.tasks]: (init) => jsonResponse(taskView({
    request_id: JSON.parse(init.body).request_id, thread_id: 'thread-other',
  })) });
  const { controller, byId } = await unlocked(routes);
  await controller.selectThread('thread-1');
  assert.equal(await controller.submitTask('private'), false);
  assert.equal(controller.state().activeTaskId, null);
  assert.match(byId.get('pilot-task-note').textContent, /malformed|match/i);
});

test('R1 delayed valid COMPLETED admission cannot restore output after Lock and re-unlock', async () => {
  for (const decode of [false, true]) {
    const held = deferred();
    let completed;
    const routes = baseRoutes({ [ENDPOINTS.tasks]: (init) => {
      completed = taskView({ request_id: JSON.parse(init.body).request_id, state: 'COMPLETED',
        response: 'SECRET-OLD-COMPLETED-BODY', assessment: toolReport('task-1', 'SECRET-OLD-COMPLETED-BODY') });
      return decode ? { ok: true, status: 202, json: () => held.promise } : held.promise;
    } });
    const { controller, byId } = await unlocked(routes);
    await controller.selectThread('thread-1');
    const old = controller.submitTask('old work');
    await flush();
    controller.lock();
    await controller.unlock('new-owner-token');
    const snapshot = domText(byId);
    held.resolve(decode ? completed : jsonResponse(completed));
    assert.equal(await old, false);
    assert.equal(domText(byId), snapshot);
    assert.equal(byId.get('pilot-result-body').textContent, '');
    assert.equal(controller.state().activeTaskId, null);
  }
});

test('R4 digest continuation must own the exact same-task render snapshot, not only its task ID', async () => {
  const subtle = globalThis.crypto.subtle;
  const original = subtle.digest;
  const firstDigest = deferred();
  const secondDigest = deferred();
  const body = 'SAME-TASK-OUTPUT';
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: admitRoute,
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({ state: 'COMPLETED', response: body, assessment: toolReport('task-1', body) })),
  });
  const { controller, byId, calls } = await unlocked(routes);
  await controller.selectThread('thread-1');
  await controller.submitTask('first');
  try {
    subtle.digest = sequence(firstDigest.promise, secondDigest.promise);
    const old = controller.refreshTask('task-1');
    await flush();
    const newer = controller.refreshTask('task-1');
    await flush();
    const bytes = Uint8Array.from(createHash('sha256').update(body).digest()).buffer;
    secondDigest.resolve(bytes);
    await newer;
    const snapshot = domText(byId);
    const count = calls.length;
    firstDigest.resolve(bytes);
    await old;
    assert.equal(domText(byId), snapshot);
    assert.equal(calls.length, count, 'obsolete digest cannot reload the transcript');
  } finally { subtle.digest = original; }
});

test('R6 task selection generations reject ABA status loads and wrong task IDs', async () => {
  const held = deferred();
  const routes = baseRoutes({
    [`${ENDPOINTS.tasks}?limit=50`]: jsonResponse({ tasks: [taskView(), taskView({ task_id: 'task-2' })] }),
    [`${ENDPOINTS.tasks}/task-1`]: () => held.promise,
    [`${ENDPOINTS.tasks}/task-2`]: jsonResponse(taskView({ task_id: 'task-2' })),
  });
  const { controller, byId, calls } = await unlocked(routes);
  const old = controller.selectTask('task-1');
  await controller.selectTask('task-2');
  routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(taskView({ state: 'CANCELLED' }));
  await controller.selectTask('task-1');
  held.resolve(jsonResponse(taskView()));
  await old;
  assert.match(byId.get('pilot-task-state').textContent, /CANCELLED/);
  assert.equal(controller.state().polling, false);
  routes[`${ENDPOINTS.tasks}/task-2`] = jsonResponse(taskView({ task_id: 'task-other' }));
  assert.equal(await controller.selectTask('task-2'), null);
  assert.equal(controller.state().activeTaskId, null);
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 0);
});

/* ------------------------- static safety ------------------------- */

test('pilot.js passes node --check', () => {
  execFileSync(process.execPath, ['--check', jsPath], { stdio: 'pipe' });
});

test('pilot.html references only local pilot assets and has no remote/inline resources', () => {
  assert.match(html, /href="\/assets\/pilot\.css"/);
  assert.match(html, /src="\/assets\/pilot\.js"/);
  assert.ok(existsSync(cssPath), 'pilot.css lives in static/');
  assert.ok(existsSync(jsPath), 'pilot.js lives in static/');

  const combined = `${html}\n${css}\n${js}`;
  assert.doesNotMatch(combined, /https?:\/\//i, 'no remote URLs anywhere');
  assert.doesNotMatch(combined, /unpkg|jsdelivr|cdn\.|googleapis|fonts\./i);
  assert.doesNotMatch(html, /\son[a-z]+\s*=/i, 'no inline event handlers');
  assert.doesNotMatch(html, /<style\b/i, 'no inline stylesheet');
  assert.doesNotMatch(css, /@import/, 'no CSS imports');
});

test('pilot.js renders safely, keeps credentials in memory and uses no browser storage', () => {
  assert.doesNotMatch(js, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.doesNotMatch(js, /localStorage|sessionStorage|indexedDB|document\.cookie/);
  assert.doesNotMatch(js, /console\.(log|info|warn|error|debug)/);
  assert.doesNotMatch(js, /location\.(search|hash|href)|URLSearchParams/);
  assert.match(js, /Authorization/);
  assert.match(js, /X-Prototype-CSRF/);
  assert.match(js, /credentials:\s*'same-origin'/);
  assert.match(js, /encodeURIComponent/);
  // No hard-coded bearer value: the token is read from the password field.
  assert.doesNotMatch(js, /Bearer\s+[A-Za-z0-9_-]{8,}/);
});

test('pilot.html exposes accessibility hooks and visibly disabled consequential actions', () => {
  assert.match(html, /aria-live="polite"/);
  assert.match(html, /role="log"/);
  assert.match(html, /<label[^>]+for="pilot-token-input"/);
  assert.match(html, /type="password"/);
  assert.match(html, /calculator,\s*utc_time,\s*web_search/i);
  assert.match(html, /shell/i);
  assert.match(html, /fetch/i);
  assert.match(html, /governance/i);
  assert.match(html, /Disabled/i);
  assert.match(html, /not production-ready/i);
  assert.match(html, /not decentralized/i);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /@media[^{]*max-width/);
});

/* ------------------------- lock/unlock ------------------------- */

test('init bootstraps only the CSRF session and exposes no workspace data before unlock', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.threads]: jsonResponse({ threads: [thread('thread-1', 'SECRET-THREAD-TITLE')] }),
  });
  const { byId, calls, controller } = setup(routes);
  await controller.init();

  assert.deepEqual(calls.map((call) => call.url), [ENDPOINTS.session]);
  assert.equal(byId.get('pilot-workspace').hidden, true);
  assert.equal(byId.get('pilot-lock').hidden, false);
  assert.equal(controller.state().locked, true);
  assert.equal(controller.state().hasBearer, false);
  assert.doesNotMatch(domText(byId), /SECRET-THREAD-TITLE/);
});

test('unlock without a bearer performs no capabilities request', async () => {
  const { byId, calls, controller } = setup(baseRoutes());
  await controller.init();
  assert.equal(await controller.unlock('   '), false);
  assert.deepEqual(calls.map((call) => call.url), [ENDPOINTS.session]);
  assert.match(byId.get('pilot-unlock-note').textContent, /bearer/i);
  assert.equal(controller.state().locked, true);
});

test('successful unlock calls capabilities with the in-memory bearer, then lists threads/tasks', async () => {
  const routes = baseRoutes();
  const { byId, calls, controller } = await unlocked(routes);

  assert.deepEqual(calls.map((call) => call.url), [
    ENDPOINTS.session,
    ENDPOINTS.capabilities,
    ENDPOINTS.threads,
    `${ENDPOINTS.tasks}?limit=50`,
  ]);
  const capabilityCall = calls.find((call) => call.url === ENDPOINTS.capabilities);
  assert.equal(capabilityCall.init.headers.Authorization, 'Bearer owner-secret-token');
  assert.equal(capabilityCall.init.credentials, 'same-origin');
  assert.equal(capabilityCall.init.headers['X-Prototype-CSRF'], undefined);

  for (const call of calls.filter((entry) => entry.url.startsWith('/api/pilot/'))) {
    assert.equal(call.init.headers.Authorization, 'Bearer owner-secret-token');
  }
  assert.equal(byId.get('pilot-token-input').value, '');
  assert.equal(byId.get('pilot-workspace').hidden, false);
  assert.equal(byId.get('pilot-lock').hidden, true);
  assert.doesNotMatch(byId.get('pilot-workspace').textContent, /owner-secret-token/);
  assert.match(byId.get('pilot-thread-list').textContent, /First conversation/);
  assert.equal(controller.state().locked, false);
  assert.equal(controller.state().hasBearer, true);
});

test('capabilities data is validated: disabled or malformed capabilities do not unlock', async () => {
  for (const payload of [
    capabilities({ enabled: false }),
    capabilities({ ready: 'yes' }),
    capabilities({ mode: 'peer' }),
    { ...capabilities(), extra: true },
  ]) {
    const routes = baseRoutes({ [ENDPOINTS.capabilities]: jsonResponse(payload) });
    const { byId, controller } = setup(routes);
    await controller.init();
    assert.equal(await controller.unlock('owner-secret-token'), false);
    assert.equal(controller.state().locked, true);
    assert.equal(byId.get('pilot-workspace').hidden, true);
  }
});

test('401 during any pilot request locks, clears credentials/sensitive DOM and stops polling', async () => {
  const routes = baseRoutes();
  const { byId, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  assert.match(messagesText(byId), /hello/);

  routes[ENDPOINTS.threads] = jsonResponse({ error: 'unauthorized' }, { ok: false, status: 401 });
  await controller.refreshThreads();

  assert.equal(controller.state().locked, true);
  assert.equal(controller.state().hasBearer, false);
  assert.equal(controller.state().polling, false);
  assert.equal(byId.get('pilot-workspace').hidden, true);
  assert.equal(byId.get('pilot-lock').hidden, false);
  assert.equal(byId.get('pilot-token-input').value, '');
  assert.equal(messagesText(byId), '');
  assert.equal(byId.get('pilot-thread-list').textContent, '');
  assert.match(byId.get('pilot-unlock-note').textContent, /cleared|rejected|expired/i);

  await controller.submitTask('must not send');
  assert.equal(controller.state().locked, true);
});

/* ------------------------- threads ------------------------- */

test('thread selection uses encodeURIComponent and isolates one thread at a time', async () => {
  const first = thread('thread/one', 'First & <b>unsafe</b>');
  const second = thread('thread two', 'Second thread');
  const routes = baseRoutes({
    [ENDPOINTS.threads]: jsonResponse({ threads: [first, second] }),
    [`${ENDPOINTS.threads}/${encodeURIComponent(first.id)}/messages`]: jsonResponse({
      messages: [message('m1', first.id, 'assistant', 'FIRST-BODY')],
    }),
    [`${ENDPOINTS.threads}/${encodeURIComponent(second.id)}/messages`]: jsonResponse({
      messages: [message('m2', second.id, 'assistant', 'SECOND-BODY')],
    }),
  });
  const { byId, created, calls, controller } = await unlocked(routes);

  await controller.selectThread(first.id);
  assert.match(messagesText(byId), /FIRST-BODY/);
  assert.doesNotMatch(messagesText(byId), /SECOND-BODY/);
  assert.equal(created.filter((node) => node.tagName === 'B').length, 0);

  await controller.selectThread(second.id);
  assert.match(messagesText(byId), /SECOND-BODY/);
  assert.doesNotMatch(messagesText(byId), /FIRST-BODY/);
  assert.ok(calls.some((call) => call.url.endsWith('/thread%2Fone/messages')));
  assert.ok(calls.some((call) => call.url.endsWith('/thread%20two/messages')));
});

test('messages from a different thread id are not rendered into the selected thread', async () => {
  const routes = baseRoutes({
    [`${ENDPOINTS.threads}/thread-1/messages`]: jsonResponse({
      messages: [
        message('m1', 'thread-1', 'assistant', 'OWN-THREAD'),
        message('leak', 'thread-2', 'assistant', 'FOREIGN-THREAD'),
      ],
    }),
  });
  const { byId, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  assert.match(messagesText(byId), /OWN-THREAD/);
  assert.doesNotMatch(messagesText(byId), /FOREIGN-THREAD/);
});

test('create thread posts only the frozen request model with CSRF and a valid request id', async () => {
  const createdThread = thread('thread-new', 'New work');
  const routes = baseRoutes();
  let sent = null;
  const { byId, calls, controller } = await unlocked(routes);
  // Replace the shared list route after unlock and add the POST route, which
  // shares the same URL with different methods.
  routes[ENDPOINTS.threads] = (init) => {
    if (init.method === 'POST') {
      sent = JSON.parse(init.body);
      return jsonResponse({
        request: { request_id: sent.request_id, state: 'created', replayed: false, resource_id: createdThread.id },
        thread: createdThread,
      }, { status: 201 });
    }
    return jsonResponse({ threads: [thread(), createdThread] });
  };
  routes[`${ENDPOINTS.threads}/thread-new/messages`] = jsonResponse({ messages: [] });

  byId.get('pilot-thread-title').value = 'New work';
  byId.get('pilot-thread-form').dispatch('submit', { preventDefault() {} });
  await controller.settled();

  const post = calls.find((call) => call.init.method === 'POST');
  assert.ok(post, 'a thread POST should be sent');
  assert.equal(post.url, ENDPOINTS.threads);
  assert.match(post.init.headers['X-Prototype-CSRF'], /csrf-test-token/);
  assert.deepEqual(Object.keys(sent).sort(), ['parent_thread_id', 'request_id', 'title']);
  assert.equal(sent.title, 'New work');
  assert.equal(sent.parent_thread_id, null);
  assert.match(sent.request_id, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
  assert.equal(byId.get('pilot-thread-title').value, '');
  assert.match(byId.get('pilot-thread-list').textContent, /New work/);
});

test('missing CSRF token prevents both POST writes and shows an honest notice', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.session]: jsonResponse({ csrf_token: '' }),
  });
  const { byId, calls, controller } = await unlocked(routes);
  byId.get('pilot-thread-title').value = 'No protection';
  byId.get('pilot-thread-form').dispatch('submit', { preventDefault() {} });
  await controller.settled();
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 0);
  assert.match(byId.get('pilot-threads-note').textContent, /session|CSRF|protection/i);
});

/* ------------------------- tasks ------------------------- */

test('task submit posts only frozen fields, polls read-only, binds tool-path output, and renders inert text', async () => {
  const body = '<img src=x onerror=alert(1)> task body & stuff';
  const completed = taskView({
    state: 'COMPLETED',
    response: body,
    assessment: toolReport('task-1', body),
    usage: { model_attempts: 2, tool_attempts: 1 },
  });
  let postedBody = null;
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      postedBody = JSON.parse(init.body);
      return jsonResponse(taskView({
        request_id: postedBody.request_id,
        thread_id: postedBody.thread_id,
        state: 'ADMITTED',
      }), { status: 202 });
    },
    [`${ENDPOINTS.tasks}/task-1`]: sequence(
      jsonResponse(taskView({ state: 'RUNNING' })),
      jsonResponse(completed),
      jsonResponse(completed),
    ),
    [ENDPOINTS.threads + '/thread-1/messages']: jsonResponse({
      messages: [
        message('u1', 'thread-1', 'user', 'do work'),
        message('a1', 'thread-1', 'assistant', body),
      ],
    }),
  });
  const { byId, created, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'do work';

  const submit = await controller.submitTask('do work');
  assert.equal(submit, true);
  assert.deepEqual(Object.keys(postedBody).sort(), ['message', 'request_id', 'thread_id']);
  assert.equal(postedBody.message, 'do work');
  assert.equal(postedBody.thread_id, 'thread-1');
  assert.match(postedBody.request_id, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1);
  assert.match(byId.get('pilot-task-state').textContent, /ADMITTED/);
  assert.equal(byId.get('pilot-cancel-task').disabled, false);

  await controller.refreshTask('task-1');
  await controller.refreshTask('task-1');

  const taskGet = calls.filter((call) => call.url === `${ENDPOINTS.tasks}/task-1`);
  assert.ok(taskGet.length >= 2);
  for (const call of taskGet) {
    assert.equal(call.init.method, 'GET');
    assert.equal(call.init.headers['X-Prototype-CSRF'], undefined);
    assert.equal(call.init.headers.Authorization, 'Bearer owner-secret-token');
  }

  assert.match(byId.get('pilot-task-state').textContent, /COMPLETED/);
  assert.equal(byId.get('pilot-result').hidden, false);
  assert.equal(byId.get('pilot-result-body').textContent, body);
  assert.match(byId.get('pilot-result-execution').textContent, /completed/i);
  assert.match(byId.get('pilot-result-check').textContent, /not performed|No independent verification/i);
  assert.match(byId.get('pilot-result-check').textContent, /risk not assessed/i);
  assert.doesNotMatch(byId.get('pilot-result-check').textContent, /independently verified: yes/i);
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
  assert.match(messagesText(byId), /do work/);
  assert.match(messagesText(byId), /task body/);
  assert.equal(created.filter((node) => node.tagName === 'IMG' || node.tagName === 'SCRIPT').length, 0);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1, 'no automatic resubmit');
});

test('malformed or misbound assessments withhold the completed body as unavailable', async () => {
  const body = 'bound result';
  const good = toolReport('task-1', body);
  const variants = [
    null,
    { ...good, task_id: 'other-task' },
    { ...good, output_sha256: 'a'.repeat(64) },
    { ...good, execution_status: 'failed' },
    { ...good, independently_verified: true },
    { ...good, extra: 'not allowed' },
    { ...good, subtasks: [{ ...good.subtasks[0], subtask_id: 'isolated' }] },
    { ...good, subtasks: [{ ...good.subtasks[0], path: 'isolated' }] },
    { ...good, subtasks: [{ ...good.subtasks[0], output_sha256: 'b'.repeat(64) }] },
    { ...good, subtasks: [{ ...good.subtasks[0], checks: [] }] },
    { ...good, subtasks: [] },
  ];

  for (const assessment of variants) {
    const completed = taskView({ state: 'COMPLETED', response: body, assessment });
    const routes = baseRoutes({
      [ENDPOINTS.tasks]: (init) => {
        const sent = JSON.parse(init.body);
        return jsonResponse(taskView({
          request_id: sent.request_id,
          state: 'RUNNING',
        }), { status: 202 });
      },
      [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(completed),
    });
    const { byId, controller } = await unlocked(routes);
    await controller.selectThread('thread-1');
    byId.get('pilot-message-input').value = 'run';
    await controller.submitTask('run');
    await controller.refreshTask('task-1');

    assert.equal(byId.get('pilot-task-state').textContent.includes('COMPLETED'), true);
    assert.equal(byId.get('pilot-result').hidden, true, 'unbound body must stay hidden');
    assert.equal(byId.get('pilot-result-body').textContent, '');
    assert.match(byId.get('pilot-task-note').textContent, /unavailable|withheld/i);
    assert.doesNotMatch(messagesText(byId), /bound result/);
    controller.stop();
  }
});

test('FAILED and UNCERTAIN are shown honestly with no body and no automatic retry', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      const sent = JSON.parse(init.body);
      return jsonResponse(taskView({
        request_id: sent.request_id,
        state: 'RUNNING',
      }), { status: 202 });
    },
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({
      state: 'UNCERTAIN',
      uncertain: true,
      error_code: 'deadline_expired',
    })),
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'run';
  await controller.submitTask('run');
  await controller.refreshTask('task-1');

  assert.match(byId.get('pilot-task-state').textContent, /UNCERTAIN/);
  assert.match(byId.get('pilot-task-note').textContent, /uncertain/i);
  assert.match(byId.get('pilot-task-note').textContent, /deadline_expired|expired/i);
  assert.equal(byId.get('pilot-result').hidden, true);
  assert.equal(byId.get('pilot-result-body').textContent, '');
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
  assert.equal(controller.state().polling, false);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1);
});

test('ambiguous task submission retains the same request id and never auto-retries', async () => {
  let attempts = 0;
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      attempts += 1;
      if (attempts === 1) throw new Error('connection reset');
      const sent = JSON.parse(init.body);
      return jsonResponse(taskView({
        request_id: sent.request_id,
        state: 'RUNNING',
      }), { status: 202 });
    },
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'same work';

  assert.equal(await controller.submitTask('same work'), false);
  assert.match(byId.get('pilot-task-note').textContent, /unknown|uncertain/i);
  assert.equal(controller.state().polling, false);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1);

  assert.equal(await controller.submitTask('same work'), true);
  const posts = calls.filter((call) => call.init.method === 'POST');
  assert.equal(posts.length, 2);
  assert.equal(JSON.parse(posts[0].init.body).request_id, JSON.parse(posts[1].init.body).request_id);
});

test('cancel is offered only while active, waits for the server, and never claims a local cancellation', async () => {
  const pendingCancel = deferred();
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      const sent = JSON.parse(init.body);
      return jsonResponse(taskView({
        request_id: sent.request_id,
        state: 'RUNNING',
      }), { status: 202 });
    },
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(taskView({ state: 'RUNNING' })),
    [`${ENDPOINTS.tasks}/task-1/cancel`]: () => pendingCancel.promise,
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'long work';
  await controller.submitTask('long work');
  await controller.refreshTask('task-1');
  assert.equal(byId.get('pilot-cancel-task').disabled, false);

  const cancelling = controller.cancelTask();
  assert.match(byId.get('pilot-cancel-note').textContent, /requested|waiting|confirm/i);
  assert.doesNotMatch(byId.get('pilot-task-state').textContent, /CANCELLED/);

  pendingCancel.resolve(jsonResponse({ task: taskView({ state: 'CANCELLED', cancel_requested: true }) }, { status: 202 }));
  assert.match(await cancelling, /CANCELLED|requested/i);

  const cancelCall = calls.find((call) => call.url.endsWith('/task-1/cancel'));
  assert.ok(cancelCall);
  assert.equal(cancelCall.init.method, 'POST');
  assert.match(cancelCall.init.headers['X-Prototype-CSRF'], /csrf-test-token/);
  assert.deepEqual(Object.keys(JSON.parse(cancelCall.init.body)), ['request_id']);
  assert.match(byId.get('pilot-task-state').textContent, /CANCELLED/);
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
});

test('completed tasks cannot be cancelled and no cancel request is sent', async () => {
  const body = 'done';
  const completed = taskView({ state: 'COMPLETED', response: body, assessment: toolReport('task-1', body) });
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      const sent = JSON.parse(init.body);
      return jsonResponse(taskView({ request_id: sent.request_id, state: 'RUNNING' }), { status: 202 });
    },
    [`${ENDPOINTS.tasks}/task-1`]: jsonResponse(completed),
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  byId.get('pilot-message-input').value = 'run';
  await controller.submitTask('run');
  await controller.refreshTask('task-1');
  assert.equal(byId.get('pilot-cancel-task').disabled, true);
  await controller.cancelTask();
  assert.equal(calls.filter((call) => call.url.endsWith('/cancel')).length, 0);
});

test('empty threads, empty tasks and quota errors have honest explicit states', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.threads]: jsonResponse({ threads: [] }),
    [`${ENDPOINTS.tasks}?limit=50`]: jsonResponse({ tasks: [] }),
  });
  const { byId, controller } = await unlocked(routes);
  assert.match(byId.get('pilot-threads-note').textContent, /No conversations/i);
  assert.match(byId.get('pilot-tasks-note').textContent, /No tasks/i);

  routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread()] });
  await controller.refreshThreads();
  await controller.selectThread('thread-1');
  routes[ENDPOINTS.tasks] = jsonResponse({
    error: 'quota', code: 'quota_exceeded', request_id: null, task_id: null,
    uncertain: false, retryable: true,
  }, { ok: false, status: 429 });
  byId.get('pilot-message-input').value = 'over budget';
  assert.equal(await controller.submitTask('over budget'), false);
  assert.match(byId.get('pilot-task-note').textContent, /Budget|quota/i);
  assert.equal(controller.state().polling, false);
});

test('Enter submits a task, Shift+Enter does not, and a failed write keeps the draft', async () => {
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      const sent = JSON.parse(init.body);
      return jsonResponse(taskView({ request_id: sent.request_id, state: 'RUNNING' }), { status: 202 });
    },
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');
  const input = byId.get('pilot-message-input');
  input.value = 'keyboard task';

  let prevented = false;
  input.dispatch('keydown', { key: 'Enter', shiftKey: true, isComposing: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 0);

  input.dispatch('keydown', { key: 'Enter', shiftKey: false, isComposing: false, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(await controller.settled(), true);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1);
  assert.equal(input.value, '');
});

test('R1 delayed write receipts cannot restore sensitive state across Lock or re-unlock', async () => {
  for (const operation of ['submit', 'create', 'cancel']) {
    for (const decode of [false, true]) {
      for (const reunlock of [false, true]) {
        const held = deferred();
        const routes = baseRoutes({ [ENDPOINTS.tasks]: admitRoute });
        const { controller, byId, calls } = await unlocked(routes);
        await controller.selectThread('thread-1');
        let writing;
        let reply;
        const delayed = (init) => {
          const sent = JSON.parse(init.body);
          const secretTask = taskView({ request_id: sent.request_id,
            task_id: operation === 'cancel' ? 'task-1' : 'SECRET-OLD-TASK',
            state: operation === 'cancel' ? 'CANCELLED' : 'RUNNING' });
          reply = operation === 'create' ? {
            request: { request_id: sent.request_id, state: 'created', replayed: false, resource_id: 'SECRET-THREAD' },
            thread: thread('SECRET-THREAD', 'SECRET-TITLE'),
          } : operation === 'cancel' ? { task: secretTask } : secretTask;
          return decode ? { ok: true, status: 202, json: () => held.promise } : held.promise;
        };
        if (operation === 'submit') {
          routes[ENDPOINTS.tasks] = delayed;
          writing = controller.submitTask('PRIVATE');
        } else if (operation === 'create') {
          byId.get('pilot-thread-title').value = 'PRIVATE';
          routes[ENDPOINTS.threads] = delayed;
          writing = controller.createThread();
        } else {
          await controller.submitTask('prepare');
          routes[`${ENDPOINTS.tasks}/task-1/cancel`] = delayed;
          writing = controller.cancelTask();
        }
        await flush();
        controller.lock();
        routes[ENDPOINTS.threads] = jsonResponse({ threads: [thread()] });
        if (reunlock) await controller.unlock('new-owner-token');
        const callCount = calls.length;
        held.resolve(decode ? reply : jsonResponse(reply, { status: 202 }));
        await writing;
        assert.equal(controller.state().activeTaskId, null, `${operation}/${decode}/${reunlock}: stale task discarded`);
        assert.doesNotMatch(domText(byId), /SECRET/);
        assert.equal(calls.length, callCount, 'stale receipt must not trigger more reads');
        assert.equal(controller.state().locked, !reunlock);
      }
    }
  }
});
