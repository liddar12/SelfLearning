// Fixed-allowlist TEST ONLY PostgreSQL-WASM pipe; no sockets/admin/SQL input.
import assert from 'node:assert/strict';
import { createInterface } from 'node:readline';
import { openCaptureApplicationDatabase } from './capture_application_harness.mjs';
import { asRole } from './harness.mjs';

const params=Object.freeze({
  selflearn_journal_append_receipt_v1:['p_receipt_json'],
  selflearn_journal_append_object_v1:['p_kind','p_object_id','p_payload_json','p_hash_json'],
  selflearn_journal_commit_generation_v1:['p_object_id','p_receipts_json','p_rejections_json'],
  selflearn_journal_append_resolution_v1:['p_revision_json'],
  selflearn_journal_read_v1:['p_view','p_key','p_after','p_limit','p_watermark'],
  selflearn_journal_read_observed_v1:['p_view','p_key','p_after','p_limit','p_watermark'],
  selflearn_journal_commit_observed_v1:['p_batch_json','p_watermark'],
  selflearn_journal_commit_capture_delivery_v1:['p_request_json','p_binding_json','p_batch_json','p_watermark'],
  selflearn_journal_read_capture_delivery_v1:['p_delivery_id','p_after','p_limit'],
  selflearn_journal_register_capture_application_v2:['p_request_json','p_seal_json','p_watermark'],
  selflearn_journal_commit_capture_application_batch_v2:['p_application_id','p_step_id','p_group_results_json','p_batch_json','p_watermark'],
  selflearn_journal_stop_capture_application_v2:['p_application_id','p_reason','p_watermark'],
  selflearn_journal_read_capture_application_v2:['p_application_id','p_view','p_key','p_after','p_limit','p_ledger_watermark'],
  selflearn_journal_prepare_capture_membership_v2:['p_season','p_weeks_json'],
  selflearn_reserve_capture_stage_v1:['p_namespace_id','p_chunk_id','p_raw_bytes'],
});
const db=await openCaptureApplicationDatabase();
const lines=createInterface({input:process.stdin,crlfDelay:Infinity});
const write=value=>process.stdout.write(JSON.stringify(value)+'\n');
try {
  write({ready:true,engine:'postgresql-wasm',version:(await db.query('SELECT version() AS value')).rows[0].value});
  for await(const line of lines) {
    let request;
    try {
      assert.ok(Buffer.byteLength(line,'utf8')<=16*1024*1024);
      request=JSON.parse(line);
      if(request.method==='TEST_ADMIN') {
        const data=request.data;assert.match(data.namespace_id,/^sha256:[0-9a-f]{64}$/);
        let result;
        if(request.path==='/test/provision-capture-staging') {
          assert.deepEqual(Object.keys(data).sort(),['enabled','max_chunks','max_total_bytes','namespace_id']);
          assert.equal(typeof data.enabled,'boolean');assert.ok(Number.isSafeInteger(data.max_total_bytes)&&Number.isInteger(data.max_chunks));
          await db.query('INSERT INTO selflearn_private.capture_staging_limits(namespace_id,max_total_bytes,max_chunks,enabled) VALUES($1,$2,$3,$4)',
            [data.namespace_id,data.max_total_bytes,data.max_chunks,data.enabled]);result={version:1,fixture_provisioned:true};
        } else {
          assert.equal(request.path,'/test/capture-staging-summary');assert.deepEqual(Object.keys(data),['namespace_id']);
          const summary=(await db.query('SELECT coalesce(sum(raw_bytes),0)::bigint AS reserved_total_bytes,count(*)::integer AS reserved_chunks FROM selflearn_private.capture_staging_reservations WHERE namespace_id=$1',[data.namespace_id])).rows[0];
          result={version:1,reserved_total_bytes:Number(summary.reserved_total_bytes),reserved_chunks:summary.reserved_chunks};
        }
        write({id:request.id??null,ok:true,result});continue;
      }
      assert.equal(request.method,'POST');
      const name=request.path.replace(/^\/rest\/v1\/rpc\//,'');
      assert.ok(Object.hasOwn(params,name));assert.equal(request.path,'/rest/v1/rpc/'+name);
      assert.deepEqual(Object.keys(request.data).sort(),[...params[name]].sort());
      const args=params[name].map(key=>request.data[key]);
      // Return PostgreSQL's JSON TEXT, not a JS parse/re-encode of its JSON
      // value: otherwise Python's authenticated fact80.0 becomes integer80.
      const raw=await asRole(db,'service_role',async()=>
        (await db.query(`SELECT public.${name}(${args.map((_,i)=>'$'+(i+1)).join(',')})::text AS result`,args)).rows[0].result);
      assert.equal(typeof raw,'string');assert.ok(Buffer.byteLength(raw,'utf8')<=8*1024*1024);
      process.stdout.write('{"id":'+JSON.stringify(request.id??null)+',"ok":true,"result":'+raw+'}\n');
    } catch(error) {
      write({id:request?.id??null,ok:false,code:typeof error.code==='string'?error.code:'TEST_BRIDGE_INPUT',message:String(error.message)});
    }
  }
} finally {lines.close();await db.close();}
