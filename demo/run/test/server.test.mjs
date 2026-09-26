import test from 'node:test';
import assert from 'node:assert/strict';
import { once } from 'node:events';
import { request as httpRequest } from 'node:http';
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
  assert.equal(server.address().address, '127.0.0.1');
  return { server, base: `http://127.0.0.1:${server.address().port}` };
}

async function post(base, path, body) {
  return fetch(`${base}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

async function assertInvalid(response) {
  assert.equal(response.status, 400);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const body = await response.json();
  assert.equal(body.error.code, 'INVALID_INPUT');
  assert.equal(typeof body.error.message, 'string');
  assert.ok(body.error.message.length > 0);
  return body;
}

test('the server factory does not listen automatically', () => {
  const server = createAppServer();
  assert.equal(server.listening, false);
  assert.equal(server.address(), null);
});

test('lists synthetic quotes as uncached JSON', async (t) => {
  const { base } = await startServer(t);
  const response = await fetch(`${base}/api/quotes`);
  assert.equal(response.status, 200);
  assert.match(response.headers.get('content-type'), /^application\/json/);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  assert.deepEqual((await response.json()).quotes, QUOTES);
});

test('previews the server-calculated quote for a decimal percentage', async (t) => {
  const { base } = await startServer(t);
  const response = await post(base, '/api/preview', { quoteId: 'Q-1001', discountBps: 1000 });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.quoteId, 'Q-1001');
  assert.equal(body.discountBps, 1000);
  assert.equal(body.netWon, 9_000_000);
  assert.equal(body.profitWon, 1_000_000);
  assert.ok(Math.abs(body.marginPercent - 100 / 9) < 1e-10);
});

test('simulates sending an undiscounted quotation without an external dispatch', async (t) => {
  const { base } = await startServer(t);
  const response = await post(base, '/api/send', { quoteId: 'Q-1001', discountBps: 0 });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'no-store');
  const body = await response.json();
  assert.equal(body.sent, true);
  assert.equal(body.simulation, true);
  assert.equal(body.message, '모의 전송 완료 — 외부 발송 없음');
  assert.equal(body.quote.netWon, 10_000_000);
  assert.equal(body.quote.costWon, 8_000_000);
});

test('uses injected server data for calculations', async (t) => {
  const quote = { id: 'TEST-1', title: '합성 테스트 견적', customer: '가상 테스트 고객', listPriceWon: 123, costWon: 40 };
  const { base } = await startServer(t, { quotes: [quote] });
  const response = await post(base, '/api/preview', { quoteId: 'TEST-1', discountBps: 5000 });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.netWon, 62);
  assert.equal(body.profitWon, 22);
});

test('rejects malformed JSON and non-object payloads with structured input errors', async (t) => {
  const { base } = await startServer(t);
  for (const body of ['', '{', 'null', '[]', '42', '"text"']) {
    await assertInvalid(await fetch(`${base}/api/preview`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body,
    }));
  }
  await assertInvalid(await fetch(`${base}/api/preview`, {
    method: 'POST', body: Buffer.from([0x7b, 0xff, 0x7d]),
  }));
});

test('rejects wrong types, extra client amounts, and zero-net input on both POST routes', async (t) => {
  const { base } = await startServer(t);
  for (const path of ['/api/preview', '/api/send']) {
    for (const body of [
      { quoteId: 'Q-1001', discountBps: '0' },
      { quoteId: 'Q-1001', discountBps: 0.5 },
      { quoteId: 'Q-1001', discountBps: -1 },
      { quoteId: 'Q-1001', discountBps: 10_001 },
      { quoteId: 'Q-1001', discountBps: 10_000 },
      { quoteId: 'Q-1001', discountBps: 0, costWon: 1 },
      { quoteId: 'Q-1001', discountBps: 0, netWon: 99_999_999 },
      { quoteId: 'Q-1001' },
    ]) {
      await assertInvalid(await post(base, path, body));
    }
  }
});

test('returns 404 JSON for unknown quotes and unknown routes', async (t) => {
  const { base } = await startServer(t);
  for (const path of ['/api/preview', '/api/send']) {
    const response = await post(base, path, { quoteId: 'UNKNOWN', discountBps: 0 });
    assert.equal(response.status, 404);
    assert.equal((await response.json()).error.code, 'QUOTE_NOT_FOUND');
  }
  for (const path of ['/api/missing', '/api/send', '/package.json', '/src/quote.mjs', '/test/server.test.mjs', '/%2e%2e%2fsrc/server.mjs']) {
    const response = await fetch(`${base}${path}`);
    assert.equal(response.status, 404);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    assert.equal((await response.json()).error.code, 'NOT_FOUND');
  }
  const wrongMethod = await post(base, '/style.css', {});
  assert.equal(wrongMethod.status, 404);
});

test('serves only the explicit static allowlist with content types and security headers', async (t) => {
  const { base } = await startServer(t);
  for (const [path, type] of [['/', 'text/html'], ['/index.html', 'text/html'], ['/app.js', 'text/javascript'], ['/style.css', 'text/css']]) {
    const response = await fetch(`${base}${path}`);
    assert.equal(response.status, 200);
    assert.ok(response.headers.get('content-type').startsWith(type));
    assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
    assert.match(response.headers.get('content-security-policy'), /default-src 'self'/);
    assert.ok((await response.text()).length > 0);
  }
});

test('accepts the 16KB limit and rejects an oversized body without stopping the server', async (t) => {
  const { base } = await startServer(t);
  const json = JSON.stringify({ quoteId: 'Q-1001', discountBps: 0 });
  const body = json + ' '.repeat(16 * 1024 - Buffer.byteLength(json));
  const atLimit = await fetch(`${base}/api/preview`, { method: 'POST', body });
  assert.equal(atLimit.status, 200);
  await atLimit.json();
  const error = await assertInvalid(await fetch(`${base}/api/preview`, { method: 'POST', body: body + ' ' }));
  assert.match(error.error.message, /16KB/);
  assert.equal((await fetch(`${base}/api/quotes`)).status, 200);
});

test('applies the size limit to chunked uploads as well', async (t) => {
  const { base } = await startServer(t);
  const result = await new Promise((resolve, reject) => {
    const request = httpRequest(`${base}/api/preview`, { method: 'POST' }, (response) => {
      const chunks = [];
      response.on('data', (chunk) => chunks.push(chunk));
      response.on('end', () => resolve({ status: response.statusCode, body: JSON.parse(Buffer.concat(chunks)) }));
      response.on('error', reject);
    });
    request.on('error', reject);
    request.write(' '.repeat(10_000));
    request.end(' '.repeat(10_000));
  });
  assert.equal(result.status, 400);
  assert.equal(result.body.error.code, 'INVALID_INPUT');
  assert.equal((await fetch(`${base}/api/quotes`)).status, 200);
});

test('survives a disconnected upload and still answers the next request', { timeout: 5000 }, async (t) => {
  const { base, server } = await startServer(t);
  const incoming = once(server, 'request');
  const client = httpRequest(`${base}/api/preview`, { method: 'POST' });
  client.on('error', () => {});
  client.write('{');
  const [request] = await incoming;
  const aborted = once(request, 'aborted');
  client.destroy();
  await aborted;
  const response = await fetch(`${base}/api/quotes`);
  assert.equal(response.status, 200);
  assert.equal((await response.json()).quotes.length, QUOTES.length);
});
