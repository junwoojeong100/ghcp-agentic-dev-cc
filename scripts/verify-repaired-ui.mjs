import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(import.meta.dirname, '..');
const workspace = resolve(root, 'demo/ghcp-live/repaired');
const output = resolve(root, 'evidence/ghcp-live/repair-verification/browser');
const { createAppServer } = await import(pathToFileURL(resolve(workspace, 'src/server.mjs')));
const app = createAppServer();
app.listen(0, '127.0.0.1');
await once(app, 'listening');
let fault = null;
let sends = 0;
const timers = new Set();
const proxy = createServer((req, res) => {
  if (req.url === '/api/send') {
    sends += 1;
    if (fault) {
      res.writeHead(422, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.flushHeaders();
      if (fault === 'json') return res.end('{invalid');
      const timer = setTimeout(() => {
        timers.delete(timer);
        if (fault === 'disconnect') res.destroy();
        else res.end('{');
      }, fault === 'disconnect' ? 250 : 12_000);
      timers.add(timer);
      return;
    }
  }
  const upstream = request({ hostname: '127.0.0.1', port: app.address().port, path: req.url,
    method: req.method, headers: req.headers }, response => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.on('error', () => res.destroy());
  req.pipe(upstream);
});
proxy.listen(0, '127.0.0.1');
await once(proxy, 'listening');
let browser;
const checks = [];
await mkdir(output, { recursive: true });
const appHash = createHash('sha256').update(await readFile(resolve(workspace, 'public/app.js'))).digest('hex');
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  await page.goto(`http://127.0.0.1:${proxy.address().port}/`);
  const preview = async discount => {
    await page.locator('#discount').fill(discount);
    await page.waitForFunction(() => document.querySelector('#quote-summary').getAttribute('aria-busy') === 'false');
  };
  for (const scenario of ['json', 'disconnect', 'timeout']) {
    await preview('0');
    assert.equal(await page.locator('#send-quote').isDisabled(), false);
    fault = scenario;
    await page.locator('#send-quote').click();
    await page.waitForFunction(() => document.querySelector('#quote-summary').getAttribute('aria-busy') === 'false'
      && document.querySelector('#feedback').dataset.tone === 'error', null, { timeout: 15_000 });
    assert.equal(await page.locator('#send-quote').isDisabled(), true);
    assert.equal(await page.locator('#margin-value').textContent(), '—');
    const before = sends;
    await page.locator('#discount').press('Enter');
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    assert.equal(sends, before);
    await page.screenshot({ path: resolve(output, `${scenario}-locked.png`) });
    fault = null;
    await preview('10');
    assert.equal(await page.locator('#send-quote').isDisabled(), true);
    await preview('0');
    assert.equal(await page.locator('#send-quote').isDisabled(), false);
    checks.push({ scenario, httpStatus: 422, faultInjectedByLocalProxy: true, lockPreserved: true,
      noAutomaticResend: true, blockedPreviewStaysLocked: true, allowedPreviewRecovers: true });
  }
  const response = await page.request.post(`http://127.0.0.1:${app.address().port}/api/send`, {
    data: { quoteId: 'Q-1001', discountBps: 1000 },
  });
  assert.equal(response.status(), 422);
  assert.equal((await response.json()).error.code, 'MARGIN_BELOW_MINIMUM');
  await page.locator('#send-quote').click();
  await page.waitForFunction(() => document.querySelector('#feedback').dataset.tone === 'success');
  checks.push({ scenario: 'normal-send-and-direct-policy', simulatedSend: true, directPolicy422: true });
  const after = createHash('sha256').update(await readFile(resolve(workspace, 'public/app.js'))).digest('hex');
  assert.equal(after, appHash);
  const report = { executor: 'Claude production session; outside filmed Copilot', status: 'passed',
    app: 'demo/ghcp-live/repaired', appSha256: appHash, checks,
    note: 'Real Chromium and HTTP. Malformed/stalled/disconnected 422 responses deliberately injected by local proxy; not real production outages.' };
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
  console.log(JSON.stringify(report, null, 2));
} finally {
  for (const timer of timers) clearTimeout(timer);
  await browser?.close();
  for (const server of [proxy, app]) {
    const closed = new Promise(resolve => server.close(resolve));
    server.closeAllConnections();
    await closed;
  }
}
