import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rename, stat } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { startRecorder, control } from '../scripts/record-ghcp-live.mjs';

async function begin(options = {}) {
  const takeDir = await mkdtemp(resolve(tmpdir(), 'recorder-failure-fixture-'));
  const recorder = await startRecorder({ fixture: true, takeDir, ...options });
  const deadline = Date.now() + 5000;
  while (Date.now() < deadline) {
    const screen = await recorder.inspect();
    if (screen.text.includes('FIXTURE input')) return { recorder, screen, takeDir };
    await delay(25);
  }
  await recorder.finish();
  throw Error('Fixture prompt did not appear');
}

async function finalized(recorder) {
  const deadline = Date.now() + 20_000;
  while (!recorder.status().finalized && Date.now() < deadline) await delay(25);
  assert.equal(recorder.status().finalized, true, 'Failure must finalize without operator finish');
  const result = await recorder.finish();
  assert.equal(result.status, 'incomplete');
  assert.equal(result.bridgeClosed, true);
  await assert.rejects(() => recorder.respond({}), /closed/);
  return result;
}

test('recorder command exits nonzero for an incomplete fixture recording', { timeout: 30_000 }, async () => {
  const child = spawn(process.execPath, [resolve(import.meta.dirname, '../scripts/record-ghcp-live.mjs'), '--fixture'], { stdio: ['ignore', 'pipe', 'pipe'] });
  let output = '', errors = '', controlFile;
  child.stdout.on('data', data => { output += data; });
  child.stderr.on('data', data => { errors += data; });
  const exited = once(child, 'exit');
  try {
    const deadline = Date.now() + 15_000;
    while (!controlFile && Date.now() < deadline) {
      const lines = output.split('\n').slice(0, -1).map(line => JSON.parse(line));
      controlFile = lines.find(event => event.type === 'ready')?.controlFile;
      if (child.exitCode !== null) throw Error(`Fixture command exited early: ${errors}`);
      if (!controlFile) await delay(25);
    }
    assert.ok(controlFile, errors || 'Fixture command did not become ready');
    await control(controlFile, 'finish');
    const [code] = await exited;
    assert.equal(code, 1);
    assert.match(output, /"status":"incomplete"/);
  } finally {
    if (child.exitCode === null && child.signalCode === null) {
      if (controlFile) await control(controlFile, 'finish').catch(() => {});
      child.kill('SIGTERM');
      await exited;
    }
  }
});

const reply = screen => ({ snapshotId: screen.snapshotId, key: 'a', kind: 'user-decision',
  decision: 'Fixture-only acceptance; not a real Copilot approval' });

async function exitNormally(recorder, screen) {
  await recorder.respond({ ...reply(screen), key: 'q' });
  const deadline = Date.now() + 5000;
  while (!recorder.status().childExit && Date.now() < deadline) await delay(25);
  assert.equal(recorder.status().childExit?.exitCode, 0);
}

async function assertRetainedSource(result) {
  assert.ok(result.localVideo, 'Original encoder path must be recorded');
  assert.notEqual(result.localVideo, result.rawVideo);
  assert.equal(result.localVideo.startsWith(resolve(result.workspace, 'video') + '/'), true);
  assert.ok((await stat(result.localVideo)).size > 1000, 'Encoder source survives browser shutdown');
}

test('video save beyond the old eight-second deadline finishes before browser teardown', { timeout: 30_000 }, async () => {
  const { recorder, screen, takeDir } = await begin({ fixtureHooks: { videoSaveDelayMs: 9000 } });
  try {
    await exitNormally(recorder, screen);
    await delay(8200);
    assert.equal(recorder.status().finalized, false, 'No premature success manifest');
    assert.equal(recorder.fixturePage.context().browser().isConnected(), true, 'Do not tear down during save');
    await assert.rejects(stat(resolve(takeDir, 'capture.json')), { code: 'ENOENT' });
    await assert.rejects(stat(resolve(takeDir, 'video/capture.webm')), { code: 'ENOENT' });
    const result = await recorder.finish();
    assert.equal(result.status, 'recorded');
    assert.equal(result.videoCopyVerified, true);
    await assertRetainedSource(result);
    const source = await readFile(result.localVideo);
    assert.deepEqual(await readFile(result.rawVideo), source);
    assert.equal(result.videoBytes, source.length);
    assert.equal(result.originalVideoSha256, createHash('sha256').update(source).digest('hex'));
    assert.equal(recorder.fixturePage.context().browser().isConnected(), false);
    console.log(`Delayed-save successful fixture manifest: ${resolve(takeDir, 'capture.json')}`);
  } finally { await recorder.finish(); }
});

for (const [label, fixtureHooks, expected] of [
  ['rejection', { videoSaveError: true }, /FIXTURE video save rejected/],
  ['timeout', { videoSaveDelayMs: 1000, videoSaveTimeoutMs: 100 }, /Timeout: verified video save/],
  ['corrupt copy', { videoSaveCorrupt: true }, /Saved video hash mismatch/],
]) {
  test(`video save ${label} preserves source and cannot publish a successful capture`, { timeout: 30_000 }, async () => {
    const { recorder, screen, takeDir } = await begin({ fixtureHooks });
    try {
      await exitNormally(recorder, screen);
      const result = await finalized(recorder);
      assert.match(result.failure, expected);
      assert.equal(result.videoCopyVerified, false);
      assert.equal(result.originalVideoSha256, null);
      await assertRetainedSource(result);
      assert.equal(recorder.fixturePage.context().browser().isConnected(), false);
      await assert.rejects(stat(result.rawVideo), { code: 'ENOENT' });
      const partial = `${result.rawVideo}.partial`;
      const bytes = (await stat(partial)).size;
      await delay(200);
      assert.equal((await stat(partial)).size, bytes, 'Timed-out streams have settled');
      assert.equal(JSON.parse(await readFile(resolve(takeDir, 'capture.json'), 'utf8')).status, 'incomplete');
    } finally { await recorder.finish(); }
  });
}

for (const timedOut of [false, true]) {
  test(`encoder finalization ${timedOut ? 'late rejection after timeout' : 'rejection'} closes resources without false success`, { timeout: 30_000 }, async () => {
    const { recorder, screen } = await begin({ fixtureHooks: { videoFinalizeTimeoutMs: timedOut ? 100 : 5000 } });
    const context = recorder.fixturePage.context();
    try {
      // A short, buffered recording may still be empty when the encoder is
      // interrupted. Inject only after real encoded bytes have reached disk;
      // cleanup must preserve those bytes, not manufacture a finalized video.
      const source = await recorder.fixturePage.video().path();
      // The mostly static terminal can stay entirely in ffmpeg's output buffer.
      // Changing fixture-only pixels force actual encoded clusters onto disk;
      // do not seed or modify the source file that preservation is testing.
      await recorder.fixturePage.evaluate(() => {
        const canvas = document.createElement('canvas');
        canvas.width = 640; canvas.height = 360;
        canvas.setAttribute('aria-label', 'FIXTURE encoder stress frames');
        canvas.style.cssText = 'position:fixed;right:0;top:0;z-index:9999;pointer-events:none';
        document.body.append(canvas);
        const drawing = canvas.getContext('2d');
        const frame = drawing.createImageData(canvas.width, canvas.height);
        let seed = 1;
        setInterval(() => {
          for (let i = 0; i < frame.data.length; i += 4) {
            seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
            frame.data[i] = seed & 255;
            frame.data[i + 1] = (seed >>> 8) & 255;
            frame.data[i + 2] = (seed >>> 16) & 255;
            frame.data[i + 3] = 255;
          }
          drawing.putImageData(frame, 0, 0);
        }, 100);
      });
      const deadline = Date.now() + 15_000;
      while ((await stat(source)).size <= 1000 && Date.now() < deadline) await delay(100);
      const before = await stat(source);
      assert.ok(before.size > 1000, 'Encoder must emit video data before fault injection');
      const existing = await readFile(source);
      context.close = async () => {
        if (timedOut) await delay(500);
        throw Error('FIXTURE encoder finalization rejected');
      };
      await exitNormally(recorder, screen);
      const result = await finalized(recorder);
      assert.match(result.failure, timedOut ? /Timeout: video finalization/ : /FIXTURE encoder finalization rejected/);
      assert.equal(result.videoCopyVerified, false);
      assert.equal(result.originalVideoSha256, null);
      assert.equal(context.browser().isConnected(), false);
      await assertRetainedSource(result);
      assert.equal(result.localVideo, source);
      const after = await stat(source);
      assert.equal(after.ino, before.ino, 'Cleanup must retain the original encoder file');
      const retained = await readFile(source);
      assert.ok(retained.length >= existing.length, 'Cleanup must not truncate already recorded bytes');
      assert.deepEqual(retained.subarray(0, existing.length), existing, 'Existing encoded bytes must survive cleanup unchanged');
      await assert.rejects(stat(result.rawVideo), { code: 'ENOENT' });
      // Under --unhandled-rejections=strict the late rejection must not escape.
      await delay(600);
    } finally { await recorder.finish(); }
  });
}

test('video fault hooks remain unavailable to actual recorder mode', async () => {
  await assert.rejects(() => startRecorder({ fixtureHooks: { videoSaveError: true } }), /Fixture hooks require fixture mode/);
  await assert.rejects(() => startRecorder({ fixture: true, fixtureHooks: { unknown: true } }), /Unknown fixture hook/);
});

test('renderer failure automatically finalizes and closes input', { timeout: 30_000 }, async () => {
  const { recorder } = await begin();
  try {
    await recorder.fixturePage.evaluate(() => window.rendererFailure('fixture renderer failure'));
    const result = await finalized(recorder);
    assert.match(result.failure, /fixture renderer failure/);
    assert.ok(result.originalVideoSha256);
  } finally { await recorder.finish(); }
});

test('lost input acknowledgement prevents a second attempt and finalizes', { timeout: 30_000 }, async () => {
  const { recorder, screen, takeDir } = await begin({ fixtureHooks: { dropInputAck: true, inputAckTimeoutMs: 150 } });
  try {
    await assert.rejects(() => recorder.respond(reply(screen)), /acknowledgement/);
    const result = await finalized(recorder);
    assert.match(result.failure, /Uncertain input delivery/);
    const events = (await readFile(resolve(takeDir, 'pty-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(event => event.type === 'input-attempt').length, 1);
  } finally { await recorder.finish(); }
});

test('evidence failure blocks the pending input and still writes an incomplete manifest', { timeout: 30_000 }, async () => {
  const { recorder, screen, takeDir } = await begin();
  try {
    const file = resolve(takeDir, 'capture-events.jsonl');
    await rename(file, resolve(takeDir, 'capture-events-before-fault.jsonl'));
    await mkdir(file);
    await assert.rejects(() => recorder.respond(reply(screen)), /EISDIR|Evidence/);
    const result = await finalized(recorder);
    assert.match(result.evidenceError, /Evidence write failed/);
    const events = (await readFile(resolve(takeDir, 'pty-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(event => event.type === 'input-attempt').length, 0);
    assert.equal(JSON.parse(await readFile(resolve(takeDir, 'capture.json'), 'utf8')).status, 'incomplete');
  } finally { await recorder.finish(); }
});

test('DOM keyboard and paste events cannot send fixture acceptance', { timeout: 30_000 }, async () => {
  const { recorder, screen, takeDir } = await begin();
  try {
    await recorder.fixturePage.locator('textarea').focus();
    await recorder.fixturePage.keyboard.press('a');
    await recorder.fixturePage.keyboard.press('Enter');
    await recorder.fixturePage.evaluate(() => {
      const data = new DataTransfer(); data.setData('text/plain', 'a\r');
      document.querySelector('textarea').dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true, cancelable: true }));
    });
    const after = await recorder.inspect();
    assert.equal(after.text, screen.text);
    const events = (await readFile(resolve(takeDir, 'pty-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    assert.equal(events.filter(event => event.type === 'input-attempt' || event.type === 'protocol-attempt').length, 0);
  } finally { await recorder.finish(); }
});
