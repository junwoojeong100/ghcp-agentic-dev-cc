import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { createAppServer } from '../src/server.mjs';
import {
  MINIMUM_MARGIN_BPS,
  QUOTES,
  calculateQuote,
  evaluateSendPolicy,
} from '../src/quote.mjs';

async function startServer(t) {
  const server = createAppServer();
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

test('evaluates the minimum margin with exact integer boundaries', () => {
  const cases = [
    ['Q-1001', 1000, 9_000_000, 1_000_000, false],
    ['Q-1001', 0, 10_000_000, 2_000_000, true],
    ['Q-1500', 0, 10_000_000, 1_500_000, true],
    ['Q-1499', 0, 10_000_000, 1_499_999, false],
    ['Q-1501', 0, 10_000_000, 1_500_001, true],
  ];

  assert.equal(MINIMUM_MARGIN_BPS, 1500);
  for (const [quoteId, discountBps, netWon, profitWon, sendAllowed] of cases) {
    const calculation = calculateQuote(QUOTES.find((quote) => quote.id === quoteId), discountBps);
    assert.equal(calculation.netWon, netWon);
    assert.equal(calculation.profitWon, profitWon);
    const policy = evaluateSendPolicy(calculation);
    assert.equal(policy.sendAllowed, sendAllowed);
    assert.equal(policy.minimumMarginBps, 1500);
    assert.equal(policy.blockReason === null, sendAllowed);
  }

  const roundedBoundary = calculateQuote(QUOTES.find((quote) => quote.id === 'Q-1499'), 0);
  assert.equal(roundedBoundary.marginPercent.toFixed(1), '15.0');
  assert.equal(evaluateSendPolicy(roundedBoundary).sendAllowed, false);
});

test('returns server-owned decisions and reasons from preview', async (t) => {
  const base = await startServer(t);
  for (const [quoteId, discountBps, sendAllowed] of [
    ['Q-1001', 1000, false],
    ['Q-1001', 0, true],
    ['Q-1500', 0, true],
    ['Q-1499', 0, false],
    ['Q-1501', 0, true],
  ]) {
    const response = await post(base, '/api/preview', { quoteId, discountBps });
    assert.equal(response.status, 200);
    const body = await response.json();
    assert.equal(body.sendAllowed, sendAllowed);
    assert.equal(body.minimumMarginBps, 1500);
    if (sendAllowed) {
      assert.equal(body.blockReason, null);
    } else {
      assert.equal(typeof body.blockReason, 'string');
      assert.ok(body.blockReason.length > 0);
    }
  }
});

test('rejects a below-minimum direct send and preserves the allowed response shape', async (t) => {
  const base = await startServer(t);
  const blockedResponse = await post(base, '/api/send', { quoteId: 'Q-1001', discountBps: 1000 });
  assert.equal(blockedResponse.status, 422);
  assert.equal(blockedResponse.headers.get('cache-control'), 'no-store');
  const blocked = await blockedResponse.json();
  assert.equal(blocked.error.code, 'MARGIN_BELOW_MINIMUM');
  assert.equal(typeof blocked.error.message, 'string');
  assert.ok(blocked.error.message.length > 0);

  const allowedResponse = await post(base, '/api/send', { quoteId: 'Q-1500', discountBps: 0 });
  assert.equal(allowedResponse.status, 200);
  const allowed = await allowedResponse.json();
  assert.equal(allowed.sent, true);
  assert.equal(allowed.simulation, true);
  assert.deepEqual(Object.keys(allowed.quote), [
    'quoteId',
    'title',
    'customer',
    'listPriceWon',
    'costWon',
    'discountBps',
    'netWon',
    'profitWon',
    'marginPercent',
  ]);
});

test('rejects forged policy fields and preserves existing input validation', async (t) => {
  const base = await startServer(t);
  for (const path of ['/api/preview', '/api/send']) {
    for (const extra of [
      { listPriceWon: 100_000_000 },
      { costWon: 1 },
      { netWon: 100_000_000 },
      { profitWon: 100_000_000 },
      { marginPercent: 100 },
      { sendAllowed: true },
      { minimumMarginBps: 0 },
      { blockReason: null },
    ]) {
      const response = await post(base, path, { quoteId: 'Q-1001', discountBps: 1000, ...extra });
      assert.equal(response.status, 400);
      assert.equal((await response.json()).error.code, 'INVALID_INPUT');
    }

    for (const body of [
      { quoteId: 'Q-1001', discountBps: -1 },
      { quoteId: 'Q-1001', discountBps: 10_001 },
      { quoteId: 'Q-1001', discountBps: 0.5 },
      { quoteId: 'Q-1001', discountBps: '1000' },
      { quoteId: 'Q-1001', discountBps: 10_000 },
    ]) {
      const response = await post(base, path, body);
      assert.equal(response.status, 400);
      assert.equal((await response.json()).error.code, 'INVALID_INPUT');
    }

    const unknown = await post(base, path, { quoteId: 'UNKNOWN', discountBps: 0 });
    assert.equal(unknown.status, 404);
    assert.equal((await unknown.json()).error.code, 'QUOTE_NOT_FOUND');
  }
});
