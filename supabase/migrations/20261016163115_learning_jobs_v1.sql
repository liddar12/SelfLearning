-- Additive bounded job state, separate from forecast/outcome/owner history.
-- Local-reviewed migration ONLY: no hosted apply, scheduling or activation.
begin;

create table selflearn_private.learning_jobs_v1 (
 authority_id text not null, job_id text not null, namespace_json text not null,
 definition_json text not null, registered_utc timestamptz not null,
 primary key(authority_id,job_id)
);
create index learning_job_scope_v1 on selflearn_private.learning_jobs_v1(authority_id,namespace_json,registered_utc,job_id);
create table selflearn_private.learning_job_events_v1 (
 authority_id text not null, job_id text not null, command_json text not null,
 sequence integer not null check(sequence between 1 and 128), command_id text not null,
 event_id text not null unique, event_json text not null, recorded_utc timestamptz not null,
 primary key(authority_id,job_id,sequence), unique(authority_id,job_id,command_id),
 foreign key(authority_id,job_id) references selflearn_private.learning_jobs_v1(authority_id,job_id)
);
do $$ declare tab text; begin
 foreach tab in array array['learning_jobs_v1','learning_job_events_v1'] loop
  execute pg_catalog.format('alter table selflearn_private.%I enable row level security',tab);
  execute pg_catalog.format('alter table selflearn_private.%I force row level security',tab);
  execute pg_catalog.format('revoke all on selflearn_private.%I from public,anon,authenticated,service_role',tab);
  execute pg_catalog.format('grant select on selflearn_private.%I to service_role',tab);
  execute pg_catalog.format('create trigger learning_job_immutable before update or delete on selflearn_private.%I for each row execute function selflearn_private.refuse_mutation_v1()',tab);
  execute pg_catalog.format('create trigger learning_job_no_truncate before truncate on selflearn_private.%I for each statement execute function selflearn_private.refuse_mutation_v1()',tab);
 end loop;
end $$;
grant insert(authority_id,definition_json) on selflearn_private.learning_jobs_v1 to service_role;
grant insert(authority_id,job_id,command_json) on selflearn_private.learning_job_events_v1 to service_role;

create function selflearn_private.job_keys_v1(doc jsonb, names text[]) returns boolean
language sql immutable security invoker set search_path='' as $$
 select case when pg_catalog.jsonb_typeof(doc)='object' then
  (select pg_catalog.array_agg(k order by k) from pg_catalog.jsonb_object_keys(doc) k)
  is not distinct from (select pg_catalog.array_agg(k order by k) from pg_catalog.unnest(names) k)
 else false end;
$$;

create function selflearn_private.job_clock_v1(value text) returns timestamptz
language plpgsql immutable security invoker set search_path='' as $$
declare stamp timestamptz;
begin
 if value is null or pg_catalog.length(value)>40 or not(value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|[+-](0[0-9]|1[0-5]):[0-5][0-9])$') then
  raise sqlstate 'PT422' using message='invalid_learning_job_clock'; end if;
 stamp:=value::timestamptz;
 if extract(year from stamp at time zone 'UTC') not between 2000 and 2100 then raise sqlstate 'PT422' using message='invalid_learning_job_clock'; end if;
 return stamp;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_learning_job_clock';
end $$;
create function selflearn_private.job_stamp_v1(value timestamptz) returns text
language sql immutable strict security invoker set search_path='' as $$
 select pg_catalog.to_char(value at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
$$;

create function selflearn_private.job_definition_v1(payload text, authority text) returns jsonb
language plpgsql immutable security invoker set search_path='' as $$
declare doc jsonb; ns jsonb; k text; maximum integer; expected text;
begin
 doc:=selflearn_private.require_canonical_v1(payload,16384)::jsonb;ns:=doc->'namespace';
 if authority is null or not(authority ~ '^sha256:[0-9a-f]{64}$') or not selflearn_private.job_keys_v1(doc,
  array['version','kind','namespace','operation','input_object_id','policy_object_id','code_id','not_before_utc','not_after_utc','lease_seconds','max_runtime_seconds','max_attempts','max_steps','job_id','definition_id'])
  or doc->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(doc->'version') is distinct from 'number'
  or doc->>'kind' is distinct from 'selflearn.job_definition.v1'
  or not selflearn_private.job_keys_v1(ns,array['domain','task','producer_id']) then
  raise sqlstate 'PT422' using message='invalid_learning_job_definition'; end if;
 foreach k in array array['domain','task','producer_id'] loop
  if pg_catalog.jsonb_typeof(ns->k) is distinct from 'string' or not(ns->>k ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
   raise sqlstate 'PT422' using message='invalid_learning_job_namespace'; end if;
 end loop;
 if pg_catalog.jsonb_typeof(doc->'operation') is distinct from 'string' or not(doc->>'operation' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
  raise sqlstate 'PT422' using message='invalid_learning_job_operation'; end if;
 foreach k in array array['job_id','definition_id','input_object_id','policy_object_id','code_id'] loop
  if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string' or not(doc->>k ~ '^sha256:[0-9a-f]{64}$') then
   raise sqlstate 'PT422' using message='invalid_learning_job_pin'; end if;
 end loop;
 foreach k in array array['not_before_utc','not_after_utc'] loop
  if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string' then raise sqlstate 'PT422' using message='invalid_learning_job_clock'; end if;
  perform selflearn_private.job_clock_v1(doc->>k);
 end loop;
 if selflearn_private.job_clock_v1(doc->>'not_before_utc')>=selflearn_private.job_clock_v1(doc->>'not_after_utc') then
  raise sqlstate 'PT422' using message='invalid_learning_job_window'; end if;
 foreach k in array array['lease_seconds','max_runtime_seconds','max_attempts','max_steps'] loop
  maximum:=case k when 'lease_seconds' then 300 when 'max_runtime_seconds' then 3600 when 'max_attempts' then 8 else 32 end;
  if pg_catalog.jsonb_typeof(doc->k) is distinct from 'number' or not(doc->>k ~ '^[1-9][0-9]*$') or (doc->>k)::integer>maximum then
   raise sqlstate 'PT422' using message='invalid_learning_job_budget'; end if;
 end loop;
 expected:=selflearn_private.digest_json_v1(pg_catalog.jsonb_build_object('version',1,'kind','selflearn.job_identity.v1',
  'authority_id',authority,'scope',pg_catalog.jsonb_build_object('namespace',doc->'namespace','operation',doc->'operation',
   'input_object_id',doc->'input_object_id','policy_object_id',doc->'policy_object_id','code_id',doc->'code_id'))::json);
 if doc->>'job_id' is distinct from expected then raise sqlstate 'PT422' using message='learning_job_identity_mismatch'; end if;
 expected:=selflearn_private.digest_json_v1(pg_catalog.jsonb_build_object('version',1,'kind','selflearn.job_definition_identity.v1',
  'authority_id',authority,'definition',doc-'definition_id')::json);
 if doc->>'definition_id' is distinct from expected then raise sqlstate 'PT422' using message='learning_job_definition_hash_mismatch'; end if;
 return doc;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_learning_job_definition';
end $$;

create function selflearn_private.job_command_v1(payload text, authority text) returns jsonb
language plpgsql immutable security invoker set search_path='' as $$
declare doc jsonb; action text; k text; expected text;
begin
 doc:=selflearn_private.require_canonical_v1(payload,16384)::jsonb;action:=doc->>'action';
 if authority is null or not(authority ~ '^sha256:[0-9a-f]{64}$') or not selflearn_private.job_keys_v1(doc,
  array['version','kind','job_id','expected_head_event_id','action','worker_id','lease_id','object_id','reason_code','command_id'])
  or doc->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(doc->'version') is distinct from 'number'
  or doc->>'kind' is distinct from 'selflearn.job_command.v1' or action not in('claim','checkpoint','complete','release','fail')
  or pg_catalog.jsonb_typeof(doc->'action') is distinct from 'string'
  or pg_catalog.jsonb_typeof(doc->'worker_id') is distinct from 'string' or not(doc->>'worker_id' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
  raise sqlstate 'PT422' using message='invalid_learning_job_command'; end if;
 foreach k in array array['job_id','command_id','expected_head_event_id','lease_id','object_id'] loop
  if (k in('job_id','command_id') or (doc->k)<>'null'::jsonb) and
   (pg_catalog.jsonb_typeof(doc->k) is distinct from 'string' or not(doc->>k ~ '^sha256:[0-9a-f]{64}$')) then
   raise sqlstate 'PT422' using message='invalid_learning_job_command_pin'; end if;
 end loop;
 if (action='claim' and doc->'lease_id'<>'null'::jsonb) or (action<>'claim' and doc->'lease_id'='null'::jsonb)
  or (action in('checkpoint','complete') and doc->'object_id'='null'::jsonb)
  or (action not in('checkpoint','complete') and doc->'object_id'<>'null'::jsonb)
  or (action='release' and (pg_catalog.jsonb_typeof(doc->'reason_code') is distinct from 'string' or doc->>'reason_code' not in('paused','worker_error','cancelled')))
  or (action='fail' and (pg_catalog.jsonb_typeof(doc->'reason_code') is distinct from 'string' or doc->>'reason_code' not in('invalid_input','evaluation_failed')))
  or (action not in('release','fail') and doc->'reason_code'<>'null'::jsonb) then
  raise sqlstate 'PT422' using message='invalid_learning_job_command_fields'; end if;
 expected:=selflearn_private.digest_json_v1(pg_catalog.jsonb_build_object('version',1,'kind','selflearn.job_command_identity.v1',
  'authority_id',authority,'command',doc-'command_id')::json);
 if doc->>'command_id' is distinct from expected then raise sqlstate 'PT422' using message='learning_job_command_hash_mismatch'; end if;
 return doc;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_learning_job_command';
end $$;

create function selflearn_private.job_initial_v1() returns jsonb
language sql immutable security invoker set search_path='' as $$
 select pg_catalog.jsonb_build_object('status','queued','attempt',0,'steps',0,'worker_id',null,'lease_id',null,
 'lease_until_utc',null,'started_utc',null,'runtime_deadline_utc',null,'checkpoint_object_id',null,'result_object_id',null,'reason_code',null);
$$;

create function selflearn_private.job_advance_v1(spec jsonb, state jsonb, command jsonb, recorded timestamptz) returns jsonb
language plpgsql immutable security invoker set search_path='' as $$
declare result jsonb:=state; action text:=command->>'action'; started timestamptz; deadline timestamptz; lease_end timestamptz; steps integer;
begin
 if state->>'status' in('succeeded','failed') then raise sqlstate 'PT409' using message='learning_job_terminal'; end if;
 if recorded<selflearn_private.job_clock_v1(spec->>'not_before_utc') then raise sqlstate 'PT409' using message='learning_job_not_due'; end if;
 if recorded>=selflearn_private.job_clock_v1(spec->>'not_after_utc') then raise sqlstate 'PT409' using message='learning_job_expired'; end if;
 if state->>'runtime_deadline_utc' is not null and recorded>=selflearn_private.job_clock_v1(state->>'runtime_deadline_utc') then
  raise sqlstate 'PT409' using message='learning_job_runtime_limit'; end if;
 if action='claim' then
  if state->>'status'='running' and recorded<selflearn_private.job_clock_v1(state->>'lease_until_utc') then raise sqlstate 'PT409' using message='learning_job_busy'; end if;
  if (state->>'attempt')::integer>=(spec->>'max_attempts')::integer then raise sqlstate 'PT409' using message='learning_job_attempt_limit'; end if;
  started:=coalesce((state->>'started_utc')::timestamptz,recorded);
  deadline:=coalesce((state->>'runtime_deadline_utc')::timestamptz,least(selflearn_private.job_clock_v1(spec->>'not_after_utc'),
   started+pg_catalog.make_interval(secs=>(spec->>'max_runtime_seconds')::double precision)));
  result:=result||pg_catalog.jsonb_build_object('status','running','attempt',(state->>'attempt')::integer+1,
   'worker_id',command->>'worker_id','lease_id',command->>'command_id','started_utc',coalesce(state->>'started_utc',selflearn_private.job_stamp_v1(recorded)),
   'runtime_deadline_utc',selflearn_private.job_stamp_v1(deadline),'reason_code',null);
 else
  if state->>'status'<>'running' or command->>'lease_id' is distinct from state->>'lease_id'
   or command->>'worker_id' is distinct from state->>'worker_id' or recorded>=selflearn_private.job_clock_v1(state->>'lease_until_utc') then
   raise sqlstate 'PT409' using message='learning_job_lease_conflict'; end if;
  if action in('checkpoint','complete') then
   steps:=(state->>'steps')::integer;
   if steps>=((spec->>'max_steps')::integer-(case when action='checkpoint' then 1 else 0 end)) then raise sqlstate 'PT409' using message='learning_job_step_limit'; end if;
   result:=result||pg_catalog.jsonb_build_object('steps',steps+1);
   if action='checkpoint' then
    if command->>'object_id' is not distinct from state->>'checkpoint_object_id' then raise sqlstate 'PT409' using message='learning_job_checkpoint_not_advanced'; end if;
    result:=result||pg_catalog.jsonb_build_object('checkpoint_object_id',command->>'object_id');
   else result:=result||pg_catalog.jsonb_build_object('status','succeeded','result_object_id',command->>'object_id'); end if;
  elsif action='release' then result:=result||pg_catalog.jsonb_build_object('status','queued','reason_code',command->>'reason_code');
  else result:=result||pg_catalog.jsonb_build_object('status','failed','reason_code',command->>'reason_code'); end if;
 end if;
 if result->>'status'='running' then
  lease_end:=least(recorded+pg_catalog.make_interval(secs=>(spec->>'lease_seconds')::double precision),selflearn_private.job_clock_v1(result->>'runtime_deadline_utc'));
  result:=result||pg_catalog.jsonb_build_object('lease_until_utc',selflearn_private.job_stamp_v1(lease_end));
 else result:=result||pg_catalog.jsonb_build_object('lease_until_utc',null,'worker_id',null,'lease_id',null); end if;
 return result;
end $$;

create function selflearn_private.job_reference_v1(identity text, recorded timestamptz) returns void
language plpgsql security invoker set search_path='' as $$
begin
 if not exists(select 1 from selflearn_private.journal_objects where object_id=identity and kind='source_snapshot'
  and stored_utc<=recorded and selflearn_private.digest_json_v1(payload_json::json)=identity) then
  raise sqlstate 'PT422' using message='invalid_learning_job_reference'; end if;
end $$;

create function selflearn_private.guard_learning_job_v1() returns trigger
language plpgsql security invoker set search_path='' as $$
declare spec jsonb; command jsonb; prior jsonb; state jsonb; event jsonb; registered timestamptz; n integer; used bigint;
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed' then raise sqlstate 'PT422' using message='learning_job_requires_read_committed'; end if;
 if tg_table_name='learning_jobs_v1' then
  spec:=selflearn_private.job_definition_v1(new.definition_json,new.authority_id);
  new.job_id:=spec->>'job_id';new.namespace_json:=selflearn_private.canonical_json_v1((spec->'namespace')::json);
  perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(new.authority_id||new.job_id));
  new.registered_utc:=pg_catalog.clock_timestamp();
  perform selflearn_private.job_reference_v1(spec->>'input_object_id',new.registered_utc);
  perform selflearn_private.job_reference_v1(spec->>'policy_object_id',new.registered_utc);
 else
  command:=selflearn_private.job_command_v1(new.command_json,new.authority_id);
  if new.job_id is distinct from command->>'job_id' then raise sqlstate 'PT422' using message='learning_job_columns_mismatch'; end if;
  perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(new.authority_id||new.job_id));
  select definition_json::jsonb,registered_utc into spec,registered from selflearn_private.learning_jobs_v1 where authority_id=new.authority_id and job_id=new.job_id;
  if not found then raise sqlstate 'PT422' using message='learning_job_absent'; end if;
  select event_json::jsonb into prior from selflearn_private.learning_job_events_v1 where authority_id=new.authority_id and job_id=new.job_id order by sequence desc limit 1;
  n:=coalesce((prior->>'sequence')::integer,0);
  if n>=128 then raise sqlstate 'PT422' using message='learning_job_event_limit'; end if;
  if command->>'expected_head_event_id' is distinct from prior->>'event_id' then raise sqlstate 'PT409' using message='learning_job_head_conflict'; end if;
  new.recorded_utc:=pg_catalog.clock_timestamp();
  if new.recorded_utc<coalesce((prior->>'recorded_utc')::timestamptz,registered) then raise sqlstate 'PT422' using message='learning_job_clock_regressed'; end if;
  state:=selflearn_private.job_advance_v1(spec,coalesce(prior->'state',selflearn_private.job_initial_v1()),command,new.recorded_utc);
  if command->>'object_id' is not null then perform selflearn_private.job_reference_v1(command->>'object_id',new.recorded_utc); end if;
  event:=pg_catalog.jsonb_build_object('version',1,'kind','selflearn.job_event.v1','authority_id',new.authority_id,'job_id',new.job_id,'definition_id',spec->>'definition_id',
   'sequence',n+1,'previous_event_id',command->'expected_head_event_id','command',command,'recorded_utc',selflearn_private.job_stamp_v1(new.recorded_utc),'state',state);
  new.event_id:=selflearn_private.digest_json_v1(event::json);event:=event||pg_catalog.jsonb_build_object('event_id',new.event_id);
  new.sequence:=n+1;new.command_id:=command->>'command_id';new.event_json:=selflearn_private.canonical_json_v1(event::json);
  select coalesce(sum(pg_catalog.octet_length(event_json)),0) into used from selflearn_private.learning_job_events_v1 where authority_id=new.authority_id and job_id=new.job_id;
  if used+pg_catalog.octet_length(new.event_json)+pg_catalog.octet_length(spec::text)>262144-8192 then raise sqlstate 'PT422' using message='learning_job_history_byte_limit'; end if;
 end if;
 return new;
end $$;
create trigger learning_job_guard before insert on selflearn_private.learning_jobs_v1 for each row execute function selflearn_private.guard_learning_job_v1();
create trigger learning_job_event_guard before insert on selflearn_private.learning_job_events_v1 for each row execute function selflearn_private.guard_learning_job_v1();

create function selflearn_private.job_ack_v1(event jsonb) returns jsonb
language sql immutable security invoker set search_path='' as $$
 select pg_catalog.jsonb_build_object('version',1,'authority_id',event->'authority_id','job_id',event->'job_id','command_id',event->'command'->'command_id',
 'event_id',event->'event_id','sequence',event->'sequence','recorded_utc',event->'recorded_utc','state',event->'state',
 'durable',true,'current_lease_authority',false,'activation',false);
$$;

create function public.selflearn_jobs_read_v1(p_authority_id text,p_job_id text) returns json
language plpgsql security invoker set search_path='' as $$
declare row selflearn_private.learning_jobs_v1%rowtype; history jsonb; state jsonb; effective text; observed timestamptz; answer jsonb;
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed' or p_authority_id is null or not(p_authority_id ~ '^sha256:[0-9a-f]{64}$')
  or p_job_id is null or not(p_job_id ~ '^sha256:[0-9a-f]{64}$') then raise sqlstate 'PT422' using message='invalid_learning_job_read'; end if;
 perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(p_authority_id||p_job_id));
 select * into row from selflearn_private.learning_jobs_v1 where authority_id=p_authority_id and job_id=p_job_id;
 if not found then raise sqlstate 'PT422' using message='learning_job_absent'; end if;
 select coalesce(pg_catalog.jsonb_agg(event_json::jsonb order by sequence),'[]'::jsonb) into history from selflearn_private.learning_job_events_v1 where authority_id=p_authority_id and job_id=p_job_id;
 state:=coalesce(history->-1->'state',selflearn_private.job_initial_v1());effective:=state->>'status';observed:=pg_catalog.clock_timestamp();
 if observed<coalesce((history->-1->>'recorded_utc')::timestamptz,row.registered_utc) then raise sqlstate 'PT422' using message='learning_job_clock_regressed'; end if;
 if effective not in('succeeded','failed') then
  if observed>=selflearn_private.job_clock_v1(row.definition_json::jsonb->>'not_after_utc') then effective:='expired';
  elsif state->>'runtime_deadline_utc' is not null and observed>=selflearn_private.job_clock_v1(state->>'runtime_deadline_utc') then effective:='runtime_limit';
  elsif effective='queued' and observed<selflearn_private.job_clock_v1(row.definition_json::jsonb->>'not_before_utc') then effective:='not_due';
  elsif effective='running' and observed>=selflearn_private.job_clock_v1(state->>'lease_until_utc') then effective:=case when (state->>'attempt')::integer>=(row.definition_json::jsonb->>'max_attempts')::integer then 'attempt_limit'else'expired_lease'end;
  elsif effective='queued' and (state->>'attempt')::integer>=(row.definition_json::jsonb->>'max_attempts')::integer then effective:='attempt_limit';end if;
 end if;
 answer:=pg_catalog.jsonb_build_object('version',1,'authority_id',p_authority_id,'definition',row.definition_json::jsonb,
  'registered_utc',selflearn_private.job_stamp_v1(row.registered_utc),'observed_utc',selflearn_private.job_stamp_v1(observed),
  'events',history,'head_event_id',history->-1->'event_id','state',state,'effective_status',effective,'activation',false,'exactly_once_compute',false);
 if pg_catalog.octet_length(selflearn_private.canonical_json_v1(answer::json))>262144 then raise sqlstate 'PT422' using message='learning_job_history_byte_limit'; end if;
 return answer::json;
end $$;

create function public.selflearn_jobs_register_v1(p_authority_id text,p_definition_json text) returns json
language plpgsql security invoker set search_path='' as $$
declare spec jsonb; old text;
begin
 spec:=selflearn_private.job_definition_v1(p_definition_json,p_authority_id);
 perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(p_authority_id||(spec->>'job_id')));
 select definition_json into old from selflearn_private.learning_jobs_v1 where authority_id=p_authority_id and job_id=spec->>'job_id';
 if found then
  if old is distinct from p_definition_json then raise sqlstate 'PT409' using message='learning_job_definition_conflict'; end if;
 else insert into selflearn_private.learning_jobs_v1(authority_id,definition_json) values(p_authority_id,p_definition_json);end if;
 return public.selflearn_jobs_read_v1(p_authority_id,spec->>'job_id');
end $$;

create function public.selflearn_jobs_append_v1(p_authority_id text,p_command_json text) returns json
language plpgsql security invoker set search_path='' as $$
declare command jsonb; old jsonb; result jsonb;
begin
 command:=selflearn_private.job_command_v1(p_command_json,p_authority_id);
 perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(p_authority_id||(command->>'job_id')));
 select event_json::jsonb into old from selflearn_private.learning_job_events_v1 where authority_id=p_authority_id and job_id=command->>'job_id' and command_id=command->>'command_id';
 if found then
  if selflearn_private.canonical_json_v1((old->'command')::json) is distinct from p_command_json then raise sqlstate 'PT409' using message='learning_job_command_conflict'; end if;
  return selflearn_private.job_ack_v1(old)::json;
 end if;
 insert into selflearn_private.learning_job_events_v1(authority_id,job_id,command_json) values(p_authority_id,command->>'job_id',p_command_json) returning event_json::jsonb into result;
 return selflearn_private.job_ack_v1(result)::json;
end $$;

revoke execute on function selflearn_private.job_keys_v1(jsonb,text[]),selflearn_private.job_clock_v1(text),selflearn_private.job_stamp_v1(timestamptz),
 selflearn_private.job_definition_v1(text,text),selflearn_private.job_command_v1(text,text),selflearn_private.job_initial_v1(),
 selflearn_private.job_advance_v1(jsonb,jsonb,jsonb,timestamptz),selflearn_private.job_reference_v1(text,timestamptz),
 selflearn_private.guard_learning_job_v1(),selflearn_private.job_ack_v1(jsonb) from public,anon,authenticated;
grant execute on function selflearn_private.job_keys_v1(jsonb,text[]),selflearn_private.job_clock_v1(text),selflearn_private.job_stamp_v1(timestamptz),
 selflearn_private.job_definition_v1(text,text),selflearn_private.job_command_v1(text,text),selflearn_private.job_initial_v1(),
 selflearn_private.job_advance_v1(jsonb,jsonb,jsonb,timestamptz),selflearn_private.job_reference_v1(text,timestamptz),
 selflearn_private.guard_learning_job_v1(),selflearn_private.job_ack_v1(jsonb) to service_role;
revoke execute on function public.selflearn_jobs_read_v1(text,text),public.selflearn_jobs_register_v1(text,text),public.selflearn_jobs_append_v1(text,text) from public,anon,authenticated;
grant execute on function public.selflearn_jobs_read_v1(text,text),public.selflearn_jobs_register_v1(text,text),public.selflearn_jobs_append_v1(text,text) to service_role;
commit;
