-- Local-only publication evidence; not applied to any hosted database.
-- HTTP is observed by the configured server BEFORE this transaction. The
-- service-only RPC validates its immutable evidence and stamps the DB clock
-- after the same writer lock used by all receipt journal writers.
begin;

create table selflearn_private.journal_publication_attestations (
  object_id text primary key references selflearn_private.journal_generation_commits(object_id),
  observation_json text not null,
  ack_json text not null,
  attestation_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null
);
alter table selflearn_private.journal_publication_attestations enable row level security;
alter table selflearn_private.journal_publication_attestations force row level security;
revoke all on selflearn_private.journal_publication_attestations from public,anon,authenticated,service_role;
grant select on selflearn_private.journal_publication_attestations to service_role;
-- No direct insertion grant: the one narrowly scoped SECURITY DEFINER RPC is
-- the write capability. Other roles cannot execute it or access this schema.
create trigger publication_immutable before update or delete
  on selflearn_private.journal_publication_attestations
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger publication_no_truncate before truncate
  on selflearn_private.journal_publication_attestations
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function selflearn_private.publication_watermark_v1() returns bigint
language sql stable security invoker set search_path = '' as $$
  select greatest(selflearn_private.observed_watermark_v1(),
    coalesce((select max(journal_seq) from selflearn_private.journal_publication_attestations),0));
$$;

create function selflearn_private.commit_publication_v1(p_object_id text,p_observation_json text)
returns json language plpgsql security definer set search_path = '' as $$
declare
  old selflearn_private.journal_publication_attestations%rowtype;
  obs json; policy json; body json; doc json; gen json; ack json;
  pool json; schedule json; calibration json; source json; generic json; row json; pair json;
  observed_start timestamptz; observed_end timestamptz; recorded timestamptz;
  stamp text; origin text; host text; label text; raw_body text; source_pool text;
  source_ids text[]; ack_ids text[]; generic_ids text[]; mapped text[] := array[]::text[];
  accepted json[] := array[]::json[]; rejected json[] := array[]::json[];
  candidates json[]; candidate json; leg json; game json; manifest json;
  pool_index jsonb; schedule_index jsonb; generation_index jsonb;
  generation_id text; generation_recorded text; generation_generated text;
  period_week text; period_season text; pool_base text;
  source_id text; receipt_id text; k text; result json; status text;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  -- Return the ORIGINAL evidence/result on replay, even after kickoff and even
  -- if the caller's new observation is malformed. Never mint new identities.
  select * into old from selflearn_private.journal_publication_attestations where object_id=p_object_id;
  if found then return pg_catalog.json_build_object('version',1,'attestation',old.attestation_json::json,'replayed',true); end if;
  recorded := pg_catalog.clock_timestamp();
  stamp := pg_catalog.to_char(recorded at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
  -- The raw HTTP body is one potentially multi-MiB JSON STRING here. Running
  -- the legacy character-concatenating canonical string encoder over it is
  -- quadratic. Bound/parse this envelope and validate its exact key sets and
  -- scalar types below instead. The body itself still undergoes the strict
  -- recursive canonical digest (duplicate keys, depth, finite numbers), while
  -- its exact original UTF-8 text is retained without reserialization.
  -- A legal 4-MiB body can expand sixfold as an ASCII JSON string (DEL), plus
  -- bounded policy/clock framing. This bound is ONLY the observation wrapper.
  if p_observation_json is null or pg_catalog.octet_length(p_observation_json)>25231360 then
    raise sqlstate 'PT422' using message='invalid_publication_observation_size';
  end if;
  obs := p_observation_json::json;
  if (select array_agg(key order by key) from pg_catalog.json_object_keys(obs) key)
       is distinct from array['body_utf8','http_status','observed_completed_utc','observed_started_utc','policy','version']::text[]
     or pg_catalog.json_typeof(obs->'version') is distinct from 'number' or obs->>'version' is distinct from '1'
     or pg_catalog.json_typeof(obs->'http_status') is distinct from 'number' or obs->>'http_status' is distinct from '200'
     or pg_catalog.json_typeof(obs->'body_utf8') is distinct from 'string' then
    raise sqlstate 'PT422' using message='invalid_publication_http_observation';
  end if;
  policy := obs->'policy';
  if (select array_agg(key order by key) from pg_catalog.json_object_keys(policy) key)
       is distinct from array['kind','origin','path','version']::text[]
     or pg_catalog.json_typeof(policy->'version') is distinct from 'number' or policy->>'version' is distinct from '1'
     or policy->>'kind' is distinct from 'nfl.pool_http_publication.v1'
     or policy->>'path' is distinct from '/data/leg_pool.json'
     or pg_catalog.json_typeof(policy->'origin') is distinct from 'string' then
    raise sqlstate 'PT422' using message='invalid_publication_policy';
  end if;
  origin := policy->>'origin'; host := pg_catalog.substr(origin,9);
  if origin !~ '^https://[a-z0-9.-]+$' or pg_catalog.length(host)>253
     or pg_catalog.strpos(host,'.')=0 or host='localhost' or host like '%.localhost'
     or pg_catalog.split_part(host,'.',-1) !~ '^[a-z]{2,63}$'
     or host ~ '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$' then
    raise sqlstate 'PT422' using message='invalid_publication_origin';
  end if;
  foreach label in array pg_catalog.string_to_array(host,'.') loop
    if label !~ '^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$' then
      raise sqlstate 'PT422' using message='invalid_publication_origin';
    end if;
  end loop;
  foreach k in array array['observed_started_utc','observed_completed_utc'] loop
    if pg_catalog.json_typeof(obs->k) is distinct from 'string' or obs->>k !~ '(Z|[+-][0-9]{2}:[0-9]{2})$' then
      raise sqlstate 'PT422' using message='invalid_publication_clock';
    end if;
  end loop;
  observed_start := (obs->>'observed_started_utc')::timestamptz;
  observed_end := (obs->>'observed_completed_utc')::timestamptz;
  if not (observed_start<=observed_end and observed_end<=recorded) then
    raise sqlstate 'PT422' using message='invalid_publication_clock_order';
  end if;
  raw_body := obs->>'body_utf8';
  if pg_catalog.octet_length(raw_body) not between 1 and 4194304 then
    raise sqlstate 'PT422' using message='invalid_publication_body_size';
  end if;
  -- json (not jsonb) retains duplicates for the existing canonical validator
  -- to reject at ANY depth; it also rejects invalid JSON/nonfinite numbers.
  body := raw_body::json;
  source_pool := selflearn_private.digest_json_v1(body);
  select o.payload_json::json,c.ack_json::json into doc,ack
    from selflearn_private.journal_generation_commits c
    join selflearn_private.journal_objects o using(object_id)
    where c.object_id=p_object_id and o.kind='generation';
  if not found or pg_catalog.json_typeof(doc->'generations') is distinct from 'array'
     or pg_catalog.json_array_length(doc->'generations')<>1
     or pg_catalog.json_typeof(doc->'receipts') is distinct from 'array'
     or selflearn_private.digest_json_v1(doc,'envelope_id') is distinct from p_object_id then
    raise sqlstate 'PT422' using message='publication_generation_not_finalized';
  end if;
  gen := doc->'generations'->0;
  if pg_catalog.json_typeof(doc->'version') is distinct from 'number' or doc->>'version' is distinct from '1'
     or pg_catalog.json_typeof(gen->'version') is distinct from 'number' or gen->>'version' is distinct from '1'
     or pg_catalog.json_typeof(doc->'season') is distinct from 'number' or doc->>'season' !~ '^[0-9]+$'
     or pg_catalog.json_typeof(doc->'week') is distinct from 'number' or doc->>'week' !~ '^[0-9]+$'
     or (doc->>'week')::integer not between 1 and 18
     or pg_catalog.json_typeof(gen->'season') is distinct from 'number' or gen->>'season' !~ '^[0-9]+$'
     or pg_catalog.json_typeof(gen->'week') is distinct from 'number' or gen->>'week' !~ '^[0-9]+$'
     or (select array_agg(key order by key) from pg_catalog.json_object_keys(gen->'source_snapshots') key)
          is distinct from array['leg_pool','parlay_calibration','schedule']::text[]
     or pg_catalog.json_typeof(gen->'offered_cards') is distinct from 'array'
     or gen->>'selection_policy_version' !~ '^sha256:[0-9a-f]{64}$'
     or gen->>'joint_model_version' !~ '^sha256:[0-9a-f]{64}$' then
    raise sqlstate 'PT422' using message='invalid_publication_generation_manifest';
  end if;
  select pg_catalog.json_object_agg(key,value order by key) into manifest from pg_catalog.json_each(gen)
    where key=any(array['version','season','week','pool_generated_utc','source_snapshots','offered_cards',
                       'selection_policy_version','joint_model_version']);
  if (gen->>'recorded_utc')::timestamptz>observed_start
     or gen->>'generation_id' is distinct from selflearn_private.digest_json_v1(manifest)
     or gen->>'publication_status' is distinct from 'generated_unverified'
     or gen->>'population' is distinct from 'eligible_pool_prop_rungs_before_display_dedupe'
     or (gen->'season')::jsonb is distinct from (doc->'season')::jsonb
     or (gen->'week')::jsonb is distinct from (doc->'week')::jsonb
     or gen->'source_snapshots'->>'leg_pool' is distinct from source_pool then
    raise sqlstate 'PT422' using message='publication_generation_or_pool_mismatch';
  end if;
  select payload_json::json into pool from selflearn_private.journal_objects
    where object_id=source_pool and kind='source_snapshot';
  if not found or selflearn_private.digest_json_v1(pool) is distinct from source_pool
     or pg_catalog.json_typeof(pool->'players') is distinct from 'array'
     or (pool->'season')::jsonb is distinct from (doc->'season')::jsonb
     or (pool->'week')::jsonb is distinct from (doc->'week')::jsonb
     or pool->>'generated_utc' is distinct from gen->>'pool_generated_utc'
     or (pool->>'generated_utc')::timestamptz>(gen->>'recorded_utc')::timestamptz then
    raise sqlstate 'PT422' using message='publication_pool_hash_mismatch';
  end if;
  select payload_json::json into schedule from selflearn_private.journal_objects
    where object_id=gen->'source_snapshots'->>'schedule' and kind='source_snapshot';
  if not found or selflearn_private.digest_json_v1(schedule) is distinct from gen->'source_snapshots'->>'schedule'
     or pg_catalog.json_typeof(schedule->'games') is distinct from 'array' then
    raise sqlstate 'PT422' using message='publication_schedule_missing';
  end if;
  if schedule->>'season' is not null and (schedule->'season')::jsonb is distinct from (doc->'season')::jsonb then
    raise sqlstate 'PT422' using message='publication_schedule_period_mismatch';
  end if;
  select payload_json::json into calibration from selflearn_private.journal_objects
    where object_id=gen->'source_snapshots'->>'parlay_calibration' and kind='source_snapshot';
  if not found or selflearn_private.digest_json_v1(calibration) is distinct from gen->'source_snapshots'->>'parlay_calibration'
     or pg_catalog.json_typeof(calibration) not in ('object','null') then
    raise sqlstate 'PT422' using message='publication_calibration_missing';
  end if;
  -- Frozen ACK is exhaustive over source receipts, including ingestion rejects.
  select coalesce(array_agg(e.value->>'receipt_id' order by e.value->>'receipt_id'),array[]::text[]) into source_ids
    from pg_catalog.json_array_elements(doc->'receipts') e;
  select coalesce(array_agg(sid order by sid),array[]::text[]) into ack_ids from (
    select e.value->>'source_receipt_id' sid from pg_catalog.json_array_elements(ack->'receipts') e union all
    select e.value->>'source_receipt_id' sid from pg_catalog.json_array_elements(ack->'rejections') e) ids;
  select coalesce(array_agg(e.value->>'receipt_id'),array[]::text[]) into generic_ids
    from pg_catalog.json_array_elements(ack->'receipts') e;
  if (select array_agg(key order by key) from pg_catalog.json_object_keys(ack) key)
       is distinct from array['durable','object_id','receipts','rejections','version']::text[]
     or pg_catalog.json_typeof(ack->'version') is distinct from 'number' or ack->>'version' is distinct from '1'
     or pg_catalog.json_typeof(ack->'durable') is distinct from 'boolean'
     or ack->>'object_id' is distinct from p_object_id or ack->>'durable' is distinct from 'true'
     or source_ids is distinct from ack_ids
     or gen->'receipt_ids' is null
     or selflearn_private.canonical_json_v1(gen->'receipt_ids') is distinct from selflearn_private.canonical_json_v1(pg_catalog.to_json(source_ids))
     or exists(select 1 from pg_catalog.unnest(source_ids) sid where sid is null or sid !~ '^sha256:[0-9a-f]{64}$')
     or exists(select 1 from pg_catalog.json_array_elements(ack->'receipts') e
       where e.value->>'status' not in ('recorded','duplicate') or e.value->>'status' is null
          or (select array_agg(key order by key) from pg_catalog.json_object_keys(e.value) key)
             is distinct from array['receipt_id','source_receipt_id','status']::text[])
     or exists(select 1 from pg_catalog.json_array_elements(ack->'rejections') e
       where pg_catalog.json_typeof(e.value->'reason') is distinct from 'string' or pg_catalog.btrim(e.value->>'reason')=''
          or (select array_agg(key order by key) from pg_catalog.json_object_keys(e.value) key)
             is distinct from array['reason','source_receipt_id']::text[])
     or pg_catalog.cardinality(source_ids)<>(select count(distinct sid) from pg_catalog.unnest(source_ids) sid)
     or pg_catalog.cardinality(generic_ids)<>(select count(distinct rid) from pg_catalog.unnest(generic_ids) rid) then
    raise sqlstate 'PT422' using message='invalid_publication_generation_partition';
  end if;
  -- Build lookup keys ONCE. The JSONB values are JSON TEXT STRINGS, never
  -- converted numeric evidence: their contents retain -0.0/1.0/exponents.
  -- Repeated scans of a full pool/document per receipt are quadratic and made
  -- the bounded 100-rung WASM acceptance exceed 55 seconds.
  select coalesce(pg_catalog.jsonb_object_agg(sid,items),'{}'::jsonb) into pool_index from (
    select sid,pg_catalog.jsonb_agg(candidate_text) items from (
      select r.value->'learning_receipt'->>'receipt_id' sid,
        pg_catalog.json_build_object('source',r.value->'learning_receipt','leg',
          (select pg_catalog.json_object_agg(key,value order by key) from (
            select distinct on (key) key,value from (
              select key,value,1 priority from pg_catalog.json_each(p.value) union all
              select key,value,2 priority from pg_catalog.json_each(r.value)
            ) merged order by key,priority desc
          ) winner where key=any(array['game_id','gsis_id','market','line','side','direction','comparison_operator',
                                       'model_prob','selection','position'])))::text candidate_text
      from pg_catalog.json_array_elements(pool->'players') p
      cross join lateral pg_catalog.json_array_elements(p.value->'rungs') r
      where pg_catalog.json_typeof(r.value->'learning_receipt'->'receipt_id')='string'
    ) candidates group by sid
  ) grouped;
  select coalesce(pg_catalog.jsonb_object_agg(gid,items),'{}'::jsonb) into schedule_index from (
    select value->>'game_id' gid,pg_catalog.jsonb_agg(value::text) items
      from pg_catalog.json_array_elements(schedule->'games') group by value->>'game_id'
  ) grouped where gid is not null;
  select coalesce(pg_catalog.jsonb_object_agg(value->>'receipt_id',value::text),'{}'::jsonb) into generation_index
    from pg_catalog.json_array_elements(doc->'receipts');
  -- json scalar access reparses its containing text. Cache these values once,
  -- rather than reparsing a multi-MiB pool/document for every membership row.
  generation_id := gen->>'generation_id'; generation_recorded := gen->>'recorded_utc';
  generation_generated := gen->>'pool_generated_utc';
  period_week := doc->>'week'; period_season := doc->>'season'; pool_base := pool->>'base_model_version';
  -- Validate every generated source row against its original pool/schedule.
  for row in select value from pg_catalog.json_array_elements(doc->'receipts') loop
    source := row->'learning_receipt'; source_id := row->>'receipt_id'; leg := row->'leg';
    select array_agg((value#>>'{}')::json) into candidates
      from pg_catalog.jsonb_array_elements(pool_index->source_id);
    if candidates is null then raise sqlstate 'PT422' using message='publication_pool_member_missing'; end if;
    foreach candidate in array candidates loop
      if selflearn_private.digest_json_v1(candidate->'source') is distinct from selflearn_private.digest_json_v1(source)
         or selflearn_private.digest_json_v1(candidate->'leg') is distinct from selflearn_private.digest_json_v1(leg) then
        raise sqlstate 'PT422' using message='publication_pool_member_mismatch';
      end if;
    end loop;
    select array_agg((value#>>'{}')::json) into candidates
      from pg_catalog.jsonb_array_elements(schedule_index->(leg->>'game_id'));
    if candidates is null then raise sqlstate 'PT422' using message='publication_schedule_member_missing'; end if;
    game := candidates[1];
    foreach candidate in array candidates loop
      if selflearn_private.digest_json_v1(candidate) is distinct from selflearn_private.digest_json_v1(game) then
        raise sqlstate 'PT422' using message='publication_schedule_member_conflict';
      end if;
    end loop;
    if source->>'receipt_id' is distinct from source_id
       or selflearn_private.digest_json_v1(source,'receipt_id') is distinct from source_id
       or row->>'generation_id' is distinct from generation_id
       or row->>'recorded_utc' is distinct from generation_recorded
       or row->>'kickoff_utc' is distinct from game->>'kickoff_utc'
       or (game->'week')::jsonb is distinct from period_week::jsonb
       or game->>'status' is distinct from 'STATUS_SCHEDULED'
       or source->>'base_model_version' is distinct from pool_base
       or (source->>'forecast_created_utc')::timestamptz is distinct from generation_generated::timestamptz
       or (row->>'recorded_utc')::timestamptz >= (row->>'kickoff_utc')::timestamptz then
      raise sqlstate 'PT422' using message='publication_generation_member_mismatch';
    end if;
  end loop;
  -- Validation itself can cross kickoff on a large generation. Stamp again at
  -- the membership decision, not merely transaction start or lock acquisition.
  recorded := pg_catalog.clock_timestamp();
  stamp := pg_catalog.to_char(recorded at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
  -- Publication partition covers exactly the ACK's accepted generic receipts;
  -- source rows rejected during intake never acquire a generic identity here.
  for pair in select value from pg_catalog.json_array_elements(ack->'receipts') order by value->>'source_receipt_id' loop
    source_id := pair->>'source_receipt_id'; receipt_id := pair->>'receipt_id';
    select payload_json::json into generic from selflearn_private.journal_receipts r
      where r.receipt_id=pair->>'receipt_id' and r.source_receipt_id=source_id;
    if not found then raise sqlstate 'PT422' using message='publication_receipt_missing'; end if;
    row := (generation_index->>source_id)::json;
    source := row->'learning_receipt';
    if source->>'market' not in ('qb_pass_yds','rb_rush_yds','wr_rec_yds')
       or source->>'direction' is distinct from 'over' or source->>'comparison_operator' is distinct from 'gte'
       or generic->>'domain' is distinct from 'nfl' or generic->>'task' is distinct from 'nfl.parlay_leg'
       or generic->>'event_start_utc' is distinct from row->>'kickoff_utc'
       or generic->>'contract_id' is distinct from source->>'contract_id'
       or generic->>'event_id' is distinct from 'nfl:'||period_season||':game:'||(source->>'game_id') then
      raise sqlstate 'PT422' using message='publication_generic_receipt_mismatch';
    end if;
    foreach k in array array['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc',
                              'source_snapshot_id','source_timestamps','features','publication_status'] loop
      if selflearn_private.canonical_json_v1(generic->k) is distinct from selflearn_private.canonical_json_v1(source->k) then
        raise sqlstate 'PT422' using message='publication_generic_receipt_mismatch';
      end if;
    end loop;
    mapped := pg_catalog.array_append(mapped,source_id);
    if observed_end >= (generic->>'event_start_utc')::timestamptz or recorded >= (generic->>'event_start_utc')::timestamptz then
      rejected := pg_catalog.array_append(rejected,pg_catalog.json_build_object('receipt_id',receipt_id,
        'source_receipt_id',source_id,'reason','publication_not_preevent'));
    else
      accepted := pg_catalog.array_append(accepted,pg_catalog.json_build_object('receipt_id',receipt_id,
        'source_receipt_id',source_id,'market',source->>'market','direction',source->>'direction',
        'served_prob',generic->'served_prob','event_start_utc',generic->>'event_start_utc'));
    end if;
  end loop;
  status := case when cardinality(accepted)>0 and cardinality(rejected)=0 then 'accepted'
    when cardinality(accepted)>0 then 'partial' when cardinality(rejected)>0 then 'withheld' else 'no_supported_membership' end;
  result := pg_catalog.json_build_object('version',1,'kind','nfl.pool_http_publication_attestation.v1',
    'object_id',p_object_id,'generation_id',gen->>'generation_id','source_pool_id',source_pool,
    'policy',policy,'policy_id',selflearn_private.digest_json_v1(policy),'recorded_utc',stamp,
    'observation',pg_catalog.json_build_object('observed_started_utc',obs->>'observed_started_utc',
      'observed_completed_utc',obs->>'observed_completed_utc','http_status',200,
      'body_sha256','sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(raw_body,'UTF8')),'hex'),
      'body_bytes',pg_catalog.octet_length(raw_body)),
    'receipts',pg_catalog.to_json(accepted),'rejections',pg_catalog.to_json(rejected),
    'status',status,'scope','public_pool_http_evidence_only','durable',true);
  -- Preserve JSON numeric types/lexemes; jsonb concatenation can turn -0.0
  -- into 0.0 or normalize exponent-form integer-looking floats before hashing.
  select pg_catalog.json_object_agg(key,value order by key) into result from (
    select key,value from pg_catalog.json_each(result) union all
    select 'attestation_id',pg_catalog.to_json(selflearn_private.digest_json_v1(result))
  ) fields;
  insert into selflearn_private.journal_publication_attestations
    (object_id,observation_json,ack_json,attestation_json,journal_seq,stored_utc)
    values(p_object_id,p_observation_json,ack::text,selflearn_private.canonical_json_v1(result),
           pg_catalog.nextval('selflearn_private.journal_sequence'::regclass),recorded);
  return pg_catalog.json_build_object('version',1,'attestation',result,'replayed',false);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_publication_evidence';
end $$;

create function public.selflearn_journal_commit_publication_v1(p_object_id text,p_observation_json text)
returns json language sql security invoker set search_path = '' as $$
  select selflearn_private.commit_publication_v1(p_object_id,p_observation_json);
$$;

create function public.selflearn_journal_read_publication_v1(p_view text,p_key text,p_after text,p_limit integer,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare ceiling bigint; legacy bigint; after_seq bigint := 0; rows json; answer json; more boolean; next_id text;
begin
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
  ceiling := selflearn_private.publication_watermark_v1();
  if p_watermark is not null then
    if p_watermark !~ '^(0|[1-9][0-9]{0,18})$' or p_watermark::bigint>ceiling then
      raise sqlstate 'PT422' using message='invalid_read_watermark';
    end if;
    ceiling := p_watermark::bigint;
  end if;
  if p_after is not null then
    if p_after !~ '^(0|[1-9][0-9]{0,18})$' then raise sqlstate 'PT422' using message='invalid_page_cursor'; end if;
    after_seq := p_after::bigint;
  end if;
  if after_seq>ceiling then raise sqlstate 'PT422' using message='cursor_after_watermark'; end if;
  if p_view='publication_generation_receipts' then
    if p_key is null or p_key !~ '^sha256:[0-9a-f]{64}$' then
      raise sqlstate 'PT422' using message='journal_key_required';
    end if;
    if not exists(select 1 from selflearn_private.journal_generation_commits c
                  where c.object_id=p_key and c.journal_seq<=ceiling) then
      raise sqlstate 'PT422' using message='publication_generation_not_finalized';
    end if;
    -- Enumerate the immutable accepted ACK only, then lookup original generic
    -- payloads by their primary key; unrelated historical receipts never cross
    -- this RPC boundary. Missing/mismatched members fail the Python join.
    with bounded as (
      select r.journal_seq,pg_catalog.json_build_object('receipt_id',r.receipt_id,'payload_json',r.payload_json,
        'received_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'journal_seq',r.journal_seq::text) item
      from selflearn_private.journal_generation_commits c
      cross join lateral pg_catalog.json_array_elements(c.ack_json::json->'receipts') member
      join selflearn_private.journal_receipts r on r.receipt_id=member.value->>'receipt_id'
        and r.source_receipt_id=member.value->>'source_receipt_id'
      where c.object_id=p_key and c.journal_seq<=ceiling and r.journal_seq>after_seq and r.journal_seq<=ceiling
      order by r.journal_seq limit p_limit+1
    ), measured as (
      select *,pg_catalog.row_number() over(order by journal_seq) row_number,
        pg_catalog.sum(pg_catalog.octet_length(item::text)+1) over(order by journal_seq) wire_bytes from bounded
    ), page as (
      select * from measured where wire_bytes<=6291456 or row_number=1 order by journal_seq limit p_limit
    )
    select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
      (select count(*)>(select count(*) from page) from bounded),
      (select max(journal_seq)::text from page) into rows,more,next_id;
    return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
                                       'watermark',ceiling::text);
  end if;
  if p_view in ('publication_attestation','publication_attestations') then
    if p_view='publication_attestation' and (p_key is null or p_key='') then
      raise sqlstate 'PT422' using message='journal_key_required';
    end if;
    -- One heavy observation/attestation per page bounds escaped JSON responses.
    if p_limit<>1 then raise sqlstate 'PT422' using message='publication_page_limit_is_one'; end if;
    with bounded as (
      select journal_seq,pg_catalog.json_build_object('object_id',object_id,'attestation_json',attestation_json,
        'recorded_utc',pg_catalog.to_char(stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
        'journal_seq',journal_seq::text) item
      from selflearn_private.journal_publication_attestations
      where journal_seq>after_seq and journal_seq<=ceiling and (p_view='publication_attestations' or object_id=p_key)
      order by journal_seq limit 2
    ), page as (select * from bounded order by journal_seq limit 1)
    select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
      (select count(*)>1 from bounded),(select max(journal_seq)::text from page) into rows,more,next_id;
    return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
                                       'watermark',ceiling::text);
  end if;
  legacy := least(selflearn_private.observed_watermark_v1(),ceiling);
  if after_seq>legacy then
    return pg_catalog.json_build_object('version',1,'rows','[]'::json,'next_after',null,'watermark',ceiling::text);
  end if;
  answer := public.selflearn_journal_read_observed_v1(p_view,p_key,p_after,p_limit,legacy::text);
  return pg_catalog.json_build_object('version',1,'rows',answer->'rows','next_after',answer->'next_after','watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_publication_journal_page';
end $$;

revoke execute on function selflearn_private.publication_watermark_v1() from public,anon,authenticated;
grant execute on function selflearn_private.publication_watermark_v1() to service_role;
revoke execute on function selflearn_private.commit_publication_v1(text,text) from public,anon,authenticated;
grant execute on function selflearn_private.commit_publication_v1(text,text) to service_role;
revoke execute on function public.selflearn_journal_commit_publication_v1(text,text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_read_publication_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_commit_publication_v1(text,text) to service_role;
grant execute on function public.selflearn_journal_read_publication_v1(text,text,text,integer,text) to service_role;
commit;
