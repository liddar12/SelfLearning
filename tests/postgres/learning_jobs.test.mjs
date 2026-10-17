import {test,beforeEach,afterEach} from 'node:test';
import assert from 'node:assert/strict';
import {openJobDatabase,asRole,canonical,digest,AUTHORITY,definition,sealSpec,command,sealCommand,register,append,read,seedObject,seeded,jobRpc} from './learning_jobs_harness.mjs';

let db;
beforeEach(async()=>{db=await openJobDatabase();});
afterEach(async()=>{if(db)await db.close();});

test('actual SQL registers one logical job, recovers original ACKs after completion and never calls them current leases',async()=>{
 let current=await seeded(db);const id=current.definition.job_id;
 assert.equal((await register(db,current.definition)).registered_utc,current.registered_utc);
 const modified={...current.definition,not_after_utc:'2099-01-01T00:00:00Z'};delete modified.job_id;delete modified.definition_id;
 const extended=sealSpec(modified);assert.equal(extended.job_id,id);assert.notEqual(extended.definition_id,current.definition.definition_id);
 await assert.rejects(register(db,extended),/learning_job_definition_conflict/);
 const claim=command(current),original=await append(db,claim);assert.deepEqual(await append(db,claim),original);
 current=await read(db,id);assert.equal(current.state.attempt,1);
 const oid=await seedObject(db,{synthetic_result:true,activation:false});const done=command(current,{action:'complete',object:oid});
 const result=await append(db,done);assert.deepEqual(await append(db,done),result);assert.deepEqual(await append(db,claim),original);
 current=await read(db,id);assert.equal(current.effective_status,'succeeded');assert.equal(current.events.length,2);
 assert.equal(original.current_lease_authority,false);assert.equal(result.activation,false);assert.equal(current.exactly_once_compute,false);
 await assert.rejects(append(db,command(current)),/learning_job_terminal/);
 for(const table of ['journal_receipts','journal_revisions','journal_generation_commits'])
  assert.equal((await db.query('select count(*)::int as n from selflearn_private.'+table)).rows[0].n,0);
});

test('stale competing claim refuses without duplicate work and release preserves the checkpoint',async()=>{
 let current=await seeded(db);const id=current.definition.job_id;const stale=command(current,{worker:'loser'});
 await append(db,command(current));await assert.rejects(append(db,stale),/learning_job_head_conflict/);
 current=await read(db,id);await assert.rejects(append(db,command(current,{worker:'loser'})),/learning_job_busy/);
 const oid=await seedObject(db,{checkpoint:1});await append(db,command(current,{action:'checkpoint',object:oid}));current=await read(db,id);
 await assert.rejects(append(db,command(current,{action:'checkpoint',object:oid})),/learning_job_checkpoint_not_advanced/);
 await append(db,command(current,{action:'release',reason:'paused'}));current=await read(db,id);
 assert.equal(current.effective_status,'queued');assert.equal(current.state.checkpoint_object_id,oid);
 await append(db,command(current,{worker:'replacement'}));current=await read(db,id);assert.equal(current.state.attempt,2);
 assert.equal(current.state.checkpoint_object_id,oid);
});

test('actual DB expiry fences old worker including rehashed old lease with a fresh head',async()=>{
 let state=await seeded(db,{lease_seconds:1});const id=state.definition.job_id;
 await append(db,command(state));state=await read(db,id);const oldLease=state.state.lease_id;
 const result=await seedObject(db,{expiry_result:true});
 await new Promise(resolve=>setTimeout(resolve,1100));
 state=await read(db,id);assert.equal(state.effective_status,'expired_lease');
 await assert.rejects(append(db,command(state,{action:'complete',object:result})),/learning_job_lease_conflict/);
 await append(db,command(state,{worker:'replacement'}));state=await read(db,id);
 await assert.rejects(append(db,command(state,{action:'complete',worker:'replacement',object:result,lease_id:oldLease})),/learning_job_lease_conflict/);
 await append(db,command(state,{action:'complete',worker:'replacement',object:result}));
 assert.equal((await read(db,id)).effective_status,'succeeded');
});

test('runtime, late cron, early scheduling and attempt limits refuse without inventing successful runs',async()=>{
 const now=Date.now();
 for(const[changes,status,reason]of[
  [{not_before_utc:new Date(now-120000).toISOString(),not_after_utc:new Date(now-60000).toISOString()},'expired','learning_job_expired'],
  [{not_before_utc:new Date(now+60000).toISOString()},'not_due','learning_job_not_due']]){
  const state=await seeded(db,{...changes,code_id:digest(status+'-test-code')});assert.equal(state.effective_status,status);await assert.rejects(append(db,command(state)),new RegExp(reason));
  assert.equal((await read(db,state.definition.job_id)).events.length,0);
 }
 let state=await seeded(db,{max_attempts:1});await append(db,command(state));state=await read(db,state.definition.job_id);
 await append(db,command(state,{action:'release',reason:'cancelled'}));state=await read(db,state.definition.job_id);
 assert.equal(state.effective_status,'attempt_limit');await assert.rejects(append(db,command(state)),/learning_job_attempt_limit/);
 state=await seeded(db,{lease_seconds:1,max_runtime_seconds:1,code_id:digest('runtime-test-code')});await append(db,command(state));
 await new Promise(resolve=>setTimeout(resolve,1100));state=await read(db,state.definition.job_id);
 assert.equal(state.effective_status,'runtime_limit');await assert.rejects(append(db,command(state)),/learning_job_runtime_limit/);
});

test('checkpoint cannot use completion reserve; failure is explicit terminal and references are required before a write',async()=>{
 let state=await seeded(db,{max_steps:2});const id=state.definition.job_id;
 await append(db,command(state));state=await read(db,id);const oid=await seedObject(db,{reserve_checkpoint:1});
 await assert.rejects(append(db,command(state,{action:'complete',object:digest('absent')})),/invalid_learning_job_reference/);
 assert.equal((await read(db,id)).events.length,1);
 await append(db,command(state,{action:'checkpoint',object:oid}));state=await read(db,id);
 await assert.rejects(append(db,command(state,{action:'checkpoint',object:oid})),/learning_job_step_limit/);
 await append(db,command(state,{action:'complete',object:oid}));assert.equal((await read(db,id)).state.steps,2);
 state=await seeded(db,{code_id:digest('failure-test-code')});await append(db,command(state));state=await read(db,state.definition.job_id);
 await append(db,command(state,{action:'fail',reason:'invalid_input'}));state=await read(db,state.definition.job_id);
 assert.equal(state.effective_status,'failed');assert.equal(state.state.reason_code,'invalid_input');
});

test('strict definition/command types, exact identities and authority isolation cannot be uploaded away',async()=>{
 const valid=definition();const unsigned={...valid};delete unsigned.job_id;delete unsigned.definition_id;
 for(const change of [{max_steps:true},{max_attempts:9},{operation:false},{code_id:'bad'},
   {namespace:{...valid.namespace,producer_id:'../unsafe'}},{not_after_utc:'2026-10-16T24:00:00Z'},
   {not_after_utc:'2026-10-16T23:00:00+16:00'},{extra:'not allowed'}])
  await assert.rejects(register(db,sealSpec({...unsigned,...change})),/invalid_learning_job/);
 await assert.rejects(register(db,{...valid,code_id:digest('changed')}),/learning_job_identity_mismatch/);
 await assert.rejects(register(db,valid,digest('other-owner')),/learning_job_identity_mismatch/);
 const state=await seeded(db);const c=command(state);const u={...c};delete u.command_id;
 for(const change of[{lease_id:digest('fake')},{worker_id:false},{object_id:digest('wrong')},{reason_code:'token-private'},
  {action:'approve'},{version:true}])await assert.rejects(append(db,sealCommand({...u,...change})),/invalid_learning_job/);
 await assert.rejects(read(db,state.definition.job_id,digest('other-owner')),/learning_job_absent/);
});

test('new private tables and RPCs deny public roles, derived-column injection and history mutation',async()=>{
 const state=await seeded(db),id=state.definition.job_id;
 for(const role of['anon','authenticated']){
  await assert.rejects(asRole(db,role,()=>jobRpc(db,'selflearn_jobs_read_v1',{p_authority_id:AUTHORITY,p_job_id:id})),/permission denied/);
  await assert.rejects(asRole(db,role,()=>db.query('select * from selflearn_private.learning_jobs_v1')),/permission denied/);
 }
 const c=command(state);
 await assert.rejects(asRole(db,'service_role',()=>db.query('insert into selflearn_private.learning_job_events_v1(authority_id,job_id,command_json,recorded_utc)values($1,$2,$3,now())',[AUTHORITY,id,canonical(c)])),/permission denied/);
 // Direct permitted-column inserts still execute the mandatory validation.
 await assert.rejects(asRole(db,'service_role',()=>db.query('insert into selflearn_private.learning_job_events_v1(authority_id,job_id,command_json)values($1,$2,$3)',[AUTHORITY,id,canonical({...c,worker_id:'forged'})])),/learning_job_command_hash_mismatch/);
 await append(db,c);
 for(const tab of['learning_jobs_v1','learning_job_events_v1']){
  for(const statement of['update selflearn_private.'+tab+' set authority_id=authority_id','delete from selflearn_private.'+tab]){
   await assert.rejects(asRole(db,'service_role',()=>db.exec(statement)),/permission denied/);
   await assert.rejects(db.exec(statement),/immutable_journal_evidence/);
  }
 }
 const truncate='truncate selflearn_private.learning_job_events_v1,selflearn_private.learning_jobs_v1';
 await assert.rejects(asRole(db,'service_role',()=>db.exec(truncate)),/permission denied/);
 // Include both FK-related tables so this reaches the no-truncate trigger,
 // rather than merely passing because PostgreSQL rejected the FK dependency.
 await assert.rejects(db.exec(truncate),/immutable_journal_evidence/);
 assert.equal((await read(db,id)).events.length,1);
 const rls=(await db.query("select relrowsecurity,relforcerowsecurity from pg_class c join pg_namespace n on n.oid=c.relnamespace where n.nspname='selflearn_private'and c.relname in('learning_jobs_v1','learning_job_events_v1')")).rows;
 assert.equal(rls.length,2);assert.ok(rls.every(r=>r.relrowsecurity&&r.relforcerowsecurity));
 const functions=(await db.query("select p.proname,p.prosecdef,p.proconfig,has_function_privilege('anon',p.oid,'execute')as anon,has_function_privilege('authenticated',p.oid,'execute')as authenticated,has_function_privilege('service_role',p.oid,'execute')as service from pg_proc p join pg_namespace n on n.oid=p.pronamespace where (n.nspname='selflearn_private'and(p.proname like 'job_%'or p.proname='guard_learning_job_v1'))or(n.nspname='public'and p.proname like 'selflearn_jobs_%')")).rows;
 assert.equal(functions.length,13);
 assert.ok(functions.every(r=>!r.prosecdef&&!r.anon&&!r.authenticated&&r.service
  &&r.proconfig.some(s=>s==='search_path=""'||s==='search_path=')));
});
