-- Private, immutable producer-attempt metadata. Forecast bytes stay in journal_objects.
create table selflearn_private.nfl_collection_handoff_v1 (
  repository text not null,
  workflow text not null check (workflow in ('daily.yml', 'gameday.yml')),
  run_id text not null check (run_id ~ '^[1-9][0-9]{0,18}$'),
  run_attempt text not null check (run_attempt ~ '^[1-9][0-9]{0,18}$'),
  stage text not null check (stage in ('intent', 'publisher')),
  record_id text collate "C" not null unique check (record_id ~ '^sha256:[0-9a-f]{64}$'),
  document_json text not null check (pg_catalog.octet_length(document_json) <= 8192),
  recorded_utc timestamptz not null default pg_catalog.clock_timestamp(),
  primary key (repository, workflow, run_id, run_attempt, stage)
);

alter table selflearn_private.nfl_collection_handoff_v1 enable row level security;
revoke all on selflearn_private.nfl_collection_handoff_v1 from public, anon, authenticated, service_role;
grant select on selflearn_private.nfl_collection_handoff_v1 to service_role;
grant insert (repository,workflow,run_id,run_attempt,stage,record_id,document_json)
  on selflearn_private.nfl_collection_handoff_v1 to service_role;
create index nfl_collection_handoff_scan_v1 on selflearn_private.nfl_collection_handoff_v1(repository, record_id)
  where stage = 'intent';

create function public.selflearn_nfl_collection_handoff_v1(
  p_operation text, p_repository text, p_workflow text, p_run_id text, p_run_attempt text,
  p_document_json text, p_limit integer, p_after text
) returns json language plpgsql security invoker set search_path = '' as $$
declare
  observed timestamptz;
  original selflearn_private.nfl_collection_handoff_v1%rowtype;
  receipt selflearn_private.nfl_collection_handoff_v1%rowtype;
  old_json text;
  doc json;
  intent_doc json;
  identity text;
  id_field text;
  stage_name text;
  key_count integer;
  pool text;
  previous text;
  expected_disposition text;
  missing json;
  pending json;
  cursor_id text;
  clock_pattern constant text := '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$';
begin
  if p_operation is null or p_operation not in ('read','put_intent','put_publisher','pending')
      or p_repository is null or p_repository !~ '^[a-z0-9_.-]{1,100}/[a-z0-9_.-]{1,100}$' then
    raise sqlstate 'PT422' using message='invalid_collection_scope';
  end if;

  if p_operation = 'pending' then
    if p_workflow is not null or p_run_id is not null or p_run_attempt is not null or p_document_json is not null
        or p_limit is null or p_limit not between 1 and 50
        or (p_after is not null and p_after !~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message='invalid_collection_scan';
    end if;
    observed := pg_catalog.clock_timestamp();
    -- Read at most limit+1 indexed candidates before eligibility checks. Advance
    -- over every scanned candidate, including held or invalid original attempts.
    with scan as materialized (
      select record_id, workflow, run_id, run_attempt, document_json::json as document
      from selflearn_private.nfl_collection_handoff_v1
      where repository = p_repository and stage = 'intent' and record_id > coalesce(p_after, '')
      order by record_id limit p_limit + 1
    ), page as materialized (select * from scan order by record_id limit p_limit)
    select coalesce(pg_catalog.json_agg(pg_catalog.json_build_object('workflow', page.workflow,
             'run_id', page.run_id, 'run_attempt', page.run_attempt, 'intent_id', page.record_id)
             order by page.record_id) filter (where
                (page.document->>'latest_kickoff_utc')::timestamptz > observed
                and exists (select 1 from selflearn_private.journal_objects g
                    where g.object_id = page.document->>'generation_object_id' and g.kind = 'generation')
                and not exists (select 1 from pg_catalog.json_each_text(page.document->'source_snapshots') s
                    where not exists (select 1 from selflearn_private.journal_objects o
                        where o.object_id = s.value and o.kind = 'source_snapshot'))
             ), '[]'::json),
           case when (select pg_catalog.count(*) from scan) > p_limit then pg_catalog.max(page.record_id) end
      into pending, cursor_id from page;
    return pg_catalog.json_build_object('version', 1, 'observed_utc', observed,
                                        'pending', pending, 'next_after', cursor_id);
  end if;

  if p_workflow is null or p_workflow not in ('daily.yml', 'gameday.yml')
      or p_run_id is null or p_run_id !~ '^[1-9][0-9]{0,18}$'
      or p_run_attempt is null or p_run_attempt !~ '^[1-9][0-9]{0,18}$'
      or p_limit is not null or p_after is not null
      or ((p_operation = 'read') is distinct from (p_document_json is null)) then
    raise sqlstate 'PT422' using message='invalid_collection_scope';
  end if;

  if p_operation <> 'read' then
    perform pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      p_repository || ':' || p_workflow || ':' || p_run_id || ':' || p_run_attempt, 821911));
    doc := selflearn_private.require_canonical_v1(p_document_json, 8192);
    if pg_catalog.json_typeof(doc) is distinct from 'object' or (doc->'version')::text is distinct from '1' then
      raise sqlstate 'PT422' using message='invalid_collection_document';
    end if;
    select pg_catalog.count(*) into key_count from pg_catalog.json_object_keys(doc);
    if p_operation = 'put_intent' then
      stage_name := 'intent'; id_field := 'intent_id';
      if key_count <> 12 or doc->>'kind' is distinct from 'nfl.collection_intent.v1'
          or doc->>'repository' is distinct from p_repository or doc->>'workflow' is distinct from p_workflow
          or pg_catalog.json_typeof(doc->'run_id') is distinct from 'string' or doc->>'run_id' is distinct from p_run_id
          or pg_catalog.json_typeof(doc->'run_attempt') is distinct from 'string' or doc->>'run_attempt' is distinct from p_run_attempt
          or coalesce(doc->>'generation_object_id', '') !~ '^sha256:[0-9a-f]{64}$'
          or coalesce(doc->>'generation_id', '') !~ '^sha256:[0-9a-f]{64}$'
          or pg_catalog.json_typeof(doc->'source_snapshots') is distinct from 'object'
          or not (doc->'source_snapshots')::jsonb ?& array['leg_pool','schedule','parlay_calibration']
          or coalesce(doc->>'recorded_utc', '') !~ clock_pattern
          or coalesce(doc->>'latest_kickoff_utc', '') !~ clock_pattern then
        raise sqlstate 'PT422' using message='invalid_collection_intent';
      end if;
      if (doc->>'recorded_utc')::timestamptz >= (doc->>'latest_kickoff_utc')::timestamptz
          or (select pg_catalog.count(*) from pg_catalog.json_each(doc->'source_snapshots')) <> 3
          or exists (select 1 from pg_catalog.json_each_text(doc->'source_snapshots') s
              where coalesce(s.value, '') !~ '^sha256:[0-9a-f]{64}$' or s.value = doc->>'generation_object_id') then
        raise sqlstate 'PT422' using message='invalid_collection_intent';
      end if;
    else
      stage_name := 'publisher'; id_field := 'receipt_id';
      select h.document_json::json into intent_doc from selflearn_private.nfl_collection_handoff_v1 h
        where h.repository = p_repository and h.workflow = p_workflow and h.run_id = p_run_id
          and h.run_attempt = p_run_attempt and h.stage = 'intent';
      if intent_doc is null or (doc->>'intent_id') is distinct from (intent_doc->>'intent_id') then
        raise sqlstate 'PT409' using message='collection_publisher_intent_conflict';
      end if;
      if key_count <> 8 or doc->>'kind' is distinct from 'nfl.collection_publisher_receipt.v1'
          or not doc::jsonb ?& array['published_sha','published_pool_id','previous_pool_id'] then
        raise sqlstate 'PT422' using message='invalid_collection_publisher';
      end if;
      pool := doc->>'published_pool_id'; previous := doc->>'previous_pool_id';
      if pg_catalog.json_typeof(doc->'published_sha') = 'null' then
        if pool is not null or previous is not null then
          raise sqlstate 'PT422' using message='invalid_collection_noop';
        end if;
        expected_disposition := 'noop';
      else
        if pg_catalog.json_typeof(doc->'published_sha') is distinct from 'string'
            or coalesce(doc->>'published_sha', '') !~ '^[0-9a-f]{40}$'
            or coalesce(pool, '') !~ '^sha256:[0-9a-f]{64}$'
            or (previous is not null and previous !~ '^sha256:[0-9a-f]{64}$') then
          raise sqlstate 'PT422' using message='invalid_collection_publisher';
        end if;
        expected_disposition := case when pool is distinct from (intent_doc->'source_snapshots'->>'leg_pool') then 'mismatch'
                                     when pool = previous then 'unchanged' else 'match' end;
      end if;
      if doc->>'disposition' is distinct from expected_disposition then
        raise sqlstate 'PT422' using message='invalid_collection_publisher_disposition';
      end if;
    end if;
    identity := 'sha256:' || pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
      selflearn_private.canonical_json_v1(doc, id_field), 'UTF8')), 'hex');
    if doc->>id_field is distinct from identity then
      raise sqlstate 'PT422' using message='invalid_collection_record_identity';
    end if;
    select h.document_json into old_json from selflearn_private.nfl_collection_handoff_v1 h
      where h.repository = p_repository and h.workflow = p_workflow and h.run_id = p_run_id
        and h.run_attempt = p_run_attempt and h.stage = stage_name;
    if found then
      if old_json is distinct from p_document_json then
        raise sqlstate 'PT409' using message='immutable_collection_conflict';
      end if;
    else
      if stage_name = 'intent' and (doc->>'recorded_utc')::timestamptz > pg_catalog.clock_timestamp() then
        raise sqlstate 'PT422' using message='future_collection_clock';
      end if;
      insert into selflearn_private.nfl_collection_handoff_v1(repository,workflow,run_id,run_attempt,stage,record_id,document_json)
        values(p_repository,p_workflow,p_run_id,p_run_attempt,stage_name,identity,p_document_json);
    end if;
  end if;

  select i.* into original from selflearn_private.nfl_collection_handoff_v1 i
    where i.repository=p_repository and i.workflow=p_workflow and i.run_id=p_run_id and i.run_attempt=p_run_attempt and i.stage='intent';
  missing := '[]'::json;
  if original.record_id is not null then
    select p.* into receipt from selflearn_private.nfl_collection_handoff_v1 p
      where p.repository=p_repository and p.workflow=p_workflow and p.run_id=p_run_id and p.run_attempt=p_run_attempt
        and p.stage='publisher';
    intent_doc := original.document_json::json;
    select coalesce(pg_catalog.json_agg(refs.identity order by refs.identity), '[]'::json) into missing
    from (select distinct s.value as identity, 'source_snapshot'::text as kind
          from pg_catalog.json_each_text(intent_doc->'source_snapshots') s
          union all select intent_doc->>'generation_object_id', 'generation') refs
    where not exists (select 1 from selflearn_private.journal_objects o where o.object_id=refs.identity and o.kind=refs.kind);
  end if;
  return pg_catalog.json_build_object('version', 1, 'observed_utc', pg_catalog.clock_timestamp(),
      'intent', case when original.record_id is not null then pg_catalog.json_build_object(
          'recorded_utc', original.recorded_utc, 'document_json',original.document_json) end,
      'publisher_receipt', case when receipt.record_id is not null then pg_catalog.json_build_object(
          'recorded_utc', receipt.recorded_utc, 'document_json',receipt.document_json) end,
      'missing_object_ids', missing);
exception when invalid_datetime_format or datetime_field_overflow then
  raise sqlstate 'PT422' using message='invalid_collection_clock';
end $$;

revoke execute on function public.selflearn_nfl_collection_handoff_v1(text,text,text,text,text,text,integer,text)
  from public, anon, authenticated;
grant execute on function public.selflearn_nfl_collection_handoff_v1(text,text,text,text,text,text,integer,text) to service_role;
