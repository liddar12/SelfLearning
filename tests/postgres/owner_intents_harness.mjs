// Additive owner-intent fixture; no existing harness/migration is changed.
import { readFile } from 'node:fs/promises';
import { openObservedDatabase, asRole, canonical, digest } from './harness.mjs';

export { canonical, digest, asRole };
export const ownerMigration = new URL('../../supabase/migrations/20261016070717_owner_intents_v1.sql',import.meta.url);
export const AUTHORITY = digest('offline-owner-authority');
export const ACTOR = 'configured-single-owner';
export const NS = {domain:'example',task:'binary.forecast',producer_id:'local-producer'};
export const OBJECT = {unverified_evidence:{coefficients:{a:0,b:1}}};
export const OID = digest(OBJECT);
export async function openOwnerDatabase() {
 const db=await openObservedDatabase();
 try { await db.exec(await readFile(ownerMigration,'utf8')); return db; }
 catch(error) { await db.close(); throw error; }
}
const RPCS = new Map([
 ['selflearn_journal_append_owner_intent_v1',['p_command_json','p_authority_id','p_actor_ref']],
 ['selflearn_journal_read_owner_intents_v1',['p_authority_id','p_namespace_json','p_after','p_limit','p_watermark']],
 ['selflearn_journal_append_object_v1',['p_kind','p_object_id','p_payload_json','p_hash_json']],
 ['selflearn_journal_read_v1',['p_view','p_key','p_after','p_limit','p_watermark']],
 ['selflearn_journal_read_observed_v1',['p_view','p_key','p_after','p_limit','p_watermark']],
]);
export async function ownerRpc(db,name,data) {
 const fields=RPCS.get(name);
 if(!fields || canonical(Object.keys(data).sort())!==canonical([...fields].sort())) throw new Error('unsupported_owner_fixture_rpc');
 return (await db.query('SELECT public.'+name+'('+fields.map((_,i)=>'$'+(i+1)).join(',')+') AS result',fields.map(k=>data[k]))).rows[0].result;
}
export const service = (db,name,data)=>asRole(db,'service_role',()=>ownerRpc(db,name,data));
export async function seedObject(db,object=OBJECT) {
 const id=digest(object), encoded=canonical(object);
 await service(db,'selflearn_journal_append_object_v1',{p_kind:'source_snapshot',p_object_id:id,p_payload_json:encoded,p_hash_json:encoded});
 return id;
}
export function sealCommand(unsigned,authority=AUTHORITY) {
 return {...unsigned,command_id:digest({version:1,kind:'selflearn.owner_intent_identity.v1',authority_id:authority,command:unsigned})};
}
export function command({head=null,action='approve',artifact='artifact-a',refs=[OID],...changes}={}) {
 return sealCommand({version:1,kind:'selflearn.owner_intent_command.v1',namespace:{...NS},action,expected_head_event_id:head,
  pins:{candidate_id:digest('candidate_id'),artifact_id:digest(artifact),evaluation_report_id:digest('evaluation_report_id'),policy_id:digest('policy_id'),
   generation_context_id:digest('generation_context_id'),expected_previous_current_id:digest('expected_previous_current_id'),evidence_object_ids:refs},
  effective_from_utc:null,expires_utc:action==='revoke'?null:'2099-01-01T00:00:00Z',reason:'Explicit owner intent; no serving activation',...changes});
}
export const append=(db,value)=>service(db,'selflearn_journal_append_owner_intent_v1',{p_command_json:canonical(value),p_authority_id:AUTHORITY,p_actor_ref:ACTOR});
export const read=(db,{namespace=NS,authority=AUTHORITY,after=null,limit=100,watermark=null}={})=>service(db,'selflearn_journal_read_owner_intents_v1',
 {p_authority_id:authority,p_namespace_json:canonical(namespace),p_after:after,p_limit:limit,p_watermark:watermark});
