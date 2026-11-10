// Reproducible sequential SQL executor. Invoked by publication_sql_smoke.py.
// No install, socket, hosted project, or filesystem-backed database is used.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { performance } from 'node:perf_hooks';
import { openObservedDatabase } from './harness.mjs';

let input = '';
for await (const chunk of process.stdin) input += chunk;
const fixture = JSON.parse(input);
const timings = {};
const started = performance.now();
const db = await openObservedDatabase();
try {
  await db.exec(await readFile(new URL('../../supabase/migrations/20261110011758_publication_attestations_v1.sql', import.meta.url), 'utf8'));
  timings.migrations_ms = performance.now() - started;
  const version = (await db.query('SELECT version() AS version')).rows[0].version;
  await db.exec('SET ROLE service_role');
  let stage = performance.now();
  for (const object of fixture.objects) {
    await db.query('SELECT public.selflearn_journal_append_object_v1($1,$2,$3,NULL)',
      [object.kind, object.object_id, object.payload_json]);
  }
  timings.objects_ms = performance.now() - stage;
  process.stderr.write(JSON.stringify({ completed_stage: 'objects', timings }) + '\n');
  stage = performance.now();
  const ack = (await db.query('SELECT public.selflearn_journal_commit_generation_v1($1,$2,$3) AS result',
    [fixture.object_id, fixture.receipts_json, '[]'])).rows[0].result;
  timings.generation_commit_ms = performance.now() - stage;
  process.stderr.write(JSON.stringify({ completed_stage: 'generation_commit', timings }) + '\n');
  const before = (await db.query('SELECT public.selflearn_journal_read_publication_v1($1,NULL,NULL,500,NULL) AS result',
    ['receipts'])).rows[0].result.watermark;
  // Validation happens before the first commit; testing invalid evidence only
  // after commit would exercise the intentional original-result replay path.
  const observation = JSON.parse(fixture.observation_json);
  const invalid = [
    fixture.observation_json.slice(0, -1) + ',"body_utf8":"{}"}',
    JSON.stringify({ ...observation, policy: { ...observation.policy, kind: 'untrusted-policy' } }),
    JSON.stringify({ ...observation, body_utf8: '{"players":[],"players":[]}' }),
  ];
  for (const encoded of invalid) {
    await assert.rejects(() => db.query('SELECT public.selflearn_journal_commit_publication_v1($1,$2)',
      [fixture.object_id, encoded]), error => error.code === 'PT422');
  }
  const absent = (await db.query('SELECT public.selflearn_journal_read_publication_v1($1,$2,NULL,1,NULL) AS result',
    ['publication_attestation', fixture.object_id])).rows[0].result;
  assert.deepEqual(absent.rows, []);
  stage = performance.now();
  const result = (await db.query('SELECT public.selflearn_journal_commit_publication_v1($1,$2) AS result',
    [fixture.object_id, fixture.observation_json])).rows[0].result;
  timings.publication_commit_ms = performance.now() - stage;
  process.stderr.write(JSON.stringify({ completed_stage: 'publication_commit', timings }) + '\n');
  assert.equal(result.replayed, false);
  const replay = (await db.query('SELECT public.selflearn_journal_commit_publication_v1($1,$2) AS result',
    [fixture.object_id, 'invalid new observation on original replay'])).rows[0].result;
  assert.equal(replay.replayed, true);
  assert.deepEqual(replay.attestation, result.attestation);
  const pinned = (await db.query('SELECT public.selflearn_journal_read_publication_v1($1,$2,NULL,1,$3) AS result',
    ['publication_attestation', fixture.object_id, before])).rows[0].result;
  assert.deepEqual(pinned.rows, []);
  const current = (await db.query('SELECT public.selflearn_journal_read_publication_v1($1,$2,NULL,1,NULL) AS result',
    ['publication_attestation', fixture.object_id])).rows[0].result;
  assert.deepEqual(JSON.parse(current.rows[0].attestation_json), result.attestation);
  const stored = (await db.query('SELECT observation_json,ack_json FROM selflearn_private.journal_publication_attestations WHERE object_id=$1',
    [fixture.object_id])).rows[0];
  assert.equal(stored.observation_json, fixture.observation_json);
  assert.deepEqual(JSON.parse(stored.ack_json), ack);
  await db.exec('RESET ROLE; SET ROLE anon');
  await assert.rejects(() => db.query('SELECT public.selflearn_journal_commit_publication_v1($1,$2)',
    [fixture.object_id, fixture.observation_json]), error => error.code === '42501');
  timings.total_ms = performance.now() - started;
  process.stdout.write(JSON.stringify({ engine: 'postgresql-wasm', version,
    result, timings, checks: ['exact_body', 'original_replay', 'pinned_fence', 'private_rpc_grant',
      'duplicate_outer_key_refused', 'invalid_policy_refused', 'duplicate_body_key_refused'] }) + '\n');
} finally {
  await db.close();
}
