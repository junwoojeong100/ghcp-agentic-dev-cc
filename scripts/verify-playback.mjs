import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { stat, realpath, readFile, writeFile, mkdir } from 'node:fs/promises';
import { once } from 'node:events';
import { isAbsolute, relative, resolve } from 'node:path';
import { chromium } from 'playwright';

const root = resolve(import.meta.dirname, '..');
const args = process.argv.slice(2);
assert.ok(args.length === 0 || (args.length === 2 && args[0] === '--run-dir'),
  'Expected --run-dir <directory inside production/ghcp-live> or no arguments');
const live = args.length > 0;
const inside = (parent, child) => {
  const path = relative(parent, child);
  return !isAbsolute(path) && path !== '..' && !path.startsWith('../');
};
const production = await realpath(resolve(root, live ? args[1] : 'production'));
if (live) assert.ok(inside(await realpath(resolve(root, 'production/ghcp-live')), production), 'Run directory escapes live production');
const timeline = live ? JSON.parse(await readFile(resolve(production, 'timeline.json'), 'utf8')) : null;
const expectedDuration = live ? timeline.duration : 300;
assert.ok(Number.isFinite(expectedDuration) && expectedDuration > 2);
const file = await realpath(resolve(root, live ? timeline.video.path : 'deliverables/ghcp-cxo-demo-ko.mp4'));
if (live) assert.ok(inside(await realpath(resolve(root, 'deliverables/ghcp-live')), file), 'Video escapes live deliverables');
const { size } = await stat(file);
const server = createServer((req, res) => {
  if (req.url !== '/film.mp4') { res.writeHead(404); res.end(); return; }
  const range = req.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
  if (range) {
    const start = Number(range[1]);
    const end = range[2] ? Math.min(Number(range[2]), size - 1) : size - 1;
    if (start > end || start >= size) { res.writeHead(416); res.end(); return; }
    res.writeHead(206, {'Content-Type':'video/mp4', 'Content-Range':`bytes ${start}-${end}/${size}`,
      'Accept-Ranges':'bytes', 'Content-Length':end-start+1});
    createReadStream(file, { start, end }).pipe(res);
  } else {
    res.writeHead(200, {'Content-Type':'video/mp4','Content-Length':size,'Accept-Ranges':'bytes'});
    createReadStream(file).pipe(res);
  }
});
server.listen(0, '127.0.0.1');
await once(server, 'listening');
let browser;
try {
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const url = `http://127.0.0.1:${server.address().port}/film.mp4`;
  await page.setContent(`<video id="film" muted controls preload="auto" src="${url}"></video>`);
  await page.waitForFunction(() => document.querySelector('video').readyState >= 2);
  const metadata = await page.locator('video').evaluate(v => ({duration:v.duration,width:v.videoWidth,height:v.videoHeight}));
  assert.ok(Math.abs(metadata.duration - expectedDuration) < .05);
  assert.equal(metadata.width, 1920); assert.equal(metadata.height, 1080);
  const positions = [];
  for (const at of [0, expectedDuration / 2, expectedDuration - 2]) {
    await page.locator('video').evaluate(async (v, at) => { v.currentTime = at; await v.play(); }, at);
    await page.waitForFunction(at => document.querySelector('video').currentTime > at + .25, at);
    const state = await page.locator('video').evaluate(v => { v.pause(); return {time:v.currentTime,error:v.error,readyState:v.readyState}; });
    assert.equal(state.error, null); positions.push(state.time);
  }
  await page.locator('video').evaluate(async (v, duration) => { v.currentTime = duration - .3; await v.play(); }, expectedDuration);
  await page.waitForFunction(() => document.querySelector('video').ended);
  const report = {engine:'Playwright Chromium', metadata, testedPositions:positions, reachedEnd:true,
    playback:'Muted playback and seeking verified at start, middle and end.',
    limitation:'This is not a complete human viewing or Korean narration listening check.'};
  const out = resolve(root, production, 'video-qa'); await mkdir(out, {recursive:true});
  await writeFile(resolve(out,'playback-report.json'), JSON.stringify(report,null,2));
  console.log(JSON.stringify(report,null,2));
} finally {
  await browser?.close();
  server.closeAllConnections(); server.close();
}
