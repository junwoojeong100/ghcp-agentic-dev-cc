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

