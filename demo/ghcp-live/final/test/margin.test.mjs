import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { createContext, Script } from 'node:vm';
import { createAppServer } from '../src/server.mjs';
import { QUOTES } from '../src/quote.mjs';

const appScript = new Script(await readFile(new URL('../public/app.js', import.meta.url), 'utf8'));
const cases = [
  { quoteId: 'Q-1001', discountBps: 0, netWon: 10_000_000, profitWon: 2_000_000, canSend: true },
  { quoteId: 'Q-1001', discountBps: 1000, netWon: 9_000_000, profitWon: 1_000_000, canSend: false },
  { quoteId: 'Q-1500', discountBps: 0, netWon: 10_000_000, profitWon: 1_500_000, canSend: true },
  { quoteId: 'Q-1499', discountBps: 0, netWon: 10_000_000, profitWon: 1_499_999, canSend: false },
  { quoteId: 'Q-1501', discountBps: 0, netWon: 10_000_000, profitWon: 1_500_001, canSend: true },
];
const rejection = { error: { code: 'MARGIN_BELOW_MINIMUM', message: 'Margin below 15%.' } };
const allowed = previewFor(cases[0]);
const belowMinimum = previewFor(cases[1]);

function previewFor(entry) {
  const { id, ...quote } = QUOTES.find((item) => item.id === entry.quoteId);
  return {
    ...quote,
    quoteId: id,
    discountBps: entry.discountBps,
    netWon: entry.netWon,
    profitWon: entry.profitWon,
    marginPercent: entry.profitWon / entry.netWon * 100,
    eligibility: {
      canSend: entry.canSend,
      minimumMarginPercent: 15,
      reason: entry.canSend ? null : rejection.error.message,
    },
  };
}

function sent(quote = allowed) {
  return { sent: true, simulation: true, message: 'Simulated send completed.', quote };
}

async function startServer(t, options) {
  const server = createAppServer(options);
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  t.after(() => new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve());
    server.closeAllConnections();
  }));
  return `http://127.0.0.1:${server.address().port}`;
}

function post(base, path, body) {
  return fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

function assertEligibility(calculation, canSend) {
  assert.equal(calculation.eligibility?.canSend, canSend, 'the server must supply its send decision');
  assert.equal(calculation.eligibility.minimumMarginPercent, 15);
  if (canSend) {
    assert.equal(calculation.eligibility.reason, null);
  } else {
    assert.match(calculation.eligibility.reason, /15%/);
  }
}

for (const entry of cases) {
  test(`margin API: ${entry.quoteId}, ${entry.discountBps} bps, canSend=${entry.canSend}`, async (t) => {
    const base = await startServer(t);
    const input = { quoteId: entry.quoteId, discountBps: entry.discountBps };
    // Send first: a caller must not need a preceding preview to enforce the policy.
    const response = await post(base, '/api/send', input);
    assert.equal(response.status, entry.canSend ? 200 : 422);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const result = await response.json();
    if (entry.canSend) {
      assert.equal(result.sent, true);
      assert.equal(result.simulation, true);
    } else {
      assert.equal(result.error.code, 'MARGIN_BELOW_MINIMUM');
      assert.notEqual(result.sent, true);
      assert.match(result.error.message, /15%/);
    }
    const previewResponse = await post(base, '/api/preview', input);
    assert.equal(previewResponse.status, 200);
    const calculation = await previewResponse.json();
    assert.equal(calculation.netWon, entry.netWon);
    assert.equal(calculation.profitWon, entry.profitWon);
    assert.equal(calculation.marginPercent, entry.profitWon / entry.netWon * 100);
    assertEligibility(calculation, entry.canSend);
    assert.deepEqual(result.quote, calculation);
  });
}

test('margin uses rounded whole won, including half-won, upper-bound and negative-profit cases', async (t) => {
  for (const [listPriceWon, costWon, discountBps, netWon, canSend] of [
    [39, 17, 5000, 20, true],
    [1_000_000_000, 850_000_000, 0, 1_000_000_000, true],
    [1_000_000_000, 850_000_001, 0, 1_000_000_000, false],
    [10_000_000, 8_000_000, 9000, 1_000_000, false],
  ]) {
    const quote = { ...QUOTES[0], listPriceWon, costWon };
    const base = await startServer(t, { quotes: [quote] });
    const response = await post(base, '/api/send', { quoteId: quote.id, discountBps });
    assert.equal(response.status, canSend ? 200 : 422);
    const result = await response.json();
    assert.equal(result.quote.netWon, netWon);
    assert.equal(result.quote.profitWon, netWon - costWon);
    assertEligibility(result.quote, canSend);
  }
});

test('send re-evaluates server-owned costs instead of trusting an earlier allowed preview', async (t) => {
  const quote = { ...QUOTES[0] };
  const base = await startServer(t, { quotes: [quote] });
  const input = { quoteId: quote.id, discountBps: 0 };
  const preview = await (await post(base, '/api/preview', input)).json();
  assertEligibility(preview, true);
  quote.costWon = 8_500_001;
  const response = await post(base, '/api/send', input);
  assert.equal(response.status, 422);
  const result = await response.json();
  assert.equal(result.error.code, 'MARGIN_BELOW_MINIMUM');
  assert.equal(result.quote.costWon, quote.costWon);
  assertEligibility(result.quote, false);
});

test('both POST routes reject forged amounts and policy fields, retaining 400 and 404', async (t) => {
  const base = await startServer(t);
  for (const path of ['/api/preview', '/api/send']) {
    for (const [key, value] of Object.entries({
      listPriceWon: 100_000_000, costWon: 1, netWon: 100_000_000, profitWon: 99_999_999,
      marginPercent: 99, minimumMarginPercent: 0, canSend: true,
      eligibility: { canSend: true }, policy: { minimumMarginPercent: 0 },
    })) {
      const response = await post(base, path, { quoteId: 'Q-1499', discountBps: 0, [key]: value });
      assert.equal(response.status, 400, `${path} must reject ${key}`);
      assert.equal((await response.json()).error.code, 'INVALID_INPUT');
    }
    for (const discountBps of ['0', null, 0.5, -1, 10_001, 10_000]) {
      const response = await post(base, path, { quoteId: 'Q-1001', discountBps });
      assert.equal(response.status, 400);
      assert.equal((await response.json()).error.code, 'INVALID_INPUT');
    }
    const missing = await post(base, path, { quoteId: 'UNKNOWN', discountBps: 0 });
    assert.equal(missing.status, 404);
    assert.equal((await missing.json()).error.code, 'QUOTE_NOT_FOUND');
  }
  const tiny = await startServer(t, { quotes: [{ ...QUOTES[0], listPriceWon: 1, costWon: 1 }] });
  for (const path of ['/api/preview', '/api/send']) {
    const response = await post(tiny, path, { quoteId: 'Q-1001', discountBps: 9999 });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, 'INVALID_INPUT');
  }
});

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

class Element {
  value = '';
  textContent = '';
  disabled = false;
  hidden = false;
  dataset = {};
  validity = { valid: true };
  attributes = new Map();
  listeners = new Map();
  firstElementChild = { textContent: '' };

  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  replaceChildren(...children) { this.children = children; }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  dispatch(type) {
    assert.ok(this.listeners.has(type), `missing ${type} listener`);
    return this.listeners.get(type)({ preventDefault() {} });
  }
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

function createUi(t) {
  const selectors = [
    '#quote-form', '#quote-id', '#discount', '#send-quote', '#feedback', '#retry-load',
    '#quote-status', '#quote-summary', '#preview-label', '#quote-reference', '#quote-title',
    '#quote-customer', '#list-amount', '#cost-amount', '#net-amount', '#profit-amount', '#margin-value',
  ];
  const nodes = Object.fromEntries(selectors.map((selector) => [selector, new Element()]));
  for (const selector of ['#quote-id', '#discount', '#send-quote']) nodes[selector].disabled = true;
  nodes['#discount'].value = '0';
  nodes['#retry-load'].hidden = true;
  const requests = [];
  const timers = new Map();
  let nextTimer = 0;
  let context;
  const abortError = () => new DOMException('The request was aborted.', 'AbortError');
  context = createContext({
    document: {
      querySelector(selector) {
        assert.ok(nodes[selector], `unmodelled selector ${selector}`);
        return nodes[selector];
      },
      createElement(name) {
        assert.equal(name, 'option');
        return new Element();
      },
    },
    AbortController, Intl, Error, TypeError, SyntaxError,
    setTimeout(callback, delay) {
      timers.set(++nextTimer, { callback, delay });
      return nextTimer;
    },
    clearTimeout(id) { timers.delete(id); },
    fetch(path, options) {
      const headers = deferred();
      const body = deferred();
      const timerId = nextTimer;
      const request = {
        path, options, ignoreAbort: false, bodyStarted: false,
        resolveHeaders(status) {
          headers.resolve({
            status,
            ok: status >= 200 && status < 300,
            json() {
              request.bodyStarted = true;
              request.allowedAtBodyRead = new Script('preview?.eligibility?.canSend === true').runInContext(context);
              request.previewInputAtBodyRead = new Script('previewInput').runInContext(context);
              if (options.signal.aborted && !request.ignoreAbort) return Promise.reject(abortError());
              return body.promise;
            },
          });
        },
        respond(status, result) { request.resolveHeaders(status); body.resolve(result); },
        resolveBody: body.resolve,
        rejectBody: body.reject,
        reject: headers.reject,
        expire() {
          const timer = timers.get(timerId);
          assert.ok(timer, 'the request timeout must remain active during body reads');
          assert.equal(timer.delay, 10_000);
          timers.delete(timerId);
          timer.callback();
        },
        dispose() {
          headers.reject(abortError());
          if (request.bodyStarted) body.reject(abortError());
        },
      };
      options.signal.addEventListener('abort', () => {
        if (!request.ignoreAbort) request.dispose();
      }, { once: true });
      requests.push(request);
      return headers.promise;
    },
  });
  const ui = {
    nodes, requests,
    latest(path) {
      const request = requests.at(-1);
      assert.equal(request?.path, path);
      return request;
    },
    submit() { return nodes['#quote-form'].dispatch('submit'); },
    change(discount, quoteId = nodes['#quote-id'].value) {
      const quoteChanged = quoteId !== nodes['#quote-id'].value;
      nodes['#quote-id'].value = quoteId;
      nodes['#discount'].value = discount;
      return nodes[quoteChanged ? '#quote-id' : '#discount'].dispatch(quoteChanged ? 'change' : 'input');
    },
    sends() { return requests.filter((request) => request.path === '/api/send').length; },
    snapshot() {
      return selectors.map((selector) => {
        const node = nodes[selector];
        return { text: node.textContent, disabled: node.disabled, label: node.firstElementChild.textContent,
          attributes: [...node.attributes], dataset: { ...node.dataset }, value: node.value, hidden: node.hidden };
      });
    },
  };
  t.after(() => {
    for (const request of requests) request.dispose();
    timers.clear();
  });
  appScript.runInContext(context);
  return ui;
}

async function readyUi(t) {
  const ui = createUi(t);
  ui.latest('/api/quotes').respond(200, { quotes: QUOTES });
  await flush();
  ui.latest('/api/preview').respond(200, allowed);
  await flush();
  assert.equal(ui.nodes['#send-quote'].disabled, false);
  return ui;
}

for (const entry of cases) {
  test(`margin UI uses the server decision: ${entry.quoteId}, ${entry.discountBps} bps`, async (t) => {
    const ui = await readyUi(t);
    const calculation = previewFor(entry);
    const pending = ui.change(String(entry.discountBps / 100), entry.quoteId);
    assert.equal(ui.nodes['#send-quote'].disabled, true);
    ui.latest('/api/preview').respond(200, calculation);
    await pending;
    assert.equal(ui.nodes['#send-quote'].disabled, !entry.canSend);
    if (entry.quoteId.startsWith('Q-15') || entry.quoteId === 'Q-1499') {
      assert.equal(ui.nodes['#margin-value'].textContent, '15.0%');
    }
    assert.equal(ui.sends(), 0, 'preview must never automatically send');
    if (!entry.canSend) {
      assert.match(ui.nodes['#feedback'].textContent, /15%/);
      assert.equal(ui.nodes['#feedback'].dataset.tone, 'error');
      ui.nodes['#send-quote'].disabled = false;
      await ui.submit();
      assert.equal(ui.sends(), 0, 'the submit handler must also enforce eligibility');
    } else {
      const sending = ui.submit();
      assert.equal(ui.nodes['#send-quote'].disabled, true);
      await ui.submit();
      assert.equal(ui.sends(), 1, 'duplicate submits must not send twice');
      const request = ui.latest('/api/send');
      assert.deepEqual(JSON.parse(request.options.body), { quoteId: entry.quoteId, discountBps: entry.discountBps });
      request.respond(200, sent(calculation));
      await sending;
      assert.equal(ui.nodes['#feedback'].dataset.tone, 'success');
      assert.equal(ui.nodes['#send-quote'].disabled, false);
      assert.equal(ui.nodes['#quote-summary'].getAttribute('aria-busy'), 'false');
    }
  });
}

test('UI retains loading retry, input validation, preview failures and manual recovery', async (t) => {
  const ui = createUi(t);
  assert.equal(ui.nodes['#discount'].disabled, true);
  ui.latest('/api/quotes').reject(new TypeError('Connection lost.'));
  await flush();
  assert.equal(ui.nodes['#retry-load'].hidden, false);
  const loading = ui.nodes['#retry-load'].dispatch('click');
  ui.latest('/api/quotes').respond(200, { quotes: QUOTES });
  await loading;
  ui.latest('/api/preview').respond(200, allowed);
  await flush();
  assert.equal(ui.nodes['#discount'].disabled, false);
  assert.equal(ui.nodes['#retry-load'].hidden, true);
  await ui.change('');
  assert.equal(ui.nodes['#discount'].getAttribute('aria-invalid'), 'true');
  assert.equal(ui.nodes['#send-quote'].disabled, true);
  await ui.submit();
  const invalid = ui.change('100');
  ui.latest('/api/preview').respond(400, { error: { code: 'INVALID_INPUT', message: 'Net must be positive.' } });
  await invalid;
  assert.equal(ui.nodes['#send-quote'].disabled, true);
  const failed = ui.change('1');
  ui.latest('/api/preview').reject(new TypeError('Connection lost.'));
  await failed;
  assert.equal(ui.nodes['#send-quote'].disabled, true);
  const valid = ui.change('0');
  ui.latest('/api/preview').respond(200, allowed);
  await valid;
  assert.equal(ui.nodes['#discount'].getAttribute('aria-invalid'), null);
  assert.equal(ui.nodes['#send-quote'].disabled, false);
  assert.equal(ui.sends(), 0);
});

test('a preview without a server decision cannot authorize sending', async (t) => {
  const ui = await readyUi(t);
  const pending = ui.change('0');
  const { eligibility, ...withoutDecision } = allowed;
  ui.latest('/api/preview').respond(200, withoutDecision);
  await pending;
  assert.equal(ui.nodes['#send-quote'].disabled, true);
  assert.equal(ui.nodes['#feedback'].dataset.tone, 'error');
  assert.ok(ui.nodes['#feedback'].textContent.length > 0);
});

function finishBody(request, mode) {
  if (mode === 'json') request.resolveBody(rejection);
  if (mode === 'malformed') request.rejectBody(new SyntaxError('Invalid JSON.'));
  if (mode === 'disconnected') request.rejectBody(new TypeError('Body stream disconnected.'));
  if (mode === 'timeout') {
    request.expire();
    assert.equal(request.options.signal.aborted, true);
    if (request.ignoreAbort) request.rejectBody(new DOMException('Late abort.', 'AbortError'));
  }
}

for (const mode of ['json', 'malformed', 'disconnected', 'timeout']) {
  test(`latest send 422 invalidates approval before reading its ${mode} body and stays locked`, async (t) => {
    const ui = await readyUi(t);
    const sending = ui.submit();
    const request = ui.latest('/api/send');
    request.resolveHeaders(422);
    await flush();
    assert.equal(request.bodyStarted, true);
    assert.equal(request.allowedAtBodyRead, false, '422 headers must revoke the old approval before json()');
    assert.equal(request.previewInputAtBodyRead, null);
    finishBody(request, mode);
    await sending;
    assert.equal(ui.nodes['#send-quote'].disabled, true);
    assert.equal(ui.nodes['#quote-summary'].getAttribute('aria-busy'), 'false');
    assert.equal(ui.nodes['#feedback'].dataset.tone, 'error');
    assert.match(ui.nodes['#quote-status'].textContent, /입력값.*변경/);
    await ui.submit();
    ui.nodes['#send-quote'].disabled = false;
    await ui.submit();
    assert.equal(ui.sends(), 1, 'neither retry nor a disabled-button bypass may send');
    const blocked = ui.change('10');
    ui.latest('/api/preview').respond(200, belowMinimum);
    await blocked;
    assert.equal(ui.nodes['#send-quote'].disabled, true);
    const failed = ui.change('1');
    ui.latest('/api/preview').reject(new TypeError('Preview unavailable.'));
    await failed;
    assert.equal(ui.nodes['#send-quote'].disabled, true);
    const permitted = ui.change('0');
    assert.equal(ui.nodes['#send-quote'].disabled, true);
    ui.latest('/api/preview').respond(200, allowed);
    await permitted;
    assert.equal(ui.nodes['#send-quote'].disabled, false);
    assert.equal(ui.sends(), 1, 'a new allowed preview must not automatically resend');
    const retry = ui.submit();
    ui.latest('/api/send').respond(200, sent());
    await retry;
    assert.equal(ui.sends(), 2);
    assert.equal(ui.nodes['#feedback'].dataset.tone, 'success');
  });

  for (const latestState of ['sending', 'sent']) {
    test(`stale 422 ${mode} body cannot change the newer ${latestState} UI`, async (t) => {
      const ui = await readyUi(t);
      const oldSend = ui.submit();
      const oldRequest = ui.latest('/api/send');
      oldRequest.ignoreAbort = true;
      oldRequest.resolveHeaders(422);
      await flush();
      const previewing = ui.change('0', 'Q-1500');
      const newer = previewFor(cases[2]);
      ui.latest('/api/preview').respond(200, newer);
      await previewing;
      const newSend = ui.submit();
      const newRequest = ui.latest('/api/send');
      if (latestState === 'sent') {
        newRequest.respond(200, sent(newer));
        await newSend;
      }
      const current = ui.snapshot();
      finishBody(oldRequest, mode);
      await oldSend;
      assert.deepEqual(ui.snapshot(), current);
      assert.equal(ui.sends(), 2);
      if (latestState === 'sending') {
        assert.equal(ui.nodes['#send-quote'].disabled, true);
        newRequest.respond(200, sent(newer));
        await newSend;
      }
    });
  }
}

for (const outcome of ['allowed', 'error']) {
  test(`stale preview ${outcome} cannot replace the newer blocked preview`, async (t) => {
    const ui = await readyUi(t);
    const oldPreview = ui.change('0', 'Q-1500');
    const oldRequest = ui.latest('/api/preview');
    oldRequest.ignoreAbort = true;
    const newPreview = ui.change('10', 'Q-1001');
    ui.latest('/api/preview').respond(200, belowMinimum);
    await newPreview;
    const current = ui.snapshot();
    if (outcome === 'allowed') oldRequest.respond(200, previewFor(cases[2]));
    else oldRequest.reject(new TypeError('Late network failure.'));
    await oldPreview;
    assert.deepEqual(ui.snapshot(), current);
    assert.equal(ui.nodes['#send-quote'].disabled, true);
  });
}

for (const status of [200, 422, 500]) {
  test(`stale send ${status} headers and body cannot affect the newest input`, async (t) => {
    const ui = await readyUi(t);
    const oldSend = ui.submit();
    const oldRequest = ui.latest('/api/send');
    oldRequest.ignoreAbort = true;
    const newPreview = ui.change('0', 'Q-1500');
    ui.latest('/api/preview').respond(200, previewFor(cases[2]));
    await newPreview;
    const current = ui.snapshot();
    oldRequest.resolveHeaders(status);
    await flush();
    assert.deepEqual(ui.snapshot(), current, 'stale headers must not revoke a newer approval');
    oldRequest.resolveBody(status === 200 ? sent() : rejection);
    await oldSend;
    assert.deepEqual(ui.snapshot(), current);
    assert.equal(ui.sends(), 1);
    assert.notEqual(ui.nodes['#feedback'].dataset.tone, 'success');
  });
}

test('changing input during send clears success and ignores an old success while preview is pending', async (t) => {
  const ui = await readyUi(t);
  const firstSend = ui.submit();
  ui.latest('/api/send').respond(200, sent());
  await firstSend;
  const oldSend = ui.submit();
  const oldRequest = ui.latest('/api/send');
  oldRequest.ignoreAbort = true;
  const newPreview = ui.change('10');
  assert.equal(ui.nodes['#feedback'].textContent, '');
  const current = ui.snapshot();
  oldRequest.respond(200, sent());
  await oldSend;
  assert.deepEqual(ui.snapshot(), current);
  ui.latest('/api/preview').respond(200, belowMinimum);
  await newPreview;
  assert.equal(ui.nodes['#send-quote'].disabled, true);
  assert.notEqual(ui.nodes['#feedback'].dataset.tone, 'success');
});
