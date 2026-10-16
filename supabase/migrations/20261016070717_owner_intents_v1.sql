-- Additive single-owner pilot intent journal. NO serving/model writes; NO
-- outcome/training watermark participation. Not a Supabase Auth/tenant claim:
-- the backend service credential and operator HTTP boundary are trusted.
begin;

create sequence selflearn_private.owner_intent_sequence_v1;
create table selflearn_private.owner_intent_events_v1 (
  authority_id text not null,
  actor_ref text not null,
  command_json text not null,
  namespace_json text not null,
  namespace_sequence integer not null check(namespace_sequence between 1 and 256),
  command_id text not null,
  event_id text not null unique,
  previous_event_id text,
  event_json text not null,
  result_json text not null,
  ledger_seq bigint not null unique,
  stored_utc timestamptz not null,
  primary key(authority_id,namespace_json,command_id),
  unique(authority_id,namespace_json,namespace_sequence)
);
create index owner_intent_scope_sequence_v1 on selflearn_private.owner_intent_events_v1(authority_id,namespace_json,ledger_seq);
alter table selflearn_private.owner_intent_events_v1 enable row level security;
alter table selflearn_private.owner_intent_events_v1 force row level security;
revoke all on selflearn_private.owner_intent_events_v1 from public,anon,authenticated,service_role;
grant select on selflearn_private.owner_intent_events_v1 to service_role;
grant insert(authority_id,actor_ref,command_json) on selflearn_private.owner_intent_events_v1 to service_role;
revoke all on sequence selflearn_private.owner_intent_sequence_v1 from public,anon,authenticated,service_role;
grant usage on sequence selflearn_private.owner_intent_sequence_v1 to service_role;

create function selflearn_private.owner_keys_v1(doc jsonb, names text[]) returns boolean
language plpgsql immutable security invoker set search_path='' as $$
begin
 if pg_catalog.jsonb_typeof(doc) is distinct from 'object' then return false; end if;
 return (select pg_catalog.array_agg(k order by k) from pg_catalog.jsonb_object_keys(doc) k)
  is not distinct from (select pg_catalog.array_agg(k order by k) from pg_catalog.unnest(names) k);
end
$$;

create function selflearn_private.owner_namespace_v1(doc jsonb) returns text
language plpgsql immutable strict security invoker set search_path='' as $$
declare k text;
begin
 if not selflearn_private.owner_keys_v1(doc,array['domain','task','producer_id']) then
  raise sqlstate 'PT422' using message='invalid_owner_namespace'; end if;
 foreach k in array array['domain','task','producer_id'] loop
  if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string' or not(doc->>k ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
   raise sqlstate 'PT422' using message='invalid_owner_namespace'; end if;
 end loop;
 return selflearn_private.canonical_json_v1(doc::json);
end $$;

create function selflearn_private.owner_clock_v1(value text) returns timestamptz
language plpgsql immutable strict security invoker set search_path='' as $$
declare stamp timestamptz;
begin
 -- PostgreSQL accepts ISO24:00 and normalizes it; Python's frozen protocol
 -- rejects it. Validate hour/minute tokens BEFORE the timestamptz cast.
 if value is null or pg_catalog.length(value)>40 or not(value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T(0[0-9]|1[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](\.[0-9]{1,6})?(Z|[+-](0[0-9]|1[0-5]):[0-5][0-9])$') then
  raise sqlstate 'PT422' using message='invalid_owner_clock'; end if;
 stamp:=value::timestamptz;
 if extract(year from stamp at time zone 'UTC') not between 2000 and 2100 then
  raise sqlstate 'PT422' using message='invalid_owner_clock'; end if;
 return stamp;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_owner_clock';
end $$;

create function selflearn_private.owner_command_v1(payload text, authority text) returns jsonb
language plpgsql immutable security invoker set search_path='' as $$
declare parsed json; doc jsonb; pins jsonb; k text; refs text[]; expected text; whitespace text;
begin
 parsed:=selflearn_private.require_canonical_v1(payload,131072); doc:=parsed::jsonb; pins:=doc->'pins';
 if authority is null or not(authority ~ '^sha256:[0-9a-f]{64}$') or not selflearn_private.owner_keys_v1(doc,
  array['version','kind','namespace','action','expected_head_event_id','pins','effective_from_utc','expires_utc','reason','command_id'])
  or pg_catalog.jsonb_typeof(doc->'version') is distinct from 'number' or doc->>'version' is distinct from '1'
  or doc->>'kind' is distinct from 'selflearn.owner_intent_command.v1'
  or pg_catalog.jsonb_typeof(doc->'action') is distinct from 'string' or doc->>'action' not in('approve','revoke','rollback') then
  raise sqlstate 'PT422' using message='invalid_owner_command'; end if;
 perform selflearn_private.owner_namespace_v1(doc->'namespace');
 if doc->'expected_head_event_id'<>'null'::jsonb and (pg_catalog.jsonb_typeof(doc->'expected_head_event_id') is distinct from 'string'
  or not(doc->>'expected_head_event_id' ~ '^sha256:[0-9a-f]{64}$')) then
  raise sqlstate 'PT422' using message='invalid_owner_parent'; end if;
 if not selflearn_private.owner_keys_v1(pins,array['candidate_id','artifact_id','evaluation_report_id','policy_id',
    'generation_context_id','expected_previous_current_id','evidence_object_ids']) then
  raise sqlstate 'PT422' using message='invalid_owner_pins'; end if;
 foreach k in array array['candidate_id','artifact_id','evaluation_report_id','policy_id','generation_context_id','expected_previous_current_id'] loop
  if pg_catalog.jsonb_typeof(pins->k) is distinct from 'string' or not(pins->>k ~ '^sha256:[0-9a-f]{64}$') then
   raise sqlstate 'PT422' using message='invalid_owner_pins'; end if;
 end loop;
 if pg_catalog.jsonb_typeof(pins->'evidence_object_ids') is distinct from 'array' then
  raise sqlstate 'PT422' using message='invalid_owner_references'; end if;
 if pg_catalog.jsonb_array_length(pins->'evidence_object_ids') not between 1 and 16 or exists(
  select 1 from pg_catalog.jsonb_array_elements(pins->'evidence_object_ids') x
  where pg_catalog.jsonb_typeof(x) is distinct from 'string' or not(x#>>'{}' ~ '^sha256:[0-9a-f]{64}$')) then
  raise sqlstate 'PT422' using message='invalid_owner_references'; end if;
 select pg_catalog.array_agg(x order by n) into refs from pg_catalog.jsonb_array_elements_text(pins->'evidence_object_ids') with ordinality e(x,n);
 if refs is distinct from array(select distinct x from pg_catalog.unnest(refs) x order by x) then
  raise sqlstate 'PT422' using message='invalid_owner_references'; end if;
 -- Python str.strip whitespace, not only SQL's default U+0020 trim.
 whitespace:=E' \t\n\r'||chr(11)||chr(12)||chr(28)||chr(29)||chr(30)||chr(31)||chr(133)||chr(160)||chr(5760)||
  chr(8192)||chr(8193)||chr(8194)||chr(8195)||chr(8196)||chr(8197)||chr(8198)||chr(8199)||chr(8200)||chr(8201)||chr(8202)||chr(8232)||chr(8233)||chr(8239)||chr(8287)||chr(12288);
 if pg_catalog.jsonb_typeof(doc->'reason') is distinct from 'string' or pg_catalog.length(doc->>'reason') not between 1 and 1000
  or pg_catalog.btrim(doc->>'reason',whitespace)='' then
  raise sqlstate 'PT422' using message='invalid_owner_reason'; end if;
 if doc->>'action'='revoke' then
  if doc->'effective_from_utc' is distinct from 'null'::jsonb or doc->'expires_utc' is distinct from 'null'::jsonb then
   raise sqlstate 'PT422' using message='revocation_must_be_immediate_permanent'; end if;
 else
  if pg_catalog.jsonb_typeof(doc->'expires_utc') is distinct from 'string' then
   raise sqlstate 'PT422' using message='invalid_owner_clock'; end if;
  perform selflearn_private.owner_clock_v1(doc->>'expires_utc');
  if doc->'effective_from_utc'<>'null'::jsonb then
   if pg_catalog.jsonb_typeof(doc->'effective_from_utc') is distinct from 'string'
    or selflearn_private.owner_clock_v1(doc->>'effective_from_utc')>=selflearn_private.owner_clock_v1(doc->>'expires_utc') then
    raise sqlstate 'PT422' using message='invalid_owner_clock'; end if;
  end if;
 end if;
 expected:=selflearn_private.digest_json_v1(pg_catalog.jsonb_build_object('version',1,'kind','selflearn.owner_intent_identity.v1',
   'authority_id',authority,'command',doc-'command_id')::json);
 if pg_catalog.jsonb_typeof(doc->'command_id') is distinct from 'string' or doc->>'command_id' is distinct from expected then
  raise sqlstate 'PT422' using message='owner_command_hash_mismatch'; end if;
 return doc;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_owner_command';
end $$;

create function selflearn_private.owner_action_v1(command jsonb, history jsonb) returns void
language plpgsql immutable security invoker set search_path='' as $$
declare item jsonb; prior jsonb; revoked boolean:=false; k text;
begin
 if command->>'action'<>'rollback' then return; end if;
 for item in select value->'command' from pg_catalog.jsonb_array_elements(history) loop
  if item->'pins'->>'artifact_id'=command->'pins'->>'artifact_id' then
   if item->>'action'='approve' then prior:=item->'pins'; revoked:=false;
   elsif item->>'action'='revoke' then revoked:=true; end if;
  end if;
 end loop;
 if prior is null or revoked then raise sqlstate 'PT422' using message='rollback_intent_target_unavailable'; end if;
 foreach k in array array['candidate_id','artifact_id','evaluation_report_id','policy_id'] loop
  if prior->k is distinct from command->'pins'->k then
   raise sqlstate 'PT422' using message='rollback_intent_target_unavailable'; end if;
 end loop;
end $$;

create function selflearn_private.owner_history_v1(authority text, ns text, ceiling bigint) returns jsonb
language plpgsql security invoker set search_path='' as $$
declare history jsonb:='[]'; row selflearn_private.owner_intent_events_v1%rowtype; event jsonb; command jsonb;
 n integer:=0; previous text; last_clock timestamptz;
begin
 for row in select * from selflearn_private.owner_intent_events_v1 e
  where e.authority_id=authority and e.namespace_json=ns and e.ledger_seq<=ceiling order by e.namespace_sequence loop
  n:=n+1;
  if n>256 then raise sqlstate 'PT503' using message='incomplete_owner_history'; end if;
  event:=selflearn_private.require_canonical_v1(row.event_json,135168)::jsonb;
  command:=selflearn_private.owner_command_v1(row.command_json,authority);
  if not selflearn_private.owner_keys_v1(event,array['version','kind','authority_id','actor_ref','namespace','sequence',
   'previous_event_id','command','recorded_utc','effective_from_utc','event_id']) or event->>'version' is distinct from '1'
   or pg_catalog.jsonb_typeof(event->'version') is distinct from 'number' or event->>'kind' is distinct from 'selflearn.owner_intent_event.v1'
   or event->>'authority_id' is distinct from authority or event->>'actor_ref' is distinct from row.actor_ref
   or pg_catalog.jsonb_typeof(event->'sequence') is distinct from 'number' or event->>'sequence' is distinct from n::text
   or row.namespace_sequence<>n or row.event_id is distinct from event->>'event_id' or row.command_id is distinct from command->>'command_id'
   or row.previous_event_id is distinct from previous or event->>'previous_event_id' is distinct from previous
   or command->>'expected_head_event_id' is distinct from previous or event->'command' is distinct from command
   or selflearn_private.owner_namespace_v1(event->'namespace') is distinct from ns
   or selflearn_private.owner_namespace_v1(command->'namespace') is distinct from ns
   or event->>'event_id' is distinct from selflearn_private.digest_json_v1(event::json,'event_id')
   or selflearn_private.owner_clock_v1(event->>'recorded_utc') is distinct from row.stored_utc
   or (last_clock is not null and last_clock>row.stored_utc)
   or not(row.actor_ref ~ '^[ -~]{1,200}$') or pg_catalog.btrim(row.actor_ref)<>row.actor_ref
   or selflearn_private.owner_clock_v1(event->>'effective_from_utc')<row.stored_utc
   or selflearn_private.owner_clock_v1(event->>'effective_from_utc') is distinct from coalesce(selflearn_private.owner_clock_v1(command->>'effective_from_utc'),row.stored_utc)
   or (command->>'action'<>'revoke' and selflearn_private.owner_clock_v1(event->>'effective_from_utc')>=selflearn_private.owner_clock_v1(command->>'expires_utc')) then
   raise sqlstate 'PT503' using message='incomplete_owner_history'; end if;
  perform selflearn_private.owner_action_v1(command,history);
  history:=history||pg_catalog.jsonb_build_array(event); previous:=row.event_id; last_clock:=row.stored_utc;
 end loop;
 return history;
end $$;

create function selflearn_private.owner_ack_v1(event jsonb) returns jsonb
language sql immutable security invoker set search_path='' as $$
 select pg_catalog.jsonb_build_object('version',1,'authority_id',event->>'authority_id','namespace',event->'namespace',
  'command_id',event->'command'->>'command_id','event_id',event->>'event_id','sequence',(event->>'sequence')::integer,
  'recorded_utc',event->>'recorded_utc','durable',true,'scope','owner_intent_record_only',
  'content_hash_verified',true,'domain_pin_binding_verified',false,'current_serving_verified',false,'activation',false)
$$;

create function selflearn_private.guard_owner_intent_v1() returns trigger
language plpgsql security invoker set search_path='' as $$
declare command jsonb; history jsonb; previous text; event jsonb; ref text; obj selflearn_private.journal_objects%rowtype;
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed' then
  raise sqlstate 'PT422' using message='owner_requires_read_committed'; end if;
 perform pg_catalog.pg_advisory_xact_lock(619044,1);
 command:=selflearn_private.owner_command_v1(new.command_json,new.authority_id);
 if new.actor_ref is null or not(new.actor_ref ~ '^[ -~]{1,200}$') or pg_catalog.btrim(new.actor_ref)<>new.actor_ref then
  raise sqlstate 'PT422' using message='invalid_owner_actor'; end if;
 new.namespace_json:=selflearn_private.owner_namespace_v1(command->'namespace');
 history:=selflearn_private.owner_history_v1(new.authority_id,new.namespace_json,9223372036854775807);
 if pg_catalog.jsonb_array_length(history)>=256 then raise sqlstate 'PT429' using message='owner_intent_capacity_exceeded'; end if;
 previous:=history->-1->>'event_id';
 if command->>'expected_head_event_id' is distinct from previous then
  raise sqlstate 'PT409' using message='owner_head_conflict'; end if;
 perform selflearn_private.owner_action_v1(command,history);
 new.stored_utc:=pg_catalog.clock_timestamp();
 if pg_catalog.jsonb_array_length(history)>0 and new.stored_utc<selflearn_private.owner_clock_v1(history->-1->>'recorded_utc') then
  raise sqlstate 'PT503' using message='owner_clock_regression'; end if;
 for ref in select value from pg_catalog.jsonb_array_elements_text(command->'pins'->'evidence_object_ids') loop
  select * into obj from selflearn_private.journal_objects where object_id=ref;
  if not found or obj.kind<>'source_snapshot' or selflearn_private.digest_json_v1(obj.payload_json::json) is distinct from ref
   or obj.stored_utc>new.stored_utc then raise sqlstate 'PT422' using message='invalid_owner_evidence_reference'; end if;
 end loop;
 if command->>'action'<>'revoke' and (selflearn_private.owner_clock_v1(command->>'expires_utc')<=new.stored_utc
  or (command->'effective_from_utc'<>'null'::jsonb and selflearn_private.owner_clock_v1(command->>'effective_from_utc')<new.stored_utc)) then
  raise sqlstate 'PT422' using message='expired_owner_command'; end if;
 new.namespace_sequence:=pg_catalog.jsonb_array_length(history)+1; new.previous_event_id:=previous;
 new.command_id:=command->>'command_id';
 event:=pg_catalog.jsonb_build_object('version',1,'kind','selflearn.owner_intent_event.v1','authority_id',new.authority_id,
  'actor_ref',new.actor_ref,'namespace',command->'namespace','sequence',new.namespace_sequence,'previous_event_id',previous,
  'command',command,'recorded_utc',pg_catalog.to_char(new.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
  'effective_from_utc',coalesce(command->>'effective_from_utc',pg_catalog.to_char(new.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')));
 new.event_id:=selflearn_private.digest_json_v1(event::json); event:=event||pg_catalog.jsonb_build_object('event_id',new.event_id);
 -- A durable event must not make the complete Python ensure_ascii history
 -- unreadable. Reserve8192bytes for snapshot framing inside its3MiB limit.
 -- jsonb::text counts raw UTF8 and would undercharge supplementary Unicode.
 -- Check BEFORE nextval too: quota failures do not consume owner sequences.
 if pg_catalog.octet_length(selflearn_private.canonical_json_v1((history||pg_catalog.jsonb_build_array(event))::json))>3137536 then
  raise sqlstate 'PT429' using message='owner_intent_capacity_exceeded'; end if;
 new.ledger_seq:=pg_catalog.nextval('selflearn_private.owner_intent_sequence_v1'::regclass);
 new.event_json:=selflearn_private.canonical_json_v1(event::json);
 new.result_json:=selflearn_private.canonical_json_v1(selflearn_private.owner_ack_v1(event)::json);
 return new;
end $$;
create trigger owner_intent_insert_v1 before insert on selflearn_private.owner_intent_events_v1 for each row execute function selflearn_private.guard_owner_intent_v1();
create trigger owner_intent_immutable_v1 before update or delete on selflearn_private.owner_intent_events_v1 for each row execute function selflearn_private.refuse_mutation_v1();
create trigger owner_intent_no_truncate_v1 before truncate on selflearn_private.owner_intent_events_v1 for each statement execute function selflearn_private.refuse_mutation_v1();

create function public.selflearn_journal_append_owner_intent_v1(p_command_json text,p_authority_id text,p_actor_ref text) returns json
language plpgsql security invoker set search_path='' as $$
declare command jsonb; ns text; old selflearn_private.owner_intent_events_v1%rowtype; result text;
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed' then raise sqlstate 'PT422' using message='owner_requires_read_committed'; end if;
 perform pg_catalog.pg_advisory_xact_lock(619044,1);
 command:=selflearn_private.owner_command_v1(p_command_json,p_authority_id); ns:=selflearn_private.owner_namespace_v1(command->'namespace');
 select * into old from selflearn_private.owner_intent_events_v1 where authority_id=p_authority_id and namespace_json=ns and command_id=command->>'command_id';
 if found then
  perform selflearn_private.owner_history_v1(p_authority_id,ns,9223372036854775807);
  if old.command_json is distinct from p_command_json then raise sqlstate 'PT409' using message='owner_command_conflict'; end if;
  return old.result_json::json; -- Original historical ACK BEFORE current CAS/expiry.
 end if;
 -- Novel commands MUST go through the mandatory input INSERT trigger, which
 -- validates the complete history under this SAME lock. Do not scan it twice.
 insert into selflearn_private.owner_intent_events_v1(authority_id,actor_ref,command_json) values(p_authority_id,p_actor_ref,p_command_json) returning result_json into result;
 return result::json;
end $$;

create function public.selflearn_journal_read_owner_intents_v1(p_authority_id text,p_namespace_json text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path='' as $$
declare ns text; maximum bigint; fence bigint; cursor bigint; history jsonb; selected jsonb; has_more boolean; result jsonb; n integer;
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed' then raise sqlstate 'PT422' using message='owner_requires_read_committed'; end if;
 if p_authority_id is null or not(p_authority_id ~ '^sha256:[0-9a-f]{64}$') or p_limit is null or p_limit not between 1 and 100
  or (p_after is not null and not(p_after ~ '^(0|[1-9][0-9]{0,18})$')) or (p_watermark is not null and not(p_watermark ~ '^(0|[1-9][0-9]{0,18})$')) then
  raise sqlstate 'PT422' using message='invalid_owner_read'; end if;
 ns:=selflearn_private.owner_namespace_v1(selflearn_private.require_canonical_v1(p_namespace_json,1024)::jsonb);
 perform pg_catalog.pg_advisory_xact_lock(619044,1);
 select coalesce(max(ledger_seq),0) into maximum from selflearn_private.owner_intent_events_v1;
 fence:=coalesce(p_watermark::bigint,maximum); cursor:=coalesce(p_after::bigint,0);
 if cursor>fence or fence>maximum then raise sqlstate 'PT422' using message='invalid_owner_watermark'; end if;
 history:=selflearn_private.owner_history_v1(p_authority_id,ns,fence);
 select coalesce(pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('event',e.event_json::jsonb,'ledger_seq',e.ledger_seq::text) order by e.ledger_seq),'[]'::jsonb)
 into selected from (select event_json,ledger_seq from selflearn_private.owner_intent_events_v1 where authority_id=p_authority_id and namespace_json=ns and ledger_seq>cursor and ledger_seq<=fence order by ledger_seq limit p_limit+1) e;
 n:=pg_catalog.jsonb_array_length(selected); has_more:=n>p_limit;
 if has_more then selected:=selected-(n-1); end if;
 result:=pg_catalog.jsonb_build_object('version',1,'authority_id',p_authority_id,'namespace',ns::jsonb,'rows',selected,
  'next_after',case when has_more then selected->-1->>'ledger_seq' else null end,'watermark',fence::text,
  'head_sequence',pg_catalog.jsonb_array_length(history),'head_event_id',history->-1->>'event_id',
  'observed_utc',pg_catalog.to_char(pg_catalog.clock_timestamp() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
 if pg_catalog.octet_length(result::text)>3145728 then raise sqlstate 'PT422' using message='owner_page_exceeds_byte_limit'; end if;
 return result::json;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_owner_read';
end $$;

revoke execute on function selflearn_private.owner_keys_v1(jsonb,text[]),selflearn_private.owner_namespace_v1(jsonb),selflearn_private.owner_clock_v1(text),
 selflearn_private.owner_command_v1(text,text),selflearn_private.owner_action_v1(jsonb,jsonb),selflearn_private.owner_history_v1(text,text,bigint),
 selflearn_private.owner_ack_v1(jsonb),selflearn_private.guard_owner_intent_v1() from public,anon,authenticated;
grant execute on function selflearn_private.owner_keys_v1(jsonb,text[]),selflearn_private.owner_namespace_v1(jsonb),selflearn_private.owner_clock_v1(text),
 selflearn_private.owner_command_v1(text,text),selflearn_private.owner_action_v1(jsonb,jsonb),selflearn_private.owner_history_v1(text,text,bigint),
 selflearn_private.owner_ack_v1(jsonb),selflearn_private.guard_owner_intent_v1() to service_role;
revoke execute on function public.selflearn_journal_append_owner_intent_v1(text,text,text),public.selflearn_journal_read_owner_intents_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_append_owner_intent_v1(text,text,text),public.selflearn_journal_read_owner_intents_v1(text,text,text,integer,text) to service_role;
commit;
