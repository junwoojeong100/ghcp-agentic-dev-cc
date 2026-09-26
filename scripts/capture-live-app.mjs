#!/usr/bin/env node
// Real browser production capture, outside the filmed Copilot session.
// Only reads the two apps; outputs a fresh, unique evidence folder every run.
import { chromium } from 'playwright';
import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, realpath, writeFile, stat, rename } from 'node:fs/promises';
import { dirname, isAbsolute, join, resolve, relative } from 'node:path';
import { parseArgs } from 'node:util';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { execFileSync } from 'node:child_process';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const { values } = parseArgs({ options: {
  workspace: { type: 'string', default: '/Users/junwoojeong/ghcp-headless-demo-wNmrAn' },
  'output-root': { type: 'string', default: 'evidence/ghcp-live/producer-verification/app' },
  contract: { type: 'string', default: 'sendEligibility' },
} });
if (!['sendEligibility', 'eligibility'].includes(values.contract)) throw Error('Unknown API contract');
const canSend = quote => values.contract === 'eligibility' ? quote?.eligibility?.canSend : quote?.sendEligibility?.allowed;
const sources = {
  baseline: join(root, 'evidence/ghcp-live/baseline-snapshot'),
  final: await realpath(resolve(root, values.workspace)),
};
const outputRoot = resolve(root, values['output-root']);
const evidenceRoot = await realpath(join(root, 'evidence/ghcp-live'));
const within = (parent, child) => {
  const path = relative(parent, child);
  return !isAbsolute(path) && path !== '..' && !path.startsWith('../');
};
if (!within(evidenceRoot, outputRoot)) throw Error('Output must stay within live evidence');
let ancestor = outputRoot;
for (;;) {
  try {
    if (!within(evidenceRoot, await realpath(ancestor))) throw Error('Output symlink escapes live evidence');
    break;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
    ancestor = dirname(ancestor);
  }
}
await mkdir(outputRoot, { recursive: true });
if (!within(evidenceRoot, await realpath(outputRoot))) throw Error('Output symlink escapes live evidence');
const out = join(outputRoot,
  `${new Date().toISOString().replaceAll(':', '-').replaceAll('.', '-')}-${randomUUID().slice(0, 8)}`);
await mkdir(out, { recursive: true });
const manifestPath = join(out, 'manifest.json');
const sha = (value) => createHash('sha256').update(value).digest('hex');
const codePaths = ['package.json', 'src/server.mjs', 'src/quote.mjs', 'public/app.js', 'public/index.html', 'public/style.css'];
async function hashCode() {
  const result = {};
  for (const [version, folder] of Object.entries(sources)) {
    const files = [];
    for (const path of codePaths) files.push({ path, sha256: sha(await readFile(join(folder, path))) });
    result[version] = { root: folder, files, aggregateSha256: sha(files.map(f => `${f.path}\0${f.sha256}`).join('\n')) };
  }
  return result;
}
const manifest = {
  schemaVersion: 1,
  executor: 'Claude production session outside filmed Copilot',
  executionStatus: 'running', startedAt: new Date().toISOString(),
  description: 'Real app browser interactions using isolated ephemeral loopback servers; app fixtures are synthetic, transmissions are simulations. No Copilot/recorder/shared MCP browser controlled.',
  outputDirectory: out,
  command: ['node', fileURLToPath(import.meta.url), ...process.argv.slice(2)],
  captureScriptSha256: sha(await readFile(fileURLToPath(import.meta.url))),
  codeBefore: await hashCode(), codeAfter: null,
  serverOrigins: {}, scenes: [], checks: [], errors: [], files: [],
  productionNotes: [
    'Unmodified app DOM and CSS; native 1920x1080 desktop viewport and video.',
    'Each WebM is a separate real page recording. Source in/out refer to its complete probed media timeline, not fabricated replay.',
    'Network route holds, if used, delay genuine server responses; no fabricated payloads.',
    'Existing automated test suites were not rerun.'
  ],
};
const flush = () => writeFile(manifestPath, JSON.stringify(manifest, null, 2) + '\n');
await flush();
console.log(JSON.stringify({ event: 'capture-started', outputDirectory: out, manifestPath }));
const servers = [];
let browser;
const contexts = new Set();
const pause = (page, ms = 1500) => page.waitForTimeout(ms);
function check(name, condition, actual) {
  manifest.checks.push({ name, passed: Boolean(condition), actual });
  if (!condition) console.error(`CHECK FAILED: ${name}`);
}
async function state(page) {
  return page.evaluate(() => ({
    quoteId: document.querySelector('#quote-id').value,
    discount: document.querySelector('#discount').value,
    margin: document.querySelector('#margin-value').textContent,
    net: document.querySelector('#net-amount').textContent,
    profit: document.querySelector('#profit-amount').textContent,
    sendDisabled: document.querySelector('#send-quote').disabled,
    busy: document.querySelector('#quote-summary').getAttribute('aria-busy'),
    label: document.querySelector('#preview-label').textContent,
    feedback: document.querySelector('#feedback').textContent,
    status: document.querySelector('#quote-status').textContent,
    invalid: document.querySelector('#discount').getAttribute('aria-invalid'),
    viewportWidth: innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }));
}
async function settled(page) {
  await page.waitForFunction(() => !document.querySelector('#quote-id')?.disabled &&
    document.querySelector('#quote-summary')?.getAttribute('aria-busy') === 'false');
}
async function setInput(page, quoteId, discount) {
  if (quoteId && await page.locator('#quote-id').inputValue() !== quoteId) {
    await page.locator('#quote-id').selectOption(quoteId);
    await settled(page);
  }
  if (discount !== undefined && await page.locator('#discount').inputValue() !== discount) {
    await page.locator('#discount').fill(discount);
    await settled(page);
  }
}
async function asset(path, kind, extra = {}) {
  const buffer = await readFile(path);
  const item = { filename: relative(out, path), kind, bytes: buffer.length, sha256: sha(buffer), ...extra };
  manifest.files.push(item);
  return item.filename;
}
async function shot(page, name, fullPage = false) {
  const path = join(out, `${name}.png`);
  await page.screenshot({ path, fullPage, animations: 'disabled' });
  await asset(path, 'screenshot', { viewport: page.viewportSize(), fullPage });
  return relative(out, path);
}
function probeVideo(path) {
  const parsed = JSON.parse(execFileSync('/opt/homebrew/bin/ffprobe', [
    '-v', 'error', '-show_entries', 'format=duration:stream=codec_name,width,height,r_frame_rate', '-of', 'json', path,
  ], { encoding: 'utf8' }));
  return { durationSeconds: Number(parsed.format.duration), streams: parsed.streams };
}
async function scene(id, version, action, viewport = { width: 1920, height: 1080 }) {
  const entry = { id, version, startedAt: new Date().toISOString(), status: 'running', viewport, screenshots: [], states: [], apiResponses: [] };
  manifest.scenes.push(entry);
  const context = await browser.newContext({
    viewport, deviceScaleFactor: 1, locale: 'ko-KR', timezoneId: 'Asia/Seoul',
    colorScheme: 'light', reducedMotion: 'reduce',
    recordVideo: { dir: join(out, 'raw-video'), size: viewport },
  });
  contexts.add(context);
  const page = await context.newPage();
  page.setDefaultTimeout(8000);
  const video = page.video();
  const pending = [];
  page.on('response', response => {
    if (!/\/api\/(preview|send)$/.test(response.url())) return;
    pending.push((async () => {
      try { entry.apiResponses.push({ url: response.url(), status: response.status(), body: await response.json() }); }
      catch { /* Aborted obsolete previews are expected during rapid-input checks. */ }
    })());
  });
  page.on('pageerror', error => manifest.errors.push({ scene: id, kind: 'pageerror', message: error.message }));
  try {
    await page.goto(manifest.serverOrigins[version], { waitUntil: 'networkidle' });
    await settled(page);
    await action(page, entry);
    entry.status = 'completed';
  } catch (error) {
    entry.status = 'failed'; entry.error = error.stack;
    manifest.errors.push({ scene: id, kind: 'capture', message: error.message });
    try { entry.screenshots.push(await shot(page, `${id}-failure`)); } catch { /* Preserve manifest even if browser failed. */ }
  } finally {
    await Promise.allSettled(pending);
    await context.close();
    contexts.delete(context);
    const generated = await video.path();
    const path = join(out, `${id}.webm`);
    await rename(generated, path);
    const media = probeVideo(path);
    entry.video = await asset(path, 'real-browser-video', media);
    entry.sourceInSeconds = 0;
    entry.sourceOutSeconds = media.durationSeconds;
    entry.timingBasis = 'ffprobe complete per-scene recording; includes real navigation/input transitions';
    entry.finishedAt = new Date().toISOString();
    await flush();
    console.log(JSON.stringify({ event: 'scene-complete', id, status: entry.status, screenshots: entry.screenshots, video: entry.video }));
  }
}

try {
  for (const [version, folder] of Object.entries(sources)) {
    const { createAppServer } = await import(pathToFileURL(join(folder, 'src/server.mjs')).href);
    const server = createAppServer(); servers.push(server);
    await new Promise((res, rej) => { server.once('error', rej); server.listen(0, '127.0.0.1', res); });
    manifest.serverOrigins[version] = `http://127.0.0.1:${server.address().port}`;
  }
  // Keep this production process's browser temporary artifacts inside its permitted evidence tree.
  await mkdir(join(out, 'browser-temp'));
  process.env.TMPDIR = join(out, 'browser-temp');
  browser = await chromium.launch({ headless: true });
  manifest.browserVersion = browser.version();
  await flush();

  await scene('01-baseline-q1001-10-send', 'baseline', async (page, entry) => {
    await setInput(page, 'Q-1001', '10');
    const before = await state(page); entry.states.push(before);
    check('baseline Q-1001 10% send enabled', !before.sendDisabled && before.margin === '11.11%', before);
    await pause(page, 1800);
    entry.screenshots.push(await shot(page, '01-baseline-q1001-10-enabled'));
    const responsePromise = page.waitForResponse(r => r.url().endsWith('/api/send'));
    await page.locator('#send-quote').click();
    const response = await responsePromise; const result = await response.json();
    await settled(page);
    const after = await state(page); entry.states.push(after);
    check('baseline Q-1001 10% actually simulated sent', response.status() === 200 && result.sent === true && result.simulation === true && after.feedback.includes('모의 전송 완료'), { status: response.status(), result, state: after });
    await pause(page, 2200);
    entry.screenshots.push(await shot(page, '02-baseline-q1001-10-sent'));
  });

  await scene('02-final-q1001-10-blocked', 'final', async (page, entry) => {
    const current = await state(page); entry.states.push(current);
    check('final Q-1001 10% blocked in UI', current.sendDisabled && current.feedback.includes('15%') && current.margin === '11.11%', current);
    const api = await page.evaluate(async () => {
      const response = await fetch('/api/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quoteId: 'Q-1001', discountBps: 1000 }) });
      return { status: response.status, body: await response.json() };
    });
    check('final Q-1001 10% direct send rejected by real server', api.status === 422 && api.body.error?.code === 'MARGIN_BELOW_MINIMUM' && canSend(api.body.quote) === false, api);
    await pause(page, 2800);
    entry.screenshots.push(await shot(page, '03-final-q1001-10-blocked'));
  });

  await scene('03-final-q1001-0-allowed-sent', 'final', async (page, entry) => {
    await pause(page, 700);
    await setInput(page, 'Q-1001', '0');
    const before = await state(page); entry.states.push(before);
    check('final Q-1001 0% allowed', !before.sendDisabled && before.margin === '20.0%', before);
    await pause(page, 1800);
    entry.screenshots.push(await shot(page, '04-final-q1001-0-allowed'));
    const responsePromise = page.waitForResponse(r => r.url().endsWith('/api/send'));
    await page.locator('#send-quote').click();
    const response = await responsePromise; const result = await response.json();
    await settled(page);
    const after = await state(page); entry.states.push(after);
    check('final Q-1001 0% simulated sent', response.status() === 200 && result.sent === true && result.simulation === true && after.feedback.includes('모의 전송 완료'), { status: response.status(), result, state: after });
    await pause(page, 2400);
    entry.screenshots.push(await shot(page, '05-final-q1001-0-sent'));
  });

  for (const [id, quote, allowed] of [
    ['04-final-q1500-exact15-allowed', 'Q-1500', true],
    ['05-final-q1499-rounded15-blocked', 'Q-1499', false],
    ['06-final-q1501-above15-allowed', 'Q-1501', true],
  ]) {
    await scene(id, 'final', async (page, entry) => {
      await setInput(page, quote, '0');
      const current = await state(page); entry.states.push(current);
      check(`${quote} displays 15.0% and is ${allowed ? 'allowed' : 'blocked'}`, current.margin === '15.0%' && current.sendDisabled === !allowed, current);
      const api = await page.evaluate(async quoteId => {
        const response = await fetch('/api/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ quoteId, discountBps: 0 }) });
        return { status: response.status, body: await response.json() };
      }, quote);
      check(`${quote} real send API boundary`, allowed ? api.status === 200 && api.body.sent === true && api.body.simulation === true : api.status === 422 && api.body.error?.code === 'MARGIN_BELOW_MINIMUM', api);
      await pause(page, 2200);
      entry.screenshots.push(await shot(page, id));
    });
  }

  await scene('07-final-invalid-zero-rapid-delayed', 'final', async (page, entry) => {
    for (const [value, label] of [['', 'empty'], ['-1', 'negative'], ['100.01', 'out-of-range'], ['1.001', 'too-precise']]) {
      await page.locator('#discount').fill(value); await settled(page);
      const current = await state(page); entry.states.push({ case: label, ...current });
      check(`invalid ${label} disabled with feedback`, current.sendDisabled && current.invalid === 'true' && current.feedback.length > 0, current);
    }
    entry.screenshots.push(await shot(page, '09-final-invalid-discount'));
    await page.locator('#discount').fill('100'); await settled(page);
    const zero = await state(page); entry.states.push({ case: 'zero-net', ...zero });
    check('zero-net blocked with explanatory feedback', zero.sendDisabled && zero.feedback.includes('1원 이상'), zero);
    await pause(page, 1000);
    entry.screenshots.push(await shot(page, '10-final-zero-net'));
    await page.locator('#discount').fill('0');
    await page.locator('#discount').fill('10');
    await page.locator('#discount').fill('0');
    await page.locator('#discount').fill('10');
    await settled(page);
    const rapid = await state(page); entry.states.push({ case: 'rapid-input', ...rapid });
    check('rapid input latest 10% wins', rapid.discount === '10' && rapid.sendDisabled && rapid.margin === '11.11%', rapid);

    let release; let captured;
    const gate = new Promise(resolveGate => { release = resolveGate; });
    const seen = new Promise(resolveSeen => { captured = resolveSeen; });
    const routeHandler = async route => {
      const input = route.request().postDataJSON();
      if (input.discountBps !== 0) { await route.continue(); return; }
      const response = await route.fetch();
      captured();
      await gate;
      try { await route.fulfill({ response }); } catch { /* Superseded requests may already be aborted by the app. */ }
    };
    await page.route('**/api/preview', routeHandler);
    try {
      await page.locator('#discount').fill('0');
      await Promise.race([seen, new Promise((_, reject) => setTimeout(() => reject(new Error('Preview route hold not observed')), 5000))]);
      const held = await state(page); entry.states.push({ case: 'delayed-allowed-preview', ...held });
      check('send disabled while real preview response held', held.sendDisabled && held.busy === 'true', held);
      await pause(page, 1000);
      entry.screenshots.push(await shot(page, '11-final-preview-held'));
      await page.locator('#discount').fill('10'); await settled(page);
      release();
      await pause(page, 1200);
      const latest = await state(page); entry.states.push({ case: 'obsolete-preview-released', ...latest });
      check('obsolete allowed preview cannot override latest blocked input', latest.discount === '10' && latest.sendDisabled && latest.margin === '11.11%' && latest.feedback.includes('15%'), latest);
      entry.screenshots.push(await shot(page, '12-final-stale-preview-ignored'));
    } finally { release?.(); await page.unroute('**/api/preview', routeHandler); }
  });

  await scene('08-final-mobile390', 'final', async (page, entry) => {
    const top = await state(page); entry.states.push(top);
    check('390px mobile no document horizontal overflow', top.viewportWidth === 390 && top.documentWidth <= 390, top);
    entry.screenshots.push(await shot(page, '13-final-mobile390-fullpage', true));
    await page.locator('#send-quote').scrollIntoViewIfNeeded();
    await pause(page, 1700);
    entry.screenshots.push(await shot(page, '14-final-mobile390-summary'));
    const current = await state(page);
    check('390px mobile Q-1001 10% still blocked', current.sendDisabled && current.feedback.includes('15%'), current);
  }, { width: 390, height: 844 });
} catch (error) {
  manifest.errors.push({ kind: 'fatal', message: error.message, stack: error.stack });
  console.error(error);
} finally {
  await Promise.allSettled([...contexts].map(context => context.close()));
  if (browser) await browser.close().catch(error => manifest.errors.push({ kind: 'browser-cleanup', message: error.message }));
  await Promise.all(servers.map(server => new Promise(resolveClose => {
    server.close(resolveClose); server.closeAllConnections();
  })));
  manifest.ownServersShutdown = servers.every(server => !server.listening);
  manifest.codeAfter = await hashCode();
  check('baseline source code unchanged by capture', manifest.codeBefore.baseline.aggregateSha256 === manifest.codeAfter.baseline.aggregateSha256, { before: manifest.codeBefore.baseline.aggregateSha256, after: manifest.codeAfter.baseline.aggregateSha256 });
  check('final source code unchanged by capture', manifest.codeBefore.final.aggregateSha256 === manifest.codeAfter.final.aggregateSha256, { before: manifest.codeBefore.final.aggregateSha256, after: manifest.codeAfter.final.aggregateSha256 });
  check('owned ephemeral servers shut down', manifest.ownServersShutdown, manifest.ownServersShutdown);
  manifest.finishedAt = new Date().toISOString();
  manifest.executionStatus = manifest.errors.length || manifest.checks.some(item => !item.passed) ? 'completed-with-failures' : 'passed';
  manifest.summary = { passed: manifest.checks.filter(item => item.passed).length, failed: manifest.checks.filter(item => !item.passed).length, scenes: manifest.scenes.length, screenshots: manifest.files.filter(item => item.kind === 'screenshot').length, videos: manifest.files.filter(item => item.kind === 'real-browser-video').length };
  await flush();
  console.log(JSON.stringify({ event: 'capture-finished', manifestPath, status: manifest.executionStatus, ...manifest.summary }));
  if (manifest.executionStatus !== 'passed') process.exitCode = 1;
}
