/**
 * Browser-JS tests for the bounded calculator-verification UI (no deps).
 *
 * Drives the real pilot.js controller through the real pilot.html ids with a
 * tiny DOM fake and a deterministic same-origin fetch fake. No backend,
 * provider, network or browser is involved.
 *
 * Run: node --test tests/calculator_verification_ui.test.mjs
 */
import test from 'node:test';
import { createHash } from 'node:crypto';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const rootDir = path.resolve(here, '..');
const staticDir = path.join(rootDir, 'src', 'agency', 'prototype', 'static');
const htmlPath = path.join(staticDir, 'pilot.html');
const jsPath = path.join(staticDir, 'pilot.js');

const html = readFileSync(htmlPath, 'utf8');
await import(pathToFileURL(jsPath).href);
const ui = globalThis.__pilotUI;

const ENDPOINTS = {
  session: '/api/session',
  capabilities: '/api/pilot/capabilities',
  threads: '/api/pilot/threads',
  tasks: '/api/pilot/tasks',
  calculatorTasks: '/api/pilot/calculator-tasks',
};

function verificationUrl(taskId) {
  return `${ENDPOINTS.tasks}/${encodeURIComponent(taskId)}/verification`;
}

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

function buildDoc() {
  const ids = [...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]);
  const byId = new Map(ids.map((id) => [id, new FakeElement('div')]));
  for (const id of ids) {
    if (/input|expression|title/.test(id)) byId.get(id).tagName = 'INPUT';
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

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

async function flush() {
  await new Promise((resolve) => setImmediate(resolve));
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

const sha = (text) => createHash('sha256').update(text, 'utf8').digest('hex');

function calculatorReceipt(overrides = {}) {
  const { response: responseOverride, ...fields } = overrides;
  const expression = Object.prototype.hasOwnProperty.call(fields, 'expression')
    ? fields.expression : '2 + 3 * 4';
  const response = responseOverride === undefined ? '14' : responseOverride;
  const base = {
    schema_version: 'calculator.v1',
    method: 'bounded_integer_arithmetic/v1',
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    expression,
    criteria_sha256: sha(expression),
    output_sha256: sha(response),
    status: 'passed',
    reason: 'matches',
    expected: '14',
    observed: '14',
  };
  return { ...base, ...fields };
}

function verificationEnvelope(task, receiptValue, reason = 'available', overrides = {}) {
  return {
    task_id: task.task_id,
    request_id: task.request_id,
    thread_id: task.thread_id,
    execution_state: task.state,
    receipt: reason === 'available' ? receiptValue : null,
    reason,
    ...overrides,
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

function domText(byId) {
  return [...byId.values()].map((el) => el.textContent).join('\n');
}

/* ------------------------- end-to-end ------------------------- */

test('calculator submission posts exact fields, keeps unlock reads frozen, and renders a bound passed check separately', async () => {
  let sent = null;
  let completed = null;
  const routes = baseRoutes({
    [ENDPOINTS.calculatorTasks]: (init) => {
      sent = JSON.parse(init.body);
      completed = taskView({
        request_id: sent.request_id,
        thread_id: sent.thread_id,
        state: 'COMPLETED',
        response: '14',
        assessment: toolReport('task-1', '14'),
      });
      return jsonResponse(completed, { status: 202 });
    },
    [verificationUrl('task-1')]: () => jsonResponse(verificationEnvelope(
      completed,
      calculatorReceipt({ request_id: sent.request_id, expression: sent.expression }),
    )),
  });
  const { byId, calls, controller } = await unlocked(routes);
  await controller.selectThread('thread-1');

  byId.get('pilot-calculator-expression').value = '2 + 3 * 4';
  byId.get('pilot-calculator-form').dispatch('submit', { preventDefault() {} });
  await controller.settled();

  const posts = calls.filter((call) => call.init.method === 'POST');
  assert.equal(posts.length, 1, 'only the explicit calculator endpoint is posted');
  assert.equal(posts[0].url, ENDPOINTS.calculatorTasks);
  assert.deepEqual(Object.keys(sent).sort(), ['expression', 'request_id', 'thread_id']);
  assert.equal(sent.expression, '2 + 3 * 4');
  assert.equal(sent.thread_id, 'thread-1');
  assert.match(sent.request_id, /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/);
  assert.match(posts[0].init.headers['X-Prototype-CSRF'], /csrf-test-token/);
  assert.equal(posts[0].init.headers.Authorization, 'Bearer owner-secret-token');
  assert.equal(byId.get('pilot-calculator-expression').value, '');
  assert.match(byId.get('pilot-task-state').textContent, /COMPLETED/);

  assert.equal(calls.filter((call) => call.url === verificationUrl('task-1')).length, 0,
    'unlock and admission must not read the verification endpoint');
  assert.doesNotMatch(byId.get('pilot-verification-verdict').textContent, /^Passed/i);
  assert.match(byId.get('pilot-verification-note').textContent, /not read|Refresh check|No calculation/i);

  byId.get('pilot-refresh-verification').dispatch('click');
  await controller.settled();

  const reads = calls.filter((call) => call.url === verificationUrl('task-1'));
  assert.equal(reads.length, 1, 'one explicit check read');
  assert.equal(reads[0].init.method, 'GET');
  assert.equal(reads[0].init.headers.Authorization, 'Bearer owner-secret-token');
  assert.equal(reads[0].init.headers['X-Prototype-CSRF'], undefined);

  assert.equal(byId.get('pilot-verification-state').textContent, 'COMPLETED');
  assert.match(byId.get('pilot-verification-verdict').textContent, /^Passed/);
  assert.match(byId.get('pilot-verification-expression').textContent, /2 \+ 3 \* 4/);
  assert.equal(byId.get('pilot-verification-expected').textContent, '14');
  assert.equal(byId.get('pilot-verification-observed').textContent, '14');
  assert.match(byId.get('pilot-verification-method').textContent, /bounded_integer_arithmetic\/v1/);
  assert.match(byId.get('pilot-verification-binding').textContent, /output SHA-256|response bytes/i);
  assert.match(byId.get('pilot-verification-scope').textContent, /not proof|honesty|peer approval|independently_verified=false/i);
  assert.doesNotMatch(domText(byId), /Agency-wide independent verification/i);
  assert.equal(calls.filter((call) => call.init.method === 'POST').length, 1, 'no automatic resubmit');
});

/* ------------------------- shared calculator setup ------------------------- */

async function calculatorContext(options = {}) {
  var taskOverrides = { state: 'COMPLETED', ...(options.task || {}) };
  var response = Object.prototype.hasOwnProperty.call(options, 'response') ? options.response : '14';
  var reason = options.reason || 'available';
  var sent = null;
  var currentTask = null;
  var routes = baseRoutes({
    [ENDPOINTS.calculatorTasks]: (init) => {
      sent = JSON.parse(init.body);
      currentTask = taskView({
        request_id: sent.request_id,
        thread_id: sent.thread_id,
        response: response,
        assessment: null,
        ...taskOverrides,
      });
      return jsonResponse(currentTask, { status: 202 });
    },
    [verificationUrl('task-1')]: () => {
      var defaultReceipt = calculatorReceipt({
        request_id: sent.request_id,
        response: response === null ? '' : response,
      });
      var receiptValue = options.receiptFactory
        ? options.receiptFactory(sent, currentTask)
        : options.receipt === undefined ? defaultReceipt : options.receipt;
      return jsonResponse(verificationEnvelope(currentTask, receiptValue, reason, options.envelope || {}));
    },
  });
  var ctx = await unlocked(routes);
  await ctx.controller.selectThread('thread-1');
  ctx.byId.get('pilot-calculator-expression').value = '2 + 3 * 4';
  ctx.byId.get('pilot-calculator-form').dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  return { ...ctx, routes, sent, task: currentTask };
}

/* ------------------------- verdicts and null reasons ------------------------- */

test('renders passed, failed and unable-to-verify verdicts as narrow arithmetic evidence', async () => {
  const cases = [
    {
      name: 'passed',
      response: '14',
      label: /^Passed/,
      note: /not a general proof|honesty/i,
      observed: '14',
    },
    {
      name: 'failed',
      response: '15',
      label: /^Failed/,
      note: /No pass is claimed/i,
      observed: '15',
      receiptFactory: (sent, task) => calculatorReceipt({
        request_id: sent.request_id, task_id: task.task_id, thread_id: task.thread_id,
        response: '15', status: 'failed', reason: 'mismatch', expected: '14', observed: '15',
      }),
    },
    {
      name: 'unable',
      response: 'four',
      label: /^Unable to verify/,
      note: /No pass is claimed/i,
      observed: '—',
      receiptFactory: (sent, task) => calculatorReceipt({
        request_id: sent.request_id, task_id: task.task_id, thread_id: task.thread_id,
        response: 'four', status: 'unable_to_verify', reason: 'answer_not_integer',
        expected: '14', observed: null,
      }),
    },
  ];
  for (const item of cases) {
    const ctx = await calculatorContext({
      response: item.response, receiptFactory: item.receiptFactory,
    });
    await ctx.controller.refreshVerification();
    assert.equal(ctx.byId.get('pilot-verification-state').textContent, 'COMPLETED', item.name);
    assert.match(ctx.byId.get('pilot-verification-verdict').textContent, item.label, item.name);
    assert.match(ctx.byId.get('pilot-verification-note').textContent, item.note, item.name);
    assert.equal(ctx.byId.get('pilot-verification-observed').textContent, item.observed, item.name);
    assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '14', item.name);
    assert.match(ctx.byId.get('pilot-verification-expression').textContent, /2 \+ 3 \* 4/, item.name);
    assert.match(ctx.byId.get('pilot-verification-method').textContent,
      /bounded_integer_arithmetic\/v1/, item.name);
    assert.match(ctx.byId.get('pilot-verification-binding').textContent, /output SHA-256/, item.name);
    assert.match(ctx.byId.get('pilot-verification-scope').textContent,
      /not proof|honesty|peer approval|independently_verified=false/i, item.name);
    if (item.name !== 'passed') {
      assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/, item.name);
    }
  }
});

test('null receipts render explicit not-requested, not-complete and legacy reasons, never success', async () => {
  const cases = [
    {
      reason: 'not_requested',
      task: { state: 'COMPLETED' },
      response: 'hello',
      verdict: /Not requested/,
      note: /Not requested/,
    },
    {
      reason: 'not_completed',
      task: { state: 'RUNNING' },
      response: null,
      verdict: /Not complete/,
      note: /Not complete/,
    },
    {
      reason: 'legacy_result',
      task: { state: 'COMPLETED' },
      response: 'old',
      verdict: /Legacy result/,
      note: /Legacy result.*not re-run|no stored calculation check/i,
    },
  ];
  for (const item of cases) {
    const ctx = await calculatorContext({
      task: item.task, response: item.response, reason: item.reason, receipt: null,
    });
    await ctx.controller.refreshVerification();
    assert.equal(ctx.byId.get('pilot-verification-state').textContent, item.task.state, item.reason);
    assert.match(ctx.byId.get('pilot-verification-verdict').textContent, item.verdict, item.reason);
    assert.match(ctx.byId.get('pilot-verification-note').textContent, item.note, item.reason);
    assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/, item.reason);
    assert.equal(ctx.byId.get('pilot-verification-observed').textContent, '—', item.reason);
    assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '—', item.reason);
  }
});

/* ------------------------- malformed and misbound payloads ------------------------- */

test('malformed, wrong-ID or hash-misbound envelopes are unavailable, never a passed badge', async () => {
  const ctx = await calculatorContext();
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);

  const requestId = ctx.sent.request_id;
  const baseReceipt = calculatorReceipt({ request_id: requestId, response: '14' });
  const baseEnvelope = verificationEnvelope(ctx.task, baseReceipt);
  const missingReason = { ...baseEnvelope };
  delete missingReason.reason;
  const variants = [
    ['envelope extra key', { ...baseEnvelope, extra: true }],
    ['envelope missing key', missingReason],
    ['envelope task id', { ...baseEnvelope, task_id: 'task-other' }],
    ['envelope request id', { ...baseEnvelope, request_id: 'req-other' }],
    ['envelope thread id', { ...baseEnvelope, thread_id: 'thread-other' }],
    ['envelope execution state', { ...baseEnvelope, execution_state: 'RUNNING' }],
    ['available with null receipt', { ...baseEnvelope, receipt: null }],
    ['null reason with receipt', { ...baseEnvelope, reason: 'not_requested', receipt: baseReceipt }],
    ['receipt extra key', { ...baseEnvelope, receipt: { ...baseReceipt, extra: true } }],
    ['receipt task id', { ...baseEnvelope, receipt: { ...baseReceipt, task_id: 'task-other' } }],
    ['receipt request id', { ...baseEnvelope, receipt: { ...baseReceipt, request_id: 'req-other' } }],
    ['receipt thread id', { ...baseEnvelope, receipt: { ...baseReceipt, thread_id: 'thread-other' } }],
    ['criteria hash mismatch', { ...baseEnvelope, receipt: { ...baseReceipt, criteria_sha256: 'a'.repeat(64) } }],
    ['output hash mismatch', { ...baseEnvelope, receipt: { ...baseReceipt, output_sha256: 'b'.repeat(64) } }],
    ['contradictory passed', { ...baseEnvelope, receipt: { ...baseReceipt, observed: '15' } }],
    ['contradictory failed', { ...baseEnvelope, receipt: { ...baseReceipt, status: 'failed', reason: 'mismatch', observed: '14' } }],
    ['numeric expected', { ...baseEnvelope, receipt: { ...baseReceipt, expected: 14 } }],
    ['boolean observed', { ...baseEnvelope, receipt: { ...baseReceipt, observed: true } }],
    ['unable with observed', { ...baseEnvelope, receipt: { ...baseReceipt, status: 'unable_to_verify', reason: 'answer_not_integer', observed: '14' } }],
    ['bad schema', { ...baseEnvelope, receipt: { ...baseReceipt, schema_version: 'calculator.v2' } }],
    ['bad expression', { ...baseEnvelope, receipt: { ...baseReceipt, expression: '2 / 3' } }],
    ['not completed on completed', { ...baseEnvelope, reason: 'not_completed', receipt: null }],
    ['legacy on running', { ...baseEnvelope, reason: 'legacy_result', receipt: null, execution_state: 'RUNNING' }],
    ['leading zero expected', { ...baseEnvelope, receipt: { ...baseReceipt, expected: '00014' } }],
    ['wrong response binding', {
      ...baseEnvelope,
      receipt: calculatorReceipt({ request_id: requestId, response: '15' }),
    }],
  ];

  for (const [name, payload] of variants) {
    ctx.routes[verificationUrl('task-1')] = jsonResponse(payload);
    assert.equal(await ctx.controller.refreshVerification(), null, name);
    assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/, name);
    assert.match(ctx.byId.get('pilot-verification-note').textContent,
      /unavailable|malformed|did not bind|No success|does not bind/i, name);
    assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '—', name);
    assert.equal(ctx.byId.get('pilot-verification-observed').textContent, '—', name);
  }

  ctx.routes[verificationUrl('task-1')] = jsonResponse(baseEnvelope);
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/,
    'a valid envelope remains usable after rejected payloads');
});

/* ------------------------- privacy, fencing and stale completions ------------------------- */

test('lock, 401, task/thread changes and stale completions clear private receipt evidence', async () => {
  const ctx = await calculatorContext();
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);

  const held = deferred();
  ctx.routes[verificationUrl('task-1')] = () => held.promise;
  const pending = ctx.controller.refreshVerification();
  await flush();
  assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/,
    'old evidence is cleared before a refresh replaces it');
  assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '—');
  held.resolve(jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, response: '14' }),
  )));
  await pending;
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);

  const late = deferred();
  ctx.routes[verificationUrl('task-1')] = () => late.promise;
  const lateRead = ctx.controller.refreshVerification();
  await flush();
  ctx.controller.lock();
  const lockedSnapshot = domText(ctx.byId);
  late.resolve(jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, response: '14' }),
  )));
  await lateRead;
  assert.equal(ctx.controller.state().locked, true);
  assert.equal(domText(ctx.byId), lockedSnapshot, 'late read cannot repaint after lock');
  assert.equal(ctx.byId.get('pilot-verification-verdict').textContent, '—');
  assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '—');

  await ctx.controller.unlock('new-owner-token');
  await ctx.controller.selectThread('thread-1');
  ctx.routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(ctx.task);
  await ctx.controller.selectTask('task-1');
  ctx.routes[verificationUrl('task-1')] = jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, response: '14' }),
  ));
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);

  ctx.routes[verificationUrl('task-1')] = jsonResponse({}, { ok: false, status: 401 });
  await ctx.controller.refreshVerification();
  assert.equal(ctx.controller.state().locked, true);
  assert.equal(ctx.controller.state().hasBearer, false);
  assert.equal(ctx.byId.get('pilot-verification-verdict').textContent, '—');
  assert.equal(ctx.byId.get('pilot-verification-expected').textContent, '—');
});

test('overlapping reads cannot repaint stale evidence across newer reads, ABA task selection or thread changes', async () => {
  const ctx = await calculatorContext();
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);

  const oldReadReply = deferred();
  ctx.routes[verificationUrl('task-1')] = () => oldReadReply.promise;
  const oldRead = ctx.controller.refreshVerification();
  await flush();
  ctx.routes[verificationUrl('task-1')] = jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, expression: '2 + 3 * 4', response: '14' }),
  ));
  await ctx.controller.refreshVerification();
  assert.match(ctx.byId.get('pilot-verification-expression').textContent, /2 \+ 3 \* 4/);
  oldReadReply.resolve(jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, expression: '2 * 7', response: '14' }),
  )));
  await oldRead;
  assert.match(ctx.byId.get('pilot-verification-expression').textContent, /2 \+ 3 \* 4/);
  assert.doesNotMatch(ctx.byId.get('pilot-verification-expression').textContent, /2 \* 7/);

  const abaReply = deferred();
  ctx.routes[verificationUrl('task-1')] = () => abaReply.promise;
  const abaRead = ctx.controller.refreshVerification();
  await flush();
  ctx.routes[`${ENDPOINTS.tasks}/task-2`] = jsonResponse(taskView({
    task_id: 'task-2', request_id: 'req-2', state: 'RUNNING',
  }));
  await ctx.controller.selectTask('task-2');
  ctx.routes[`${ENDPOINTS.tasks}/task-1`] = jsonResponse(ctx.task);
  await ctx.controller.selectTask('task-1');
  abaReply.resolve(jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, expression: '2 * 7', response: '14' }),
  )));
  await abaRead;
  assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);
  assert.match(ctx.byId.get('pilot-verification-note').textContent, /not read|No calculation|Refresh check/i);
  assert.doesNotMatch(ctx.byId.get('pilot-verification-expression').textContent, /2 \* 7/);

  ctx.routes[`${ENDPOINTS.threads}/thread-2/messages`] = jsonResponse({ messages: [] });
  const threadReply = deferred();
  ctx.routes[verificationUrl('task-1')] = () => threadReply.promise;
  const threadRead = ctx.controller.refreshVerification();
  await flush();
  await ctx.controller.selectThread('thread-2');
  threadReply.resolve(jsonResponse(verificationEnvelope(
    ctx.task, calculatorReceipt({ request_id: ctx.sent.request_id, expression: '2 * 7', response: '14' }),
  )));
  await threadRead;
  assert.doesNotMatch(ctx.byId.get('pilot-verification-verdict').textContent, /^Passed/);
  assert.doesNotMatch(ctx.byId.get('pilot-verification-expression').textContent, /2 \* 7/);
});

/* ------------------------- retained submission identity and drafts ------------------------- */

test('retains one calculator request ID across ambiguous outcomes and preserves newer drafts', async () => {
  let posts = 0;
  let mode = 'network';
  let lastSent = null;
  const deferredReply = deferred();
  const routes = baseRoutes({
    [ENDPOINTS.calculatorTasks]: (init) => {
      posts += 1;
      lastSent = JSON.parse(init.body);
      if (mode === 'network') throw new Error('connection reset');
      if (mode === 'malformed') {
        return jsonResponse(taskView({
          request_id: 'wrong-request', thread_id: lastSent.thread_id, state: 'RUNNING',
        }), { status: 202 });
      }
      if (mode === 'deferred') return deferredReply.promise;
      return jsonResponse(taskView({
        request_id: lastSent.request_id, thread_id: lastSent.thread_id, state: 'RUNNING',
      }), { status: 202 });
    },
  });
  const ctx = await unlocked(routes);
  await ctx.controller.selectThread('thread-1');
  const input = ctx.byId.get('pilot-calculator-expression');
  const form = ctx.byId.get('pilot-calculator-form');
  const note = ctx.byId.get('pilot-calculator-note');

  input.value = '1 + 1';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 1);
  assert.equal(input.value, '1 + 1', 'failed request retains the draft');
  assert.match(note.textContent, /unknown|retained/i);
  assert.equal(posts, 1, 'ambiguous outcome is not retried automatically');
  const firstId = lastSent.request_id;

  mode = 'ok';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 2);
  assert.equal(lastSent.request_id, firstId, 'unchanged thread/expression reuses the retained ID');
  assert.equal(input.value, '');

  input.value = '2 + 2';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 3);
  assert.notEqual(lastSent.request_id, firstId, 'changed expression gets a new ID');
  const secondId = lastSent.request_id;

  routes[`${ENDPOINTS.threads}/thread-2/messages`] = jsonResponse({ messages: [] });
  await ctx.controller.selectThread('thread-2');
  input.value = '2 + 2';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 4);
  assert.equal(lastSent.thread_id, 'thread-2');
  assert.notEqual(lastSent.request_id, secondId, 'changed thread gets a new ID');

  await ctx.controller.selectThread('thread-1');
  input.value = '3 + 3';
  mode = 'malformed';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 5);
  const thirdId = lastSent.request_id;
  assert.equal(input.value, '3 + 3', 'malformed receipt retains the draft');
  assert.match(note.textContent, /malformed|unknown|retained/i);
  mode = 'ok';
  form.dispatch('submit', { preventDefault() {} });
  await ctx.controller.settled();
  assert.equal(posts, 6);
  assert.equal(lastSent.request_id, thirdId, 'manual retry after malformed receipt reuses the ID');

  mode = 'deferred';
  input.value = '4 + 4';
  form.dispatch('submit', { preventDefault() {} });
  await flush();
  assert.equal(posts, 7);
  input.value = 'new draft';
  deferredReply.resolve(jsonResponse(taskView({
    request_id: lastSent.request_id, thread_id: lastSent.thread_id, state: 'RUNNING',
  }), { status: 202 }));
  await ctx.controller.settled();
  assert.equal(input.value, 'new draft', 'a delayed receipt must not erase a newer draft');
});

/* ------------------------- parser bounds and ordinary-task compatibility ------------------------- */

test('rejects unsupported expressions and bounds before any calculator write', async () => {
  const invalid = [
    '1 / 2', '1.5', '1e3', '2 ** 3', '01 + 1', '1 +\n2', '1 + 2;', '1 + 2 abc',
    'é + 1', '1 + ', '(1 + 2', '1 + 2)', '1 2',
    '1+'.repeat(60) + '1',
    Array(33).fill('1').join('+'),
    '('.repeat(13) + '1' + ')'.repeat(13),
    '1000000000000001',
    '1000000000000000 + 1',
    '1'.repeat(121),
  ];
  for (const expression of invalid) {
    assert.equal(ui.validateExpression(expression), false, JSON.stringify(expression));
  }
  const valid = [
    '0', '2 + 3 * 4', '(2 + 3) * -4', ' 1\t+\t2 ', '-0',
    '1000000000000000', '-1000000000000000', '1+1+1',
  ];
  for (const expression of valid) {
    assert.equal(ui.validateExpression(expression), true, JSON.stringify(expression));
  }

  const routes = baseRoutes({
    [ENDPOINTS.calculatorTasks]: () => { throw new Error('invalid expression must not be posted'); },
  });
  const ctx = await unlocked(routes);
  await ctx.controller.selectThread('thread-1');
  const input = ctx.byId.get('pilot-calculator-expression');
  for (const expression of ['1 / 2', '01 + 1', '(1 + 2', '1000000000000000 + 1']) {
    input.value = expression;
    ctx.byId.get('pilot-calculator-form').dispatch('submit', { preventDefault() {} });
    await ctx.controller.settled();
  }
  assert.equal(ctx.calls.filter((call) => call.url === ENDPOINTS.calculatorTasks).length, 0);
  assert.match(ctx.byId.get('pilot-calculator-note').textContent,
    /supported integer expression|magnitude|nesting|tokens/i);
});

test('ordinary messages still use the legacy task endpoint and never trigger a verification read', async () => {
  let posted = null;
  const routes = baseRoutes({
    [ENDPOINTS.tasks]: (init) => {
      posted = JSON.parse(init.body);
      return jsonResponse(taskView({
        request_id: posted.request_id, thread_id: posted.thread_id, state: 'RUNNING',
      }), { status: 202 });
    },
  });
  const ctx = await unlocked(routes);
  await ctx.controller.selectThread('thread-1');
  assert.equal(await ctx.controller.submitTask('ordinary work'), true);
  assert.deepEqual(Object.keys(posted).sort(), ['message', 'request_id', 'thread_id']);
  assert.equal(posted.message, 'ordinary work');
  assert.equal(ctx.calls.some((call) => call.url === ENDPOINTS.calculatorTasks), false);
  assert.equal(ctx.calls.some((call) => call.url.includes('/verification')), false);
});

/* ------------------------- strict enum own-membership regressions ------------------------- */

test('rejects inherited object prototype names in verification enum fields and keeps valid enums', () => {
  const baseEnvelope = {
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    execution_state: 'COMPLETED',
    receipt: null,
    reason: 'not_requested',
  };
  const inherited = ['toString', 'constructor', '__proto__', 'hasOwnProperty'];

  assert.equal(ui.normalizeVerification({
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    execution_state: 'COMPLETED',
    receipt: null,
    reason: 'toString',
  }), null, 'exact reproduced verification bug');

  for (const reason of inherited) {
    assert.equal(ui.normalizeVerification({ ...baseEnvelope, reason }), null, reason);
  }
  for (const executionState of inherited) {
    assert.equal(ui.normalizeVerification({
      ...baseEnvelope, execution_state: executionState,
    }), null, executionState);
  }

  for (const executionState of ['ADMITTED', 'RUNNING', 'COMPLETED', 'FAILED', 'DENIED', 'CANCELLED', 'UNCERTAIN']) {
    assert.equal(ui.normalizeVerification({
      ...baseEnvelope, execution_state: executionState,
    }) !== null, true, executionState);
  }
  assert.equal(ui.normalizeVerification({
    ...baseEnvelope, execution_state: 'RUNNING', reason: 'not_completed',
  }) !== null, true, 'not_completed remains valid for a non-completed task');
  assert.equal(ui.normalizeVerification({
    ...baseEnvelope, execution_state: 'COMPLETED', reason: 'legacy_result',
  }) !== null, true, 'legacy_result remains valid for a completed task');
  const validReceipt = calculatorReceipt({
    request_id: 'req-1', expression: '1+1', response: '2', expected: '2', observed: '2',
  });
  assert.equal(ui.normalizeVerification({
    ...baseEnvelope, reason: 'available', receipt: validReceipt,
  }) !== null, true, 'available remains valid with a valid receipt');
});

test('rejects inherited object prototype names in calculator receipt enums and keeps valid enums', () => {
  const baseReceipt = {
    schema_version: 'calculator.v1',
    method: 'bounded_integer_arithmetic/v1',
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    expression: '1+1',
    criteria_sha256: 'a'.repeat(64),
    output_sha256: 'b'.repeat(64),
    status: 'passed',
    reason: 'matches',
    expected: '2',
    observed: '2',
  };
  const inherited = ['toString', 'constructor', '__proto__', 'hasOwnProperty'];

  assert.equal(ui.normalizeCalculatorReceipt({
    schema_version: 'calculator.v1',
    method: 'bounded_integer_arithmetic/v1',
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    expression: '1+1',
    criteria_sha256: 'a'.repeat(64),
    output_sha256: 'b'.repeat(64),
    status: 'constructor',
    reason: 'answer_not_integer',
    expected: '2',
    observed: null,
  }), null, 'exact reproduced receipt bug');

  for (const status of inherited) {
    assert.equal(ui.normalizeCalculatorReceipt({ ...baseReceipt, status }), null, status);
  }
  for (const reason of inherited) {
    assert.equal(ui.normalizeCalculatorReceipt({ ...baseReceipt, reason }), null, reason);
  }

  const validCases = [
    { status: 'passed', reason: 'matches', expected: '2', observed: '2' },
    { status: 'failed', reason: 'mismatch', expected: '2', observed: '3' },
    { status: 'unable_to_verify', reason: 'answer_not_integer', expected: '2', observed: null },
  ];
  for (const item of validCases) {
    assert.equal(ui.normalizeCalculatorReceipt({
      ...baseReceipt, ...item,
    }) !== null, true, item.status);
  }
});

test('rejects non-string JSON enum values in receipt and verification guards', () => {
  const baseReceipt = {
    schema_version: 'calculator.v1',
    method: 'bounded_integer_arithmetic/v1',
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    expression: '1+1',
    criteria_sha256: 'a'.repeat(64),
    output_sha256: 'b'.repeat(64),
    status: 'passed',
    reason: 'matches',
    expected: '2',
    observed: '2',
  };
  const baseEnvelope = {
    task_id: 'task-1',
    request_id: 'req-1',
    thread_id: 'thread-1',
    execution_state: 'COMPLETED',
    receipt: null,
    reason: 'not_requested',
  };
  const nonStrings = [
    [], {}, null, true, false, 0, 1,
    ['unable_to_verify'], ['answer_not_integer'], ['not_requested'], ['COMPLETED'],
  ];
  for (const value of nonStrings) {
    const label = JSON.stringify(value);
    assert.equal(ui.normalizeCalculatorReceipt({ ...baseReceipt, status: value }), null, 'receipt status ' + label);
    assert.equal(ui.normalizeCalculatorReceipt({ ...baseReceipt, reason: value }), null, 'receipt reason ' + label);
    assert.equal(ui.normalizeVerification({ ...baseEnvelope, execution_state: value }), null, 'execution state ' + label);
    assert.equal(ui.normalizeVerification({ ...baseEnvelope, reason: value }), null, 'verification reason ' + label);
  }
  assert.equal(ui.normalizeCalculatorReceipt({
    ...baseReceipt, status: ['unable_to_verify'], reason: 'answer_not_integer', observed: null,
  }), null, 'exact reproduced receipt status array');
  assert.equal(ui.normalizeVerification({ ...baseEnvelope, reason: ['not_requested'] }), null,
    'exact reproduced verification reason array');
  assert.equal(ui.normalizeVerification({ ...baseEnvelope, execution_state: ['COMPLETED'] }), null,
    'reproduced execution-state array');
  for (const inherited of ['toString', 'constructor', '__proto__', 'hasOwnProperty']) {
    assert.equal(ui.normalizeVerification({ ...baseEnvelope, execution_state: inherited }), null, inherited);
  }
  assert.notEqual(ui.normalizeCalculatorReceipt(baseReceipt), null, 'valid receipt strings remain accepted');
  assert.notEqual(ui.normalizeVerification(baseEnvelope), null, 'valid verification strings remain accepted');
});
