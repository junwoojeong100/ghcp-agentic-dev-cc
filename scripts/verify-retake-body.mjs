import assert from 'node:assert/strict';
import { createServer, request } from 'node:http';
import { once } from 'node:events';
import { createHash } from 'node:crypto';
import { mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from 'playwright';

const root = resolve(import.meta.dirname, '..');
const workspace = '/Users/junwoojeong/ghcp-headless-demo-H976Zg';
const output = resolve(root, 'evidence/ghcp-live/retake-20260926/body-verification');
const hashes = async () => {
  const entries = [];
  for (const dir of ['public', 'src']) {
    for (const name of (await readdir(resolve(workspace, dir))).sort()) {
      const path = `${dir}/${name}`;
      entries.push([path, createHash('sha256').update(await readFile(resolve(workspace, path))).digest('hex')]);
    }
  }
  return Object.fromEntries(entries);
};
await mkdir(output); // Refuse to overwrite evidence from any earlier run.
const report = { startedAt: new Date().toISOString(), workspace, status: 'running', checks: [],
  executor: 'Independent final verification, outside the ended filmed CLI session',
  note: 'Real Chromium and loopback HTTP. Three 422 body faults are deliberately injected by a local proxy; not production outages. Normal send is simulated; direct policy 422 uses the actual app server. Displayed amounts are not required to clear.' };
report.sourceHashesBefore = await hashes();
report.appSha256 = report.sourceHashesBefore['public/app.js'];
const { createAppServer } = await import(pathToFileURL(resolve(workspace, 'src/server.mjs')));
const app = createAppServer();
let fault = null, sends = 0, browser, page;
const timers = new Set();
const proxy = createServer((req, res) => {
  if (req.url === '/api/send') {
    sends += 1;
    if (fault) {
      const mode = fault;
      req.resume();
      res.writeHead(422, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
      res.flushHeaders();
      if (mode === 'malformed-json') return res.end('{invalid');
      const timer = setTimeout(() => {
        timers.delete(timer);
        if (mode === 'body-disconnect') res.destroy();
        else res.end('{');
      }, mode === 'body-disconnect' ? 750 : 12_000);
      timers.add(timer);
      res.on('close', () => { clearTimeout(timer); timers.delete(timer); });
      return;
    }
  }
  const upstream = request({ hostname: '127.0.0.1', port: app.address().port,
    path: req.url, method: req.method, headers: req.headers }, response => {
    res.writeHead(response.statusCode, response.headers);
    response.pipe(res);
  });
  upstream.on('error', () => res.destroy());
  req.pipe(upstream);
});
const check = async (scenario, test) => {
  const result = { scenario, status: 'running' };
  report.checks.push(result);
  try { await test(result); result.status = 'passed'; }
  catch (error) {
    result.status = 'failed'; result.error = error.stack;
    await page?.screenshot({ path: resolve(output, `${scenario}-failure.png`) }).catch(() => {});
  } finally { fault = null; }
};
try {
  app.listen(0, '127.0.0.1'); await once(app, 'listening');
  proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening');
  browser = await chromium.launch({ headless: true });
  page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.setDefaultTimeout(15_000);
  report.chromiumVersion = browser.version();
  const base = `http://127.0.0.1:${proxy.address().port}`;
  const idle = () => page.waitForFunction(() => document.querySelector('#quote-summary')?.getAttribute('aria-busy') === 'false');
  const preview = async value => {
    if (await page.locator('#discount').inputValue() !== value) {
      const response = page.waitForResponse(r => r.url().endsWith('/api/preview') && r.request().postDataJSON().discountBps === Number(value) * 100);
      await page.locator('#discount').fill(value);
      assert.equal((await response).status(), 200);
    }
    await idle();
  };
  const ready = async () => { await page.goto(base); await idle(); await preview('0'); assert.equal(await page.locator('#send-quote').isDisabled(), false); };
  const noResend = async () => {
    const before = sends;
    await page.locator('#discount').press('Enter');
    await page.evaluate(() => { document.querySelector('#send-quote').click(); document.querySelector('#quote-form').requestSubmit(); });
    await page.waitForTimeout(300);
    assert.equal(sends, before);
    assert.equal(await page.locator('#send-quote').isDisabled(), true);
    return { before, after: sends, attempts: ['Enter', 'disabled-button-click', 'form-requestSubmit'] };
  };
  for (const scenario of ['malformed-json', 'body-disconnect', 'body-timeout']) {
    await check(scenario, async result => {
      await ready(); fault = scenario; result.faultInjectedByLocalProxy = true;
      const before = sends, started = Date.now();
      const response = page.waitForResponse(r => r.url().endsWith('/api/send'));
      await page.locator('#send-quote').click();
      result.httpStatus = (await response).status(); assert.equal(result.httpStatus, 422);
      await page.waitForFunction(() => document.querySelector('#preview-label').textContent === '재확인 필요');
      assert.equal(await page.locator('#send-quote').isDisabled(), true);
      if (scenario === 'body-timeout') {
        assert.equal(await page.locator('#quote-summary').getAttribute('aria-busy'), 'true');
        result.revokedWhileBodyPending = true;
        await page.screenshot({ path: resolve(output, `${scenario}-headers-locked.png`) });
      }
      await idle(); result.elapsedMs = Date.now() - started;
      assert.equal(await page.locator('#feedback').getAttribute('data-tone'), 'error');
      assert.equal(sends, before + 1);
      if (scenario === 'body-timeout') {
        assert.ok(result.elapsedMs >= 9_000 && result.elapsedMs < 12_000, 'Expected app timeout before proxy body completion');
        assert.match(await page.locator('#feedback').textContent(), /응답을 받지 못했습니다/);
      }
      result.noResendAfterFailure = await noResend();
      result.feedback = await page.locator('#feedback').textContent();
      result.displayedMarginObserved = await page.locator('#margin-value').textContent();
      await page.screenshot({ path: resolve(output, `${scenario}-locked.png`) });
      fault = null; await preview('10');
      result.blockedPreviewNoResend = await noResend();
      await page.screenshot({ path: resolve(output, `${scenario}-blocked-preview.png`) });
      await preview('0'); assert.equal(await page.locator('#send-quote').isDisabled(), false);
      result.changedAllowedPreviewUnlocks = true;
    });
  }
  await check('normal-simulated-send', async result => {
    await ready();
    const response = page.waitForResponse(r => r.url().endsWith('/api/send'));
    await page.locator('#send-quote').click();
    const actual = await response; result.httpStatus = actual.status(); result.body = await actual.json();
    assert.equal(result.httpStatus, 200); assert.equal(result.body.sent, true); assert.equal(result.body.simulation, true);
    await idle(); assert.equal(await page.locator('#feedback').getAttribute('data-tone'), 'success');
    await page.screenshot({ path: resolve(output, 'normal-simulated-send.png') });
  });
  await check('direct-api-policy-422', async result => {
    const response = await page.request.post(`http://127.0.0.1:${app.address().port}/api/send`, { data: { quoteId: 'Q-1001', discountBps: 1000 } });
    result.faultInjectedByLocalProxy = false; result.httpStatus = response.status(); result.body = await response.json();
    assert.equal(result.httpStatus, 422); assert.equal(result.body.error.code, 'MARGIN_BELOW_MINIMUM');
    assert.equal(result.body.quote.eligibility.canSend, false);
  });
} catch (error) { report.fatalError = error.stack; }
finally {
  for (const timer of timers) clearTimeout(timer);
  await browser?.close();
  for (const server of [proxy, app]) {
    if (!server.listening) continue;
    const closed = new Promise(resolve => server.close(resolve)); server.closeAllConnections(); await closed;
  }
  report.sourceHashesAfter = await hashes();
  report.sourceHashesUnchanged = JSON.stringify(report.sourceHashesBefore) === JSON.stringify(report.sourceHashesAfter);
  report.status = !report.fatalError && report.sourceHashesUnchanged && report.checks.length === 5 && report.checks.every(c => c.status === 'passed') ? 'passed' : 'failed';
  report.finishedAt = new Date().toISOString();
  await writeFile(resolve(output, 'report.json'), JSON.stringify(report, null, 2), { flag: 'wx' });
  console.log(JSON.stringify({ status: report.status, checks: report.checks.map(({ scenario, status }) => ({ scenario, status })), sourceHashesUnchanged: report.sourceHashesUnchanged, appSha256: report.appSha256, report: resolve(output, 'report.json') }));
  if (report.status !== 'passed') process.exitCode = 1;
}
