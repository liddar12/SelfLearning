import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import test from 'node:test';
import {asRole,digestText} from './harness.mjs';
import {openGameCaptureApplicationDatabase} from './capture_application_harness.mjs';

const load = name=>readFile(new URL(name,import.meta.url),'utf8');
const migration='../../supabase/migrations/20261124143736_bound_shared_observed_journal_reads.sql';
const id=number=>'sha256:'+String(number).padStart(64,'0');
const raw=async(db,args)=>(await db.query('select public.selflearn_journal_read_observed_v1($1,$2,$3,$4,$5)::text value',args)).rows[0].value;
async function measure(db,cases) {
  const start=performance.now();
  const evidence=await asRole(db,'service_role',async()=>{
    const evidence=[];
    for(const [view,key,after,limit,fence] of cases) {
      const body=await raw(db,[view,key,after,limit,fence]);
      const value=JSON.parse(body);
      assert.ok(value.rows.length<=limit);
      evidence.push({digest:digestText(body),rows:value.rows.length,cursor:value.next_after,watermark:value.watermark});
    }
    return evidence;
  });
  return {evidence,milliseconds:performance.now()-start};
}

const reads=[
  ['status',null,null,1,null],['status',null,'1',1,'80000'],
  ['receipt',id(2),null,10,null],['receipts',null,'28000',500,null],['receipts',null,null,500,'2000'],
  ['observations',null,'61000',500,null],['observation_history',id(1),null,2,null],
  ['observations',null,'110020',500,null],['revisions',null,'110021',500,null],
  ['resolution_history',id(1),null,2,null],['resolution_history',id(1),null,2,'95000'],
  ['revisions',null,'81000',500,null],['latest_resolutions',null,null,500,'95000'],
  ['latest_resolutions',null,null,500,null],['latest_resolutions',null,'110010',10,null],
];

test('shared status and legacy clamp retain exact bytes across a scaled correction ledger',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    const empty=await raw(db,['status',null,null,1,null]);
    await db.exec(await load('./scoped_history_paging_fixture.sql'));
    await db.exec(`alter table selflearn_private.journal_observations disable trigger user;
      insert into selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json,journal_seq,stored_utc)
      values('${id(330001)}','${id(1)}','${id(340002)}','{"raw_float":82.0}',120021,'2026-11-01T00:05:00.067890Z');
      alter table selflearn_private.journal_observations enable trigger user;`);
    const readDefinition=async()=>(await db.query("select pg_get_functiondef('public.selflearn_journal_read_v1(text,text,text,integer,text)'::regprocedure) value")).rows[0].value;
    const oldRead=await readDefinition();
    const before=await measure(db,reads);
    assert.equal(before.evidence[0].watermark,'120021');
    assert.equal(before.evidence[7].rows,1);assert.equal(before.evidence[8].rows,0);
    await db.exec(await load(migration));
    const after=await measure(db,reads);
    assert.deepEqual(after.evidence,before.evidence);
    assert.equal(await readDefinition(),oldRead,'direct legacy RPC body unchanged');
    assert.equal(await raw(db,['status',null,null,1,'0']),empty);
    assert.equal((await db.query('select selflearn_private.observed_watermark_v1()::text value')).rows[0].value,'120021');
    for(const role of ['anon','authenticated'])await assert.rejects(asRole(db,role,()=>raw(db,['status',null,null,1,null])),e=>e.code==='42501');
    for(const [args,msg] of [
      [['status',null,'120022',1,null],'cursor_after_watermark'],[['status',null,null,1,'120022'],'invalid_read_watermark'],
      [['observation_history',null,null,1,null],'journal_key_required'],[['generations',null,null,2,null],'generation_page_limit_is_one'],
    ])await assert.rejects(asRole(db,'service_role',()=>raw(db,args)),e=>e.code==='PT422'&&e.message===msg);
    console.log('SHARED_JOURNAL_SQL_SCALE '+JSON.stringify({ledger_receipts:28736,selected_pages:reads.length,before_ms:before.milliseconds,after_ms:after.milliseconds}));
  } finally {await db.close();}
});

test('shared exact object and single-generation pages preserve oversize first rows and original ACK bytes',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    await db.exec(await load('./scoped_journal_paging_fixture.sql'));
    const cases=[['object',id(100),null,1,null],['object',id(124),null,1,null],['generation_ack',id(100),null,1,null],
      ['generations',null,null,1,null],['generations',null,'220',1,null],['generations',null,'225',1,null],
      ['generations',null,'226',1,null],['generations',null,'219',1,'220']];
    const before=await measure(db,cases);
    assert.equal(before.evidence[1].rows,1);assert.equal(before.evidence[5].rows,1);
    assert.equal(before.evidence[6].rows,0);assert.equal(before.evidence[7].cursor,null);
    await db.exec(await load(migration));
    const after=await measure(db,cases);
    assert.deepEqual(after.evidence,before.evidence);
  } finally {await db.close();}
});
