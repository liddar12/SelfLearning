// Local actual PostgreSQL-WASM only. No hosted config or default route changes.
import { readFile, readdir } from 'node:fs/promises';
import { openDatabase, asRole, canonical, digest } from './harness.mjs';
export { asRole, canonical, digest };
export const jobsMigration = new URL('../../supabase/migrations/20261016163115_learning_jobs_v1.sql',import.meta.url);
export const AUTHORITY=digest('local-job-owner');
export const NS={domain:'example',task:'binary.forecast',producer_id:'fixture'};
export async function openJobDatabase(){
 const db=await openDatabase();
 try{
  // Apply the complete additive chain, not an isolated new schema that could
  // hide incompatibility with owner intent or capture/outcome persistence.
  const directory=new URL('../../supabase/migrations/',import.meta.url);
  const names=(await readdir(directory)).filter(n=>/^\d+_.*\.sql$/.test(n)&&n>'20261015222425_receipt_journal_v1.sql').sort();
  for(const name of names)await db.exec(await readFile(new URL(name,directory),'utf8'));
  return db;
 }
 catch(error){await db.close();throw error;}
}
const RPCS=new Map([
 ['selflearn_jobs_register_v1',['p_authority_id','p_definition_json']],
 ['selflearn_jobs_append_v1',['p_authority_id','p_command_json']],
 ['selflearn_jobs_read_v1',['p_authority_id','p_job_id']],
 ['selflearn_journal_append_object_v1',['p_kind','p_object_id','p_payload_json','p_hash_json']],
 ['selflearn_journal_read_v1',['p_view','p_key','p_after','p_limit','p_watermark']],
]);
export async function jobRpc(db,name,data){
 const fields=RPCS.get(name);
 if(!fields||canonical(Object.keys(data).sort())!==canonical([...fields].sort()))throw new Error('unsupported_job_fixture_rpc');
 return(await db.query('SELECT public.'+name+'('+fields.map((_,i)=>'$'+(i+1)).join(',')+') AS result',fields.map(k=>data[k]))).rows[0].result;
}
export const service=(db,name,data)=>asRole(db,'service_role',()=>jobRpc(db,name,data));
export async function seedObject(db,payload){
 const encoded=canonical(payload),id=digest(payload);
 await service(db,'selflearn_journal_append_object_v1',{p_kind:'source_snapshot',p_object_id:id,p_payload_json:encoded,p_hash_json:encoded});return id;
}
export function sealSpec(value,authority=AUTHORITY){
 const scope=Object.fromEntries(['namespace','operation','input_object_id','policy_object_id','code_id'].map(k=>[k,value[k]]));
 const definition={...value,job_id:digest({version:1,kind:'selflearn.job_identity.v1',authority_id:authority,scope})};
 return{...definition,definition_id:digest({version:1,kind:'selflearn.job_definition_identity.v1',authority_id:authority,definition})};
}
export function definition(changes={},authority=AUTHORITY){
 const now=Date.now();return sealSpec({version:1,kind:'selflearn.job_definition.v1',namespace:{...NS},operation:'shadow_evaluation',
 input_object_id:digest({synthetic_input:true}),policy_object_id:digest({synthetic_policy:true}),code_id:digest('fixture-code'),
 not_before_utc:new Date(now-10000).toISOString(),not_after_utc:new Date(now+300000).toISOString(),
 lease_seconds:30,max_runtime_seconds:60,max_attempts:3,max_steps:4,...changes},authority);
}
export function sealCommand(value,authority=AUTHORITY){return{...value,command_id:digest({version:1,kind:'selflearn.job_command_identity.v1',authority_id:authority,command:value})};}
export function command(state,{action='claim',worker='worker-a',object=null,reason=null,...changes}={},authority=AUTHORITY){
 return sealCommand({version:1,kind:'selflearn.job_command.v1',job_id:state.definition.job_id,expected_head_event_id:state.head_event_id,
 action,worker_id:worker,lease_id:action==='claim'?null:state.state.lease_id,object_id:object,reason_code:reason,...changes},authority);
}
export const register=(db,value,authority=AUTHORITY)=>service(db,'selflearn_jobs_register_v1',{p_authority_id:authority,p_definition_json:canonical(value)});
export const append=(db,value,authority=AUTHORITY)=>service(db,'selflearn_jobs_append_v1',{p_authority_id:authority,p_command_json:canonical(value)});
export const read=(db,id,authority=AUTHORITY)=>service(db,'selflearn_jobs_read_v1',{p_authority_id:authority,p_job_id:id});
export async function seeded(db,changes={}){
 await seedObject(db,{synthetic_input:true});await seedObject(db,{synthetic_policy:true});return register(db,definition(changes));
}
