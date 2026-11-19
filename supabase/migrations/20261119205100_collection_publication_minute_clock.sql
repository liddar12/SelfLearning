-- Adapt only provider kickoff precision. Job clocks and immutable receipt bytes stay strict.
begin;

create or replace function public.selflearn_nfl_collection_publication_v1(
  p_operation text,p_repository text,p_intent_id text,p_document_json text,p_limit integer,p_after text
) returns json language plpgsql security invoker set search_path='' as $$
declare
  original selflearn_private.nfl_collection_handoff_v1%rowtype;
  saved selflearn_private.nfl_collection_publication_v1%rowtype;
  intent_doc jsonb;
  publisher jsonb;
  record_doc jsonb;
  declaration jsonb;
  definition jsonb;
  generation jsonb;
  ack jsonb;
  declared_cutoff timestamptz;
  observed timestamptz;
  authority text;
  job_identity text;
  old_definition text;
  expected_input text;
  result json;
  pending json;
  cursor_id text;
begin
  if p_operation is null or p_operation not in ('read','prepare','find','register','pending')
      or p_repository is null or p_repository !~ '^[a-z0-9_.-]{1,100}/[a-z0-9_.-]{1,100}$' then
    raise sqlstate 'PT422' using message='invalid_collection_publication_scope';
  end if;
  if p_operation = 'pending' then
    if p_intent_id is not null or p_document_json is not null or p_limit is null or p_limit not between 1 and 50
        or (p_after is not null and p_after !~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message='invalid_collection_publication_scan';
    end if;
    observed := pg_catalog.clock_timestamp();
    with scan as materialized (
      select record_id,workflow,run_id,run_attempt,document_json::jsonb as intent
      from selflearn_private.nfl_collection_handoff_v1
      where repository=p_repository and stage='intent' and record_id>coalesce(p_after,'')
      order by record_id limit p_limit+1
    ), page as materialized (select * from scan order by record_id limit p_limit)
    select coalesce(pg_catalog.json_agg(pg_catalog.json_build_object('workflow',page.workflow,'run_id',page.run_id,
      'run_attempt',page.run_attempt,'intent_id',page.record_id) order by page.record_id) filter (where
      exists (select 1 from selflearn_private.journal_objects g
        where g.object_id=page.intent->>'generation_object_id' and g.kind='generation')
      and not exists (select 1 from pg_catalog.jsonb_each_text(page.intent->'source_snapshots') s
        where not exists (select 1 from selflearn_private.journal_objects o
          where o.object_id=s.value and o.kind='source_snapshot'))
      and case when j.job_id is null then (page.intent->>'latest_kickoff_utc')::timestamptz>observed
        else j.definition_json=selflearn_private.canonical_json_v1((prepared.doc->'definition')::json)
          and (prepared.doc->'definition'->>'not_after_utc')::timestamptz>observed
          and coalesce(head.state->>'status','queued') not in ('succeeded','failed')
          and ((head.state->>'runtime_deadline_utc') is null or (head.state->>'runtime_deadline_utc')::timestamptz>observed)
          and (coalesce((head.state->>'attempt')::integer,0)<(prepared.doc->'definition'->>'max_attempts')::integer
            or (head.state->>'status'='running' and (head.state->>'lease_until_utc')::timestamptz>observed)) end
    ),'[]'::json), case when (select pg_catalog.count(*) from scan)>p_limit then pg_catalog.max(page.record_id) end
    into pending,cursor_id from page
    left join lateral (select cp.document_json::jsonb as doc from selflearn_private.nfl_collection_publication_v1 cp
      where cp.intent_id=page.record_id) prepared on true
    left join selflearn_private.learning_jobs_v1 j on j.authority_id=prepared.doc->'declaration'->>'authority_id'
      and j.job_id=prepared.doc->'definition'->>'job_id'
    left join lateral (select ev.event_json::jsonb->'state' as state
      from selflearn_private.learning_job_events_v1 ev where ev.authority_id=j.authority_id and ev.job_id=j.job_id
      order by ev.sequence desc limit 1) head on true;
    return pg_catalog.json_build_object('version',1,'observed_utc',observed,'pending',pending,'next_after',cursor_id);
  end if;

  if p_intent_id is null or p_intent_id !~ '^sha256:[0-9a-f]{64}$' or p_limit is not null or p_after is not null
      or ((p_operation='prepare') is distinct from (p_document_json is not null)) then
    raise sqlstate 'PT422' using message='invalid_collection_publication_scope';
  end if;
  select h.* into original from selflearn_private.nfl_collection_handoff_v1 h
    where h.record_id=p_intent_id and h.repository=p_repository and h.stage='intent';
  if not found then
    if p_operation in ('read','find') then return 'null'::json; end if;
    raise sqlstate 'PT409' using message='collection_publication_intent_missing';
  end if;
  intent_doc := original.document_json::jsonb;
  declared_cutoff := selflearn_private.job_clock_v1(intent_doc->>'latest_kickoff_utc');

  if p_operation='prepare' then
    -- This lock is used only by preparation. Registration takes the existing
    -- generic job lock without taking the preparation lock, avoiding a cycle.
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(p_intent_id,923311));
  end if;
  select r.* into saved from selflearn_private.nfl_collection_publication_v1 r where r.intent_id=p_intent_id;
  if p_operation='prepare' then
    if saved.intent_id is not null then
      if saved.document_json is distinct from p_document_json then
        raise sqlstate 'PT409' using message='immutable_collection_publication_conflict';
      end if;
    else
      if pg_catalog.clock_timestamp()>=declared_cutoff then
        raise sqlstate 'PT410' using message='collection_publication_cutoff';
      end if;
      record_doc := selflearn_private.require_canonical_v1(p_document_json,8192)::jsonb;
      if not selflearn_private.job_keys_v1(record_doc,array['version','kind','intent_id','publisher_receipt_id',
          'generation_object_id','generation_id','declaration','definition','prepared_id'])
          or (record_doc->'version')::text is distinct from '1'
          or record_doc->>'kind' is distinct from 'nfl.collection_publication_definition.v1'
          or record_doc->>'prepared_id' is distinct from selflearn_private.digest_json_v1((record_doc-'prepared_id')::json)
          or record_doc->>'intent_id' is distinct from p_intent_id
          or record_doc->>'generation_id' is distinct from intent_doc->>'generation_id'
          or record_doc->>'generation_object_id' is distinct from intent_doc->>'generation_object_id' then
        raise sqlstate 'PT422' using message='invalid_collection_publication_definition';
      end if;
      select h.document_json::jsonb into publisher from selflearn_private.nfl_collection_handoff_v1 h
        where h.repository=p_repository and h.workflow=original.workflow and h.run_id=original.run_id
          and h.run_attempt=original.run_attempt and h.stage='publisher';
      if publisher is null or publisher->>'receipt_id' is distinct from record_doc->>'publisher_receipt_id'
          or publisher->>'intent_id' is distinct from p_intent_id or publisher->>'disposition' is distinct from 'match'
          or publisher->>'published_sha' is null or publisher->>'published_pool_id' is distinct from intent_doc->'source_snapshots'->>'leg_pool'
          or publisher->>'published_pool_id' is not distinct from publisher->>'previous_pool_id' then
        raise sqlstate 'PT409' using message='collection_publication_receipt_mismatch';
      end if;
      select c.ack_json::jsonb,o.payload_json::jsonb into ack,generation
        from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
        where c.object_id=intent_doc->>'generation_object_id' and o.kind='generation';
      if ack is null or ack->'durable' is distinct from 'true'::jsonb or pg_catalog.jsonb_array_length(ack->'receipts')=0
          or pg_catalog.jsonb_array_length(ack->'rejections')<>0
          or generation->'generations'->0->>'generation_id' is distinct from intent_doc->>'generation_id'
          or generation->'generations'->0->>'recorded_utc' is distinct from intent_doc->>'recorded_utc'
          or generation->'generations'->0->'source_snapshots' is distinct from intent_doc->'source_snapshots'
          or (select pg_catalog.max(selflearn_private.job_clock_v1(pg_catalog.regexp_replace(
                r.value->>'kickoff_utc',
                '^([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2})(Z|[+-][0-9]{2}:[0-9]{2})$',
                '\1:00\2')))
              from pg_catalog.jsonb_array_elements(generation->'receipts') r) is distinct from declared_cutoff then
        raise sqlstate 'PT409' using message='collection_publication_intake_incomplete';
      end if;
      declaration := record_doc->'declaration';
      authority := declaration->>'authority_id';
      definition := selflearn_private.job_definition_v1(selflearn_private.canonical_json_v1((record_doc->'definition')::json),authority);
      expected_input := selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',1,
        'kind','nfl.publication_job_input.v1','object_id',intent_doc->>'generation_object_id',
        'generation_id',intent_doc->>'generation_id','policy_id',selflearn_private.digest_json_v1((declaration->'policy')::json)));
      if not selflearn_private.job_keys_v1(declaration,array['version','authority_id','code_id','policy','settings'])
          or (declaration->'version')::text is distinct from '2'
          or definition->'namespace' is distinct from '{"domain":"nfl","task":"publication.recording","producer_id":"nfl2026"}'::jsonb
          or definition->>'operation' is distinct from 'nfl.publication.record'
          or definition->>'code_id' is distinct from declaration->>'code_id'
          or definition->>'input_object_id' is distinct from expected_input
          or definition->>'policy_object_id' is distinct from selflearn_private.digest_json_v1((declaration->'settings')::json)
          or definition->>'not_before_utc' is distinct from intent_doc->>'recorded_utc'
          or selflearn_private.job_clock_v1(definition->>'not_after_utc') is distinct from declared_cutoff+interval '1 hour' then
        raise sqlstate 'PT422' using message='invalid_collection_publication_declaration';
      end if;
      insert into selflearn_private.nfl_collection_publication_v1(intent_id,document_json)
        values(p_intent_id,p_document_json) returning * into saved;
      if saved.stored_utc>=declared_cutoff then raise sqlstate 'PT410' using message='collection_publication_cutoff'; end if;
    end if;
  end if;
  if p_operation in ('read','prepare') then
    return case when saved.intent_id is null then 'null'::json else
      pg_catalog.json_build_object('document_json',saved.document_json,'stored_utc',saved.stored_utc) end;
  end if;
  if saved.intent_id is null then
    if p_operation='find' then return 'null'::json; end if;
    raise sqlstate 'PT409' using message='collection_publication_definition_missing';
  end if;
  record_doc := saved.document_json::jsonb;
  authority := record_doc->'declaration'->>'authority_id';
  job_identity := record_doc->'definition'->>'job_id';
  if p_operation='find' then
    return public.selflearn_jobs_find_v1(authority,job_identity);
  end if;
  -- Match the existing generic register lock exactly. Under that reentrant
  -- lock, an already registered job remains readable at any clock; a new job
  -- must have its actual DB-stamped registered_utc strictly before kickoff.
  perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(authority||job_identity));
  select j.definition_json into old_definition from selflearn_private.learning_jobs_v1 j
    where j.authority_id=authority and j.job_id=job_identity;
  if old_definition is null and pg_catalog.clock_timestamp()>=declared_cutoff then
    raise sqlstate 'PT410' using message='collection_publication_cutoff';
  end if;
  result := public.selflearn_jobs_register_v1(authority,selflearn_private.canonical_json_v1((record_doc->'definition')::json));
  if old_definition is null and selflearn_private.job_clock_v1(result->>'registered_utc')>=declared_cutoff then
    raise sqlstate 'PT410' using message='collection_publication_cutoff';
  end if;
  return result;
end $$;

commit;
