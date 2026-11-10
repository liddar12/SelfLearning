// Executed PostgreSQL-WASM, full additive migrations; no hosted/native claim.
import {test,before,after} from 'node:test';
import assert from 'node:assert/strict';
import {openJobDatabase,asRole,digest,AUTHORITY,seeded,service,jobRpc} from './learning_jobs_harness.mjs';
let db;
before(async()=>{db=await openJobDatabase();});
after(async()=>{if(db)await db.close();});
const find=(id,authority=AUTHORITY)=>service(db,'selflearn_jobs_find_v1',{p_authority_id:authority,p_job_id:id});
test('find returns scoped null or the existing snapshot without writing rows',async()=>{
 assert.equal(await find(digest('missing')),null);
 const state=await seeded(db),id=state.definition.job_id;
 const counts=async()=>(await db.query('select (select count(*) from selflearn_private.learning_jobs_v1)::int as jobs,(select count(*) from selflearn_private.learning_job_events_v1)::int as events')).rows[0];
 const before=await counts(),found=await find(id);
 assert.deepEqual(found.definition,state.definition);assert.deepEqual(found.events,[]);
 assert.equal(await find(id,digest('different-authority')),null);
 assert.deepEqual(await counts(),before);
 await assert.rejects(service(db,'selflearn_jobs_read_v1',{p_authority_id:AUTHORITY,p_job_id:digest('missing')}),/learning_job_absent/);
 for(const [authority,job] of [[null,id],['bad',id],[AUTHORITY,null],[AUTHORITY,'bad']])
  await assert.rejects(find(job,authority),/invalid_learning_job_read/);
 await db.exec('begin isolation level repeatable read');
 try{await assert.rejects(db.query('select public.selflearn_jobs_find_v1($1,$2)',[AUTHORITY,id]),/invalid_learning_job_read/);}finally{await db.exec('rollback');}
});
test('lookup cannot hide existing read failures and grants remain private',async()=>{
 const state=await seeded(db,{code_id:digest('find-error-fixture')}),id=state.definition.job_id;
 const parameters={p_authority_id:AUTHORITY,p_job_id:id};
 for(const role of ['anon','authenticated'])
  await assert.rejects(asRole(db,role,()=>jobRpc(db,'selflearn_jobs_find_v1',parameters)),/permission denied/);
 const row=(await db.query("select prosecdef,proconfig,has_function_privilege('service_role',oid,'execute') as service from pg_proc where oid='public.selflearn_jobs_find_v1(text,text)'::regprocedure")).rows[0];
 assert.equal(row.prosecdef,false);assert.equal(row.service,true);assert.ok(row.proconfig.includes('search_path=""'));
 // Inject a read failure transactionally: find must propagate it, not return null.
 await db.exec('begin');
 try{
  await db.exec("create or replace function public.selflearn_jobs_read_v1(p_authority_id text,p_job_id text) returns json language plpgsql security invoker set search_path='' as $$ begin raise sqlstate 'PT422' using message='fixture_existing_read_error'; end $$");
  await assert.rejects(db.query('select public.selflearn_jobs_find_v1($1,$2)',[AUTHORITY,id]),/fixture_existing_read_error/);
 }finally{await db.exec('rollback');}
});
