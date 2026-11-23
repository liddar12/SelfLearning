import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {performance} from 'node:perf_hooks';
import {canonical,digest,asRole,appendGeneration,commitGeneration} from './harness.mjs';
import {openGameCaptureApplicationDatabase as openCaptureApplicationDatabase,openCaptureApplicationDatabase as openLegacyDatabase,addMembers,captureRequest,applicationRequest,serviceV2,
        memberProof,commonFact,stepPayload,commitStep,readApplication} from './capture_application_harness.mjs';


test('28,736-row ledger backfill and reads stay scoped before work limits and network pages',async()=>{
  const db=await openLegacyDatabase();
  try {
    // Administrative load fixture only: 28,232 unrelated rows with large
    // generation documents stress lookup/backfill. These are not claimed as
    // authentic producer evidence. The selected 504 receipts use real RPCs.
    await db.exec(String.raw`
      alter table selflearn_private.journal_receipts disable trigger journal_stamp;
      alter table selflearn_private.journal_objects disable trigger journal_stamp;
      alter table selflearn_private.journal_generation_commits disable trigger journal_stamp;
      insert into selflearn_private.journal_receipts(receipt_id,domain,task,source_receipt_id,payload_json,journal_seq,stored_utc)
        select 'sha256:'||lpad(to_hex(i),64,'0'),'nfl','nfl.parlay_leg','sha256:'||lpad(to_hex(i+50000),64,'0'),
          json_build_object('event_id','nfl:2026:game:UNRELATED','source_receipt_id','sha256:'||lpad(to_hex(i+50000),64,'0'))::text,
          i,clock_timestamp() from generate_series(1,28232) i;
      insert into selflearn_private.journal_objects(object_id,kind,payload_json,journal_seq,stored_utc)
        select 'sha256:'||lpad(to_hex(i+100000),64,'0'),'generation',
          json_build_object('version',1,'week',6,'padding',repeat('x',1048576))::text,28232+i,clock_timestamp()
          from generate_series(1,29) i;
      insert into selflearn_private.journal_generation_commits(object_id,receipts_json,rejections_json,ack_json,journal_seq,stored_utc)
        select 'sha256:'||lpad(to_hex(((i-1)/1000)+100001),64,'0'),'[]','[]',
          json_build_object('receipts',json_agg(json_build_object('receipt_id','sha256:'||lpad(to_hex(i),64,'0'),
            'source_receipt_id','sha256:'||lpad(to_hex(i+50000),64,'0'))))::text,
          28300+((i-1)/1000),clock_timestamp() from generate_series(1,28232) i group by (i-1)/1000;
      select setval('selflearn_private.journal_sequence',28400);
      alter table selflearn_private.journal_receipts enable trigger journal_stamp;
      alter table selflearn_private.journal_objects enable trigger journal_stamp;
      alter table selflearn_private.journal_generation_commits enable trigger journal_stamp;
    `);
    for(let i=0;i<3;i++)await addMembers(db,168,{vintage:'selected-'+i,player:n=>'p'+(n%28)});
    const began=performance.now();
    await db.exec(await readFile(new URL('../../supabase/migrations/20261118192717_nfl_roster_capture_provenance.sql',import.meta.url),'utf8'));
    await db.exec(await readFile(new URL('../../supabase/migrations/20261119195500_journal_ascii_string_fast_path.sql',import.meta.url),'utf8'));
    await db.exec(await readFile(new URL('../../supabase/migrations/20261123184027_nfl_game_scoped_capture_journal_v1.sql',import.meta.url),'utf8'));
    const backfill_ms=performance.now()-began;
    assert.equal(Number((await db.query('select count(*) n from selflearn_private.capture_game_generation_members_v1')).rows[0].n),28736);
    await db.exec('analyze selflearn_private.journal_receipts; analyze selflearn_private.capture_game_generation_members_v1;');
    const plan=await db.query("explain (format json) select * from selflearn_private.journal_receipts where domain='nfl' and task='nfl.parlay_leg' and payload_json::jsonb->>'event_id'='nfl:2026:game:G1'");
    assert.match(JSON.stringify(plan.rows),/journal_nfl_event_sequence_v1/);
    let started=performance.now(),page=await gameRead(db,'G1','receipts'),receipt_rpc_count=1;
    const receipts=[...page.rows],watermark=page.watermark;
    while(page.next_after){page=await gameRead(db,'G1','receipts',page.next_after,500,watermark);receipts.push(...page.rows);receipt_rpc_count++;}
    const receipt_read_ms=performance.now()-started;
    assert.equal(receipts.length,504);assert.equal(receipt_rpc_count,2);
    started=performance.now();
    const generations=await gameRead(db,'G1','game_generations',null,500,watermark);
    const generation_read_ms=performance.now()-started;
    assert.equal(generations.rows.length,3);assert.equal(generations.next_after,null,'complete originals pack into one bounded page');
    await assert.rejects(serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']),e=>e.code==='PT422'&&e.message==='capture_membership_exceeds_work_limit');
    started=performance.now();
    const scoped=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope('G1'))]);
    const cold_prepare_ms=performance.now()-started;
    assert.equal(scoped.member_count,504);assert.equal(scoped.group_count,28);
    console.log('GAME_SCOPED_SQL_SCALE '+JSON.stringify({scope:'synthetic_local_postgresql_wasm_not_hosted_latency',
      total_receipts:28736,selected_receipts:504,backfill_ms,receipt_read_ms,receipt_rpc_count,generation_read_ms,generation_rpc_count:1,cold_prepare_ms}));
  } finally {await db.close();}
});


test('scoped grouped/source builders preserve v2 bytes and all duplicate original-generation proofs',async()=>{
  const db=await openCaptureApplicationDatabase();
  try {
    const members=await addMembers(db,12,{player:i=>'p'+(i%3)});
    const original=members[0].document,duplicate={...original,fixture:'second immutable envelope'};
    delete duplicate.envelope_id;duplicate.envelope_id=digest(duplicate);
    await appendGeneration(db,duplicate);
    await commitGeneration(db,duplicate.envelope_id,members.map(m=>m.generic),[]);
    const map=(await db.query('select selflearn_private.capture_game_members_v1($1::json) value',[canonical(scope('G1'))])).rows[0].value;
    assert.ok(Object.values(map).every(m=>m.generation_object_ids.length===2));
    const result=(await db.query(String.raw`
      select selflearn_private.capture_game_groups_v1($1::jsonb)::text fast_groups,
        selflearn_private.capture_groups_v2($1::jsonb)::text old_groups,
        selflearn_private.capture_game_source_index_v1($1::jsonb)::text fast_sources,
        selflearn_private.capture_source_index_v2($1::jsonb)::text old_sources
    `,[canonical(map)])).rows[0];
    assert.deepEqual(JSON.parse(result.fast_groups),JSON.parse(result.old_groups));
    assert.equal(result.fast_sources,result.old_sources,'original source JSON stays exact inside wrappers');
    const cached=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope('G1'))]);
    assert.equal(cached.member_count,12);assert.equal(cached.group_count,3);
  } finally {await db.close();}
});

const scope=game_id=>({policy:'nfl.capture_game_scope_v1',season:2026,week:6,game_id});
const gameRead=(db,game,view,after=null,limit=500,watermark=null)=>serviceV2(db,'selflearn_journal_read_observed_game_v1',
  [view,canonical(scope(game)),after,limit,watermark]);
async function prepare(db,game,{facts={}}={}) {
  const game_scope=scope(game),capture=captureRequest(),request=applicationRequest(capture);
  request.game_scope=game_scope;
  request.application_id=digest({version:2,kind:'nfl.capture_application.v2',capture_intent_id:capture.delivery_id,
    execution_policy:request.execution_policy,game_scope});
  const cert=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(game_scope)]);
  const {groups}= (await db.query('select selflearn_private.capture_groups_v2(selflearn_private.capture_game_members_v1($1::json)) as groups',[canonical(game_scope)])).rows[0];
  const batches=[];let active=null;
  for(const g of groups) {
    const fact=facts[g.scope[2]];
    if(!fact)Object.assign(g,{status:'withheld',reason_codes:['fixture_unverified'],batch_ordinal:null,fact:null,fact_id:null});
    else {
      if(!active||active.member_count+g.receipt_ids.length>500) {
        const ordinal=batches.length; active={ordinal,step_id:digest({version:2,kind:'nfl.capture_application_step.v2',application_id:request.application_id,ordinal}),group_ids:[],member_count:0};batches.push(active);
      }
      Object.assign(g,{status:'planned',reason_codes:[],batch_ordinal:active.ordinal,fact,fact_id:digest(fact)});
      active.group_ids.push(g.group_id);active.member_count+=g.receipt_ids.length;
    }
  }
  const source_watermark=(await db.query('select selflearn_private.observed_watermark_v1()::text value')).rows[0].value;
  const source_vector_id=(await db.query('select selflearn_private.capture_source_vector_v3($1::json,$2::json) value',[canonical(capture),canonical(game_scope)])).rows[0].value;
  const seal={version:2,kind:'nfl.capture_application_seal.v2',application_id:request.application_id,request_id:digest(request),
    capture_intent_id:digest({capture_intent_id:capture.delivery_id,game_scope}),capture_id:capture.capture_id,execution_policy:request.execution_policy,
    binding_policy:capture.binding_policy,season:2026,weeks:[6],source_watermark,membership_id:cert.membership_id,source_vector_id,
    limits:{members:500,observations:500,revisions:500,rpc_bytes:8388608},groups,batches,game_scope};
  seal.seal_id=digest(seal);return {request,seal,cert};
}
const register=(db,{request,seal},watermark=seal.source_watermark)=>serviceV2(db,'selflearn_journal_register_capture_application_v3',
  [canonical(request),canonical(seal),watermark]);

test('game-scoped original registration, indexed reads, immutable membership, and cross-game source reuse',async()=>{
  const db=await openCaptureApplicationDatabase();
  try {
    const first=await addMembers(db,3,{player:'selected'});
    await addMembers(db,4,{gameId:'G2',vintage:'other'});
    const page=await gameRead(db,'G1','receipts',null,2);
    assert.equal(page.rows.length,2);assert.ok(page.next_after);
    const final=await gameRead(db,'G1','receipts',page.next_after,2,page.watermark);
    assert.equal(final.rows.length,1);assert.equal(final.next_after,null);
    assert.ok([...page.rows,...final.rows].every(r=>JSON.parse(r.payload_json).event_id==='nfl:2026:game:G1'));
    const gens=await gameRead(db,'G1','game_generations',null,500,page.watermark);
    assert.equal(gens.rows.length,1);assert.equal(JSON.parse(gens.rows[0].payload_json).envelope_id,first[0].document.envelope_id);
    const selected=await prepare(db,'G1'),other=await prepare(db,'G2');
    assert.equal(selected.cert.member_count,3);assert.equal(other.cert.member_count,4);
    const a=await register(db,selected),b=await register(db,other);
    assert.notEqual(a.application_id,b.application_id);assert.notEqual(a.source_vector_id,b.source_vector_id);
    assert.deepEqual(await register(db,selected,'0'),a);
    const stored=(await db.query('select game_id,game_scope_json from selflearn_private.capture_applications_v2 order by game_id')).rows;
    assert.deepEqual(stored.map(r=>r.game_id),['G1','G2']);
    assert.equal(stored[0].game_scope_json,canonical(scope('G1')));
    await addMembers(db,1,{gameId:'G2',vintage:'later-other'});
    const again=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope('G1'))]);
    assert.equal(again.membership_id,selected.cert.membership_id);
    assert.equal(again.cache_id,selected.cert.cache_id,'unrelated game does not invalidate selected cache');
    await assert.rejects(asRole(db,'service_role',()=>db.query('insert into selflearn_private.capture_game_generation_members_v1(object_id,receipt_id) values($1,$2)',[first[0].document.envelope_id,first[0].generic.receipt_id])),e=>e.code==='42501');
    for(const role of ['anon','authenticated'])await assert.rejects(asRole(db,role,()=>db.query('select public.selflearn_journal_read_observed_game_v1($1,$2,null,10,null)',['receipts',canonical(scope('G1'))])),e=>e.code==='42501');
  } finally {await db.close();}
});

test('scoped membership CAS notices selected-game additions and leaves other games untouched',async()=>{
  const db=await openCaptureApplicationDatabase();
  try {
    const [member]=await addMembers(db,1,{player:'selected'});
    const proof=memberProof(member,captureRequest());
    const app=await prepare(db,'G1',{facts:{selected:commonFact(proof)}});
    await register(db,app);
    await addMembers(db,1,{gameId:'G2',vintage:'unrelated'});
    let watermark=(await gameRead(db,'G1','receipts')).watermark;
    await assert.rejects(commitStep(db,app.request,app.seal,0,stepPayload(app.seal,0,[proof]),app.seal.source_watermark),e=>e.code==='PT409');
    const result=await commitStep(db,app.request,app.seal,0,stepPayload(app.seal,0,[proof]),watermark);
    assert.equal(result.status,'committed');assert.equal(result.added_revisions,1);
    assert.equal((await gameRead(db,'G2','revisions')).rows.length,0);
  } finally {await db.close();}
  const changed=await openCaptureApplicationDatabase();
  try {
    const [member]=await addMembers(changed,1,{player:'selected'});
    const proof=memberProof(member,captureRequest()),app=await prepare(changed,'G1',{facts:{selected:commonFact(proof)}});
    await register(changed,app);await addMembers(changed,1,{vintage:'late-selected',player:'new'});
    const result=await commitStep(changed,app.request,app.seal,0,stepPayload(app.seal,0,[proof]),app.seal.source_watermark);
    assert.equal(result.status,'not_applied');assert.equal(result.added_revisions,0);
    assert.deepEqual(result.groups[0].reason_codes,['application_membership_changed']);
    assert.equal((await readApplication(changed,app.request.application_id,'terminal')).records[0].status,'incomplete_membership_changed');
  } finally {await changed.close();}
});
