import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import test from 'node:test';
import {asRole,canonical,digestText} from './harness.mjs';
import {openGameCaptureApplicationDatabase} from './capture_application_harness.mjs';

const fixture = new URL('./scoped_journal_paging_fixture.sql',import.meta.url);
const migration = new URL('../../supabase/migrations/20261123195835_pack_scoped_journal_pages_incrementally.sql',import.meta.url);
const scope = (game_id) => canonical({policy:'nfl.capture_game_scope_v1',season:2026,week:11,game_id});
const ids = (first,last) => canonical(Array.from({length:last-first+1},(_,i)=>'sha256:'+String(first+i).padStart(64,'0')));
const observed = 'selflearn_journal_read_observed_game_v1';
const objects = 'selflearn_journal_read_objects_v1';
const read = (db, name, args) => asRole(db,'service_role',async()=>(await db.query(`select public.${name}($1,$2,$3,$4,$5)::text value`,args)).rows[0].value);

async function allPages(db, name, view, key, limit) {
  const pages=[];
  let after=null,watermark=null;
  do {
    const text=await read(db,name,[view,key,after,limit,watermark]);
    const page=JSON.parse(text);
    assert.ok(page.rows.length>0);
    assert.ok(page.rows.every(row=>row.payload_json.includes('"float":1.0')));
    if(name===observed)assert.ok(page.rows.every(row=>row.ack_json==='{"raw_ack_lexeme":1.0}'));
    pages.push({digest:digestText(text),rows:page.rows.length,bytes:Buffer.byteLength(text)});
    after=page.next_after;
    if(watermark!==null)assert.equal(page.watermark,watermark);
    watermark=page.watermark;
  } while(after);
  assert.equal(pages.reduce((n,page)=>n+page.rows,0),24);
  return pages;
}

test('scoped page packing serializes fewer candidates with identical bytes, cursors and fences',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    await db.exec(await readFile(fixture,'utf8'));
    const cases=[[observed,'game_generations',scope('G1')],[objects,'objects',ids(100,123)]];
    const baseline=[];
    const began=performance.now();
    for(const args of cases)baseline.push(await allPages(db,...args,500));
    const baselineMs=performance.now()-began;
    const smallBefore=await read(db,observed,['game_generations',scope('G1'),null,1,'212']);
    const singleOversize=await read(db,observed,['game_generations',scope('G2'),null,500,null]);
    assert.ok(Buffer.byteLength(singleOversize)>6*1024*1024);
    assert.equal(JSON.parse(singleOversize).rows.length,1);
    assert.equal(JSON.parse(singleOversize).next_after,null);
    await assert.rejects(read(db,objects,['objects',ids(124,124),null,500,null]),e=>e.code==='PT422'&&e.message==='object_exceeds_batch_page_byte_limit');
    for(const [name,key,view] of [[observed,scope('G1'),'receipts'],[observed,scope('G1'),'revisions'],[observed,scope('G1'),'observations']])
      baseline.push(await read(db,name,[view,key,null,500,null]));
    await db.exec(await readFile(migration,'utf8'));
    const optimized=[];
    const started=performance.now();
    for(const args of cases)optimized.push(await allPages(db,...args,500));
    const optimizedMs=performance.now()-started;
    for(const [name,key,view] of [[observed,scope('G1'),'receipts'],[observed,scope('G1'),'revisions'],[observed,scope('G1'),'observations']])
      optimized.push(await read(db,name,[view,key,null,500,null]));
    assert.deepEqual(optimized,baseline);
    assert.equal(await read(db,observed,['game_generations',scope('G1'),null,1,'212']),smallBefore);
    assert.equal(await read(db,observed,['game_generations',scope('G2'),null,500,null]),singleOversize);
    await assert.rejects(read(db,objects,['objects',ids(124,124),null,500,null]),e=>e.code==='PT422'&&e.message==='object_exceeds_batch_page_byte_limit');
    for(const [name,view,key] of cases) {
      await assert.rejects(read(db,name,[view,key,'227',1,'226']),e=>e.code==='PT422'&&e.message==='cursor_after_watermark');
      for(const role of ['anon','authenticated'])await assert.rejects(asRole(db,role,()=>db.query(
        `select public.${name}($1,$2,null,1,null)`,[view,key])),e=>e.code==='42501');
    }
    console.log('SCOPED_PAGE_SQL_SCALE '+JSON.stringify({objects:24,game_generations:24,baseline_ms:baselineMs,optimized_ms:optimizedMs,
      scope:'postgresql_wasm_not_hosted_latency'}));
  } finally {await db.close();}
});
