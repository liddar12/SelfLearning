-- Bounded local-only capture delivery/result ledger. No hosted migration,
-- producer authorization, raw Storage atomicity, quota or model adoption claim.
-- One complete capture (<=500 members/proofs), never a silently split group.
begin;

create sequence selflearn_private.capture_delivery_sequence as bigint;
grant usage on sequence selflearn_private.capture_delivery_sequence to service_role;

create table selflearn_private.journal_capture_deliveries (
  request_json text not null,
  binding_json text not null,
  batch_json text not null,
  expected_watermark text not null,
  delivery_id text primary key,
  request_id text not null,
  destination_id text not null,
  producer_id text not null,
  result_json text not null,
  delivery_seq bigint not null unique,
  recorded_utc timestamptz not null
);
create index capture_delivery_owner_seq on selflearn_private.journal_capture_deliveries
  (destination_id,producer_id,delivery_seq);
alter table selflearn_private.journal_capture_deliveries enable row level security;
alter table selflearn_private.journal_capture_deliveries force row level security;
revoke all on selflearn_private.journal_capture_deliveries from public,anon,authenticated,service_role;
grant select on selflearn_private.journal_capture_deliveries to service_role;
grant insert(request_json,binding_json,batch_json,expected_watermark)
  on selflearn_private.journal_capture_deliveries to service_role;

-- Only identity/closure is derived here. Independent raw/frozen-pool label
-- binding belongs to the trusted NFL adapter, not a client-normalized flag.
-- Lazy bounded enumeration avoids changing any existing generation writer.
create function selflearn_private.capture_members_v1(season integer,weeks jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; forecast record; doc json; item json; source json; sourceb jsonb;
  accepted jsonb; scoped text[] := array[]::text[]; seen text[] := array[]::text[];
  rid text; sid text; player text; k text; key json; scope jsonb; member jsonb;
  members jsonb := '{}'::jsonb; all_ids text[]; partition_ids text[];
begin
  if (select pg_catalog.count(*)>10000 or coalesce(pg_catalog.sum(pg_catalog.octet_length(payload_json)),0)>67108864
      from selflearn_private.journal_receipts where domain='nfl' and task='nfl.parlay_leg') then
    raise sqlstate 'PT422' using message='capture_membership_exceeds_work_limit';
  end if;
  for forecast in select * from selflearn_private.journal_receipts where domain='nfl' and task='nfl.parlay_leg' loop
    if forecast.payload_json::jsonb->>'event_id' !~ '^nfl:[0-9]{4}:game:.+$' then
      raise sqlstate 'PT422' using message='unlocatable_nfl_membership';
    end if;
    if pg_catalog.split_part(forecast.payload_json::jsonb->>'event_id',':',2)=season::text then
      scoped := pg_catalog.array_append(scoped,forecast.receipt_id);
    end if;
  end loop;
  if (select pg_catalog.count(*)>256 or coalesce(pg_catalog.sum(pg_catalog.octet_length(o.payload_json)),0)>67108864
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)) then
    raise sqlstate 'PT422' using message='capture_generations_exceed_work_limit';
  end if;
  for rec in select c.*,o.payload_json from selflearn_private.journal_generation_commits c
      join selflearn_private.journal_objects o using(object_id) order by c.journal_seq loop
    accepted := rec.ack_json::jsonb->'receipts';
    if not exists(select 1 from pg_catalog.jsonb_array_elements(accepted) a where a->>'receipt_id'=any(scoped)) then continue; end if;
    doc := rec.payload_json::json;
    if pg_catalog.json_typeof(doc) is distinct from 'object' or pg_catalog.json_typeof(doc->'version') is distinct from 'number'
       or doc->>'version' is distinct from '1' or selflearn_private.digest_json_v1(doc,'envelope_id') is distinct from rec.object_id
       or doc->>'envelope_id' is distinct from rec.object_id then
      raise sqlstate 'PT422' using message='invalid_capture_generation_hash';
    end if;
    select pg_catalog.array_agg(x->>'receipt_id' order by x->>'receipt_id') into all_ids
      from pg_catalog.json_array_elements(doc->'receipts') x;
    select pg_catalog.array_agg(id order by id) into partition_ids from (
      select x->>'source_receipt_id' id from pg_catalog.jsonb_array_elements(accepted) x union all
      select x->>'source_receipt_id' from pg_catalog.jsonb_array_elements(rec.ack_json::jsonb->'rejections') x) p;
    if all_ids is distinct from partition_ids or pg_catalog.cardinality(all_ids)<>(select pg_catalog.count(distinct x) from pg_catalog.unnest(all_ids) x) then
      raise sqlstate 'PT422' using message='invalid_capture_generation_partition';
    end if;
    for item in select value from pg_catalog.json_array_elements(rec.ack_json::json->'receipts') loop
      rid := item->>'receipt_id'; sid := item->>'source_receipt_id';
      if not (rid=any(scoped)) then continue; end if;
      select * into forecast from selflearn_private.journal_receipts where receipt_id=rid;
      if not found or forecast.source_receipt_id is distinct from sid then
        raise sqlstate 'PT422' using message='capture_receipt_mapping_mismatch';
      end if;
      select x->'learning_receipt' into source from pg_catalog.json_array_elements(doc->'receipts') x where x->>'receipt_id'=sid;
      sourceb := source::jsonb;
      player := coalesce(nullif(sourceb->>'player_id',''),nullif(sourceb->>'gsis_id',''));
      if pg_catalog.json_typeof(source) is distinct from 'object'
         or pg_catalog.jsonb_typeof(sourceb->'version') is distinct from 'number' or sourceb->>'version' is distinct from '1'
         or selflearn_private.digest_json_v1(source,'receipt_id') is distinct from sid
         or sourceb->>'receipt_id' is distinct from sid
         or pg_catalog.jsonb_typeof(sourceb->'season') is distinct from 'number' or sourceb->>'season' is distinct from season::text
         or pg_catalog.jsonb_typeof(sourceb->'week') is distinct from 'number' or not (sourceb->>'week' ~ '^[1-9][0-9]?$')
         or (sourceb->>'week')::integer not between 1 and 18
         or doc->>'season' is distinct from sourceb->>'season' or pg_catalog.json_typeof(doc->'season') is distinct from 'number'
         or doc->>'week' is distinct from sourceb->>'week' or pg_catalog.json_typeof(doc->'week') is distinct from 'number'
         or pg_catalog.jsonb_typeof(sourceb->'game_id') is distinct from 'string' or pg_catalog.btrim(sourceb->>'game_id')=''
         or player is null or (sourceb ? 'player_id' and pg_catalog.jsonb_typeof(sourceb->'player_id') is distinct from 'string')
         or (sourceb ? 'gsis_id' and pg_catalog.jsonb_typeof(sourceb->'gsis_id') is distinct from 'string')
         or (nullif(sourceb->>'player_id','') is not null and nullif(sourceb->>'gsis_id','') is not null and sourceb->>'player_id'<>sourceb->>'gsis_id')
         or pg_catalog.jsonb_typeof(sourceb->'market') is distinct from 'string' or sourceb->>'market' not in ('qb_pass_yds','rb_rush_yds','wr_rec_yds')
         or pg_catalog.jsonb_typeof(sourceb->'line') is distinct from 'number'
         or pg_catalog.jsonb_typeof(sourceb->'side') is distinct from 'string' or sourceb->>'side' not in ('home','away')
         or sourceb->>'direction' is distinct from 'over' or sourceb->>'comparison_operator' is distinct from 'gte'
         or sourceb->>'publication_status' is distinct from 'generated_unverified' then
        raise sqlstate 'PT422' using message='unlocatable_original_capture_member';
      end if;
      key := pg_catalog.json_build_array(season,sourceb->>'game_id',player,sourceb->>'market',
               selflearn_private.nfl_line_token_v1(sourceb->>'line'),sourceb->>'side','over','gte');
      if selflearn_private.digest_json_v1(key) is distinct from sourceb->>'contract_id'
         or sourceb->>'contract_id' is distinct from forecast.payload_json::jsonb->>'contract_id'
         or forecast.payload_json::jsonb->>'event_id' is distinct from 'nfl:'||season::text||':game:'||(sourceb->>'game_id') then
        raise sqlstate 'PT422' using message='capture_member_contract_mismatch';
      end if;
      foreach k in array array['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc',
          'source_snapshot_id','source_timestamps','features','publication_status'] loop
        if selflearn_private.canonical_json_v1(source->k) is distinct from selflearn_private.canonical_json_v1(forecast.payload_json::json->k) then
          raise sqlstate 'PT422' using message='capture_member_original_parity_mismatch';
        end if;
      end loop;
      seen := pg_catalog.array_append(seen,rid);
      if not (weeks @> pg_catalog.jsonb_build_array((sourceb->>'week')::integer)) then continue; end if;
      scope := pg_catalog.jsonb_build_array(season,sourceb->>'game_id',player,sourceb->>'market',sourceb->>'side');
      if members ? rid then
        member := members->rid;
        if member->'scope' is distinct from scope or member->>'source_receipt_id' is distinct from sid then
          raise sqlstate 'PT422' using message='conflicting_capture_member_identity';
        end if;
        member := pg_catalog.jsonb_set(member,'{generation_object_ids}',
          (select pg_catalog.jsonb_agg(v order by v) from (select distinct value v from pg_catalog.jsonb_array_elements_text(
             member->'generation_object_ids'||pg_catalog.jsonb_build_array(rec.object_id))) ids));
      else
        member := pg_catalog.jsonb_build_object('receipt_id',rid,'source_receipt_id',sid,'scope',scope,
                    'generation_object_ids',pg_catalog.jsonb_build_array(rec.object_id));
      end if;
      members := pg_catalog.jsonb_set(members,array[rid],member);
    end loop;
  end loop;
  if exists(select 1 from pg_catalog.unnest(scoped) x where not (x=any(seen))) then
    raise sqlstate 'PT422' using message='unlocatable_missing_capture_generation';
  end if;
  if (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members))>500 then
    raise sqlstate 'PT422' using message='complete_capture_exceeds_member_limit';
  end if;
  return members;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_membership';
end $$;

create function selflearn_private.guard_capture_delivery_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  request json; req jsonb; binding jsonb; batch jsonb; envelope jsonb; pointer jsonb; ref jsonb;
  group_row jsonb; member jsonb; proof jsonb; members jsonb; expected jsonb; identity json;
  receipt_ids jsonb; source_ids jsonb; generation_ids jsonb; group_membership json;
  delivered text[] := array[]::text[]; group_ids text[] := array[]::text[]; ids text[];
  seen_observations text[] := array[]::text[]; seen_revisions text[] := array[]::text[];
  total bigint := 0; size bigint; index integer; rid text; k text; ack json; status text; group_count integer;
  manifest jsonb; source_ref jsonb; observation jsonb; component jsonb; profile text; expected_url text;
  source_id text; wanted_week jsonb; previous timestamptz; completed timestamptz; began timestamptz; observed timestamptz;
  source_bytes bigint := 0; ordinal integer := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_request_json',new.request_json,'p_binding_json',new.binding_json,
       'p_batch_json',new.batch_json,'p_watermark',new.expected_watermark)::text)>8388608 then
    raise sqlstate 'PT422' using message='capture_delivery_rpc_exceeds_byte_limit';
  end if;
  request := selflearn_private.require_canonical_v1(new.request_json,3145728); req := request::jsonb;
  binding := selflearn_private.require_canonical_v1(new.binding_json,3145728)::jsonb;
  batch := selflearn_private.require_canonical_v1(new.batch_json,8388608)::jsonb;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(req) key) is distinct from
      array['binding_policy','capture_id','delivery_id','destination_id','envelope','kind','producer_id','raw_pointers','version']::text[]
     or req->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(req->'version') is distinct from 'number' or req->>'kind' is distinct from 'nfl.capture_delivery_request.v1'
     or req->>'binding_policy' is distinct from 'nfl.capture_labels_v1'
     or pg_catalog.jsonb_typeof(req->'producer_id') is distinct from 'string' or not (req->>'producer_id' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
    raise sqlstate 'PT422' using message='invalid_capture_delivery_request';
  end if;
  foreach k in array array['destination_id','capture_id','delivery_id'] loop
    if pg_catalog.jsonb_typeof(req->k) is distinct from 'string' or not (req->>k ~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message='invalid_capture_delivery_identity';
    end if;
  end loop;
  identity := pg_catalog.json_build_object('version',1,'kind','nfl.capture_delivery.v1','destination_id',req->>'destination_id',
      'producer_id',req->>'producer_id','capture_id',req->>'capture_id','binding_policy',req->>'binding_policy');
  if selflearn_private.digest_json_v1(identity) is distinct from req->>'delivery_id' then
    raise sqlstate 'PT422' using message='capture_delivery_id_mismatch';
  end if;
  envelope := req->'envelope';
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(envelope) key) is distinct from
       array['kind','manifest','observations','raw_objects','scope','version']::text[]
     or envelope->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(envelope->'version') is distinct from 'number' or envelope->>'kind' is distinct from 'nfl.capture_envelope.v1'
     or envelope->>'scope' is distinct from 'uploaded_only' or envelope->'manifest'->>'capture_id' is distinct from req->>'capture_id'
     or selflearn_private.digest_json_v1(request->'envelope'->'manifest','capture_id') is distinct from req->>'capture_id'
     or pg_catalog.jsonb_typeof(envelope->'raw_objects') is distinct from 'array'
     or pg_catalog.jsonb_array_length(envelope->'raw_objects') not between 1 and 20
     or pg_catalog.jsonb_typeof(req->'raw_pointers') is distinct from 'array'
     or pg_catalog.jsonb_array_length(req->'raw_pointers')<>pg_catalog.jsonb_array_length(envelope->'raw_objects') then
    raise sqlstate 'PT422' using message='invalid_capture_delivery_envelope';
  end if;
  if (select pg_catalog.jsonb_agg(value order by value->>'object_id') from pg_catalog.jsonb_array_elements(req->'raw_pointers')) is distinct from req->'raw_pointers'
     or (select pg_catalog.count(distinct value->>'object_id') from pg_catalog.jsonb_array_elements(req->'raw_pointers'))<>pg_catalog.jsonb_array_length(req->'raw_pointers') then
    raise sqlstate 'PT422' using message='noncanonical_capture_pointer_set';
  end if;
  for pointer in select value from pg_catalog.jsonb_array_elements(req->'raw_pointers') loop
    if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(pointer) key) is distinct from
       array['chunks','kind','object_id','raw_bytes','scope','version']::text[]
       or pointer->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(pointer->'version') is distinct from 'number' or pointer->>'kind' is distinct from 'selflearn.evidence_object.v1'
       or pointer->>'scope' is distinct from 'uploaded_only' or pg_catalog.jsonb_typeof(pointer->'object_id') is distinct from 'string'
       or not (pointer->>'object_id' ~ '^sha256:[0-9a-f]{64}$')
       or pg_catalog.jsonb_typeof(pointer->'raw_bytes') is distinct from 'number' or not (pointer->>'raw_bytes' ~ '^[0-9]+$')
       or (pointer->>'raw_bytes')::bigint not between 0 and 67108864 or pg_catalog.jsonb_typeof(pointer->'chunks') is distinct from 'array' then
      raise sqlstate 'PT422' using message='invalid_capture_raw_pointer';
    end if;
    size := (pointer->>'raw_bytes')::bigint; total := total+size;
    if total>67108864 or pg_catalog.jsonb_array_length(pointer->'chunks')<>(size+1048575)/1048576 then
      raise sqlstate 'PT422' using message='invalid_capture_pointer_layout';
    end if;
    index := 0;
    for ref in select value from pg_catalog.jsonb_array_elements(pointer->'chunks') loop
      if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(ref) key) is distinct from array['chunk_id','raw_bytes']::text[]
         or pg_catalog.jsonb_typeof(ref->'chunk_id') is distinct from 'string' or not (ref->>'chunk_id' ~ '^sha256:[0-9a-f]{64}$')
         or pg_catalog.jsonb_typeof(ref->'raw_bytes') is distinct from 'number' or ref->>'raw_bytes' is distinct from least(1048576,size-index*1048576)::text then
        raise sqlstate 'PT422' using message='invalid_capture_chunk_reference';
      end if;
      index := index+1;
    end loop;
  end loop;
  if (select pg_catalog.jsonb_agg(pg_catalog.jsonb_build_object('raw_sha256',value->>'object_id','raw_bytes',value->'raw_bytes') order by value->>'object_id')
      from pg_catalog.jsonb_array_elements(req->'raw_pointers')) is distinct from envelope->'raw_objects' then
    raise sqlstate 'PT422' using message='capture_pointer_envelope_mismatch';
  end if;
  manifest := envelope->'manifest';
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(manifest) key) is distinct from
       array['audit','capture_id','clock_policy','completed_utc','kind','publication_status','season','sources','started_utc','version','weeks']::text[]
     or manifest->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(manifest->'version') is distinct from 'number'
     or manifest->>'kind' is distinct from 'nfl.outcome_capture.v1'
     or manifest->>'clock_policy' is distinct from 'producer observations only; upstream issue and exact game-finish times unknown'
     or manifest->>'publication_status' is distinct from 'source_capture_not_public_forecast_evidence'
     or pg_catalog.jsonb_typeof(manifest->'season') is distinct from 'number' or not (manifest->>'season' ~ '^(20[0-9]{2}|2100)$')
     or pg_catalog.jsonb_typeof(manifest->'weeks') is distinct from 'array' or pg_catalog.jsonb_array_length(manifest->'weeks') not between 1 and 18
     or pg_catalog.jsonb_typeof(manifest->'sources') is distinct from 'array'
     or pg_catalog.jsonb_array_length(manifest->'sources')<>pg_catalog.jsonb_array_length(manifest->'weeks')+2
     or pg_catalog.jsonb_typeof(envelope->'observations') is distinct from 'object'
     or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(envelope->'observations'))<>pg_catalog.jsonb_array_length(manifest->'sources')
     or pg_catalog.octet_length(selflearn_private.canonical_json_v1((request->'envelope'->'manifest'))) >65536 then
    raise sqlstate 'PT422' using message='invalid_delivery_capture_metadata';
  end if;
  foreach k in array array['started_utc','completed_utc'] loop
    if pg_catalog.jsonb_typeof(manifest->k) is distinct from 'string' or not (manifest->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
      raise sqlstate 'PT422' using message='invalid_delivery_capture_clock';
    end if;
  end loop;
  previous := (manifest->>'started_utc')::timestamptz; completed := (manifest->>'completed_utc')::timestamptz;
  if previous>completed or completed>pg_catalog.clock_timestamp() then raise sqlstate 'PT422' using message='invalid_delivery_capture_clock'; end if;
  for source_ref in select value from pg_catalog.jsonb_array_elements(manifest->'sources') loop
    ordinal := ordinal+1;
    if ordinal<=pg_catalog.jsonb_array_length(manifest->'weeks') then
      profile := 'espn_scoreboard_v1'; wanted_week := manifest->'weeks'->(ordinal-1);
      expected_url := 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&dates='||(manifest->>'season')||'&week='||(wanted_week#>>'{}');
    elsif ordinal=pg_catalog.jsonb_array_length(manifest->'weeks')+1 then
      profile := 'nflverse_stats_player_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_'||(manifest->>'season')||'.csv';
    else
      profile := 'nflverse_snap_counts_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_'||(manifest->>'season')||'.csv';
    end if;
    source_id := source_ref->>'observation_id'; observation := envelope->'observations'->source_id;
    if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(source_ref) key) is distinct from array['observation_id','profile','season','week']::text[]
       or source_ref->>'profile' is distinct from profile
       or pg_catalog.jsonb_typeof(source_ref->'week') is distinct from pg_catalog.jsonb_typeof(wanted_week)
       or source_ref->>'week' is distinct from wanted_week#>>'{}'
       or pg_catalog.jsonb_typeof(source_ref->'season') is distinct from 'number' or source_ref->>'season' is distinct from manifest->>'season'
       or pg_catalog.jsonb_typeof(source_ref->'observation_id') is distinct from 'string' or not (source_id ~ '^sha256:[0-9a-f]{64}$')
       or pg_catalog.jsonb_typeof(observation) is distinct from 'object'
       or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(observation) key) is distinct from
         array['audit','evidence_class','fetch_started_utc','http','kind','observed_utc','profile','raw_bytes','raw_sha256','season','source_url','upstream_updated_utc','version','week']::text[]
       or selflearn_private.digest_json_v1(request->'envelope'->'observations'->source_id) is distinct from source_id
       or observation->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(observation->'version') is distinct from 'number'
       or observation->>'kind' is distinct from 'nfl.outcome_source_observation.v1' or observation->>'evidence_class' is distinct from 'observed_source_snapshot'
       or observation->>'profile' is distinct from profile
       or pg_catalog.jsonb_typeof(observation->'season') is distinct from 'number' or observation->>'season' is distinct from manifest->>'season'
       or pg_catalog.jsonb_typeof(observation->'week') is distinct from pg_catalog.jsonb_typeof(wanted_week)
       or observation->>'week' is distinct from wanted_week#>>'{}'
       or observation->>'source_url' is distinct from expected_url or observation->'upstream_updated_utc' is distinct from 'null'::jsonb
       or pg_catalog.jsonb_typeof(observation->'http') is distinct from 'object' or observation->'http'->>'status' is distinct from '200'
       or pg_catalog.jsonb_typeof(observation->'http'->'status') is distinct from 'number'
       or pg_catalog.jsonb_typeof(observation->'audit') is distinct from 'object'
       or pg_catalog.jsonb_typeof(observation->'raw_bytes') is distinct from 'number' or not (observation->>'raw_bytes' ~ '^[1-9][0-9]*$')
       or (observation->>'raw_bytes')::bigint>(case when profile='espn_scoreboard_v1' then 4194304 else 16777216 end)
       or not exists(select 1 from pg_catalog.jsonb_array_elements(envelope->'raw_objects') r where r->>'raw_sha256'=observation->>'raw_sha256' and r->'raw_bytes'=observation->'raw_bytes')
       or pg_catalog.octet_length(selflearn_private.canonical_json_v1((request->'envelope'->'observations'->source_id)))>65536 then
      raise sqlstate 'PT422' using message='invalid_delivery_source_metadata';
    end if;
    foreach k in array array['fetch_started_utc','observed_utc'] loop
      if pg_catalog.jsonb_typeof(observation->k) is distinct from 'string' or not (observation->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
        raise sqlstate 'PT422' using message='invalid_delivery_source_clock';
      end if;
    end loop;
    began := (observation->>'fetch_started_utc')::timestamptz; observed := (observation->>'observed_utc')::timestamptz;
    if previous>began or began>observed or observed>completed then raise sqlstate 'PT422' using message='invalid_delivery_source_clock'; end if;
    previous := observed; source_bytes := source_bytes+(observation->>'raw_bytes')::bigint;
  end loop;
  if source_bytes>67108864 or exists(select 1 from pg_catalog.jsonb_array_elements(envelope->'raw_objects') r
      where not exists(select 1 from pg_catalog.jsonb_each(envelope->'observations') o where o.value->>'raw_sha256'=r->>'raw_sha256')) then
    raise sqlstate 'PT422' using message='invalid_delivery_source_closure';
  end if;
  if new.expected_watermark is null or new.expected_watermark !~ '^(0|[1-9][0-9]{0,18})$' then
    raise sqlstate 'PT422' using message='invalid_capture_delivery_watermark';
  end if;
  if new.expected_watermark::bigint is distinct from selflearn_private.observed_watermark_v1() then
    raise sqlstate 'PT409' using message='stale_capture_delivery_watermark';
  end if;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(binding) key) is distinct from
       array['groups','kind','membership_id','season','version','weeks']::text[]
     or binding->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(binding->'version') is distinct from 'number' or binding->>'kind' is distinct from 'nfl.capture_delivery_binding.v1'
     or pg_catalog.jsonb_typeof(binding->'season') is distinct from 'number' or not (binding->>'season' ~ '^[0-9]{4}$')
     or binding->'season' is distinct from envelope->'manifest'->'season' or binding->'weeks' is distinct from envelope->'manifest'->'weeks'
     or pg_catalog.jsonb_typeof(binding->'weeks') is distinct from 'array' or pg_catalog.jsonb_array_length(binding->'weeks') not between 1 and 18
     or exists(select 1 from pg_catalog.jsonb_array_elements(binding->'weeks') w where pg_catalog.jsonb_typeof(w)<>'number' or w::text !~ '^[1-9][0-9]?$' or w::text::integer>18)
     or (select pg_catalog.jsonb_agg(value order by value::integer) from (select distinct value from pg_catalog.jsonb_array_elements(binding->'weeks')) w) is distinct from binding->'weeks'
     or pg_catalog.jsonb_typeof(binding->'groups') is distinct from 'array' or pg_catalog.jsonb_array_length(binding->'groups')>500
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(batch) key) is distinct from array['observations','revisions','version']::text[]
     or batch->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(batch->'version') is distinct from 'number' or pg_catalog.jsonb_typeof(batch->'observations') is distinct from 'array'
     or pg_catalog.jsonb_typeof(batch->'revisions') is distinct from 'array' or pg_catalog.jsonb_array_length(batch->'observations')>500 or pg_catalog.jsonb_array_length(batch->'revisions')>500 then
    raise sqlstate 'PT422' using message='invalid_capture_delivery_binding';
  end if;
  members := selflearn_private.capture_members_v1((binding->>'season')::integer,binding->'weeks');
  for proof in select value from pg_catalog.jsonb_array_elements(batch->'observations') loop
    for k,component in select key,value from pg_catalog.jsonb_each(proof->'source_observations') loop
      profile := case k when 'espn.scoreboard' then 'espn_scoreboard_v1' when 'nflverse.stats_player' then 'nflverse_stats_player_v1'
                    when 'nflverse.snap_counts' then 'nflverse_snap_counts_v1' else null end;
      select value->>'observation_id' into source_id from pg_catalog.jsonb_array_elements(manifest->'sources')
        where value->>'profile'=profile and (profile<>'espn_scoreboard_v1' or value->'week'=proof->'measurement'->'week');
      if source_id is null or component->>'observation_id' is distinct from source_id
         or component->>'observed_utc' is distinct from envelope->'observations'->source_id->>'observed_utc' then
        raise sqlstate 'PT422' using message='capture_observation_request_provenance_mismatch';
      end if;
    end loop;
  end loop;
  for group_row in select value from pg_catalog.jsonb_array_elements(binding->'groups') loop
    if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(group_row) key) is distinct from
       array['generation_object_ids','group_id','membership_id','observation_ids','reason_codes','receipt_ids','revision_ids','scope','source_receipt_ids','status']::text[]
       or pg_catalog.jsonb_typeof(group_row->'scope') is distinct from 'array' or pg_catalog.jsonb_array_length(group_row->'scope')<>5
       or group_row->>'group_id' is distinct from selflearn_private.digest_json_v1((group_row->'scope')::json)
       or group_row->>'group_id'=any(group_ids) then raise sqlstate 'PT422' using message='invalid_capture_group'; end if;
    group_ids := pg_catalog.array_append(group_ids,group_row->>'group_id');
    select coalesce(pg_catalog.jsonb_agg(key order by key),'[]'::jsonb),coalesce(pg_catalog.jsonb_agg(value->>'source_receipt_id' order by value->>'source_receipt_id'),'[]'::jsonb)
      into receipt_ids,source_ids from pg_catalog.jsonb_each(members) where selflearn_private.canonical_json_v1((value->'scope')::json)=selflearn_private.canonical_json_v1((group_row->'scope')::json);
    select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) into generation_ids from (
      select distinct g.v from pg_catalog.jsonb_each(members) m cross join lateral pg_catalog.jsonb_array_elements_text(m.value->'generation_object_ids') g(v)
        where m.value->'scope'=group_row->'scope') x;
    group_membership := pg_catalog.json_build_object('binding_policy','nfl.capture_labels_v1','scope',group_row->'scope',
      'receipt_ids',receipt_ids,'source_receipt_ids',source_ids,'generation_object_ids',generation_ids);
    if receipt_ids='[]'::jsonb or group_row->'receipt_ids' is distinct from receipt_ids or group_row->'source_receipt_ids' is distinct from source_ids
       or group_row->'generation_object_ids' is distinct from generation_ids
       or group_row->>'membership_id' is distinct from selflearn_private.digest_json_v1(group_membership)
       or group_row->>'status' not in ('accepted','withheld') or pg_catalog.jsonb_typeof(group_row->'status') is distinct from 'string'
       or pg_catalog.jsonb_typeof(group_row->'reason_codes') is distinct from 'array'
       or exists(select 1 from pg_catalog.jsonb_array_elements(group_row->'reason_codes') r where pg_catalog.jsonb_typeof(r)<>'string' or not (r#>>'{}' ~ '^[a-z][a-z0-9_]{0,99}$'))
       or (select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) from (select distinct value v from pg_catalog.jsonb_array_elements(group_row->'reason_codes')) r) is distinct from group_row->'reason_codes'
       or (group_row->>'status'='accepted') is distinct from (group_row->'reason_codes'='[]'::jsonb) then
      raise sqlstate 'PT422' using message='capture_group_membership_or_status_mismatch';
    end if;
    for rid in select value from pg_catalog.jsonb_array_elements_text(receipt_ids) loop delivered := pg_catalog.array_append(delivered,rid); end loop;
    foreach k in array array['observation_ids','revision_ids'] loop
      if pg_catalog.jsonb_typeof(group_row->k) is distinct from 'array'
         or (select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) from (select distinct value v from pg_catalog.jsonb_array_elements(group_row->k)) r) is distinct from group_row->k
         or exists(select 1 from pg_catalog.jsonb_array_elements(group_row->k) r where pg_catalog.jsonb_typeof(r)<>'string') then
        raise sqlstate 'PT422' using message='invalid_capture_group_proof_ids';
      end if;
    end loop;
    select coalesce(pg_catalog.jsonb_agg(o->>'observation_id' order by o->>'observation_id'),'[]'::jsonb) into expected
      from pg_catalog.jsonb_array_elements(batch->'observations') o where receipt_ids ? (o->>'receipt_id');
    if expected is distinct from group_row->'observation_ids' then raise sqlstate 'PT422' using message='capture_group_observation_partition'; end if;
    select coalesce(pg_catalog.jsonb_agg(r->>'revision_id' order by r->>'revision_id'),'[]'::jsonb) into expected
      from pg_catalog.jsonb_array_elements(batch->'revisions') r where receipt_ids ? (r->'resolution'->>'receipt_id');
    if expected is distinct from group_row->'revision_ids' then raise sqlstate 'PT422' using message='capture_group_revision_partition'; end if;
    if group_row->>'status'='withheld' and (group_row->'observation_ids'<>'[]'::jsonb or group_row->'revision_ids'<>'[]'::jsonb) then
      raise sqlstate 'PT422' using message='withheld_capture_group_has_writes';
    end if;
    if group_row->>'status'='accepted' and ((select pg_catalog.count(distinct o->>'receipt_id') from pg_catalog.jsonb_array_elements(batch->'observations') o where receipt_ids ? (o->>'receipt_id'))<>pg_catalog.jsonb_array_length(receipt_ids)
       or pg_catalog.jsonb_array_length(group_row->'observation_ids')<>pg_catalog.jsonb_array_length(receipt_ids)) then
      raise sqlstate 'PT422' using message='incomplete_capture_group_proofs';
    end if;
    if group_row->>'status'='accepted' and (select pg_catalog.count(distinct selflearn_private.canonical_json_v1(
        pg_catalog.json_build_object('actual',o->'measurement'->'actual','played',o->'measurement'->'played',
          'reason',o->'measurement'->'reason','status',o->'measurement'->'status','participation_basis',o->'measurement'->'participation_basis')))
        from pg_catalog.jsonb_array_elements(batch->'observations') o where receipt_ids ? (o->>'receipt_id'))<>1 then
      raise sqlstate 'PT422' using message='conflicting_capture_group_measurements';
    end if;
    for k in select value from pg_catalog.jsonb_array_elements_text(group_row->'observation_ids') loop seen_observations:=pg_catalog.array_append(seen_observations,k); end loop;
    for k in select value from pg_catalog.jsonb_array_elements_text(group_row->'revision_ids') loop seen_revisions:=pg_catalog.array_append(seen_revisions,k); end loop;
  end loop;
  if pg_catalog.cardinality(delivered)<>(select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members))
     or pg_catalog.cardinality(delivered)<>(select pg_catalog.count(distinct x) from pg_catalog.unnest(delivered) x)
     or pg_catalog.cardinality(seen_observations)<>pg_catalog.jsonb_array_length(batch->'observations')
     or pg_catalog.cardinality(seen_revisions)<>pg_catalog.jsonb_array_length(batch->'revisions')
     or pg_catalog.cardinality(seen_observations)<>(select pg_catalog.count(distinct x) from pg_catalog.unnest(seen_observations) x)
     or pg_catalog.cardinality(seen_revisions)<>(select pg_catalog.count(distinct x) from pg_catalog.unnest(seen_revisions) x)
     or binding->>'membership_id' is distinct from selflearn_private.digest_json_v1(pg_catalog.json_build_object(
        'binding_policy','nfl.capture_labels_v1','groups',(select coalesce(pg_catalog.json_agg(g->>'membership_id' order by n),'[]'::json)
        from pg_catalog.jsonb_array_elements(binding->'groups') with ordinality a(g,n)))) then
    raise sqlstate 'PT422' using message='incomplete_capture_population_partition';
  end if;
  ack := public.selflearn_journal_commit_observed_v1(new.batch_json,new.expected_watermark);
  new.delivery_id := req->>'delivery_id'; new.request_id := selflearn_private.digest_json_v1(request);
  new.destination_id := req->>'destination_id'; new.producer_id := req->>'producer_id';
  new.recorded_utc := pg_catalog.clock_timestamp(); new.delivery_seq := pg_catalog.nextval('selflearn_private.capture_delivery_sequence'::regclass);
  group_count := pg_catalog.jsonb_array_length(binding->'groups');
  status := case when group_count=0 then 'no_supported_membership'
    when not exists(select 1 from pg_catalog.jsonb_array_elements(binding->'groups') g where g->>'status'='withheld') then 'accepted'
    when exists(select 1 from pg_catalog.jsonb_array_elements(binding->'groups') g where g->>'status'='accepted') then 'partial' else 'withheld' end;
  new.result_json := selflearn_private.canonical_json_v1(pg_catalog.json_build_object('version',1,'kind','nfl.capture_delivery_result.v1',
    'delivery_id',new.delivery_id,'request_id',new.request_id,'capture_id',req->>'capture_id','membership_id',binding->>'membership_id',
    'status',status,'groups',binding->'groups','added_observations',(ack->>'added_observations')::integer,'added_revisions',(ack->>'added_revisions')::integer,
    'input_watermark',new.expected_watermark,'watermark',ack->>'watermark','delivery_seq',new.delivery_seq::text,
    'recorded_utc',pg_catalog.to_char(new.recorded_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'durable',true,'scope','observations_and_group_results','source_authentication','not_performed'));
  if pg_catalog.octet_length(new.result_json)>3145728 then raise sqlstate 'PT422' using message='capture_delivery_result_exceeds_byte_limit'; end if;
  return new;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_delivery';
end $$;

create trigger capture_delivery_insert before insert on selflearn_private.journal_capture_deliveries
  for each row execute function selflearn_private.guard_capture_delivery_v1();
create trigger capture_delivery_immutable before update or delete on selflearn_private.journal_capture_deliveries
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger capture_delivery_no_truncate before truncate on selflearn_private.journal_capture_deliveries
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function public.selflearn_journal_commit_capture_delivery_v1(p_request_json text,p_binding_json text,p_batch_json text,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare request json; prior selflearn_private.journal_capture_deliveries%rowtype; answer text;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_request_json',p_request_json,'p_binding_json',p_binding_json,
      'p_batch_json',p_batch_json,'p_watermark',p_watermark)::text)>8388608 then
    raise sqlstate 'PT422' using message='capture_delivery_rpc_exceeds_byte_limit';
  end if;
  request := selflearn_private.require_canonical_v1(p_request_json,3145728);
  select * into prior from selflearn_private.journal_capture_deliveries where delivery_id=request->>'delivery_id';
  if found then
    if prior.request_json is distinct from p_request_json then raise sqlstate 'PT409' using message='capture_delivery_request_conflict'; end if;
    -- Recovery deliberately precedes today's CAS and binding schema checks.
    return prior.result_json::json;
  end if;
  insert into selflearn_private.journal_capture_deliveries(request_json,binding_json,batch_json,expected_watermark)
    values(p_request_json,p_binding_json,p_batch_json,p_watermark) returning result_json into answer;
  return answer::json;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_delivery_request';
end $$;

create function public.selflearn_journal_read_capture_delivery_v1(p_delivery_id text,p_after text,p_limit integer)
returns json language plpgsql security invoker set search_path = '' as $$
declare result jsonb; cursor integer := 0; total integer; groups jsonb;
begin
  if p_delivery_id is null or p_delivery_id !~ '^sha256:[0-9a-f]{64}$' or p_limit is null or p_limit not between 1 and 100
     or (p_after is not null and p_after !~ '^(0|[1-9][0-9]{0,3})$') then
    raise sqlstate 'PT422' using message='invalid_capture_delivery_page';
  end if;
  cursor := coalesce(p_after,'0')::integer;
  select result_json::jsonb into result from selflearn_private.journal_capture_deliveries where delivery_id=p_delivery_id;
  if not found then return pg_catalog.json_build_object('version',1,'result',null,'groups','[]'::json,'next_after',null,'total_groups',0); end if;
  total := pg_catalog.jsonb_array_length(result->'groups');
  if cursor>total then raise sqlstate 'PT422' using message='capture_cursor_after_end'; end if;
  select coalesce(pg_catalog.jsonb_agg(value order by n),'[]'::jsonb) into groups
    from pg_catalog.jsonb_array_elements(result->'groups') with ordinality g(value,n) where n>cursor and n<=cursor+p_limit;
  return pg_catalog.json_build_object('version',1,'result',result-'groups','groups',groups,
    'next_after',case when cursor+p_limit<total then (cursor+p_limit)::text else null end,'total_groups',total);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_delivery_page';
end $$;

revoke execute on function selflearn_private.capture_members_v1(integer,jsonb) from public,anon,authenticated;
revoke execute on function selflearn_private.guard_capture_delivery_v1() from public,anon,authenticated;
grant execute on function selflearn_private.capture_members_v1(integer,jsonb) to service_role;
grant execute on function selflearn_private.guard_capture_delivery_v1() to service_role;
revoke execute on function public.selflearn_journal_commit_capture_delivery_v1(text,text,text,text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_read_capture_delivery_v1(text,text,integer) from public,anon,authenticated;
grant execute on function public.selflearn_journal_commit_capture_delivery_v1(text,text,text,text) to service_role;
grant execute on function public.selflearn_journal_read_capture_delivery_v1(text,text,integer) to service_role;
commit;
