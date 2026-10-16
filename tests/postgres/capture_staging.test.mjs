import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import { openDatabase, digest, asRole, rejectsCode } from './harness.mjs';

const migration = new URL('../../supabase/migrations/20261016040611_capture_staging_quotas_v1.sql', import.meta.url);
const namespace = digest('configured-producer-destination');
// The existing harness intentionally allowlists journal RPC names. Keep it
// unchanged; this additive quota test calls one fixed parameterized function.
const quotaRpc = async (db, args) => (await db.query('SELECT public.selflearn_reserve_capture_stage_v1($1,$2,$3) AS result', args)).rows[0].result;
const quotaService = (db, args) => asRole(db, 'service_role', () => quotaRpc(db, args));
const reserve = (db, chunk, size) => quotaService(db, [namespace, digest(chunk), size]);
async function database(run) {
  const db = await openDatabase();
  try { await db.exec(await readFile(migration, 'utf8')); await run(db); }
  finally { await db.close(); }
}
async function provision(db, bytes=5, chunks=2) {
  await db.query('INSERT INTO selflearn_private.capture_staging_limits(namespace_id,max_total_bytes,max_chunks,enabled) VALUES($1,$2,$3,true)', [namespace,bytes,chunks]);
}

test('staging namespace has no default configuration; service cannot self-provision or forge reservation result', async () => database(async db => {
  await rejectsCode(() => reserve(db, 'a', 1), 'PT503');
  for (const role of ['anon', 'authenticated']) {
    await rejectsCode(() => asRole(db, role, () => quotaRpc(db, [namespace,digest('a'),1])), '42501');
    await rejectsCode(() => asRole(db, role, () => db.query('SELECT * FROM selflearn_private.capture_staging_reservations')), '42501');
  }
  await rejectsCode(() => asRole(db, 'service_role', () => db.query(
    'INSERT INTO selflearn_private.capture_staging_limits VALUES($1,5,2,true)', [namespace])), '42501');
  await provision(db);
  await rejectsCode(() => asRole(db, 'service_role', () => db.query(
    'INSERT INTO selflearn_private.capture_staging_reservations VALUES($1,$2,1,$3)', [namespace,digest('a'),'{}'])), '42501');
  const rows = (await db.query("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid IN ('selflearn_private.capture_staging_limits'::regclass,'selflearn_private.capture_staging_reservations'::regclass)")).rows;
  assert.equal(rows.length,2); assert.ok(rows.every(r=>r.relrowsecurity&&r.relforcerowsecurity));
}));

test('byte/count budget, exact original reservation recovery, failed upload charging and admin disable', async () => database(async db => {
  await provision(db);
  const first = await reserve(db,'a',2);
  assert.equal(first.quota_reserved,true); assert.equal(first.learning_accepted,false); assert.equal(first.durable_ack,false);
  assert.equal(first.scope,'staging_budget_only'); assert.equal(first.reserved_total_bytes,2);
  const second = await reserve(db,'b',3); assert.equal(second.reserved_total_bytes,5); assert.equal(second.reserved_chunks,2);
  assert.deepEqual(await reserve(db,'a',2),first);
  await rejectsCode(()=>reserve(db,'a',3),'PT409');
  await rejectsCode(()=>reserve(db,'c',1),'PT429');
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_staging_reservations')).rows[0].n,2);
  // No Storage write was needed to consume the reservation: failures stay charged.
  await db.query('UPDATE selflearn_private.capture_staging_limits SET enabled=false WHERE namespace_id=$1',[namespace]);
  await rejectsCode(()=>reserve(db,'a',2),'PT503');
}));

test('direct input-only INSERT has identical budget and immutability guards; namespace accounting is isolated', async () => database(async db => {
  await provision(db,100,1);
  const direct=await asRole(db,'service_role',()=>db.query(
    'INSERT INTO selflearn_private.capture_staging_reservations(namespace_id,chunk_id,raw_bytes) VALUES($1,$2,2) RETURNING result_json',[namespace,digest('a')]));
  assert.equal(JSON.parse(direct.rows[0].result_json).reserved_total_bytes,2);
  await rejectsCode(()=>reserve(db,'b',1),'PT429');
  const other=digest('other-namespace');
  await db.query('INSERT INTO selflearn_private.capture_staging_limits VALUES($1,1,1,true)',[other]);
  assert.equal((await quotaService(db,[other,digest('a'),1])).reserved_total_bytes,1);
  for(const command of ['UPDATE selflearn_private.capture_staging_reservations SET raw_bytes=1',
    'DELETE FROM selflearn_private.capture_staging_reservations','TRUNCATE selflearn_private.capture_staging_reservations']) {
    await assert.rejects(()=>db.exec(command));
  }
  for (const size of [0,1048577,null]) await rejectsCode(()=>reserve(db,'invalid',size),'PT422');
}));

test('higher-isolation transactions fail closed before RPC or direct insert; not a native multi-session proof', async () => database(async db => {
  await provision(db);
  for (const isolation of ['REPEATABLE READ','SERIALIZABLE']) {
    // RESET ROLE inside an aborted transaction would mask the actual SQLSTATE.
    // Roll back before restoring the role; do not weaken the existing helper.
    await db.exec('SET ROLE service_role');
    try {
      await db.exec('BEGIN ISOLATION LEVEL '+isolation);
      try { await rejectsCode(()=>quotaRpc(db,[namespace,digest('a'),1]),'PT422'); }
      finally { await db.exec('ROLLBACK'); }
      await db.exec('BEGIN ISOLATION LEVEL '+isolation);
      try { await rejectsCode(()=>db.query(
        'INSERT INTO selflearn_private.capture_staging_reservations(namespace_id,chunk_id,raw_bytes) VALUES($1,$2,1)',[namespace,digest('a')]),'PT422'); }
      finally { await db.exec('ROLLBACK'); }
    } finally { await db.exec('RESET ROLE'); }
  }
  assert.equal((await db.query('SELECT count(*)::integer AS n FROM selflearn_private.capture_staging_reservations')).rows[0].n,0);
}));
