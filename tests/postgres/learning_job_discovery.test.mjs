// Real PostgreSQL-WASM discovery over the full migration chain; no cloud calls.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {openJobDatabase,asRole,canonical,digest,AUTHORITY,NS,definition,command,register,append,read,seedObject,service,jobRpc} from './learning_jobs_harness.mjs';

let db,serial=0;
const CODE=digest('discovery-current-code');
before(async()=>{db=await openJobDatabase();});
after(async()=>{if(db)await db.close();});
const discover=(scope,changes={})=>service(db,'selflearn_jobs_discover_v1',{
 p_authority_id:AUTHORITY,p_namespace_json:canonical(scope),p_operation:'bounded_observation',p_code_id:CODE,p_policy_object_id:null,p_limit:16,...changes});
async function add(scope,changes={}){
 const input=await seedObject(db,{discovery_input:++serial});
 const policy=await seedObject(db,{discovery_policy:'no-activation'});
 return register(db,definition({namespace:scope,operation:'bounded_observation',code_id:CODE,
  input_object_id:input,policy_object_id:policy,...changes}));
}

test('SQL returns only due queued or expired-lease IDs, without ownership or journal writes',async()=>{
 const scope={...NS,producer_id:'discovery-states'};
 const queued=await add(scope);
 await add(scope,{not_before_utc:new Date(Date.now()+60000).toISOString()});
 await add(scope,{not_before_utc:new Date(Date.now()-120000).toISOString(),not_after_utc:new Date(Date.now()-60000).toISOString()});
 const live=await add(scope),expired=await add(scope,{lease_seconds:1}),runtime=await add(scope,{lease_seconds:1,max_runtime_seconds:1});
 for(const state of[live,expired,runtime])await append(db,command(state));
 for(const action of['fail','complete','release']){
  let state=await add(scope,action==='release'?{max_attempts:1}:{});
  await append(db,command(state));state=await read(db,state.definition.job_id);
  await append(db,command(state,{action,reason:action==='release'?'worker_error':action==='fail'?'invalid_input':null,
   object:action==='complete'?state.definition.input_object_id:null}));
 }
 await new Promise(resolve=>setTimeout(resolve,1100));
 const beforeCount=(await db.query('select count(*)::int as n from selflearn_private.learning_job_events_v1')).rows[0].n;
 const found=await discover(scope);
 assert.deepEqual(new Set(found.job_ids),new Set([queued.definition.job_id,expired.definition.job_id]));
 assert.deepEqual(Object.keys(found).sort(),['version','authority_id','namespace','operation','code_id','policy_object_id','observed_utc','job_ids'].sort());
 assert.equal(found.authority_id,AUTHORITY);assert.deepEqual(found.namespace,scope);assert.equal(found.code_id,CODE);
 assert.ok(Number.isFinite(Date.parse(found.observed_utc)));
 assert.equal((await db.query('select count(*)::int as n from selflearn_private.learning_job_events_v1')).rows[0].n,beforeCount);
 for(const table of['journal_receipts','journal_revisions','journal_generation_commits'])
  assert.equal((await db.query('select count(*)::int as n from selflearn_private.'+table)).rows[0].n,0);
});

test('scope/code isolation, bounded fair retry order and stale discovered claims',async()=>{
 const scope={...NS,producer_id:'discovery-fairness'};
 let older=await add(scope);const untouched=await add(scope);
 await add(scope,{code_id:digest('older-code')});
 await add(scope,{operation:'different_operation'});await add({...scope,task:'other.task'});
 await append(db,command(older));older=await read(db,older.definition.job_id);
 await append(db,command(older,{action:'release',reason:'worker_error'}));
 assert.deepEqual((await discover(scope,{p_limit:1})).job_ids,[untouched.definition.job_id]);
 assert.deepEqual((await discover(scope)).job_ids,[untouched.definition.job_id,older.definition.job_id]);
 assert.equal((await discover(scope,{p_code_id:null})).job_ids.length,3);
 assert.deepEqual((await discover(scope,{p_authority_id:digest('different-authority')})).job_ids,[]);
 const stale=await read(db,untouched.definition.job_id);await append(db,command(stale));
 await assert.rejects(append(db,command(stale,{worker:'loser'})),/learning_job_head_conflict/);
});

test('RPC input types, service-only grants and security invoker remain strict',async()=>{
 const scope={...NS,producer_id:'discovery-security'};await add(scope);
 for(const change of[{p_limit:0},{p_limit:17},{p_limit:null},{p_limit:true},{p_limit:1.5},
  {p_authority_id:'bad'},{p_code_id:'bad'},{p_policy_object_id:'bad'},{p_operation:'../bad'},
  {p_namespace_json:canonical({...scope,extra:'no'})},{p_namespace_json:canonical({...scope,domain:false})}])
  await assert.rejects(discover(scope,change));
 const parameters={p_authority_id:AUTHORITY,p_namespace_json:canonical(scope),p_operation:'bounded_observation',p_code_id:CODE,p_policy_object_id:null,p_limit:16};
 for(const role of['anon','authenticated'])
  await assert.rejects(asRole(db,role,()=>jobRpc(db,'selflearn_jobs_discover_v1',parameters)),/permission denied/);
 const functionRow=(await db.query("select prosecdef,proconfig from pg_catalog.pg_proc where oid='public.selflearn_jobs_discover_v1(text,text,text,text,text,integer)'::regprocedure")).rows[0];
 assert.equal(functionRow.prosecdef,false);assert.ok(functionRow.proconfig.some(v=>v.startsWith('search_path=')));
 const indexes=(await db.query("select indexname from pg_catalog.pg_indexes where schemaname='selflearn_private' and tablename in ('learning_jobs_v1','learning_job_events_v1')")).rows.map(r=>r.indexname);
 assert.ok(indexes.includes('learning_job_discovery_scope_v1'));assert.ok(indexes.includes('learning_job_latest_event_v1'));
});

test('current policy is discovered despite more than sixteen older incompatible jobs',async()=>{
 const scope={...NS,producer_id:'discovery-policy-fairness'};
 for(let i=0;i<17;i++)await add(scope);
 const policy=await seedObject(db,{policy:'current-publication-context'});
 const current=await add(scope,{policy_object_id:policy});
 assert.equal((await discover(scope)).job_ids.includes(current.definition.job_id),false);
 const found=await discover(scope,{p_policy_object_id:policy});
 assert.deepEqual(found.job_ids,[current.definition.job_id]);assert.equal(found.policy_object_id,policy);
});
