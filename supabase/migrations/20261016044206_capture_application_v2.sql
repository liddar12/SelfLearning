-- Additive capture application v2. Local-only until separately reviewed/applied.
-- Existing v1 function bodies and limits remain unchanged.
begin;

create function selflearn_private.capture_members_v2(season integer,weeks jsonb) returns jsonb
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
  if (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members))>10000 then
    raise sqlstate 'PT422' using message='complete_capture_exceeds_member_limit';
  end if;
  return members;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_membership';
end $$;

revoke execute on function selflearn_private.capture_members_v2(integer,jsonb) from public,anon,authenticated;
grant execute on function selflearn_private.capture_members_v2(integer,jsonb) to service_role;

-- New reusable request validation; the v1 trigger/body is not rewritten.
create function selflearn_private.capture_request_v2(payload text) returns json
language plpgsql security invoker set search_path = '' as $$
declare
  request json; req jsonb; envelope jsonb; pointer jsonb; ref jsonb; manifest jsonb;
  source_ref jsonb; observation jsonb; profile text; expected_url text; source_id text; wanted_week jsonb;
  total bigint := 0; size bigint; index integer; k text; identity json;
  previous timestamptz; completed timestamptz; began timestamptz; observed timestamptz;
  source_bytes bigint := 0; ordinal integer := 0;
begin
  request := selflearn_private.require_canonical_v1(payload,3145728);
  perform selflearn_private.capture_metadata_v2(request->'envelope',0);
  req := request::jsonb;
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
  -- JSONB numeric equality is not a typed DTO check: descriptor1.0 must not
  -- alias pointer/source integer1 and leave an unreadable registered request.
  for ref in select value from pg_catalog.jsonb_array_elements(envelope->'raw_objects') loop
    if pg_catalog.jsonb_typeof(ref) is distinct from 'object'
       or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(ref) key) is distinct from array['raw_bytes','raw_sha256']::text[]
       or pg_catalog.jsonb_typeof(ref->'raw_sha256') is distinct from 'string' or not (ref->>'raw_sha256' ~ '^sha256:[0-9a-f]{64}$')
       or pg_catalog.jsonb_typeof(ref->'raw_bytes') is distinct from 'number' or not (ref->>'raw_bytes' ~ '^[1-9][0-9]*$')
       or (ref->>'raw_bytes')::bigint>16777216 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  end loop;
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
  perform selflearn_private.capture_audit_v2((request->'envelope'->'manifest'->'audit'),null);
  foreach k in array array['started_utc','completed_utc'] loop
    if pg_catalog.jsonb_typeof(manifest->k) is distinct from 'string' or pg_catalog.length(manifest->>k)>128 or not (manifest->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
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
    perform selflearn_private.capture_audit_v2((request->'envelope'->'observations'->source_id->'audit'),profile);
    if exists(select 1 from pg_catalog.jsonb_object_keys(observation->'http') key where key not in ('status','etag','last_modified','content_type'))
       or exists(select 1 from pg_catalog.jsonb_each(observation->'http') h where h.key<>'status' and
         (pg_catalog.jsonb_typeof(h.value) is distinct from 'string' or pg_catalog.length(h.value#>>'{}')>512
           or h.value#>>'{}' ~ ('['||pg_catalog.chr(1)||'-'||pg_catalog.chr(31)||']'))) then
      raise sqlstate 'PT422' using message='invalid_capture_application_request';
    end if;
    foreach k in array array['fetch_started_utc','observed_utc'] loop
      if pg_catalog.jsonb_typeof(observation->k) is distinct from 'string' or pg_catalog.length(observation->>k)>128 or not (observation->>k ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
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
  if exists(select 1 from pg_catalog.jsonb_array_elements(manifest->'weeks') w where pg_catalog.jsonb_typeof(w) is distinct from 'number' or w::text !~ '^[1-9][0-9]?$' or w::text::integer>18)
     or (select pg_catalog.jsonb_agg(value order by value::integer) from (select distinct value from pg_catalog.jsonb_array_elements(manifest->'weeks')) w) is distinct from manifest->'weeks' then
    raise sqlstate 'PT422' using message='invalid_capture_application_weeks';
  end if;
  return request;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application_request';
end $$;

-- Match the packaged capture envelope's bounded metadata domain, NOT facts:
-- metadata accepts no floats; outcome actuals continue to use original JSON.
create function selflearn_private.capture_metadata_v2(value json,depth integer) returns bigint[]
language plpgsql security invoker set search_path = '' as $$
declare kind text:=pg_catalog.json_typeof(value); nodes bigint:=1; chars bigint:=0; child json; pair record; counts bigint[];
begin
  if value is null or depth>24 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  if kind='string' then
    chars:=pg_catalog.length(value#>>'{}');
    if chars>65536 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  elsif kind='number' then
    if value::text !~ '^(0|-?[1-9][0-9]*)$' or pg_catalog.abs((value::text)::numeric)>9223372036854775807 then
      raise sqlstate 'PT422' using message='invalid_capture_application_request';
    end if;
  elsif kind='array' then
    if pg_catalog.json_array_length(value)>2048 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    for child in select x.value from pg_catalog.json_array_elements(value) x(value) loop
      counts:=selflearn_private.capture_metadata_v2(child,depth+1); nodes:=nodes+counts[1]; chars:=chars+counts[2];
      if nodes>16384 or chars>3145728 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    end loop;
  elsif kind='object' then
    if (select pg_catalog.count(*) from pg_catalog.json_object_keys(value))>2048 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    for pair in select * from pg_catalog.json_each(value) loop
      if pg_catalog.length(pair.key)>128 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
      counts:=selflearn_private.capture_metadata_v2(pair.value,depth+1); nodes:=nodes+counts[1]; chars:=chars+counts[2];
      if nodes>16384 or chars>3145728 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    end loop;
  elsif kind not in ('null','boolean') then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  return array[nodes,chars];
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application_request';
end $$;
create function selflearn_private.capture_audit_v2(audit json,profile text) returns void
language plpgsql security invoker set search_path = '' as $$
declare keys text[]; expected text[]; k text; maximum integer; value json; count bigint;
begin
  if pg_catalog.json_typeof(audit) is distinct from 'object' then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  select pg_catalog.array_agg(key order by key) into keys from pg_catalog.json_object_keys(audit) key;
  if profile in ('nflverse_stats_player_v1','nflverse_snap_counts_v1') then
    expected:=array['duplicate_rows','measurement_policy','outside_regular_week_rows','rows'];
    if keys is distinct from expected or pg_catalog.json_typeof(audit->'rows') is distinct from 'number' or not (audit->>'rows' ~ '^[1-9][0-9]*$')
       or (audit->>'rows')::bigint>16777216 or audit->>'measurement_policy' is distinct from 'raw values retained; missing/negative cells are not coerced or graded' then
      raise sqlstate 'PT422' using message='invalid_capture_application_request';
    end if;
    foreach k in array array['duplicate_rows','outside_regular_week_rows'] loop
      if pg_catalog.json_typeof(audit->k) is distinct from 'number' or not (audit->>k ~ '^(0|[1-9][0-9]*)$')
         or (audit->>k)::bigint>(audit->>'rows')::bigint then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    end loop;
    return;
  elsif profile='espn_scoreboard_v1' then
    expected:=array['conflicting_game_ids','duplicate_events','events','final_games','invalid_final_scores','malformed_events','nonfinal_games','normalized_games','quarantined_game_ids'];
    if keys is distinct from expected then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    foreach k in array array['conflicting_game_ids','duplicate_events','events','final_games','invalid_final_scores','malformed_events','nonfinal_games','normalized_games'] loop
      if pg_catalog.json_typeof(audit->k) is distinct from 'number' or not (audit->>k ~ '^(0|[1-9][0-9]*)$')
         or (audit->>k)::bigint>32 or (k='events' and audit->>k='0') then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    end loop;
    expected:=array['quarantined_game_ids']; maximum:=32;
  else
    if keys is distinct from array['cross_week_conflicting_game_ids','normalized_games','quarantined_game_ids']::text[]
       or pg_catalog.json_typeof(audit->'normalized_games') is distinct from 'number' or not (audit->>'normalized_games' ~ '^(0|[1-9][0-9]*)$')
       or (audit->>'normalized_games')::bigint>576 then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
    expected:=array['cross_week_conflicting_game_ids','quarantined_game_ids']; maximum:=576;
  end if;
  foreach k in array expected loop
    if pg_catalog.json_typeof(audit->k) is distinct from 'array' or pg_catalog.json_array_length(audit->k)>maximum
       or exists(select 1 from pg_catalog.json_array_elements(audit->k) ids(value) where pg_catalog.json_typeof(ids.value) is distinct from 'string'
          or not (ids.value#>>'{}' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$'))
       or selflearn_private.canonical_json_v1(audit->k) is distinct from selflearn_private.canonical_json_v1(
         (select coalesce(pg_catalog.json_agg(id order by id collate pg_catalog."C"),'[]'::json)
           from (select distinct ids.value#>>'{}' id from pg_catalog.json_array_elements(audit->k) ids(value)) uniqueids)) then
      raise sqlstate 'PT422' using message='invalid_capture_application_request';
    end if;
  end loop;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application_request';
end $$;

revoke execute on function selflearn_private.capture_request_v2(text) from public,anon,authenticated;
grant execute on function selflearn_private.capture_request_v2(text) to service_role;

create function selflearn_private.capture_source_vector_v2(request json) returns text
language plpgsql security invoker set search_path = '' as $$
declare answer json;
begin
  select pg_catalog.json_agg(pg_catalog.json_build_array(o.value->>'profile',(o.value->>'season')::integer,
       o.value->'week',o.value->>'raw_sha256',(o.value->>'raw_bytes')::bigint,
       pg_catalog.to_char((o.value->>'observed_utc')::timestamptz at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
       order by o.value->>'profile',(o.value->>'season')::integer,coalesce((o.value->>'week')::integer,0))
    into answer from pg_catalog.json_each(request->'envelope'->'observations') o;
  if answer is null then raise sqlstate 'PT422' using message='invalid_capture_source_vector'; end if;
  return selflearn_private.digest_json_v1(answer);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_source_vector';
end $$;
revoke execute on function selflearn_private.capture_source_vector_v2(json) from public,anon,authenticated;
grant execute on function selflearn_private.capture_source_vector_v2(json) to service_role;

create sequence selflearn_private.capture_application_sequence_v2 as bigint;
grant usage on sequence selflearn_private.capture_application_sequence_v2 to service_role;
create table selflearn_private.capture_applications_v2 (
  request_json text not null, seal_json text not null, expected_watermark text not null,
  application_id text primary key, capture_intent_id text not null unique,
  request_id text not null, capture_id text not null, destination_id text not null, producer_id text not null,
  binding_policy text not null, source_vector_id text not null, seal_id text not null, membership_id text not null,
  registration_json text not null, application_seq bigint not null unique, registered_utc timestamptz not null,
  unique(destination_id,producer_id,binding_policy,source_vector_id)
);
create table selflearn_private.capture_application_batches_v2 (
  application_id text not null references selflearn_private.capture_applications_v2(application_id),
  step_id text primary key, group_results_json text not null, batch_json text not null, expected_watermark text not null,
  ordinal integer not null, result_json text not null, batch_result_id text not null unique,
  application_seq bigint not null unique, recorded_utc timestamptz not null,
  unique(application_id,ordinal)
);
create table selflearn_private.capture_application_results_v2 (
  application_id text primary key references selflearn_private.capture_applications_v2(application_id),
  stop_reason text not null, expected_watermark text not null,
  result_json text not null, terminal_result_id text not null unique,
  application_seq bigint not null unique, recorded_utc timestamptz not null
);
-- Bounded, DB-derived optimization only. No raw/provider/outcome authority.
-- JSONB wrappers contain original canonical source JSON AS TEXT, so source
-- numeric lexemes never pass through JSONB number normalization.
create table selflearn_private.capture_membership_cache_v2 (
  season integer not null, weeks_json text not null,
  authority_watermark bigint not null, policy_id text not null, cache_id text primary key,
  membership_id text not null, members jsonb not null, groups jsonb not null, source_index jsonb not null,
  payload_sha256 text not null, accounted_bytes bigint not null, stored_utc timestamptz not null,
  unique(season,weeks_json,authority_watermark,policy_id)
);
create index capture_application_owner_v2 on selflearn_private.capture_applications_v2(destination_id,producer_id,application_seq);
-- The PK/UNIQUE(application_id,ordinal) indexes serve original-step lookups
-- and ordered per-application progress. Evidence indexes remain unchanged.
do $$ declare name text; begin
  foreach name in array array['capture_applications_v2','capture_application_batches_v2','capture_application_results_v2','capture_membership_cache_v2'] loop
    execute pg_catalog.format('alter table selflearn_private.%I enable row level security',name);
    execute pg_catalog.format('alter table selflearn_private.%I force row level security',name);
    execute pg_catalog.format('revoke all on selflearn_private.%I from public,anon,authenticated,service_role',name);
    execute pg_catalog.format('grant select on selflearn_private.%I to service_role',name);
    execute pg_catalog.format('create trigger application_immutable before update or delete on selflearn_private.%I for each row execute function selflearn_private.refuse_mutation_v1()',name);
    execute pg_catalog.format('create trigger application_no_truncate before truncate on selflearn_private.%I for each statement execute function selflearn_private.refuse_mutation_v1()',name);
  end loop;
end $$;
grant insert(request_json,seal_json,expected_watermark) on selflearn_private.capture_applications_v2 to service_role;
grant insert(application_id,step_id,group_results_json,batch_json,expected_watermark) on selflearn_private.capture_application_batches_v2 to service_role;
grant insert(application_id,stop_reason,expected_watermark) on selflearn_private.capture_application_results_v2 to service_role;
grant insert(season,weeks_json) on selflearn_private.capture_membership_cache_v2 to service_role;

create function selflearn_private.capture_lock_v2() returns void
language plpgsql security invoker set search_path = '' as $$
begin
  if pg_catalog.current_setting('transaction_isolation')<>'read committed' then
    raise sqlstate 'PT422' using message='invalid_capture_application_isolation';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
end $$;
create function selflearn_private.capture_fence_v2(value text) returns bigint
language plpgsql security invoker set search_path = '' as $$
declare expected bigint;
begin
  if value is null or value !~ '^(0|[1-9][0-9]{0,18})$' then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  expected:=value::bigint;
  if expected is distinct from selflearn_private.observed_watermark_v1() then
    raise sqlstate 'PT409' using message='stale_capture_application_watermark';
  end if;
  return expected;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
create function selflearn_private.capture_ledger_watermark_v2() returns bigint
language sql stable security invoker set search_path = '' as $$
  select coalesce(pg_catalog.max(n),0) from (
    select pg_catalog.max(application_seq) n from selflearn_private.capture_applications_v2 union all
    select pg_catalog.max(application_seq) from selflearn_private.capture_application_batches_v2 union all
    select pg_catalog.max(application_seq) from selflearn_private.capture_application_results_v2) committed
$$;
create function selflearn_private.capture_groups_v2(members jsonb) returns json
language plpgsql security invoker set search_path = '' as $$
declare s jsonb; answer json[]:=array[]::json[]; rid jsonb; sid jsonb; gid jsonb;
begin
  for s in select distinct value->'scope' from pg_catalog.jsonb_each(members) loop
    select pg_catalog.jsonb_agg(key order by key),pg_catalog.jsonb_agg(value->>'source_receipt_id' order by value->>'source_receipt_id')
      into rid,sid from pg_catalog.jsonb_each(members) where value->'scope'=s;
    select pg_catalog.jsonb_agg(v order by v) into gid from (select distinct g.v from pg_catalog.jsonb_each(members) m
      cross join lateral pg_catalog.jsonb_array_elements_text(m.value->'generation_object_ids') g(v) where m.value->'scope'=s) refs;
    answer:=pg_catalog.array_append(answer,pg_catalog.json_build_object('group_id',selflearn_private.digest_json_v1(s::json),
      'scope',s,'receipt_ids',rid,'source_receipt_ids',sid,'generation_object_ids',gid,
      'membership_id',selflearn_private.digest_json_v1(pg_catalog.json_build_object('binding_policy','nfl.capture_labels_v1',
         'scope',s,'receipt_ids',rid,'source_receipt_ids',sid,'generation_object_ids',gid))));
  end loop;
  if pg_catalog.cardinality(answer)>2000 then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  return (select coalesce(pg_catalog.json_agg(v order by v->>'group_id'),'[]'::json) from pg_catalog.unnest(answer) v);
end $$;
create function selflearn_private.capture_membership_id_v2(groups json) returns text
language sql security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('binding_policy','nfl.capture_labels_v1',
    'groups',(select coalesce(pg_catalog.json_agg(value->>'membership_id' order by value->>'group_id'),'[]'::json)
      from pg_catalog.json_array_elements(groups))))
$$;
create function selflearn_private.capture_held_group_v2(g json,reasons json) returns json
language sql security invoker set search_path = '' as $$
  select pg_catalog.json_build_object('group_id',g->'group_id','scope',g->'scope','receipt_ids',g->'receipt_ids',
    'source_receipt_ids',g->'source_receipt_ids','generation_object_ids',g->'generation_object_ids','membership_id',g->'membership_id',
    'status','withheld','reason_codes',reasons,'observation_ids','[]'::json,'revision_ids','[]'::json)
$$;
create function selflearn_private.capture_fact_v2(proof json) returns json
language sql security invoker set search_path = '' as $$
  select pg_catalog.json_build_object('week',proof->'measurement'->'week','actual',proof->'measurement'->'actual',
    'played',proof->'measurement'->'played','status',proof->'measurement'->'status','reason',proof->'measurement'->'reason',
    'participation_basis',proof->'measurement'->'participation_basis','game',proof->'measurement'->'game',
    'source_observations',proof->'source_observations')
$$;
create function selflearn_private.capture_group_views_v2(app_id text,seal json,ceiling bigint,terminal_reason text default null) returns json
language plpgsql security invoker set search_path = '' as $$
declare g json; result json; batch json; state text; reasons json; answer json[]:=array[]::json[];
begin
  for g in select value from pg_catalog.json_array_elements(seal->'groups') order by value->>'group_id' loop
    result:=null;
    if g->>'status'='withheld' then
      state:='initial_withheld'; result:=selflearn_private.capture_held_group_v2(g,g->'reason_codes');
    else
      select result_json::json into batch from selflearn_private.capture_application_batches_v2
        where application_id=app_id and ordinal=(g->>'batch_ordinal')::integer and application_seq<=ceiling;
      if found then
        select value into result from pg_catalog.json_array_elements(batch->'groups') where value->>'group_id'=g->>'group_id';
        state:=case when batch->>'status'='not_applied' then 'not_applied' else 'committed' end;
      elsif terminal_reason is not null then
        state:='not_applied'; reasons:=pg_catalog.json_build_array(terminal_reason);
        result:=selflearn_private.capture_held_group_v2(g,reasons);
      else state:='pending'; end if;
    end if;
    answer:=pg_catalog.array_append(answer,pg_catalog.json_build_object('sealed_group',g,'execution_state',state,'result',result));
  end loop;
  return pg_catalog.to_json(answer);
end $$;

create function selflearn_private.capture_v1_ownership_guard_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare request json; identity json; k text;
begin
  perform selflearn_private.capture_lock_v2();
  request:=selflearn_private.require_canonical_v1(new.request_json,3145728);
  -- Derive only common identity here. The UNCHANGED v1 trigger owns its old
  -- metadata contract; new v2 audit/snapshot rules must not retrofit v1.
  foreach k in array array['destination_id','capture_id','delivery_id'] loop
    if pg_catalog.json_typeof(request->k) is distinct from 'string' or not (request->>k ~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
  end loop;
  if pg_catalog.json_typeof(request->'version') is distinct from 'number' or request->>'version' is distinct from '1'
     or request->>'kind' is distinct from 'nfl.capture_delivery_request.v1' or request->>'binding_policy' is distinct from 'nfl.capture_labels_v1'
     or pg_catalog.json_typeof(request->'producer_id') is distinct from 'string' or not (request->>'producer_id' ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$') then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  identity:=pg_catalog.json_build_object('version',1,'kind','nfl.capture_delivery.v1','destination_id',request->'destination_id',
    'producer_id',request->'producer_id','capture_id',request->'capture_id','binding_policy',request->'binding_policy');
  if request->>'delivery_id' is distinct from selflearn_private.digest_json_v1(identity) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  if exists(select 1 from selflearn_private.capture_applications_v2 where capture_intent_id=request->>'delivery_id') then
    raise sqlstate 'PT409' using message='capture_application_v2_owns_intent';
  end if;
  return new;
end $$;
-- Alphabetical BEFORE-trigger ordering puts this before capture_delivery_insert.
create trigger capture_delivery_crossversion_v2 before insert on selflearn_private.journal_capture_deliveries
  for each row execute function selflearn_private.capture_v1_ownership_guard_v2();

create function selflearn_private.capture_components_v2(request json,components json,week json) returns void
language plpgsql security invoker set search_path = '' as $$
declare k text; component json; profile text; ref json; observation json;
begin
  if pg_catalog.json_typeof(components) is distinct from 'object' then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  for k,component in select key,value from pg_catalog.json_each(components) loop
    profile:=case k when 'espn.scoreboard' then 'espn_scoreboard_v1' when 'nflverse.stats_player' then 'nflverse_stats_player_v1'
      when 'nflverse.snap_counts' then 'nflverse_snap_counts_v1' else null end;
    select value into ref from pg_catalog.json_array_elements(request->'envelope'->'manifest'->'sources')
      where value->>'profile'=profile and (profile<>'espn_scoreboard_v1' or value->>'week' is not distinct from week#>>'{}');
    observation:=request->'envelope'->'observations'->(ref->>'observation_id');
    if profile is null or ref is null or component->>'observation_id' is distinct from ref->>'observation_id'
       or component->>'observed_utc' is distinct from observation->>'observed_utc' then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
  end loop;
end $$;
create function selflearn_private.capture_validate_fact_v2(request json,g json,members jsonb,source_index jsonb) returns void
language plpgsql security invoker set search_path = '' as $$
declare fact json:=g->'fact'; member jsonb; original json; generic json; contract json; measurement json; proof json;
  mid text; evidence_id text; observed text; hit json;
begin
  if pg_catalog.json_typeof(fact) is distinct from 'object' or
     (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(fact) key) is distinct from
       array['actual','game','participation_basis','played','reason','source_observations','status','week']::text[]
     or g->>'fact_id' is distinct from selflearn_private.digest_json_v1(fact) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  member:=members->(g->'receipt_ids'->>0);
  select payload_json::json into generic from selflearn_private.journal_receipts where receipt_id=member->>'receipt_id';
  original:=(source_index->(member->>'receipt_id')->>'source_json')::json;
  contract:=pg_catalog.json_build_array((original->>'season')::integer,original->>'game_id',
    coalesce(nullif(original->>'player_id',''),original->>'gsis_id'),original->>'market',
    selflearn_private.nfl_line_token_v1(original->>'line'),original->>'side','over','gte');
  hit:=case when fact->>'status'='VOID' then 'null'::json else pg_catalog.to_json((fact->>'actual')::numeric >= (original->>'line')::numeric) end;
  measurement:=pg_catalog.json_build_object('version',1,'canonical_contract',contract,'week',fact->'week','actual',fact->'actual',
    'played',fact->'played','status',fact->'status','hit',hit,'reason',fact->'reason',
    'participation_basis',fact->'participation_basis','game',fact->'game');
  mid:=selflearn_private.digest_json_v1(measurement);
  evidence_id:=selflearn_private.digest_json_v1(pg_catalog.json_build_object('measurement_id',mid,'components',fact->'source_observations'));
  select value->>'observed_utc' into observed from pg_catalog.json_each(fact->'source_observations')
    order by (value->>'observed_utc')::timestamptz desc limit 1;
  proof:=pg_catalog.json_build_object('version',1,'kind','nfl.observed_measurement.v1','evidence_basis','nfl.observed_resolution_v2',
    'receipt_id',generic->'receipt_id','contract_id',generic->'contract_id','event_id',generic->'event_id',
    'measurement',measurement,'measurement_id',mid,'source_observations',fact->'source_observations',
    'evidence_observation_id',evidence_id,'observed_utc',observed);
  proof:=pg_catalog.json_build_object('version',1,'kind','nfl.observed_measurement.v1','evidence_basis','nfl.observed_resolution_v2',
    'receipt_id',generic->'receipt_id','contract_id',generic->'contract_id','event_id',generic->'event_id',
    'measurement',measurement,'measurement_id',mid,'source_observations',fact->'source_observations',
    'evidence_observation_id',evidence_id,'observed_utc',observed,'observation_id',selflearn_private.digest_json_v1(proof));
  perform selflearn_private.validate_observation_v1(selflearn_private.canonical_json_v1(proof));
  perform selflearn_private.capture_components_v2(request,fact->'source_observations',fact->'week');
  if fact->>'week' is distinct from original->>'week' or exists(
    select 1 from pg_catalog.json_array_elements_text(g->'receipt_ids') ids(rid)
      where (source_index->ids.rid->>'kickoff_utc')::timestamptz is distinct from (fact->'game'->>'kickoff_utc')::timestamptz
         or (source_index->ids.rid->>'source_json')::json->>'week' is distinct from fact->>'week') then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
end $$;

create function selflearn_private.capture_source_authority_v2() returns bigint
language sql volatile security invoker set search_path = '' as $$
  select coalesce(pg_catalog.max(n),0) from (
    select pg_catalog.max(journal_seq) n from selflearn_private.journal_receipts union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_objects union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_generation_commits) authority
$$;
create function selflearn_private.capture_source_index_v2(members jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare source_to_receipt jsonb; answer jsonb:='{}'::jsonb; rec record; item json; source json; rid text; kickoff text;
begin
  select coalesce(pg_catalog.jsonb_object_agg(value->>'source_receipt_id',key),'{}'::jsonb) into source_to_receipt
    from pg_catalog.jsonb_each(members);
  for rec in select distinct o.object_id,o.payload_json from pg_catalog.jsonb_each(members) m
      cross join lateral pg_catalog.jsonb_array_elements_text(m.value->'generation_object_ids') refs(id)
      join selflearn_private.journal_objects o on o.object_id=refs.id order by o.object_id loop
    for item in select value from pg_catalog.json_array_elements(rec.payload_json::json->'receipts') loop
      rid:=source_to_receipt->>(item->>'receipt_id');
      if rid is null then continue; end if;
      source:=item->'learning_receipt';
      if answer ? rid then
        if answer->rid->>'source_json' is distinct from source::text then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      else
        select payload_json::json->>'event_start_utc' into kickoff from selflearn_private.journal_receipts where receipt_id=rid;
        answer:=pg_catalog.jsonb_set(answer,array[rid],pg_catalog.jsonb_build_object('source_json',source::text,'kickoff_utc',kickoff));
      end if;
    end loop;
  end loop;
  if (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(answer))<>(select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members)) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  return answer;
end $$;
create function selflearn_private.capture_membership_policy_v2() returns text
language sql volatile security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',2,'kind','nfl.capture_membership_policy.v2',
    'functions',pg_catalog.json_agg(pg_catalog.json_build_array(p.oid::regprocedure::text,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex')) order by p.proname)))
  from pg_catalog.pg_proc p where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(array[
    'capture_members_v2','capture_groups_v2','capture_membership_id_v2','capture_source_index_v2','capture_source_authority_v2',
    'guard_capture_membership_cache_v2','capture_get_membership_v2','capture_membership_policy_v2','capture_lock_v2',
    'require_canonical_v1','canonical_json_v1','digest_json_v1','json_number_v1','json_string_v1','nfl_line_token_v1'])
$$;
create function selflearn_private.guard_capture_membership_cache_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare weeks json; current_bytes bigint; current_rows bigint;
begin
  perform selflearn_private.capture_lock_v2();
  weeks:=selflearn_private.require_canonical_v1(new.weeks_json,1024);
  if new.season is null or new.season not between 2000 and 2100 or pg_catalog.json_typeof(weeks) is distinct from 'array'
     or pg_catalog.json_array_length(weeks) not between 1 and 18
     or exists(select 1 from pg_catalog.json_array_elements(weeks) w where pg_catalog.json_typeof(w) is distinct from 'number'
       or w::text !~ '^[1-9][0-9]?$' or w::text::integer>18)
     or selflearn_private.canonical_json_v1(weeks) is distinct from selflearn_private.canonical_json_v1(
       (select pg_catalog.json_agg(w order by w) from (select distinct value::text::integer w from pg_catalog.json_array_elements(weeks)) sorted)) then
    raise sqlstate 'PT422' using message='invalid_capture_membership_cache';
  end if;
  new.authority_watermark:=selflearn_private.capture_source_authority_v2(); new.policy_id:=selflearn_private.capture_membership_policy_v2();
  new.cache_id:=selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',2,'kind','nfl.capture_membership_cache.v2',
    'season',new.season,'weeks',weeks,'authority_watermark',new.authority_watermark::text,'policy_id',new.policy_id));
  select pg_catalog.count(*),coalesce(pg_catalog.sum(accounted_bytes),0) into current_rows,current_bytes
    from selflearn_private.capture_membership_cache_v2;
  if current_rows>=256 then raise sqlstate 'PT503' using message='capture_membership_cache_capacity_exceeded'; end if;
  new.members:=selflearn_private.capture_members_v2(new.season,weeks::jsonb);
  new.groups:=selflearn_private.capture_groups_v2(new.members)::jsonb;
  new.membership_id:=selflearn_private.capture_membership_id_v2(new.groups::json);
  new.source_index:=selflearn_private.capture_source_index_v2(new.members);
  new.payload_sha256:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    pg_catalog.jsonb_build_array(new.members,new.groups,new.source_index)::text,'UTF8')),'hex');
  new.stored_utc:=pg_catalog.clock_timestamp();
  -- Actual serialized full row, including escaped original-source text and
  -- metadata; 1KiB conservatively covers this counter's own representation.
  new.accounted_bytes:=pg_catalog.octet_length(pg_catalog.row_to_json(new)::text)+1024;
  if current_bytes+new.accounted_bytes>134217728 then raise sqlstate 'PT503' using message='capture_membership_cache_capacity_exceeded'; end if;
  return new;
end $$;
create trigger membership_cache_insert before insert on selflearn_private.capture_membership_cache_v2
  for each row execute function selflearn_private.guard_capture_membership_cache_v2();
create function selflearn_private.capture_get_membership_v2(season integer,weeks json) returns selflearn_private.capture_membership_cache_v2
language plpgsql volatile security invoker set search_path = '' as $$
declare answer selflearn_private.capture_membership_cache_v2%rowtype; fence bigint; policy text; encoded text;
begin
  perform selflearn_private.capture_lock_v2();
  fence:=selflearn_private.capture_source_authority_v2(); policy:=selflearn_private.capture_membership_policy_v2();
  encoded:=selflearn_private.canonical_json_v1(weeks);
  select c.* into answer from selflearn_private.capture_membership_cache_v2 c
    where c.season=capture_get_membership_v2.season and c.weeks_json=encoded and c.authority_watermark=fence and c.policy_id=policy;
  if not found then
    insert into selflearn_private.capture_membership_cache_v2(season,weeks_json) values(season,encoded) returning * into answer;
  end if;
  if answer.payload_sha256 is distinct from pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
       pg_catalog.jsonb_build_array(answer.members,answer.groups,answer.source_index)::text,'UTF8')),'hex')
     or answer.membership_id is distinct from selflearn_private.capture_membership_id_v2(answer.groups::json)
     or answer.cache_id is distinct from selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',2,'kind','nfl.capture_membership_cache.v2',
       'season',season,'weeks',weeks,'authority_watermark',fence::text,'policy_id',policy)) then
    raise sqlstate 'PT422' using message='invalid_capture_membership_cache';
  end if;
  return answer;
end $$;
create function public.selflearn_journal_prepare_capture_membership_v2(p_season integer,p_weeks_json text) returns json
language plpgsql volatile security invoker set search_path = '' as $$
declare value selflearn_private.capture_membership_cache_v2%rowtype; weeks json;
begin
  perform selflearn_private.capture_lock_v2(); weeks:=selflearn_private.require_canonical_v1(p_weeks_json,1024);
  value:=selflearn_private.capture_get_membership_v2(p_season,weeks);
  return pg_catalog.json_build_object('version',2,'kind','nfl.capture_membership_cache.v2','scope','derived_membership_cache_only',
    'durable_ack',false,'learning_accepted',false,'season',value.season,'weeks',value.weeks_json::json,
    'authority_watermark',value.authority_watermark::text,'policy_id',value.policy_id,'cache_id',value.cache_id,'membership_id',value.membership_id,
    'member_count',(select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(value.members)),'group_count',pg_catalog.jsonb_array_length(value.groups));
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_membership_cache';
end $$;
revoke execute on function public.selflearn_journal_prepare_capture_membership_v2(integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_prepare_capture_membership_v2(integer,text) to service_role;

create function selflearn_private.guard_capture_application_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare request json; rb jsonb; capture json; seal json; sb jsonb; identity json; population json; members jsonb;
  certificate selflearn_private.capture_membership_cache_v2%rowtype;
  g json; base json; assignment json; expected_step text; group_ids jsonb; seen text[]:=array[]::text[];
  previous text:=''; previous_ordinal integer:=-1; ordinal integer:=0; n integer; total integer; answer json; record json;
begin
  perform selflearn_private.capture_lock_v2();
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_request_json',new.request_json,'p_seal_json',new.seal_json,
       'p_watermark',new.expected_watermark)::text)>8388608 then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  request:=selflearn_private.require_canonical_v1(new.request_json,3145728); rb:=request::jsonb;
  seal:=selflearn_private.require_canonical_v1(new.seal_json,3145728); sb:=seal::jsonb;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(rb) key) is distinct from
      array['application_id','capture_request','execution_policy','kind','version']::text[]
     or pg_catalog.jsonb_typeof(rb->'version') is distinct from 'number' or rb->>'version' is distinct from '2'
     or rb->>'kind' is distinct from 'nfl.capture_application_request.v2' or rb->>'execution_policy' is distinct from 'sealed_whole_groups_v2' then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  capture:=selflearn_private.capture_request_v2(selflearn_private.canonical_json_v1(request->'capture_request'));
  identity:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application.v2','capture_intent_id',capture->'delivery_id',
    'execution_policy','sealed_whole_groups_v2');
  new.application_id:=selflearn_private.digest_json_v1(identity);
  if rb->>'application_id' is distinct from new.application_id then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  new.capture_intent_id:=capture->>'delivery_id'; new.request_id:=selflearn_private.digest_json_v1(request);
  new.capture_id:=capture->>'capture_id'; new.destination_id:=capture->>'destination_id'; new.producer_id:=capture->>'producer_id';
  new.binding_policy:='nfl.capture_labels_v1'; new.source_vector_id:=selflearn_private.capture_source_vector_v2(capture);
  if exists(select 1 from selflearn_private.journal_capture_deliveries where delivery_id=new.capture_intent_id) then
    raise sqlstate 'PT409' using message='capture_application_legacy_original_exists';
  end if;
  if exists(select 1 from selflearn_private.capture_applications_v2 a where a.destination_id=new.destination_id
      and a.producer_id=new.producer_id and a.binding_policy=new.binding_policy and a.source_vector_id=new.source_vector_id) then
    raise sqlstate 'PT409' using message='capture_application_source_vector_reused';
  end if;
  perform selflearn_private.capture_fence_v2(new.expected_watermark);
  certificate:=selflearn_private.capture_get_membership_v2((capture->'envelope'->'manifest'->>'season')::integer,capture->'envelope'->'manifest'->'weeks');
  members:=certificate.members; population:=certificate.groups::json; new.membership_id:=certificate.membership_id;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(sb) key) is distinct from
     array['application_id','batches','binding_policy','capture_id','capture_intent_id','execution_policy','groups','kind','limits',
       'membership_id','request_id','seal_id','season','source_vector_id','source_watermark','version','weeks']::text[]
     or pg_catalog.jsonb_typeof(sb->'version') is distinct from 'number' or sb->>'version' is distinct from '2'
     or sb->>'kind' is distinct from 'nfl.capture_application_seal.v2' or sb->>'application_id' is distinct from new.application_id
     or sb->>'request_id' is distinct from new.request_id or sb->>'capture_intent_id' is distinct from new.capture_intent_id
     or sb->>'capture_id' is distinct from new.capture_id or sb->>'execution_policy' is distinct from 'sealed_whole_groups_v2'
     or sb->>'binding_policy' is distinct from new.binding_policy or sb->>'source_watermark' is distinct from new.expected_watermark
     or pg_catalog.json_typeof(seal->'source_watermark') is distinct from 'string'
     or sb->>'source_vector_id' is distinct from new.source_vector_id or sb->>'membership_id' is distinct from new.membership_id
     or selflearn_private.canonical_json_v1(seal->'season') is distinct from selflearn_private.canonical_json_v1(capture->'envelope'->'manifest'->'season')
     or selflearn_private.canonical_json_v1(seal->'weeks') is distinct from selflearn_private.canonical_json_v1(capture->'envelope'->'manifest'->'weeks')
     or pg_catalog.json_typeof(seal->'limits') is distinct from 'object'
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(seal->'limits') key) is distinct from
       array['members','observations','revisions','rpc_bytes']::text[]
     or exists(select 1 from pg_catalog.json_each(seal->'limits') e where pg_catalog.json_typeof(e.value) is distinct from 'number'
        or e.value::text !~ '^[1-9][0-9]*$' or (e.value::text)::bigint>case when e.key='rpc_bytes' then 8388608 else 500 end)
     or pg_catalog.jsonb_typeof(sb->'groups') is distinct from 'array' or pg_catalog.jsonb_array_length(sb->'groups')<>pg_catalog.json_array_length(population)
     or pg_catalog.jsonb_typeof(sb->'batches') is distinct from 'array' or pg_catalog.jsonb_array_length(sb->'batches')>2000
     or sb->>'seal_id' is distinct from selflearn_private.digest_json_v1(seal,'seal_id') then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  new.seal_id:=sb->>'seal_id';
  for g in select value from pg_catalog.json_array_elements(seal->'groups') loop
    if (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(g) key) is distinct from
       array['batch_ordinal','fact','fact_id','generation_object_ids','group_id','membership_id','reason_codes','receipt_ids','scope','source_receipt_ids','status']::text[]
       or pg_catalog.json_typeof(g->'scope') is distinct from 'array' or pg_catalog.json_array_length(g->'scope')<>5
       or exists(select 1 from pg_catalog.json_array_elements(g->'scope') with ordinality s(value,n) where s.n>1
         and (pg_catalog.json_typeof(value) is distinct from 'string' or pg_catalog.length(value#>>'{}') not between 1 and 200))
       or g->>'group_id'<=previous then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
    previous:=g->>'group_id';
    select value into base from pg_catalog.json_array_elements(population) where value->>'group_id'=g->>'group_id';
    if base is null or exists(select 1 from pg_catalog.json_each(base) e where
       selflearn_private.canonical_json_v1(e.value) is distinct from selflearn_private.canonical_json_v1(g->e.key))
       or pg_catalog.json_typeof(g->'reason_codes') is distinct from 'array' or pg_catalog.json_array_length(g->'reason_codes')>100
       or exists(select 1 from pg_catalog.json_array_elements(g->'reason_codes') r where pg_catalog.json_typeof(r)<>'string' or not (r#>>'{}' ~ '^[a-z][a-z0-9_]{0,99}$'))
       or (select coalesce(pg_catalog.jsonb_agg(v order by v),'[]'::jsonb) from (select distinct value::jsonb v from pg_catalog.json_array_elements(g->'reason_codes')) reasons) is distinct from (g->'reason_codes')::jsonb then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
    if g->>'status'='withheld' then
      if pg_catalog.json_array_length(g->'reason_codes')=0 or (g->'batch_ordinal')::jsonb is distinct from 'null'::jsonb
         or (g->'fact')::jsonb is distinct from 'null'::jsonb or (g->'fact_id')::jsonb is distinct from 'null'::jsonb then
        raise sqlstate 'PT422' using message='invalid_capture_application';
      end if;
    elsif g->>'status'='planned' then
      if pg_catalog.json_array_length(g->'reason_codes')<>0 or pg_catalog.json_typeof(g->'batch_ordinal') is distinct from 'number'
         or not (g->>'batch_ordinal' ~ '^(0|[1-9][0-9]{0,3})$') or (g->>'batch_ordinal')::integer<previous_ordinal
         or pg_catalog.json_array_length(g->'receipt_ids')>least((seal->'limits'->>'members')::integer,(seal->'limits'->>'observations')::integer) then
        raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      previous_ordinal:=(g->>'batch_ordinal')::integer;
      perform selflearn_private.capture_validate_fact_v2(capture,g,members,certificate.source_index);
    else raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  end loop;
  for assignment in select value from pg_catalog.json_array_elements(seal->'batches') loop
    expected_step:=selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_step.v2',
      'application_id',new.application_id,'ordinal',ordinal));
    select coalesce(pg_catalog.jsonb_agg(value->>'group_id' order by value->>'group_id'),'[]'::jsonb),
      coalesce(pg_catalog.sum(pg_catalog.json_array_length(value->'receipt_ids')),0) into group_ids,n
      from pg_catalog.json_array_elements(seal->'groups') where value->>'status'='planned' and value->>'batch_ordinal'=ordinal::text;
    if (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(assignment) key) is distinct from array['group_ids','member_count','ordinal','step_id']::text[]
       or pg_catalog.json_typeof(assignment->'ordinal') is distinct from 'number' or assignment->>'ordinal' is distinct from ordinal::text
       or assignment->>'step_id' is distinct from expected_step or (assignment->'group_ids')::jsonb is distinct from group_ids
       or pg_catalog.json_typeof(assignment->'member_count') is distinct from 'number' or assignment->>'member_count' is distinct from n::text
       or n<1 or n>least((seal->'limits'->>'members')::integer,(seal->'limits'->>'observations')::integer) then
      raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
    ordinal:=ordinal+1;
  end loop;
  if exists(select 1 from pg_catalog.json_array_elements(seal->'groups') where value->>'status'='planned'
      and (value->>'batch_ordinal')::integer>=ordinal) then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  new.application_seq:=pg_catalog.nextval('selflearn_private.capture_application_sequence_v2'::regclass);
  new.registered_utc:=pg_catalog.clock_timestamp();
  answer:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_registration.v2','application_id',new.application_id,
    'request_id',new.request_id,'capture_intent_id',new.capture_intent_id,'capture_id',new.capture_id,'seal_id',new.seal_id,
    'membership_id',new.membership_id,'source_vector_id',new.source_vector_id,'status','registered',
    'group_count',pg_catalog.json_array_length(population),'member_count',(select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members)),
    'batch_count',ordinal,'next_step_id',seal->'batches'->0->'step_id','source_watermark',new.expected_watermark,
    'application_seq',new.application_seq::text,'recorded_utc',pg_catalog.to_char(new.registered_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'scope','sealed_application_only','durable',true,'learning_accepted',false,'source_authentication','not_performed');
  new.registration_json:=selflearn_private.canonical_json_v1(answer);
  new.registration_json:=selflearn_private.canonical_json_v1((new.registration_json::jsonb||pg_catalog.jsonb_build_object(
    'registration_id',selflearn_private.digest_json_v1(answer)))::json);
  record:=pg_catalog.json_build_object('request',request,'seal_header',sb-'groups'-'batches','registration',new.registration_json::json);
  if pg_catalog.octet_length(pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_page.v2','found',true,
      'application_id',new.application_id,'view','registration','ledger_watermark','9223372036854775807',
      'registration_id',new.registration_json::json->'registration_id','seal_id',new.seal_id,'records',pg_catalog.json_build_array(record),
      'total_records',1,'next_after',null)::text)>3145728 then
    raise sqlstate 'PT422' using message='capture_application_registration_record_exceeds_byte_limit';
  end if;
  return new;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
create trigger application_insert before insert on selflearn_private.capture_applications_v2
  for each row execute function selflearn_private.guard_capture_application_v2();

create function public.selflearn_journal_register_capture_application_v2(p_request_json text,p_seal_json text,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare request json; prior selflearn_private.capture_applications_v2%rowtype; answer text;
begin
  perform selflearn_private.capture_lock_v2();
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_request_json',p_request_json,'p_seal_json',p_seal_json,'p_watermark',p_watermark)::text)>8388608 then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  request:=selflearn_private.require_canonical_v1(p_request_json,3145728);
  select * into prior from selflearn_private.capture_applications_v2 where application_id=request->>'application_id';
  if found then
    if prior.request_json is distinct from p_request_json then raise sqlstate 'PT409' using message='capture_application_request_conflict'; end if;
    return prior.registration_json::json;
  end if;
  insert into selflearn_private.capture_applications_v2(request_json,seal_json,expected_watermark)
    values(p_request_json,p_seal_json,p_watermark) returning registration_json into answer;
  return answer::json;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
revoke execute on function public.selflearn_journal_register_capture_application_v2(text,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_register_capture_application_v2(text,text,text) to service_role;

create function selflearn_private.guard_capture_application_batch_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare app selflearn_private.capture_applications_v2%rowtype; seal json; assignment json; groups json; batch json; g json; sealed json;
  certificate selflearn_private.capture_membership_cache_v2%rowtype;
  population json; current_membership text; members jsonb; results json[]:=array[]::json[]; ack json; answer json;
  expected jsonb; seen_observations text[]:=array[]::text[]; seen_revisions text[]:=array[]::text[];
  ids jsonb; proof json; k text; rid text; previous text:=''; status text:='committed'; current_fence text;
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
  certificate:=selflearn_private.capture_get_membership_v2((seal->>'season')::integer,seal->'weeks');
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
    batch:=selflearn_private.require_canonical_v1(new.batch_json,8388608);
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
        from pg_catalog.json_array_elements(batch->'observations') where ids ? (value->>'receipt_id');
      if expected is distinct from (g->'observation_ids')::jsonb then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      select coalesce(pg_catalog.jsonb_agg(value->>'revision_id' order by value->>'revision_id'),'[]'::jsonb) into expected
        from pg_catalog.json_array_elements(batch->'revisions') where ids ? (value->'resolution'->>'receipt_id');
      if expected is distinct from (g->'revision_ids')::jsonb then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
      if g->>'status'='withheld' then
        if pg_catalog.json_array_length(g->'observation_ids')<>0 or pg_catalog.json_array_length(g->'revision_ids')<>0 then
          raise sqlstate 'PT422' using message='invalid_capture_application';
        end if;
      elsif pg_catalog.json_array_length(g->'observation_ids')<>pg_catalog.json_array_length(g->'receipt_ids')
         or pg_catalog.json_array_length(g->'revision_ids')>pg_catalog.json_array_length(g->'receipt_ids')
         or (select pg_catalog.count(distinct value->>'receipt_id') from pg_catalog.json_array_elements(batch->'observations')
             where ids ? (value->>'receipt_id'))<>pg_catalog.json_array_length(g->'receipt_ids') then
        raise sqlstate 'PT422' using message='invalid_capture_application';
      end if;
      for proof in select value from pg_catalog.json_array_elements(batch->'observations') where ids ? (value->>'receipt_id') loop
        if selflearn_private.canonical_json_v1(selflearn_private.capture_fact_v2(proof)) is distinct from selflearn_private.canonical_json_v1(sealed->'fact') then
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
    ack:=public.selflearn_journal_commit_observed_v1(new.batch_json,new.expected_watermark);
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
create trigger application_batch_insert before insert on selflearn_private.capture_application_batches_v2
  for each row execute function selflearn_private.guard_capture_application_batch_v2();

create function public.selflearn_journal_commit_capture_application_batch_v2(p_application_id text,p_step_id text,
  p_group_results_json text,p_batch_json text,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare prior selflearn_private.capture_application_batches_v2%rowtype; answer text;
begin
  perform selflearn_private.capture_lock_v2();
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_application_id',p_application_id,'p_step_id',p_step_id,
      'p_group_results_json',p_group_results_json,'p_batch_json',p_batch_json,'p_watermark',p_watermark)::text)>8388608 then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  select * into prior from selflearn_private.capture_application_batches_v2 where step_id=p_step_id;
  if found then
    if prior.application_id is distinct from p_application_id then raise sqlstate 'PT409' using message='capture_application_request_conflict'; end if;
    return prior.result_json::json;
  end if;
  insert into selflearn_private.capture_application_batches_v2(application_id,step_id,group_results_json,batch_json,expected_watermark)
    values(p_application_id,p_step_id,p_group_results_json,p_batch_json,p_watermark) returning result_json into answer;
  return answer::json;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
revoke execute on function public.selflearn_journal_commit_capture_application_batch_v2(text,text,text,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_commit_capture_application_batch_v2(text,text,text,text,text) to service_role;

create function selflearn_private.guard_capture_application_result_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare app selflearn_private.capture_applications_v2%rowtype; seal json; population json; members jsonb; membership text;
  certificate selflearn_private.capture_membership_cache_v2%rowtype;
  views json; view_row json; answer json; reasons json; reason text; status text; complete boolean;
  groups integer:=0; total integer:=0; accepted_groups integer:=0; accepted_members integer:=0;
  not_applied_groups integer:=0; not_applied_members integer:=0; committed integer; batches integer;
begin
  perform selflearn_private.capture_lock_v2();
  select * into app from selflearn_private.capture_applications_v2 where application_id=new.application_id;
  if not found or new.stop_reason is null or new.stop_reason not in ('membership_changed','source_integrity_failed','runtime_capacity_exceeded','complete') then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  seal:=app.seal_json::json;
  certificate:=selflearn_private.capture_get_membership_v2((seal->>'season')::integer,seal->'weeks');
  members:=certificate.members; population:=certificate.groups::json; membership:=certificate.membership_id;
  if membership is distinct from app.membership_id then
    new.stop_reason:='membership_changed'; new.expected_watermark:=selflearn_private.observed_watermark_v1()::text;
  else
    if new.stop_reason='membership_changed' then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
    perform selflearn_private.capture_fence_v2(new.expected_watermark);
  end if;
  batches:=pg_catalog.json_array_length(seal->'batches');
  select pg_catalog.count(*) into committed from selflearn_private.capture_application_batches_v2
    where application_id=new.application_id and result_json::json->>'status'='committed';
  if new.stop_reason='complete' and committed<>batches then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  complete:=new.stop_reason='complete';
  reason:=case new.stop_reason when 'membership_changed' then 'application_membership_changed'
    when 'source_integrity_failed' then 'source_integrity_failed' when 'runtime_capacity_exceeded' then 'runtime_capacity_exceeded' else null end;
  reasons:=case when reason is null then '[]'::json else pg_catalog.json_build_array(reason) end;
  views:=selflearn_private.capture_group_views_v2(new.application_id,seal,selflearn_private.capture_ledger_watermark_v2(),reason);
  for view_row in select value from pg_catalog.json_array_elements(views) loop
    groups:=groups+1; total:=total+pg_catalog.json_array_length(view_row->'sealed_group'->'receipt_ids');
    if view_row->'result'->>'status'='accepted' then
      accepted_groups:=accepted_groups+1; accepted_members:=accepted_members+pg_catalog.json_array_length(view_row->'sealed_group'->'receipt_ids');
    end if;
    if view_row->>'execution_state'='not_applied' then
      not_applied_groups:=not_applied_groups+1; not_applied_members:=not_applied_members+pg_catalog.json_array_length(view_row->'sealed_group'->'receipt_ids');
    end if;
    if complete and view_row->>'execution_state'='pending' then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  end loop;
  status:=case when not complete then case when new.stop_reason='membership_changed' then 'incomplete_membership_changed' else 'incomplete_integrity_or_capacity' end
    when groups=0 then 'no_supported_membership' when accepted_groups=groups then 'accepted'
    when accepted_groups>0 then 'partial' else 'withheld' end;
  new.application_seq:=pg_catalog.nextval('selflearn_private.capture_application_sequence_v2'::regclass); new.recorded_utc:=pg_catalog.clock_timestamp();
  answer:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_result.v2','application_id',app.application_id,
    'request_id',app.request_id,'capture_intent_id',app.capture_intent_id,'capture_id',app.capture_id,'seal_id',app.seal_id,
    'membership_id',app.membership_id,'source_vector_id',app.source_vector_id,'status',status,'reason_codes',reasons,
    'current_membership_id',membership,'group_report_id',selflearn_private.digest_json_v1(views),
    'counts',pg_catalog.json_build_object('groups',groups,'members',total,'accepted_groups',accepted_groups,'accepted_members',accepted_members,
      'withheld_groups',groups-accepted_groups,'withheld_members',total-accepted_members,'not_applied_groups',not_applied_groups,
      'not_applied_members',not_applied_members,'batches',batches,'committed_batches',committed),
    'registered_watermark',seal->'source_watermark','watermark',selflearn_private.observed_watermark_v1()::text,
    'application_seq',new.application_seq::text,'recorded_utc',pg_catalog.to_char(new.recorded_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    'processing_complete',complete,'complete_success',status='accepted','learning_accepted',accepted_groups>0,
    'durable',true,'scope','application_group_results','source_authentication','not_performed');
  new.terminal_result_id:=selflearn_private.digest_json_v1(answer);
  new.result_json:=selflearn_private.canonical_json_v1((answer::jsonb||pg_catalog.jsonb_build_object('terminal_result_id',new.terminal_result_id))::json);
  if pg_catalog.octet_length(new.result_json)>3000000 then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  return new;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
create trigger application_result_insert before insert on selflearn_private.capture_application_results_v2
  for each row execute function selflearn_private.guard_capture_application_result_v2();

create function selflearn_private.finish_capture_application_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare seal json; reason text;
begin
  perform selflearn_private.capture_lock_v2();
  if tg_table_name='capture_applications_v2' then
    seal:=new.seal_json::json;
    if pg_catalog.json_array_length(seal->'batches')=0 then reason:='complete'; end if;
  else
    select seal_json::json into seal from selflearn_private.capture_applications_v2 where application_id=new.application_id;
    if new.result_json::json->>'status'='not_applied' then reason:='membership_changed';
    elsif new.ordinal=pg_catalog.json_array_length(seal->'batches')-1 then reason:='complete'; end if;
  end if;
  if reason is not null then
    insert into selflearn_private.capture_application_results_v2(application_id,stop_reason,expected_watermark)
      values(new.application_id,reason,selflearn_private.observed_watermark_v1()::text);
  end if;
  return null;
end $$;
create trigger application_finish after insert on selflearn_private.capture_applications_v2
  for each row execute function selflearn_private.finish_capture_application_v2();
create trigger application_batch_finish after insert on selflearn_private.capture_application_batches_v2
  for each row execute function selflearn_private.finish_capture_application_v2();

create function public.selflearn_journal_stop_capture_application_v2(p_application_id text,p_reason text,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare answer text;
begin
  perform selflearn_private.capture_lock_v2();
  select result_json into answer from selflearn_private.capture_application_results_v2 where application_id=p_application_id;
  if found then return answer::json; end if;
  insert into selflearn_private.capture_application_results_v2(application_id,stop_reason,expected_watermark)
    values(p_application_id,p_reason,p_watermark) returning result_json into answer;
  return answer::json;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application';
end $$;
revoke execute on function public.selflearn_journal_stop_capture_application_v2(text,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_stop_capture_application_v2(text,text,text) to service_role;

create function public.selflearn_journal_read_capture_application_v2(p_application_id text,p_view text,p_key text,
  p_after text,p_limit integer,p_ledger_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare app selflearn_private.capture_applications_v2%rowtype; seal json; terminal json; terminal_reason text;
  ceiling bigint; current_ceiling bigint; records json[]:=array[]::json[]; all_records json; item json; result json;
  answer json; total integer:=0; start_at integer:=0; position integer:=0; next_after text; committed integer; next_step json;
  registration_id text; is_list boolean;
begin
  if p_application_id is null or p_application_id !~ '^sha256:[0-9a-f]{64}$' or p_view is null
     or p_view not in ('registration','status','groups','batches','batch','terminal') or p_limit is null or p_limit not between 1 and 100 then
    raise sqlstate 'PT422' using message='invalid_capture_application_page';
  end if;
  is_list:=p_view in ('groups','batches');
  if (not is_list and (p_limit<>1 or p_after is not null)) or (p_view='batch' and (p_key is null or p_key !~ '^sha256:[0-9a-f]{64}$'))
     or (p_view<>'batch' and p_key is not null) then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
  current_ceiling:=selflearn_private.capture_ledger_watermark_v2();
  if p_ledger_watermark is not null and p_ledger_watermark !~ '^(0|[1-9][0-9]{0,18})$' then
    raise sqlstate 'PT422' using message='invalid_capture_application_page';
  end if;
  ceiling:=coalesce(p_ledger_watermark::bigint,current_ceiling);
  if ceiling>current_ceiling then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
  select * into app from selflearn_private.capture_applications_v2 where application_id=p_application_id;
  if not found then
    return pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_page.v2','found',false,'application_id',p_application_id,
      'view',p_view,'ledger_watermark',null,'registration_id',null,'seal_id',null,'records','[]'::json,'total_records',0,'next_after',null);
  end if;
  if ceiling<app.application_seq then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
  seal:=app.seal_json::json; registration_id:=app.registration_json::json->>'registration_id';
  select result_json::json,case stop_reason when 'membership_changed' then 'application_membership_changed'
    when 'source_integrity_failed' then 'source_integrity_failed' when 'runtime_capacity_exceeded' then 'runtime_capacity_exceeded' else null end
    into terminal,terminal_reason from selflearn_private.capture_application_results_v2
    where application_id=p_application_id and application_seq<=ceiling;
  if p_view='registration' then
    records:=array[pg_catalog.json_build_object('request',app.request_json::json,'seal_header',seal::jsonb-'groups'-'batches',
      'registration',app.registration_json::json)]; total:=1;
  elsif p_view='terminal' then
    if terminal is not null then records:=array[terminal]; total:=1; end if;
  elsif p_view='status' then
    select pg_catalog.count(*) into committed from selflearn_private.capture_application_batches_v2
      where application_id=p_application_id and application_seq<=ceiling and result_json::json->>'status'='committed';
    if terminal is null then
      select value->'step_id' into next_step from pg_catalog.json_array_elements(seal->'batches') a(value)
        where not exists(select 1 from selflearn_private.capture_application_batches_v2 b
          where b.application_id=p_application_id and b.ordinal=(a.value->>'ordinal')::integer and b.application_seq<=ceiling)
        order by (value->>'ordinal')::integer limit 1;
    end if;
    records:=array[pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_status.v2','application_id',p_application_id,
      'registration_id',registration_id,'seal_id',app.seal_id,'membership_id',app.membership_id,
      'status',case when terminal is null then 'active' else 'terminal' end,'batch_count',pg_catalog.json_array_length(seal->'batches'),
      'committed_batches',committed,'next_step_id',next_step,'terminal_result_id',terminal->'terminal_result_id','ledger_watermark',ceiling::text,
      'scope','pinned_application_progress','durable_ack',false,'execution_ready',terminal is null,'source_authentication','not_performed')]; total:=1;
  elsif p_view='batch' then
    if not exists(select 1 from pg_catalog.json_array_elements(seal->'batches') where value->>'step_id'=p_key) then
      raise sqlstate 'PT422' using message='invalid_capture_application_page';
    end if;
    select result_json::json into result from selflearn_private.capture_application_batches_v2
      where application_id=p_application_id and step_id=p_key and application_seq<=ceiling;
    if found then records:=array[result]; total:=1; end if;
  else
    if p_view='groups' then
      all_records:=selflearn_private.capture_group_views_v2(p_application_id,seal,ceiling,terminal_reason);
      if p_after is not null and p_after !~ '^sha256:[0-9a-f]{64}$' then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
    else
      all_records:='[]'::json; records:=array[]::json[];
      for item in select value from pg_catalog.json_array_elements(seal->'batches') order by (value->>'ordinal')::integer loop
        select result_json::json into result from selflearn_private.capture_application_batches_v2
          where application_id=p_application_id and ordinal=(item->>'ordinal')::integer and application_seq<=ceiling;
        records:=pg_catalog.array_append(records,pg_catalog.json_build_object('assignment',item,'result',result));
      end loop;
      all_records:=pg_catalog.to_json(records); records:=array[]::json[];
      if p_after is not null and p_after !~ '^(0|[1-9][0-9]{0,3})$' then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
    end if;
    total:=pg_catalog.json_array_length(all_records);
    if p_after is not null then
      select n::integer into start_at from pg_catalog.json_array_elements(all_records) with ordinality a(v,n)
        where case when p_view='groups' then v->'sealed_group'->>'group_id' else v->'assignment'->>'ordinal' end=p_after;
      if start_at is null or start_at>=total then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
    end if;
    for item in select value from pg_catalog.json_array_elements(all_records) with ordinality a(value,n)
       where n>start_at and n<=start_at+p_limit order by n loop records:=pg_catalog.array_append(records,item); end loop;
  end if;
  loop
    next_after:=null;
    if is_list and start_at+pg_catalog.cardinality(records)<total and pg_catalog.cardinality(records)>0 then
      item:=records[pg_catalog.cardinality(records)];
      next_after:=case when p_view='groups' then item->'sealed_group'->>'group_id' else item->'assignment'->>'ordinal' end;
    end if;
    answer:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_page.v2','found',true,'application_id',p_application_id,
      'view',p_view,'ledger_watermark',ceiling::text,'registration_id',registration_id,'seal_id',app.seal_id,
      'records',pg_catalog.to_json(records),'total_records',total,'next_after',next_after);
    if pg_catalog.octet_length(answer::text)<=3145728 then return answer; end if;
    if not is_list or pg_catalog.cardinality(records)<=1 then raise sqlstate 'PT422' using message='invalid_capture_application_page'; end if;
    records:=records[1:pg_catalog.cardinality(records)-1];
  end loop;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_application_page';
end $$;
revoke execute on function public.selflearn_journal_read_capture_application_v2(text,text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_read_capture_application_v2(text,text,text,text,integer,text) to service_role;

-- Revoke PUBLIC's function default only for this migration's named helpers.
do $$ declare f record; begin
  for f in select p.oid::regprocedure identity from pg_catalog.pg_proc p where p.pronamespace='selflearn_private'::regnamespace
    and p.proname=any(array['capture_members_v2','capture_request_v2','capture_source_vector_v2','capture_lock_v2','capture_fence_v2',
      'capture_ledger_watermark_v2','capture_groups_v2','capture_membership_id_v2','capture_held_group_v2','capture_fact_v2',
      'capture_group_views_v2','capture_v1_ownership_guard_v2','capture_components_v2','capture_validate_fact_v2',
      'capture_source_authority_v2','capture_source_index_v2','capture_membership_policy_v2','guard_capture_membership_cache_v2','capture_get_membership_v2',
      'guard_capture_application_v2','guard_capture_application_batch_v2','guard_capture_application_result_v2','finish_capture_application_v2',
      'capture_metadata_v2','capture_audit_v2']) loop
    execute pg_catalog.format('revoke execute on function %s from public,anon,authenticated',f.identity);
    execute pg_catalog.format('grant execute on function %s to service_role',f.identity);
  end loop;
end $$;
commit;
