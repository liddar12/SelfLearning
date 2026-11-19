import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { spawnSync } from 'node:child_process';
import {
  openDatabase, canonical, digest, digestText, receipt, resolution, revision, generation,
  asRole, rpc, appendReceipt, appendRevision, appendObject, appendGeneration,
  commitGeneration, read, rejectsCode, payload,
} from './harness.mjs';

let db;
before(async () => { db = await openDatabase(); });
after(async () => { if (db) await db.close(); });

test('actual PostgreSQL migration creates RLS tables and invoker-only RPCs', async () => {
  const version = await db.query('SELECT version() AS version');
  assert.match(version.rows[0].version, /PostgreSQL/);
  const tables = await db.query(`
    SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
      FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname = 'selflearn_private' AND c.relkind = 'r'
     ORDER BY c.relname
  `);
  assert.deepEqual(tables.rows.map(row => row.relname),
    ['journal_generation_commits', 'journal_objects', 'journal_receipts', 'journal_revisions']);
  assert.ok(tables.rows.every(row => row.relrowsecurity === true && row.relforcerowsecurity === true));
  const functions = await db.query(`
    SELECT p.proname, p.prosecdef,
      has_function_privilege('anon', p.oid, 'EXECUTE') AS anonymous,
      has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated,
      has_function_privilege('service_role', p.oid, 'EXECUTE') AS service
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'public' AND p.proname LIKE 'selflearn_journal_%_v1'
     ORDER BY p.proname
  `);
  assert.equal(functions.rows.length, 5);
  assert.ok(functions.rows.every(row => !row.prosecdef && !row.anonymous && !row.authenticated && row.service));
  const helpers = await db.query(`
    SELECT has_function_privilege('anon', p.oid, 'EXECUTE') AS anonymous,
      has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated
      FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname = 'selflearn_private'
  `);
  assert.ok(helpers.rows.length > 0);
  assert.ok(helpers.rows.every(row => !row.anonymous && !row.authenticated));
});

test('anonymous and authenticated roles cannot inspect private data or invoke journal RPCs', async () => {
  for (const role of ['anon', 'authenticated']) {
    await asRole(db, role, async () => {
      await rejectsCode(() => db.query('SELECT * FROM selflearn_private.journal_receipts'), '42501');
      await rejectsCode(() => rpc(db, 'selflearn_journal_read_v1', ['receipts', null, null, 10, null]), '42501');
      await rejectsCode(() => rpc(db, 'selflearn_journal_append_receipt_v1', [canonical(receipt('anonymous-' + role))]), '42501');
      await rejectsCode(() => rpc(db, 'selflearn_journal_append_resolution_v1', ['{}']), '42501');
      await rejectsCode(() => rpc(db, 'selflearn_journal_append_object_v1', ['source_snapshot', digest({}), '{}', '{}']), '42501');
      await rejectsCode(() => rpc(db, 'selflearn_journal_commit_generation_v1', [digest({}), '[]', '[]']), '42501');
    });
  }
});

test('receipt RPC preserves exact payload, rejects source mutation, and replays idempotently', async () => {
  const value = receipt('receipt-replay', { note: 'Français \u2603' });
  assert.deepEqual(await appendReceipt(db, value), { version: 1, added: true });
  assert.deepEqual(await appendReceipt(db, structuredClone(value)), { version: 1, added: false });
  const found = await read(db, 'receipt', value.receipt_id);
  assert.equal(found.rows.length, 1);
  assert.equal(found.rows[0].payload_json, canonical(value));
  assert.deepEqual(payload(found.rows[0]), value);
  const changedSource = receipt('receipt-replay', { first_seen_utc: '2026-09-01T12:02:00Z' });
  await rejectsCode(() => appendReceipt(db, changedSource), 'PT409');
  const corrupted = { ...value, served_prob: 0.1 };
  await rejectsCode(() => appendReceipt(db, corrupted), 'PT422');
  assert.equal((await read(db, 'receipt', value.receipt_id)).rows[0].payload_json, canonical(value));
});

test('receipt contract rejects malformed documents rather than accepting a malformed envelope', async () => {
  for (const update of [
    { version: true }, { base_prob: true }, { served_prob: 2 },
    { forecast_created_utc: '2026-09-03T12:00:00Z' },
    { first_seen_utc: '2026-09-02T00:15:00Z' },
    { source_timestamps: { fixture: '2026-09-01T12:01:00Z' } },
    { source_timestamps: {} }, { features: {} }, { publication_status: 'invented' },
  ]) {
    const value = receipt('bad-' + digest(update).slice(-12), update);
    await rejectsCode(() => appendReceipt(db, value), 'PT422');
    assert.equal((await read(db, 'receipt', value.receipt_id)).rows.length, 0);
  }
  await rejectsCode(() => asRole(db, 'service_role', () => rpc(db, 'selflearn_journal_append_receipt_v1', ['not json'])), 'PT422');
});

test('database clock and append sequence cannot be supplied by a service client', async () => {
  const value = receipt('database-clock', { received_utc: '2000-01-01T00:00:00Z' });
  const before = (await db.query('SELECT clock_timestamp() AS value')).rows[0].value;
  await appendReceipt(db, value);
  const after = (await db.query('SELECT clock_timestamp() AS value')).rows[0].value;
  const row = (await read(db, 'receipt', value.receipt_id)).rows[0];
  const received = Date.parse(row.received_utc);
  assert.ok(Number.isFinite(received));
  assert.ok(received >= new Date(before).getTime() - 1);
  assert.ok(received <= new Date(after).getTime() + 1);
  assert.notEqual(row.received_utc, value.received_utc);
  assert.match(row.journal_seq, /^\d+$/);
  const privileges = await db.query(`
    SELECT has_column_privilege('service_role', 'selflearn_private.journal_receipts', 'stored_utc', 'INSERT') AS clock,
           has_column_privilege('service_role', 'selflearn_private.journal_receipts', 'journal_seq', 'INSERT') AS sequence
  `);
  assert.deepEqual(privileges.rows[0], { clock: false, sequence: false });
  await asRole(db, 'service_role', async () => {
    await rejectsCode(() => db.query("INSERT INTO selflearn_private.journal_receipts(stored_utc) VALUES ('2000-01-01T00:00:00Z')"), '42501');
    await rejectsCode(() => db.query('INSERT INTO selflearn_private.journal_receipts(journal_seq) VALUES (1)'), '42501');
  });
});

test('source and generation objects retain byte identity and validate content digests', async () => {
  const source = { fixture: 'object-replay', number: 1 };
  const id = digest(source);
  assert.deepEqual(await appendObject(db, 'source_snapshot', id, source), { version: 1, added: true });
  assert.deepEqual(await appendObject(db, 'source_snapshot', id, structuredClone(source)), { version: 1, added: false });
  const row = (await read(db, 'object', id)).rows[0];
  assert.equal(row.payload_json, canonical(source));
  assert.equal(row.kind, 'source_snapshot');
  await rejectsCode(() => appendObject(db, 'source_snapshot', id, { ...source, number: 2 }), 'PT422');
  await rejectsCode(() => appendObject(db, 'unsupported', id, source), 'PT422');
  // Both documents have the same legitimate digest input, but changing kind is
  // not an idempotent replay of the originally persisted object.
  const otherKind = { ...source, envelope_id: id };
  await rejectsCode(() => appendObject(db, 'generation', id, otherKind, source), 'PT409');
  const doc = generation('object-generation', [digest({ source: 'for-generation' })]);
  assert.deepEqual(await appendGeneration(db, doc), { version: 1, added: true });
  assert.equal((await read(db, 'object', doc.envelope_id)).rows[0].payload_json, canonical(doc));
});

test('canonical text preserves Python numeric tokens instead of JSONB equality normalization', async () => {
  const integer = '{"fixture":"numeric-token","value":1}';
  const decimal = '{"fixture":"numeric-token","value":1.0}';
  const boolean = '{"fixture":"numeric-token","value":true}';
  const ids = [integer, decimal, boolean].map(digestText);
  assert.equal(new Set(ids).size, 3);
  for (const [index, text] of [integer, decimal, boolean].entries()) {
    const result = await asRole(db, 'service_role', () => rpc(db,
      'selflearn_journal_append_object_v1', ['source_snapshot', ids[index], text, text]));
    assert.deepEqual(result, { version: 1, added: true });
    assert.equal((await read(db, 'object', ids[index])).rows[0].payload_json, text);
  }
  const numericKeys = '{"10":"ten","2":"two","unicode":"\\u00e9"}';
  assert.equal(canonical({ 2: 'two', 10: 'ten', unicode: '\u00e9' }), numericKeys);
  const keyId = digestText(numericKeys);
  assert.deepEqual(await asRole(db, 'service_role', () => rpc(db,
    'selflearn_journal_append_object_v1', ['source_snapshot', keyId, numericKeys, numericKeys])),
  { version: 1, added: true });
  await rejectsCode(() => asRole(db, 'service_role', () => rpc(db,
    'selflearn_journal_append_object_v1', ['source_snapshot', digestText(decimal), integer, decimal])), 'PT422');
});

test('duplicate JSON keys and excessive depth fail closed before hashing or persistence', async () => {
  for (const text of ['{"a":1,"a":1}', '{"a":{"x":1,"x":1}}', '{"a":[{"x":1,"x":2}]}']) {
    const id = digestText(text);
    await rejectsCode(() => asRole(db, 'service_role', () => rpc(db,
      'selflearn_journal_append_object_v1', ['source_snapshot', id, text, text])), 'PT422');
    assert.equal((await read(db, 'object', id)).rows.length, 0);
  }
  const nested = depth => '['.repeat(depth) + 'null' + ']'.repeat(depth);
  const valid = nested(24);
  assert.deepEqual(await asRole(db, 'service_role', () => rpc(db,
    'selflearn_journal_append_object_v1', ['source_snapshot', digestText(valid), valid, valid])),
  { version: 1, added: true });
  const invalid = nested(25);
  await rejectsCode(() => asRole(db, 'service_role', () => rpc(db,
    'selflearn_journal_append_object_v1', ['source_snapshot', digestText(invalid), invalid, invalid])), 'PT422');
});

test('SQL canonicalization and hashes match an independent Python numeric and Unicode oracle', async () => {
  const oracle = spawnSync(process.env.SELFLEARN_TEST_PYTHON || 'python3', ['-c', String.raw`
import hashlib, json, math, random, struct
values = [None, True, False, 0, 1, -1, 2**63, -(2**63), 2**100,
          0.0, -0.0, 1.0, -1.0, 0.1, 1e-3, 1e-4, 1e-5, 1e-6,
          1e-7, 1e14, 1e15, 1e16, 1.2345678901234567,
          9007199254740992.0, 1.0000000000000002, 5e-324,
          2.2250738585072014e-308, 1.7976931348623157e308,
          {'10':'ten','2':'two','unicode':'Français ' + chr(0x2603)},
          {'astral':chr(0x1f3c8),'control':''.join(chr(n) for n in (8,9,10,13,12,31,127)),
           'slash':'/' + chr(92) + chr(34)},
          {chr(0x10000):1,chr(0xe000):2}]
for threshold in (1e-4, 1e16):
    values.extend([math.nextafter(threshold, -math.inf), threshold,
                   math.nextafter(threshold, math.inf)])
rng = random.Random(1722026)
for _ in range(256):
    value = struct.unpack('>d', rng.getrandbits(64).to_bytes(8, 'big'))[0]
    if math.isfinite(value):
        values.append(value)
result = []
for value in values:
    encoded = json.dumps(value, sort_keys=True, separators=(',',':'), ensure_ascii=True, allow_nan=False)
    result.append({'encoded':encoded,'digest':'sha256:'+hashlib.sha256(encoded.encode()).hexdigest()})
print(json.dumps(result, ensure_ascii=True, separators=(',',':')))
`], { encoding: 'utf8', maxBuffer: 2 * 1024 * 1024 });
  assert.equal(oracle.status, 0, oracle.stderr || 'Python oracle did not run');
  const cases = JSON.parse(oracle.stdout);
  assert.ok(cases.length > 280);
  for (const expected of cases) {
    const result = (await db.query(`
      SELECT selflearn_private.canonical_json_v1($1::json) AS canonical,
             selflearn_private.digest_json_v1($1::json) AS digest
    `, [expected.encoded])).rows[0];
    assert.equal(result.canonical, expected.encoded, 'Python canonical mismatch for ' + expected.encoded);
    assert.equal(result.digest, expected.digest, 'Python hash mismatch for ' + expected.encoded);
  }
  // PostgreSQL text cannot represent NUL or a lone surrogate. The public RPC
  // must quarantine these unsupported values, not leak a raw database error or
  // claim they were durably persisted.
  for (const text of ['{"invalid":"\\ud800"}', '{"invalid":"\\u0000"}']) {
    await rejectsCode(() => asRole(db, 'service_role', () => rpc(db,
      'selflearn_journal_append_object_v1', ['source_snapshot', digestText(text), text, text])), 'PT422');
  }
});

test('generation commit atomically partitions sources, returns duplicate replay and retains original ACK', async () => {
  const a = receipt('generation-accepted-a');
  const b = receipt('generation-accepted-b');
  const rejectedSource = digest({ source: 'generation-rejected' });
  await appendReceipt(db, a);
  const doc = generation('generation-atomic', [a.source_receipt_id, b.source_receipt_id, rejectedSource]);
  await appendGeneration(db, doc);
  const rejected = [{ source_receipt_id: rejectedSource, reason: 'documented fixture quarantine' }];
  const ack = await commitGeneration(db, doc.envelope_id, [a, b], rejected);
  assert.equal(ack.version, 1);
  assert.equal(ack.object_id, doc.envelope_id);
  assert.equal(ack.durable, true);
  assert.deepEqual(ack.receipts, [
    { source_receipt_id: a.source_receipt_id, receipt_id: a.receipt_id, status: 'duplicate' },
    { source_receipt_id: b.source_receipt_id, receipt_id: b.receipt_id, status: 'recorded' },
  ]);
  assert.deepEqual(ack.rejections, rejected);
  assert.equal((await read(db, 'receipt', b.receipt_id)).rows.length, 1);
  assert.deepEqual(await commitGeneration(db, doc.envelope_id, [a, b], rejected),
    { ...ack, receipts: ack.receipts.map(row => ({ ...row, status: 'duplicate' })) });
  const stored = (await read(db, 'generation_ack', doc.envelope_id)).rows[0];
  assert.deepEqual(JSON.parse(stored.ack_json), ack);
  await rejectsCode(() => commitGeneration(db, doc.envelope_id, [a, b],
    [{ source_receipt_id: rejectedSource, reason: 'changed explanation' }]), 'PT409');
});

test('invalid generation partitions and midway receipt conflicts leave no partial receipt or ACK', async () => {
  const a = receipt('generation-rollback-a');
  const b = receipt('generation-rollback-b');
  const doc = generation('generation-rollback', [a.source_receipt_id, b.source_receipt_id]);
  await appendGeneration(db, doc);
  for (const accepted of [[a], [a, a], [a, receipt('not-in-generation')]]) {
    await rejectsCode(() => commitGeneration(db, doc.envelope_id, accepted, []), 'PT422');
    assert.equal((await read(db, 'receipt', a.receipt_id)).rows.length, 0);
    assert.equal((await read(db, 'generation_ack', doc.envelope_id)).rows.length, 0);
  }
  await appendReceipt(db, b);
  const conflicting = receipt('generation-rollback-b', { first_seen_utc: '2026-09-01T12:02:00Z' });
  await rejectsCode(() => commitGeneration(db, doc.envelope_id, [a, conflicting], []), 'PT409');
  assert.equal((await read(db, 'receipt', a.receipt_id)).rows.length, 0);
  assert.equal((await read(db, 'generation_ack', doc.envelope_id)).rows.length, 0);
  assert.equal((await read(db, 'receipt', b.receipt_id)).rows.length, 1);
});

test('official corrections retain history, refuse forks and replace binary results with authoritative VOID', async () => {
  const value = receipt('revision-chain');
  await appendReceipt(db, value);
  const first = revision(value, 'fixture-revision-v1');
  assert.deepEqual(await appendRevision(db, first), { version: 1, added: true });
  assert.deepEqual(await appendRevision(db, first), { version: 1, added: false });
  const originalWatermark = (await read(db, 'status')).watermark;
  const corrected = revision(value, 'fixture-revision-v2', {
    supersedes: first.revision_id, reason: 'official correction',
    resolution: resolution(value, false, { source: 'official-corrected-fixture' }),
  });
  assert.deepEqual(await appendRevision(db, corrected), { version: 1, added: true });
  assert.deepEqual(await appendRevision(db, first), { version: 1, added: false });
  await rejectsCode(() => appendRevision(db, revision(value, 'fixture-revision-fork', {
    supersedes: first.revision_id, reason: 'stale reader',
  })), 'PT409');
  const voided = revision(value, 'fixture-revision-void', {
    supersedes: corrected.revision_id, reason: 'official DNP correction',
    resolution: resolution(value, null, {
      status: 'VOID', authoritative: true, final_evidence_status: 'FINAL',
      reason: 'official result invalidated the player appearance',
    }),
  });
  assert.deepEqual(await appendRevision(db, voided), { version: 1, added: true });
  const history = await read(db, 'resolution_history', value.receipt_id);
  assert.deepEqual(history.rows.map(row => payload(row).revision_id),
    [first.revision_id, corrected.revision_id, voided.revision_id]);
  assert.deepEqual(history.rows.map(row => payload(row).resolution.hit), [true, false, null]);
  assert.ok(history.rows.every(row => Number.isFinite(Date.parse(row.recorded_utc))));
  const latest = (await read(db, 'latest_resolutions')).rows
    .map(row => payload(row)).filter(row => row.receipt_id === value.receipt_id || row.resolution?.receipt_id === value.receipt_id);
  assert.equal(latest.length, 1);
  assert.equal((latest[0].resolution ?? latest[0]).status, 'VOID');
  const earlier = (await read(db, 'latest_resolutions', null, null, 500, originalWatermark)).rows
    .map(row => payload(row)).filter(row => row.resolution?.receipt_id === value.receipt_id);
  assert.equal(earlier.length, 1);
  assert.equal(earlier[0].revision_id, first.revision_id);
  assert.equal(earlier[0].resolution.hit, true);
});

test('resolution RPC rejects non-FINAL, ambiguous VOID, mismatched contracts and regressed evidence', async () => {
  const value = receipt('revision-invalid');
  await appendReceipt(db, value);
  for (const [label, update] of [
    ['live', { status: 'LIVE' }], ['numeric-hit', { hit: 1 }],
    ['missing-source', { source: '' }], ['wrong-event', { event_id: 'other-game' }],
    ['wrong-contract', { contract_id: digest({ other: true }) }],
    ['too-early', { resolved_utc: '2026-09-01T12:01:00Z' }],
    ['ambiguous-void', { status: 'VOID', hit: null }],
    ['void-loss', { status: 'VOID', hit: false, authoritative: true, final_evidence_status: 'FINAL', reason: 'fixture' }],
    ['source-after-resolution', { source_updated_utc: '2026-09-02T04:16:00Z' }],
    ['observed-before-resolution', { evidence_observed_utc: '2026-09-02T04:14:00Z' }],
    ['future-result', { resolved_utc: '2099-01-01T00:00:00Z' }],
  ]) {
    await rejectsCode(() => appendRevision(db, revision(value, 'invalid-' + label,
      { resolution: resolution(value, true, update) })), 'PT422');
  }
  assert.equal((await read(db, 'resolution_history', value.receipt_id)).rows.length, 0);
  const first = revision(value, 'time-v1');
  await appendRevision(db, first);
  await rejectsCode(() => appendRevision(db, revision(value, 'time-v2', {
    supersedes: first.revision_id, reason: 'regressed timestamp',
    resolution: resolution(value, false, { resolved_utc: '2026-09-02T03:00:00Z' }),
  })), 'PT422');
  const other = receipt('revision-other');
  await appendReceipt(db, other);
  await rejectsCode(() => appendRevision(db, revision(other, 'cross-receipt',
    { supersedes: first.revision_id, reason: 'wrong chain' })), ['PT422', 'PT409']);
});

test('database guards append-only history for service role and table owner', async () => {
  const value = receipt('append-only');
  await appendReceipt(db, value);
  await appendRevision(db, revision(value, 'append-only-v1'));
  for (const table of ['journal_receipts', 'journal_objects', 'journal_generation_commits', 'journal_revisions']) {
    const privileges = (await db.query(`
      SELECT has_table_privilege('service_role', $1, 'UPDATE') AS update,
        has_table_privilege('service_role', $1, 'DELETE') AS delete,
        has_table_privilege('service_role', $1, 'TRUNCATE') AS truncate
    `, ['selflearn_private.' + table])).rows[0];
    assert.deepEqual(privileges, { update: false, delete: false, truncate: false });
  }
  await asRole(db, 'service_role', async () => {
    await rejectsCode(() => db.query('DELETE FROM selflearn_private.journal_receipts'), '42501');
    await rejectsCode(() => db.query("UPDATE selflearn_private.journal_receipts SET payload_json = '{}'"), '42501');
  });
  // Superusers can intentionally disable triggers or alter schemas. These probes
  // verify ordinary owner SQL cannot accidentally rewrite append-only records.
  await assert.rejects(() => db.query('DELETE FROM selflearn_private.journal_receipts'), /immutable|append.only/i);
  await assert.rejects(() => db.query("UPDATE selflearn_private.journal_revisions SET payload_json = '{}'"), /immutable|append.only/i);
  assert.equal((await read(db, 'receipt', value.receipt_id)).rows[0].payload_json, canonical(value));
});

test('paged reads retain one watermark and do not include later appends', async () => {
  const before = await read(db, 'receipts', null, null, 1);
  assert.equal(before.rows.length, 1);
  assert.equal(typeof before.next_after, 'string');
  const later = receipt('after-watermark');
  await appendReceipt(db, later);
  const seen = [...before.rows];
  let cursor = before.next_after;
  let rounds = 0;
  while (cursor !== null) {
    const page = await read(db, 'receipts', null, cursor, 1, before.watermark);
    assert.equal(page.watermark, before.watermark);
    seen.push(...page.rows);
    cursor = page.next_after;
    assert.ok(++rounds < 1000, 'Pagination must terminate');
  }
  assert.equal(seen.some(row => payload(row).receipt_id === later.receipt_id), false);
  const sequence = seen.map(row => BigInt(row.journal_seq));
  assert.equal(new Set(sequence.map(String)).size, sequence.length);
  assert.ok(sequence.every((value, index) => index === 0 || sequence[index - 1] < value));
  assert.equal((await read(db, 'receipt', later.receipt_id)).rows.length, 1);
});

test('bounded read RPC refuses unknown views, unsafe cursors and excessive pages', async () => {
  for (const args of [
    ['unknown', null, null, 1, null],
    ['receipts', null, null, 0, null],
    ['receipts', null, null, 501, null],
    ['receipts', null, 'not-an-integer', 1, null],
    ['receipts', null, null, 1, '-1'],
  ]) {
    await rejectsCode(() => asRole(db, 'service_role', () => rpc(db, 'selflearn_journal_read_v1', args)), 'PT422');
  }
  assert.equal((await read(db, 'receipt', digest({ absent: 'receipt' }))).rows.length, 0);
  assert.equal((await read(db, 'object', digest({ absent: 'object' }))).rows.length, 0);
  assert.equal((await read(db, 'generation_ack', digest({ absent: 'generation' }))).rows.length, 0);
  const current = (await read(db, 'status')).watermark;
  const future = (BigInt(current) + 100n).toString();
  await rejectsCode(() => read(db, 'receipts', null, null, 1, future), 'PT422');
});
