import assert from 'node:assert/strict';
import { test } from 'node:test';
import { execFileSync } from 'node:child_process';
import {
  openObservedDatabase, canonical, digest, receipt, asRole, rpc, serviceRpc,
  appendReceipt, appendRevision, read, rejectsCode, payload,
  generation, appendGeneration, commitGeneration,
} from './harness.mjs';

const BASIS = 'nfl.observed_resolution_v2';
const GAME = 'espn.scoreboard';
const STAT = 'nflverse.stats_player';
const SNAP = 'nflverse.snap_counts';

function forecast(label) {
  const contract = [2026, 'G1', label, 'wr_rec_yds', '40', 'home', 'over', 'gte'];
  return { contract, value: receipt(label, {
    event_id: 'nfl:2026:game:G1', correlation_group: 'nfl:2026:game:G1', contract_id: digest(contract),
  }) };
}

function observation(forecast, tag = 'first', actual = 80, clock = '2026-09-02T04:00:00Z', options = {}) {
  const components = {};
  for (const source of [GAME, STAT, ...(options.includeSnaps ? [SNAP] : [])]) {
    const observed_utc = options.clocks?.[source] ?? clock;
    components[source] = { source, observed_utc,
      observation_id: options.ids?.[source] ?? digest({ source, tag, observed_utc }) };
  }
  if (actual === null && !components[SNAP]) {
    components[SNAP] = { source: SNAP, observed_utc: clock, observation_id: digest({ source: SNAP, tag, clock }) };
    delete components[STAT];
  }
  const measurement = {
    version: 1, canonical_contract: forecast.contract, week: 6, actual,
    played: actual !== null, status: actual === null ? 'VOID' : 'FINAL',
    hit: actual === null ? null : actual >= Number(forecast.contract[4]), reason: actual === null ? 'did_not_play' : null,
    participation_basis: actual === null ? 'r106_team_snap_absence_inference' : 'stat_row',
    game: { season: 2026, week: 6, game_id: 'G1', kickoff_utc: forecast.value.event_start_utc,
      home: 'SEA', away: 'LAR', state: 'FINAL' },
  };
  const measurement_id = digest(measurement);
  const unsigned = {
    version: 1, kind: 'nfl.observed_measurement.v1', evidence_basis: BASIS,
    receipt_id: forecast.value.receipt_id, contract_id: forecast.value.contract_id, event_id: forecast.value.event_id,
    measurement, measurement_id, source_observations: components,
    evidence_observation_id: digest({ measurement_id, components }),
    observed_utc: Object.values(components).map(c => c.observed_utc).sort().at(-1),
  };
  return { ...unsigned, observation_id: digest(unsigned) };
}

function revision(record, previous = null, carried = {}) {
  const resolution = {
    receipt_id: record.receipt_id, contract_id: record.contract_id, event_id: record.event_id,
    evidence_basis: BASIS, measurement_id: record.measurement_id, measurement: record.measurement,
    evidence_observation_id: record.evidence_observation_id, source_observations: record.source_observations,
    observation_watermarks: { ...carried, ...record.source_observations },
    resolved_utc: record.observed_utc, evidence_observed_utc: record.observed_utc,
    status: record.measurement.status, hit: record.measurement.hit, actual: record.measurement.actual,
    reason: record.measurement.reason, participation_basis: record.measurement.participation_basis,
    source: record.measurement.status === 'VOID' ? SNAP : STAT,
    authoritative: true, final_evidence_status: 'FINAL',
  };
  return { version: 1, supersedes: previous, reason: previous ? 'new captured measurement' : null, resolution,
    revision_id: BASIS + ':' + digest({ receipt_id: record.receipt_id,
      observation_id: record.evidence_observation_id, supersedes: previous }) };
}

function reseal(record) {
  record.measurement_id = digest(record.measurement);
  record.evidence_observation_id = digest({ measurement_id: record.measurement_id, components: record.source_observations });
  const { observation_id, ...unsigned } = record;
  return { ...unsigned, observation_id: digest(unsigned) };
}

const readObserved = (db, view, key = null, after = null, limit = 500, watermark = null) =>
  serviceRpc(db, 'selflearn_journal_read_observed_v1', [view, key, after, limit, watermark]);
const fence = async db => (await readObserved(db, 'status')).watermark;
const commit = (db, observations, revisions, watermark) => serviceRpc(db,
  'selflearn_journal_commit_observed_v1', [canonical({ version: 1, observations, revisions }), watermark]);

function databaseTest(name, callback) {
  test(name, async () => {
    const db = await openObservedDatabase();
    try { await callback(db); } finally { await db.close(); }
  });
}

databaseTest('additive observed migration uses real PostgreSQL, private RLS and invoker-only privileges', async db => {
  assert.match((await db.query('SELECT version() AS v')).rows[0].v, /PostgreSQL 17/);
  const table = (await db.query(`SELECT relrowsecurity,relforcerowsecurity FROM pg_class
    WHERE oid='selflearn_private.journal_observations'::regclass`)).rows[0];
  assert.deepEqual(table, { relrowsecurity: true, relforcerowsecurity: true });
  const functions = await db.query(`SELECT proname,prosecdef FROM pg_proc WHERE proname IN
    ('selflearn_journal_commit_observed_v1','selflearn_journal_read_observed_v1')`);
  assert.equal(functions.rows.length, 2);
  assert.ok(functions.rows.every(row => row.prosecdef === false));
  for (const role of ['anon', 'authenticated']) {
    await rejectsCode(() => asRole(db, role, () => rpc(db, 'selflearn_journal_read_observed_v1', ['status', null, null, 1, null])), '42501');
    await rejectsCode(() => asRole(db, role, () => rpc(db, 'selflearn_journal_commit_observed_v1', [canonical({ version: 1, observations: [], revisions: [] }), '0'])), '42501');
    await rejectsCode(() => asRole(db, role, () => db.query('SELECT * FROM selflearn_private.journal_observations')), '42501');
  }
  assert.equal((await read(db, 'status')).rows[0].schema_version, 1, 'old read remains unchanged');
});

databaseTest('atomic receipt proof/outcome commit, exact replay, immutable DB clocks and keyset enumeration', async db => {
  const a = forecast('round-trip'); await appendReceipt(db, a.value);
  const proof = observation(a); const result = revision(proof); const before = await fence(db);
  const ack = await commit(db, [proof], [result], before);
  assert.deepEqual({ ...ack, watermark: 'new' }, { version: 1, added_observations: 1, added_revisions: 1, watermark: 'new' });
  assert.ok(BigInt(ack.watermark) > BigInt(before));
  const rows = await readObserved(db, 'observations');
  assert.equal(rows.watermark, ack.watermark);
  assert.equal(rows.rows.length, 1);
  assert.deepEqual(payload(rows.rows[0]), proof);
  assert.deepEqual(Object.keys(rows.rows[0]).sort(), ['journal_seq', 'observation_id', 'payload_json', 'receipt_id', 'recorded_utc']);
  assert.ok(Date.parse(rows.rows[0].recorded_utc) > Date.parse(proof.observed_utc), 'clock is DB insertion time, not caller time');
  const replay = await commit(db, [proof], [result], ack.watermark);
  assert.deepEqual(replay, { version: 1, added_observations: 0, added_revisions: 0, watermark: ack.watermark });
  assert.deepEqual((await readObserved(db, 'observations')).rows, rows.rows);
  assert.deepEqual((await readObserved(db, 'observation_history', a.value.receipt_id)).rows, rows.rows);
  for (const statement of ['UPDATE selflearn_private.journal_observations SET payload_json=payload_json',
    'DELETE FROM selflearn_private.journal_observations', 'TRUNCATE selflearn_private.journal_observations']) {
    await rejectsCode(() => db.exec(statement), 'PT409');
  }
  await rejectsCode(() => asRole(db, 'service_role', () => db.query(
    'UPDATE selflearn_private.journal_observations SET stored_utc=now()')), '42501');
});

databaseTest('legacy generation commits and offered cohorts remain readable at the new complete fence', async db => {
  const a = forecast('generation-a'), b = forecast('generation-b');
  const document = generation('observed-compatibility', [a.value.source_receipt_id,b.value.source_receipt_id]);
  await appendGeneration(db, document);
  const oldAck = await commitGeneration(db, document.envelope_id, [a.value,b.value], []);
  assert.equal(oldAck.durable, true);
  const proof = observation(a); await commit(db, [proof], [revision(proof)], await fence(db));
  await commit(db, [observation(a, 'confirmation', 80, '2026-09-02T06:00:00Z')], [], await fence(db));
  const current = await fence(db);
  const records = await readObserved(db, 'generations', null, null, 1, current);
  assert.equal(records.rows.length, 1); assert.equal(records.watermark, current);
  assert.deepEqual(payload(records.rows[0]), document);
  assert.deepEqual(JSON.parse(records.rows[0].ack_json), oldAck);
  assert.equal((await readObserved(db, 'receipts', null, null, 500, current)).rows.length, 2);
  assert.deepEqual((await read(db, 'generations', null, null, 1)).rows, records.rows,
    'additive reader preserves the old generation payload, cohort and ACK bytes');
});

databaseTest('stale global CAS rejects before writes even when an unrelated legacy writer advanced', async db => {
  const a = forecast('cas-a'); await appendReceipt(db, a.value);
  const before = await fence(db); const proof = observation(a);
  await appendReceipt(db, forecast('cas-unrelated').value);
  const current = await fence(db);
  await rejectsCode(() => commit(db, [proof], [revision(proof)], before), 'PT409');
  assert.equal((await readObserved(db, 'observations')).rows.length, 0);
  assert.equal((await readObserved(db, 'revisions')).rows.length, 0);
  assert.equal(await fence(db), current);
  await commit(db, [proof], [revision(proof)], current);
});

databaseTest('a later invalid revision rolls back every observation and earlier revision in its batch', async db => {
  const a = forecast('rollback-a'), b = forecast('rollback-b');
  await appendReceipt(db, a.value); await appendReceipt(db, b.value);
  const proofs = [observation(a), observation(b)]; const revisions = proofs.map(p => revision(p));
  revisions[1].resolution.contract_id = digest({ wrong: true });
  const before = await fence(db);
  await rejectsCode(() => commit(db, proofs, revisions, before), 'PT422');
  assert.equal((await readObserved(db, 'observations')).rows.length, 0);
  assert.equal((await readObserved(db, 'revisions')).rows.length, 0);
  assert.equal(await fence(db), before, 'rolled-back sequence gaps are not committed watermarks');
  revisions[1] = revision(proofs[1]);
  assert.equal((await commit(db, proofs, revisions, before)).added_revisions, 2);
});

databaseTest('canonical hashes, duplicated fields, receipt binding and future client clocks are rejected', async db => {
  const a = forecast('invalid'); await appendReceipt(db, a.value);
  const proof = observation(a); const before = await fence(db);
  for (const mutate of [p => { p.observation_id = digest({ wrong: true }); },
    p => { p.contract_id = digest({ wrong: true }); }, p => { p.observed_utc = '2099-01-01T00:00:00Z'; },
    p => { p.measurement.actual = 10; }]) {
    const changed = structuredClone(proof); mutate(changed);
    await rejectsCode(() => commit(db, [changed], [revision(changed)], before), 'PT422');
  }
  const duplicate = canonical({ version: 1, observations: [], revisions: [] }).replace('"version":1', '"version":1,"version":1');
  await rejectsCode(() => serviceRpc(db, 'selflearn_journal_commit_observed_v1', [duplicate, before]), 'PT422');
  for (const change of [p => { p.measurement.version = '1'; }, p => { p.measurement.version = true; },
    p => { p.measurement.participation_basis = null; }, p => { p.measurement.participation_basis = 1; },
    p => { p.measurement.participation_basis = ''; }]) {
    const changed = structuredClone(proof); change(changed); const sealed = reseal(changed);
    await rejectsCode(() => commit(db, [sealed], [revision(sealed)], before), 'PT422');
  }
  assert.equal(await fence(db), before);
});

databaseTest('all canonical-key types and exact .12g line identity are checked, not coerced after hashing', async db => {
  const invalid = [
    [0, '2026'], [0, 2026.5], [1, null], [1, ''], [2, null], [2, ''], [3, null], [3, 'unsupported_market'],
    [4, 40], [4, null], [4, '59.500'], [4, '4e1'], [4, '04'], [4, '0'], [4, 'Infinity'], [4, 'NaN'],
    [5, null], [5, 1], [5, 'middle'], [6, null], [7, null],
  ];
  for (let i = 0; i < invalid.length; i++) {
    const [position, value] = invalid[i]; const a = forecast('typed-key-' + i);
    a.contract[position] = value;
    a.value = receipt('typed-key-' + i, { contract_id: digest(a.contract), event_id: 'nfl:2026:game:G1' });
    await appendReceipt(db, a.value);
    const proof = observation(a);
    if (position === 0) proof.measurement.game.season = value;
    const sealed = reseal(proof);
    const before = await fence(db);
    await rejectsCode(() => commit(db, [sealed], [revision(sealed)], before), 'PT422');
  }
});

databaseTest('hash-valid integers beyond finite-double range cannot create unreadable stored outcomes', async db => {
  const a = forecast('huge-integer'); await appendReceipt(db, a.value);
  const proof = observation(a); const rev = revision(proof);
  const raw = execFileSync('python3', ['-c', `
import json,hashlib,sys
d=json.load(sys.stdin);p=d['proof'];r=d['revision']
def canonical(v):return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False)
def digest(v):return 'sha256:'+hashlib.sha256(canonical(v).encode()).hexdigest()
p['measurement']['actual']=10**400
p['measurement_id']=digest(p['measurement'])
p['evidence_observation_id']=digest({'measurement_id':p['measurement_id'],'components':p['source_observations']})
p['observation_id']=digest({k:v for k,v in p.items() if k!='observation_id'})
for k in ('measurement','measurement_id','evidence_observation_id'):r['resolution'][k]=p[k]
r['resolution']['actual']=10**400
r['revision_id']='nfl.observed_resolution_v2:'+digest({'receipt_id':p['receipt_id'],
 'observation_id':p['evidence_observation_id'],'supersedes':None})
print(canonical({'version':1,'observations':[p],'revisions':[r]}))
`], { encoding: 'utf8', input: JSON.stringify({ proof, revision: rev }) }).trim();
  const before = await fence(db);
  await rejectsCode(() => serviceRpc(db, 'selflearn_journal_commit_observed_v1', [raw,before]), 'PT422');
  assert.equal(await fence(db), before);
  assert.equal((await readObserved(db, 'observations')).rows.length, 0);
});

databaseTest('SQL .12g formatter matches independent Python across seeded float/tie/exponent boundaries without GUC leaks', async db => {
  const oracle = JSON.parse(execFileSync('python3', ['-c', `
import json,math,random
r=random.Random(20261015)
values=[59.5,40.,1e-5,1e-4,1e11,1e12,1.234567890125,999999999999.5,
        math.nextafter(1e-4,0),math.nextafter(1e12,0),5e-324,1.7976931348623157e308]
for _ in range(700):values.append(math.ldexp(r.random()+.5,r.randrange(-1020,1023)))
for value in [1.234567890125,999999999999.5,1e-4,1e12]:
 values.extend([math.nextafter(value,0),math.nextafter(value,math.inf)])
print(json.dumps([[repr(v),format(v,'.12g')] for v in values if math.isfinite(v) and v>0]))
`], { encoding: 'utf8' }));
  const before = (await db.query('SHOW extra_float_digits')).rows[0].extra_float_digits;
  for (const [value, expected] of oracle) {
    const actual = await asRole(db, 'service_role', () => db.query('SELECT selflearn_private.nfl_line_token_v1($1) token', [value]));
    assert.equal(actual.rows[0].token, expected, value);
  }
  assert.equal((await db.query('SHOW extra_float_digits')).rows[0].extra_float_digits, before);
});

databaseTest('unchanged confirmations advance durable fences without changing outcome history or clocks', async db => {
  const a = forecast('unchanged'); await appendReceipt(db, a.value);
  const first = observation(a, 'first'); const firstRevision = revision(first);
  await commit(db, [first], [firstRevision], await fence(db));
  const old = await readObserved(db, 'revisions');
  const confirmed = observation(a, 'unchanged', 80, '2026-09-02T06:00:00Z');
  const ack = await commit(db, [confirmed], [], await fence(db));
  assert.equal(ack.added_observations, 1); assert.equal(ack.added_revisions, 0);
  assert.deepEqual((await readObserved(db, 'revisions')).rows, old.rows);
  const stale = observation(a, 'stale-change', 20, '2026-09-02T05:00:00Z');
  await rejectsCode(() => commit(db, [stale], [revision(stale, firstRevision.revision_id)], ack.watermark), 'PT409');
  assert.equal((await readObserved(db, 'observations')).rows.length, 2);
  assert.equal((await readObserved(db, 'latest_resolutions')).rows[0].revision_id, firstRevision.revision_id);
});

databaseTest('legacy append RPC cannot ACK missing indexed proof or stale proof behind unchanged fences', async db => {
  const a = forecast('legacy-bypass'); await appendReceipt(db, a.value);
  const first = observation(a, 'first'); const firstRevision = revision(first);
  await rejectsCode(() => appendRevision(db, firstRevision), 'PT422');
  await commit(db, [first], [firstRevision], await fence(db));
  const six = observation(a, 'six', 80, '2026-09-02T06:00:00Z');
  const seven = observation(a, 'seven', 80, '2026-09-02T07:00:00Z');
  await commit(db, [six], [], await fence(db));
  await commit(db, [seven], [], await fence(db));
  await rejectsCode(() => appendRevision(db, revision(six, firstRevision.revision_id)), 'PT409');
  await rejectsCode(() => appendRevision(db, revision(seven, firstRevision.revision_id)), 'PT409',
    'a latest unchanged proof cannot manufacture a new outcome revision/time');
  assert.equal((await readObserved(db, 'revisions')).rows.length, 1);
});

databaseTest('legacy revisions cannot downgrade indexed observed receipts, including before their first outcome', async db => {
  const a = forecast('no-legacy-downgrade'); await appendReceipt(db, a.value);
  const proof = observation(a); const original = revision(proof);
  await commit(db, [proof], [original], await fence(db));
  const legacy = { ...original, revision_id: 'legacy-bypass', supersedes: original.revision_id,
    reason: 'attempted legacy downgrade', resolution: { ...original.resolution, evidence_basis: 'legacy.v1',
      hit: false, actual: 20, resolved_utc: '2026-09-02T08:00:00Z', evidence_observed_utc: '2026-09-02T08:00:00Z' } };
  await rejectsCode(() => appendRevision(db, legacy), 'PT409');
  assert.equal((await readObserved(db, 'latest_resolutions')).rows[0].revision_id, original.revision_id);

  const b = forecast('indexed-before-first-outcome'); await appendReceipt(db, b.value);
  const pendingProof = observation(b);
  const initialLegacy = { version: 1, revision_id: 'legacy-before-first', supersedes: null, reason: null,
    resolution: { receipt_id: b.value.receipt_id, contract_id: b.value.contract_id, event_id: b.value.event_id,
      status: 'FINAL', hit: true, source: 'legacy-only fixture', resolved_utc: '2026-09-02T04:00:00Z' } };
  await db.exec('BEGIN; SET LOCAL ROLE service_role');
  try {
    await db.query(`INSERT INTO selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json)
      VALUES($1,$2,$3,$4)`, [pendingProof.observation_id,pendingProof.receipt_id,pendingProof.evidence_observation_id,canonical(pendingProof)]);
    await rejectsCode(() => rpc(db, 'selflearn_journal_append_resolution_v1', [canonical(initialLegacy)]), 'PT409');
  } finally { await db.exec('ROLLBACK'); }
  assert.deepEqual((await readObserved(db, 'observation_history', b.value.receipt_id)).rows, []);

  const c = forecast('unrelated-legacy-only'); await appendReceipt(db, c.value);
  const legacyOnly = { ...initialLegacy, revision_id: 'unrelated-legacy-first', resolution: {
    ...initialLegacy.resolution, receipt_id: c.value.receipt_id, contract_id: c.value.contract_id, event_id: c.value.event_id } };
  assert.equal((await appendRevision(db, legacyOnly)).added, true);
  const correction = { ...legacyOnly, revision_id: 'unrelated-legacy-corrected', supersedes: legacyOnly.revision_id,
    reason: 'legacy correction', resolution: { ...legacyOnly.resolution, hit: false, resolved_utc: '2026-09-02T06:00:00Z' } };
  assert.equal((await appendRevision(db, correction)).added, true);
  assert.equal((await appendRevision(db, legacyOnly)).added, false, 'exact old legacy retry remains idempotent');
});

databaseTest('new historical measurement confirmations need a current correction, while exact old retries do not revert', async db => {
  const a = forecast('historical'); await appendReceipt(db, a.value);
  const first = observation(a, 'A'); const firstRevision = revision(first);
  await commit(db, [first], [firstRevision], await fence(db));
  const changed = observation(a, 'B', 20, '2026-09-02T06:00:00Z'); const correction = revision(changed, firstRevision.revision_id);
  await commit(db, [changed], [correction], await fence(db));
  const before = await fence(db);
  const restored = observation(a, 'new-A', 80, '2026-09-02T08:00:00Z');
  await rejectsCode(() => commit(db, [restored], [], before), 'PT422');
  assert.equal(await fence(db), before);
  const retry = await commit(db, [first], [firstRevision], before);
  assert.equal(retry.added_observations, 0); assert.equal(retry.added_revisions, 0);
  assert.equal((await readObserved(db, 'latest_resolutions')).rows[0].revision_id, correction.revision_id);
  const ack = await commit(db, [restored], [revision(restored, correction.revision_id)], before);
  assert.equal(ack.added_revisions, 1);
});

databaseTest('new observation-only global ceilings delegate old views and pin all keyset pages as-of', async db => {
  const a = forecast('fence-a'), b = forecast('fence-b');
  await appendReceipt(db, a.value); await appendReceipt(db, b.value);
  const initial = [observation(a), observation(b)];
  await commit(db, initial, initial.map(p => revision(p)), await fence(db));
  const firstPage = await readObserved(db, 'observations', null, null, 1);
  assert.equal(firstPage.rows.length, 1); assert.equal(typeof firstPage.next_after, 'string');
  await commit(db, [observation(a, 'later', 80, '2026-09-02T06:00:00Z')], [], await fence(db));
  const current = await fence(db);
  const oldSecond = await readObserved(db, 'observations', null, firstPage.next_after, 1, firstPage.watermark);
  assert.equal(oldSecond.rows.length, 1); assert.equal(oldSecond.next_after, null);
  assert.equal(oldSecond.watermark, firstPage.watermark);
  assert.equal((await readObserved(db, 'observations')).rows.length, 3);
  assert.equal((await readObserved(db, 'receipts', null, null, 500, current)).rows.length, 2);
  const end = await readObserved(db, 'receipts', null, current, 500, current);
  assert.deepEqual(end.rows, []); assert.equal(end.watermark, current);
  for (const view of ['revisions', 'latest_resolutions', 'receipts']) {
    assert.equal((await readObserved(db, view, null, null, 500, firstPage.watermark)).watermark, firstPage.watermark);
  }
  await rejectsCode(() => readObserved(db, 'observations', null, null, 501), 'PT422');
  await rejectsCode(() => readObserved(db, 'observations', null, null, 1, (BigInt(current) + 1n).toString()), 'PT422');
});

databaseTest('VOID corrections preserve omitted source fences and reject source clock mutation', async db => {
  const a = forecast('void'); await appendReceipt(db, a.value);
  const first = observation(a, 'first', 80, '2026-09-02T04:00:00Z', { includeSnaps: true });
  const original = revision(first); await commit(db, [first], [original], await fence(db));
  const changed = observation(a, 'void', null, '2026-09-02T06:00:00Z');
  const missing = revision(changed, original.revision_id);
  await rejectsCode(async () => commit(db, [changed], [missing], await fence(db)), 'PT409');
  const ack = await commit(db, [changed], [revision(changed, original.revision_id, first.source_observations)], await fence(db));
  assert.equal(ack.added_revisions, 1);
  const latest = payload((await readObserved(db, 'latest_resolutions')).rows[0]).resolution;
  assert.equal(latest.status, 'VOID'); assert.equal(latest.hit, null);
  assert.deepEqual(latest.observation_watermarks[STAT], first.source_observations[STAT]);
  const mutated = observation(a, 'mutated', null, '2026-09-02T08:00:00Z', {
    ids: { [GAME]: changed.source_observations[GAME].observation_id },
  });
  await rejectsCode(() => commit(db, [mutated], [], ack.watermark), 'PT409');
});

databaseTest('carried outcome-chain fences remain distinct from newer unchanged observation-index fences', async db => {
  const a = forecast('carried-vs-index'); await appendReceipt(db, a.value);
  const firstVoid = observation(a, 'void-four', null, '2026-09-02T04:00:00Z');
  const first = revision(firstVoid); await commit(db, [firstVoid], [first], await fence(db));
  const unchangedVoid = observation(a, 'void-six', null, '2026-09-02T06:00:00Z');
  await commit(db, [unchangedVoid], [], await fence(db));
  const finalSeven = observation(a, 'final-seven', 80, '2026-09-02T07:00:00Z');
  const final = revision(finalSeven, first.revision_id, first.resolution.observation_watermarks);
  assert.equal(final.resolution.observation_watermarks[SNAP].observed_utc, '2026-09-02T04:00:00Z');
  const ack = await commit(db, [finalSeven], [final], await fence(db));
  assert.equal(ack.added_observations, 1); assert.equal(ack.added_revisions, 1);
  const all = (await readObserved(db, 'observations')).rows.map(payload);
  assert.equal(all.find(p => p.observation_id === unchangedVoid.observation_id).source_observations[SNAP].observed_utc,
    '2026-09-02T06:00:00Z', 'new unchanged index fence is still durable');
  assert.equal(payload((await readObserved(db, 'latest_resolutions')).rows[0]).resolution.observation_watermarks[SNAP].observed_utc,
    '2026-09-02T04:00:00Z', 'frozen v2 outcome-chain semantics are unchanged');
  const staleVoid = observation(a, 'stale-void-five', null, '2026-09-02T05:00:00Z');
  await rejectsCode(() => commit(db, [staleVoid], [revision(staleVoid, final.revision_id,
    final.resolution.observation_watermarks)], ack.watermark), 'PT409');
});
