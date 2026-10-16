// Explicit real-browser/in-process ASGI/SQL check, not real HTTP/network proof.
// Requires an already-installed Playwright module and Chromium. No downloads,
// cloud calls, real credentials, production mutations or universal UX claim.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdir, readFile, stat} from 'node:fs/promises';
import {createInterface} from 'node:readline';
import {resolve} from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const root=fileURLToPath(new URL('../../',import.meta.url));
const modulePath=process.env.SELFLEARN_PLAYWRIGHT_MODULE;
assert.ok(modulePath,'Set SELFLEARN_PLAYWRIGHT_MODULE to an existing local playwright/index.mjs');
const {chromium}=await import(pathToFileURL(resolve(modulePath)).href);
// Explicit existing-local binary only. A cached browser may differ from the
// module's expected revision; report that override, never install/download it.
const executable=process.env.SELFLEARN_CHROMIUM_EXECUTABLE;
if(executable)assert.ok((await stat(executable)).isFile(),'Explicit Chromium executable must be an existing local file');
const playwrightVersion=JSON.parse(await readFile(resolve(modulePath,'../package.json'),'utf8')).version;
const python=process.env.SELFLEARN_TEST_PYTHON || 'python3';
const server=spawn(python,['tests/browser/trace_browser_bridge.py'],{cwd:root,stdio:['pipe','pipe','pipe']});
let stderr='';
server.stderr.on('data',chunk=>{stderr=(stderr+chunk.toString()).slice(-16000);});
const lines=createInterface({input:server.stdout});
const pending=new Map();
let requestID=0;
let readOnlyVerified=false;
// 'close' follows stdout drainage; 'exit' alone can race the final SQL ACK.
const exited=new Promise(yes=>server.once('close',(code,signal)=>yes({code,signal})));
lines.on('line',line=>{
  const value=JSON.parse(line);
  if(value.fixture_closed===true)readOnlyVerified=value.read_only_unchanged===true;
  if(pending.has(value.id)){pending.get(value.id)(value);pending.delete(value.id);}
});
function apiRequest(method,path,headers){
  const id=++requestID;
  return new Promise((yes,no)=>{
    const timer=setTimeout(()=>{pending.delete(id);no(new Error('Local trace request timed out'));},15000);
    pending.set(id,value=>{clearTimeout(timer);yes(value);});
    server.stdin.write(JSON.stringify({id,method,path,headers})+'\n');
  });
}
const ready=new Promise((yes,no)=>{
  const timer=setTimeout(()=>no(new Error('Local trace fixture startup timed out')),30000);
  lines.on('line',line=>{
    try {const value=JSON.parse(line);if(value.fixture_ready){clearTimeout(timer);yes(value);}} catch {}
  });
  server.once('exit',code=>{clearTimeout(timer);no(new Error('Local fixture exited '+code+' '+stderr));});
});
let browser;
const output=resolve(process.env.SELFLEARN_BROWSER_ARTIFACTS || '/private/tmp/selflearning-trace-browser');
const key='local-browser-fixture-owner-'+'a'.repeat(32);
const results=[];
try {
  const fixture=await ready;
  assert.equal(fixture.url,'http://selflearn-browser.invalid');
  assert.equal(fixture.receipts,4);assert.equal(fixture.revisions,12);
  await mkdir(output,{recursive:true});
  browser=await chromium.launch({headless:true,chromiumSandbox:true,
    ...(executable?{executablePath:resolve(executable)}:{})});
  for(const [name,width,height] of [['desktop',1365,900],['phone',390,844],['narrow',320,740]]){
    const context=await browser.newContext({viewport:{width,height}});
    const page=await context.newPage(),errors=[],requests=[];
    page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>requests.push({url:request.url(),method:request.method(),headers:request.headers()}));
    await context.route('**/*',async route=>{
      assert.ok(route.request().url().startsWith(fixture.url+'/'),'Browser must not call external services');
      const request=route.request(),url=new URL(request.url());
      assert.equal(request.method(),'GET','Browser verification is read-only');
      const reply=await apiRequest(request.method(),url.pathname+url.search,request.headers());
      const headers={...reply.headers};delete headers['content-length'];
      await route.fulfill({status:reply.status,headers,body:Buffer.from(reply.body,'base64')});
    });
    await page.goto(fixture.url,{waitUntil:'networkidle'});
    assert.equal(await page.locator('#trace-search').isDisabled(),true);
    assert.equal(requests.some(r=>r.url.includes('/api/')),false,'Locked page must fetch no private data');
    await page.locator('#access-key').fill(key);
    await page.locator('#unlock').click();
    await page.waitForFunction(()=>document.getElementById('trace-state').textContent.includes('4 forecasts'));
    assert.equal(await page.locator('#access-key').inputValue(),'');
    assert.equal(await page.locator('#trace-list .record').count(),4);
    await page.locator('#trace-list button').first().click();
    await page.locator('#trace-detail h3').waitFor();
    const trace=JSON.parse(await page.locator('#trace-detail pre').innerText());
    assert.equal(trace.read_only,true);assert.equal(trace.forecast.outcome_state,'void');
    assert.equal(trace.outcome_revisions.length,3);
    assert.deepEqual(trace.outcome_revisions.map(row=>row.status),['FINAL','FINAL','VOID']);
    assert.equal(trace.forecast.outcome_hit,null);
    assert.equal(trace.production_connection_verified,false);
    assert.equal(trace.source_freshness,'unverified');
    assert.equal(trace.forecast.model_adoption,'unverified');
    const layout=await page.evaluate(()=>({viewport:innerWidth,page:document.documentElement.scrollWidth,
      pre:document.querySelector('#trace-detail pre').getBoundingClientRect().width,
      detail:document.querySelector('#trace-detail').getBoundingClientRect().width,
      local:Object.keys(localStorage),session:Object.keys(sessionStorage)}));
    assert.ok(layout.page<=width+1,'Unexpected horizontal document overflow: '+JSON.stringify(layout));
    assert.ok(layout.pre<=layout.detail+1,'Trace details must stay inside their panel');
    assert.deepEqual(layout.local,[]);assert.deepEqual(layout.session,[]);
    assert.deepEqual(await context.cookies(),[]);
    assert.equal(errors.length,0,errors.join('\n'));
    assert.ok(requests.filter(r=>r.url.includes('/api/')).every(r=>r.method==='GET'));
    assert.ok(requests.filter(r=>r.url.includes('/api/')).every(r=>r.headers.authorization==='Bearer '+key));
    assert.ok(requests.every(r=>!r.url.includes(key)),'Credential must never enter a URL');
    await page.locator('#forecast-trace').screenshot({path:resolve(output,name+'-trace.png')});
    await page.locator('#lock').click();
    assert.equal(await page.locator('#trace-list .record').count(),0);
    assert.equal(await page.locator('#trace-search').isDisabled(),true);
    assert.equal(await page.locator('#trace-detail pre').count(),0);
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('#trace-search').isDisabled(),true,'Reload cannot restore an owner key');
    results.push({name,width,height,passed:true,trace_receipt_id:trace.forecast.receipt_id,layout});
    await context.close();
  }
  // EOF lets the bridge execute its actual post-read SQL immutability check.
  // SIGTERM cleanup alone must never be mistaken for that assertion passing.
  server.stdin.end();
  let closeTimer;
  const closed=await Promise.race([exited,new Promise((_,no)=>{
    closeTimer=setTimeout(()=>no(new Error('Local trace fixture shutdown timed out')),15000);
  })]).finally(()=>clearTimeout(closeTimer));
  assert.equal(closed.code,0,stderr);
  assert.equal(readOnlyVerified,true,'Bridge must finish the SQL read-only assertion');
  console.log(JSON.stringify({scope:fixture.scope,browser:await browser.version(),playwright:playwrightVersion,
    explicit_existing_browser_override:Boolean(executable),results,artifacts:output,
    hosted_native_verified:false,production_connection_verified:false}));
} finally {
  if(browser)await browser.close();
  lines.close();
  if(server.exitCode===null)server.kill('SIGTERM');
  let killTimer;
  await Promise.race([exited,new Promise(yes=>{
    killTimer=setTimeout(()=>{server.kill('SIGKILL');yes();},10000);
  })]).finally(()=>clearTimeout(killTimer));
}
