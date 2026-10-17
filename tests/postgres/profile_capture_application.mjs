// Explicit local SQL profiling command; NOT a hosted/native/time-budget pass.
import {performance} from 'node:perf_hooks';
import {openCaptureApplicationDatabase,addMembers,memberMap,applicationRequest,sealApplication,
  registerApplication,memberProof,commonFact,stepPayload,serviceV2} from './capture_application_harness.mjs';
import {canonical} from './harness.mjs';

const db=await openCaptureApplicationDatabase(),timings={};
const measured=async(name,run)=>{const began=performance.now();const answer=await run();timings[name]=performance.now()-began;return answer;};
try {
  const members=[];
  await measured('fixture_ms',async()=>{
    for(const[vintage,count]of[['v0',370],['v1',370],['v2',346],['v3',2]])
      members.push(...await addMembers(db,count,{vintage,player:i=>'player-'+Math.floor(i/2),line:i=>40+i%2}));
  });
  const mapping=await measured('membership_only_ms',()=>memberMap(db,2));
  await measured('groups_only_ms',()=>db.query('SELECT selflearn_private.capture_groups_v2($1::jsonb)',[canonical(mapping)]));
  const request=applicationRequest(),proofs=members.map(m=>memberProof(m,request.capture_request));
  const facts=Object.fromEntries(proofs.map((p,i)=>[members[i].key[2],commonFact(p)]));
  const seal=await measured('seal_enumeration_ms',()=>sealApplication(db,request,{facts}));
  await measured('cold_prewarm_ms',()=>serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']));
  await measured('warm_hit_ms',()=>serviceV2(db,'selflearn_journal_prepare_capture_membership_v2',[2026,'[6]']));
  await measured('registration_ms',()=>registerApplication(db,request,seal));
  const payload=stepPayload(seal,0,proofs),encoded=canonical(payload.batch);
  for(const kind of ['existing_D1','application_v2']) {
    await db.exec('SET ROLE service_role; BEGIN');
    try {
      await measured(kind+'_rollback_batch_ms',()=>kind==='existing_D1'?
        db.query('SELECT public.selflearn_journal_commit_observed_v1($1,$2)',[encoded,seal.source_watermark]):
        db.query('SELECT public.selflearn_journal_commit_capture_application_batch_v2($1,$2,$3,$4,$5)',
          [request.application_id,seal.batches[0].step_id,canonical(payload.groups),encoded,seal.source_watermark]));
    } finally {await db.exec('ROLLBACK; RESET ROLE');}
  }
  const remaining=(await db.query('SELECT (SELECT count(*) FROM selflearn_private.journal_observations)::integer AS observations,(SELECT count(*) FROM selflearn_private.journal_revisions)::integer AS revisions,(SELECT count(*) FROM selflearn_private.capture_application_batches_v2)::integer AS batches')).rows[0];
  console.log(JSON.stringify({scope:'local_postgresql_wasm_synthetic_1088_SQL_fixture_only',timings,
    members:members.length,groups:seal.groups.length,batch_members:seal.batches.map(b=>b.member_count),
    rollback_left:remaining,existing_transport_timeout_ms:12000,native_or_hosted_verified:false},null,2));
} finally {await db.close();}
