// Test-only actual PostgreSQL-WASM RPC bridge, never an HTTP server.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { openCaptureDeliveryDatabase } from './capture_delivery_harness.mjs';
import { serviceRpc } from './harness.mjs';

const params = Object.freeze({
  selflearn_journal_append_receipt_v1: ['p_receipt_json'],
  selflearn_journal_append_object_v1: ['p_kind','p_object_id','p_payload_json','p_hash_json'],
  selflearn_journal_commit_generation_v1: ['p_object_id','p_receipts_json','p_rejections_json'],
  selflearn_journal_append_resolution_v1: ['p_revision_json'],
  selflearn_journal_read_v1: ['p_view','p_key','p_after','p_limit','p_watermark'],
  selflearn_journal_read_observed_v1: ['p_view','p_key','p_after','p_limit','p_watermark'],
  selflearn_journal_commit_observed_v1: ['p_batch_json','p_watermark'],
  selflearn_journal_commit_capture_delivery_v1: ['p_request_json','p_binding_json','p_batch_json','p_watermark'],
  selflearn_journal_read_capture_delivery_v1: ['p_delivery_id','p_after','p_limit'],
});
const db = await openCaptureDeliveryDatabase();
const lines = createInterface({ input:process.stdin, crlfDelay:Infinity });
const write = value => process.stdout.write(JSON.stringify(value)+'\n');
try {
  write({ready:true,engine:'postgresql-wasm',version:(await db.query('SELECT version() AS value')).rows[0].value});
  for await (const line of lines) {
    let request;
    try {
      assert.ok(Buffer.byteLength(line,'utf8')<=16*1024*1024);
      request=JSON.parse(line);
      assert.equal(request.method,'POST');
      const name=request.path.replace(/^\/rest\/v1\/rpc\//,'');
      assert.ok(Object.hasOwn(params,name));
      assert.equal(request.path,'/rest/v1/rpc/'+name);
      assert.deepEqual(Object.keys(request.data).sort(),[...params[name]].sort());
      write({id:request.id??null,ok:true,result:await serviceRpc(db,name,params[name].map(key=>request.data[key]))});
    } catch (error) {
      write({id:request?.id??null,ok:false,code:typeof error.code==='string'?error.code:'TEST_BRIDGE_INPUT',message:String(error.message)});
    }
  }
} finally { lines.close(); await db.close(); }
