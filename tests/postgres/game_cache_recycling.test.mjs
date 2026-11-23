import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';
import {asRole, canonical} from './harness.mjs';
import {addMembers,openProvenGameCaptureApplicationDatabase,serviceV2} from './capture_application_harness.mjs';

const migration = new URL('../../supabase/migrations/20261123193506_recycle_scoped_membership_cache.sql',import.meta.url);
const table = 'selflearn_private.capture_game_membership_cache_v1';
const scope = (game_id) => ({policy:'nfl.capture_game_scope_v1',season:2026,week:6,game_id});
const warm = (db,game) => asRole(db,'service_role',async () => (await db.query(
  'select public.selflearn_journal_prepare_capture_generation_proofs_v1($1) as value',[canonical(scope(game))])).rows[0].value);
const prepare = (db,game) => serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope(game))]);
const exists = async (db,id) => (await db.query(`select exists(select 1 from ${table} where cache_id=$1) as value`,[id])).rows[0].value;
const counts = async (db) => (await db.query(`select count(*)::int n,coalesce(sum(accounted_bytes),0)::bigint bytes from ${table}`)).rows[0];

// Administrative fixtures assign metadata only to unrelated recyclable rows.
// Every selected game and reconstructed member/source proof uses real RPCs.
async function fill(db, count, bytes, storedUtc='2020-01-01T00:00:00Z') {
  await db.exec(`alter table ${table} disable trigger user`);
  await db.query(`insert into ${table}(season,weeks_json,authority_watermark,policy_id,cache_id,
    membership_id,members,groups,source_index,payload_sha256,accounted_bytes,stored_utc,game_scope_json)
    select 2026,'[6]',0,selflearn_private.capture_game_policy_v1(),'fake-cache-'||i,'fake-membership','{}','[]','{}',
      repeat('0',64),$2, $3::timestamptz+i*interval '1 second',
      selflearn_private.canonical_json_v1(json_build_object('policy','nfl.capture_game_scope_v1','season',2026,'week',6,'game_id','inactive-fixture-'||i))
    from generate_series(1,$1::integer) i`,[count,bytes,storedUtc]);
  await db.exec(`alter table ${table} enable trigger user`);
}

test('scoped capacity retires stale policy/authority without changing original membership or allowing direct mutation',async()=>{
  const db=await openProvenGameCaptureApplicationDatabase();
  try {
    await addMembers(db,2,{vintage:'before',gameId:'G1'});
    await addMembers(db,2,{vintage:'unrelated',gameId:'G2'});
    await warm(db,'G1');await warm(db,'G2');
    const old=await prepare(db,'G1'),unrelated=await prepare(db,'G2');
    const originalSource=(await db.query(`select source_index from ${table} where cache_id=$1`,[old.cache_id])).rows[0];
    const originalLedger=(await db.query('select selflearn_private.observed_watermark_v1() as value')).rows[0];
    await db.exec(await readFile(migration,'utf8'));
    const after=await prepare(db,'G1');
    assert.equal(after.membership_id,old.membership_id);
    assert.deepEqual((await db.query(`select source_index from ${table} where cache_id=$1`,[after.cache_id])).rows[0],originalSource);
    assert.deepEqual((await db.query('select selflearn_private.observed_watermark_v1() as value')).rows[0],originalLedger);
    assert.equal(await exists(db,old.cache_id),false);assert.equal(await exists(db,unrelated.cache_id),false);
    for(const [role,code] of [['service_role','42501'],['anon','42501'],['authenticated','42501']]) {
      await assert.rejects(asRole(db,role,()=>db.query(`delete from ${table} where cache_id=$1`,[after.cache_id])),e=>e.code===code);
      await assert.rejects(asRole(db,role,()=>db.exec('select selflearn_private.recycle_capture_game_membership_cache_v1()')),e=>e.code===code);
    }
    await assert.rejects(db.query(`delete from ${table} where cache_id=$1`,[after.cache_id]),e=>e.code==='PT409');
    await assert.rejects(db.query(`update ${table} set accounted_bytes=0 where cache_id=$1`,[after.cache_id]),e=>e.code==='PT409');
    await assert.rejects(db.exec(`truncate ${table}`),e=>e.code==='PT409');
    const other=await prepare(db,'G2');
    assert.equal(other.membership_id,unrelated.membership_id);
    await addMembers(db,1,{vintage:'late-selected',gameId:'G1'});await warm(db,'G1');
    const newer=await prepare(db,'G1');
    assert.notEqual(newer.membership_id,after.membership_id);
    assert.equal(newer.member_count,3);
    assert.equal(await exists(db,after.cache_id),false);assert.equal(await exists(db,other.cache_id),true);
  } finally {await db.close();}
});

test('both hard limits recycle oldest eligible derived rows without duplicate-insert eviction',async()=>{
  const bytes=134217728;
  // One case is byte-limited; the second is row-limited.
  for(const [fillRows,accounted] of [[2,(bytes-2048)/2],[2047,1024]]) {
    const db=await openProvenGameCaptureApplicationDatabase();
    try {
      await db.exec(await readFile(migration,'utf8'));
      await addMembers(db,2,{vintage:'oldest',gameId:'G1'});await warm(db,'G1');
      const old=await prepare(db,'G1');
      await fill(db,fillRows,accounted);
      // A duplicate attempted insert must never evict a valid independent row.
      await asRole(db,'service_role',()=>db.query(`insert into ${table}(season,weeks_json,game_scope_json)
        values (2026,'[6]',$1) on conflict do nothing`,[canonical(scope('G1'))]));
      assert.equal(await exists(db,'fake-cache-1'),true);
      await addMembers(db,2,{vintage:'next',gameId:'G2'});await warm(db,'G2');
      const second=await prepare(db,'G2');
      assert.equal(await exists(db,'fake-cache-1'),false);
      assert.equal(await exists(db,'fake-cache-2'),true);
      assert.equal(await exists(db,old.cache_id),true);
      const quota=await counts(db);
      assert.ok(Number(quota.bytes)<=bytes);assert.ok(quota.n<=2048);
      assert.equal((await prepare(db,'G2')).cache_id,second.cache_id);
    } finally {await db.close();}
  }
});


test('an evicted authoritative game regenerates the identical cache, membership and source bytes',async()=>{
  const db=await openProvenGameCaptureApplicationDatabase();
  try {
    await db.exec(await readFile(migration,'utf8'));
    await addMembers(db,2,{vintage:'original',gameId:'G1'});await warm(db,'G1');
    const original=await prepare(db,'G1');
    const proof=(await db.query(`select members,groups,source_index,payload_sha256 from ${table} where cache_id=$1`,[original.cache_id])).rows[0];
    await fill(db,1,134217728-Number((await counts(db)).bytes),'2030-01-01T00:00:00Z');
    await addMembers(db,2,{vintage:'pressure',gameId:'G2'});await warm(db,'G2');
    await prepare(db,'G2');
    assert.equal(await exists(db,original.cache_id),false);
    const recovered=await prepare(db,'G1');
    assert.deepEqual(recovered,original);
    assert.deepEqual((await db.query(`select members,groups,source_index,payload_sha256 from ${table} where cache_id=$1`,[recovered.cache_id])).rows[0],proof);
  } finally {await db.close();}
});
