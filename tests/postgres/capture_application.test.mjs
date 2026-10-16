import assert from 'node:assert/strict';
import { test } from 'node:test';
import { openCaptureApplicationDatabase, addMembers, memberMap, applicationRequest, sealApplication, registerApplication, serviceV2,
  memberProof,commonFact,stepPayload,commitStep,readApplication,captureRequest,rpcV2,moveCaptureDay,resealCapture } from './capture_application_harness.mjs';
import { appendReceipt, receipt, asRole, rejectsCode,canonical,digest,digestText,serviceRpc,appendObject,
  appendRevision,revision,read,appendGeneration,commitGeneration,generation } from './harness.mjs';

function databaseTest(name, run) {
  test(name, async () => {
    const db = await openCaptureApplicationDatabase();
    try { await run(db); } finally { await db.close(); }
  });
}

databaseTest('v2 additive membership leaves every existing function body unchanged', async db => {
  for (const row of db.originalV1Functions) {
    const current = (await db.query('SELECT pg_get_functiondef($1::regprocedure) AS definition', [row.signature])).rows[0].definition;
    assert.equal(current, row.definition, row.signature);
  }
  const definitions = (await db.query("SELECT proname,prosecdef,proconfig FROM pg_proc WHERE proname IN ('capture_members_v1','capture_members_v2') ORDER BY proname")).rows;
  assert.equal(definitions.length, 2);
  assert.ok(definitions.every(row => !row.prosecdef && row.proconfig.includes('search_path=""')));
});

databaseTest('v2 equals v1 complete accepted membership for rungs, vintages and rejected neighbors', async db => {
  await addMembers(db, 4, { player: 'same-player', line: i => 40 + i, vintage: 'first' });
  await addMembers(db, 4, { player: 'same-player', line: i => 40 + i, vintage: 'second' });
  await addMembers(db, 2, { vintage: 'rejected', accepted: false });
  const first = await memberMap(db, 1), second = await memberMap(db, 2);
  assert.deepEqual(second, first);
  assert.equal(Object.keys(second).length, 8);
  assert.deepEqual(await memberMap(db, 2, [7]), {});
  assert.ok(Object.values(second).every(row => row.scope[2] === 'same-player'));
});

databaseTest('v2 admits 501 full members while unchanged v1 keeps its 500 limit', async db => {
  await addMembers(db, 501);
  await assert.rejects(() => memberMap(db, 1), error => error.code === 'PT422' && error.message === 'complete_capture_exceeds_member_limit');
  assert.equal(Object.keys(await memberMap(db, 2)).length, 501);
});

databaseTest('both membership versions fail the same malformed/unlocatable original authority', async db => {
  await addMembers(db, 1, { sourceExtra: { player_id: null } });
  for (const version of [1, 2]) {
    await assert.rejects(() => memberMap(db, version), error => error.code === 'PT422' && error.message === 'unlocatable_original_capture_member');
  }
});

databaseTest('both membership versions reject orphan forecasts rather than silently truncate', async db => {
  await appendReceipt(db, receipt('orphan', { event_id: 'nfl:2026:game:G1' }));
  for (const version of [1, 2]) {
    await assert.rejects(() => memberMap(db, version), error => error.code === 'PT422' && error.message === 'unlocatable_missing_capture_generation');
  }
});

databaseTest('new membership helper is invoker-only and unavailable to anonymous roles', async db => {
  for (const role of ['anon', 'authenticated'])
    await rejectsCode(() => asRole(db, role, () => memberMap(db, 2)), '42501');
  assert.deepEqual(await asRole(db, 'service_role', () => memberMap(db, 2)), {});
});

databaseTest('expanded database preserves old receipt/object/generation/outcome replay and reads',async db=>{
  const value=receipt('legacy-cross-version');
  assert.deepEqual(await appendReceipt(db,value),{version:1,added:true});
  assert.deepEqual(await appendReceipt(db,value),{version:1,added:false});
  const outcome=revision(value,'legacy-outcome');
  assert.deepEqual(await appendRevision(db,outcome),{version:1,added:true});
  assert.deepEqual(await appendRevision(db,outcome),{version:1,added:false});
  assert.equal((await read(db,'receipt',value.receipt_id)).rows[0].payload_json,canonical(value));
  const source={frozen:'legacy-source'};assert.deepEqual(await appendObject(db,'source_snapshot',digest(source),source),{version:1,added:true});
  const second=receipt('legacy-generation'),doc=generation('old-generic',[second.source_receipt_id]);
  await appendGeneration(db,doc);const ack=await commitGeneration(db,doc.envelope_id,[second],[]);
  assert.equal(ack.durable,true);assert.equal(ack.receipts[0].receipt_id,second.receipt_id);
  const replay=await commitGeneration(db,doc.envelope_id,[second],[]);assert.equal(replay.receipts[0].status,'duplicate');
  assert.deepEqual(JSON.parse((await read(db,'generation_ack',doc.envelope_id)).rows[0].ack_json),ack);
});

databaseTest('registration seals full membership with DB metadata and immutable original recovery',async db=>{
  await addMembers(db,4,{player:'same-player',line:i=>40+i});
  const request=applicationRequest(),seal=await sealApplication(db,request);
  const ack=await registerApplication(db,request,seal);
  assert.equal(ack.status,'registered');assert.equal(ack.member_count,4);assert.equal(ack.group_count,1);assert.equal(ack.batch_count,0);
  assert.equal(ack.next_step_id,null);assert.equal(ack.learning_accepted,false);assert.equal(ack.source_authentication,'not_performed');
  const {registration_id,...unsigned}=ack;assert.equal(registration_id,digest(unsigned));
  assert.deepEqual(await serviceV2(db,'selflearn_journal_register_capture_application_v2',
    [canonical(request),'not a current seal','999']),ack);
  assert.equal((await db.query('SELECT selflearn_private.observed_watermark_v1()::text AS value')).rows[0].value,seal.source_watermark);
});

databaseTest('planned whole group writes matching facts and exact original batch result',async db=>{
  const members=await addMembers(db,4,{player:'same-player',line:i=>40+i});
  const request=applicationRequest(),proofs=members.map(m=>memberProof(m,request.capture_request));
  const seal=await sealApplication(db,request,{facts:{'same-player':commonFact(proofs[0])}});
  await registerApplication(db,request,seal);
  const payload=stepPayload(seal,0,proofs),ack=await commitStep(db,request,seal,0,payload,seal.source_watermark);
  assert.equal(ack.status,'committed');assert.equal(ack.added_observations,4);assert.equal(ack.added_revisions,4);
  const {batch_result_id,...unsigned}=ack;assert.equal(batch_result_id,digest(unsigned));
  assert.deepEqual(await serviceV2(db,'selflearn_journal_commit_capture_application_batch_v2',
    [request.application_id,seal.batches[0].step_id,'not groups','not batch','999']),ack);
  const terminal=(await readApplication(db,request.application_id,'terminal')).records[0];
  assert.equal(terminal.status,'accepted');assert.equal(terminal.complete_success,true);assert.equal(terminal.counts.accepted_members,4);
  assert.equal('terminal_result_id' in ack,false);
});

async function planned(db,count=3) {
  const members=await addMembers(db,count),request=applicationRequest();
  const proofs=members.map(m=>memberProof(m,request.capture_request));
  const facts=Object.fromEntries(proofs.map((p,i)=>[members[i].key[2],commonFact(p)]));
  const seal=await sealApplication(db,request,{facts,maximum:2});
  const ack=await registerApplication(db,request,seal);return{members,request,proofs,seal,ack};
}
const fence=async db=>(await db.query('SELECT selflearn_private.observed_watermark_v1()::text AS value')).rows[0].value;

databaseTest('pinned pages retain pending state; last batch atomically seals complete terminal report',async db=>{
  const {request,seal,proofs,ack}=await planned(db);
  const registered=await readApplication(db,request.application_id,'registration');
  assert.deepEqual(registered.records[0].request,request);assert.deepEqual(registered.records[0].registration,ack);
  const {groups,batches,...header}=seal;assert.deepEqual(registered.records[0].seal_header,header);
  const original=await readApplication(db,request.application_id,'groups',{limit:1});
  assert.equal(original.total_records,3);assert.equal(original.records[0].execution_state,'pending');
  assert.deepEqual((await readApplication(db,request.application_id,'batch',{key:seal.batches[0].step_id})).records,[]);
  const first=await commitStep(db,request,seal,0,stepPayload(seal,0,proofs),seal.source_watermark);
  const stillPending=await readApplication(db,request.application_id,'groups',{watermark:original.ledger_watermark});
  assert.ok(stillPending.records.every(g=>g.execution_state==='pending'));
  const active=(await readApplication(db,request.application_id)).records[0];
  assert.equal(active.status,'active');assert.equal(active.committed_batches,1);assert.equal(active.next_step_id,seal.batches[1].step_id);
  const beforeFinal=await readApplication(db,request.application_id,'groups',{limit:1});
  const last=await commitStep(db,request,seal,1,stepPayload(seal,1,proofs),first.watermark);
  const oldRest=await readApplication(db,request.application_id,'groups',{after:beforeFinal.next_after,watermark:beforeFinal.ledger_watermark});
  assert.ok([...beforeFinal.records,...oldRest.records].some(g=>g.execution_state==='pending'));
  const terminal=(await readApplication(db,request.application_id,'terminal')).records[0];
  const report=await readApplication(db,request.application_id,'groups');
  assert.equal(terminal.status,'accepted');assert.equal(terminal.group_report_id,digest(report.records));
  assert.deepEqual(terminal.counts,{groups:3,members:3,accepted_groups:3,accepted_members:3,withheld_groups:0,withheld_members:0,
    not_applied_groups:0,not_applied_members:0,batches:2,committed_batches:2});
  const {terminal_result_id,...unsigned}=terminal;assert.equal(terminal_result_id,digest(unsigned));
  const status=(await readApplication(db,request.application_id)).records[0];
  assert.equal(status.status,'terminal');assert.equal(status.terminal_result_id,terminal_result_id);assert.equal(status.next_step_id,null);
  assert.equal(status.execution_ready,false);
  assert.deepEqual((await readApplication(db,request.application_id,'batch',{key:seal.batches[1].step_id})).records,[last]);
  const assignments=await readApplication(db,request.application_id,'batches',{limit:1});
  assert.equal(assignments.next_after,'0');assert.equal(assignments.records[0].assignment.step_id,seal.batches[0].step_id);
});

databaseTest('membership expansion persists not-applied step and original immutable prefix',async db=>{
  const {request,seal,proofs}=await planned(db);
  const first=await commitStep(db,request,seal,0,stepPayload(seal,0,proofs),seal.source_watermark);
  await addMembers(db,1,{vintage:'new-vintage',player:'player-0',line:55});const before=await fence(db);
  const stopped=await commitStep(db,request,seal,1,stepPayload(seal,1,proofs),'999');
  assert.equal(stopped.status,'not_applied');assert.equal(stopped.added_observations,0);assert.equal(stopped.added_revisions,0);
  assert.equal(stopped.input_watermark,before);assert.equal(stopped.watermark,before);assert.equal(await fence(db),before);
  assert.ok(stopped.groups.every(g=>g.status==='withheld'&&g.reason_codes[0]==='application_membership_changed'));
  const terminal=(await readApplication(db,request.application_id,'terminal')).records[0];
  assert.equal(terminal.status,'incomplete_membership_changed');assert.equal(terminal.processing_complete,false);
  assert.notEqual(terminal.current_membership_id,terminal.membership_id);assert.equal(terminal.counts.accepted_members,2);
  assert.equal(terminal.counts.not_applied_members,1);
  assert.deepEqual(await commitStep(db,request,seal,0,stepPayload(seal,0,proofs),'999'),first);
  assert.deepEqual(await registerApplication(db,request,{...seal,membership_id:digest('new')},'999'),
    (await readApplication(db,request.application_id,'registration')).records[0].registration);
});

databaseTest('unrelated CAS, order, partial proofs and post-evidence fault do not leak writes',async db=>{
  const {request,seal,proofs}=await planned(db);
  await rejectsCode(()=>commitStep(db,request,seal,1,stepPayload(seal,1,proofs),seal.source_watermark),'PT409');
  const unrelated={fixture:'other'};await appendObject(db,'source_snapshot',digest(unrelated),unrelated);
  await rejectsCode(()=>commitStep(db,request,seal,0,stepPayload(seal,0,proofs),seal.source_watermark),'PT409');
  const current=await fence(db),payload=stepPayload(seal,0,proofs);
  const partial=structuredClone(payload);partial.batch.observations.pop();
  await rejectsCode(()=>commitStep(db,request,seal,0,partial,current),'PT422');
  const counter=async()=>(await db.query('SELECT (SELECT count(*) FROM selflearn_private.journal_observations)::integer AS observations,(SELECT count(*) FROM selflearn_private.journal_revisions)::integer AS revisions,(SELECT count(*) FROM selflearn_private.capture_application_batches_v2)::integer AS batches')).rows[0];
  assert.deepEqual(await counter(),{observations:0,revisions:0,batches:0});
  await db.exec("CREATE FUNCTION public.test_fail_application_v2() RETURNS trigger LANGUAGE plpgsql AS $$BEGIN RAISE EXCEPTION 'local injected after-result failure'; END$$; CREATE TRIGGER zz_test_fail AFTER INSERT ON selflearn_private.capture_application_batches_v2 FOR EACH ROW EXECUTE FUNCTION public.test_fail_application_v2()");
  await rejectsCode(()=>commitStep(db,request,seal,0,payload,current),'P0001');
  assert.deepEqual(await counter(),{observations:0,revisions:0,batches:0});assert.equal(await fence(db),current);
  await db.exec('DROP TRIGGER zz_test_fail ON selflearn_private.capture_application_batches_v2; DROP FUNCTION public.test_fail_application_v2()');
  assert.equal((await commitStep(db,request,seal,0,payload,current)).added_observations,2);
});

databaseTest('v1/v2 exact intent ownership and semantic metadata restamp conflict, original retries survive',async db=>{
  const capture=captureRequest('legacy'),request=applicationRequest(capture),seal=await sealApplication(db,request);
  const binding={version:1,kind:'nfl.capture_delivery_binding.v1',season:2026,weeks:[6],groups:[],membership_id:digest({binding_policy:'nfl.capture_labels_v1',groups:[]})};
  const batch={version:1,observations:[],revisions:[]};
  const legacy=await serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(capture),canonical(binding),canonical(batch),'0']);
  await assert.rejects(()=>registerApplication(db,request,seal),e=>e.code==='PT409'&&e.message==='capture_application_legacy_original_exists');
  assert.deepEqual(await serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(capture),'bad','bad','999']),legacy);
  const fresh=applicationRequest(captureRequest('fresh')),freshSeal=await sealApplication(db,fresh);await registerApplication(db,fresh,freshSeal);
  await assert.rejects(()=>serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(fresh.capture_request),canonical(binding),canonical(batch),'0']),
    e=>e.code==='PT409'&&e.message==='capture_application_v2_owns_intent');
  const restamped=structuredClone(fresh.capture_request),env=restamped.envelope;
  for(const ref of env.manifest.sources) {
    const obs=env.observations[ref.observation_id];delete env.observations[ref.observation_id];obs.http.etag='decoration';
    ref.observation_id=digest(obs);env.observations[ref.observation_id]=obs;
  }
  delete env.manifest.capture_id;env.manifest.capture_id=digest(env.manifest);restamped.capture_id=env.manifest.capture_id;
  const {delivery_id,envelope,raw_pointers,kind,...identity}=restamped;
  restamped.delivery_id=digest({...identity,kind:'nfl.capture_delivery.v1'});
  const alias=applicationRequest(restamped),aliasSeal=await sealApplication(db,alias);
  await assert.rejects(()=>registerApplication(db,alias,aliasSeal),e=>e.code==='PT409'&&e.message==='capture_application_source_vector_reused');
});

databaseTest('new writer roles, input-only columns, RLS, mutation and isolation deny forgery',async db=>{
  for(const table of ['capture_applications_v2','capture_application_batches_v2','capture_application_results_v2']) {
    const row=(await db.query('SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid=$1::regclass',['selflearn_private.'+table])).rows[0];
    assert.deepEqual(row,{relrowsecurity:true,relforcerowsecurity:true});
    for(const role of ['anon','authenticated']) await rejectsCode(()=>asRole(db,role,()=>db.query('SELECT * FROM selflearn_private.'+table)),'42501');
  }
  for(const role of ['anon','authenticated']) await rejectsCode(()=>asRole(db,role,()=>rpcV2(db,'selflearn_journal_read_capture_application_v2',[digest('x'),'status',null,null,1,null])),'42501');
  const request=applicationRequest(),seal=await sealApplication(db,request);await registerApplication(db,request,seal);
  await rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.capture_applications_v2(request_json,seal_json,expected_watermark,registration_json) VALUES($1,$2,$3,$4)',[canonical(request),canonical(seal),'0','{}'])),'42501');
  for(const sql of ['UPDATE selflearn_private.capture_applications_v2 SET request_json=request_json','DELETE FROM selflearn_private.capture_applications_v2','TRUNCATE selflearn_private.capture_application_results_v2']) await rejectsCode(()=>db.exec(sql),'PT409');
  await db.exec('SET ROLE service_role; BEGIN ISOLATION LEVEL REPEATABLE READ');
  try {await rejectsCode(()=>rpcV2(db,'selflearn_journal_register_capture_application_v2',[canonical(request),canonical(seal),seal.source_watermark]),'PT422');}
  finally {await db.exec('ROLLBACK; RESET ROLE');}
});

databaseTest('typed seal, complete groups, immutable facts and bounds fail before admission',async db=>{
  const members=await addMembers(db,2,{player:'same-player',line:i=>40+i});const request=applicationRequest();
  const proofs=members.map(m=>memberProof(m,request.capture_request));
  const seal=await sealApplication(db,request,{facts:{'same-player':commonFact(proofs[0])}});
  const mutations=[s=>{s.source_watermark=Number(s.source_watermark);},s=>{s.groups[0].receipt_ids.pop();},
    s=>{s.groups[0].scope[0]='2026';},s=>{s.groups[0].fact.actual=true;},s=>{s.groups[0].fact.status=null;},
    s=>{s.groups[0].fact.game.kickoff_utc='2026-09-02T00:16:00Z';},
    s=>{delete s.groups[0].fact.source_observations['espn.scoreboard'];},
    s=>{s.groups[0].fact.source_observations['nflverse.stats_player'].observation_id=digest('different capture');},
    s=>{s.batches[0].group_ids=[];},s=>{s.batches[0].ordinal='0';},s=>{s.batches[0].member_count=501;},
    s=>{s.groups[0].batch_ordinal=1;},s=>{s.limits.members=501;},s=>{s.limits.members=0;},s=>{s.limits.members=true;},
    s=>{s.limits.observations=1.5;},s=>{s.limits.rpc_bytes=8388609;},s=>{delete s.limits.revisions;},s=>{s.limits.extra=1;}];
  for(const mutate of mutations) {
    const bad=structuredClone(seal);mutate(bad);
    if(bad.groups[0].fact)bad.groups[0].fact_id=digest(bad.groups[0].fact);
    delete bad.seal_id;bad.seal_id=digest(bad);
    await rejectsCode(()=>registerApplication(db,request,bad),'PT422');
  }
  await rejectsCode(()=>serviceV2(db,'selflearn_journal_register_capture_application_v2',
    ['x'.repeat(3*1024*1024+1),canonical(seal),seal.source_watermark]),'PT422');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_applications_v2')).rows[0].n,0);
  await registerApplication(db,request,seal);
  const bad=stepPayload(seal,0,proofs);bad.batch.observations[0].measurement.actual=90;
  await rejectsCode(()=>commitStep(db,request,seal,0,bad,seal.source_watermark),'PT422');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.journal_observations')).rows[0].n,0);
});

databaseTest('only proved complete/membership stops; conservative integrity stop is immutable and exhaustive',async db=>{
  const {request,seal}=await planned(db);
  for(const reason of ['complete','membership_changed','unknown'])
    await rejectsCode(()=>serviceV2(db,'selflearn_journal_stop_capture_application_v2',[request.application_id,reason,seal.source_watermark]),'PT422');
  const terminal=await serviceV2(db,'selflearn_journal_stop_capture_application_v2',[request.application_id,'source_integrity_failed',seal.source_watermark]);
  assert.equal(terminal.status,'incomplete_integrity_or_capacity');assert.equal(terminal.complete_success,false);
  assert.equal(terminal.learning_accepted,false);assert.equal(terminal.counts.not_applied_members,3);
  assert.deepEqual(terminal.reason_codes,['source_integrity_failed']);
  assert.deepEqual(await serviceV2(db,'selflearn_journal_stop_capture_application_v2',[request.application_id,'unknown','999']),terminal);
  await rejectsCode(()=>commitStep(db,request,seal,0,{groups:[],batch:{version:1,observations:[],revisions:[]}},seal.source_watermark),'PT409');
});

databaseTest('empty and independent withheld groups are explicit terminal populations',async db=>{
  const emptyRequest=applicationRequest(),emptySeal=await sealApplication(db,emptyRequest);
  await registerApplication(db,emptyRequest,emptySeal);
  const empty=(await readApplication(db,emptyRequest.application_id,'terminal')).records[0];
  assert.equal(empty.status,'no_supported_membership');assert.equal(empty.processing_complete,true);
  assert.equal(empty.complete_success,false);assert.equal(empty.counts.members,0);assert.equal(empty.group_report_id,digest([]));
});

databaseTest('lower trusted sealed limits accept complete holds and constrain actual batch wire/proofs',async db=>{
  const members=await addMembers(db,2),request=applicationRequest();
  const proofs=members.map(m=>memberProof(m,request.capture_request));
  const facts=Object.fromEntries(proofs.map((p,i)=>[members[i].key[2],commonFact(p)]));
  const seal=await sealApplication(db,request,{facts,maximum:1});
  seal.limits={members:1,observations:1,revisions:1,rpc_bytes:8388608};delete seal.seal_id;seal.seal_id=digest(seal);
  await registerApplication(db,request,seal);
  const first=await commitStep(db,request,seal,0,stepPayload(seal,0,proofs),seal.source_watermark);
  assert.equal(first.added_observations,1);
  const last=await commitStep(db,request,seal,1,stepPayload(seal,1,proofs),first.watermark);
  assert.equal(last.added_revisions,1);
  const other=applicationRequest(captureRequest('other')),held=await sealApplication(db,other);
  held.limits={members:1,observations:1,revisions:1,rpc_bytes:100};delete held.seal_id;held.seal_id=digest(held);
  const ack=await registerApplication(db,other,held);assert.equal(ack.batch_count,0);
  assert.equal((await readApplication(db,other.application_id,'terminal')).records[0].status,'withheld');
});

databaseTest('strict pages reject future/before-registration fences, bad cursors, keys and limits',async db=>{
  const {request,seal}=await planned(db);
  for(const [view,options] of [['status',{limit:2}],['status',{after:'0'}],['status',{key:digest('key')}],
    ['batch',{key:digest('not assigned')}],['groups',{after:digest('not group')}],['groups',{limit:101}],
    ['batches',{after:'00'}],['batches',{after:'1'}],['groups',{watermark:'999'}],['status',{watermark:'0'}]])
    await rejectsCode(()=>readApplication(db,request.application_id,view,options),'PT422');
  const missing=await readApplication(db,digest('missing'));
  assert.equal(missing.found,false);assert.equal(missing.ledger_watermark,null);assert.deepEqual(missing.records,[]);
  await rejectsCode(()=>readApplication(db,request.application_id,'unknown'),'PT422');
  await rejectsCode(()=>readApplication(db,request.application_id,'batch',{key:seal.batches[0].step_id,after:'0'}),'PT422');
});

databaseTest('one receipt cannot replay two historical revisions into an unreadable batch ACK',async db=>{
  const [member]=await addMembers(db,1),a=memberProof(member,captureRequest('A'),80);
  await serviceRpc(db,'selflearn_journal_commit_observed_v1',[canonical({version:1,observations:[a.observation],revisions:[a.revision]}),await fence(db)]);
  const b=memberProof(member,moveCaptureDay(captureRequest('B'),4),90);
  b.revision.supersedes=a.revision.revision_id;b.revision.reason='source_correction';
  b.revision.revision_id='nfl.observed_resolution_v2:'+digest({receipt_id:b.observation.receipt_id,observation_id:b.observation.evidence_observation_id,supersedes:a.revision.revision_id});
  await serviceRpc(db,'selflearn_journal_commit_observed_v1',[canonical({version:1,observations:[b.observation],revisions:[b.revision]}),await fence(db)]);
  const request=applicationRequest(moveCaptureDay(captureRequest('C'),5)),c=memberProof(member,request.capture_request,90);
  c.revision=b.revision;
  const seal=await sealApplication(db,request,{facts:{[member.key[2]]:commonFact(c)}});await registerApplication(db,request,seal);
  const payload=stepPayload(seal,0,[c]);payload.batch.revisions=[a.revision,b.revision];
  payload.groups[0].revision_ids=[a.revision.revision_id,b.revision.revision_id].sort();const before=await fence(db);
  await rejectsCode(()=>commitStep(db,request,seal,0,payload,before),'PT422');
  assert.equal(await fence(db),before);
  const counts=(await db.query('SELECT (SELECT count(*) FROM selflearn_private.journal_observations)::integer AS observations,(SELECT count(*) FROM selflearn_private.journal_revisions)::integer AS revisions,(SELECT count(*) FROM selflearn_private.capture_application_batches_v2)::integer AS batches,(SELECT count(*) FROM selflearn_private.capture_application_results_v2)::integer AS terminals')).rows[0];
  assert.deepEqual(counts,{observations:2,revisions:2,batches:0,terminals:0});
  const valid=await commitStep(db,request,seal,0,stepPayload(seal,0,[c]),before);
  assert.equal(valid.added_observations,1);assert.equal(valid.added_revisions,0);
  assert.deepEqual(valid.groups[0].revision_ids,[b.revision.revision_id]);
});

databaseTest('unrepresentable long frozen scope is rejected before either held/planned registration',async db=>{
  const name='x'.repeat(300),[member]=await addMembers(db,1,{player:name}),request=applicationRequest();
  const proof=memberProof(member,request.capture_request);
  for(const facts of [{},{[name]:commonFact(proof)}]) {
    const seal=await sealApplication(db,request,{facts});await rejectsCode(()=>registerApplication(db,request,seal),'PT422');
  }
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_applications_v2')).rows[0].n,0);
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.journal_observations')).rows[0].n,0);
});

databaseTest('v2 envelope metadata/audit/HTTP types match pure verifier and never retrofit old v1',async db=>{
  const valid=captureRequest();
  const validate=value=>asRole(db,'service_role',()=>db.query('SELECT selflearn_private.capture_request_v2($1) AS value',[value]));
  await validate(canonical(valid));
  await rejectsCode(()=>validate(canonical(valid).replace('"raw_objects":[{"raw_bytes":1,','"raw_objects":[{"raw_bytes":1.0,')),'PT422');
  for(const change of [r=>{r.envelope.manifest.audit.normalized_games=true;},
    r=>{r.envelope.manifest.audit.extra=1;},r=>{r.envelope.manifest.audit.normalized_games=1.5;},
    r=>{Object.values(r.envelope.observations)[0].http.url='https://caller.invalid';},
    r=>{Object.values(r.envelope.observations)[0].http.etag='bad\x1fheader';},
    r=>{Object.values(r.envelope.observations)[0].audit.events=0;},
    r=>{Object.values(r.envelope.observations).find(o=>o.profile==='nflverse_stats_player_v1').audit.duplicate_rows=2;}]) {
    const bad=structuredClone(valid);change(bad);await rejectsCode(()=>validate(canonical(resealCapture(bad))),'PT422');
  }
  for(const metadata of ['{"value":1.0}','{"value":1e0}','{"value":9223372036854775808}'])
    await rejectsCode(()=>asRole(db,'service_role',()=>db.query('SELECT selflearn_private.capture_metadata_v2($1::json,0)',[metadata])),'PT422');
  const legacy=structuredClone(valid);legacy.envelope.manifest.audit={fixture:true};
  for(const o of Object.values(legacy.envelope.observations))o.audit={fixture:true};
  const original=resealCapture(legacy),binding={version:1,kind:'nfl.capture_delivery_binding.v1',season:2026,weeks:[6],groups:[],
    membership_id:digest({binding_policy:'nfl.capture_labels_v1',groups:[]})};
  const ack=await serviceRpc(db,'selflearn_journal_commit_capture_delivery_v1',[canonical(original),canonical(binding),canonical({version:1,observations:[],revisions:[]}),'0']);
  assert.equal(ack.status,'no_supported_membership');
});

databaseTest('every original vintage must share the registered fact week, not only the first receipt',async db=>{
  const members=[...await addMembers(db,1,{vintage:'week5',player:'same-player',week:5}),
    ...await addMembers(db,1,{vintage:'week6',player:'same-player',week:6})];
  const first=[...members].sort((a,b)=>a.generic.receipt_id.localeCompare(b.generic.receipt_id))[0],week=first.source.week;
  const request=applicationRequest(captureRequest('multiweek',[5,6])),capture=request.capture_request;
  const component=(source,profile,w)=>{
    const ref=capture.envelope.manifest.sources.find(r=>r.profile===profile&&(w===null||r.week===w));
    return{source,observation_id:ref.observation_id,observed_utc:capture.envelope.observations[ref.observation_id].observed_utc};
  };
  const fact={week,actual:80,played:true,status:'FINAL',reason:null,participation_basis:'stat_row',
    game:{season:2026,week,game_id:'G1',kickoff_utc:first.generic.event_start_utc,home:'SEA',away:'LAR',state:'FINAL'},
    source_observations:{'espn.scoreboard':component('espn.scoreboard','espn_scoreboard_v1',week),
      'nflverse.stats_player':component('nflverse.stats_player','nflverse_stats_player_v1',null)}};
  const seal=await sealApplication(db,request,{facts:{'same-player':fact}});
  await rejectsCode(()=>registerApplication(db,request,seal),'PT422');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_applications_v2')).rows[0].n,0);
});

databaseTest('oversized whole group is held while an independent complete group survives',async db=>{
  await addMembers(db,501,{player:'oversized-player',line:i=>40+i});
  const neighbor=(await addMembers(db,1,{vintage:'neighbor',player:'good-neighbor'}))[0],request=applicationRequest();
  const proof=memberProof(neighbor,request.capture_request),seal=await sealApplication(db,request,{facts:{'good-neighbor':commonFact(proof)}});
  assert.equal(seal.groups.length,2);assert.equal(seal.batches[0].member_count,1);
  await registerApplication(db,request,seal);
  await commitStep(db,request,seal,0,stepPayload(seal,0,[proof]),seal.source_watermark);
  const terminal=(await readApplication(db,request.application_id,'terminal')).records[0];
  assert.equal(terminal.status,'partial');assert.equal(terminal.counts.members,502);
  assert.equal(terminal.counts.accepted_members,1);assert.equal(terminal.counts.withheld_members,501);
});

databaseTest('1088 synthetic durable rungs across four vintages execute 185 complete groups in three batches',async db=>{
  const started=performance.now(),members=[];
  for(const [vintage,count] of [['v0',370],['v1',370],['v2',346],['v3',2]])
    members.push(...await addMembers(db,count,{vintage,player:i=>'player-'+Math.floor(i/2),line:i=>40+i%2}));
  const fixture_ms=performance.now()-started,request=applicationRequest(),proofs=members.map(m=>memberProof(m,request.capture_request));
  const facts=Object.fromEntries(proofs.map((p,i)=>[members[i].key[2],commonFact(p)]));
  const sealStart=performance.now(),seal=await sealApplication(db,request,{facts}),seal_ms=performance.now()-sealStart;
  assert.equal(members.length,1088);assert.equal(seal.groups.length,185);assert.equal(seal.batches.length,3);
  assert.equal(Math.max(...seal.groups.map(g=>g.receipt_ids.length)),8);
  const warmStart=performance.now();const certificate=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']);
  const cold_prewarm_ms=performance.now()-warmStart;
  assert.equal(certificate.member_count,1088);assert.equal(certificate.group_count,185);
  const hitStart=performance.now();assert.deepEqual(await serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']),certificate);
  const warm_hit_ms=performance.now()-hitStart;
  const registrationStart=performance.now();await registerApplication(db,request,seal);const registration_ms=performance.now()-registrationStart;
  let watermark=seal.source_watermark;const batch_ms=[],batch_bytes=[];
  for(let ordinal=0;ordinal<seal.batches.length;ordinal++) {
    const payload=stepPayload(seal,ordinal,proofs),stepStart=performance.now();
    batch_bytes.push(Buffer.byteLength(JSON.stringify({p_application_id:request.application_id,p_step_id:seal.batches[ordinal].step_id,
      p_group_results_json:canonical(payload.groups),p_batch_json:canonical(payload.batch),p_watermark:watermark}),'utf8'));
    const ack=await commitStep(db,request,seal,ordinal,payload,watermark);batch_ms.push(performance.now()-stepStart);
    assert.equal(ack.added_observations,seal.batches[ordinal].member_count);assert.equal(ack.added_revisions,seal.batches[ordinal].member_count);
    watermark=ack.watermark;
  }
  const terminal=(await readApplication(db,request.application_id,'terminal')).records[0];
  assert.equal(terminal.status,'accepted');assert.equal(terminal.counts.accepted_members,1088);
  assert.equal(terminal.counts.committed_batches,3);
  console.log('V2_CAPACITY_TIMINGS '+JSON.stringify({scope:'synthetic_SQL_fixture_not_provider_capture',fixture_ms,seal_ms,cold_prewarm_ms,warm_hit_ms,registration_ms,
    batch_ms,batch_members:seal.batches.map(b=>b.member_count),batch_bytes,total_ms:performance.now()-started}));
});

const prepareMembership=(db,season=2026,weeks=[6])=>serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[season,canonical(weeks)]);

databaseTest('derived cache binds full scope, exact policy and all-domain source authority, not outcomes',async db=>{
  const [member]=await addMembers(db,1),first=await prepareMembership(db);
  assert.deepEqual(Object.keys(first).sort(),['authority_watermark','cache_id','durable_ack','group_count','kind','learning_accepted',
    'member_count','membership_id','policy_id','scope','season','version','weeks']);
  assert.equal(first.scope,'derived_membership_cache_only');assert.equal(first.durable_ack,false);assert.equal(first.learning_accepted,false);
  assert.equal(first.member_count,1);assert.deepEqual(await prepareMembership(db),first);
  const otherWeek=await prepareMembership(db,2026,[7]);assert.notEqual(otherWeek.cache_id,first.cache_id);assert.equal(otherWeek.member_count,0);
  const otherSeason=await prepareMembership(db,2025,[6]);assert.notEqual(otherSeason.cache_id,first.cache_id);assert.equal(otherSeason.member_count,0);
  const proof=memberProof(member,captureRequest());
  await serviceRpc(db,'selflearn_journal_commit_observed_v1',[canonical({version:1,observations:[proof.observation],revisions:[proof.revision]}),
    (await db.query('SELECT selflearn_private.observed_watermark_v1()::text AS value')).rows[0].value]);
  assert.deepEqual(await prepareMembership(db),first,'observation/revision writes do not change source authority');
  const unrelated={other_domain:'immutable source'};await appendObject(db,'source_snapshot',digest(unrelated),unrelated);
  const afterObject=await prepareMembership(db);assert.notEqual(afterObject.cache_id,first.cache_id);assert.equal(afterObject.membership_id,first.membership_id);
  const other=receipt('other-domain',{domain:'soccer',task:'soccer.winner',event_id:'soccer:2026:match:1',correlation_group:'soccer:2026:match:1'});
  await appendReceipt(db,other);const afterReceipt=await prepareMembership(db);assert.notEqual(afterReceipt.cache_id,afterObject.cache_id);
  const doc=generation('other-domain-generation',[other.source_receipt_id]);await appendGeneration(db,doc);
  const afterGenerationObject=await prepareMembership(db);await commitGeneration(db,doc.envelope_id,[other],[]);
  const afterGenerationCommit=await prepareMembership(db);assert.notEqual(afterGenerationCommit.cache_id,afterGenerationObject.cache_id);
  await addMembers(db,1,{vintage:'later',player:'new-member'});const expanded=await prepareMembership(db);
  assert.equal(expanded.member_count,2);assert.notEqual(expanded.membership_id,first.membership_id);
  await appendReceipt(db,receipt('new-global-malformed-nfl',{event_id:'nfl:2026:game:unmapped'}));
  await rejectsCode(()=>prepareMembership(db),'PT422');
});

databaseTest('cache policy-definition changes miss, source text stays exact, and accounting covers complete stored rows',async db=>{
  await addMembers(db,2);const first=await prepareMembership(db);
  const row=(await db.query('SELECT c.*,octet_length(row_to_json(c)::text) AS actual_bytes FROM selflearn_private.capture_membership_cache_v2 c')).rows[0];
  assert.ok(Number(row.accounted_bytes)>=row.actual_bytes);assert.equal(Object.keys(row.source_index).length,2);
  for(const value of Object.values(row.source_index))assert.equal(typeof value.source_json,'string');
  const definition=(await db.query("SELECT pg_get_functiondef('selflearn_private.capture_source_index_v2(jsonb)'::regprocedure) AS value")).rows[0].value;
  await db.exec(definition.replace('declare source_to_receipt','-- test-only policy definition change\ndeclare source_to_receipt'));
  const changed=await prepareMembership(db);assert.notEqual(changed.policy_id,first.policy_id);assert.notEqual(changed.cache_id,first.cache_id);
  assert.equal(changed.membership_id,first.membership_id);
  await db.exec(definition);assert.deepEqual(await prepareMembership(db),first);
  const flags=(await db.query("SELECT proname,provolatile,prosecdef FROM pg_proc WHERE proname IN ('capture_get_membership_v2','capture_source_authority_v2','capture_membership_policy_v2','guard_capture_membership_cache_v2')")).rows;
  assert.equal(flags.length,4);assert.ok(flags.every(r=>r.provolatile==='v'&&!r.prosecdef));
});

databaseTest('private source-index wrappers preserve authenticated integer versus float source lexemes',async db=>{
  const [member]=await addMembers(db,1,{player:'source-number-player',line:40});
  const {receipt_id:ignored,...unsigned}=member.source;
  const sourceUnsigned=canonical(unsigned).replace('"line":40,','"line":40.0,');
  assert.notEqual(sourceUnsigned,canonical(unsigned));const sourceId=digestText(sourceUnsigned);
  const sourceText=canonical({...unsigned,receipt_id:sourceId}).replace('"line":40,','"line":40.0,');
  const {receipt_id:oldId,...genericUnsigned}=member.generic;
  genericUnsigned.source_receipt_id=sourceId;const generic={...genericUnsigned,receipt_id:digest(genericUnsigned)};
  const doc={version:1,season:2026,week:6,receipts:[{receipt_id:sourceId,learning_receipt:'__ORIGINAL_SOURCE_JSON__'}],generations:[]};
  const encode=value=>canonical(value).replace('"__ORIGINAL_SOURCE_JSON__"',sourceText);
  const unsignedDoc=encode(doc),objectId=digestText(unsignedDoc),document=encode({...doc,envelope_id:objectId});
  await serviceRpc(db,'selflearn_journal_append_object_v1',['generation',objectId,document,unsignedDoc]);
  await commitGeneration(db,objectId,[generic],[]);
  const ack=await prepareMembership(db);assert.equal(ack.member_count,2);assert.equal(ack.group_count,1);
  const stored=(await db.query('SELECT source_index->($1::text)->>\'source_json\' AS value FROM selflearn_private.capture_membership_cache_v2 WHERE cache_id=$2',[generic.receipt_id,ack.cache_id])).rows[0].value;
  assert.equal(stored,sourceText);assert.ok(stored.includes('"line":40.0'));
  assert.notEqual(digestText(sourceUnsigned),digest(unsigned));
});

databaseTest('cache inputs-only privileges, strict scope, immutable rows and corrupt-hit refusal prevent authority forgery',async db=>{
  for(const role of ['anon','authenticated'])await rejectsCode(()=>asRole(db,role,()=>rpcV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]'])),'42501');
  for(const weeks of ['[]','[6,6]','[7,6]','[6.0]','[true]','[0]','[19]'])
    await rejectsCode(()=>serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,weeks]),'PT422');
  for(const season of [1999,2101,null])await rejectsCode(()=>prepareMembership(db,season),'PT422');
  await rejectsCode(()=>asRole(db,'service_role',()=>db.query("INSERT INTO selflearn_private.capture_membership_cache_v2(season,weeks_json,members) VALUES(2026,'[6]','{}')")),'42501');
  const first=await prepareMembership(db);
  for(const sql of ['UPDATE selflearn_private.capture_membership_cache_v2 SET accounted_bytes=0','DELETE FROM selflearn_private.capture_membership_cache_v2','TRUNCATE selflearn_private.capture_membership_cache_v2'])
    await rejectsCode(()=>db.exec(sql),'PT409');
  await db.exec('SET ROLE service_role; BEGIN ISOLATION LEVEL REPEATABLE READ');
  try{await rejectsCode(()=>rpcV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']),'PT422');}
  finally{await db.exec('ROLLBACK; RESET ROLE');}
  // Explicit admin fault injection is test-only, not a service capability.
  await db.exec('ALTER TABLE selflearn_private.capture_membership_cache_v2 DISABLE TRIGGER application_immutable');
  await db.query("UPDATE selflearn_private.capture_membership_cache_v2 SET members='{}',payload_sha256='tampered' WHERE cache_id=$1",[first.cache_id]);
  await db.exec('ALTER TABLE selflearn_private.capture_membership_cache_v2 ENABLE TRIGGER application_immutable');
  await rejectsCode(()=>prepareMembership(db),'PT422');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.journal_observations')).rows[0].n,0);
});

databaseTest('bounded cache refuses new rows at 256 and accounts bytes before any certificate admission',async db=>{
  const first=await prepareMembership(db);
  for(let bits=1;bits<=255;bits++) {
    const weeks=Array.from({length:9},(_,i)=>i+1).filter(w=>bits&(1<<(w-1)));
    await prepareMembership(db,2025,weeks);
  }
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_membership_cache_v2')).rows[0].n,256);
  assert.deepEqual(await prepareMembership(db),first,'existing exact hits remain available at capacity');
  await rejectsCode(()=>prepareMembership(db,2024),'PT503');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_membership_cache_v2')).rows[0].n,256);
});

databaseTest('byte-quota guard includes existing computed accounting and never silently evicts',async db=>{
  const first=await prepareMembership(db);
  // Fixed admin fault injection tests the sum guard without allocating128MiB;
  // the separate exact-row test proves normal accounting includes all payloads.
  await db.exec('ALTER TABLE selflearn_private.capture_membership_cache_v2 DISABLE TRIGGER application_immutable');
  await db.query('UPDATE selflearn_private.capture_membership_cache_v2 SET accounted_bytes=134217728 WHERE cache_id=$1',[first.cache_id]);
  await db.exec('ALTER TABLE selflearn_private.capture_membership_cache_v2 ENABLE TRIGGER application_immutable');
  await rejectsCode(()=>prepareMembership(db,2025),'PT503');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_membership_cache_v2')).rows[0].n,1);
});
