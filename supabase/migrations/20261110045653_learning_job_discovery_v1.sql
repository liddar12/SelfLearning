-- Bounded read-only discovery. It grants no lease, performs no work, and does
-- not alter forecast/outcome/owner watermarks. Atomic claims remain mandatory.
begin;

create index learning_job_discovery_scope_v1 on selflearn_private.learning_jobs_v1
 (authority_id,namespace_json,((definition_json::jsonb)->>'operation'),
  ((definition_json::jsonb)->>'code_id'),((definition_json::jsonb)->>'policy_object_id'),registered_utc,job_id);
create index learning_job_latest_event_v1 on selflearn_private.learning_job_events_v1
 (authority_id,job_id,sequence desc) include(recorded_utc);

create function public.selflearn_jobs_discover_v1(p_authority_id text,p_namespace_json text,
 p_operation text,p_code_id text default null,p_policy_object_id text default null,p_limit integer default 16) returns json
language plpgsql volatile security invoker set search_path='' as $$
declare ns jsonb; observed timestamptz; found_ids json; k text;
begin
 if p_authority_id is null or p_authority_id !~ '^sha256:[0-9a-f]{64}$'
    or p_operation is null or p_operation !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$'
    or (p_code_id is not null and p_code_id !~ '^sha256:[0-9a-f]{64}$')
    or (p_policy_object_id is not null and p_policy_object_id !~ '^sha256:[0-9a-f]{64}$')
    or p_limit is null or p_limit not between 1 and 16 then
  raise sqlstate 'PT422' using message='invalid_job_discovery_scope';
 end if;
 ns:=selflearn_private.require_canonical_v1(p_namespace_json,1024)::jsonb;
 if not selflearn_private.job_keys_v1(ns,array['domain','task','producer_id']) then
  raise sqlstate 'PT422' using message='invalid_job_discovery_namespace';
 end if;
 foreach k in array array['domain','task','producer_id'] loop
  if pg_catalog.jsonb_typeof(ns->k) is distinct from 'string'
     or ns->>k !~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$' then
   raise sqlstate 'PT422' using message='invalid_job_discovery_namespace';
  end if;
 end loop;
 observed:=pg_catalog.clock_timestamp();
 with selected as (
  select j.job_id,j.registered_utc,coalesce(head.recorded_utc,j.registered_utc) as eligible_age
  from selflearn_private.learning_jobs_v1 j
  left join lateral (
   select e.event_json::jsonb as event,e.recorded_utc,e.sequence
   from selflearn_private.learning_job_events_v1 e
   where e.authority_id=j.authority_id and e.job_id=j.job_id
   order by e.sequence desc limit 1
  ) head on true
  where j.authority_id=p_authority_id and j.namespace_json=p_namespace_json
   and j.definition_json::jsonb->>'operation'=p_operation
   and (p_code_id is null or j.definition_json::jsonb->>'code_id'=p_code_id)
   and (p_policy_object_id is null or j.definition_json::jsonb->>'policy_object_id'=p_policy_object_id)
   and j.registered_utc<=observed and coalesce(head.recorded_utc,j.registered_utc)<=observed
   and selflearn_private.job_clock_v1(j.definition_json::jsonb->>'not_before_utc')<=observed
   and selflearn_private.job_clock_v1(j.definition_json::jsonb->>'not_after_utc')>observed
   and coalesce(head.sequence,0)<128
   and coalesce((head.event->'state'->>'attempt')::integer,0)<(j.definition_json::jsonb->>'max_attempts')::integer
   and (head.event->'state'->>'runtime_deadline_utc' is null
        or selflearn_private.job_clock_v1(head.event->'state'->>'runtime_deadline_utc')>observed)
   and (coalesce(head.event->'state'->>'status','queued')='queued'
        or (head.event->'state'->>'status'='running'
            and selflearn_private.job_clock_v1(head.event->'state'->>'lease_until_utc')<=observed))
  order by eligible_age,j.registered_utc,j.job_id limit p_limit
 )
 select coalesce(pg_catalog.json_agg(job_id order by eligible_age,registered_utc,job_id),'[]'::json)
 into found_ids from selected;
 return pg_catalog.json_build_object('version',1,'authority_id',p_authority_id,'namespace',ns,
  'operation',p_operation,'code_id',p_code_id,'policy_object_id',p_policy_object_id,
  'observed_utc',selflearn_private.job_stamp_v1(observed),
  'job_ids',found_ids);
exception when data_exception then
 raise sqlstate 'PT422' using message='invalid_job_discovery_scope';
end $$;

revoke execute on function public.selflearn_jobs_discover_v1(text,text,text,text,text,integer) from public,anon,authenticated;
grant execute on function public.selflearn_jobs_discover_v1(text,text,text,text,text,integer) to service_role;
commit;
