import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import {
  addMembers, applicationRequest, captureRequest, commitStep, commonFact, memberProof,
  openCaptureApplicationDatabase, registerApplication, resealCapture, sealApplication, stepPayload,
} from './capture_application_harness.mjs';
import { canonical, digest, rejectsCode, serviceRpc } from './harness.mjs';

const migration = new URL('../../supabase/migrations/20261118192717_nfl_roster_capture_provenance.sql', import.meta.url);
const rosterSource = 'nflverse.rosters';
const rosterProfile = 'nflverse_rosters_v1';
const joinPolicy = 'stable_player_ids_exact_fixture_v2';
const emptyBatch = { version: 1, observations: [], revisions: [] };
const upgrade = async (db) => db.exec(await readFile(migration, 'utf8'));

function withRoster(tag, weeks = [6]) {
  const capture = captureRequest(tag, weeks);
  const { envelope, raw_pointers: pointers } = capture;
  const raw_sha256 = digest({ roster: tag });
  const hour = envelope.manifest.sources.length + 1;
  const stamp = (h, minute) => `2026-09-03T${String(h).padStart(2, '0')}:${minute}:00Z`;
  const roster = {
    version: 1, kind: 'nfl.outcome_source_observation.v1', evidence_class: 'observed_source_snapshot',
    profile: rosterProfile, season: 2026, week: null, raw_sha256, raw_bytes: 1,
    source_url: 'https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_2026.csv',
    fetch_started_utc: stamp(hour, '00'), observed_utc: stamp(hour, '01'),
    upstream_updated_utc: null, http: { status: 200 },
    audit: { rows: 1, duplicate_rows: 0, outside_regular_week_rows: 0,
      measurement_policy: 'raw values retained; missing/negative cells are not coerced or graded' },
  };
  const observation_id = digest(roster);
  envelope.observations[observation_id] = roster;
  envelope.manifest.sources.push({ profile: rosterProfile, season: 2026, week: null, observation_id });
  envelope.manifest.completed_utc = stamp(hour + 1, '00');
  envelope.raw_objects.push({ raw_sha256, raw_bytes: 1 });
  envelope.raw_objects.sort((a, b) => a.raw_sha256.localeCompare(b.raw_sha256));
  pointers.push({ version: 1, kind: 'selflearn.evidence_object.v1', scope: 'uploaded_only',
    object_id: raw_sha256, raw_bytes: 1, chunks: [{ chunk_id: raw_sha256, raw_bytes: 1 }] });
  pointers.sort((a, b) => a.object_id.localeCompare(b.object_id));
  return resealCapture(capture);
}

function withRosterProof(member, capture) {
  const proof = memberProof(member, capture);
  const { observation, revision } = proof;
  const ref = capture.envelope.manifest.sources.find((source) => source.profile === rosterProfile);
  const raw = capture.envelope.observations[ref.observation_id];
  const component = { source: rosterSource, observation_id: ref.observation_id, observed_utc: raw.observed_utc };
  const final = capture.envelope.manifest.sources.find((source) =>
    source.profile === 'espn_scoreboard_v1' && source.week === observation.measurement.week);
  observation.source_observations['espn.scoreboard'] = {
    source: 'espn.scoreboard', observation_id: final.observation_id,
    observed_utc: capture.envelope.observations[final.observation_id].observed_utc,
  };
  observation.source_observations[rosterSource] = component;
  observation.observed_utc = component.observed_utc;
  observation.evidence_observation_id = digest({ measurement_id: observation.measurement_id, components: observation.source_observations });
  delete observation.observation_id;
  observation.observation_id = digest(observation);
  const value = revision.resolution;
  value.source_observations = value.observation_watermarks = observation.source_observations;
  value.resolved_utc = value.evidence_observed_utc = observation.observed_utc;
  value.evidence_observation_id = observation.evidence_observation_id;
  value.producer_trace = { identity_join_policy: joinPolicy, identity_crosswalk: {
    ...component, raw_sha256: raw.raw_sha256, player_id: member.key[2],
    gsis_id: '00-0080001', pfr_id: 'FictRe00', scope: 'postgame_identity_only',
  } };
  revision.revision_id = value.evidence_basis + ':' + digest({
    receipt_id: value.receipt_id, observation_id: value.evidence_observation_id, supersedes: null,
  });
  return proof;
}

function binding(capture, groups = []) {
  return { version: 1, kind: 'nfl.capture_delivery_binding.v1', season: 2026, weeks: capture.envelope.manifest.weeks,
    membership_id: digest({ binding_policy: capture.binding_policy, groups: groups.map((group) => group.membership_id) }), groups };
}

const deliver = (db, capture, bind, batch, watermark) =>
  serviceRpc(db, 'selflearn_journal_commit_capture_delivery_v1', [canonical(capture), canonical(bind), canonical(batch), watermark]);

test('additive migration preserves legacy V1 ACK and commits 21-source roster evidence', async () => {
  const db = await openCaptureApplicationDatabase();
  try {
    const legacy = captureRequest('original-legacy');
    const original = await deliver(db, legacy, binding(legacy), emptyBatch, '0');
    await upgrade(db);
    const [member] = await addMembers(db, 1, { player: 'espn-101' });
    assert.deepEqual(await deliver(db, legacy, binding(legacy), emptyBatch, '0'), original);

    const capture = withRoster('full-season', Array.from({ length: 18 }, (_, i) => i + 1));
    assert.equal(capture.raw_pointers.length, 21);
    const proof = withRosterProof(member, capture);
    const request = applicationRequest(capture);
    const seal = await sealApplication(db, request, { facts: { 'espn-101': commonFact(proof) } });
    const payload = stepPayload(seal, 0, [proof]);
    const stale = structuredClone(payload.batch);
    stale.revisions[0].resolution.producer_trace.identity_crosswalk.raw_sha256 = digest('unrelated roster');
    await rejectsCode(() => deliver(db, capture, binding(capture, payload.groups), stale, seal.source_watermark), 'PT422');
    const committed = await deliver(db, capture, binding(capture, payload.groups), payload.batch, seal.source_watermark);
    assert.equal(committed.added_observations, 1);
    assert.equal(committed.added_revisions, 1);
    assert.deepEqual(await deliver(db, capture, binding(capture, payload.groups), payload.batch, seal.source_watermark), committed);
    const { rows: [stored] } = await db.query(
      'SELECT payload_json::jsonb AS value FROM selflearn_private.journal_revisions WHERE receipt_id=$1', [member.generic.receipt_id],
    );
    assert.deepEqual(stored.value.resolution.producer_trace.identity_crosswalk,
      proof.revision.resolution.producer_trace.identity_crosswalk);
    assert.deepEqual(stored.value.resolution.observation_watermarks[rosterSource],
      proof.observation.source_observations[rosterSource]);
  } finally {
    await db.close();
  }
});

test('V2 binds sealed facts and durable revisions to the roster observation', async () => {
  const db = await openCaptureApplicationDatabase();
  try {
    await upgrade(db);
    const [member] = await addMembers(db, 1, { player: 'espn-101' });
    const capture = withRoster('application');
    const request = applicationRequest(capture);
    const unqualified = commonFact(memberProof(member, capture));
    const unqualifiedSeal = await sealApplication(db, request, { facts: { 'espn-101': unqualified } });
    await rejectsCode(() => registerApplication(db, request, unqualifiedSeal), 'PT422');

    const proof = withRosterProof(member, capture);
    const seal = await sealApplication(db, request, { facts: { 'espn-101': commonFact(proof) } });
    const registered = await registerApplication(db, request, seal);
    const payload = stepPayload(seal, 0, [proof]);
    const wrongPlayer = structuredClone(payload);
    wrongPlayer.batch.revisions[0].resolution.producer_trace.identity_crosswalk.player_id = 'espn-999';
    await rejectsCode(() => commitStep(db, request, seal, 0, wrongPlayer, registered.source_watermark), 'PT422');
    const committed = await commitStep(db, request, seal, 0, payload, registered.source_watermark);
    assert.equal(committed.added_observations, 1);
    assert.equal(committed.added_revisions, 1);
    assert.deepEqual(await commitStep(db, request, seal, 0, payload, registered.source_watermark), committed);
    const { rows: [stored] } = await db.query(
      'SELECT payload_json::jsonb AS value FROM selflearn_private.journal_observations WHERE receipt_id=$1', [member.generic.receipt_id],
    );
    assert.equal(stored.value.observed_utc, proof.observation.source_observations[rosterSource].observed_utc);
    assert.deepEqual(stored.value.source_observations[rosterSource], proof.observation.source_observations[rosterSource]);
  } finally {
    await db.close();
  }
});
