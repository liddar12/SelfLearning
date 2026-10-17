// Independent real PG-WASM checks. No native-concurrency or hosted claim.
import assert from 'node:assert/strict';
import {test} from 'node:test';
import {rejectsCode, generation} from './harness.mjs';
import {openOwnerDatabase, AUTHORITY, ACTOR, canonical, asRole, service,
        seedObject, command, append, read} from './owner_intents_harness.mjs';

async function database(run) {
  const db = await openOwnerDatabase();
  try { await seedObject(db); await run(db); } finally { await db.close(); }
}

test('SQL rejects normalized 24-hour clock values rather than disagreeing with Python DTOs', async () => database(async db => {
  for (const expires_utc of ['2099-01-01T24:00:00Z', '2099-01-01T24:00:00.000000Z',
                              '2099-01-01T24:00:00+01:00', '2099-01-01T12:60:00Z']) {
    await rejectsCode(() => append(db, command({expires_utc})), 'PT422');
  }
  assert.equal((await read(db)).head_sequence, 0);
}));

test('actual server expiry does not erase original ACK and post-expiry revocation has no fallback', async () => database(async db => {
  const expires_utc = (await db.query("SELECT to_char((clock_timestamp() + interval '1 second') at time zone 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS expires")).rows[0].expires;
  const original = command({expires_utc});
  const first = await append(db, original);
  // A bounded real wait tests actual SQL wall-clock expiry, not a caller's
  // backdated recorded_utc field or a privileged edit to immutable history.
  await new Promise(resolve => setTimeout(resolve, 1100));
  const revoked = await append(db, command({head:first.event_id, action:'revoke'}));
  assert.deepEqual(await append(db, original), first);
  await rejectsCode(() => append(db, command({head:revoked.event_id, expires_utc})), 'PT422');
  const snapshot = await read(db);
  assert.equal(snapshot.head_sequence, 2);
  assert.equal(snapshot.head_event_id, revoked.event_id);
  assert.equal(snapshot.rows.at(-1).event.command.action, 'revoke');
}));

test('every server-computed output column is denied to direct service inserts', async () => database(async db => {
  const columns = {namespace_json:'{}', namespace_sequence:1, command_id:'injected', event_id:'injected',
    previous_event_id:'injected', event_json:'{}', result_json:'{}', ledger_seq:1, stored_utc:'2026-10-01T00:00:00Z'};
  for (const [column, value] of Object.entries(columns)) {
    // Identifiers come only from this fixed local allowlist, never an upload.
    await rejectsCode(() => asRole(db, 'service_role', () => db.query(
      `INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json,${column}) VALUES($1,$2,$3,$4)`,
      [AUTHORITY, ACTOR, canonical(command()), value])), '42501');
  }
  assert.equal((await read(db)).head_sequence, 0);
}));

test('a real existing generation object is not accepted as source evidence merely because its ID hashes', async () => database(async db => {
  const value = generation('wrong-evidence-kind', []);
  const unsigned = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'envelope_id'));
  await service(db, 'selflearn_journal_append_object_v1', {p_kind:'generation', p_object_id:value.envelope_id,
    p_payload_json:canonical(value), p_hash_json:canonical(unsigned)});
  await rejectsCode(() => append(db, command({refs:[value.envelope_id]})), 'PT422');
  assert.equal((await read(db)).head_sequence, 0);
}));
