import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { runInNewContext } from 'node:vm';
import { createAppServer } from '../src/server.mjs';
import { QUOTES } from '../src/quote.mjs';

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

async function post(base, path, body) {
  return fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

const marginCases = [
  { quoteId: 'Q-1001', discountBps: 1000, allowed: false, netWon: 9_000_000, profitWon: 1_000_000 },
  { quoteId: 'Q-1001', discountBps: 0, allowed: true, netWon: 10_000_000, profitWon: 2_000_000 },
  { quoteId: 'Q-1500', discountBps: 0, allowed: true, netWon: 10_000_000, profitWon: 1_500_000 },
  { quoteId: 'Q-1499', discountBps: 0, allowed: false, netWon: 10_000_000, profitWon: 1_499_999 },
  { quoteId: 'Q-1501', discountBps: 0, allowed: true, netWon: 10_000_000, profitWon: 1_500_001 },
];

for (const { quoteId, discountBps, allowed, netWon, profitWon } of marginCases) {
  test(`direct send enforces the margin boundary: ${quoteId}, ${discountBps} BPS`, async (t) => {
    const base = await startServer(t);
    const response = await post(base, '/api/send', { quoteId, discountBps });
    assert.equal(response.status, allowed ? 200 : 422);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const body = await response.json();
    if (allowed) {
      assert.equal(body.sent, true);
      assert.equal(body.simulation, true);
      assert.equal(body.message, '모의 전송 완료 — 외부 발송 없음');
      assert.equal(body.quote.netWon, netWon);
      assert.equal(body.quote.profitWon, profitWon);
    } else {
      assert.equal(body.error.code, 'MARGIN_BELOW_MINIMUM');
      assert.equal(typeof body.error.message, 'string');
      assert.ok(body.error.message.length > 0);
      assert.equal(body.sent, undefined);
      assert.equal(body.message, undefined);
    }
  });
}

function assertEligibility(calculation, allowed) {
  assert.equal(calculation.sendEligibility.allowed, allowed);
  assert.equal(calculation.sendEligibility.minimumMarginPercent, 15);
  if (allowed) {
    assert.equal(calculation.sendEligibility.reason, null);
  } else {
    assert.equal(calculation.sendEligibility.reason.code, 'MARGIN_BELOW_MINIMUM');
    assert.match(calculation.sendEligibility.reason.message, /15%/);
  }
}

for (const { quoteId, discountBps, allowed, netWon, profitWon } of marginCases) {
  test(`preview and send share the server decision: ${quoteId}, ${discountBps} BPS`, async (t) => {
    const base = await startServer(t);
    const input = { quoteId, discountBps };
    const response = await post(base, '/api/preview', input);
    assert.equal(response.status, 200);
    const preview = await response.json();
    assert.equal(preview.quoteId, quoteId);
    assert.equal(preview.discountBps, discountBps);
    assert.equal(preview.netWon, netWon);
    assert.equal(preview.profitWon, profitWon);
    assert.equal(preview.marginPercent, profitWon / netWon * 100);
    assertEligibility(preview, allowed);
    const sendResponse = await post(base, '/api/send', input);
    assert.equal(sendResponse.status, allowed ? 200 : 422);
    const sent = await sendResponse.json();
    assert.deepEqual(sent.quote, preview);
    if (!allowed) assert.deepEqual(sent.error, preview.sendEligibility.reason);
  });
}

test('the policy uses rounded whole won and preserves single-BPS discounts', async (t) => {
  const quote = { ...QUOTES[0], id: 'ROUND', listPriceWon: 39, costWon: 17 };
  const base = await startServer(t, { quotes: [...QUOTES, quote] });
  for (const [discountBps, netWon, allowed] of [[5000, 20, true], [5001, 19, false]]) {
    const input = { quoteId: quote.id, discountBps };
    const preview = await (await post(base, '/api/preview', input)).json();
    assert.equal(preview.netWon, netWon);
    assert.equal(preview.profitWon, netWon - 17);
    assertEligibility(preview, allowed);
    assert.equal((await post(base, '/api/send', input)).status, allowed ? 200 : 422);
  }
  const preview = await (await post(base, '/api/preview', { quoteId: 'Q-1001', discountBps: 1 })).json();
  assert.equal(preview.discountBps, 1);
  assert.equal(preview.netWon, 9_999_000);
  assertEligibility(preview, true);
});

test('one-won boundaries remain exact at the money limits', async (t) => {
  const cases = [
    { listPriceWon: 1_000_000_000, costWon: 849_999_999, allowed: true },
    { listPriceWon: 1_000_000_000, costWon: 850_000_000, allowed: true },
    { listPriceWon: 1_000_000_000, costWon: 850_000_001, allowed: false },
    { listPriceWon: 1, costWon: 1, allowed: false },
    { listPriceWon: 1, costWon: 1_000_000_000, allowed: false },
  ];
  const quotes = cases.map(({ listPriceWon, costWon }, index) => ({
    ...QUOTES[0], id: `LIMIT-${index}`, listPriceWon, costWon,
  }));
  const base = await startServer(t, { quotes });
  for (const [index, { allowed }] of cases.entries()) {
    const input = { quoteId: quotes[index].id, discountBps: 0 };
    const response = await post(base, '/api/preview', input);
    assert.equal(response.status, 200);
    assertEligibility(await response.json(), allowed);
    assert.equal((await post(base, '/api/send', input)).status, allowed ? 200 : 422);
  }
  for (const path of ['/api/preview', '/api/send']) {
    const response = await post(base, path, { quoteId: 'LIMIT-3', discountBps: 6000 });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error.code, 'INVALID_INPUT');
  }
});

test('client policy overrides and invalid inputs keep their 400/404 precedence', async (t) => {
  const base = await startServer(t);
  const input = { quoteId: 'Q-1001', discountBps: 1000 };
  const invalid = [
    ...Object.entries({
      listPriceWon: 1_000_000_000, costWon: 1, netWon: 1_000_000_000,
      profitWon: 1_000_000_000, marginPercent: 100, minimumMarginPercent: 0,
      allowed: true, sendEligibility: { allowed: true, minimumMarginPercent: 0, reason: null },
    }).map(([key, value]) => ({ ...input, [key]: value })),
    ...[-1, 10_001, 10_000, 0.5, '0', null, true].map((discountBps) => ({ ...input, discountBps })),
    { quoteId: 'Q-1001' },
    { quoteId: 'UNKNOWN', discountBps: -1 },
    null,
    [],
  ];
  for (const path of ['/api/preview', '/api/send']) {
    for (const body of invalid) {
      const response = await post(base, path, body);
      assert.equal(response.status, 400, `${path}: ${JSON.stringify(body)}`);
      assert.equal((await response.json()).error.code, 'INVALID_INPUT');
    }
    for (const discountBps of [0, 10_000]) {
      const response = await post(base, path, { quoteId: 'UNKNOWN', discountBps });
      assert.equal(response.status, 404);
      assert.equal((await response.json()).error.code, 'QUOTE_NOT_FOUND');
    }
  }
});

test('send recalculates both the discount and the current server-owned cost', async (t) => {
  const quote = { ...QUOTES[0] };
  const base = await startServer(t, { quotes: [quote] });
  const input = { quoteId: quote.id, discountBps: 0 };
  assertEligibility(await (await post(base, '/api/preview', input)).json(), true);
  assert.equal((await post(base, '/api/send', { ...input, discountBps: 1000 })).status, 422);
  quote.costWon = 8_500_001;
  const response = await post(base, '/api/send', input);
  assert.equal(response.status, 422);
  const body = await response.json();
  assert.equal(body.quote.costWon, 8_500_001);
  assertEligibility(body.quote, false);
});

const appSource = await readFile(new URL('../public/app.js', import.meta.url), 'utf8');

class Element {
  value = '';
  validity = { valid: true };
  disabled = false;
  hidden = false;
  textContent = '';
  dataset = {};
  firstElementChild = { textContent: '' };
  attributes = new Map();
  listeners = new Map();

  addEventListener(type, listener) {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type) {
    return Promise.all((this.listeners.get(type) ?? []).map((listener) => listener({
      preventDefault() {},
    })));
  }

  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  replaceChildren(...children) { this.children = children; }
}

function createUi() {
  const ids = [
    'quote-form', 'quote-id', 'discount', 'send-quote', 'feedback', 'retry-load',
    'quote-status', 'quote-summary', 'preview-label', 'quote-reference',
    'quote-title', 'quote-customer', 'list-amount', 'cost-amount', 'net-amount',
    'profit-amount', 'margin-value',
  ];
  const elements = new Map(ids.map((id) => [`#${id}`, new Element()]));
  const get = (selector) => {
    assert.ok(elements.has(selector), `Missing test DOM element: ${selector}`);
    return elements.get(selector);
  };
  get('#discount').value = '10';
  get('#send-quote').disabled = true;
  const requests = [];
  const timers = new Set();
  runInNewContext(appSource, {
    document: {
      querySelector: get,
      createElement(tag) {
        assert.equal(tag, 'option');
        return new Element();
      },
    },
    AbortController, Error, TypeError, SyntaxError,
    setTimeout(callback, milliseconds) {
      const timer = { callback, milliseconds };
      timers.add(timer);
      return timer;
    },
    clearTimeout(timer) { timers.delete(timer); },
    // Deliberately ignore cancellation so stale responses exercise the revision guard.
    fetch(path, options) {
      return new Promise((resolve, reject) => {
        requests.push({
          path,
          input: options.body ? JSON.parse(options.body) : undefined,
          signal: options.signal,
          reject,
          respond(body, status = 200) {
            resolve({ ok: status >= 200 && status < 300, status, json: async () => structuredClone(body) });
          },
          invalidJson() {
            resolve({ ok: true, status: 200, json: async () => { throw new SyntaxError('Invalid JSON'); } });
          },
        });
      });
    },
  }, { filename: 'public/app.js' });
  return { get, requests, timers };
}

async function settleUi() {
  await new Promise(setImmediate);
}

async function replyFromApi(base, request) {
  const response = request.input
    ? await post(base, request.path, request.input)
    : await fetch(`${base}${request.path}`);
  const body = await response.json();
  request.respond(body, response.status);
  await settleUi();
  return body;
}

async function startUi(t, options) {
  const base = await startServer(t, options);
  const ui = { ...createUi(), base };
  await replyFromApi(base, ui.requests[0]);
  await replyFromApi(base, ui.requests[1]);
  return ui;
}

function changeInput(ui, quoteId, discount) {
  const quoteChanged = ui.get('#quote-id').value !== quoteId;
  ui.get('#quote-id').value = quoteId;
  ui.get('#discount').value = discount;
  return ui.get(quoteChanged ? '#quote-id' : '#discount').dispatch(quoteChanged ? 'change' : 'input');
}

async function showPreview(ui, quoteId, discount) {
  const pending = changeInput(ui, quoteId, discount);
  const result = await replyFromApi(ui.base, ui.requests.at(-1));
  await pending;
  return result;
}

for (const { quoteId, discountBps, allowed, netWon, profitWon } of marginCases) {
  test(`UI follows the real server decision: ${quoteId}, ${discountBps} BPS`, async (t) => {
    const ui = await startUi(t);
    const preview = await showPreview(ui, quoteId, String(discountBps / 100));
    assert.equal(ui.get('#net-amount').textContent, `${netWon.toLocaleString('ko-KR')}원`);
    assert.equal(ui.get('#profit-amount').textContent, `${profitWon.toLocaleString('ko-KR')}원`);
    assert.equal(ui.get('#send-quote').disabled, !allowed);
    assert.equal(ui.get('#discount').getAttribute('aria-invalid'), null);
    if (quoteId.startsWith('Q-15') || quoteId === 'Q-1499') {
      assert.equal(ui.get('#margin-value').textContent, '15.0%');
    }
    const count = ui.requests.length;
    const sending = ui.get('#quote-form').dispatch('submit');
    if (allowed) {
      assert.equal(ui.requests.length, count + 1);
      assert.equal(ui.requests.at(-1).path, '/api/send');
      assert.deepEqual(ui.requests.at(-1).input, { quoteId, discountBps });
      await replyFromApi(ui.base, ui.requests.at(-1));
      await sending;
      assert.equal(ui.get('#feedback').dataset.tone, 'success');
      assert.match(ui.get('#feedback').textContent, /외부 발송 없음/);
      assert.equal(ui.get('#send-quote').disabled, false);
    } else {
      await sending;
      assert.equal(ui.requests.length, count);
      assert.equal(ui.get('#feedback').textContent, preview.sendEligibility.reason.message);
      assert.equal(ui.get('#feedback').dataset.tone, 'error');
    }
  });
}

test('UI preserves BPS input and invalidates prior success for invalid or zero-net input', async (t) => {
  const ui = await startUi(t);
  await showPreview(ui, 'Q-1001', '0.01');
  assert.deepEqual(ui.requests.at(-1).input, { quoteId: 'Q-1001', discountBps: 1 });
  const sending = ui.get('#quote-form').dispatch('submit');
  await replyFromApi(ui.base, ui.requests.at(-1));
  await sending;
  assert.equal(ui.get('#feedback').dataset.tone, 'success');
  for (const discount of ['', '-1', '100.01', '0.001', '1e1']) {
    const count = ui.requests.length;
    await changeInput(ui, 'Q-1001', discount);
    assert.equal(ui.requests.length, count);
    assert.equal(ui.get('#send-quote').disabled, true);
    assert.equal(ui.get('#feedback').dataset.tone, 'error');
    assert.equal(ui.get('#discount').getAttribute('aria-invalid'), 'true');
    assert.equal(ui.get('#net-amount').textContent, '—');
  }
  await showPreview(ui, 'Q-1001', '100');
  assert.equal(ui.get('#send-quote').disabled, true);
  assert.equal(ui.get('#feedback').dataset.tone, 'error');
  assert.match(ui.get('#feedback').textContent, /1원/);
});

for (const [oldDiscount, newQuote, newDiscount, allowed] of [
  ['0', 'Q-1001', '10', false],
  ['10', 'Q-1001', '0', true],
  ['0', 'Q-1499', '0', false],
]) {
  test(`UI ignores an older preview after ${newQuote}/${newDiscount}%`, async (t) => {
    const ui = await startUi(t);
    const older = changeInput(ui, 'Q-1001', oldDiscount);
    const oldRequest = ui.requests.at(-1);
    const latest = changeInput(ui, newQuote, newDiscount);
    assert.equal(oldRequest.signal.aborted, true);
    assert.equal(ui.get('#send-quote').disabled, true);
    assert.equal(ui.get('#feedback').textContent, '');
    const result = await replyFromApi(ui.base, ui.requests.at(-1));
    await latest;
    await replyFromApi(ui.base, oldRequest);
    await older;
    assert.equal(ui.get('#send-quote').disabled, !allowed);
    assert.equal(ui.get('#net-amount').textContent, `${result.netWon.toLocaleString('ko-KR')}원`);
    assert.equal(ui.get('#feedback').textContent, allowed ? '' : result.sendEligibility.reason.message);
    assert.equal(ui.get('#quote-summary').getAttribute('aria-busy'), 'false');
  });
}

test('UI ignores an older preview error and clears success as soon as input changes', async (t) => {
  const ui = await startUi(t);
  await showPreview(ui, 'Q-1001', '0');
  const sending = ui.get('#quote-form').dispatch('submit');
  await replyFromApi(ui.base, ui.requests.at(-1));
  await sending;
  const older = changeInput(ui, 'Q-1001', '1');
  const oldRequest = ui.requests.at(-1);
  assert.equal(ui.get('#feedback').textContent, '');
  assert.equal(ui.get('#send-quote').disabled, true);
  await showPreview(ui, 'Q-1500', '0');
  oldRequest.reject(new TypeError('Older network error'));
  await older;
  assert.equal(ui.get('#send-quote').disabled, false);
  assert.equal(ui.get('#feedback').textContent, '');
  assert.equal(ui.get('#margin-value').textContent, '15.0%');
});

for (const outcome of ['success', 'error']) {
  for (const previewFirst of [false, true]) {
    test(`UI ignores stale send ${outcome}; latest preview completed=${previewFirst}`, async (t) => {
      const ui = await startUi(t);
      await showPreview(ui, 'Q-1001', '0');
      const sending = ui.get('#quote-form').dispatch('submit');
      const sendRequest = ui.requests.at(-1);
      const latest = changeInput(ui, 'Q-1499', '0');
      const previewRequest = ui.requests.at(-1);
      assert.equal(sendRequest.signal.aborted, true);
      if (previewFirst) await replyFromApi(ui.base, previewRequest);
      const feedback = ui.get('#feedback').textContent;
      const busy = ui.get('#quote-summary').getAttribute('aria-busy');
      if (outcome === 'success') await replyFromApi(ui.base, sendRequest);
      else sendRequest.respond({ error: { code: 'INTERNAL_ERROR', message: 'Older send error' } }, 500);
      await sending;
      assert.equal(ui.get('#send-quote').disabled, true);
      assert.equal(ui.get('#feedback').textContent, feedback);
      assert.equal(ui.get('#quote-summary').getAttribute('aria-busy'), busy);
      if (!previewFirst) await replyFromApi(ui.base, previewRequest);
      await latest;
      assert.equal(ui.get('#feedback').dataset.tone, 'error');
      assert.match(ui.get('#feedback').textContent, /15%/);
    });
  }
}

test('a latest send 422 discards permission until a new allowed preview', async (t) => {
  const quote = { ...QUOTES[0] };
  const ui = await startUi(t, { quotes: [quote] });
  await showPreview(ui, quote.id, '0');
  quote.costWon = 8_500_001;
  const sending = ui.get('#quote-form').dispatch('submit');
  const result = await replyFromApi(ui.base, ui.requests.at(-1));
  await sending;
  assert.equal(result.error.code, 'MARGIN_BELOW_MINIMUM');
  assert.equal(ui.get('#send-quote').disabled, true);
  assert.equal(ui.get('#feedback').textContent, result.error.message);
  assert.notEqual(ui.get('#margin-value').textContent, '20.0%');
  const count = ui.requests.length;
  await ui.get('#quote-form').dispatch('submit');
  assert.equal(ui.requests.length, count);
  await showPreview(ui, quote.id, '0.01');
  assert.equal(ui.get('#send-quote').disabled, true);
  quote.costWon = 8_000_000;
  await showPreview(ui, quote.id, '0');
  assert.equal(ui.get('#send-quote').disabled, false);
  assert.equal(ui.get('#feedback').textContent, '');
});

test('UI rejects missing, malformed, contradictory, or mismatched preview decisions', async (t) => {
  const ui = await startUi(t);
  const valid = await showPreview(ui, 'Q-1001', '0');
  const mutations = [
    (body) => { delete body.sendEligibility; },
    (body) => { body.sendEligibility.allowed = 'true'; },
    (body) => { body.sendEligibility.minimumMarginPercent = '15'; },
    (body) => { body.sendEligibility.reason = { code: 'MARGIN_BELOW_MINIMUM', message: 'Blocked' }; },
    (body) => { body.sendEligibility.allowed = false; body.sendEligibility.reason = null; },
    (body) => { body.quoteId = 'Q-1499'; },
    (body) => { body.discountBps = 1000; },
    (body) => { body.netWon = 0; },
    (body) => { body.profitWon = '2000000'; },
    (body) => { body.marginPercent = null; },
  ];
  for (const mutate of mutations) {
    const pending = changeInput(ui, 'Q-1001', '0');
    const body = structuredClone(valid);
    mutate(body);
    ui.requests.at(-1).respond(body);
    await pending;
    assert.equal(ui.get('#send-quote').disabled, true);
    assert.equal(ui.get('#feedback').dataset.tone, 'error');
    assert.ok(ui.get('#feedback').textContent.length > 0);
    assert.equal(ui.get('#net-amount').textContent, '—');
  }
});

test('UI does not report success for malformed send responses', async (t) => {
  const ui = await startUi(t);
  const quote = await showPreview(ui, 'Q-1001', '0');
  for (const body of [
    { sent: true, simulation: false, quote, message: 'Incorrect success' },
    { sent: true, simulation: true, message: 'Missing quote' },
    { sent: true, simulation: true, quote: { ...quote, quoteId: 'Q-1499' }, message: 'Wrong quote' },
  ]) {
    const sending = ui.get('#quote-form').dispatch('submit');
    ui.requests.at(-1).respond(body);
    await sending;
    assert.equal(ui.get('#feedback').dataset.tone, 'error');
    assert.doesNotMatch(ui.get('#quote-status').textContent, /완료했습니다/);
  }
});

test('UI prevents duplicate sends and re-previews input changed without an event', async (t) => {
  const ui = await startUi(t);
  await showPreview(ui, 'Q-1001', '0');
  ui.get('#discount').value = '10';
  await ui.get('#quote-form').dispatch('submit');
  assert.equal(ui.requests.at(-1).path, '/api/preview');
  await replyFromApi(ui.base, ui.requests.at(-1));
  assert.equal(ui.get('#send-quote').disabled, true);
  await showPreview(ui, 'Q-1001', '0');
  const sending = ui.get('#quote-form').dispatch('submit');
  const count = ui.requests.length;
  await ui.get('#quote-form').dispatch('submit');
  assert.equal(ui.requests.length, count);
  await replyFromApi(ui.base, ui.requests.at(-1));
  await sending;
  assert.equal(ui.get('#feedback').dataset.tone, 'success');
});

test('UI surfaces current preview transport, JSON and timeout failures and can recover', async (t) => {
  const ui = await startUi(t);
  for (const failure of ['network', 'json', 'timeout']) {
    const pending = changeInput(ui, 'Q-1001', '0');
    const request = ui.requests.at(-1);
    if (failure === 'network') request.reject(new TypeError('Disconnected'));
    if (failure === 'json') request.invalidJson();
    if (failure === 'timeout') {
      assert.equal(ui.timers.size, 1);
      const [timer] = ui.timers;
      assert.equal(timer.milliseconds, 10_000);
      timer.callback();
      assert.equal(request.signal.aborted, true);
      const error = new Error('Timed out');
      error.name = 'AbortError';
      request.reject(error);
    }
    await pending;
    assert.equal(ui.get('#send-quote').disabled, true);
    assert.equal(ui.get('#feedback').dataset.tone, 'error');
    assert.equal(ui.get('#discount').getAttribute('aria-invalid'), null);
    assert.equal(ui.get('#quote-summary').getAttribute('aria-busy'), 'false');
    await showPreview(ui, 'Q-1001', '0');
    assert.equal(ui.get('#send-quote').disabled, false);
    assert.equal(ui.get('#feedback').textContent, '');
  }
});

test('UI keeps the existing quote-list error and retry flow', async (t) => {
  const base = await startServer(t);
  const ui = createUi();
  ui.requests[0].respond({ error: { code: 'INTERNAL_ERROR', message: 'List unavailable' } }, 500);
  await settleUi();
  assert.equal(ui.get('#quote-id').disabled, true);
  assert.equal(ui.get('#discount').disabled, true);
  assert.equal(ui.get('#send-quote').disabled, true);
  assert.equal(ui.get('#retry-load').hidden, false);
  assert.equal(ui.get('#feedback').textContent, 'List unavailable');
  const retry = ui.get('#retry-load').dispatch('click');
  await replyFromApi(base, ui.requests.at(-1));
  await retry;
  await replyFromApi(base, ui.requests.at(-1));
  assert.equal(ui.get('#quote-id').disabled, false);
  assert.equal(ui.get('#discount').disabled, false);
  assert.equal(ui.get('#retry-load').hidden, true);
  assert.equal(ui.get('#send-quote').disabled, true);
  assert.match(ui.get('#feedback').textContent, /15%/);
});
