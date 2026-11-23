// Real RPC-admitted synthetic ledger; local WASM timings are not hosted claims.
import {performance} from 'node:perf_hooks';
import {readFile} from 'node:fs/promises';
import {openGameCaptureApplicationDatabase,addMembers,serviceV2,generationProofMigration} from './capture_application_harness.mjs';
import {canonical} from './harness.mjs';
const db=await openGameCaptureApplicationDatabase();
const scope={policy:'nfl.capture_game_scope_v1',season:2026,week:6,game_id:'G1'};
try {
  const began=performance.now();let total=0;
  for(let generation=0;generation<43;generation++){
    const selected=generation===42?81:83,count=generation===42?680:668;
    await addMembers(db,count,{vintage:'generation-'+generation,player:i=>'p'+(i%41),
      gameId:i=>i<selected?'G1':'OTHER'});total+=count;
    if(generation%10===9)console.log(JSON.stringify({phase:'fixture',generations:generation+1,total,ms:performance.now()-began}));
  }
  let started=performance.now();
  const before=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope)]);
  const old_prepare_ms=performance.now()-started;
  const prior=(await db.query('select members,groups,source_index from selflearn_private.capture_game_membership_cache_v1 where cache_id=$1',[before.cache_id])).rows[0];
  await db.exec(await readFile(generationProofMigration,'utf8'));
  const warm_ms=[];let result;
  do {
    started=performance.now();
    result=await serviceV2(db,'selflearn_journal_prepare_capture_generation_proofs_v1',[canonical(scope)]);
    warm_ms.push(performance.now()-started);
  } while(result.remaining_generation_count);
  started=performance.now();
  const after=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope)]);
  const proven_prepare_ms=performance.now()-started;
  const current=(await db.query('select members,groups,source_index from selflearn_private.capture_game_membership_cache_v1 where cache_id=$1',[after.cache_id])).rows[0];
  if(canonical(prior)!==canonical(current)||before.membership_id!==after.membership_id)throw Error('authority/source bytes changed');
  const sizes=(await db.query('select sum(octet_length(payload_json))::text generation_raw_bytes from selflearn_private.journal_objects where kind=\'generation\'')).rows[0];
  console.log(JSON.stringify({scope:'synthetic_local_postgresql_wasm_not_hosted_latency',total_receipts:total,selected_receipts:after.member_count,
    generation_count:43,...sizes,old_prepare_ms,proven_prepare_ms,warm_calls:warm_ms.length,
    max_warm_call_ms:Math.max(...warm_ms),total_warm_ms:warm_ms.reduce((a,b)=>a+b,0),exact_membership_and_source_index_equal:true}));
} finally {await db.close();}
