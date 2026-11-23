import assert from 'node:assert/strict';
import test from 'node:test';
import {readFile} from 'node:fs/promises';
import {canonical,asRole,appendGeneration,commitGeneration,digest} from './harness.mjs';
import {openGameCaptureApplicationDatabase,openProvenGameCaptureApplicationDatabase,
  generationProofMigration,addMembers,serviceV2} from './capture_application_harness.mjs';
const scope=game_id=>({policy:'nfl.capture_game_scope_v1',season:2026,week:6,game_id});
const warm=(db,game='G1')=>serviceV2(db,'selflearn_journal_prepare_capture_generation_proofs_v1',[canonical(scope(game))]);
const prepare=(db,game='G1')=>serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope(game))]);
const proofCount=async db=>Number((await db.query('select count(*) n from selflearn_private.capture_generation_envelope_proofs_v1')).rows[0].n);

test('one-generation warm resumes, reuses shared originals, preserves membership/source bytes and invalidates once on append',async()=>{
  const db=await openGameCaptureApplicationDatabase();
  try {
    for(let i=0;i<3;i++) await addMembers(db,6,{vintage:'generation-'+i,gameId:n=>n<3?'G1':'G2'});
    const before=await prepare(db),watermark=(await db.query('select selflearn_private.observed_watermark_v1()::text value')).rows[0].value;
    const original=(await db.query('select members,groups,source_index from selflearn_private.capture_game_membership_cache_v1')).rows[0];
    await db.exec(await readFile(generationProofMigration,'utf8'));
    await assert.rejects(prepare(db),e=>e.code==='PT503'&&e.message==='capture_generation_proof_pending');
    for(let i=1;i<=3;i++){
      const result=await warm(db);
      assert.equal(result.added,true);assert.equal(result.total_generation_count,3);
      assert.equal(result.verified_generation_count,i);assert.equal(result.remaining_generation_count,3-i);
      assert.equal(await proofCount(db),i,'one bounded transaction adds only one proof');
      assert.equal(result.durable_ack,false);assert.equal(result.learning_accepted,false);
    }
    const after=await prepare(db);
    assert.equal(after.membership_id,before.membership_id);assert.notEqual(after.policy_id,before.policy_id);
    const current=(await db.query('select members,groups,source_index from selflearn_private.capture_game_membership_cache_v1 where cache_id=$1',[after.cache_id])).rows[0];
    assert.deepEqual(current,original,'pure membership and exact original-source strings are unchanged');
    const reused=await warm(db,'G2');assert.equal(reused.remaining_generation_count,0);
    assert.equal(reused.prepared_object_id,null);assert.equal(reused.added,false);assert.equal(await proofCount(db),3);
    assert.equal((await db.query('select selflearn_private.observed_watermark_v1()::text value')).rows[0].value,watermark);
    const appended=await addMembers(db,1,{vintage:'later'});
    await assert.rejects(prepare(db),e=>e.code==='PT503');
    const next=await warm(db);assert.equal(next.prepared_object_id,appended[0].document.envelope_id);
    assert.equal(next.total_generation_count,4);assert.equal(next.remaining_generation_count,0);
    const changed=await prepare(db);assert.notEqual(changed.membership_id,after.membership_id);assert.equal(changed.member_count,10);
  } finally {await db.close();}
});

test('only the verifier can write certificates; original corruption and malformed ACK cannot be certified',async()=>{
  const db=await openProvenGameCaptureApplicationDatabase();
  try {
    const members=await addMembers(db,2),id=members[0].document.envelope_id;
    for(const role of ['anon','authenticated']) await assert.rejects(asRole(db,role,()=>
      db.query('select public.selflearn_journal_prepare_capture_generation_proofs_v1($1)',[canonical(scope('G1'))])),e=>e.code==='42501');
    await assert.rejects(asRole(db,'service_role',()=>db.exec("insert into selflearn_private.capture_generation_envelope_proofs_v1(object_id) values('x')")),e=>e.code==='42501');
    await db.exec('alter table selflearn_private.journal_objects disable trigger user');
    await db.query("update selflearn_private.journal_objects set payload_json=replace(payload_json,'player-0','tampered') where object_id=$1",[id]);
    await db.exec('alter table selflearn_private.journal_objects enable trigger user');
    await assert.rejects(warm(db),e=>e.code==='PT422');assert.equal(await proofCount(db),0);
    await db.exec('alter table selflearn_private.journal_objects disable trigger user');
    await db.query('update selflearn_private.journal_objects set payload_json=$2 where object_id=$1',[id,canonical(members[0].document)]);
    await db.exec('alter table selflearn_private.journal_objects enable trigger user');
    await db.exec('alter table selflearn_private.journal_generation_commits disable trigger user');
    await db.query("update selflearn_private.journal_generation_commits set ack_json=jsonb_set(ack_json::jsonb,'{receipts}', '[]'::jsonb)::text where object_id=$1",[id]);
    await db.exec('alter table selflearn_private.journal_generation_commits enable trigger user');
    await assert.rejects(warm(db),e=>e.code==='PT422');assert.equal(await proofCount(db),0);
  } finally {await db.close();}
});

test('proof reuse checks raw bytes; policy changes require fresh certificates',async()=>{
  const db=await openProvenGameCaptureApplicationDatabase();
  try {
    const members=await addMembers(db,1),id=members[0].document.envelope_id;
    const first=await warm(db);await prepare(db);
    await db.exec('alter table selflearn_private.capture_generation_envelope_proofs_v1 disable trigger user');
    await db.query("update selflearn_private.capture_generation_envelope_proofs_v1 set ack_sha256=repeat('0',64) where object_id=$1",[id]);
    await db.exec('alter table selflearn_private.capture_generation_envelope_proofs_v1 enable trigger user');
    await assert.rejects(db.query('select selflearn_private.capture_game_members_v1($1::json)',[canonical(scope('G1'))]),e=>e.code==='PT422'&&e.message==='capture_generation_proof_integrity_mismatch');
    // Fixture administration changes a validator definition, simulating a later
    // reviewed policy migration. Both the proof and membership cache invalidate.
    const definition=(await db.query("select pg_get_functiondef('selflearn_private.capture_verify_generation_envelope_v1(text)'::regprocedure) value")).rows[0].value;
    await db.exec(definition.replace('declare','-- revised validator policy\ndeclare'));
    await assert.rejects(prepare(db),e=>e.code==='PT503');
    const second=await warm(db);assert.notEqual(second.policy_id,first.policy_id);assert.equal(await proofCount(db),2);
    assert.equal((await prepare(db)).member_count,1);
  } finally {await db.close();}
});

test('envelope certificates do not bypass selected source hash, parity or duplicate raw-source checks',async()=>{
  const db=await openProvenGameCaptureApplicationDatabase();
  try {
    await addMembers(db,1,{sourceExtra:{base_prob:0.123}});
    await warm(db);
    await assert.rejects(prepare(db),e=>e.code==='PT422'&&e.message==='capture_member_original_parity_mismatch');
  } finally {await db.close();}
  const duplicateDb=await openProvenGameCaptureApplicationDatabase();
  try {
    const members=await addMembers(duplicateDb,2);
    const document={...members[0].document,fixture:'second envelope'};delete document.envelope_id;document.envelope_id=digest(document);
    await appendGeneration(duplicateDb,document);await commitGeneration(duplicateDb,document.envelope_id,members.map(m=>m.generic),[]);
    await warm(duplicateDb);await warm(duplicateDb);
    assert.equal((await prepare(duplicateDb)).member_count,2);
    const map=(await duplicateDb.query('select selflearn_private.capture_game_members_v1($1::json) value',[canonical(scope('G1'))])).rows[0].value;
    assert.ok(Object.values(map).every(row=>row.generation_object_ids.length===2));
  } finally {await duplicateDb.close();}
});
