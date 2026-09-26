import test from 'node:test';
import assert from 'node:assert/strict';
import { mkdtemp, readFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { startRecorder } from '../scripts/record-ghcp-live.mjs';

async function screenWith(recorder, text) {
  const deadline = Date.now() + 10_000;
  while (Date.now() < deadline) {
    const screen = await recorder.inspect();
    if (screen.text.includes(text)) return screen;
    await delay(30);
  }
  throw Error(`Missing fixture output: ${text}`);
}
const input = (screen, key) => ({ snapshotId: screen.snapshotId, key, kind: 'navigation',
  decision: `Fixture renderer test: ${key}; not a real Copilot or human approval` });

test('startup device replies and synchronized frames remain separate from operator input', { timeout: 60_000 }, async () => {
  const takeDir = await mkdtemp(resolve(tmpdir(), 'terminal-rendering-fixture-'));
  const recorder = await startRecorder({ fixture: true, takeDir });
  try {
    const initial = await screenWith(recorder, 'FIXTURE input');
    await recorder.respond(input(initial, 't'));
    const startup = await screenWith(recorder, 'FIXTURE STARTUP PROTOCOL OK');
    assert.equal(startup.error, null);
    assert.equal(startup.bracketedPasteMode, true);
    await recorder.respond(input(startup, 'p'));
    const protocol = await screenWith(recorder, 'FIXTURE PROTOCOL OK');
    assert.equal(protocol.error, null);
    assert.equal(protocol.renderedSeq, protocol.seq);
    assert.equal(protocol.syncPending, false);

    const unusedBeforeUpdate = await recorder.inspect();
    await recorder.respond(input(protocol, 's'));
    await recorder.fixturePage.waitForFunction(() => window.captureState().syncPending, { }, { timeout: 2000 });
    // The active buffer already contains the next screen, but it is not eligible
    // for an approval snapshot until the synchronized update has been painted.
    const pending = await recorder.fixturePage.evaluate(() => window.captureState());
    assert.equal(pending.syncPending, true);
    assert.notEqual(pending.renderedSeq, pending.seq);
    const painted = await recorder.inspect();
    assert.match(painted.text, /FIXTURE SYNCHRONIZED NEW SCREEN/);
    assert.match(painted.text, /FIXTURE input/);
    assert.equal(painted.syncPending, false);
    assert.equal(painted.seq, painted.renderedSeq);
    assert.equal(painted.seq, recorder.status().ack);
    await assert.rejects(() => recorder.respond(input(unusedBeforeUpdate, 'a')), /Stale/);
    await assert.rejects(() => recorder.respond(input(protocol, 'a')), /consumed/);
    await recorder.respond(input(painted, 'q'));

    const deadline = Date.now() + 5000;
    while (!recorder.status().childExit && Date.now() < deadline) await delay(25);
    const result = await recorder.finish();
    assert.equal(result.status, 'recorded');
    assert.equal(result.rendererEnded, true);
    assert.equal(result.seq, result.renderedSeq);
    const events = (await readFile(resolve(takeDir, 'pty-events.jsonl'), 'utf8')).trim().split('\n').map(JSON.parse);
    const expectedQueries = ['focus', 'foreground', 'background',
      ...Array.from({ length: 16 }, (_, i) => `palette-${i}`), 'cursor'];
    const replies = events.filter(e => e.type === 'protocol-attempt');
    assert.deepEqual(replies.map(e => e.query), expectedQueries);
    assert.deepEqual(events.filter(e => e.type === 'protocol').map(e => [e.status, e.query]),
      expectedQueries.map(query => ['sent', query]));
    const replyBytes = replies.map(e => Buffer.from(e.data, 'base64').toString('ascii'));
    assert.deepEqual(replyBytes.slice(0, 3), ['\x1b[O',
      '\x1b]10;rgb:e3e3/eaea/f2f2\x1b\\', '\x1b]11;rgb:1111/1616/1d1d\x1b\\']);
    for (let i = 0; i < 16; i++) {
      assert.ok(replyBytes[i + 3].startsWith(`\x1b]4;${i};rgb:`));
      assert.match(replyBytes[i + 3], /^\x1b\]4;\d{1,2};rgb:[0-9a-f]{4}\/[0-9a-f]{4}\/[0-9a-f]{4}\x1b\\$/);
    }
    assert.match(replyBytes.at(-1), /^\x1b\[[1-9]\d*;[1-9]\d*R$/);
    assert.deepEqual(events.filter(e => e.type === 'input-attempt')
      .map(e => Buffer.from(e.data, 'base64').toString('ascii')), ['t', 'p', 's', 'q']);
    assert.ok(!(await readFile(resolve(takeDir, 'terminal.ansi'), 'utf8')).includes('ACCEPTED ONCE'));
  } finally { await recorder.finish(); }
});
