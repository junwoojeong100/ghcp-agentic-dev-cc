import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdir, mkdtemp, readFile, rename, stat } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { request } from 'node:http';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { spawnSync } from 'node:child_process';
import { startRecorder, control } from '../scripts/record-ghcp-live.mjs';

function rawRequest(url, options, body = '') {
  return new Promise((resolveResponse, reject) => {
    const req = request(url, options, res => {
      res.resume();
      res.on('end', () => resolveResponse(res.statusCode));
    });
    req.on('error', reject);
    req.setTimeout(5000, () => req.destroy(Error('Test request timed out')));
    req.end(body);
  });
}

async function screenWith(recorder, text) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const snapshot = await recorder.inspect();
    if (snapshot.text.includes(text)) return snapshot;
    await delay(50);
  }
  throw Error(`Fixture screen missing: ${text}`);
}
const reply = (snapshot, key) => ({ snapshotId: snapshot.snapshotId, key, kind: 'user-decision', decision: `FIXTURE ONLY: send ${key}; not real Copilot approval` });
const eventsAt = async (takeDir, name = 'capture-events.jsonl') => (await readFile(resolve(takeDir, name), 'utf8')).trim().split('\n').filter(Boolean).map(JSON.parse);

async function automaticFinal(recorder) {
  const deadline = Date.now() + 15_000;
  while (!recorder.status().finalized && Date.now() < deadline) await delay(25);
  assert.equal(recorder.status().finalized, true, 'Capture must finalize without another operator request');
  assert.equal(recorder.status().bridgeClosed, true, 'Owned bridge must exit');
  assert.equal(recorder.fixturePage.isClosed(), true, 'Encoder page must close');
  assert.equal(recorder.fixturePage.context().browser().isConnected(), false, 'Owned browser must disconnect');
  await assert.rejects(fetch(`${recorder.origin}/status`, { signal: AbortSignal.timeout(1000) }), /fetch failed/);
  return JSON.parse(await readFile(resolve(recorder.takeDir, 'capture.json'), 'utf8'));
}

test('real PTY fixture records Korean, rejects stale/duplicate input and finalizes playable video', { timeout: 60_000 }, async () => {
  const takeDir = await mkdtemp(resolve(tmpdir(), 'headless-fixture-'));
  const recorder = await startRecorder({ fixture: true, takeDir });
  let finished = false;
  try {
    const first = await screenWith(recorder, 'FIXTURE input');
    assert.equal(first.cols, 110); assert.equal(first.rows, 28);
    assert.match(first.text, /[가-힣]/);
    assert.equal(first.bufferType, 'alternate');
    assert.equal(await recorder.fixturePage.evaluate(() => document.querySelector('footer').getBoundingClientRect().bottom <= innerHeight), true, 'Disclosure footer must fit inside the recorded frame');
    assert.equal(await recorder.fixturePage.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    assert.ok((await stat(first.screenshot)).size > 1000);
    const controlMode = (await stat(recorder.controlFile)).mode & 0o777;
    assert.equal(controlMode, 0o600);
    // Merely inspecting and waiting never sends an approval.
    await delay(300);
    assert.equal((await recorder.inspect()).text, first.text);
    const old = await recorder.inspect();
    await recorder.respond(reply(first, 'a'));
    await screenWith(recorder, 'ACCEPTED ONCE');
    await assert.rejects(() => recorder.respond(reply(first, 'a')), /consumed/);
    await assert.rejects(() => recorder.respond(reply(old, 'a')), /Stale/);
    const current = await recorder.inspect();
    await assert.rejects(() => recorder.respond({ ...reply(current, 'a'), text: 'x' }), /exactly one/);
    await assert.rejects(() => recorder.respond({ ...reply(current, 'a'), extra: true }), /Unknown response field/);
    await recorder.respond(reply(current, 'q'));
    const deadline = Date.now() + 5000;
    while (!recorder.status().childExit && Date.now() < deadline) await delay(25);
    const final = await automaticFinal(recorder); finished = true;
    assert.deepEqual(await recorder.finish(), final, 'Finalization is idempotent');
    assert.equal(final.status, 'recorded');
    assert.equal(final.rendererEnded, true);
    assert.equal(final.renderedSeq, final.seq);
    assert.equal(final.childExit.exitCode, 0);
    assert.equal(final.seq, final.ack);
    assert.ok(final.originalVideoSha256);
    assert.ok((await stat(final.rawVideo)).size > 1000);
    const events = (await readFile(resolve(takeDir, 'capture-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(e => e.type === 'input-request').length, 2);
    const secret = JSON.parse(await readFile(recorder.controlFile, 'utf8')).token;
    assert.ok(!JSON.stringify(events).includes(secret));
    const probe = spawnSync('ffprobe', ['-v', 'error', '-show_streams', '-of', 'json', final.rawVideo], { encoding: 'utf8' });
    assert.equal(probe.status, 0, probe.stderr);
    const video = JSON.parse(probe.stdout).streams.find(s => s.codec_type === 'video');
    assert.equal(video.width, 1920); assert.equal(video.height, 1080);
    const decode = spawnSync('ffmpeg', ['-v', 'error', '-xerror', '-i', final.rawVideo, '-f', 'null', '-'], { encoding: 'utf8', timeout: 15_000 });
    assert.equal(decode.status, 0, decode.stderr);
    console.log(`Fixture capture retained for visual QA: ${final.rawVideo}`);
  } finally { if (!finished) await recorder.finish(); }
});

test('loopback read capability cannot control CLI; bad origins and stream reconnect fail closed', { timeout: 60_000 }, async () => {
  const takeDir = await mkdtemp(resolve(tmpdir(), 'headless-auth-fixture-'));
  const recorder = await startRecorder({ fixture: true, takeDir });
  const config = JSON.parse(await readFile(recorder.controlFile, 'utf8'));
  try {
    await screenWith(recorder, 'FIXTURE input');
    assert.equal((await fetch(`${config.origin}/status`)).status, 401);
    assert.equal(await recorder.fixturePage.evaluate(async () => (await fetch('/respond', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })).status), 403);
    const headers = { Authorization: `Bearer ${config.token}`, 'Content-Type': 'application/json' };
    assert.equal((await fetch(`${config.origin}/inspect`, { method: 'POST', headers: { ...headers, Origin: 'https://example.invalid' }, body: '{}' })).status, 403);
    // node:http preserves this deliberately wrong Host; fetch normalizes it.
    assert.equal(await rawRequest(`${config.origin}/inspect`, { method: 'POST', headers: { ...headers, Host: 'example.invalid' } }, '{}'), 403);
    assert.equal((await fetch(`${config.origin}/unknown`, { headers })).status, 404);
    assert.equal((await fetch(`${config.origin}/events`, { headers })).status, 409);
    const snapshot = await control(recorder.controlFile, 'inspect');
    assert.match(snapshot.text, /FIXTURE/);
    await recorder.respond(reply(snapshot, 'x'));
    const deadline = Date.now() + 5000;
    while (!recorder.status().childExit && Date.now() < deadline) await delay(25);
    const final = await recorder.finish();
    assert.equal(final.status, 'incomplete');
    assert.equal(final.childExit.exitCode, 3);
  } finally { await recorder.finish(); }
});

test('stopping a waiting CLI closes queued input immediately and remains incomplete', { timeout: 45_000 }, async () => {
  const recorder = await startRecorder({ fixture: true, takeDir: await mkdtemp(resolve(tmpdir(), 'headless-stop-fixture-')) });
  try {
    const snapshot = await screenWith(recorder, 'FIXTURE input');
    const queued = recorder.respond(reply(snapshot, 'a'));
    const finished = recorder.finish();
    assert.equal(recorder.status().closing, true);
    await assert.rejects(queued, /closed/);
    await assert.rejects(() => recorder.respond({}), /closed/);
    const final = await finished;
    assert.equal(final.status, 'incomplete');
    assert.equal((await eventsAt(recorder.takeDir, 'pty-events.jsonl')).filter(event => event.type === 'input-attempt').length, 0);
  } finally { await recorder.finish(); }
});

test('renderer failure automatically closes PTY, browser encoder and server', { timeout: 30_000 }, async () => {
  const recorder = await startRecorder({ fixture: true, takeDir: await mkdtemp(resolve(tmpdir(), 'headless-renderer-fault-')) });
  try {
    const snapshot = await screenWith(recorder, 'FIXTURE input');
    await recorder.fixturePage.evaluate(() => window.rendererFailure('FIXTURE renderer failed'));
    assert.equal(recorder.status().closing, true);
    await assert.rejects(() => recorder.respond(reply(snapshot, 'a')), /closed/);
    const final = await automaticFinal(recorder);
    assert.equal(final.status, 'incomplete');
    assert.match(final.failure, /FIXTURE renderer failed/);
    assert.equal((await eventsAt(recorder.takeDir, 'pty-events.jsonl')).filter(event => event.type === 'input-attempt').length, 0);
  } finally { await recorder.finish(); }
});

test('failed evidence append sends no input and still writes an incomplete manifest', { timeout: 30_000 }, async () => {
  const recorder = await startRecorder({ fixture: true, takeDir: await mkdtemp(resolve(tmpdir(), 'headless-evidence-fault-')) });
  try {
    const snapshot = await screenWith(recorder, 'FIXTURE input');
    const log = resolve(recorder.takeDir, 'capture-events.jsonl');
    await rename(log, `${log}.retained`);
    await mkdir(log); // Real append failure; the separate manifest remains writable.
    await assert.rejects(() => recorder.respond(reply(snapshot, 'a')), /EISDIR|illegal operation on a directory/i);
    assert.equal(recorder.status().closing, true);
    await assert.rejects(() => recorder.inspect(), /closed/);
    const final = await automaticFinal(recorder);
    assert.equal(final.status, 'incomplete');
    assert.match(final.evidenceError, /Evidence write failed/);
    assert.equal((await eventsAt(recorder.takeDir, 'pty-events.jsonl')).filter(event => ['input-attempt', 'input'].includes(event.type)).length, 0);
    assert.ok(!(await readFile(resolve(recorder.takeDir, 'terminal.ansi'), 'utf8')).includes('ACCEPTED ONCE'));
  } finally { await recorder.finish(); }
});

test('uncertain input acknowledgement closes the whole take, not only its snapshot', { timeout: 30_000 }, async () => {
  const recorder = await startRecorder({ fixture: true, takeDir: await mkdtemp(resolve(tmpdir(), 'headless-input-fault-')),
    fixtureHooks: { dropInputAck: true, inputAckTimeoutMs: 200 } });
  try {
    const first = await screenWith(recorder, 'FIXTURE input');
    const other = await recorder.inspect();
    // Ignored by the fixture: the screen and sequence stay identical, so an
    // alternate snapshot would permit a retry if only the first ID were closed.
    await assert.rejects(() => recorder.respond(reply(first, 'z')), /input acknowledgement missing/i);
    assert.match(recorder.status().failure, /Uncertain input delivery/);
    await assert.rejects(() => recorder.respond(reply(other, 'z')), /closed/);
    await assert.rejects(() => recorder.inspect(), /closed/);
    const final = await automaticFinal(recorder);
    assert.equal(final.status, 'incomplete');
    assert.equal((await eventsAt(recorder.takeDir)).filter(event => event.type === 'input-request').length, 1);
    assert.equal((await eventsAt(recorder.takeDir, 'pty-events.jsonl')).filter(event => event.type === 'input' && event.status === 'sent').length, 1);
  } finally { await recorder.finish(); }
});

test('bridge startup error finishes without starting a browser or CLI', { timeout: 20_000 }, async () => {
  const takeDir = await mkdtemp(resolve(tmpdir(), 'headless-startup-fault-'));
  await mkdir(resolve(takeDir, 'terminal.ansi')); // Bridge evidence must be exclusive.
  await assert.rejects(() => startRecorder({ fixture: true, takeDir }), /PTY controller:.*File exists/);
  const final = JSON.parse(await readFile(resolve(takeDir, 'capture.json'), 'utf8'));
  assert.equal(final.status, 'incomplete');
  assert.equal(final.started, false);
  assert.equal(final.bridgeClosed, true);
  assert.equal(final.rawVideo, undefined);
  assert.equal(final.originalVideoSha256, null);
  await assert.rejects(stat(resolve(takeDir, 'video')), { code: 'ENOENT' });
});

test('multiline text requires the actual terminal bracketed paste mode', { timeout: 30_000 }, async () => {
  const recorder = await startRecorder({ fixture: true, takeDir: await mkdtemp(resolve(tmpdir(), 'headless-paste-fixture-')) });
  try {
    const snapshot = await screenWith(recorder, 'FIXTURE input');
    assert.equal(snapshot.bracketedPasteMode, false);
    for (const text of ['one\ntwo', 'one\rtwo', 'one\r\ntwo']) {
      await assert.rejects(() => recorder.respond({ ...reply(snapshot, 'z'), key: undefined, text }), /requires active bracketed paste mode/);
    }
    assert.equal((await eventsAt(recorder.takeDir)).filter(event => event.type === 'input-request').length, 0);
    await recorder.respond(reply(snapshot, 'q'));
    assert.equal((await automaticFinal(recorder)).status, 'recorded');
  } finally { await recorder.finish(); }
});
