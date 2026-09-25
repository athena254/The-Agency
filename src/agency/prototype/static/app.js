'use strict';
/**
 * The Agency — local Remex/Butler prototype browser UI.
 *
 * Contract (docs/SPEC_MVP_PROTOTYPE_UI_TELEGRAM.md, visual-UI lane):
 *  - Same-origin fetch only: /api/session, /api/health, /api/agents, /api/chat.
 *  - The CSRF token comes from /api/session at startup and is sent only in the
 *    X-Prototype-CSRF header of POST /api/chat.
 *  - No API keys, no CDN or remote assets, no storage of messages, and no
 *    fabricated answers: failures surface the real HTTP or network error.
 *  - Every server- or user-supplied string is rendered with textContent only
 *    (never parsed as markup), so transcript text remains plain text.
 *
 * The helpers and createController() are exported on globalThis.__remexUI so
 * that tests/prototype_ui.test.mjs can drive the logic with a tiny DOM fake.
 */
(function () {
  var ENDPOINTS = Object.freeze({
    health: '/api/health',
    agents: '/api/agents',
    session: '/api/session',
    chat: '/api/chat'
  });
  var CSRF_HEADER = 'X-Prototype-CSRF';
  var DEFAULT_POLL_MS = 30000;
  var UNKNOWN = '\u2014';
  var ONLINE_RE = /^(ok|healthy|up|online)$/i;

  /** Coerce a primitive API value to display text; anything else is ''. */
  function displayValue(value) {
    if (typeof value === 'string') return value;
    if (typeof value === 'number' && isFinite(value)) return String(value);
    if (typeof value === 'boolean') return String(value);
    return '';
  }

  /**
   * Map GET /api/health to an honest badge view. Unknown payloads degrade to
   * "unavailable" or the raw status text instead of guessing an online claim.
   */
  function healthView(payload) {
    var hasPayload = payload !== null && typeof payload === 'object';
    var statusText = hasPayload ? displayValue(payload.status) : '';
    var butlerText = hasPayload ? displayValue(payload.butler) : '';
    var agentsValue = hasPayload ? payload.agents : null;
    var agentsText = (typeof agentsValue === 'number' && isFinite(agentsValue))
      ? String(agentsValue)
      : displayValue(agentsValue);
    var state;
    var label;
    if (!statusText) {
      state = 'offline';
      label = 'Status unavailable';
    } else if (ONLINE_RE.test(statusText)) {
      state = 'online';
      label = 'Local service online';
    } else {
      state = 'unknown';
      label = 'Status: ' + statusText;
    }
    return {
      state: state,
      label: label,
      detail: 'Butler: ' + (butlerText || UNKNOWN) + ' \u00b7 Agents: ' + (agentsText || UNKNOWN),
      statusText: statusText || UNKNOWN,
      butlerText: butlerText || UNKNOWN,
      agentsText: agentsText || UNKNOWN
    };
  }

  /** Validate GET /api/agents into [{id,name,domain}]; never invent entries. */
  function normalizeAgents(payload) {
    if (!payload || !Array.isArray(payload.agents)) return [];
    var out = [];
    payload.agents.forEach(function (item) {
      if (!item || typeof item !== 'object') return;
      var id = displayValue(item.id).trim();
      var name = displayValue(item.name).trim();
      var domain = displayValue(item.domain).trim();
      if (!id && !name) return;
      out.push({ id: id, name: name || id, domain: domain });
    });
    return out;
  }

  /** Honest HTTP error text: prefer the server's sanitized {error} string. */
  function describeHttpError(status, serverError, statusText) {
    if (typeof serverError === 'string' && serverError.trim()) return serverError.trim();
    var head = 'Request failed';
    if (typeof status === 'number' && status > 0) head += ' (HTTP ' + status + ')';
    if (typeof statusText === 'string' && statusText.trim()) head += ' ' + statusText.trim();
    return head + '.';
  }

  function errorFrom(err) {
    if (err && typeof err.message === 'string' && err.message) return err.message;
    return String(err);
  }

  function serverErrorOf(payload) {
    if (payload && typeof payload === 'object' && typeof payload.error === 'string') {
      return payload.error;
    }
    return '';
  }

  /** Read a response body as JSON without ever throwing. */
  async function readJsonSafe(response) {
    if (!response || typeof response.json !== 'function') return null;
    try {
      return await response.json();
    } catch (err) {
      return null;
    }
  }

  /** GET /api/session -> csrf_token, or an Error with an honest message. */
  async function requestSession(fetchFn) {
    var res = await fetchFn(ENDPOINTS.session, {
      method: 'GET',
      headers: { 'Accept': 'application/json' },
      credentials: 'same-origin'
    });
    var payload = await readJsonSafe(res);
    if (!res.ok) {
      throw new Error(describeHttpError(res.status, serverErrorOf(payload), res.statusText));
    }
    if (!payload || typeof payload.csrf_token !== 'string' || payload.csrf_token === '') {
      throw new Error('Session response did not include a CSRF token.');
    }
    return payload.csrf_token;
  }

  function clockTime(date) {
    function pad(n) {
      return n < 10 ? '0' + n : String(n);
    }
    return pad(date.getHours()) + ':' + pad(date.getMinutes());
  }

  /**
   * Build the interactive controller against a document (real or fake) and a
   * same-origin fetch function. All DOM writes for API/user text go through
   * textContent via appendMessage()/textContent assignment only.
   */
  function createController(options) {
    var doc = options.document;
    var fetchFn = options.fetch;
    var pollMs = typeof options.pollIntervalMs === 'number'
      ? options.pollIntervalMs
      : DEFAULT_POLL_MS;

    if (!doc || typeof doc.getElementById !== 'function') {
      throw new Error('createController requires a document');
    }
    if (typeof fetchFn !== 'function') {
      throw new Error('createController requires a fetch function');
    }

    var els = {
      badge: doc.getElementById('status-badge'),
      statusDetail: doc.getElementById('status-detail'),
      factStatus: doc.getElementById('fact-status'),
      factButler: doc.getElementById('fact-butler'),
      factAgents: doc.getElementById('fact-agents'),
      transcript: doc.getElementById('transcript'),
      notice: doc.getElementById('notice'),
      noticeText: doc.getElementById('notice-text'),
      noticeDismiss: doc.getElementById('notice-dismiss'),
      composer: doc.getElementById('composer'),
      messageInput: doc.getElementById('message-input'),
      sendButton: doc.getElementById('send-button'),
      agentsList: doc.getElementById('agents-list'),
      agentsNote: doc.getElementById('agents-note')
    };
    Object.keys(els).forEach(function (key) {
      if (!els[key]) throw new Error('Missing required UI element: ' + key);
    });

    var csrfToken = null;
    var busy = false;
    var pollTimer = null;
    var pendingSubmit = null;

    function showNotice(text) {
      els.noticeText.textContent = text;
      els.notice.hidden = false;
    }

    function hideNotice() {
      els.notice.hidden = true;
      els.noticeText.textContent = '';
    }

    function nearBottom() {
      return (els.transcript.scrollHeight - els.transcript.scrollTop - els.transcript.clientHeight) < 96;
    }

    function scrollToBottom(force) {
      if (force || nearBottom()) els.transcript.scrollTop = els.transcript.scrollHeight;
    }

    function appendMessage(role, text, state) {
      var article = doc.createElement('article');
      article.className = 'msg msg--' + role;
      article.setAttribute('data-role', role);
      if (state) article.setAttribute('data-state', state);

      var head = doc.createElement('header');
      head.className = 'msg-head';
      var who = doc.createElement('span');
      who.className = 'msg-who';
      who.textContent = role === 'user' ? 'You' : 'Butler';
      head.appendChild(who);
      var time = doc.createElement('time');
      time.className = 'msg-time';
      time.textContent = clockTime(new Date());
      head.appendChild(time);
      article.appendChild(head);

      var body = doc.createElement('p');
      body.className = 'msg-body';
      body.textContent = text;
      article.appendChild(body);

      var stick = nearBottom() || role === 'user';
      els.transcript.appendChild(article);
      if (stick) scrollToBottom(true);
      return { article: article, body: body };
    }

    function setState(msg, state) {
      msg.article.setAttribute('data-state', state);
    }

    function failMessage(msg, text) {
      msg.body.textContent = text;
      setState(msg, 'failed');
    }

    function succeedMessage(msg, text) {
      msg.body.textContent = text;
      setState(msg, 'delivered');
    }

    function setBusy(next) {
      busy = next;
      els.composer.setAttribute('data-state', next ? 'busy' : 'idle');
      els.messageInput.setAttribute('aria-busy', next ? 'true' : 'false');
      els.sendButton.textContent = next ? 'Sending\u2026' : 'Send';
      els.sendButton.disabled = next || els.messageInput.value.trim() === '';
    }

    function updateSendState() {
      els.sendButton.disabled = busy || els.messageInput.value.trim() === '';
    }

    function autosize() {
      els.messageInput.style.height = 'auto';
      var next = Math.min(els.messageInput.scrollHeight, 160);
      if (next > 0) els.messageInput.style.height = next + 'px';
    }

    async function refreshSession() {
      var token = await requestSession(fetchFn);
      csrfToken = token;
      return token;
    }

    function applyHealth(view) {
      els.badge.className = 'badge badge--' + view.state;
      els.badge.textContent = view.label;
      els.statusDetail.textContent = view.detail;
      els.factStatus.textContent = view.statusText;
      els.factButler.textContent = view.butlerText;
      els.factAgents.textContent = view.agentsText;
    }

    async function refreshHealth() {
      try {
        var res = await fetchFn(ENDPOINTS.health, {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin'
        });
        var payload = await readJsonSafe(res);
        if (!res.ok) {
          throw new Error(describeHttpError(res.status, serverErrorOf(payload), res.statusText));
        }
        applyHealth(healthView(payload));
      } catch (err) {
        var view = healthView(null);
        view.detail = errorFrom(err);
        applyHealth(view);
      }
    }

    function renderAgents(list) {
      while (els.agentsList.firstChild) {
        els.agentsList.removeChild(els.agentsList.firstChild);
      }
      list.forEach(function (agent) {
        var li = doc.createElement('li');
        li.className = 'agent';
        var name = doc.createElement('span');
        name.className = 'agent-name';
        name.textContent = agent.name;
        li.appendChild(name);
        var parts = [];
        if (agent.domain) parts.push(agent.domain);
        if (agent.id && agent.id !== agent.name) parts.push(agent.id);
        if (parts.length > 0) {
          var meta = doc.createElement('span');
          meta.className = 'agent-meta';
          meta.textContent = parts.join(' \u00b7 ');
          li.appendChild(meta);
        }
        els.agentsList.appendChild(li);
      });
    }

    async function refreshAgents() {
      try {
        var res = await fetchFn(ENDPOINTS.agents, {
          method: 'GET',
          headers: { 'Accept': 'application/json' },
          credentials: 'same-origin'
        });
        var payload = await readJsonSafe(res);
        if (!res.ok) {
          throw new Error(describeHttpError(res.status, serverErrorOf(payload), res.statusText));
        }
        var list = normalizeAgents(payload);
        renderAgents(list);
        els.agentsNote.textContent = list.length > 0
          ? list.length + (list.length === 1 ? ' agent' : ' agents') + ' reported by the local service.'
          : 'The local service reports no agents.';
      } catch (err) {
        renderAgents([]);
        els.agentsNote.textContent = 'Agents unavailable \u2014 ' + errorFrom(err);
      }
    }


    async function postChat(message) {
      var headers = {
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      };
      headers[CSRF_HEADER] = csrfToken;
      var res = await fetchFn(ENDPOINTS.chat, {
        method: 'POST',
        headers: headers,
        credentials: 'same-origin',
        body: JSON.stringify({ message: message })
      });
      var payload = await readJsonSafe(res);
      return { res: res, payload: payload };
    }

    /**
     * Send one user message. Renders the real reply or the real error and
     * never invents text. Resolves true only when a reply was delivered.
     */
    async function submitMessage(raw) {
      if (busy) return false;
      var text = String(raw === null || raw === undefined ? '' : raw).trim();
      if (!text) return false;

      if (!csrfToken) {
        try {
          await refreshSession();
        } catch (err) {
          showNotice('No local session yet \u2014 message not sent. ' + errorFrom(err));
          return false;
        }
      }

      hideNotice();
      var userMsg = appendMessage('user', text, 'pending');
      var replyMsg = appendMessage('assistant', 'Waiting for reply\u2026', 'pending');
      setBusy(true);
      var delivered = false;
      try {
        var result = await postChat(text);
        if (!result.res.ok) {
          if (result.res.status === 403) csrfToken = null;
          failMessage(
            replyMsg,
            describeHttpError(result.res.status, serverErrorOf(result.payload), result.res.statusText)
          );
          return false;
        }
        var reply = result.payload && typeof result.payload.response === 'string'
          ? result.payload.response
          : '';
        if (!reply.trim()) {
          failMessage(replyMsg, 'The server returned no response text.');
          return false;
        }
        succeedMessage(replyMsg, reply);
        delivered = true;
        els.messageInput.value = '';
        autosize();
        return true;
      } catch (err) {
        failMessage(
          replyMsg,
          'Network error \u2014 could not reach the local prototype server. (' + errorFrom(err) + ')'
        );
        return false;
      } finally {
        setState(userMsg, delivered ? 'delivered' : 'failed');
        setBusy(false);
        scrollToBottom(true);
        els.messageInput.focus();
      }
    }

    function submitFromForm() {
      var value = els.messageInput.value;
      if (value.trim() === '') return null;
      pendingSubmit = submitMessage(value);
      return pendingSubmit;
    }

    function wireEvents() {
      els.composer.addEventListener('submit', function (event) {
        event.preventDefault();
        submitFromForm();
      });
      els.messageInput.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) {
          event.preventDefault();
          submitFromForm();
        }
      });
      els.messageInput.addEventListener('input', function () {
        updateSendState();
        autosize();
      });
      els.noticeDismiss.addEventListener('click', function () {
        hideNotice();
      });
    }


    function stop() {
      if (pollTimer !== null) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
    }

    async function init() {
      try {
        await refreshSession();
      } catch (err) {
        showNotice('Could not open a local session: ' + errorFrom(err) + ' Sending will retry it.');
      }
      await Promise.all([refreshHealth(), refreshAgents()]);
      updateSendState();
      if (pollMs > 0 && typeof setInterval === 'function') {
        pollTimer = setInterval(function () {
          if (doc.hidden) return;
          refreshHealth();
          refreshAgents();
        }, pollMs);
        if (pollTimer && typeof pollTimer.unref === 'function') pollTimer.unref();
      }
      return controllerApi;
    }

    var controllerApi = {
      init: init,
      stop: stop,
      submitMessage: submitMessage,
      refreshSession: refreshSession,
      refreshHealth: refreshHealth,
      refreshAgents: refreshAgents,
      showNotice: showNotice,
      hideNotice: hideNotice,
      settled: function () {
        return pendingSubmit || Promise.resolve(false);
      },
      state: function () {
        return { busy: busy, hasSession: csrfToken !== null };
      }
    };

    wireEvents();
    return controllerApi;
  }

  function startUi() {
    if (typeof fetch !== 'function') {
      throw new Error('This browser did not expose fetch().');
    }
    var controller = createController({
      document: document,
      fetch: fetch.bind(globalThis),
      pollIntervalMs: DEFAULT_POLL_MS
    });
    controller.init();
    return controller;
  }

  function safelyStart() {
    try {
      startUi();
    } catch (err) {
      if (typeof console !== 'undefined' && console.error) {
        console.error('Prototype UI failed to start:', errorFrom(err));
      }
    }
  }

  if (typeof document !== 'undefined' && typeof document.getElementById === 'function') {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', safelyStart);
    } else {
      safelyStart();
    }
  }

  // Test seam for tests/prototype_ui.test.mjs (no secrets, helpers only).
  globalThis.__remexUI = {
    ENDPOINTS: ENDPOINTS,
    CSRF_HEADER: CSRF_HEADER,
    displayValue: displayValue,
    healthView: healthView,
    normalizeAgents: normalizeAgents,
    describeHttpError: describeHttpError,
    errorFrom: errorFrom,
    serverErrorOf: serverErrorOf,
    readJsonSafe: readJsonSafe,
    requestSession: requestSession,
    clockTime: clockTime,
    createController: createController
  };
})();
