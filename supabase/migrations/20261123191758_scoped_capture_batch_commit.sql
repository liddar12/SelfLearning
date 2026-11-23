-- Keep the original guards and RPC for season/week applications. Scoped
-- writes use the same row/chain/clock triggers and global CAS with no repeat
-- recursive batch encoding or duplicate pre-validation of new observations.
begin;

create function selflearn_private.commit_scoped_capture_observed_v1(p_batch_json text,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare
  doc json; item json; observations json[]; revisions json[]; old text; ack json; expected bigint;
  added_observations integer:=0; added_revisions integer:=0;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  if p_watermark is null or p_watermark !~ '^(0|[1-9][0-9]{0,18})$' then
    raise sqlstate 'PT422' using message='invalid_observed_commit_watermark';
  end if;
  expected:=p_watermark::bigint;
  if expected is distinct from selflearn_private.observed_watermark_v1() then
    raise sqlstate 'PT409' using message='stale_observed_commit_watermark';
  end if;
  if p_batch_json is null or pg_catalog.octet_length(p_batch_json)>8388608 then
    raise sqlstate 'PT422' using message='invalid_journal_json';
  end if;
  doc:=p_batch_json::json;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(doc) key)
       is distinct from array['observations','revisions','version']::text[]
     or doc->>'version' is distinct from '1' or pg_catalog.json_typeof(doc->'version') is distinct from 'number'
     or pg_catalog.json_typeof(doc->'observations') is distinct from 'array'
     or pg_catalog.json_typeof(doc->'revisions') is distinct from 'array'
     or pg_catalog.json_array_length(doc->'observations')>500 or pg_catalog.json_array_length(doc->'revisions')>500 then
    raise sqlstate 'PT422' using message='invalid_observed_commit_batch';
  end if;
  observations:=array(select value from pg_catalog.json_array_elements(doc->'observations'));
  revisions:=array(select value from pg_catalog.json_array_elements(doc->'revisions'));
  -- The parsed json array retains each row's exact input bytes. Check the
  -- outer object/layout here, then verify each row independently at its
  -- existing mutation boundary. This preserves numeric lexemes and rejects
  -- duplicate keys or noncanonical bytes even on a replay.
  if p_batch_json is distinct from
       '{"observations":['||pg_catalog.array_to_string(observations,',')||
       '],"revisions":['||pg_catalog.array_to_string(revisions,',')||'],"version":1}' then
    raise sqlstate 'PT422' using message='noncanonical_journal_json';
  end if;
  foreach item in array observations loop
    select payload_json into old from selflearn_private.journal_observations
      where observation_id=item->>'observation_id' or
        (receipt_id=item->>'receipt_id' and evidence_observation_id=item->>'evidence_observation_id');
    if found then
      if old is distinct from item::text then raise sqlstate 'PT409' using message='observation_id_conflict'; end if;
    else
      -- The existing BEFORE INSERT trigger independently validates the
      -- exact observation bytes, digest, receipt binding and history.
      insert into selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json)
        values(item->>'observation_id',item->>'receipt_id',item->>'evidence_observation_id',item::text);
      added_observations:=added_observations+1;
    end if;
  end loop;
  foreach item in array revisions loop
    if item->'resolution'->>'evidence_basis' is distinct from 'nfl.observed_resolution_v2' then
      raise sqlstate 'PT422' using message='observed_batch_requires_observed_revision';
    end if;
    ack:=public.selflearn_journal_append_resolution_v1(item::text);
    if (ack->>'added')::boolean then added_revisions:=added_revisions+1; end if;
  end loop;
  if exists(select 1 from selflearn_private.journal_observations o where o.journal_seq>expected and not exists(
      select 1 from selflearn_private.journal_revisions r where r.receipt_id=o.receipt_id
      and r.payload_json::jsonb->'resolution'->>'evidence_basis'='nfl.observed_resolution_v2'
      and r.payload_json::jsonb->'resolution'->>'measurement_id'=o.payload_json::jsonb->>'measurement_id'
      and not exists(select 1 from selflearn_private.journal_revisions n
                     where n.receipt_id=r.receipt_id and n.journal_seq>r.journal_seq))) then
    raise sqlstate 'PT422' using message='observation_without_matching_outcome';
  end if;
  return pg_catalog.json_build_object('version',1,'added_observations',added_observations,
    'added_revisions',added_revisions,'watermark',selflearn_private.observed_watermark_v1()::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_observed_commit';
end $$;
revoke execute on function selflearn_private.commit_scoped_capture_observed_v1(text,text) from public,anon,authenticated;
grant execute on function selflearn_private.commit_scoped_capture_observed_v1(text,text) to service_role;

create or replace function selflearn_private.guard_capture_application_batch_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare app selflearn_private.capture_applications_v2%rowtype; seal json; assignment json; groups json; batch json; g json; sealed json;
  certificate selflearn_private.capture_membership_cache_v2%rowtype;
  population json; current_membership text; members jsonb; results json[]:=array[]::json[]; ack json; answer json;
  expected jsonb; seen_observations text[]:=array[]::text[]; seen_revisions text[]:=array[]::text[];
  ids jsonb; proof json; k text; rid text; previous text:=''; status text:='committed'; current_fence text;
  observed_rows json[]; revision_rows json[]; sealed_fact text;
begin
  perform selflearn_private.capture_lock_v2();
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_application_id',new.application_id,'p_step_id',new.step_id,
      'p_group_results_json',new.group_results_json,'p_batch_json',new.batch_json,'p_watermark',new.expected_watermark)::text)>8388608 then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  select * into app from selflearn_private.capture_applications_v2 where application_id=new.application_id;
  if not found then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  seal:=app.seal_json::json;
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_application_id',new.application_id,'p_step_id',new.step_id,
      'p_group_results_json',new.group_results_json,'p_batch_json',new.batch_json,'p_watermark',new.expected_watermark)::text)>(seal->'limits'->>'rpc_bytes')::bigint then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  select value into assignment from pg_catalog.json_array_elements(seal->'batches') where value->>'step_id'=new.step_id;
  if not found then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  new.ordinal:=(assignment->>'ordinal')::integer;
  if exists(select 1 from selflearn_private.capture_application_results_v2 where application_id=new.application_id) then
    raise sqlstate 'PT409' using message='capture_application_terminal';
  end if;
  if (select pg_catalog.count(*) from selflearn_private.capture_application_batches_v2 where application_id=new.application_id)<>new.ordinal then
    raise sqlstate 'PT409' using message='capture_application_step_out_of_order';
  end if;
  certificate:=selflearn_private.capture_get_membership_v3((seal->>'season')::integer,seal->'weeks',app.game_scope_json::json);
  members:=certificate.members; population:=certificate.groups::json; current_membership:=certificate.membership_id;
  if current_membership is distinct from app.membership_id then
    status:='not_applied';
    for sealed in select value from pg_catalog.json_array_elements(seal->'groups')
       where (assignment->'group_ids')::jsonb ? (value->>'group_id') order by value->>'group_id' loop
      results:=pg_catalog.array_append(results,selflearn_private.capture_held_group_v2(sealed,'["application_membership_changed"]'::json));
    end loop;
    groups:=pg_catalog.to_json(results); new.group_results_json:=selflearn_private.canonical_json_v1(groups);
    new.batch_json:='{"observations":[],"revisions":[],"version":1}';
    current_fence:=selflearn_private.observed_watermark_v1()::text; new.expected_watermark:=current_fence;
    ack:=pg_catalog.json_build_object('added_observations',0,'added_revisions',0,'watermark',current_fence);
  else
    perform selflearn_private.capture_fence_v2(new.expected_watermark);
    groups:=selflearn_private.require_canonical_v1(new.group_results_json,3145728);
    batch:=case when app.game_scope_json is null then selflearn_private.require_canonical_v1(new.batch_json,8388608)
                else new.batch_json::json end;
    if pg_catalog.json_typeof(groups) is distinct from 'array'
       or pg_catalog.json_array_length(groups)<>pg_catalog.json_array_length(assignment->'group_ids')
       or (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(batch) key) is distinct from array['observations','revisions','version']::text[]
       or pg_catalog.json_typeof(batch->'version') is distinct from 'number' or batch->>'version' is distinct from '1'
       or pg_catalog.json_typeof(batch->'observations') is distinct from 'array' or pg_catalog.json_typeof(batch->'revisions') is distinct from 'array'
       or pg_catalog.json_array_length(batch->'observations')>(seal->'limits'->>'observations')::integer
       or pg_catalog.json_array_length(batch->'revisions')>(seal->'limits'->>'revisions')::integer
       or (select pg_catalog.count(distinct value->'resolution'->>'receipt_id') from pg_catalog.json_array_elements(batch->'revisions'))
          <>pg_catalog.json_array_length(batch->'revisions') then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
    observed_rows:=array(select value from pg_catalog.json_array_elements(batch->'observations'));
    revision_rows:=array(select value from pg_catalog.json_array_elements(batch->'revisions'));
    for g in select value from pg_catalog.json_array_elements(groups) loop
      if (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(g) key) is distinct from
         array['generation_object_ids','group_id','membership_id','observation_ids','reason_codes','receipt_ids','revision_ids','scope','source_receipt_ids','status']::text[]
         or pg_catalog.json_typeof(g->'group_id') is distinct from 'string' or g->>'group_id'<=previous
         or not ((assignment->'group_ids')::jsonb ? (g->>'group_id')) then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      previous:=g->>'group_id';
      select value into sealed from pg_catalog.json_array_elements(seal->'groups') where value->>'group_id'=g->>'group_id';
      foreach k in array array['scope','receipt_ids','source_receipt_ids','generation_object_ids','membership_id'] loop
        if selflearn_private.canonical_json_v1(g->k) is distinct from selflearn_private.canonical_json_v1(sealed->k) then
          raise sqlstate 'PT422' using message='invalid_capture_application';
        end if;
      end loop;
      if pg_catalog.json_typeof(g->'status') is distinct from 'string' or g->>'status' not in ('accepted','withheld')
         or pg_catalog.json_typeof(g->'reason_codes') is distinct from 'array' or pg_catalog.json_array_length(g->'reason_codes')>100
         or exists(select 1 from pg_catalog.json_array_elements(g->'reason_codes') r where pg_catalog.json_typeof(r)<>'string' or not (r#>>'{}' ~ '^[a-z][a-z0-9_]{0,99}$'))
         or (select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) from (select distinct value::jsonb v from pg_catalog.json_array_elements(g->'reason_codes')) reasons) is distinct from (g->'reason_codes')::jsonb
         or (g->>'status'='accepted') is distinct from (pg_catalog.json_array_length(g->'reason_codes')=0) then
        raise sqlstate 'PT422' using message='invalid_capture_application';
      end if;
      foreach k in array array['observation_ids','revision_ids'] loop
        if pg_catalog.json_typeof(g->k) is distinct from 'array' or exists(select 1 from pg_catalog.json_array_elements(g->k) v where pg_catalog.json_typeof(v)<>'string')
           or (select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) from (select distinct value::jsonb v from pg_catalog.json_array_elements(g->k)) valueset) is distinct from (g->k)::jsonb then
          raise sqlstate 'PT422' using message='invalid_capture_application';
        end if;
      end loop;
      ids:=(g->'receipt_ids')::jsonb;
      select coalesce(pg_catalog.jsonb_agg(value->>'observation_id' order by value->>'observation_id'),'[]'::jsonb) into expected
        from pg_catalog.unnest(observed_rows) rows(value) where ids ? (value->>'receipt_id');
      if expected is distinct from (g->'observation_ids')::jsonb then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      select coalesce(pg_catalog.jsonb_agg(value->>'revision_id' order by value->>'revision_id'),'[]'::jsonb) into expected
        from pg_catalog.unnest(revision_rows) rows(value) where ids ? (value->'resolution'->>'receipt_id');
      if expected is distinct from (g->'revision_ids')::jsonb then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      if g->>'status'='withheld' then
        if pg_catalog.json_array_length(g->'observation_ids')<>0 or pg_catalog.json_array_length(g->'revision_ids')<>0 then
          raise sqlstate 'PT422' using message='invalid_capture_application';
        end if;
      elsif pg_catalog.json_array_length(g->'observation_ids')<>pg_catalog.json_array_length(g->'receipt_ids')
         or pg_catalog.json_array_length(g->'revision_ids')>pg_catalog.json_array_length(g->'receipt_ids')
         or (select pg_catalog.count(distinct value->>'receipt_id') from pg_catalog.unnest(observed_rows) rows(value)
             where ids ? (value->>'receipt_id'))<>pg_catalog.json_array_length(g->'receipt_ids') then
        raise sqlstate 'PT422' using message='invalid_capture_application';
      end if;
      sealed_fact:=selflearn_private.canonical_json_v1(sealed->'fact');
      for proof in select value from pg_catalog.unnest(observed_rows) rows(value) where ids ? (value->>'receipt_id') loop
        if selflearn_private.canonical_json_v1(selflearn_private.capture_fact_v2(proof)) is distinct from sealed_fact then
          raise sqlstate 'PT422' using message='invalid_capture_application';
        end if;
      end loop;
      for rid in select value from pg_catalog.json_array_elements_text(g->'observation_ids') loop seen_observations:=pg_catalog.array_append(seen_observations,rid); end loop;
      for rid in select value from pg_catalog.json_array_elements_text(g->'revision_ids') loop seen_revisions:=pg_catalog.array_append(seen_revisions,rid); end loop;
    end loop;
    if pg_catalog.cardinality(seen_observations)<>pg_catalog.json_array_length(batch->'observations')
       or pg_catalog.cardinality(seen_revisions)<>pg_catalog.json_array_length(batch->'revisions')
       or pg_catalog.cardinality(seen_observations)<>(select pg_catalog.count(distinct v) from pg_catalog.unnest(seen_observations) v)
       or pg_catalog.cardinality(seen_revisions)<>(select pg_catalog.count(distinct v) from pg_catalog.unnest(seen_revisions) v) then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
    perform selflearn_private.capture_roster_revisions_v1(app.request_json::jsonb->'capture_request'->'envelope',batch::jsonb);
    if app.game_scope_json is null then
      ack:=public.selflearn_journal_commit_observed_v1(new.batch_json,new.expected_watermark);
    else
      ack:=selflearn_private.commit_scoped_capture_observed_v1(new.batch_json,new.expected_watermark);
    end if;
  end if;
  new.application_seq:=pg_catalog.nextval('selflearn_private.capture_application_sequence_v2'::regclass); new.recorded_utc:=pg_catalog.clock_timestamp();
  answer:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_batch_result.v2','application_id',new.application_id,
    'step_id',new.step_id,'ordinal',new.ordinal,'seal_id',app.seal_id,'membership_id',app.membership_id,'status',status,
    'groups',groups,'added_observations',(ack->>'added_observations')::integer,'added_revisions',(ack->>'added_revisions')::integer,
    'input_watermark',new.expected_watermark,'watermark',ack->>'watermark','application_seq',new.application_seq::text,
    'recorded_utc',pg_catalog.to_char(new.recorded_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'durable',true,'scope','observations_and_batch_results','source_authentication','not_performed');
  new.batch_result_id:=selflearn_private.digest_json_v1(answer);
  new.result_json:=selflearn_private.canonical_json_v1((answer::jsonb||pg_catalog.jsonb_build_object('batch_result_id',new.batch_result_id))::json);
  if pg_catalog.octet_length(pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_page.v2','found',true,
      'application_id',new.application_id,'view','batches','ledger_watermark','9223372036854775807',
      'registration_id',app.registration_json::json->'registration_id','seal_id',app.seal_id,
      'records',pg_catalog.json_build_array(pg_catalog.json_build_object('assignment',assignment,'result',new.result_json::json)),
      'total_records',pg_catalog.json_array_length(seal->'batches'),'next_after',new.ordinal::text)::text)>3145728 then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  return new;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;

commit;
