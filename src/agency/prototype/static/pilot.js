'use strict';
/**
 * The Agency — single-owner limited-autonomy pilot UI.
 *
 * Frozen contract: docs/SPEC_OWNER_PILOT_M2_M3_20261019.md (UI section).
 *
 * Security rules enforced here:
 *  - The owner bearer lives only in this closure's memory. It is never put in
 *    storage, cookies, the URL, static source, or console output.
 *  - Every pilot call is a same-origin fetch. POSTs additionally carry the
 *    X-Prototype-CSRF token bootstrapped from GET /api/session.
 *  - Server/user text is rendered with textContent only.
 *  - A completed body is displayed only after the exact ExecutionAssessment
 *    shape, task id, terminal state and SHA-256 output bytes bind together.
 *  - A 401 or explicit lock clears the bearer, CSRF token, polling and all
 *    owner-sensitive DOM.
 */
(function () {
  var ENDPOINTS = Object.freeze({
    session: '/api/session',
    capabilities: '/api/pilot/capabilities',
    threads: '/api/pilot/threads',
    tasks: '/api/pilot/tasks'
  });
  var CSRF_HEADER = 'X-Prototype-CSRF';
  var AUTH_HEADER = 'Authorization';
  var BEARER_PREFIX = 'Bearer ';
  var DEFAULT_POLL_MS = 1500;
  var DEFAULT_MAX_POLL_MS = 135000;
  var REQUEST_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
  var DIGEST_RE = /^[0-9a-f]{64}$/;
  var FIXED_TOOLS = Object.freeze(['calculator', 'utc_time', 'web_search']);
  var TASK_STATES = Object.freeze({
    ADMITTED: true, RUNNING: true, COMPLETED: true, FAILED: true,
    DENIED: true, CANCELLED: true, UNCERTAIN: true
  });
  var ACTIVE_STATES = Object.freeze({ ADMITTED: true, RUNNING: true });
  var TERMINAL_STATES = Object.freeze({
    COMPLETED: true, FAILED: true, DENIED: true, CANCELLED: true, UNCERTAIN: true
  });
  var CHECK_CRITERIA = Object.freeze(['completeness', 'accuracy', 'safety']);
  var TERMINAL_EXECUTION = Object.freeze({
    completed: true, failed: true, cancelled: true, timeout: true
  });
  var VERIFICATION_STATES = Object.freeze({
    not_performed: true, passed: true, failed: true, needs_review: true
  });
  var RISK_STATES = Object.freeze({
    not_assessed: true, assessed: true, partially_assessed: true
  });
  var CHECK_STATUS = Object.freeze({
    not_performed: true, passed: true, failed: true, needs_review: true
  });
  var CHECK_METHOD = Object.freeze({
    not_performed: true,
    'legacy_keyword_coverage/v1': true,
    'legacy_denylist/v1': true
  });
  var CHECK_REASON = Object.freeze({
    tool_path_unchecked: true,
    isolated_path_unchecked: true,
    execution_not_completed: true,
    no_trusted_accuracy_oracle: true,
    heuristic_passed: true,
    heuristic_failed: true
  });
  var UNKNOWN = '\u2014';

  /* ------------------------------------------------------------------ *
   * Generic helpers
   * ------------------------------------------------------------------ */

  function isObject(value) {
    return value !== null && typeof value === 'object' && !Array.isArray(value);
  }

  function exactKeys(value, keys) {
    if (!isObject(value)) return false;
    var actual = Object.keys(value);
    if (actual.length !== keys.length) return false;
    for (var i = 0; i < keys.length; i++) {
      if (!Object.prototype.hasOwnProperty.call(value, keys[i])) return false;
    }
    return true;
  }

  function nonEmptyString(value) {
    return typeof value === 'string' && value.length > 0;
  }

  function nonNegativeInt(value) {
    return typeof value === 'number' && isFinite(value) && Math.floor(value) === value && value >= 0;
  }

  function displayValue(value) {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' && isFinite(value)) return String(value);
    if (typeof value === 'boolean') return String(value);
    return '';
  }

  function clearNode(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  function setText(node, text) {
    node.textContent = typeof text === 'string' ? text : '';
  }

  function readJsonSafe(response) {
    if (!response || typeof response.json !== 'function') return Promise.resolve(null);
    return response.json().catch(function () { return null; });
  }

  function newRequestId() {
    var bytes = new Uint8Array(16);
    var i;
    if (globalThis.crypto && typeof globalThis.crypto.getRandomValues === 'function') {
      globalThis.crypto.getRandomValues(bytes);
    } else {
      for (i = 0; i < bytes.length; i++) bytes[i] = Math.floor(Math.random() * 256);
    }
    var hex = '';
    for (i = 0; i < bytes.length; i++) hex += bytes[i].toString(16).padStart(2, '0');
    return 'ui-' + hex;
  }

  async function sha256Hex(text) {
    if (typeof text !== 'string' || text.trim() === '') return null;
    try {
      var bytes = new TextEncoder().encode(text);
      // TextEncoder replaces lone surrogates; never bind a replacement digest.
      if (new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes) !== text) {
        return null;
      }
      if (!globalThis.crypto || !globalThis.crypto.subtle ||
          typeof globalThis.crypto.subtle.digest !== 'function') return null;
      var digest = await globalThis.crypto.subtle.digest('SHA-256', bytes);
      return Array.from(new Uint8Array(digest), function (byte) {
        return byte.toString(16).padStart(2, '0');
      }).join('');
    } catch (err) {
      return null;
    }
  }

  /* ------------------------------------------------------------------ *
   * Wire model validation (extra='forbid' mirrors the frozen Pydantic models)
   * ------------------------------------------------------------------ */

  function normalizeCapabilities(payload) {
    if (!exactKeys(payload, ['enabled', 'ready', 'mode', 'tools', 'disabled', 'limits',
        'provider_ready', 'uncertainty_blocked'])) return null;
    if (typeof payload.enabled !== 'boolean' || typeof payload.ready !== 'boolean' ||
        payload.mode !== 'single_owner_pilot' || typeof payload.provider_ready !== 'boolean' ||
        typeof payload.uncertainty_blocked !== 'boolean') return null;
    if (!Array.isArray(payload.tools) || !Array.isArray(payload.disabled)) return null;
    if (!payload.tools.every(nonEmptyString) || !payload.disabled.every(nonEmptyString)) return null;
    if (!exactKeys(payload.limits, ['requests_per_hour', 'requests_per_day',
        'model_calls_per_hour', 'model_calls_per_day', 'model_calls_per_request',
        'tool_calls_per_request'])) return null;
    var limits = payload.limits;
    for (var key in limits) {
      if (!Object.prototype.hasOwnProperty.call(limits, key) || !nonNegativeInt(limits[key])) return null;
    }
    return payload;
  }

  function normalizeThread(value) {
    if (!exactKeys(value, ['id', 'title', 'parent_thread_id', 'state', 'created_at', 'updated_at'])) {
      return null;
    }
    if (!nonEmptyString(value.id) || typeof value.title !== 'string' ||
        (value.parent_thread_id !== null && !nonEmptyString(value.parent_thread_id)) ||
        typeof value.state !== 'string' || typeof value.created_at !== 'string' ||
        typeof value.updated_at !== 'string') return null;
    return value;
  }

  function normalizeThreadList(payload) {
    if (!isObject(payload) || !Array.isArray(payload.threads)) return null;
    var out = [];
    payload.threads.forEach(function (item) {
      var view = normalizeThread(item);
      if (view) out.push(view);
    });
    return out;
  }

  function normalizeMessage(value) {
    if (!exactKeys(value, ['id', 'thread_id', 'role', 'content', 'created_at'])) return null;
    if (!nonEmptyString(value.id) || !nonEmptyString(value.thread_id) ||
        (value.role !== 'user' && value.role !== 'assistant' && value.role !== 'system') ||
        typeof value.content !== 'string' || typeof value.created_at !== 'string') return null;
    return value;
  }

  function normalizeMessageList(payload, threadId) {
    if (!isObject(payload) || !Array.isArray(payload.messages)) return null;
    var out = [];
    payload.messages.forEach(function (item) {
      var view = normalizeMessage(item);
      if (view && view.thread_id === threadId) out.push(view);
    });
    return out;
  }

  function normalizeUsage(value) {
    if (!exactKeys(value, ['model_attempts', 'tool_attempts'])) return null;
    if (!nonNegativeInt(value.model_attempts) || !nonNegativeInt(value.tool_attempts)) return null;
    return value;
  }

  function normalizeTask(value) {
    if (!exactKeys(value, ['request_id', 'task_id', 'thread_id', 'state', 'replayed',
        'cancel_requested', 'response', 'assessment', 'error_code', 'uncertain', 'usage',
        'created_at', 'updated_at'])) return null;
    if (!nonEmptyString(value.request_id) || !REQUEST_ID_RE.test(value.request_id)) return null;
    if (!nonEmptyString(value.task_id) || !nonEmptyString(value.thread_id)) return null;
    if (!TASK_STATES[value.state]) return null;
    if (typeof value.replayed !== 'boolean' || typeof value.cancel_requested !== 'boolean' ||
        typeof value.uncertain !== 'boolean') return null;
    if (value.response !== null && typeof value.response !== 'string') return null;
    if (value.assessment !== null && !isObject(value.assessment)) return null;
    if (value.error_code !== null && !nonEmptyString(value.error_code)) return null;
    if (!normalizeUsage(value.usage)) return null;
    if (typeof value.created_at !== 'string' || typeof value.updated_at !== 'string') return null;
    return value;
  }

  function normalizeTaskList(payload) {
    if (!isObject(payload) || !Array.isArray(payload.tasks)) return null;
    var out = [];
    payload.tasks.forEach(function (item) {
      var view = normalizeTask(item);
      if (view) out.push(view);
    });
    return out;
  }

  function normalizeReceipt(value, requestId) {
    if (!exactKeys(value, ['request_id', 'state', 'replayed', 'resource_id'])) return null;
    if (value.request_id !== requestId || typeof value.state !== 'string' ||
        typeof value.replayed !== 'boolean') return null;
    if (value.resource_id !== null && !nonEmptyString(value.resource_id)) return null;
    return value;
  }

  function normalizeCancelPayload(payload) {
    if (!isObject(payload) || Object.keys(payload).length !== 1 ||
        !Object.prototype.hasOwnProperty.call(payload, 'task')) return null;
    return normalizeTask(payload.task);
  }

  /* ------------------------------------------------------------------ *
   * Actual ExecutionAssessment shape (tool path allowed deliberately)
   * ------------------------------------------------------------------ */

  function validCheck(check, criterion) {
    if (!exactKeys(check, ['criterion', 'status', 'method', 'reason', 'score'])) return false;
    if (check.criterion !== criterion) return false;
    if (!CHECK_STATUS[check.status] || !CHECK_METHOD[check.method] || !CHECK_REASON[check.reason]) {
      return false;
    }
    if (check.score !== null && (typeof check.score !== 'number' || !isFinite(check.score) ||
        check.score < 0 || check.score > 1)) return false;

    if (check.status === 'not_performed') {
      if (check.method !== 'not_performed' || check.score !== null) return false;
      if (check.reason === 'heuristic_passed' || check.reason === 'heuristic_failed') return false;
      if (check.reason === 'no_trusted_accuracy_oracle' && criterion !== 'accuracy') return false;
      return true;
    }
    if (check.status !== 'passed' && check.status !== 'failed') return false;
    var method = criterion === 'completeness'
      ? 'legacy_keyword_coverage/v1'
      : criterion === 'safety' ? 'legacy_denylist/v1' : null;
    if (method === null || check.method !== method || typeof check.score !== 'number') return false;
    var passed = check.status === 'passed';
    if (check.reason !== (passed ? 'heuristic_passed' : 'heuristic_failed')) return false;
    if (passed && check.score !== 1) return false;
    if (!passed && check.score === 1) return false;
    if (criterion === 'safety' && !passed && check.score !== 0) return false;
    return true;
  }

  function validRisk(risk) {
    if (!exactKeys(risk, ['status', 'method', 'category', 'finding_id'])) return false;
    if (risk.status === 'not_assessed') {
      return risk.method === 'not_performed' && risk.category === null && risk.finding_id === null;
    }
    if (risk.status !== 'assessed') return false;
    return risk.method === 'legacy_risk_engine/v1' &&
      ['critical', 'high', 'medium', 'low', 'negligible'].indexOf(risk.category) >= 0 &&
      nonEmptyString(risk.finding_id);
  }

  function aggregateVerification(checks) {
    var statuses = checks.map(function (check) { return check.status; });
    if (statuses.indexOf('failed') >= 0) return 'failed';
    if (statuses.length === 0) return 'not_performed';
    var allNotPerformed = statuses.every(function (status) { return status === 'not_performed'; });
    if (allNotPerformed) return 'not_performed';
    if (statuses.indexOf('needs_review') >= 0 || statuses.indexOf('not_performed') >= 0) {
      return 'needs_review';
    }
    return 'passed';
  }

  function aggregateRisk(subtasks) {
    var assessed = subtasks.filter(function (subtask) {
      return subtask.risk.status === 'assessed';
    }).length;
    if (assessed === 0) return 'not_assessed';
    return assessed === subtasks.length ? 'assessed' : 'partially_assessed';
  }

  function validSubtask(subtask) {
    if (!exactKeys(subtask, ['subtask_id', 'path', 'execution_status', 'output_sha256',
        'verification', 'checks', 'risk'])) return false;
    if (!nonEmptyString(subtask.subtask_id) ||
        (subtask.path !== 'classic' && subtask.path !== 'tool' && subtask.path !== 'isolated')) {
      return false;
    }
    if (subtask.path === 'tool' && subtask.subtask_id !== 'tool_loop') return false;
    if (subtask.path === 'isolated' && subtask.subtask_id !== 'isolated') return false;
    if (!TERMINAL_EXECUTION[subtask.execution_status]) return false;
    if (typeof subtask.output_sha256 !== 'string' || !DIGEST_RE.test(subtask.output_sha256)) return false;
    if (!VERIFICATION_STATES[subtask.verification]) return false;
    if (!Array.isArray(subtask.checks) || subtask.checks.length !== CHECK_CRITERIA.length) return false;
    for (var i = 0; i < CHECK_CRITERIA.length; i++) {
      if (!validCheck(subtask.checks[i], CHECK_CRITERIA[i])) return false;
    }
    if (aggregateVerification(subtask.checks) !== subtask.verification) return false;
    if (!validRisk(subtask.risk)) return false;

    if (subtask.path === 'classic' && subtask.execution_status === 'completed') {
      if (subtask.risk.status !== 'assessed') return false;
      for (var j = 0; j < subtask.checks.length; j++) {
        var check = subtask.checks[j];
        if (check.criterion === 'accuracy') {
          if (check.reason !== 'no_trusted_accuracy_oracle') return false;
        } else if (check.status !== 'passed' && check.status !== 'failed') {
          return false;
        }
      }
      return true;
    }

    var reason = subtask.execution_status !== 'completed'
      ? 'execution_not_completed'
      : subtask.path === 'isolated' ? 'isolated_path_unchecked' : 'tool_path_unchecked';
    if (subtask.risk.status !== 'not_assessed') return false;
    return subtask.checks.every(function (item) {
      return item.status === 'not_performed' && item.reason === reason;
    });
  }

  function validAssessment(report) {
    if (!exactKeys(report, ['schema_version', 'task_id', 'execution_status', 'output_sha256',
        'verification', 'risk_status', 'independently_verified', 'subtasks'])) return false;
    if (report.schema_version !== 'a2.v1' || !nonEmptyString(report.task_id)) return false;
    if (!TERMINAL_EXECUTION[report.execution_status]) return false;
    if (typeof report.output_sha256 !== 'string' || !DIGEST_RE.test(report.output_sha256)) return false;
    if (!VERIFICATION_STATES[report.verification] || !RISK_STATES[report.risk_status]) return false;
    if (report.independently_verified !== false) return false;
    if (!Array.isArray(report.subtasks)) return false;
    for (var i = 0; i < report.subtasks.length; i++) {
      if (!validSubtask(report.subtasks[i])) return false;
    }
    if (report.subtasks.length === 0 && report.execution_status !== 'failed') return false;
    if (report.execution_status === 'completed' && report.subtasks.some(function (subtask) {
      return subtask.execution_status !== 'completed';
    })) return false;
    var checks = [];
    report.subtasks.forEach(function (subtask) {
      checks = checks.concat(subtask.checks);
    });
    if (aggregateVerification(checks) !== report.verification) return false;
    if (aggregateRisk(report.subtasks) !== report.risk_status) return false;
    return true;
  }

  function assessmentCheckText(report) {
    var checks = report.verification.replace(/_/g, ' ');
    var risk = report.risk_status.replace(/_/g, ' ');
    var heuristic = report.subtasks.some(function (subtask) {
      return subtask.checks.some(function (check) { return check.method !== 'not_performed'; });
    });
    var text = 'Independent check: checks ' + checks + '; risk ' + risk + '.';
    if (heuristic) text += ' Heuristic checks only; accuracy has no trusted oracle.';
    text += ' No independent verification or authorization; this is not a safety clearance.';
    return text;
  }

  /** Bind one task view to the actual ExecutionAssessment shape and bytes. */
  async function bindAssessment(report, task, body) {
    var unavailable = 'Independent check unavailable: the assessment is missing, malformed, or does not bind to this task and exact output bytes. Result text withheld.';
    if (!isObject(task) || task.state !== 'COMPLETED' || task.uncertain !== false) {
      return { bound: false, notice: unavailable };
    }
    if (typeof body !== 'string' || body.trim() === '') {
      return { bound: false, notice: unavailable };
    }
    if (!isObject(report) || !validAssessment(report)) {
      return { bound: false, notice: unavailable };
    }
    if (report.task_id !== task.task_id) return { bound: false, notice: unavailable };
    if (report.execution_status !== 'completed') return { bound: false, notice: unavailable };
    var digest = await sha256Hex(body);
    if (!digest || report.output_sha256 !== digest) return { bound: false, notice: unavailable };
    if (report.subtasks.length === 1 && report.subtasks[0].output_sha256 !== digest) {
      return { bound: false, notice: unavailable };
    }
    return {
      bound: true,
      execution: 'Execution: completed (task ' + task.task_id + '). Response bytes bound to the assessment digest.',
      check: assessmentCheckText(report)
    };
  }

  /* ------------------------------------------------------------------ *
   * Controller
   * ------------------------------------------------------------------ */

  function createController(options) {
    var doc = options.document;
    var fetchFn = options.fetch;
    var pollIntervalMs = typeof options.pollIntervalMs === 'number'
      ? options.pollIntervalMs : DEFAULT_POLL_MS;
    var maxPollMs = typeof options.maxPollMs === 'number'
      ? options.maxPollMs : DEFAULT_MAX_POLL_MS;
    var scheduleTimer = typeof options.setTimeout === 'function'
      ? options.setTimeout : (typeof setTimeout === 'function' ? setTimeout : null);
    var cancelTimer = typeof options.clearTimeout === 'function'
      ? options.clearTimeout : (typeof clearTimeout === 'function' ? clearTimeout : null);
    var maxPollAttempts = pollIntervalMs > 0
      ? Math.max(1, Math.ceil(maxPollMs / pollIntervalMs) + 1) : 0;

    if (!doc || typeof doc.getElementById !== 'function') {
      throw new Error('createController requires a document');
    }
    if (typeof fetchFn !== 'function') {
      throw new Error('createController requires a fetch function');
    }

    var els = {
      lockState: doc.getElementById('pilot-lock-state'),
      sessionDetail: doc.getElementById('pilot-session-detail'),
      lockPanel: doc.getElementById('pilot-lock'),
      unlockForm: doc.getElementById('pilot-unlock-form'),
      tokenInput: doc.getElementById('pilot-token-input'),
      unlockButton: doc.getElementById('pilot-unlock-button'),
      unlockNote: doc.getElementById('pilot-unlock-note'),
      announcer: doc.getElementById('pilot-announcer'),
      workspace: doc.getElementById('pilot-workspace'),
      lockButton: doc.getElementById('pilot-lock-button'),
      capMode: doc.getElementById('pilot-cap-mode'),
      capReady: doc.getElementById('pilot-cap-ready'),
      capProvider: doc.getElementById('pilot-cap-provider'),
      capUncertainty: doc.getElementById('pilot-cap-uncertainty'),
      limits: doc.getElementById('pilot-limits'),
      toolsAllowed: doc.getElementById('pilot-tools-allowed'),
      toolsDisabled: doc.getElementById('pilot-tools-disabled'),
      readinessNote: doc.getElementById('pilot-readiness-note'),
      threadForm: doc.getElementById('pilot-thread-form'),
      threadTitle: doc.getElementById('pilot-thread-title'),
      createThread: doc.getElementById('pilot-create-thread'),
      threadsNote: doc.getElementById('pilot-threads-note'),
      threadList: doc.getElementById('pilot-thread-list'),
      threadHeading: doc.getElementById('pilot-thread-heading'),
      threadNote: doc.getElementById('pilot-thread-note'),
      messages: doc.getElementById('pilot-messages'),
      messagesNote: doc.getElementById('pilot-messages-note'),
      taskForm: doc.getElementById('pilot-task-form'),
      messageInput: doc.getElementById('pilot-message-input'),
      sendTask: doc.getElementById('pilot-send-task'),
      taskState: doc.getElementById('pilot-task-state'),
      taskId: doc.getElementById('pilot-task-id'),
      taskRequest: doc.getElementById('pilot-task-request'),
      taskUsage: doc.getElementById('pilot-task-usage'),
      taskError: doc.getElementById('pilot-task-error'),
      taskNote: doc.getElementById('pilot-task-note'),
      result: doc.getElementById('pilot-result'),
      resultExecution: doc.getElementById('pilot-result-execution'),
      resultBody: doc.getElementById('pilot-result-body'),
      resultCheck: doc.getElementById('pilot-result-check'),
      refreshTask: doc.getElementById('pilot-refresh-task'),
      cancelTask: doc.getElementById('pilot-cancel-task'),
      cancelNote: doc.getElementById('pilot-cancel-note'),
      tasksNote: doc.getElementById('pilot-tasks-note'),
      taskList: doc.getElementById('pilot-task-list')
    };
    Object.keys(els).forEach(function (key) {
      if (!els[key]) throw new Error('Missing required pilot UI element: ' + key);
    });

    var bearerToken = null;
    var csrfToken = null;
    var capabilities = null;
    var locked = true;
    var epoch = 0;
    var authAbort = typeof AbortController === 'function' ? new AbortController() : null;
    var bootstrapGeneration = 0;
    var unlockBusy = false;
    var writeBusy = false;
    var writeOwner = null;
    var threads = [];
    var threadListGeneration = 0;
    var selectedThreadId = null;
    var messages = [];
    var messageGeneration = 0;
    var tasks = [];
    var taskListGeneration = 0;
    var activeTask = null;
    var selectedTaskId = null;
    var taskGeneration = 0;
    var taskViewGeneration = 0;
    var taskReadGeneration = 0;
    var pendingSubmission = null;
    var pendingThread = null;
    var pendingWrite = null;
    var pollTimer = null;
    var pollState = null;

    function now() {
      return typeof options.now === 'function' ? options.now() : Date.now();
    }

    function live(startEpoch) {
      return !locked && startEpoch === epoch;
    }

    function advanceEpoch() {
      epoch += 1;
      if (authAbort) authAbort.abort();
      authAbort = typeof AbortController === 'function' ? new AbortController() : null;
      return epoch;
    }

    function announce(text) {
      setText(els.announcer, text);
    }

    function beginWrite() {
      var owner = { epoch: epoch };
      writeOwner = owner;
      writeBusy = true;
      updateSendState();
      return owner;
    }

    function ownsWrite(owner) {
      return live(owner.epoch) && writeOwner === owner;
    }

    function endWrite(owner) {
      if (!ownsWrite(owner)) return;
      writeOwner = null;
      writeBusy = false;
      updateSendState();
    }

    function setLockedVisuals() {
      els.lockState.className = 'badge badge--locked';
      setText(els.lockState, 'Locked');
      els.workspace.hidden = true;
      els.lockPanel.hidden = false;
      els.unlockButton.disabled = false;
      setText(els.unlockButton, 'Unlock');
    }

    function hideResult() {
      els.result.hidden = true;
      setText(els.resultExecution, '');
      setText(els.resultBody, '');
      setText(els.resultCheck, '');
    }

    function resetTaskPanel() {
      setText(els.taskState, UNKNOWN);
      setText(els.taskId, UNKNOWN);
      setText(els.taskRequest, UNKNOWN);
      setText(els.taskUsage, UNKNOWN);
      setText(els.taskError, UNKNOWN);
      setText(els.taskNote, 'No task submitted.');
      setText(els.cancelNote, 'Cancellation is available only while a task is active.');
      els.refreshTask.disabled = true;
      els.cancelTask.disabled = true;
      hideResult();
    }

    function resetCapabilities() {
      setText(els.capMode, UNKNOWN);
      setText(els.capReady, UNKNOWN);
      setText(els.capProvider, UNKNOWN);
      setText(els.capUncertainty, UNKNOWN);
      clearNode(els.limits);
      setText(els.toolsAllowed, 'Allowed tools: calculator, utc_time, web_search.');
      setText(els.toolsDisabled, 'Disabled: shell, fetch, governance, memory tools, bridges, agent activation.');
      setText(els.readinessNote, '');
    }

    function stopPolling() {
      if (pollTimer !== null && cancelTimer) cancelTimer(pollTimer);
      pollTimer = null;
      pollState = null;
    }

    function lock(reason) {
      advanceEpoch();
      messageGeneration += 1;
      stopPolling();
      bearerToken = null;
      csrfToken = null;
      capabilities = null;
      locked = true;
      unlockBusy = false;
      writeBusy = false;
      writeOwner = null;
      threads = [];
      selectedThreadId = null;
      messages = [];
      tasks = [];
      activeTask = null;
      pendingSubmission = null;
      selectedTaskId = null;
      taskGeneration += 1;
      pendingThread = null;
      pendingWrite = null;

      setLockedVisuals();
      els.tokenInput.value = '';
      els.threadTitle.value = '';
      els.messageInput.value = '';
      clearNode(els.threadList);
      clearNode(els.messages);
      clearNode(els.taskList);
      setText(els.threadsNote, 'No conversations loaded.');
      setText(els.threadHeading, 'Conversation');
      setText(els.threadNote, 'No conversation selected.');
      setText(els.messagesNote, 'Committed messages are loaded from the selected conversation.');
      resetTaskPanel();
      resetCapabilities();
      updateSendState();
      setText(els.unlockNote, reason || 'Locked. Enter the owner bearer to unlock.');
      setText(els.sessionDetail, 'Owner session locked; credentials and sensitive DOM cleared.');
      announce('Locked. Credentials and sensitive data cleared.');
    }

    function updateSendState() {
      var hasText = els.messageInput.value.trim() !== '';
      els.createThread.disabled = locked || writeBusy;
      els.refreshTask.disabled = locked || !activeTask;
      Array.from(els.taskList.children).forEach(function (row) {
        if (row.firstChild) row.firstChild.disabled = locked || writeBusy;
      });
      els.cancelTask.disabled = locked || writeBusy || !activeTask ||
        !ACTIVE_STATES[activeTask.state] || activeTask.cancel_requested;
      els.sendTask.disabled = locked || writeBusy || !selectedThreadId || !canRun() || !hasText;
      els.messageInput.setAttribute('aria-busy', writeBusy ? 'true' : 'false');
      els.workspace.setAttribute('aria-busy', (writeBusy || (pollState !== null)) ? 'true' : 'false');
    }

    function canRun() {
      return !locked && capabilities !== null && capabilities.enabled === true &&
        capabilities.ready === true && capabilities.provider_ready === true &&
        capabilities.uncertainty_blocked !== true;
    }

    function readinessProblem() {
      if (!capabilities) return 'Authenticated capabilities are unavailable.';
      if (!capabilities.enabled) return 'The pilot is not enabled on this server.';
      if (!capabilities.ready) return 'The pilot reports that it is not ready; task submission is disabled.';
      if (!capabilities.provider_ready) return 'The provider is not ready; task submission is disabled.';
      if (capabilities.uncertainty_blocked) {
        return 'An unresolved uncertain execution blocks new tasks; task submission is disabled.';
      }
      return 'Task submission is disabled.';
    }

    async function bootstrapSession() {
      var startEpoch = epoch;
      var generation = ++bootstrapGeneration;
      try {
        var res = await fetchFn(ENDPOINTS.session, {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
          signal: authAbort ? authAbort.signal : undefined,
          credentials: 'same-origin'
        });
        if (startEpoch !== epoch || generation !== bootstrapGeneration) return false;
        var payload = await readJsonSafe(res);
        if (startEpoch !== epoch || generation !== bootstrapGeneration) return false;
        if (!res.ok || !payload || typeof payload.csrf_token !== 'string' || payload.csrf_token === '') {
          csrfToken = null;
          setText(els.sessionDetail, 'Request protection unavailable; writes remain blocked until bootstrap succeeds.');
          return false;
        }
        csrfToken = payload.csrf_token;
        setText(els.sessionDetail, 'Request protection ready.');
        return true;
      } catch (err) {
        if (startEpoch !== epoch || generation !== bootstrapGeneration) return false;
        csrfToken = null;
        setText(els.sessionDetail, 'Request protection unavailable; writes remain blocked until bootstrap succeeds.');
        return false;
      }
    }

    async function ensureCsrf() {
      if (typeof csrfToken === 'string' && csrfToken !== '') return true;
      var ok = await bootstrapSession();
      return ok && typeof csrfToken === 'string' && csrfToken !== '';
    }

    async function apiFetch(url, settings) {
      var startEpoch = epoch;
      var opts = settings || {};
      var headers = { 'Accept': 'application/json' };
      var token = Object.prototype.hasOwnProperty.call(opts, 'token') ? opts.token : bearerToken;
      if (opts.auth !== false && token) headers[AUTH_HEADER] = BEARER_PREFIX + token;
      if (opts.body !== undefined) headers['Content-Type'] = 'application/json';
      if (opts.csrf) headers[CSRF_HEADER] = csrfToken;
      var init = {
        method: opts.method || 'GET',
        headers: headers,
        signal: authAbort ? authAbort.signal : undefined,
        credentials: 'same-origin'
      };
      if (opts.body !== undefined) init.body = JSON.stringify(opts.body);

      var res;
      try {
        res = await fetchFn(url, init);
      } catch (err) {
        if (startEpoch !== epoch) return { locked: true, stale: true };
        return { networkError: true, res: null, payload: null };
      }
      if (startEpoch !== epoch) return { locked: true, stale: true };
      if (res.status === 401) {
        lock('Owner session rejected (401). Credentials and sensitive data were cleared.');
        return { res: res, payload: null, locked: true };
      }
      var payload = await readJsonSafe(res);
      if (startEpoch !== epoch) return { locked: true, stale: true };
      return { res: res, payload: payload };
    }

    function safeErrorCode(payload) {
      if (isObject(payload) && typeof payload.code === 'string' &&
          /^[A-Za-z0-9._:-]{1,64}$/.test(payload.code)) return payload.code;
      return '';
    }

    function describeError(result) {
      var res = result.res;
      var payload = result.payload;
      if (!res) return 'The local pilot service could not be reached. No automatic retry was made.';
      var code = safeErrorCode(payload);
      var suffix = code ? ' (code: ' + code + ')' : '';
      if (res.status === 403) {
        return 'The pilot server rejected this write (origin, CSRF, or loopback policy)' + suffix + '.';
      }
      if (res.status === 404) return 'The requested pilot resource was not found' + suffix + '.';
      if (res.status === 409) {
        return 'The request conflicts with an earlier request' + suffix + '. Reload to reconcile; it was not retried.';
      }
      if (res.status === 413) return 'The message exceeds the pilot size limit' + suffix + '.';
      if (res.status === 415) return 'The pilot server rejected the request media type' + suffix + '.';
      if (res.status === 422) return 'The request did not match the frozen pilot schema' + suffix + '.';
      if (res.status === 429) {
        return 'Budget limit reached' + suffix + '. Wait for the limit window; no automatic retry was made.';
      }
      if (res.status === 503) {
        if (isObject(payload) && payload.uncertain === true) {
          return 'The pilot reported an uncertain outcome' + suffix + '. It was not retried automatically.';
        }
        return 'The pilot is unavailable or failed a safety/audit check' + suffix + '. It was not retried automatically.';
      }
      if (res.status >= 500) {
        return 'The pilot server failed' + suffix + '; the outcome may be uncertain. No automatic retry was made.';
      }
      return 'Pilot request failed (HTTP ' + res.status + ')' + suffix + '.';
    }

    function renderCapabilities() {
      setText(els.capMode, 'Single-owner pilot');
      setText(els.capReady, capabilities.ready ? 'Ready' : 'Not ready');
      setText(els.capProvider, capabilities.provider_ready ? 'Provider ready' : 'Provider not ready');
      setText(els.capUncertainty, capabilities.uncertainty_blocked
        ? 'Blocked by unresolved uncertainty' : 'No uncertainty block');
      setText(els.toolsAllowed, 'Allowed tools: ' + FIXED_TOOLS.join(', ') + '.');
      setText(els.toolsDisabled,
        'Disabled: shell, fetch, governance, memory tools, bridges, agent activation.');

      clearNode(els.limits);
      var rows = [
        ['Requests per hour', capabilities.limits.requests_per_hour],
        ['Requests per day', capabilities.limits.requests_per_day],
        ['Model calls per hour', capabilities.limits.model_calls_per_hour],
        ['Model calls per day', capabilities.limits.model_calls_per_day],
        ['Model calls per request', capabilities.limits.model_calls_per_request],
        ['Tool calls per request', capabilities.limits.tool_calls_per_request]
      ];
      rows.forEach(function (row) {
        var div = doc.createElement('div');
        var dt = doc.createElement('dt');
        var dd = doc.createElement('dd');
        dt.textContent = row[0];
        dd.textContent = String(row[1]);
        div.appendChild(dt);
        div.appendChild(dd);
        els.limits.appendChild(div);
      });

      if (!capabilities.ready || !capabilities.provider_ready || capabilities.uncertainty_blocked) {
        setText(els.readinessNote, readinessProblem());
        els.lockState.className = 'badge badge--busy';
        setText(els.lockState, 'Authenticated · not ready');
      } else {
        setText(els.readinessNote,
          'Authenticated. The workspace uses only the fixed pilot routes; execution and independent checks are separate signals.');
        els.lockState.className = 'badge badge--ready';
        setText(els.lockState, 'Unlocked');
      }
      updateSendState();
    }

    function renderThreads() {
      clearNode(els.threadList);
      if (threads.length === 0) {
        setText(els.threadsNote, 'No conversations yet. Create one to start.');
        if (selectedThreadId !== null) {
          selectedThreadId = null;
          clearNode(els.messages);
          setText(els.threadNote, 'No conversation selected.');
          setText(els.messagesNote, 'No committed messages to show.');
        }
        return;
      }
      setText(els.threadsNote, threads.length + (threads.length === 1
        ? ' conversation. Select one to load committed messages.'
        : ' conversations. Select one to load committed messages.'));
      threads.forEach(function (view) {
        var li = doc.createElement('li');
        var button = doc.createElement('button');
        button.setAttribute('type', 'button');
        button.className = 'thread-button';
        button.setAttribute('aria-pressed', view.id === selectedThreadId ? 'true' : 'false');
        button.textContent = view.title;
        button.addEventListener('click', function () {
          selectThread(view.id);
        });
        li.appendChild(button);
        els.threadList.appendChild(li);
      });
    }

    function renderMessages() {
      clearNode(els.messages);
      messages.forEach(function (view) {
        var article = doc.createElement('article');
        article.className = 'msg msg--' + view.role;
        article.setAttribute('data-role', view.role);
        var head = doc.createElement('header');
        head.className = 'msg-head';
        var who = doc.createElement('span');
        who.textContent = view.role === 'user' ? 'Owner'
          : view.role === 'assistant' ? 'Pilot' : 'System';
        var time = doc.createElement('span');
        time.textContent = view.created_at;
        head.appendChild(who);
        head.appendChild(time);
        var body = doc.createElement('p');
        body.className = 'msg-body';
        body.textContent = view.content;
        article.appendChild(head);
        article.appendChild(body);
        els.messages.appendChild(article);
      });
    }

    function renderTaskList() {
      clearNode(els.taskList);
      if (tasks.length === 0) {
        setText(els.tasksNote, 'No tasks yet. Submitted tasks appear here from the server.');
        return;
      }
      setText(els.tasksNote, tasks.length + (tasks.length === 1
        ? ' task reported by the server.' : ' tasks reported by the server.'));
      tasks.forEach(function (task) {
        var li = doc.createElement('li');
        var label = doc.createElement('button');
        label.setAttribute('type', 'button');
        label.className = 'thread-button';
        label.textContent = task.task_id + ' · conversation ' + task.thread_id;
        label.setAttribute('aria-pressed', selectedTaskId === task.task_id ? 'true' : 'false');
        label.disabled = locked || writeBusy;
        label.addEventListener('click', function () { track(selectTask(task.task_id)); });
        var state = doc.createElement('span');
        state.className = 'task-state';
        state.textContent = task.uncertain ? task.state + ' · uncertain' : task.state;
        li.appendChild(label);
        li.appendChild(state);
        els.taskList.appendChild(li);
      });
    }

    function upsertTask(task) {
      taskListGeneration += 1;
      task = retainedTask(task);
      var replaced = false;
      for (var i = 0; i < tasks.length; i++) {
        if (tasks[i].task_id === task.task_id) {
          tasks[i] = task;
          replaced = true;
          break;
        }
      }
      if (!replaced) tasks.unshift(task);
      renderTaskList();
      return task;
    }

    function retainedTask(task) {
      for (var i = 0; i < tasks.length; i++) {
        var previous = tasks[i];
        if (previous.task_id !== task.task_id) continue;
        // Frozen store contract: terminal success/failure/uncertainty is immutable.
        if (TERMINAL_STATES[previous.state]) return previous;
        var before = Date.parse(previous.updated_at);
        var after = Date.parse(task.updated_at);
        if (isFinite(before) && isFinite(after) && after < before) return previous;
      }
      return task;
    }

    async function renderCompleted(task, stillCurrent) {
      var bind = await bindAssessment(task.assessment, task, task.response);
      if (!stillCurrent()) {
        return false;
      }
      if (!bind.bound) {
        hideResult();
        setText(els.taskNote,
          'Independent check unavailable: the assessment is missing, malformed, or did not bind to this task and exact output bytes. Result text withheld.');
        return false;
      }
      els.result.hidden = false;
      setText(els.resultExecution, bind.execution);
      setText(els.resultBody, task.response);
      setText(els.resultCheck, bind.check);
      setText(els.taskNote,
        'Task completed only after the assessment bound to this task and the exact response bytes. Execution and independent checks are separate signals.');
      return true;
    }

    async function renderTask(task, stillCurrent) {
      hideResult();
      setText(els.taskState, task.state + (task.uncertain ? ' · uncertain' : '') +
        (task.cancel_requested ? ' · cancellation requested' : ''));
      setText(els.taskId, task.task_id);
      setText(els.taskRequest, task.request_id);
      setText(els.taskUsage, 'Model attempts: ' + task.usage.model_attempts +
        ' · Tool attempts: ' + task.usage.tool_attempts);
      setText(els.taskError, task.error_code || UNKNOWN);
      var active = ACTIVE_STATES[task.state] === true;
      els.cancelTask.disabled = locked || !active || task.cancel_requested;
      els.refreshTask.disabled = locked;

      if (task.state === 'COMPLETED') {
        if (task.uncertain) {
          setText(els.taskNote,
            'Task reported COMPLETED with uncertainty; the result is withheld because the state is contradictory. No automatic retry.');
          return { bound: false };
        }
        var bound = await renderCompleted(task, stillCurrent);
        if (!stillCurrent()) return { bound: false, stale: true };
        return { bound: bound };
      }
      if (task.state === 'FAILED') {
        setText(els.taskNote,
          'Task failed. No successful transcript or result body is shown. The pilot does not retry automatically.');
      } else if (task.state === 'UNCERTAIN') {
        setText(els.taskNote,
          'Task outcome UNCERTAIN' + (task.error_code ? ' (error code: ' + task.error_code + ')' : '') +
          '. Part of the task may have executed. It is not retried automatically; reconcile explicitly with the server.');
      } else if (task.state === 'CANCELLED') {
        setText(els.taskNote,
          'Task cancelled (server-confirmed). No successful transcript or result body is shown.');
      } else if (task.state === 'DENIED') {
        setText(els.taskNote, 'Task denied. No execution result is available.');
      } else if (task.cancel_requested) {
        setText(els.taskNote,
          'Cancellation requested; waiting for the server-confirmed task state. No local cancellation is claimed.');
      } else {
        setText(els.taskNote,
          'Task active. Status polling is read-only, bounded, and stops at a terminal server state.');
      }
      return { bound: false };
    }

    async function applyTask(task) {
      var startEpoch = epoch;
      var generation = taskGeneration;
      var viewGeneration = ++taskViewGeneration;
      selectedTaskId = task.task_id;
      task = upsertTask(task);
      activeTask = task;
      function stillCurrent() {
        return live(startEpoch) && generation === taskGeneration &&
          viewGeneration === taskViewGeneration && activeTask === task;
      }
      var info = await renderTask(task, stillCurrent);
      if (!stillCurrent()) return { bound: false, stale: true };
      if (TERMINAL_STATES[task.state] === true) {
        if (pollState !== null && pollState.taskId === task.task_id) stopPolling();
        if (task.state === 'COMPLETED' && info.bound && !locked &&
            selectedThreadId === task.thread_id) {
          await refreshMessages(task.thread_id);
          if (!stillCurrent()) return { bound: false, stale: true };
        }
      }
      updateSendState();
      return info;
    }

    async function refreshThreads() {
      if (locked) return null;
      var startEpoch = epoch;
      var generation = ++threadListGeneration;
      var result = await apiFetch(ENDPOINTS.threads);
      if (result.locked || !live(startEpoch) || generation !== threadListGeneration) return null;
      if (result.networkError) {
        setText(els.threadsNote, 'Conversations unavailable: the local pilot service could not be reached.');
        return null;
      }
      if (!result.res.ok) {
        setText(els.threadsNote, 'Conversations unavailable. ' + describeError(result));
        return null;
      }
      var list = normalizeThreadList(result.payload);
      if (list === null) {
        threads = [];
        renderThreads();
        setText(els.threadsNote,
          'Conversations unavailable: the server response was malformed. Nothing was rendered.');
        return null;
      }
      threads = list;
      renderThreads();
      return threads;
    }

    async function refreshTasks() {
      if (locked) return null;
      var startEpoch = epoch;
      var generation = ++taskListGeneration;
      var result = await apiFetch(ENDPOINTS.tasks + '?limit=50');
      if (result.locked || !live(startEpoch) || generation !== taskListGeneration) return null;
      if (result.networkError) {
        setText(els.tasksNote, 'Tasks unavailable: the local pilot service could not be reached.');
        return null;
      }
      if (!result.res.ok) {
        setText(els.tasksNote, 'Tasks unavailable. ' + describeError(result));
        return null;
      }
      var list = normalizeTaskList(result.payload);
      if (list === null) {
        tasks = [];
        renderTaskList();
        setText(els.tasksNote, 'Tasks unavailable: the server response was malformed. Nothing was rendered.');
        return null;
      }
      tasks = list.map(retainedTask);
      renderTaskList();
      return tasks;
    }

    function threadTitleFor(threadId) {
      for (var i = 0; i < threads.length; i++) {
        if (threads[i].id === threadId) return threads[i].title;
      }
      return '';
    }

    async function refreshMessages(threadId) {
      if (locked || selectedThreadId !== threadId) return null;
      var startEpoch = epoch;
      var generation = ++messageGeneration;
      setText(els.messagesNote, 'Loading committed messages…');
      var result = await apiFetch(ENDPOINTS.threads + '/' + encodeURIComponent(threadId) + '/messages');
      if (result.locked || !live(startEpoch) || selectedThreadId !== threadId || generation !== messageGeneration) {
        return null;
      }
      if (result.networkError) {
        clearNode(els.messages);
        setText(els.messagesNote, 'Messages unavailable: the local pilot service could not be reached.');
        return null;
      }
      if (!result.res.ok) {
        clearNode(els.messages);
        setText(els.messagesNote, 'Messages unavailable. ' + describeError(result));
        return null;
      }
      var list = normalizeMessageList(result.payload, threadId);
      if (list === null) {
        messages = [];
        clearNode(els.messages);
        setText(els.messagesNote,
          'Messages unavailable: the server response was malformed. Nothing was rendered.');
        return null;
      }
      messages = list;
      renderMessages();
      setText(els.messagesNote, messages.length === 0
        ? 'No committed messages in this conversation yet.'
        : messages.length + (messages.length === 1
          ? ' committed message in this conversation.' : ' committed messages in this conversation.'));
      return messages;
    }

    async function selectThread(threadId) {
      if (locked || !nonEmptyString(threadId)) return false;
      var startEpoch = epoch;
      var generation = messageGeneration + 1;
      selectedThreadId = threadId;
      var title = threadTitleFor(threadId);
      setText(els.threadHeading, title || 'Conversation');
      setText(els.threadNote, title
        ? 'Selected: ' + title + '. Messages are loaded only for this conversation.'
        : 'Selected conversation. Messages are loaded only for this conversation.');
      clearNode(els.messages);
      renderThreads();
      await refreshMessages(threadId);
      if (!live(startEpoch) || generation !== messageGeneration || selectedThreadId !== threadId) return false;
      els.messages.focus();
      updateSendState();
      return true;
    }

    async function createThread() {
      if (locked || writeBusy) return false;
      var title = els.threadTitle.value.trim();
      if (title === '') {
        setText(els.threadsNote, 'Enter a conversation title first.');
        els.threadTitle.focus();
        return false;
      }
      if (title.length > 120) {
        setText(els.threadsNote, 'Conversation titles are limited to 120 characters.');
        return false;
      }
      var requestId = pendingThread && pendingThread.title === title
        ? pendingThread.requestId : newRequestId();
      pendingThread = { requestId: requestId, title: title };
      var owner = beginWrite();
      try {
        var protectedWrite = await ensureCsrf();
        if (!ownsWrite(owner)) return false;
        if (!protectedWrite) {
          setText(els.threadsNote, 'No CSRF session token is available; the conversation was not created.');
          return false;
        }
        var result = await apiFetch(ENDPOINTS.threads, {
          method: 'POST',
          csrf: true,
          body: { request_id: requestId, title: title, parent_thread_id: null }
        });
        if (result.locked || !ownsWrite(owner)) return false;
        if (result.networkError) {
          setText(els.threadsNote,
            'Conversation creation outcome unknown. The same request ID is retained; no automatic retry was made.');
          return false;
        }
        if (!result.res.ok) {
          if (result.res.status >= 500 || result.res.status === 429) {
            setText(els.threadsNote,
              describeError(result) + ' The same request ID is retained; no automatic retry was made.');
          } else {
            pendingThread = null;
            setText(els.threadsNote, 'Conversation not created. ' + describeError(result));
          }
          return false;
        }
        var payload = result.payload;
        if (!isObject(payload) || !exactKeys(payload, ['request', 'thread']) ||
            !normalizeReceipt(payload.request, requestId) || !normalizeThread(payload.thread)) {
          setText(els.threadsNote,
            'Conversation creation response was malformed. The outcome is unknown and the same request ID is retained.');
          return false;
        }
        pendingThread = null;
        els.threadTitle.value = '';
        await refreshThreads();
        if (!ownsWrite(owner)) return false;
        var selected = await selectThread(payload.thread.id);
        if (!ownsWrite(owner) || !selected) return false;
        announce('Conversation created and selected.');
        return true;
      } finally {
        endWrite(owner);
      }
    }

    function startPolling(taskId) {
      stopPolling();
      if (locked || !nonEmptyString(taskId)) return;
      pollState = {
        taskId: taskId,
        started: now(),
        count: 0,
        busy: false,
        lastTask: activeTask
      };
      updateSendState();
      if (pollIntervalMs > 0 && scheduleTimer) schedulePoll(pollIntervalMs);
    }

    function schedulePoll(delay) {
      var state = pollState;
      if (!state || locked || !scheduleTimer) return;
      pollTimer = scheduleTimer(function () {
        if (pollState !== state || locked) return;
        pollTimer = null;
        var pending = pollOnce(state);
        if (pending && typeof pending.then === 'function') {
          pending.then(function () {
            if (pollState === state && !locked && state.lastTask &&
                ACTIVE_STATES[state.lastTask.state] === true) {
              schedulePoll(pollIntervalMs);
            }
          });
        }
      }, delay);
      if (pollTimer && typeof pollTimer.unref === 'function') pollTimer.unref();
    }

    async function fetchTask(taskId) {
      if (locked || selectedTaskId !== taskId) return null;
      var startEpoch = epoch;
      var generation = taskGeneration;
      var readGeneration = ++taskReadGeneration;
      var result = await apiFetch(ENDPOINTS.tasks + '/' + encodeURIComponent(taskId));
      if (result.locked || !live(startEpoch) || generation !== taskGeneration ||
          readGeneration !== taskReadGeneration || selectedTaskId !== taskId) return null;
      if (result.networkError) {
        setText(els.taskNote,
          'Task status unavailable: the local pilot service could not be reached. Polling remains bounded and read-only.');
        return null;
      }
      if (!result.res.ok) {
        if (result.res.status === 404) {
          stopPolling();
          setText(els.taskNote, describeError(result) + ' Polling stopped.');
          return null;
        }
        setText(els.taskNote, 'Task status unavailable. ' + describeError(result));
        return null;
      }
      var task = normalizeTask(result.payload);
      if (!task || task.task_id !== taskId) {
        stopPolling();
        setText(els.taskNote,
          'Task status response was malformed or did not match the requested task. Polling stopped; no success is implied.');
        return null;
      }
      var info = await applyTask(task);
      if (info.stale || !live(startEpoch) || generation !== taskGeneration ||
          readGeneration !== taskReadGeneration || selectedTaskId !== taskId) return null;
      return activeTask;
    }

    async function pollOnce(state) {
      if (!state || state.busy || locked || pollState !== state) return;
      var elapsed = now() - state.started;
      if (elapsed > maxPollMs || (maxPollAttempts > 0 && state.count >= maxPollAttempts)) {
        stopPolling();
        setText(els.taskNote,
          'Bounded status polling reached its deadline. The task is not assumed complete or failed; refresh explicitly to reconcile.');
        return;
      }
      state.busy = true;
      state.count += 1;
      var task = null;
      try {
        task = await fetchTask(state.taskId);
      } finally {
        if (pollState === state && !locked) state.busy = false;
      }
      if (task && pollState === state && !locked) state.lastTask = task;
    }

    async function refreshTask(taskId) {
      if (locked || !nonEmptyString(taskId)) return null;
      if (selectedTaskId !== taskId) return selectTask(taskId);
      return fetchTask(taskId);
    }

    async function selectTask(taskId) {
      if (locked || writeBusy || !nonEmptyString(taskId)) return null;
      var startEpoch = epoch;
      var generation = ++taskGeneration;
      stopPolling();
      selectedTaskId = taskId;
      activeTask = null;
      resetTaskPanel();
      setText(els.taskNote, 'Loading selected task ' + taskId + ' from the server…');
      renderTaskList();
      updateSendState();
      var task = await fetchTask(taskId);
      if (!live(startEpoch) || generation !== taskGeneration || selectedTaskId !== taskId) return null;
      if (task && activeTask && ACTIVE_STATES[activeTask.state]) startPolling(taskId);
      if (task) els.refreshTask.focus();
      return task;
    }

    async function submitTask(raw) {
      if (locked || writeBusy) return false;
      if (selectedThreadId === null) {
        setText(els.taskNote, 'Select or create a conversation first.');
        return false;
      }
      if (!canRun()) {
        setText(els.taskNote, readinessProblem());
        return false;
      }
      var text = typeof raw === 'string' ? raw : els.messageInput.value;
      if (text.trim() === '') {
        setText(els.taskNote, 'Enter a message before submitting.');
        return false;
      }
      if (text.length > 8000) {
        setText(els.taskNote, 'Messages are limited to 8000 characters; nothing was sent.');
        return false;
      }
      var threadId = selectedThreadId;
      var submittedDraft = els.messageInput.value;
      var requestId = pendingSubmission &&
        pendingSubmission.threadId === threadId &&
        pendingSubmission.message === text
        ? pendingSubmission.requestId : newRequestId();
      pendingSubmission = {
        requestId: requestId,
        threadId: threadId,
        message: text
      };
      var owner = beginWrite();
      setText(els.taskNote, 'Submitting task ' + requestId + ' to the pilot…');
      var generation = ++taskGeneration;
      selectedTaskId = null;
      stopPolling();
      try {
        var protectedWrite = await ensureCsrf();
        if (!ownsWrite(owner) || generation !== taskGeneration) return false;
        if (!protectedWrite) {
          setText(els.taskNote, 'No CSRF session token is available; the task was not sent.');
          return false;
        }
        var result = await apiFetch(ENDPOINTS.tasks, {
          method: 'POST',
          csrf: true,
          body: { request_id: requestId, thread_id: threadId, message: text }
        });
        if (result.locked || !ownsWrite(owner) || generation !== taskGeneration) return false;
        if (result.networkError) {
          setText(els.taskNote,
            'Submission outcome unknown: the service could not be reached. The same request ID is retained; no automatic retry was made.');
          return false;
        }
        if (!result.res.ok) {
          if (result.res.status >= 500 || result.res.status === 429) {
            setText(els.taskNote,
              describeError(result) + ' The same request ID is retained; no automatic retry was made.');
          } else {
            pendingSubmission = null;
            setText(els.taskNote, 'Task not admitted. ' + describeError(result));
          }
          return false;
        }
        var task = normalizeTask(result.payload);
        if (!task || task.request_id !== requestId || task.thread_id !== threadId) {
          setText(els.taskNote,
            'Submission response was malformed or did not match the request ID. The outcome is unknown; the same request ID is retained and no automatic retry was made.');
          return false;
        }
        pendingSubmission = null;
        var info = await applyTask(task);
        if (info.stale || !ownsWrite(owner) || generation !== taskGeneration) return false;
        task = activeTask;
        if (ACTIVE_STATES[task.state] === true) startPolling(task.task_id);
        if (selectedThreadId === threadId && els.messageInput.value === submittedDraft) {
          els.messageInput.value = '';
        }
        updateSendState();
        els.cancelTask.focus();
        announce('Task admitted with state ' + task.state + '.');
        return true;
      } finally {
        endWrite(owner);
      }
    }

    async function cancelTask() {
      if (locked || writeBusy || !activeTask || !ACTIVE_STATES[activeTask.state] ||
          activeTask.cancel_requested) {
        return null;
      }
      var requestId = newRequestId();
      var target = activeTask;
      var owner = beginWrite();
      var generation = ++taskGeneration;
      setText(els.cancelNote,
        'Cancellation requested; waiting for the server-confirmed task state. No local cancellation is claimed.');
      try {
        var protectedWrite = await ensureCsrf();
        if (!ownsWrite(owner) || generation !== taskGeneration || !activeTask ||
            activeTask.task_id !== target.task_id || !ACTIVE_STATES[activeTask.state] ||
            activeTask.cancel_requested) return null;
        if (!protectedWrite) {
          setText(els.cancelNote, 'No CSRF session token is available; no cancellation request was sent.');
          return null;
        }
        var result = await apiFetch(
          ENDPOINTS.tasks + '/' + encodeURIComponent(target.task_id) + '/cancel',
          { method: 'POST', csrf: true, body: { request_id: requestId } }
        );
        if (result.locked || !ownsWrite(owner) || generation !== taskGeneration) return null;
        if (result.networkError) {
          setText(els.cancelNote,
            'Cancellation outcome unknown: the service could not be reached. UI state was not changed and no automatic retry was made.');
          return null;
        }
        if (!result.res.ok) {
          setText(els.cancelNote, 'Cancellation not confirmed. ' + describeError(result));
          return null;
        }
        var task = normalizeCancelPayload(result.payload);
        if (!task || task.task_id !== target.task_id || task.thread_id !== target.thread_id) {
          setText(els.cancelNote,
            'Cancellation response was malformed; UI state was not changed.');
          return null;
        }
        var info = await applyTask(task);
        if (info.stale || !ownsWrite(owner) || generation !== taskGeneration) return null;
        task = activeTask;
        setText(els.cancelNote, 'Server-confirmed task state: ' + task.state + '.');
        if (task.state === 'UNCERTAIN') {
          announce('Cancellation returned an uncertain server state.');
        }
        return task.state;
      } finally {
        endWrite(owner);
      }
    }

    async function unlock(raw) {
      if (!locked || unlockBusy) return false;
      var candidate = typeof raw === 'string' ? raw.trim() : '';
      if (candidate === '') {
        setText(els.unlockNote, 'Enter the owner bearer before unlocking.');
        els.tokenInput.focus();
        return false;
      }
      unlockBusy = true;
      var startEpoch = advanceEpoch();
      els.unlockButton.disabled = true;
      setText(els.unlockButton, 'Unlocking…');
      updateSendState();
      try {
        if (!csrfToken) await bootstrapSession();
        if (startEpoch !== epoch) return false;
        var result = await apiFetch(ENDPOINTS.capabilities, { method: 'GET', token: candidate });
        if (result.locked || startEpoch !== epoch) return false;
        if (result.networkError) {
          setText(els.unlockNote,
            'Could not reach the pilot service; still locked. No credential was retained.');
          return false;
        }
        if (!result.res.ok) {
          setText(els.unlockNote,
            'Unlock failed. ' + describeError(result) + ' Still locked.');
          return false;
        }
        var payload = normalizeCapabilities(result.payload);
        if (!payload) {
          setText(els.unlockNote,
            'Capabilities response was malformed; still locked. No credential was retained.');
          return false;
        }
        if (payload.enabled !== true) {
          setText(els.unlockNote, 'The pilot is not enabled on this server; still locked.');
          return false;
        }

        bearerToken = candidate;
        capabilities = payload;
        locked = false;
        els.tokenInput.value = '';
        els.lockPanel.hidden = true;
        els.workspace.hidden = false;
        setText(els.unlockNote, 'Unlocked. The bearer is held only in this tab.');
        setText(els.sessionDetail, 'Owner session unlocked. Bearer is held only in this tab.');
        renderCapabilities();
        renderThreads();
        resetTaskPanel();
        renderTaskList();
        updateSendState();
        announce('Owner pilot unlocked.');
        els.threadTitle.focus();

        await Promise.all([refreshThreads(), refreshTasks()]);
        if (!live(startEpoch)) return false;
        updateSendState();
        return true;
      } finally {
        if (startEpoch === epoch) {
          unlockBusy = false;
          els.unlockButton.disabled = false;
          setText(els.unlockButton, 'Unlock');
          updateSendState();
        }
      }
    }

    function track(promise) {
      pendingWrite = promise;
      return promise;
    }

    function wireEvents() {
      els.unlockForm.addEventListener('submit', function (event) {
        event.preventDefault();
        track(unlock(els.tokenInput.value));
      });
      els.lockButton.addEventListener('click', function () {
        lock('Locked by the owner. Credentials and sensitive data were cleared.');
      });
      els.threadForm.addEventListener('submit', function (event) {
        event.preventDefault();
        track(createThread());
      });
      els.taskForm.addEventListener('submit', function (event) {
        event.preventDefault();
        track(submitTask(els.messageInput.value));
      });
      els.messageInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          track(submitTask(els.messageInput.value));
        }
      });
      els.messageInput.addEventListener('input', updateSendState);
      els.cancelTask.addEventListener('click', function () {
        track(cancelTask());
      });
      els.refreshTask.addEventListener('click', function () {
        if (activeTask && !locked) track(refreshTask(activeTask.task_id));
      });
    }

    async function init() {
      var startEpoch = epoch;
      setLockedVisuals();
      resetTaskPanel();
      resetCapabilities();
      await bootstrapSession();
      if (startEpoch !== epoch) return api;
      setText(els.unlockNote, 'Enter the owner bearer to request authenticated capabilities.');
      updateSendState();
      els.tokenInput.focus();
      return api;
    }

    var api = {
      init: init,
      unlock: unlock,
      lock: function () {
        lock('Locked by the owner. Credentials and sensitive data were cleared.');
      },
      refreshCapabilities: function () {
        return capabilities;
      },
      refreshThreads: refreshThreads,
      refreshTasks: refreshTasks,
      selectThread: selectThread,
      refreshMessages: refreshMessages,
      createThread: createThread,
      submitTask: submitTask,
      refreshTask: refreshTask,
      selectTask: selectTask,
      cancelTask: cancelTask,
      stop: stopPolling,
      settled: function () {
        return pendingWrite || Promise.resolve(false);
      },
      state: function () {
        return {
          locked: locked,
          hasBearer: bearerToken !== null,
          csrfReady: typeof csrfToken === 'string' && csrfToken !== '',
          selectedThreadId: selectedThreadId,
          activeTaskId: activeTask ? activeTask.task_id : null,
          polling: pollState !== null,
          busy: unlockBusy || writeBusy
        };
      }
    };

    wireEvents();
    return api;
  }

  function startUi() {
    if (typeof fetch !== 'function') return null;
    var controller = createController({
      document: document,
      fetch: fetch.bind(globalThis),
      pollIntervalMs: DEFAULT_POLL_MS
    });
    controller.init();
    return controller;
  }

  if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', function () {
        try { startUi(); } catch (err) { /* startup failure leaves the locked shell visible */ }
      });
    } else {
      try { startUi(); } catch (err) { /* startup failure leaves the locked shell visible */ }
    }
  }

  // Test seam only; no credentials or private state is exported.
  globalThis.__pilotUI = {
    ENDPOINTS: ENDPOINTS,
    CSRF_HEADER: CSRF_HEADER,
    DEFAULT_POLL_MS: DEFAULT_POLL_MS,
    DEFAULT_MAX_POLL_MS: DEFAULT_MAX_POLL_MS,
    newRequestId: newRequestId,
    bindAssessment: bindAssessment,
    validAssessment: validAssessment,
    normalizeTask: normalizeTask,
    createController: createController
  };
})();
