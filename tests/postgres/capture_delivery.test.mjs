import assert from 'node:assert/strict';
import { test } from 'node:test';
import { openCaptureDeliveryDatabase } from './capture_delivery_harness.mjs';
import { canonical,digest,receipt,serviceRpc,rpc,asRole,appendReceipt,appendGeneration,commitGeneration,rejectsCode } from './harness.mjs';

const BASIS='nfl.observed_resolution_v2';
const emptyBatch={version:1,observations:[],revisions:[]};
const memberKeys=['scope','receipt_ids','source_receipt_ids','generation_object_ids'];
const fence=async db=>(await serviceRpc(db,'selflearn_journal_read_observed_v1',['status',null,null,1,null])).watermark;
const deliver=(db,r,b,batch=emptyBatch,watermark='0')=>serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',
  [canonical(r),canonical(b),canonical(batch),watermark]);
const get=(db,id,after=null,limit=100)=>serviceRpc(db,'selflearn_journal_read_capture_delivery_v1',[id,after,limit]);

function capture(tag='first') {
  const observations={},sources=[],raw_objects=[],raw_pointers=[];
  const profiles=['espn_scoreboard_v1','nflverse_stats_player_v1','nflverse_snap_counts_v1'];
  for (const [index,profile] of profiles.entries()) {
    const raw_sha256=digest({raw:profile,tag});
    const week=index===0?6:null;
    const source_url=index===0?'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&dates=2026&week=6':
      index===1?'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv':
        'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2026.csv';
    const observation={version:1,kind:'nfl.outcome_source_observation.v1',evidence_class:'observed_source_snapshot',profile,
      season:2026,week,source_url,raw_sha256,raw_bytes:1,fetch_started_utc:`2026-09-03T0${index+1}:00:00Z`,
      observed_utc:`2026-09-03T0${index+1}:01:00Z`,upstream_updated_utc:null,http:{status:200},audit:{fixture:true}};
    const observation_id=digest(observation);observations[observation_id]=observation;
    sources.push({profile,season:2026,week,observation_id});raw_objects.push({raw_sha256,raw_bytes:1});
    raw_pointers.push({version:1,kind:'selflearn.evidence_object.v1',scope:'uploaded_only',object_id:raw_sha256,raw_bytes:1,
      chunks:[{chunk_id:raw_sha256,raw_bytes:1}]});
  }
  raw_objects.sort((a,b)=>a.raw_sha256.localeCompare(b.raw_sha256));raw_pointers.sort((a,b)=>a.object_id.localeCompare(b.object_id));
  const manifest={version:1,kind:'nfl.outcome_capture.v1',season:2026,weeks:[6],started_utc:'2026-09-03T00:00:00Z',
    completed_utc:'2026-09-03T04:00:00Z',sources,clock_policy:'producer observations only; upstream issue and exact game-finish times unknown',
    publication_status:'source_capture_not_public_forecast_evidence',audit:{fixture:true}};
  manifest.capture_id=digest(manifest);
  const envelope={version:1,kind:'nfl.capture_envelope.v1',scope:'uploaded_only',manifest,observations,raw_objects};
  const identity={version:1,kind:'nfl.capture_delivery.v1',destination_id:digest('destination'),producer_id:'sql-fixture',
    capture_id:manifest.capture_id,binding_policy:'nfl.capture_labels_v1'};
  return {...identity,kind:'nfl.capture_delivery_request.v1',envelope,raw_pointers,delivery_id:digest(identity)};
}

function binding(groups=[]) {
  return {version:1,kind:'nfl.capture_delivery_binding.v1',season:2026,weeks:[6],
    membership_id:digest({binding_policy:'nfl.capture_labels_v1',groups:groups.map(g=>g.membership_id)}),groups};
}

async function member(db,name,line=40) {
  const key=[2026,'G1',name,'wr_rec_yds',String(line),'home','over','gte'];
  const generic=receipt(name+line,{event_id:'nfl:2026:game:G1',correlation_group:'nfl:2026:game:G1',contract_id:digest(key)});
  const source={version:1,season:2026,week:6,game_id:'G1',player_id:name,market:'wr_rec_yds',line,side:'home',
    direction:'over',comparison_operator:'gte',contract_id:generic.contract_id};
  for (const k of ['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc','source_snapshot_id','source_timestamps','features','publication_status']) source[k]=generic[k];
  source.receipt_id=digest(source);
  generic.source_receipt_id=source.receipt_id;delete generic.receipt_id;generic.receipt_id=digest(generic);
  const document={version:1,season:2026,week:6,receipts:[{receipt_id:source.receipt_id,learning_receipt:source}],generations:[]};
  document.envelope_id=digest(document);
  await appendGeneration(db,document);await commitGeneration(db,document.envelope_id,[generic],[]);
  return {key,generic,source,document};
}

function proof(member,request,actual=80) {
  const source_observations={};
  for (const [source,profile] of [['espn.scoreboard','espn_scoreboard_v1'],['nflverse.stats_player','nflverse_stats_player_v1']]) {
    const ref=request.envelope.manifest.sources.find(s=>s.profile===profile);
    source_observations[source]={source,observation_id:ref.observation_id,observed_utc:request.envelope.observations[ref.observation_id].observed_utc};
  }
  const measurement={version:1,canonical_contract:member.key,week:6,actual,played:true,status:'FINAL',hit:actual>=Number(member.key[4]),
    reason:null,participation_basis:'stat_row',game:{season:2026,week:6,game_id:'G1',kickoff_utc:member.generic.event_start_utc,home:'SEA',away:'LAR',state:'FINAL'}};
  const measurement_id=digest(measurement);
  const observation={version:1,kind:'nfl.observed_measurement.v1',evidence_basis:BASIS,receipt_id:member.generic.receipt_id,
    contract_id:member.generic.contract_id,event_id:member.generic.event_id,measurement,measurement_id,source_observations,
    evidence_observation_id:digest({measurement_id,components:source_observations}),observed_utc:source_observations['nflverse.stats_player'].observed_utc};
  observation.observation_id=digest(observation);
  const resolution={receipt_id:observation.receipt_id,contract_id:observation.contract_id,event_id:observation.event_id,evidence_basis:BASIS,
    measurement_id,measurement,evidence_observation_id:observation.evidence_observation_id,source_observations,observation_watermarks:source_observations,
    resolved_utc:observation.observed_utc,evidence_observed_utc:observation.observed_utc,status:'FINAL',hit:measurement.hit,actual,reason:null,
    participation_basis:'stat_row',source:'nflverse.stats_player',authoritative:true,final_evidence_status:'FINAL'};
  const revision={version:1,revision_id:BASIS+':'+digest({receipt_id:observation.receipt_id,observation_id:observation.evidence_observation_id,supersedes:null}),
    supersedes:null,reason:null,resolution};
  return {observation,revision};
}

function group(members,proofs=[],held=false) {
  const scope=[2026,'G1',members[0].key[2],'wr_rec_yds','home'];
  const value={scope,receipt_ids:members.map(m=>m.generic.receipt_id).sort(),source_receipt_ids:members.map(m=>m.source.receipt_id).sort(),
    generation_object_ids:members.map(m=>m.document.envelope_id).sort()};
  return {group_id:digest(scope),...value,membership_id:digest({binding_policy:'nfl.capture_labels_v1',...value}),
    status:held?'withheld':'accepted',reason_codes:held?['fixture_withheld']:[],
    observation_ids:held?[]:proofs.map(p=>p.observation.observation_id).sort(),revision_ids:held?[]:proofs.map(p=>p.revision.revision_id).sort()};
}

function databaseTest(name,run) {test(name,async()=>{const db=await openCaptureDeliveryDatabase();try{await run(db);}finally{await db.close();}});}

databaseTest('delivery schema is private RLS/invoker, computed columns cannot be forged',async db=>{
  const table=(await db.query("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='selflearn_private.journal_capture_deliveries'::regclass")).rows[0];
  assert.deepEqual(table,{relrowsecurity:true,relforcerowsecurity:true});
  for(const role of ['anon','authenticated']) {
    await rejectsCode(()=>asRole(db,role,()=>rpc(db,'selflearn_journal_read_capture_delivery_v1',[digest('x'),null,1])),'42501');
    await rejectsCode(()=>asRole(db,role,()=>rpc(db,'selflearn_journal_commit_capture_delivery_v1',['{}','{}','{}','0'])),'42501');
    await rejectsCode(()=>asRole(db,role,()=>db.query('SELECT * FROM selflearn_private.journal_capture_deliveries')),'42501');
  }
  const r=capture();
  await rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.journal_capture_deliveries(request_json,binding_json,batch_json,expected_watermark,result_json) VALUES($1,$2,$3,$4,$5)',
    [canonical(r),canonical(binding()),canonical(emptyBatch),'0','{"durable":true}'])),'42501');
  const functions=(await db.query("SELECT prosecdef FROM pg_proc WHERE proname LIKE '%capture_delivery%' AND pronamespace='public'::regnamespace")).rows;
  assert.ok(functions.length===2&&functions.every(f=>!f.prosecdef));
});

databaseTest('ledger-only result has server clock/separate seq, exact original retry before CAS',async db=>{
  const request=capture();const result=await deliver(db,request,binding());
  assert.equal(result.status,'no_supported_membership');assert.equal(result.durable,true);
  assert.equal(result.input_watermark,'0');assert.equal(result.watermark,'0');assert.equal(await fence(db),'0');
  assert.equal(result.delivery_seq,'1');assert.ok(Date.parse(result.recorded_utc)>Date.parse(request.envelope.manifest.completed_utc));
  assert.deepEqual(await serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(request),'not current schema','not a batch','999']),result);
  const stored=await get(db,request.delivery_id);const {groups,...summary}=result;assert.deepEqual(stored.result,summary);
});

databaseTest('direct input-only INSERT executes same atomic invariant and immutable guards',async db=>{
  const request=capture();
  const row=await asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.journal_capture_deliveries(request_json,binding_json,batch_json,expected_watermark) VALUES($1,$2,$3,$4) RETURNING result_json',
    [canonical(request),canonical(binding()),canonical(emptyBatch),'0']));
  assert.equal(JSON.parse(row.rows[0].result_json).delivery_id,request.delivery_id);
  for(const sql of ['UPDATE selflearn_private.journal_capture_deliveries SET request_json=request_json','DELETE FROM selflearn_private.journal_capture_deliveries','TRUNCATE selflearn_private.journal_capture_deliveries'])
    await rejectsCode(()=>db.exec(sql),'PT409');
  await rejectsCode(()=>asRole(db,'service_role',()=>db.query('UPDATE selflearn_private.journal_capture_deliveries SET recorded_utc=now()')),'42501');
});

databaseTest('independent accepted/withheld groups persist exhaustive results and strict pagination',async db=>{
  const a=await member(db,'player-a'),b=await member(db,'player-b');const request=capture();const p=proof(a,request);
  const groups=[group([a],[p]),group([b],[],true)];const before=await fence(db);
  const result=await deliver(db,request,binding(groups),{version:1,observations:[p.observation],revisions:[p.revision]},before);
  assert.equal(result.status,'partial');assert.equal(result.added_observations,1);assert.equal(result.added_revisions,1);
  const first=await get(db,request.delivery_id,null,1),second=await get(db,request.delivery_id,first.next_after,1);
  assert.equal(first.total_groups,2);assert.equal(first.next_after,'1');assert.equal(second.next_after,null);
  assert.deepEqual([...first.groups,...second.groups],groups);assert.deepEqual(first.result,second.result);
  for(const args of [[request.delivery_id,'00',1],[request.delivery_id,'3',1],[request.delivery_id,null,101]])
    await rejectsCode(()=>serviceRpc(db,'selflearn_journal_read_capture_delivery_v1',args),'PT422');
});

databaseTest('missing rung/vintage, cross-capture proof and contradiction all fail closed',async db=>{
  const a=await member(db,'same-player',40),b=await member(db,'same-player',60);const request=capture();const pa=proof(a,request),pb=proof(b,request,30);
  const before=await fence(db);
  await rejectsCode(()=>deliver(db,request,binding([group([a],[pa])]),{version:1,observations:[pa.observation],revisions:[pa.revision]},before),'PT422');
  await rejectsCode(()=>deliver(db,request,binding([group([a,b],[pa,pb])]),{version:1,observations:[pa.observation,pb.observation],revisions:[pa.revision,pb.revision]},before),'PT422');
  const other=capture('other'),po=proof(a,other);
  await rejectsCode(()=>deliver(db,request,binding([group([a,b],[po])]),{version:1,observations:[po.observation],revisions:[po.revision]},before),'PT422');
  assert.equal((await get(db,request.delivery_id)).result,null);assert.equal(await fence(db),before);
});

databaseTest('unlocatable accepted NFL authority is never silently skipped',async db=>{
  await appendReceipt(db,receipt('orphan',{event_id:'nfl:2026:game:G1'}));
  const request=capture();const before=await fence(db);
  await rejectsCode(()=>deliver(db,request,binding(),emptyBatch,before),'PT422');
  assert.equal((await get(db,request.delivery_id)).result,null);
});

databaseTest('typed intent, pointer layouts, source closure, bounds and request mutation reject',async db=>{
  const request=capture();
  for(const mutate of [r=>{r.version=true;},r=>{r.raw_pointers[0].chunks[0].raw_bytes=2;},
    r=>{r.raw_pointers.push(structuredClone(r.raw_pointers[0]));},r=>{r.envelope.observations[digest('orphan')]={};},
    r=>{r.raw_pointers[0].url='https://evil.invalid';}]) {
    const bad=structuredClone(request);mutate(bad);await rejectsCode(()=>deliver(db,bad,binding()),'PT422');
  }
  await rejectsCode(()=>deliver(db,request,binding(),{version:1,observations:Array(501).fill({}),revisions:[]}),'PT422');
  await assert.rejects(()=>deliver(db,request,binding(),{version:1,observations:Array(500).fill({}),revisions:[]}),
    error=>error.code==='PT422'&&error.message==='incomplete_capture_population_partition');
  await rejectsCode(()=>serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',['x'.repeat(3*1024*1024+1),'{}','{}','0']),'PT422');
  await assert.rejects(()=>serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(request),'x'.repeat(8*1024*1024),'{}','0']),
    error=>error.code==='PT422'&&error.message==='capture_delivery_rpc_exceeds_byte_limit');
  await deliver(db,request,binding());const changed=structuredClone(request);changed.raw_pointers[0].chunks[0].chunk_id=digest('changed');
  await rejectsCode(()=>deliver(db,changed,binding()),'PT409');
});
