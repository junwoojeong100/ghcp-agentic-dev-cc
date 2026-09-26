import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { createHash, randomBytes, randomUUID, timingSafeEqual } from 'node:crypto';
import { createServer } from 'node:http';
import { createInterface } from 'node:readline';
import { createReadStream, createWriteStream } from 'node:fs';
import { appendFile, chmod, copyFile, link, lstat, mkdir, mkdtemp, readFile, statfs, unlink, writeFile } from 'node:fs/promises';
import { pipeline } from 'node:stream/promises';
import { homedir } from 'node:os';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright';

const root = resolve(import.meta.dirname, '..');
const hash = value => createHash('sha256').update(value).digest('hex');
const json = value => JSON.stringify(value, null, 2) + '\n';
const keys = { ENTER: '\r', ESCAPE: '\x1b', UP: '\x1b[A', DOWN: '\x1b[B', TAB: '\t', SHIFT_TAB: '\x1b[Z', CTRL_C: '\x03' };
const privateWrite = (path, data) => writeFile(path, data, { mode: 0o600, flag: 'wx' });

async function bounded(promise, label, ms = 10_000) {
  let timer;
  try {
    return await Promise.race([promise, new Promise((_, reject) => {
      timer = setTimeout(() => reject(Error(`Timeout: ${label}`)), Math.max(1, ms));
    })]);
  } finally { clearTimeout(timer); }
}

// Conditions must be synchronous. Bound asynchronous work separately instead of
// letting an awaited polling callback escape the deadline.
async function waitUntil(condition, label, ms = 10_000) {
  const deadline = Date.now() + ms;
  while (Date.now() < deadline) {
    if (condition()) return;
    await delay(25);
  }
  throw Error(`Timeout: ${label}`);
}

// Copy only a closed, local encoder file. The timeout aborts the streams and we
// await their settlement: a timeout must never leave a remote save running while
// browser teardown deletes its source. Partial copies and the source stay on disk.
async function preserveVideo(source, target, hooks = {}) {
  const before = await bounded(lstat(source), 'local video metadata', 5000);
  assert.ok(before.isFile() && before.size > 0, 'Local video must be a nonempty regular file');
  // Allow two minutes plus 1s/MiB for copying and verification (up to 30 min).
  const ms = hooks.videoSaveTimeoutMs ?? Math.min(30 * 60_000, 120_000 + Math.ceil(before.size / (1024 * 1024)) * 1000);
  const controller = new AbortController();
  const { signal } = controller;
  const timeout = setTimeout(() => controller.abort(Error('Timeout: verified video save')), ms);
  const partial = `${target}.partial`;
  const sourceHash = createHash('sha256');
  let copiedBytes = 0;
  try {
    await pipeline(createReadStream(source), async function* (chunks) {
      let first = true;
      for await (const chunk of chunks) {
        if (first && hooks.videoSaveDelayMs) await delay(hooks.videoSaveDelayMs, undefined, { signal });
        if (first && hooks.videoSaveError) throw Error('FIXTURE video save rejected');
        sourceHash.update(chunk);
        copiedBytes += chunk.length;
        if (first && hooks.videoSaveCorrupt) {
          const corrupt = Buffer.from(chunk); corrupt[0] ^= 1; yield corrupt;
        } else yield chunk;
        first = false;
      }
    }, createWriteStream(partial, { flags: 'wx', mode: 0o600, flush: true }), { signal });
    const after = await bounded(lstat(source), 'local video metadata', 5000);
    assert.ok(after.isFile() && after.size === before.size && after.mtimeMs === before.mtimeMs && copiedBytes === before.size,
      'Local video changed while saving');
    const sha256 = sourceHash.digest('hex');
    const copyHash = createHash('sha256');
    let verifiedBytes = 0;
    // Hash incrementally; multi-hour recordings must not be loaded into memory.
    for await (const chunk of createReadStream(partial, { signal })) {
      copyHash.update(chunk); verifiedBytes += chunk.length;
    }
    assert.equal(verifiedBytes, before.size, 'Saved video is truncated');
    assert.equal(copyHash.digest('hex'), sha256, 'Saved video hash mismatch');
    signal.throwIfAborted();
    // Exclusive, atomic publication: an existing output is never overwritten and
    // capture.webm does not exist until its complete bytes have been verified.
    await bounded(link(partial, target), 'verified video publication', 5000);
    await bounded(unlink(partial), 'video temporary link cleanup', 5000);
    return { sha256, bytes: verifiedBytes };
  } catch (error) {
    if (signal.aborted) throw signal.reason;
    throw error;
  } finally { clearTimeout(timeout); }
}

async function prepareWorkspace(run, take) {
  const baseline = JSON.parse(await readFile(resolve(root, run.evidenceDir, 'baseline-hashes.json'), 'utf8'));
  const roles = JSON.parse(await readFile(resolve(root, run.evidenceDir, 'agent-definitions.json'), 'utf8')).sha256;
  const requirement = JSON.parse(await readFile(resolve(root, run.evidenceDir, 'requirements.json'), 'utf8'));
  const files = Object.entries(baseline).map(([path, sha]) => [path, resolve(root, run.baselineDir, path), sha]);
  files.push(['change-request.md', resolve(root, run.productionDir, 'change-request.md'), requirement.sha256]);
  for (const [path, sha] of Object.entries(roles)) files.push([`.github/agents/${path}`, resolve(root, run.productionDir, 'agents', path), sha]);
  for (const [path, source, sha] of files) {
    assert.ok(!path.startsWith('/') && !path.split('/').includes('..'), 'Unsafe baseline path');
    assert.ok(!(await lstat(source)).isSymbolicLink(), 'Source symlink not allowed');
    assert.equal(hash(await readFile(source)), sha, `Prepared source changed: ${path}`);
  }
  const workspace = await mkdtemp(resolve(homedir(), 'ghcp-headless-demo-'));
  await chmod(workspace, 0o700);
  for (const [path, source] of files) {
    const target = resolve(workspace, path);
    await mkdir(dirname(target), { recursive: true });
    await copyFile(source, target, 1);
  }
  await privateWrite(resolve(take, 'workspace-baseline.json'), json({ workspace, codeHashes: baseline, roles, requirement }));
  return workspace;
}

export async function startRecorder({ runDir = resolve(root, 'production/ghcp-live'), fixture = false, takeDir, onEvent = () => {}, fixtureHooks } = {}) {
  // Fault injection is available only to in-process fixture tests, never HTTP/CLI
  // or the real Copilot launcher. It cannot override a command or workspace.
  if (fixtureHooks !== undefined) {
    assert.ok(fixture && fixtureHooks && typeof fixtureHooks === 'object', 'Fixture hooks require fixture mode');
    assert.ok(Object.keys(fixtureHooks).every(key => ['dropInputAck', 'inputAckTimeoutMs', 'videoSaveDelayMs', 'videoSaveTimeoutMs', 'videoSaveError', 'videoSaveCorrupt', 'videoFinalizeTimeoutMs'].includes(key)), 'Unknown fixture hook');
    for (const key of ['dropInputAck', 'videoSaveError', 'videoSaveCorrupt']) {
      assert.ok(fixtureHooks[key] === undefined || typeof fixtureHooks[key] === 'boolean');
    }
    for (const [key, max] of Object.entries({ inputAckTimeoutMs: 5000, videoSaveDelayMs: 15_000, videoSaveTimeoutMs: 120_000, videoFinalizeTimeoutMs: 120_000 })) {
      assert.ok(fixtureHooks[key] === undefined || (Number.isInteger(fixtureHooks[key]) && fixtureHooks[key] >= 50 && fixtureHooks[key] <= max));
    }
  }
  const dropInputAck = fixtureHooks?.dropInputAck === true;
  const inputAckTimeoutMs = fixtureHooks?.inputAckTimeoutMs ?? 5000;
  for (const key of ['COPILOT_ALLOW_ALL', 'COPILOT_ASSISTED_APPROVAL']) {
    if (!['', '0', 'false'].includes(process.env[key] ?? '')) throw Error(`${key} is active; review permissions before filming`);
  }
  const run = JSON.parse(await readFile(resolve(runDir, 'run.json'), 'utf8'));
  if (!takeDir) {
    const parent = resolve(root, run.evidenceDir, 'raw');
    await mkdir(parent, { recursive: true, mode: 0o700 });
    takeDir = await mkdtemp(resolve(parent, fixture ? 'fixture.' : 'headless.'));
  } else await mkdir(takeDir, { recursive: true, mode: 0o700 });
  await chmod(takeDir, 0o700);
  const takeId = randomUUID();
  const workspace = fixture ? takeDir : await prepareWorkspace(run, takeDir);
  const startedAt = new Date().toISOString();
  const readToken = randomBytes(32).toString('hex');
  const controlToken = randomBytes(32).toString('hex');
  const controlFile = resolve(takeDir, 'control.json');
  const eventsFile = resolve(takeDir, 'capture-events.jsonl');
  await privateWrite(eventsFile, '');
  await mkdir(resolve(takeDir, 'snapshots'), { mode: 0o700 });
  let browser, context, page, video, bridge, stream, origin, timer, rawVideo, localVideo, childPid;
  let bridgeReady = false, renderReady = false, seq = 0, ack = 0, lastAck = Date.now();
  let childExit = null, failure = null, evidenceError = null, closing = false, started = false, bridgeClosed = false;
  let heartbeat = 0, finishPromise, finalFrame, finalManifest, actions = Promise.resolve(), eventWrites = Promise.resolve();
  let startupComplete;
  const startupDone = new Promise(resolveStartup => { startupComplete = resolveStartup; });
  const pendingInputs = new Map();
  const snapshots = new Map();
  const status = () => ({ takeId, fixture, cols: 110, rows: 28, seq, ack, started, childExit, failure, evidenceError, closing, bridgeClosed,
    renderedSeq: finalFrame?.renderedSeq ?? null, rendererEnded: finalFrame?.ended === true, finalized: !!finalManifest, workspace });
  const record = (event, notify = true) => {
    const row = { at: new Date().toISOString(), elapsedMs: Date.now() - Date.parse(startedAt), ...event };
    const write = eventWrites.then(async () => {
      if (evidenceError) throw Error(evidenceError);
      await bounded(appendFile(eventsFile, JSON.stringify(row) + '\n', { flush: true }), 'evidence append', 5000);
      if (notify) {
        try { onEvent(row); } catch (error) { fail(`Event observer: ${error.message}`); }
      }
    });
    // Recover the queue, but preserve the individual rejection for operations
    // (especially input) that must await evidence before proceeding.
    eventWrites = write.catch(error => {
      evidenceError ??= `Evidence write failed: ${error.message}`;
      fail(evidenceError, false); // Do not recursively append a failed append.
    });
    return write;
  };
  const inputOpen = () => {
    if (closing || failure || childExit) throw Error('CLI input is closed');
  };
  const command = async value => {
    if (value.op === 'input' || value.op === 'start') inputOpen();
    if (!bridge?.stdin.writable || bridge.stdin.destroyed) throw Error('PTY controller unavailable');
    await bounded(new Promise((resolveWrite, reject) => {
      bridge.stdin.write(JSON.stringify(value) + '\n', error => error ? reject(error) : resolveWrite());
    }), 'PTY controller write', 3000);
  };
  const emit = event => {
    if (!stream || stream.destroyed || stream.writableEnded) {
      if (started && !closing && !childExit) fail('Recorder output connection missing');
      return;
    }
    if (stream.writableLength > 1024 * 1024) { fail('Recorder output consumer fell behind'); return; }
    stream.write('data: ' + JSON.stringify(event) + '\n\n');
  };
  function fail(message, log = true) {
    if (failure) return;
    failure = String(message);
    // An unconfirmed write may have reached the CLI. Never retry it using a new
    // snapshot, and do not leave an action waiting on an acknowledgement we lost.
    for (const [id, resolveInput] of pendingInputs) resolveInput({ id, status: 'uncertain', reason: failure });
    pendingInputs.clear();
    // finish closes input synchronously, independently of the action queue.
    void finish().catch(error => { console.error(`Finalization failed: ${error.message}`); });
    if (log) void record({ type: 'incomplete', message: failure });
    emit({ type: 'error', message: failure });
  }
  const inputResult = event => {
    const waiting = pendingInputs.get(event.id);
    if (waiting) { pendingInputs.delete(event.id); waiting(event); }
  };
  const parseOutput = line => {
    try {
      const event = JSON.parse(line);
      if (event.type === 'ready') bridgeReady = true;
      else if (event.type === 'started') { started = true; childPid = event.pid; void record(event); }
      else if (event.type === 'output') {
        if (event.seq !== seq + 1) throw Error('PTY output sequence gap');
        if (seq === ack) lastAck = Date.now();
        seq = event.seq; emit(event);
      } else if (event.type === 'input') {
        void record(event).then(() => { if (!dropInputAck) inputResult(event); }).catch(() => {});
      } else if (event.type === 'protocol') {
        void record(event);
        if (event.status === 'rejected') fail('Terminal protocol response rejected');
      } else if (event.type === 'rejected') {
        void record(event); fail(`PTY rejected ${event.op ?? 'command'}: ${event.reason}`);
      } else if (event.type === 'exited') {
        childExit = event; void record(event); emit(event); stream?.end();
        if (event.seq !== seq || event.exitCode !== 0 || event.stopped) fail('CLI ended without a normal successful exit');
        else void finish().catch(error => { console.error(`Finalization failed: ${error.message}`); });
      } else if (event.type === 'error') fail(`PTY controller: ${event.message}`);
    } catch (error) { fail(`PTY controller: ${error.message}`); }
  };
  const state = async ({ ended = false, ms = 10_000 } = {}) => {
    const deadline = Date.now() + ms;
    try {
      while (Date.now() < deadline) {
        if (!page || page.isClosed()) throw Error('Recording page unavailable');
        await waitUntil(() => ack === seq || failure, 'terminal parse acknowledgement', Math.max(1, deadline - Date.now()));
        if (failure) throw Error(failure);
        const screen = await bounded(page.evaluate(async () => {
          const current = window.captureState();
          if (current.error) throw Error(current.error);
          // A synchronized frame can be fully parsed but still deliberately hidden.
          if (current.syncPending) return null;
          try { return await window.captureSettled(); }
          catch (error) {
            if (['Terminal synchronized output is not settled', 'Terminal changed during painting'].includes(error.message)) return null;
            throw error;
          }
        }), 'settled terminal frame', deadline - Date.now());
        if (screen && !screen.error && !screen.syncPending && screen.seq === seq && ack === seq && screen.renderedSeq === seq && (!ended || (screen.ended && !screen.connected))) return screen;
        await delay(25);
      }
      throw Error(`Timeout: ${ended ? 'final' : 'settled'} terminal frame`);
    } catch (error) { fail(`Renderer: ${error.message}`); throw error; }
  };
  const fingerprint = screen => hash(JSON.stringify([screen.text, screen.cursorX, screen.cursorY, screen.bufferType, screen.cols, screen.rows, screen.bracketedPasteMode]));
  async function inspect() {
    inputOpen();
    let screen, png, stable = false;
    for (let attempt = 0; attempt < 5; attempt++) {
      inputOpen();
      screen = await state();
      inputOpen();
      try { png = await bounded(page.screenshot({ timeout: 5000 }), 'terminal screenshot', 5000); }
      catch (error) { fail(`Screenshot: ${error.message}`); throw error; }
      inputOpen();
      const after = await state();
      if (screen.seq === after.seq && fingerprint(screen) === fingerprint(after)) { stable = true; break; }
    }
    inputOpen();
    if (!stable) throw Error('Screen is changing; wait for the actual input prompt');
    const snapshotId = randomUUID();
    const screenshot = resolve(takeDir, 'snapshots', `${snapshotId}.png`);
    const snapshot = { snapshotId, takeId, screenshot, ...screen, fingerprint: fingerprint(screen), capturedAt: new Date().toISOString() };
    try {
      await bounded(privateWrite(screenshot, png), 'snapshot image write', 5000);
      await bounded(privateWrite(resolve(takeDir, 'snapshots', `${snapshotId}.json`), json(snapshot)), 'snapshot metadata write', 5000);
      await record({ type: 'snapshot', snapshotId, seq: screen.seq });
    } catch (error) { fail(`Snapshot evidence: ${error.message}`); throw error; }
    inputOpen();
    snapshots.set(snapshotId, { ...snapshot, used: false });
    return snapshot;
  }
  async function respond(body) {
    inputOpen();
    if (!body || typeof body !== 'object' || Array.isArray(body)) throw Error('JSON object required');
    const allowed = ['snapshotId', 'key', 'text', 'decision', 'kind'];
    if (Object.keys(body).some(key => !allowed.includes(key))) throw Error('Unknown response field');
    if (!['user-decision', 'navigation', 'task-request'].includes(body.kind)) throw Error('Input kind required');
    if (typeof body.decision !== 'string' || !body.decision.trim() || body.decision.length > 4000) throw Error('Explicit decision or navigation purpose required');
    const snapshot = snapshots.get(body.snapshotId);
    if (!snapshot || snapshot.used) throw Error('Unknown or already consumed snapshot');
    const screen = await state();
    inputOpen();
    if (screen.seq !== snapshot.seq || fingerprint(screen) !== snapshot.fingerprint) throw Error('Stale snapshot; inspect the actual prompt again');
    let data;
    if (typeof body.key === 'string' && body.text === undefined && (Object.hasOwn(keys, body.key) || /^[\x20-\x7e]$/.test(body.key))) data = keys[body.key] ?? body.key;
    else if (typeof body.text === 'string' && body.key === undefined && body.text.length > 0 && !/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/.test(body.text)) {
      if (/[\r\n]/.test(body.text) && !screen.bracketedPasteMode) throw Error('Multiline text requires active bracketed paste mode; Enter is a separate decision');
      data = screen.bracketedPasteMode ? `\x1b[200~${body.text}\x1b[201~` : body.text;
    } else throw Error('Supply exactly one supported key or plain text; Enter is a separate decision');
    if (Buffer.byteLength(data) > 16 * 1024) throw Error('Input exceeds 16KiB');
    // Evidence must reach disk before any user input can reach the PTY.
    snapshot.used = true;
    const id = snapshot.snapshotId;
    await record({ type: 'input-request', id, kind: body.kind, decision: body.decision, key: body.key, text: body.text, expectedSeq: screen.seq,
      provenance: 'Producer-relayed input; not independent proof of human consent' });
    inputOpen();
    const result = new Promise(resolveInput => pendingInputs.set(id, resolveInput));
    let event;
    try {
      await command({ op: 'input', id, expectedSeq: screen.seq, data: Buffer.from(data).toString('base64') });
      event = await bounded(result, 'input acknowledgement missing; do not resend', inputAckTimeoutMs);
    } catch (error) {
      fail(`Uncertain input delivery: ${error.message}`);
      throw error;
    } finally { pendingInputs.delete(id); }
    if (event.status !== 'sent') throw Error(`PTY ${event.status} input: ${event.reason}`);
    return event;
  }
  function serialized(action) {
    const result = actions.then(action);
    actions = result.catch(() => {});
    return result;
  }
  const authenticate = (req, token) => {
    const actual = Buffer.from(req.headers.authorization ?? '');
    const expected = Buffer.from(`Bearer ${token}`);
    return actual.length === expected.length && timingSafeEqual(actual, expected);
  };
  const send = (res, code, value) => { res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' }); res.end(json(value)); };
  async function bodyOf(req) {
    const chunks = []; let length = 0;
    for await (const part of req) {
      length += part.length;
      if (length > 24 * 1024) throw Error('Request body exceeds limit');
      chunks.push(part);
    }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw Error('JSON object required');
    return value;
  }
  const assets = new Map([
    ['/', [resolve(root, 'production/ghcp-live/terminal.html'), 'text/html; charset=utf-8']],
    ['/terminal.js', [resolve(root, 'production/ghcp-live/terminal.js'), 'text/javascript']],
    ['/xterm.js', [resolve(root, 'node_modules/@xterm/xterm/lib/xterm.js'), 'text/javascript']],
    ['/xterm.css', [resolve(root, 'node_modules/@xterm/xterm/css/xterm.css'), 'text/css']],
  ]);
  const server = createServer({ requestTimeout: 15_000, headersTimeout: 10_000 }, async (req, res) => {
    try {
      if (req.headers.host !== new URL(origin).host || (req.headers.origin && req.headers.origin !== origin)) return send(res, 403, { error: 'Origin refused' });
      if (req.headers['sec-fetch-site'] === 'cross-site') return send(res, 403, { error: 'Cross-site request refused' });
      const control = authenticate(req, controlToken);
      const reading = control || authenticate(req, readToken);
      if (!reading) return send(res, 401, { error: 'Unauthorized' });
      if (req.method === 'GET' && req.url === '/status') return send(res, 200, status());
      if (req.method === 'GET' && req.url === '/events') {
        if (stream || started) return send(res, 409, { error: 'No second reader or replay allowed' });
        stream = res;
        res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
        res.write(': live stream\n\n');
        res.on('close', () => { if (!closing && !childExit) fail('Live recording stream disconnected'); });
        return;
      }
      if (req.method === 'GET' && assets.has(req.url)) {
        const [path, type] = assets.get(req.url);
        res.writeHead(200, { 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
          'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'", 'Referrer-Policy': 'no-referrer' });
        return res.end(await readFile(path));
      }
      if (req.method !== 'POST' || !['/inspect', '/respond', '/finish'].includes(req.url)) return send(res, 404, { error: 'Not found' });
      if (!control) return send(res, 403, { error: 'Read-only capability' });
      if (req.headers['content-type'] !== 'application/json') return send(res, 415, { error: 'JSON required' });
      const body = await bodyOf(req);
      if (req.url === '/inspect') return send(res, 200, await serialized(inspect));
      if (req.url === '/respond') return send(res, 200, await serialized(() => respond(body)));
      send(res, 202, { status: 'finalizing', takeId });
      void finish().catch(error => console.error(error.message));
    } catch (error) { if (!res.headersSent) send(res, 409, { error: error.message }); else res.destroy(); }
  });
  function finish() {
    if (finishPromise) return finishPromise;
    closing = true; clearInterval(timer);
    if (!childExit) failure ??= 'Stopped before normal CLI exit';
    // Stop the owned PTY immediately, even if startup or a UI action is pending.
    // Cleanup itself is not on the action queue: failures there may call finish.
    const stopping = bridge && !bridgeClosed && !childExit
      ? command({ op: 'stop' }).catch(error => { failure ??= error.message; }) : Promise.resolve();
    finishPromise = (async () => {
      // Every startup operation is bounded and checks closing before continuing.
      // Never tear down before a pending browser launch has returned its handle.
      await startupDone;
      await stopping;
      const cleanup = async (label, operation, ms = 5000) => {
        try { await bounded(Promise.resolve().then(operation), label, ms); return true; }
        catch (error) { failure ??= `${label}: ${error.message}`; return false; }
      };
      if (bridge && !bridgeClosed) {
        await cleanup('PTY exit', () => waitUntil(() => childExit || bridgeClosed, 'PTY exit', 5000), 5500);
        if (!bridgeClosed) {
          bridge.stdin.end();
          if (!await cleanup('bridge shutdown', () => waitUntil(() => bridgeClosed, 'bridge shutdown', 2000), 2500)) {
            bridge.kill('SIGTERM');
            if (!await cleanup('bridge termination', () => waitUntil(() => bridgeClosed, 'bridge termination', 3000), 3500)) {
              // The bridge reports the leader of its own pty.fork session only.
              if (!childExit && Number.isInteger(childPid) && childPid > 1) {
                try { process.kill(-childPid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') failure ??= error.message; }
              }
              bridge.kill('SIGKILL');
              await cleanup('bridge force close', () => waitUntil(() => bridgeClosed, 'bridge force close', 1000), 1500);
              bridge.stdin.destroy(); bridge.stdout.destroy(); bridge.stderr.destroy();
            }
          }
        }
      }
      // Let an in-flight input resolve (or latch uncertain delivery) before
      // judging success. Nothing new can enter the PTY once closing is true.
      await cleanup('pending action', () => actions, 12_000);
      if (!failure && started && childExit?.exitCode === 0 && !childExit.stopped) {
        await cleanup('final terminal frame', async () => { finalFrame = await state({ ended: true, ms: 5000 }); }, 5500);
      }
      if (!finalFrame?.ended || finalFrame.syncPending || finalFrame.renderedSeq !== seq || ack !== seq) failure ??= 'Final renderer frame was not completed';
      stream?.end();
      // Local launch keeps the encoder source in the take, not a remote server's
      // disposable artifacts directory. Wait for its trailer before any copy.
      const videoClosed = context ? await cleanup('video finalization', () => context.close(), fixtureHooks?.videoFinalizeTimeoutMs ?? 120_000) : false;
      let originalVideoSha256 = null, videoBytes = null, videoCopyVerified = false;
      if (videoClosed && localVideo && rawVideo) {
        try {
          const saved = await preserveVideo(localVideo, rawVideo, fixtureHooks);
          originalVideoSha256 = saved.sha256;
          videoBytes = saved.bytes;
          videoCopyVerified = true;
        } catch (error) { failure ??= `video save: ${error.message}`; }
      }
      if (!videoCopyVerified) failure ??= 'Raw video was not finalized';
      // Local browser.close has a native graceful-close/force-kill deadline of
      // 30s. Allow it to finish, including after a rejected/timed-out encoder close.
      if (browser) await cleanup('browser shutdown', () => browser.close(), 35_000);
      server.closeAllConnections();
      await cleanup('HTTP shutdown', () => new Promise(resolveClose => server.close(resolveClose)), 2000);
      if (!await cleanup('evidence flush', () => eventWrites)) evidenceError ??= 'Evidence flush did not complete';
      // Append before the manifest so a final event write failure cannot leave a
      // successful manifest behind. No recursive logging on a broken event log,
      // and no unbounded queue await after its flush deadline has elapsed.
      const manifestPath = resolve(takeDir, 'capture.json');
      if (!await cleanup('final evidence event', () => record({ type: 'finalizing', status: failure ? 'incomplete' : 'recorded', manifest: manifestPath }, false))) {
        evidenceError ??= 'Final evidence event did not complete';
      }
      const manifest = { ...status(), finalized: true, status: failure ? 'incomplete' : 'recorded', startedAt, finishedAt: new Date().toISOString(),
        rawVideo, localVideo, originalVideoSha256, videoBytes, videoCopyVerified,
        note: fixture ? 'FIXTURE ONLY; never Copilot success evidence' : 'Actual CLI output rendered live; human decisions relayed from chat, no log replay' };
      await bounded(privateWrite(manifestPath, json(manifest)), 'final manifest write', 5000);
      finalManifest = manifest;
      try { onEvent({ type: 'finalized', status: manifest.status, manifest: manifestPath }); } catch { /* Resources and manifest are already final. */ }
      return manifest;
    })();
    return finishPromise;
  }
  // A failed step prevents all later resource creation. The native launch
  // deadline also cancels its work; other pending operations die with the
  // owned browser during cleanup, never a subsequent launch.
  const startupStep = (label, operation) => {
    inputOpen();
    return bounded(Promise.resolve().then(operation), label);
  };
  server.on('error', error => fail(`HTTP server: ${error.message}`));
  try {
    await startupStep('HTTP startup', () => new Promise((resolveListen, reject) => {
      server.once('error', reject);
      server.listen(0, '127.0.0.1', () => { server.removeListener('error', reject); resolveListen(); });
    }));
    inputOpen();
    origin = `http://127.0.0.1:${server.address().port}`;
    const args = ['-B', resolve(root, 'scripts/copilot-pty-bridge.py'), '--workspace', workspace, '--take-dir', takeDir];
    if (fixture) args.push('--fixture');
    bridge = spawn('python3', args, { stdio: ['pipe', 'pipe', 'pipe'] });
    bridge.on('error', error => fail(`PTY bridge: ${error.message}`));
    bridge.stdin.on('error', error => fail(`PTY stdin: ${error.message}`));
    bridge.on('close', () => { bridgeClosed = true; if (!childExit && !closing) fail('PTY bridge exited without final output state'); });
    createInterface({ input: bridge.stdout }).on('line', parseOutput);
    bridge.stderr.on('data', data => { void record({ type: 'bridge-stderr', message: data.toString() }); });
    await waitUntil(() => bridgeReady || bridgeClosed || failure, 'PTY bridge startup');
    assert.ok(bridgeReady && !failure, failure ?? 'PTY startup failed');
    inputOpen();
    browser = await chromium.launch({ headless: true, timeout: 10_000 });
    inputOpen();
    browser.on('disconnected', () => { if (!closing) fail('Recording browser disconnected'); });
    context = await startupStep('browser context', () => browser.newContext({ viewport: { width: 1920, height: 1080 },
      recordVideo: { dir: resolve(takeDir, 'video'), size: { width: 1920, height: 1080 } },
      locale: 'ko-KR', colorScheme: 'dark', deviceScaleFactor: 1 }));
    // Only the renderer's read capability enters browser memory. Control stays host-side.
    await startupStep('renderer headers', () => context.setExtraHTTPHeaders({ Authorization: `Bearer ${readToken}` }));
    await startupStep('renderer route', () => context.route('**/*', route => new URL(route.request().url()).origin === origin ? route.continue() : route.abort()));
    page = await startupStep('recording page', () => context.newPage());
    page.on('pageerror', error => fail(`Renderer: ${error.message}`));
    page.on('crash', () => fail('Recording page crashed'));
    page.on('close', () => { if (!closing) fail('Recording page closed'); });
    await startupStep('renderer ready binding', () => page.exposeFunction('rendererReady', () => { renderReady = true; }));
    await startupStep('renderer ack binding', () => page.exposeFunction('rendererAck', value => { if (value !== ack + 1 || value > seq) fail('Invalid render acknowledgement'); else { ack = value; lastAck = Date.now(); } }));
    await startupStep('renderer failure binding', () => page.exposeFunction('rendererFailure', message => fail(message)));
    await startupStep('terminal protocol binding', () => page.exposeFunction('terminalProtocol', async data => {
      if (closing || failure) return;
      try { await command({ op: 'protocol', data: Buffer.from(data).toString('base64') }); }
      catch (error) { fail(`PTY protocol: ${error.message}`); throw error; }
    }));
    await startupStep('renderer navigation', () => page.goto(origin, { timeout: 9000 }));
    await waitUntil(() => renderReady || failure, 'recording renderer');
    inputOpen();
    video = page.video();
    assert.ok(video, 'Recording video unavailable');
    // Preserve the encoder's original path even if finalization or copying fails.
    localVideo = await startupStep('local video path', () => video.path());
    rawVideo = resolve(takeDir, 'video', 'capture.webm');
    await command({ op: 'start', cols: 110, rows: 28 });
    await waitUntil(() => started || failure, 'actual PTY startup');
    inputOpen();
    await startupStep('control file', () => privateWrite(controlFile, json({ origin, token: controlToken, takeId, takeDir })));
    await startupStep('capture start evidence', () => privateWrite(resolve(takeDir, 'capture-start.json'), json({ takeId, fixture, startedAt, workspace, rawVideo, localVideo })));
    inputOpen();
    timer = setInterval(() => {
      if (closing) return;
      if (!childExit) stream?.write(': heartbeat\n\n');
      if (seq > ack && Date.now() - lastAck > 5000) fail('Renderer stopped acknowledging output');
      if (++heartbeat % 30 === 0) void bounded(statfs(takeDir), 'disk space check', 5000).then(s => { if (s.bavail * s.bsize < 200 * 1024 * 1024) fail('Recording disk space below 200MiB'); }).catch(error => fail(error.message));
    }, 1000);
    await record({ type: 'ready', controlFile, workspace, fixture });
    inputOpen();
    startupComplete();
    return { origin, controlFile, takeDir, takeId, workspace, inspect: () => serialized(inspect),
      respond: body => serialized(() => respond(body)), finish, status,
      fixturePage: fixture ? page : undefined };
  } catch (error) {
    startupComplete();
    fail(error.message);
    await finish().catch(() => {});
    throw error;
  }
}

export async function control(file, action, body = {}) {
  const config = JSON.parse(await readFile(file, 'utf8'));
  const url = new URL(config.origin);
  assert.equal(url.hostname, '127.0.0.1');
  assert.ok(['inspect', 'respond', 'finish'].includes(action));
  const response = await fetch(`${url.origin}/${action}`, { method: 'POST', headers: {
    Authorization: `Bearer ${config.token}`, 'Content-Type': 'application/json', Origin: url.origin,
  }, body: JSON.stringify(body), signal: AbortSignal.timeout(20_000) });
  const result = await response.json();
  if (!response.ok) throw Error(result.error ?? `HTTP ${response.status}`);
  return result;
}

async function main() {
  const args = process.argv.slice(2); const options = {};
  for (let i = 0; i < args.length; i++) {
    const key = args[i];
    if (key === '--fixture') options.fixture = true;
    else if (['--run-dir', '--control', '--snapshot', '--key', '--text-file', '--decision', '--kind'].includes(key)) {
      if (!args[i + 1]) throw Error(`Missing ${key}`); options[key.slice(2)] = args[++i];
    } else if (['inspect', 'respond', 'finish', 'start'].includes(key)) options.action = key;
    else throw Error(`Unknown argument: ${key}`);
  }
  if (options.control) {
    const body = options.action === 'respond' ? { snapshotId: options.snapshot, key: options.key,
      text: options['text-file'] ? await readFile(resolve(options['text-file']), 'utf8') : undefined,
      decision: options.decision, kind: options.kind ?? 'user-decision' } : {};
    console.log(json(await control(options.control, options.action ?? 'inspect', body)));
    return;
  }
  const recorder = await startRecorder({ runDir: options['run-dir'] ? resolve(options['run-dir']) : undefined, fixture: options.fixture,
    onEvent: event => {
      if (event.type === 'finalized' && event.status !== 'recorded') process.exitCode = 1;
      if (['ready', 'incomplete', 'exited', 'finalized'].includes(event.type)) console.log(JSON.stringify(event));
    } });
  for (const signal of ['SIGINT', 'SIGTERM']) process.once(signal, () => { void recorder.finish().catch(error => { console.error(error); process.exitCode = 1; }); });
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main().catch(error => { console.error(error.message); process.exitCode = 1; });
