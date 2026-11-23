import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import test from 'node:test';
import {asRole,canonical,digestText} from './harness.mjs';
import {openGameCaptureApplicationDatabase} from './capture_application_harness.mjs';

const load = name => readFile(new URL(name,import.meta.url),'utf8');
const prior = '../../supabase/migrations/20261123195835_pack_scoped_journal_pages_incrementally.sql';
const migration = '../../supabase/migrations/20261123201942_bound_scoped_receipt_history_pages.sql';
const scope = canonical({policy:'nfl.capture_game_scope_v1',season:2026,week:11,game_id:'G1'});
const read = async (db,view,after,limit,watermark) => (await db.query(
  'select public.selflearn_journal_read_observed_game_v1($1,$2,$3,$4,$5)::text value',
  [view,scope,after,limit,watermark])).rows[0].value;

async function walk(db,view,expected,startingAfter=null,requiredWatermark=null) {
  const fingerprints=[];
  let n=0,after=startingAfter,watermark=requiredWatermark;
  await asRole(db,'service_role',async()=>{
    do {
      const raw=await read(db,view,after,500,watermark),page=JSON.parse(raw);
      assert.ok(page.rows.length<=500);
      assert.ok(page.rows.every(row=>row.payload_json.includes('"raw_float":80.0')||row.payload_json.includes('"raw_float":81.0')));
      if(watermark!==null)assert.equal(page.watermark,watermark);
      fingerprints.push(digestText(raw));
      n+=page.rows.length;after=page.next_after;watermark=page.watermark;
    } while(after);
  });
  assert.equal(n,expected);
  return fingerprints;
}

test('scoped receipt and complete history bounds preserve every legacy response byte',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    await db.exec(await load('./scoped_history_paging_fixture.sql'));
    await db.exec(await load(prior));
    const cases=[['receipts',2996],['observations',1747],['revisions',1747],
      ['observations',1727,'30000','95000'],['revisions',1727,'30000','95000']];
    const baseline=[];
    const began=performance.now();
    for(const args of cases)baseline.push(await walk(db,...args));
    const baselineMs=performance.now()-began;
    await db.exec(await load(migration));
    const optimized=[];
    const started=performance.now();
    for(const args of cases)optimized.push(await walk(db,...args));
    const optimizedMs=performance.now()-started;
    assert.deepEqual(optimized,baseline);
    for(const role of ['anon','authenticated']) await assert.rejects(asRole(db,role,()=>read(db,'revisions',null,1,null)),e=>e.code==='42501');
    await assert.rejects(asRole(db,'service_role',()=>read(db,'revisions','95001',1,'95000')),e=>e.code==='PT422'&&e.message==='cursor_after_watermark');
    console.log('SCOPED_HISTORY_SQL_SCALE '+JSON.stringify({receipts_in_ledger:28736,selected_receipts:2996,
      selected_revisions_and_observations:1747,baseline_ms:baselineMs,optimized_ms:optimizedMs,scope:'postgresql_wasm_not_hosted_latency'}));
  } finally {await db.close();}
});
