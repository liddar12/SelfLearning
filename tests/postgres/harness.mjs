import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { isAbsolute, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';

export const migration = new URL('../../supabase/migrations/20261015222425_receipt_journal_v1.sql', import.meta.url);
export const observedMigration = new URL('../../supabase/migrations/20261015233134_observed_journal_v1.sql', import.meta.url);

// The test dependency is supplied explicitly. Never download a package, use an
// unpinned CDN, open a socket, or silently replace PostgreSQL with SQLite.
export async function openDatabase() {
  const configured = process.env.SELFLEARN_PGLITE_MODULE;
  if (!configured) {
    throw new Error('Set SELFLEARN_PGLITE_MODULE to an already installed @electric-sql/pglite module; no database tests were run.');
  }
  const specifier = configured.startsWith('file:') ? configured
    : isAbsolute(configured) || configured.startsWith('.')
      ? pathToFileURL(resolve(configured)).href : configured;
  const { PGlite } = await import(specifier);
  assert.equal(typeof PGlite, 'function', 'Configured module must export PGlite');
  const sql = await readFile(migration, 'utf8');
  assert.ok(sql.trim(), 'Receipt journal migration must contain actual SQL');
  const db = new PGlite();
  try {
    await db.exec(`
      CREATE ROLE anon NOLOGIN;
      CREATE ROLE authenticated NOLOGIN;
      CREATE ROLE service_role NOLOGIN BYPASSRLS;
    `);
    await db.exec(sql);
    return db;
  } catch (error) {
    await db.close();
    throw error;
  }
}

export async function openObservedDatabase() {
  const db = await openDatabase();
  try {
    const sql = await readFile(observedMigration, 'utf8');
    assert.ok(sql.trim(), 'Observed journal migration must contain actual SQL');
    await db.exec(sql);
    return db;
  } catch (error) {
    await db.close();
    throw error;
  }
}

// These fixtures deliberately use only integer numbers or non-integral finite
// floats. JS JSON serialization then matches Python's canonical encoding. In
// particular, the test helper must not claim to normalize arbitrary Python 1.0
// payloads to their original byte representation.
export function canonical(value) {
  function escaped(text) {
    return JSON.stringify(text).replace(/[\u007f-\uffff]/g,
      letter => '\\u' + letter.charCodeAt(0).toString(16).padStart(4, '0'));
  }
  function encode(item) {
    if (item === null || typeof item === 'boolean') return JSON.stringify(item);
    if (typeof item === 'string') return escaped(item);
    if (typeof item === 'number') {
      assert.ok(Number.isFinite(item), 'Non-finite JSON number');
      return JSON.stringify(item);
    }
    if (Array.isArray(item)) return '[' + item.map(encode).join(',') + ']';
    assert.equal(typeof item, 'object', 'Unsupported JSON value');
    return '{' + Object.keys(item).sort().map(key => escaped(key) + ':' + encode(item[key])).join(',') + '}';
  }
  return encode(value);
}

export function digest(value) {
  return digestText(canonical(value));
}

export const digestText = text => 'sha256:' + createHash('sha256').update(text, 'utf8').digest('hex');

export function receipt(label, extra = {}) {
  const value = {
    version: 1,
    domain: 'nfl',
    task: 'nfl.parlay_leg',
    event_id: 'fixture-game-' + label,
    correlation_group: 'fixture-game-' + label,
    contract_id: digest({ contract: label }),
    source_receipt_id: digest({ source: label }),
    source_snapshot_id: digest({ snapshot: label }),
    base_model_version: 'fixture-base-v1',
    calibrator_version: 'identity',
    base_prob: 0.7,
    served_prob: 0.7,
    features: { fixture: true, line: 175 },
    forecast_created_utc: '2026-09-01T12:00:00Z',
    first_seen_utc: '2026-09-01T12:01:00Z',
    event_start_utc: '2026-09-02T00:15:00Z',
    source_timestamps: { fixture: '2026-09-01T11:59:00Z' },
    publication_status: 'generated_unverified',
    ...extra,
  };
  delete value.receipt_id;
  return { ...value, receipt_id: digest(value) };
}

export function resolution(value, hit = true, extra = {}) {
  return {
    receipt_id: value.receipt_id,
    contract_id: value.contract_id,
    event_id: value.event_id,
    status: 'FINAL',
    hit,
    source: 'official-fixture',
    resolved_utc: '2026-09-02T04:15:00Z',
    ...extra,
  };
}

export function revision(value, id, options = {}) {
  return {
    version: 1,
    revision_id: id,
    supersedes: options.supersedes ?? null,
    reason: options.reason ?? null,
    resolution: options.resolution ?? resolution(value),
  };
}

export function generation(label, sourceIds) {
  const unsigned = {
    version: 1,
    season: 2026,
    week: 6,
    fixture: label,
    receipts: sourceIds.map(receipt_id => ({ receipt_id })),
    generations: [],
  };
  return { ...unsigned, envelope_id: digest(unsigned) };
}

export async function asRole(db, role, callback) {
  assert.ok(['anon', 'authenticated', 'service_role', 'postgres'].includes(role));
  await db.exec(`SET ROLE ${role}`);
  try {
    return await callback();
  } finally {
    await db.exec('RESET ROLE');
  }
}

export async function rpc(db, name, args = []) {
  assert.match(name, /^selflearn_journal_[a-z_]+_v1$/);
  const params = args.map((_, i) => '$' + (i + 1)).join(',');
  const result = await db.query(`SELECT public.${name}(${params}) AS result`, args);
  assert.equal(result.rows.length, 1);
  return result.rows[0].result;
}

export async function serviceRpc(db, name, args = []) {
  return asRole(db, 'service_role', () => rpc(db, name, args));
}

export const appendReceipt = (db, value) => serviceRpc(db, 'selflearn_journal_append_receipt_v1', [canonical(value)]);
export const appendRevision = (db, value) => serviceRpc(db, 'selflearn_journal_append_resolution_v1', [canonical(value)]);

export function appendObject(db, kind, id, payload, hashPayload = payload) {
  return serviceRpc(db, 'selflearn_journal_append_object_v1',
    [kind, id, canonical(payload), canonical(hashPayload)]);
}

export function appendGeneration(db, document) {
  const { envelope_id, ...unsigned } = document;
  return appendObject(db, 'generation', envelope_id, document, unsigned);
}

export function commitGeneration(db, id, accepted, rejected) {
  return serviceRpc(db, 'selflearn_journal_commit_generation_v1',
    [id, canonical(accepted), canonical(rejected)]);
}

export async function read(db, view, key = null, after = null, limit = 500, watermark = null) {
  const result = await serviceRpc(db, 'selflearn_journal_read_v1', [view, key, after, limit, watermark]);
  assert.equal(result.version, 1);
  assert.ok(Array.isArray(result.rows), 'Read RPC must return a rows array');
  assert.ok(result.next_after === null || typeof result.next_after === 'string');
  assert.equal(typeof result.watermark, 'string');
  return result;
}

export async function rejectsCode(callback, codes) {
  const expected = Array.isArray(codes) ? codes : [codes];
  await assert.rejects(callback, error => {
    assert.ok(expected.includes(error.code), `Expected SQLSTATE ${expected.join('/')} but received ${error.code}: ${error.message}`);
    return true;
  });
}

export function payload(row) {
  assert.equal(typeof row.payload_json, 'string', 'Stored evidence must retain exact canonical JSON text');
  return JSON.parse(row.payload_json);
}
