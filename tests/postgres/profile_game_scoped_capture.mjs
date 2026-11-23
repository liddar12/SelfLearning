// Explicit local profile. Synthetic immutable originals; not a hosted latency claim.
import {performance} from 'node:perf_hooks';
import {openGameCaptureApplicationDatabase,addMembers,serviceV2} from './capture_application_harness.mjs';
import {canonical} from './harness.mjs';
const db=await openGameCaptureApplicationDatabase();
try {
  const scope={policy:'nfl.capture_game_scope_v1',season:2026,week:6,game_id:'G1'};
  const began=performance.now();
  let total=0;
  for(let generation=0;generation<43;generation++) {
    const selected=generation===42?81:83,count=generation===42?680:668;
    await addMembers(db,count,{vintage:'generation-'+generation,player:i=>'p'+(i%41),
      gameId:i=>i<selected?'G1':'OTHER'});
    total+=count;
    if(generation%10===9)console.log(JSON.stringify({phase:'fixture',generations:generation+1,total,ms:performance.now()-began}));
  }
  const fixture_ms=performance.now()-began,t=performance.now();
  const cert=await serviceV2(db,'selflearn_journal_prepare_capture_membership_v3',[2026,'[6]',canonical(scope)]);
  const cold_prepare_ms=performance.now()-t;
  let after=null,watermark=null,pages=0,bytes=0,records=0,readStarted=performance.now();
  do {
    const page=await serviceV2(db,'selflearn_journal_read_observed_game_v1',['game_generations',canonical(scope),after,500,watermark]);
    after=page.next_after;watermark=page.watermark;pages++;records+=page.rows.length;bytes+=Buffer.byteLength(JSON.stringify(page));
  } while(after);
  console.log(JSON.stringify({scope:'synthetic_local_postgresql_wasm_not_hosted_latency',total_receipts:total,selected_receipts:cert.member_count,
    selected_groups:cert.group_count,generations:43,fixture_ms,cold_prepare_ms,generation_read_ms:performance.now()-readStarted,
    generation_pages:pages,generation_records:records,generation_wire_bytes:bytes}));
} finally {await db.close();}
