// Test-only stdin/stdout bridge from the actual Python adapter to PostgreSQL
// WASM. No HTTP listener, secret, production URL or SQL emulation is involved.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { openDatabase, serviceRpc } from './harness.mjs';

const params = Object.freeze({
  selflearn_journal_append_receipt_v1: ['p_receipt_json'],
  selflearn_journal_append_object_v1: ['p_kind', 'p_object_id', 'p_payload_json', 'p_hash_json'],
  selflearn_journal_commit_generation_v1: ['p_object_id', 'p_receipts_json', 'p_rejections_json'],
  selflearn_journal_append_resolution_v1: ['p_revision_json'],
  selflearn_journal_read_v1: ['p_view', 'p_key', 'p_after', 'p_limit', 'p_watermark'],
});

const db = await openDatabase();
const lines = createInterface({ input: process.stdin, crlfDelay: Infinity });
const write = value => process.stdout.write(JSON.stringify(value) + '\n');

try {
  const version = (await db.query('SELECT version() AS value')).rows[0].value;
  write({ ready: true, engine: 'postgresql-wasm', version });
  for await (const line of lines) {
    let request;
    try {
      assert.ok(Buffer.byteLength(line, 'utf8') <= 16 * 1024 * 1024, 'Oversized test bridge request');
      request = JSON.parse(line);
      assert.equal(request.method, 'POST');
      assert.equal(typeof request.path, 'string');
      const name = request.path.replace(/^\/rest\/v1\/rpc\//, '');
      assert.ok(Object.hasOwn(params, name), 'Unknown RPC');
      assert.equal(request.path, '/rest/v1/rpc/' + name);
      assert.deepEqual(Object.keys(request.data).sort(), [...params[name]].sort(), 'Unexpected RPC arguments');
      const result = await serviceRpc(db, name, params[name].map(key => request.data[key]));
      write({ id: request.id ?? null, ok: true, result });
    } catch (error) {
      write({ id: request?.id ?? null, ok: false,
        code: typeof error.code === 'string' ? error.code : 'TEST_BRIDGE_INPUT',
        message: String(error.message) });
    }
  }
} finally {
  lines.close();
  await db.close();
}
