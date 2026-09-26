import { chromium } from 'playwright';
import { spawn, spawnSync } from 'node:child_process';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import assert from 'node:assert/strict';
import { setTimeout as delay } from 'node:timers/promises';

const root = resolve(import.meta.dirname, '..');
const production = resolve(root, 'production');
const evidence = resolve(root, 'evidence/copilot-run');
for (const dir of ['clips/raw','screenshots']) await mkdir(resolve(production,dir),{recursive:true});
await mkdir(evidence,{recursive:true});
const children=[];
const checks=[];
const responses=[];
const pageErrors=[];
let browser;
function check(name,condition) { assert.ok(condition,name); checks.push({name,passed:true}); }
async function server(directory) {
  // Let the OS choose an unused port; never connect to or stop another session's server.
  const launcher="import {createAppServer} from './src/server.mjs'; const s=createAppServer(); s.listen(0,'127.0.0.1',()=>console.log('READY '+s.address().port)); process.on('SIGTERM',()=>{s.close();s.closeAllConnections()});";
  const child=spawn(process.execPath,['--input-type=module','-e',launcher],{cwd:resolve(root,directory),stdio:['ignore','pipe','pipe']});
  children.push(child);
  let output=''; child.stdout.on('data',d=>output+=d); child.stderr.on('data',d=>output+=d);
  for(let i=0;i<100;i++) {
    if(child.exitCode!==null) throw Error(`Server ${directory} exited: ${output}`);
    const port=output.match(/READY (\d+)/)?.[1];
    if(port) {
      const base=`http://127.0.0.1:${port}`;
      const response=await fetch(`${base}/api/quotes`);
      if(response.ok) return base;
    }
    await delay(100);
  }
  throw Error(`Server ${directory} did not start: ${output}`);
}
async function ready(page) {
  await page.waitForFunction(()=>document.querySelector('#quote-summary').getAttribute('aria-busy')==='false'&&document.querySelector('#net-amount').textContent.includes('원'));
}
async function recording(name,url,act) {
  const context=await browser.newContext({viewport:{width:1280,height:740},recordVideo:{dir:resolve(production,'clips/raw'),size:{width:1280,height:740}},locale:'ko-KR',colorScheme:'light',deviceScaleFactor:1});
  const recordingStarted=Date.now();
  const page=await context.newPage(); page.on('pageerror',e=>pageErrors.push(String(e)));
  await page.goto(url); await ready(page);
  await page.locator('#quote-form').scrollIntoViewIfNeeded();
  await page.evaluate(()=>window.scrollTo(0,Math.max(0,document.querySelector('#quote-form').getBoundingClientRect().top+scrollY-20)));
  const box=await page.locator('#quote-form').boundingBox();
  const start=Date.now();
  await act(page);
  await delay(2500);
  await page.screenshot({path:resolve(production,`screenshots/${name}.png`)});
  const raw=await page.video().path();
  await context.close();
  // Remove startup-only frames; the rest is an unaltered recording of actual UI actions.
  const trim=Math.max(0,(start-recordingStarted)/1000);
  const x=Math.max(0,Math.floor(box.x)-8),y=Math.max(0,Math.floor(box.y)-8);
  const width=Math.min(1280-x,Math.ceil(box.width)+16);
  const height=740-y;
  const result=spawnSync('ffmpeg',['-v','error','-y','-i',raw,'-ss',trim.toFixed(3),'-vf',`crop=${width}:${height}:${x}:${y},pad=ceil(iw/2)*2:ceil(ih/2)*2`,'-c:v','libx264','-crf','18','-preset','veryfast','-pix_fmt','yuv420p','-an','-movflags','+faststart',resolve(production,`clips/${name}.mp4`)],{encoding:'utf8'});
  if(result.status!==0) throw Error(result.stderr);
  console.log(`CAPTURED ${name}`);
}
async function post(base,quoteId,discountBps,extra={}) {
  const body={quoteId,discountBps,...extra};
  const response=await fetch(`${base}/api/send`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const result=await response.json();
  responses.push({request:{method:'POST',path:'/api/send',body},status:response.status,response:result});
  return {status:response.status,body:result};
}
try {
  const before=await server('evidence/copilot-run/baseline-snapshot'),after=await server('demo/run');
  browser=await chromium.launch({headless:true});
  await recording('before',before,async page=>{
    check('Baseline discount 10% may send',await page.locator('#send-quote').isEnabled());
    await delay(1400); await page.locator('#send-quote').click();
    await page.getByText('모의 전송 완료 — 외부 발송 없음',{exact:true}).waitFor();
    check('Baseline mock send succeeds',(await page.locator('#feedback').innerText()).includes('모의 전송 완료'));
    await delay(4500);
  });
  await recording('blocked',after,async page=>{
    check('Low margin send disabled',await page.locator('#send-quote').isDisabled());
    check('Low margin amount correct',(await page.locator('#net-amount').innerText())==='9,000,000원');
    check('Block reason visible',(await page.locator('#quote-form').innerText()).includes('15'));
    await page.locator('#send-quote').hover(); await delay(6500);
  });
  await recording('allowed',after,async page=>{
    await delay(1300); await page.locator('#discount').fill('0'); await ready(page);
    check('20% margin send enabled',await page.locator('#send-quote').isEnabled());
    check('20% displayed',(await page.locator('#margin-value').innerText()).startsWith('20'));
    await delay(1700); await page.locator('#send-quote').click();
    await page.getByText('모의 전송 완료 — 외부 발송 없음',{exact:true}).waitFor();
    check('Allowed mock send succeeds',(await page.locator('#feedback').innerText()).includes('모의 전송 완료'));
    await delay(3500);
  });
  await recording('boundary',after,async page=>{
    await page.locator('#discount').fill('0'); await ready(page);
    await page.locator('#quote-id').selectOption('Q-1500'); await ready(page);
    check('Exact 15% UI allows send',await page.locator('#send-quote').isEnabled());
    await delay(3300);
    await page.locator('#quote-id').selectOption('Q-1499'); await ready(page);
    check('One-won-below boundary UI blocks',await page.locator('#send-quote').isDisabled());
    await delay(3900);
  });
  let r=await post(before,'Q-1001',1000);check('Baseline direct send allowed',r.status===200&&r.body.sent);
  r=await post(after,'Q-1001',1000);check('Direct low margin send 422',r.status===422&&r.body.error.code==='MARGIN_BELOW_MINIMUM');
  r=await post(after,'Q-1001',0);check('Direct normal send 200',r.status===200&&r.body.sent);
  r=await post(after,'Q-1500',0);check('Exact boundary API 200',r.status===200&&r.body.sent);
  r=await post(after,'Q-1499',0);check('One-won-below boundary API 422',r.status===422);
  r=await post(after,'Q-1501',0);check('Above boundary API 200',r.status===200);
  r=await post(after,'Q-1001',1000,{costWon:1});check('Forged server-owned cost rejected',r.status===400);
  r=await post(after,'Q-1001',10000);check('Zero net rejected',r.status===400);
  r=await post(after,'Q-1001',-1);check('Negative discount rejected',r.status===400);
  const context=await browser.newContext({viewport:{width:390,height:844},locale:'ko-KR'});
  const mobile=await context.newPage();mobile.on('pageerror',e=>pageErrors.push(String(e)));
  await mobile.goto(after);await ready(mobile);
  check('Mobile page has no horizontal overflow',await mobile.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));
  await mobile.locator('#discount').fill('0');await ready(mobile);
  check('Mobile normal send enabled',await mobile.locator('#send-quote').isEnabled());
  await mobile.locator('#send-quote').click();await mobile.getByText('모의 전송 완료 — 외부 발송 없음',{exact:true}).waitFor();
  await mobile.screenshot({path:resolve(production,'screenshots/mobile.png'),fullPage:true});
  await context.close();check('No browser JS exceptions',pageErrors.length===0);
  await writeFile(resolve(evidence,'browser-checks.json'),JSON.stringify({passed:checks.length,checks,pageErrors},null,2));
  await writeFile(resolve(evidence,'api-responses.json'),JSON.stringify(responses,null,2));
  console.log(`BROWSER/API CHECKS PASSED: ${checks.length}`);
} finally {
  await browser?.close();
  for(const child of children) child.kill('SIGTERM');
}
