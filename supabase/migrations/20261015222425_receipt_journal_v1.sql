-- Receipt journal v1: additive, backend-only, NOT applied to a hosted database.
-- Reconcile production migration history before an owner-authorized release.
-- All writers serialize on one transaction lock: bounded initial owner pilot,
-- not a high-throughput multi-tenant design. This also closes sequence/commit
-- gaps so a read watermark cannot later acquire an earlier pending insertion.
begin;

create schema selflearn_private;
revoke all on schema selflearn_private from public, anon, authenticated;
grant usage on schema selflearn_private to service_role;
create sequence selflearn_private.journal_sequence as bigint;
grant usage on sequence selflearn_private.journal_sequence to service_role;

create function selflearn_private.json_string_v1(value text) returns text
language plpgsql immutable strict security invoker set search_path = '' as $$
declare
  raw text := pg_catalog.to_json(value)::text;
  answer text := ''; ch text; n integer; i integer;
begin
  -- Python's ensure_ascii profile, including DEL and surrogate pairs.
  for i in 1..pg_catalog.char_length(raw) loop
    ch := pg_catalog.substr(raw, i, 1); n := pg_catalog.ascii(ch);
    if n >= 127 and n <= 65535 then
      answer := answer || E'\\u' || pg_catalog.lpad(pg_catalog.to_hex(n), 4, '0');
    elsif n > 65535 then
      n := n - 65536;
      answer := answer || E'\\u' || pg_catalog.to_hex(55296 + (n / 1024))
                       || E'\\u' || pg_catalog.to_hex(56320 + (n % 1024));
    else answer := answer || ch;
    end if;
  end loop;
  return answer;
end $$;

create function selflearn_private.json_number_v1(value text) returns text
language plpgsql immutable strict security invoker set search_path = '' set extra_float_digits = 3 as $$
declare n double precision; answer text;
begin
  if value !~ '[.eE]' then
    return case when value='-0' then '0' else value end;
  end if;
  n := value::double precision;
  if n::text in ('NaN','Infinity','-Infinity') then
    raise sqlstate 'PT422' using message='nonfinite_json_number';
  end if;
  answer := pg_catalog.to_json(n)::text;
  if pg_catalog.abs(n)>=0.0001 and pg_catalog.abs(n)<10000000000000000 then
    answer := answer::numeric::text;
  end if;
  if answer !~ '[.eE]' then answer := answer || '.0'; end if;
  return answer;
end $$;

create function selflearn_private.canonical_json_v1(value json, exclude_key text default null, level integer default 0)
returns text language plpgsql immutable security invoker set search_path = '' as $$
declare answer text; kind text := pg_catalog.json_typeof(value);
begin
  if level>24 then raise sqlstate 'PT422' using message='journal_json_depth_exceeded'; end if;
  if kind = 'object' then
    if exists(select 1 from pg_catalog.json_each(value) e group by e.key having pg_catalog.count(*)>1) then
      raise sqlstate 'PT422' using message='duplicate_journal_json_key';
    end if;
    select '{' || coalesce(pg_catalog.string_agg(
      selflearn_private.json_string_v1(e.key) || ':' || selflearn_private.canonical_json_v1(e.value,null,level+1),
      ',' order by e.key collate "C"), '') || '}' into answer
    from pg_catalog.json_each(value) e where exclude_key is null or e.key <> exclude_key;
  elsif kind = 'array' then
    select '[' || coalesce(pg_catalog.string_agg(selflearn_private.canonical_json_v1(e.value,null,level+1),
      ',' order by e.ordinality), '') || ']' into answer
    from pg_catalog.json_array_elements(value) with ordinality e(value, ordinality);
  elsif kind = 'string' then answer := selflearn_private.json_string_v1(value #>> '{}');
  elsif kind = 'number' then answer := selflearn_private.json_number_v1(pg_catalog.btrim(value::text));
  else answer := pg_catalog.btrim(value::text);
  end if;
  -- Preserve integer versus float types. PG's shortest IEEE float spelling is
  -- normalized to the Python profile; jsonb equality is NOT hash equality.
  return answer;
end $$;

create function selflearn_private.digest_json_v1(value json, exclude_key text default null)
returns text language sql immutable security invoker set search_path = '' as $$
  select 'sha256:' || pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    selflearn_private.canonical_json_v1(value, exclude_key), 'UTF8')), 'hex');
$$;

create function selflearn_private.require_canonical_v1(value text, maximum integer)
returns json language plpgsql immutable security invoker set search_path = '' as $$
declare parsed json;
begin
  if value is null or pg_catalog.octet_length(value) > maximum then
    raise sqlstate 'PT422' using message = 'invalid_journal_json';
  end if;
  parsed := value::json;
  if value is distinct from selflearn_private.canonical_json_v1(parsed) then
    raise sqlstate 'PT422' using message = 'noncanonical_journal_json';
  end if;
  return parsed;
exception when data_exception then
  raise sqlstate 'PT422' using message = 'invalid_journal_json';
end $$;

create function selflearn_private.validate_receipt_v1(value text) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  parsed json := selflearn_private.require_canonical_v1(value, 262144);
  doc jsonb := parsed::jsonb; k text; created timestamptz; seen timestamptz; start_at timestamptz;
begin
  if pg_catalog.jsonb_typeof(doc) is distinct from 'object'
     or pg_catalog.jsonb_typeof(doc->'version') is distinct from 'number'
     or doc->>'version' is distinct from '1' then
    raise sqlstate 'PT422' using message = 'invalid_receipt_version';
  end if;
  foreach k in array array['domain','task','event_id','correlation_group','base_model_version','calibrator_version'] loop
    if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string' or pg_catalog.btrim(doc->>k) = '' then
      raise sqlstate 'PT422' using message = 'missing_receipt_identity';
    end if;
  end loop;
  foreach k in array array['receipt_id','contract_id','source_receipt_id','source_snapshot_id'] loop
    if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string'
       or not (doc->>k ~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message = 'invalid_receipt_digest';
    end if;
  end loop;
  foreach k in array array['base_prob','served_prob'] loop
    if pg_catalog.jsonb_typeof(doc->k) is distinct from 'number'
       or (doc->>k)::numeric < 0 or (doc->>k)::numeric > 1 then
      raise sqlstate 'PT422' using message = 'invalid_receipt_probability';
    end if;
  end loop;
  if doc->>'publication_status' not in ('generated_unverified','verified_publication')
     or doc->>'publication_status' is null
     or pg_catalog.jsonb_typeof(doc->'features') is distinct from 'object' or doc->'features' = '{}'::jsonb
     or pg_catalog.jsonb_typeof(doc->'source_timestamps') is distinct from 'object'
     or doc->'source_timestamps' = '{}'::jsonb then
    raise sqlstate 'PT422' using message = 'invalid_receipt_evidence';
  end if;
  -- Require explicit timezone rather than session-local interpretation.
  foreach k in array array['forecast_created_utc','first_seen_utc','event_start_utc'] loop
    if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string'
       or not (doc->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
      raise sqlstate 'PT422' using message = 'timezone_required';
    end if;
  end loop;
  created := (doc->>'forecast_created_utc')::timestamptz;
  seen := (doc->>'first_seen_utc')::timestamptz;
  start_at := (doc->>'event_start_utc')::timestamptz;
  if not (created <= seen and seen < start_at) or seen > pg_catalog.clock_timestamp() then
    raise sqlstate 'PT422' using message = 'invalid_preevent_receipt';
  end if;
  if exists(select 1 from pg_catalog.jsonb_each(doc->'source_timestamps') e
            where e.key = '' or pg_catalog.jsonb_typeof(e.value) <> 'string'
               or not (e.value #>> '{}' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
               or (e.value #>> '{}')::timestamptz > created) then
    raise sqlstate 'PT422' using message = 'invalid_source_timestamp';
  end if;
  if doc->>'receipt_id' is distinct from selflearn_private.digest_json_v1(parsed, 'receipt_id') then
    raise sqlstate 'PT422' using message = 'receipt_hash_mismatch';
  end if;
  return doc;
exception when data_exception then
  raise sqlstate 'PT422' using message = 'invalid_receipt_evidence';
end $$;

create table selflearn_private.journal_receipts (
  receipt_id text primary key,
  domain text not null,
  task text not null,
  source_receipt_id text not null,
  payload_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null,
  unique(domain, task, source_receipt_id)
);
create table selflearn_private.journal_objects (
  object_id text primary key check(object_id ~ '^sha256:[0-9a-f]{64}$'),
  kind text not null check(kind in ('source_snapshot','generation')),
  payload_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null
);
create table selflearn_private.journal_generation_commits (
  object_id text primary key references selflearn_private.journal_objects(object_id),
  receipts_json text not null,
  rejections_json text not null,
  ack_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null
);
create table selflearn_private.journal_revisions (
  revision_id text primary key check(pg_catalog.length(revision_id) between 1 and 200),
  receipt_id text not null references selflearn_private.journal_receipts(receipt_id),
  supersedes text unique references selflearn_private.journal_revisions(revision_id),
  payload_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null
);
create unique index journal_one_initial_revision on selflearn_private.journal_revisions(receipt_id)
  where supersedes is null;
create index journal_revision_receipt on selflearn_private.journal_revisions(receipt_id, journal_seq);

create function selflearn_private.stamp_insert_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare doc jsonb;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044, 1);
  new.journal_seq := pg_catalog.nextval('selflearn_private.journal_sequence'::regclass);
  new.stored_utc := pg_catalog.clock_timestamp();
  if tg_table_name = 'journal_receipts' then
    doc := selflearn_private.validate_receipt_v1(new.payload_json);
    if (new.receipt_id, new.domain, new.task, new.source_receipt_id) is distinct from
       (doc->>'receipt_id', doc->>'domain', doc->>'task', doc->>'source_receipt_id') then
      raise sqlstate 'PT422' using message = 'receipt_columns_mismatch';
    end if;
  end if;
  return new;
end $$;
create function selflearn_private.refuse_mutation_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin raise sqlstate 'PT409' using message = 'immutable_journal_evidence'; end $$;

do $$ declare name text; begin
  foreach name in array array['journal_receipts','journal_objects','journal_generation_commits','journal_revisions'] loop
    execute pg_catalog.format('alter table selflearn_private.%I enable row level security', name);
    execute pg_catalog.format('alter table selflearn_private.%I force row level security', name);
    execute pg_catalog.format('revoke all on selflearn_private.%I from public, anon, authenticated, service_role', name);
    execute pg_catalog.format('grant select on selflearn_private.%I to service_role', name);
    execute pg_catalog.format('create trigger journal_stamp before insert on selflearn_private.%I for each row execute function selflearn_private.stamp_insert_v1()', name);
    execute pg_catalog.format('create trigger journal_immutable before update or delete on selflearn_private.%I for each row execute function selflearn_private.refuse_mutation_v1()', name);
    execute pg_catalog.format('create trigger journal_no_truncate before truncate on selflearn_private.%I for each statement execute function selflearn_private.refuse_mutation_v1()', name);
  end loop;
end $$;
grant insert(receipt_id,domain,task,source_receipt_id,payload_json) on selflearn_private.journal_receipts to service_role;
grant insert(object_id,kind,payload_json) on selflearn_private.journal_objects to service_role;
grant insert(object_id,receipts_json,rejections_json,ack_json) on selflearn_private.journal_generation_commits to service_role;
grant insert(revision_id,receipt_id,supersedes,payload_json) on selflearn_private.journal_revisions to service_role;

create function public.selflearn_journal_append_receipt_v1(p_receipt_json text) returns json
language plpgsql security invoker set search_path = '' as $$
declare doc jsonb; old text;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044, 1);
  doc := selflearn_private.validate_receipt_v1(p_receipt_json);
  select payload_json into old from selflearn_private.journal_receipts
    where receipt_id = doc->>'receipt_id' or
      (domain=doc->>'domain' and task=doc->>'task' and source_receipt_id=doc->>'source_receipt_id');
  if found then
    if old is distinct from p_receipt_json then
      raise sqlstate 'PT409' using message = 'receipt_or_source_id_conflict';
    end if;
    return pg_catalog.json_build_object('version',1,'added',false);
  end if;
  insert into selflearn_private.journal_receipts(receipt_id,domain,task,source_receipt_id,payload_json)
    values(doc->>'receipt_id',doc->>'domain',doc->>'task',doc->>'source_receipt_id',p_receipt_json);
  return pg_catalog.json_build_object('version',1,'added',true);
end $$;

create function public.selflearn_journal_append_object_v1(p_kind text,p_object_id text,p_payload_json text,p_hash_json text)
returns json language plpgsql security invoker set search_path = '' as $$
declare doc json; expected text; old selflearn_private.journal_objects%rowtype;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044, 1);
  doc := selflearn_private.require_canonical_v1(p_payload_json,3145728);
  if p_kind = 'generation' then
    expected := selflearn_private.canonical_json_v1(doc,'envelope_id');
    if doc->>'envelope_id' is distinct from p_object_id then
      raise sqlstate 'PT422' using message='generation_object_id_mismatch';
    end if;
  elsif p_kind = 'source_snapshot' then expected := p_payload_json;
  else raise sqlstate 'PT422' using message='unsupported_object_kind'; end if;
  -- The database derives the unsigned bytes itself. Optional client attestation
  -- must agree, but production does not resend a second multi-MiB source body.
  if (p_hash_json is not null and p_hash_json is distinct from expected) or p_object_id is distinct from
     'sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(expected,'UTF8')),'hex') then
    raise sqlstate 'PT422' using message='object_hash_mismatch';
  end if;
  select * into old from selflearn_private.journal_objects where object_id=p_object_id;
  if found then
    if old.kind is distinct from p_kind or old.payload_json is distinct from p_payload_json then
      raise sqlstate 'PT409' using message='object_id_conflict';
    end if;
    return pg_catalog.json_build_object('version',1,'added',false);
  end if;
  insert into selflearn_private.journal_objects(object_id,kind,payload_json) values(p_object_id,p_kind,p_payload_json);
  return pg_catalog.json_build_object('version',1,'added',true);
end $$;

create function public.selflearn_journal_commit_generation_v1(p_object_id text,p_receipts_json text,p_rejections_json text)
returns json language plpgsql security invoker set search_path = '' as $$
declare
  obj json; receipts json; rejections json; row json; source_id text; result json;
  expected text[]; actual text[] := array[]::text[]; ack_rows json[] := array[]::json[];
  old selflearn_private.journal_generation_commits%rowtype; incoming_pairs jsonb; prior_pairs jsonb;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044, 1);
  receipts := selflearn_private.require_canonical_v1(p_receipts_json,12582912);
  rejections := selflearn_private.require_canonical_v1(p_rejections_json,3145728);
  if pg_catalog.json_typeof(receipts) is distinct from 'array' or pg_catalog.json_typeof(rejections) is distinct from 'array' then
    raise sqlstate 'PT422' using message='invalid_generation_result';
  end if;
  select payload_json::json into obj from selflearn_private.journal_objects where object_id=p_object_id and kind='generation';
  if not found or pg_catalog.json_typeof(obj->'receipts') is distinct from 'array' then
    raise sqlstate 'PT422' using message='generation_object_missing';
  end if;
  select coalesce(pg_catalog.array_agg(e.value->>'receipt_id'),array[]::text[]) into expected
    from pg_catalog.json_array_elements(obj->'receipts') e;
  if exists(select 1 from pg_catalog.json_array_elements(obj->'receipts') e
    where pg_catalog.json_typeof(e.value) is distinct from 'object'
       or pg_catalog.json_typeof(e.value->'receipt_id') is distinct from 'string'
       or not (e.value->>'receipt_id' ~ '^sha256:[0-9a-f]{64}$')) then
    raise sqlstate 'PT422' using message='invalid_generation_source_identity';
  end if;
  for row in select value from pg_catalog.json_array_elements(receipts) loop
    perform selflearn_private.validate_receipt_v1(row::text);
    actual := pg_catalog.array_append(actual,row->>'source_receipt_id');
  end loop;
  for row in select value from pg_catalog.json_array_elements(rejections) loop
    if pg_catalog.json_typeof(row) is distinct from 'object'
       or pg_catalog.json_typeof(row->'source_receipt_id') is distinct from 'string'
       or pg_catalog.json_typeof(row->'reason') is distinct from 'string'
       or pg_catalog.btrim(row->>'reason')='' then
      raise sqlstate 'PT422' using message='invalid_generation_rejection';
    end if;
    actual := pg_catalog.array_append(actual,row->>'source_receipt_id');
  end loop;
  if exists(select 1 from pg_catalog.unnest(expected) x where x is null)
     or exists(select 1 from pg_catalog.unnest(actual) x where x is null)
     or pg_catalog.cardinality(expected) <> (select pg_catalog.count(distinct x) from pg_catalog.unnest(expected) x)
     or pg_catalog.cardinality(actual) <> (select pg_catalog.count(distinct x) from pg_catalog.unnest(actual) x)
     or not (expected @> actual and actual @> expected) then
    raise sqlstate 'PT422' using message='generation_result_partition_mismatch';
  end if;
  select * into old from selflearn_private.journal_generation_commits where object_id=p_object_id;
  if found then
    select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(e.value->>'source_receipt_id',e.value->>'receipt_id') order by e.value->>'source_receipt_id')
      into incoming_pairs from pg_catalog.json_array_elements(receipts) e;
    select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_array(e.value->>'source_receipt_id',e.value->>'receipt_id') order by e.value->>'source_receipt_id')
      into prior_pairs from pg_catalog.json_array_elements(old.ack_json::json->'receipts') e;
    if incoming_pairs is distinct from prior_pairs or old.rejections_json is distinct from p_rejections_json then
      raise sqlstate 'PT409' using message='generation_commit_conflict';
    end if;
    select coalesce(pg_catalog.array_agg(pg_catalog.json_build_object('source_receipt_id',e.value->>'source_receipt_id',
      'receipt_id',e.value->>'receipt_id','status','duplicate')),array[]::json[]) into ack_rows
      from pg_catalog.json_array_elements(old.ack_json::json->'receipts') e;
  else
    for row in select value from pg_catalog.json_array_elements(receipts) loop
      result := public.selflearn_journal_append_receipt_v1(row::text);
      ack_rows := pg_catalog.array_append(ack_rows,pg_catalog.json_build_object('source_receipt_id',row->>'source_receipt_id',
        'receipt_id',row->>'receipt_id','status',case when (result->>'added')::boolean then 'recorded' else 'duplicate' end));
    end loop;
  end if;
  result := pg_catalog.json_build_object('version',1,'object_id',p_object_id,'durable',true,
    'receipts',pg_catalog.to_json(ack_rows),'rejections',rejections);
  if old.object_id is null then
    insert into selflearn_private.journal_generation_commits(object_id,receipts_json,rejections_json,ack_json)
      values(p_object_id,p_receipts_json,p_rejections_json,result::text);
  end if;
  return result;
end $$;

create function public.selflearn_journal_append_resolution_v1(p_revision_json text) returns json
language plpgsql security invoker set search_path = '' as $$
declare
  parsed json := selflearn_private.require_canonical_v1(p_revision_json,262144);
  doc jsonb := parsed::jsonb; value jsonb := doc->'resolution'; receipt jsonb; previous jsonb;
  old text; head text; k text; resolved timestamptz; observed timestamptz;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044, 1);
  if pg_catalog.jsonb_typeof(doc) is distinct from 'object' or doc->>'version' is distinct from '1'
     or pg_catalog.jsonb_typeof(doc->'version') is distinct from 'number'
     or pg_catalog.jsonb_typeof(doc->'revision_id') is distinct from 'string'
     or pg_catalog.length(doc->>'revision_id') not between 1 and 200
     or pg_catalog.btrim(doc->>'revision_id') = '' or pg_catalog.jsonb_typeof(value) is distinct from 'object'
     or not (doc ? 'supersedes') or not (doc ? 'reason') then
    raise sqlstate 'PT422' using message='invalid_revision_identity';
  end if;
  if (select pg_catalog.array_agg(keys.key_name order by keys.key_name)
        from pg_catalog.jsonb_object_keys(doc) keys(key_name))
       is distinct from array['reason','resolution','revision_id','supersedes','version']::text[] then
    raise sqlstate 'PT422' using message='invalid_revision_fields';
  end if;
  if doc->'supersedes' <> 'null'::jsonb and (pg_catalog.jsonb_typeof(doc->'supersedes') <> 'string'
       or pg_catalog.length(doc->>'supersedes') not between 1 and 200 or pg_catalog.btrim(doc->>'supersedes')='') then
    raise sqlstate 'PT422' using message='invalid_revision_predecessor';
  end if;
  if (doc->'reason' <> 'null'::jsonb or doc->'supersedes' <> 'null'::jsonb) and
     (pg_catalog.jsonb_typeof(doc->'reason') <> 'string' or coalesce(pg_catalog.btrim(doc->>'reason'),'') = '') then
    raise sqlstate 'PT422' using message='correction_reason_required';
  end if;
  select payload_json::jsonb into receipt from selflearn_private.journal_receipts where receipt_id=value->>'receipt_id';
  if not found then raise sqlstate 'PT422' using message='unknown_receipt'; end if;
  foreach k in array array['receipt_id','contract_id','event_id'] loop
    if pg_catalog.jsonb_typeof(value->k) is distinct from 'string' or value->>k is distinct from receipt->>k then
      raise sqlstate 'PT422' using message='outcome_identity_mismatch';
    end if;
  end loop;
  if value->>'status' = 'FINAL' then
    if pg_catalog.jsonb_typeof(value->'hit') is distinct from 'boolean' then
      raise sqlstate 'PT422' using message='not_final_binary_outcome';
    end if;
  elsif value->>'status' = 'VOID' then
    if value->'hit' is distinct from 'null'::jsonb or value->'authoritative' is distinct from 'true'::jsonb
       or value->>'final_evidence_status' is distinct from 'FINAL'
       or pg_catalog.jsonb_typeof(value->'reason') is distinct from 'string' or pg_catalog.btrim(value->>'reason')='' then
      raise sqlstate 'PT422' using message='void_requires_authoritative_evidence';
    end if;
  else raise sqlstate 'PT422' using message='unresolved_outcome'; end if;
  if pg_catalog.jsonb_typeof(value->'source') is distinct from 'string' or pg_catalog.btrim(value->>'source')=''
     or pg_catalog.jsonb_typeof(value->'resolved_utc') is distinct from 'string'
     or not (value->>'resolved_utc' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
    raise sqlstate 'PT422' using message='missing_outcome_source_or_time';
  end if;
  resolved := (value->>'resolved_utc')::timestamptz;
  if resolved < (receipt->>'event_start_utc')::timestamptz or resolved > pg_catalog.clock_timestamp() then
    raise sqlstate 'PT422' using message='invalid_outcome_time';
  end if;
  foreach k in array array['source_updated_utc','evidence_observed_utc'] loop
    if value ? k then
      if pg_catalog.jsonb_typeof(value->k) is distinct from 'string' or not (value->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
        raise sqlstate 'PT422' using message='invalid_evidence_time';
      end if;
      observed := (value->>k)::timestamptz;
      if (k='source_updated_utc' and observed>resolved) or (k='evidence_observed_utc' and observed<resolved)
         or observed>pg_catalog.clock_timestamp() then raise sqlstate 'PT422' using message='invalid_evidence_time'; end if;
    end if;
  end loop;
  if value ? 'source_revision_id' then
    if pg_catalog.jsonb_typeof(value->'source_revision_id') is distinct from 'string'
       or pg_catalog.btrim(value->>'source_revision_id')='' then
      raise sqlstate 'PT422' using message='invalid_official_source_revision';
    end if;
    if exists(select 1 from selflearn_private.journal_revisions r
       where r.receipt_id=value->>'receipt_id' and r.payload_json::jsonb->'resolution'->>'source'=value->>'source'
         and r.payload_json::jsonb->'resolution'->>'source_revision_id'=value->>'source_revision_id'
         and selflearn_private.canonical_json_v1(r.payload_json::json->'resolution','evidence_observed_utc')
             is distinct from selflearn_private.canonical_json_v1(parsed->'resolution','evidence_observed_utc')) then
      raise sqlstate 'PT409' using message='conflicting_official_source_revision';
    end if;
  end if;
  select payload_json into old from selflearn_private.journal_revisions where revision_id=doc->>'revision_id';
  if found then
    if old is distinct from p_revision_json then raise sqlstate 'PT409' using message='revision_id_conflict'; end if;
    return pg_catalog.json_build_object('version',1,'added',false);
  end if;
  select r.revision_id,r.payload_json::jsonb->'resolution' into head,previous
    from selflearn_private.journal_revisions r where r.receipt_id=value->>'receipt_id'
    order by r.journal_seq desc limit 1;
  if doc->>'supersedes' is distinct from head then raise sqlstate 'PT409' using message='correction_must_extend_current_head'; end if;
  if previous is not null then
    if resolved < (previous->>'resolved_utc')::timestamptz then
      raise sqlstate 'PT422' using message='correction_time_regressed';
    end if;
    foreach k in array array['source_updated_utc','evidence_observed_utc'] loop
      if previous ? k and (not (value ? k) or (value->>k)::timestamptz < (previous->>k)::timestamptz) then
        raise sqlstate 'PT422' using message='correction_evidence_time_regressed';
      end if;
    end loop;
  end if;
  insert into selflearn_private.journal_revisions(revision_id,receipt_id,supersedes,payload_json)
    values(doc->>'revision_id',value->>'receipt_id',doc->>'supersedes',p_revision_json);
  return pg_catalog.json_build_object('version',1,'added',true);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_outcome_evidence';
end $$;

create function public.selflearn_journal_read_v1(p_view text,p_key text,p_after text,p_limit integer,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare
  ceiling bigint; committed_max bigint; after_seq bigint := 0; rows json; more boolean; next_id text;
begin
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
  if p_after is not null then
    if p_after !~ '^(0|[1-9][0-9]{0,18})$' then raise sqlstate 'PT422' using message='invalid_page_cursor'; end if;
    after_seq := p_after::bigint;
  end if;
  select coalesce(pg_catalog.max(v),0) into committed_max from (
      select pg_catalog.max(journal_seq) as v from selflearn_private.journal_receipts union all
      select pg_catalog.max(journal_seq) from selflearn_private.journal_objects union all
      select pg_catalog.max(journal_seq) from selflearn_private.journal_generation_commits union all
      select pg_catalog.max(journal_seq) from selflearn_private.journal_revisions) s;
  if p_watermark is null then
    ceiling := committed_max;
  else
    if p_watermark !~ '^(0|[1-9][0-9]{0,18})$' then raise sqlstate 'PT422' using message='invalid_read_watermark'; end if;
    ceiling := p_watermark::bigint;
    if ceiling>committed_max then raise sqlstate 'PT422' using message='future_read_watermark'; end if;
  end if;
  if after_seq > ceiling then raise sqlstate 'PT422' using message='cursor_after_watermark'; end if;
  if p_view='status' then
    return pg_catalog.json_build_object('version',1,'rows',pg_catalog.json_build_array(
      pg_catalog.json_build_object('schema_version',1,'backend','postgres','journal_seq',ceiling::text)),
      'next_after',null,'watermark',ceiling::text);
  end if;
  if p_view not in ('object','receipt','receipts','generation_ack','generations','resolution_history','revisions','latest_resolutions')
     or p_view is null then raise sqlstate 'PT422' using message='unsupported_journal_view'; end if;
  if p_view in ('object','receipt','generation_ack','resolution_history') and (p_key is null or p_key='') then
    raise sqlstate 'PT422' using message='journal_key_required';
  end if;
  if p_view='generations' and p_limit<>1 then raise sqlstate 'PT422' using message='generation_page_limit_is_one'; end if;
  with candidates as (
    select o.journal_seq,pg_catalog.json_build_object('object_id',o.object_id,'kind',o.kind,'payload_json',o.payload_json,
      'journal_seq',o.journal_seq::text,'received_utc',pg_catalog.to_char(o.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) item
      from selflearn_private.journal_objects o where p_view='object' and o.object_id=p_key
    union all
    select r.journal_seq,pg_catalog.json_build_object('receipt_id',r.receipt_id,'payload_json',r.payload_json,
      'journal_seq',r.journal_seq::text,'received_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selflearn_private.journal_receipts r where p_view='receipts' or (p_view='receipt' and r.receipt_id=p_key)
    union all
    select c.journal_seq,pg_catalog.json_build_object('object_id',c.object_id,'ack_json',c.ack_json,'journal_seq',c.journal_seq::text)
      from selflearn_private.journal_generation_commits c where p_view='generation_ack' and c.object_id=p_key
    union all
    select c.journal_seq,pg_catalog.json_build_object('object_id',c.object_id,'payload_json',o.payload_json,'ack_json',c.ack_json,
      'journal_seq',c.journal_seq::text,'recorded_utc',pg_catalog.to_char(c.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
      where p_view='generations'
    union all
    select r.journal_seq,pg_catalog.json_build_object('revision_id',r.revision_id,'payload_json',r.payload_json,
      'journal_seq',r.journal_seq::text,'recorded_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selflearn_private.journal_revisions r where p_view='revisions' or (p_view='resolution_history' and r.receipt_id=p_key)
        or (p_view='latest_resolutions' and not exists(select 1 from selflearn_private.journal_revisions n
             where n.supersedes=r.revision_id and n.journal_seq<=ceiling))
  ), bounded as (
    select * from candidates where journal_seq>after_seq and journal_seq<=ceiling order by journal_seq limit p_limit+1
  ), measured as (
    select *, pg_catalog.row_number() over(order by journal_seq) as row_number,
      pg_catalog.sum(pg_catalog.octet_length(item::text)+1) over(order by journal_seq) as wire_bytes
      from bounded
  ), page as (
    -- A normal 500-row page stays fast; unusually large receipts produce a
    -- shorter page, not truncation or a response above the HTTP client's cap.
    select * from measured where wire_bytes<=6291456 or row_number=1 order by journal_seq limit p_limit
  )
  select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
    (select pg_catalog.count(*)>(select pg_catalog.count(*) from page) from bounded),
    (select pg_catalog.max(journal_seq)::text from page)
    into rows,more,next_id;
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,'watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_journal_page';
end $$;

-- Explicit scope only; never revoke grants on unrelated existing app objects.
revoke all on all functions in schema selflearn_private from public, anon, authenticated;
grant execute on all functions in schema selflearn_private to service_role;
revoke execute on function public.selflearn_journal_append_receipt_v1(text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_append_object_v1(text,text,text,text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_commit_generation_v1(text,text,text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_append_resolution_v1(text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_read_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_append_receipt_v1(text) to service_role;
grant execute on function public.selflearn_journal_append_object_v1(text,text,text,text) to service_role;
grant execute on function public.selflearn_journal_commit_generation_v1(text,text,text) to service_role;
grant execute on function public.selflearn_journal_append_resolution_v1(text) to service_role;
grant execute on function public.selflearn_journal_read_v1(text,text,text,integer,text) to service_role;
commit;
