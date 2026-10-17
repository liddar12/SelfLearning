import assert from 'node:assert/strict';
import { test } from 'node:test';
import { rejectsCode } from './harness.mjs';
import { openOwnerDatabase,AUTHORITY,ACTOR,NS,OID,canonical,digest,asRole,service,seedObject,command,sealCommand,append,read } from './owner_intents_harness.mjs';

async function database(run) {
 const db=await openOwnerDatabase();
 try {await seedObject(db); await run(db);} finally {await db.close();}
}
const size=async db=>(await db.query('SELECT count(*)::int n FROM selflearn_private.owner_intent_events_v1')).rows[0].n;

test('actual PG approve/revoke/reapprove/rollback are immutable intentions, exact original retry survives head changes',async()=>database(async db=>{
 const empty=await read(db); assert.equal(empty.head_sequence,0);assert.equal(empty.head_event_id,null);
 const first=await append(db,command());
 assert.equal(first.activation,false);assert.equal(first.content_hash_verified,true);assert.equal(first.domain_pin_binding_verified,false);assert.equal(first.current_serving_verified,false);
 const revoked=await append(db,command({head:first.event_id,action:'revoke'}));
 await rejectsCode(()=>append(db,command({head:revoked.event_id,action:'rollback'})),'PT422');
 const approved=await append(db,command({head:revoked.event_id}));
 const other=await append(db,command({head:approved.event_id,artifact:'artifact-b'}));
 const rollback=await append(db,command({head:other.event_id,action:'rollback'}));
 assert.deepEqual(await append(db,command()),first);
 const page=await read(db);assert.equal(page.head_sequence,5);assert.equal(page.head_event_id,rollback.event_id);
 for(const row of page.rows) assert.equal(row.event.event_id,digest(Object.fromEntries(Object.entries(row.event).filter(([k])=>k!=='event_id'))));
 assert.equal((await db.query('SELECT count(*)::int n FROM selflearn_private.journal_receipts')).rows[0].n,0);
 assert.equal((await db.query('SELECT count(*)::int n FROM selflearn_private.journal_revisions')).rows[0].n,0);
}));

test('CAS, missing/typed source references, invalid clocks and rollback mismatches are atomic no-ops',async()=>database(async db=>{
 const baseline=(await db.query('SELECT selflearn_private.observed_watermark_v1()::text w')).rows[0].w;
 for(const value of [command({refs:[digest('absent')]}),command({expires_utc:'2001-01-01T00:00:00Z'}),command({action:'rollback'})]) {
  await rejectsCode(()=>append(db,value),'PT422');assert.equal(await size(db),0);
 }
 const first=await append(db,command());
 await rejectsCode(()=>append(db,command({artifact:'changed'})),'PT409');
 const bad=command({head:first.event_id,action:'rollback'});bad.pins.policy_id=digest('wrong');delete bad.command_id;
 await rejectsCode(()=>append(db,sealCommand(bad)),'PT422');
 assert.equal(await size(db),1);
 assert.equal((await db.query('SELECT selflearn_private.observed_watermark_v1()::text w')).rows[0].w,baseline);
}));

test('paged snapshot pins global owner watermark; new events and namespaces do not replace earlier history',async()=>database(async db=>{
 const a=await append(db,command()),b=await append(db,command({head:a.event_id,action:'revoke'}));
 const first=await read(db,{limit:1});assert.equal(first.next_after,'1');
 await append(db,command({head:b.event_id}));
 const second=await read(db,{after:first.next_after,watermark:first.watermark,limit:1});
 assert.equal(second.head_sequence,2);assert.equal(second.next_after,null);assert.equal(second.rows[0].event.event_id,b.event_id);
 assert.deepEqual((await read(db,{namespace:{...NS,producer_id:'other'}})).rows,[]);
 for(const args of [{limit:0},{limit:101},{after:'01'},{watermark:'9999'},{after:'5',watermark:'1'}]) await rejectsCode(()=>read(db,args),'PT422');
}));

test('strict protocol JSON/types/unknown metadata and untrusted clock/actor fields are rejected before progress',async()=>database(async db=>{
 for(const change of [{version:true},{version:'1'},{namespace:{...NS,owner:'injected'}},{action:'promote'},
  {recorded_utc:'2001-01-01T00:00:00Z'},{reason:'\u2003'},{reason:'\n'},{expires_utc:'2026-01-01'},
  {effective_from_utc:'2099-02-01T00:00:00Z'},{pins:{}},{action:'revoke',expires_utc:'2099-01-01T00:00:00Z'}]) {
  const unsigned=command(change);delete unsigned.command_id;
  await rejectsCode(()=>append(db,sealCommand(unsigned)),'PT422');
 }
 const valid=canonical(command());
 for(const text of [valid.replace('"version":1','"version":1.0'),valid.replace('"version":1','"version":1,"version":1'),'{ '+valid.slice(1)]) {
  await rejectsCode(()=>service(db,'selflearn_journal_append_owner_intent_v1',{p_authority_id:AUTHORITY,p_actor_ref:ACTOR,p_command_json:text}),'PT422');
 }
 assert.equal(await size(db),0);
 // A valid single-letter 'v' reason is not whitespace or normalized away.
 assert.equal((await append(db,command({reason:'vv'}))).durable,true);
}));

test('service input-only insert is guarded; computed outputs, public roles and mutation/truncation are denied',async()=>database(async db=>{
 for(const role of ['anon','authenticated']) {
  await rejectsCode(()=>asRole(db,role,()=>db.query('SELECT * FROM selflearn_private.owner_intent_events_v1')),'42501');
  await rejectsCode(()=>asRole(db,role,()=>db.query('SELECT public.selflearn_journal_read_owner_intents_v1($1,$2,null,1,null)',[AUTHORITY,canonical(NS)])),'42501');
  await rejectsCode(()=>asRole(db,role,()=>db.query('SELECT public.selflearn_journal_append_owner_intent_v1($1,$2,$3)',[canonical(command()),AUTHORITY,ACTOR])),'42501');
 }
 await rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json,ledger_seq) VALUES($1,$2,$3,1)',[AUTHORITY,ACTOR,canonical(command())])),'42501');
 await rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json) VALUES($1,$2,$3)',[AUTHORITY,'bad\nactor',canonical(command())])),'PT422');
 const direct=await asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json) VALUES($1,$2,$3) RETURNING result_json',[AUTHORITY,ACTOR,canonical(command())]));
 assert.equal(JSON.parse(direct.rows[0].result_json).sequence,1);
 for(const sql of ['UPDATE selflearn_private.owner_intent_events_v1 SET actor_ref=\'forged\'','DELETE FROM selflearn_private.owner_intent_events_v1','TRUNCATE selflearn_private.owner_intent_events_v1']) await rejectsCode(()=>db.exec(sql),'PT409');
 const flags=(await db.query("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='selflearn_private.owner_intent_events_v1'::regclass")).rows[0];
 assert.ok(flags.relrowsecurity&&flags.relforcerowsecurity);
}));

test('authority scoping and original recovery preserve immutable bytes; higher isolation fails closed',async()=>database(async db=>{
 const first=await append(db,command());
 const wrong=command();
 await rejectsCode(()=>service(db,'selflearn_journal_append_owner_intent_v1',{p_authority_id:digest('wrong-authority'),p_actor_ref:ACTOR,p_command_json:canonical(wrong)}),'PT422');
 for(const isolation of ['REPEATABLE READ','SERIALIZABLE']) {
  await db.exec('SET ROLE service_role');
  try {await db.exec('BEGIN ISOLATION LEVEL '+isolation);try {
   await rejectsCode(()=>db.query('SELECT public.selflearn_journal_append_owner_intent_v1($1,$2,$3)',[canonical(command()),AUTHORITY,ACTOR]),'PT422');
  } finally {await db.exec('ROLLBACK');}} finally {await db.exec('RESET ROLE');}
 }
 assert.deepEqual(await append(db,command()),first);assert.equal(await size(db),1);
 const head=await append(db,command({head:first.event_id,action:'revoke'}));
 // Deliberately privileged corruption, not an available service operation.
 // Both the exact-retry branch AND novel/direct input writer must still check
 // the full chain; removing the duplicate novel scan must create no bypass.
 await db.exec('ALTER TABLE selflearn_private.owner_intent_events_v1 DISABLE TRIGGER owner_intent_immutable_v1');
 try {await db.query('DELETE FROM selflearn_private.owner_intent_events_v1 WHERE event_id=$1',[first.event_id]);}
 finally {await db.exec('ALTER TABLE selflearn_private.owner_intent_events_v1 ENABLE TRIGGER owner_intent_immutable_v1');}
 const novel=command({head:head.event_id});
 const sequenceBefore=(await db.query('SELECT last_value::text value FROM selflearn_private.owner_intent_sequence_v1')).rows[0].value;
 await rejectsCode(()=>append(db,novel),'PT503');
 await rejectsCode(()=>append(db,command({head:first.event_id,action:'revoke'})),'PT503');
 await rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json) VALUES($1,$2,$3)',[AUTHORITY,ACTOR,canonical(novel)])),'PT503');
 assert.equal(await size(db),1);
 assert.equal((await db.query('SELECT last_value::text value FROM selflearn_private.owner_intent_sequence_v1')).rows[0].value,sequenceBefore);
}));

test('raw clock tokens cannot use PostgreSQL normalization or hidden precision beyond the Python profile',async()=>database(async db=>{
 for(const stamp of ['2099-01-01T24:00:00Z','2099-01-01T23:60:00Z','2099-01-01T23:59:60Z',
  '2099-02-30T00:00:00Z','2099-01-01T00:00:00.1234567Z','2099-01-01T00:00:00+16:00']) {
  await rejectsCode(()=>append(db,command({effective_from_utc:stamp,expires_utc:'2100-12-01T00:00:00Z'})),'PT422');
  assert.equal(await size(db),0);
 }
 const valid='2099-01-01T23:59:59.999999-15:59';
 const ack=await append(db,command({effective_from_utc:valid,expires_utc:'2100-12-01T00:00:00Z'}));
 assert.equal(ack.durable,true);assert.equal((await read(db)).rows[0].event.effective_from_utc,valid);
}));

test('canonical ASCII history quota holds BEFORE commit/sequence while a full validated near-cap prefix and original ACK stay readable',async()=>database(async db=>{
 const MAX_HISTORY=3145728-8192,reason='😀'.repeat(1000);
 const timings=[];
 async function timed(operation,run) {
  const started=performance.now();
  try {return await run();}
  finally {timings.push({operation,elapsed_ms:Math.round((performance.now()-started)*1000)/1000});}
 }
 const first=await timed('append_initial',()=>append(db,command({reason})));
 const original=(await db.query('SELECT * FROM selflearn_private.owner_intent_events_v1')).rows[0];
 const history=[JSON.parse(original.event_json)],seed=[];
 let overflow;
 for(let ordinal=2;ordinal<=256;ordinal++) {
  const candidate=command({head:history.at(-1).event_id,reason});
  const event={...history[0],sequence:ordinal,previous_event_id:history.at(-1).event_id,command:candidate};
  delete event.event_id;event.event_id=digest(event);
  if(Buffer.byteLength(canonical([...history,event]),'utf8')>MAX_HISTORY) {overflow=candidate;break;}
  history.push(event);seed.push(event);
 }
 assert.ok(overflow&&history.length>200&&history.length<256,'bytecap can precede event-count cap');
 // Privileged fixture setup ONLY: seed a fully canonical, parent-linked prefix
 // in O(n) work instead of exercising n quadratic appends. Runtime service
 // output-column grants and direct-insert trigger are tested separately.
 await db.exec('ALTER TABLE selflearn_private.owner_intent_events_v1 DISABLE TRIGGER owner_intent_insert_v1');
 try {
  for(const event of seed) {
   const ack={...first,command_id:event.command.command_id,event_id:event.event_id,sequence:event.sequence};
   await db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json,namespace_json,namespace_sequence,command_id,event_id,previous_event_id,event_json,result_json,ledger_seq,stored_utc) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)',
    [AUTHORITY,ACTOR,canonical(event.command),canonical(NS),event.sequence,event.command.command_id,event.event_id,event.previous_event_id,canonical(event),canonical(ack),event.sequence,event.recorded_utc]);
  }
  await db.query("SELECT pg_catalog.setval('selflearn_private.owner_intent_sequence_v1'::regclass,$1,true)",[history.length]);
 } finally {await db.exec('ALTER TABLE selflearn_private.owner_intent_events_v1 ENABLE TRIGGER owner_intent_insert_v1');}
 const pages=[];let cursor=null,watermark=null,pageNumber=0;
 do {
  const page=await timed('read_pinned_page_'+(++pageNumber),()=>read(db,{limit:100,after:cursor,watermark}));
  if(watermark===null)watermark=page.watermark;
  assert.equal(page.head_sequence,history.length);assert.equal(page.head_event_id,history.at(-1).event_id);
  pages.push(...page.rows);cursor=page.next_after;
 } while(cursor!==null);
 assert.deepEqual(pages.map(row=>row.event),history,'actual SQL validates every canonical hash/parent/clock before paging');
 assert.ok(Buffer.byteLength(canonical(history),'utf8')<=MAX_HISTORY);
 const before=await size(db),sequenceBefore=(await db.query('SELECT last_value::text value FROM selflearn_private.owner_intent_sequence_v1')).rows[0].value;
 await timed('append_over_history_limit',()=>rejectsCode(()=>append(db,overflow),'PT429'));
 assert.equal(await size(db),before);
 assert.equal((await db.query('SELECT last_value::text value FROM selflearn_private.owner_intent_sequence_v1')).rows[0].value,sequenceBefore);
 // Direct input-only inserts cannot bypass the identical quota/rollback guard.
 await timed('direct_insert_over_history_limit',()=>rejectsCode(()=>asRole(db,'service_role',()=>db.query('INSERT INTO selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json) VALUES($1,$2,$3)',[AUTHORITY,ACTOR,canonical(overflow)])),'PT429'));
 assert.equal(await size(db),before);
 assert.equal((await db.query('SELECT last_value::text value FROM selflearn_private.owner_intent_sequence_v1')).rows[0].value,sequenceBefore);
 assert.deepEqual(await timed('retry_original_before_quota',()=>append(db,command({reason}))),first,'original retry precedes byte/count quota and current head CAS');
 assert.equal((await timed('read_head_after_capacity_hold',()=>read(db,{limit:1}))).watermark,watermark);
 console.log(JSON.stringify({scope:'local-WASM-canonical-history-capacity-only',events:history.length,history_ascii_bytes:Buffer.byteLength(canonical(history),'utf8'),max_history_bytes:MAX_HISTORY,
  timings,maximum_elapsed_ms:Math.max(...timings.map(row=>row.elapsed_ms)),timing_scope:'actual in-process PostgreSQL-WASM calls including local role/query overhead; NO HTTP/native/hosted timing proof'}));
}));
