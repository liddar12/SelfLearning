-- Add a season-wide roster observation to the existing NFL capture protocol.
-- Existing captures and original ACKs remain valid without roster evidence.
-- Replaced functions keep their signatures, ownership, and grants. No stored
-- outcome, capture, or receipt is rewritten by this migration.

create function selflearn_private.validate_roster_trace_v1(value jsonb,components jsonb) returns void
language plpgsql security invoker set search_path = '' as $$
declare
  trace jsonb := value->'producer_trace'; crosswalk jsonb := trace->'identity_crosswalk';
  roster jsonb := components->'nflverse.rosters'; player_id text := value->'measurement'->'canonical_contract'->>2;
begin
  if trace->>'identity_join_policy' is distinct from 'stable_player_ids_exact_fixture_v2' then
    if roster is not null or crosswalk is not null then
      raise sqlstate 'PT422' using message='invalid_observed_identity_crosswalk';
    end if;
    return;
  end if;
  if pg_catalog.jsonb_typeof(roster) is distinct from 'object'
     or pg_catalog.jsonb_typeof(crosswalk) is distinct from 'object'
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(crosswalk) key)
        is distinct from array['gsis_id','observation_id','observed_utc','pfr_id','player_id','raw_sha256','scope','source']::text[]
     or crosswalk->>'source' is distinct from 'nflverse.rosters'
     or crosswalk->>'scope' is distinct from 'postgame_identity_only'
     or pg_catalog.jsonb_typeof(crosswalk->'player_id') is distinct from 'string'
     or crosswalk->>'player_id' is distinct from player_id
     or crosswalk->'observation_id' is distinct from roster->'observation_id'
     or crosswalk->'observed_utc' is distinct from roster->'observed_utc'
     or pg_catalog.jsonb_typeof(crosswalk->'raw_sha256') is distinct from 'string'
     or not (crosswalk->>'raw_sha256' ~ '^sha256:[0-9a-f]{64}$')
     or pg_catalog.jsonb_typeof(crosswalk->'gsis_id') is distinct from 'string'
     or not (crosswalk->>'gsis_id' ~ '^00-[0-9]{7}$')
     or (player_id like '00-%' and crosswalk->>'gsis_id' is distinct from player_id)
     or (pg_catalog.jsonb_typeof(crosswalk->'pfr_id') not in ('null','string'))
     or (components ? 'nflverse.snap_counts' and crosswalk->'pfr_id' = 'null'::jsonb)
     or (pg_catalog.jsonb_typeof(crosswalk->'pfr_id') = 'string' and
         (pg_catalog.length(crosswalk->>'pfr_id') > 128
          or pg_catalog.btrim(crosswalk->>'pfr_id') is distinct from crosswalk->>'pfr_id'
          or pg_catalog.lower(crosswalk->>'pfr_id') in ('','na','nan','none','null'))) then
    raise sqlstate 'PT422' using message='invalid_observed_identity_crosswalk';
  end if;
end $$;
revoke execute on function selflearn_private.validate_roster_trace_v1(jsonb,jsonb) from public,anon,authenticated;
grant execute on function selflearn_private.validate_roster_trace_v1(jsonb,jsonb) to service_role;

create function selflearn_private.capture_roster_revisions_v1(envelope jsonb,batch jsonb) returns void
language plpgsql security invoker set search_path = '' as $$
declare roster_id text; raw_sha text; revision jsonb; value jsonb;
begin
  select ref->>'observation_id' into roster_id from pg_catalog.jsonb_array_elements(envelope->'manifest'->'sources') ref
    where ref->>'profile'='nflverse_rosters_v1';
  raw_sha := envelope->'observations'->roster_id->>'raw_sha256';
  for revision in select item from pg_catalog.jsonb_array_elements(batch->'revisions') item loop
    -- An exact existing revision is checked for immutable equality by commit_observed.
    if exists(select 1 from selflearn_private.journal_revisions r where r.revision_id=revision->>'revision_id') then
      continue;
    end if;
    value := revision->'resolution';
    if value->'source_observations'->'nflverse.rosters'->>'observation_id' is distinct from roster_id
       or value->'producer_trace'->'identity_crosswalk'->>'raw_sha256' is distinct from raw_sha then
      raise sqlstate 'PT422' using message='capture_roster_revision_provenance_mismatch';
    end if;
  end loop;
end $$;
revoke execute on function selflearn_private.capture_roster_revisions_v1(jsonb,jsonb) from public,anon,authenticated;
grant execute on function selflearn_private.capture_roster_revisions_v1(jsonb,jsonb) to service_role;

create or replace function selflearn_private.validate_observation_v1(payload text) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  parsed json := selflearn_private.require_canonical_v1(payload,262144);
  doc jsonb := parsed::jsonb; measurement jsonb := doc->'measurement';
  components jsonb := doc->'source_observations'; contract jsonb := measurement->'canonical_contract';
  game jsonb := measurement->'game'; forecast jsonb; source text; component jsonb;
  primary_source text; stamp timestamptz; game_stamp timestamptz; maximum_stamp timestamptz;
  started timestamptz; k text; primary_required text;
begin
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(doc) key)
       is distinct from array['contract_id','event_id','evidence_basis','evidence_observation_id','kind',
         'measurement','measurement_id','observation_id','observed_utc','receipt_id','source_observations','version']::text[]
     or doc->>'version' is distinct from '1' or pg_catalog.jsonb_typeof(doc->'version') <> 'number'
     or doc->>'kind' is distinct from 'nfl.observed_measurement.v1'
     or doc->>'evidence_basis' is distinct from 'nfl.observed_resolution_v2' then
    raise sqlstate 'PT422' using message='invalid_observation_contract';
  end if;
  foreach k in array array['observation_id','receipt_id','contract_id','measurement_id','evidence_observation_id'] loop
    if pg_catalog.jsonb_typeof(doc->k) is distinct from 'string'
       or not (doc->>k ~ '^sha256:[0-9a-f]{64}$') then
      raise sqlstate 'PT422' using message='invalid_observation_digest';
    end if;
  end loop;
  select payload_json::jsonb into forecast from selflearn_private.journal_receipts
    where receipt_id=doc->>'receipt_id';
  if not found or forecast->>'domain' <> 'nfl' or forecast->>'task' <> 'nfl.parlay_leg'
     or doc->>'contract_id' is distinct from forecast->>'contract_id'
     or doc->>'event_id' is distinct from forecast->>'event_id' then
    raise sqlstate 'PT422' using message='observation_receipt_identity_mismatch';
  end if;
  started := (forecast->>'event_start_utc')::timestamptz;
  if pg_catalog.jsonb_typeof(measurement) is distinct from 'object'
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(measurement) key)
        is distinct from array['actual','canonical_contract','game','hit','participation_basis','played','reason','status','version','week']::text[]
     or measurement->>'version' is distinct from '1'
     or pg_catalog.jsonb_typeof(measurement->'version') is distinct from 'number'
     or pg_catalog.jsonb_typeof(contract) is distinct from 'array' or pg_catalog.jsonb_array_length(contract) <> 8
     or pg_catalog.jsonb_typeof(contract->0) is distinct from 'number'
     or not (contract->>0 ~ '^[0-9]+$')
     or pg_catalog.jsonb_typeof(contract->1) is distinct from 'string' or pg_catalog.btrim(contract->>1)=''
     or pg_catalog.jsonb_typeof(contract->2) is distinct from 'string' or pg_catalog.btrim(contract->>2)=''
     or pg_catalog.jsonb_typeof(contract->3) is distinct from 'string'
     or contract->>3 not in ('qb_pass_yds','rb_rush_yds','wr_rec_yds')
     or selflearn_private.digest_json_v1(parsed->'measurement'->'canonical_contract') is distinct from doc->>'contract_id'
     -- NFL canonical_key intentionally hashes format(line,'.12g') as TEXT.
     -- Do not coerce this identity token to a JSON number before hashing.
     or pg_catalog.jsonb_typeof(contract->4) is distinct from 'string'
     or contract->>4 is distinct from selflearn_private.nfl_line_token_v1(contract->>4)
     or pg_catalog.jsonb_typeof(contract->5) is distinct from 'string'
     or contract->>5 not in ('home','away') or contract->>6 is distinct from 'over'
     or contract->>7 is distinct from 'gte'
     or pg_catalog.jsonb_typeof(measurement->'week') is distinct from 'number'
     or not (measurement->>'week' ~ '^[1-9][0-9]?$') or (measurement->>'week')::integer not between 1 and 18
     or selflearn_private.digest_json_v1(parsed->'measurement') is distinct from doc->>'measurement_id'
     or selflearn_private.digest_json_v1(parsed,'observation_id') is distinct from doc->>'observation_id'
     or selflearn_private.digest_json_v1(pg_catalog.json_build_object('measurement_id',doc->>'measurement_id',
          'components',parsed->'source_observations')) is distinct from doc->>'evidence_observation_id' then
    raise sqlstate 'PT422' using message='invalid_observation_measurement';
  end if;
  if pg_catalog.jsonb_typeof(game) is distinct from 'object'
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(game) key)
        is distinct from array['away','game_id','home','kickoff_utc','season','state','week']::text[]
     or game->'season' is distinct from contract->0 or game->'game_id' is distinct from contract->1
     or pg_catalog.jsonb_typeof(game->'season') is distinct from 'number' or not (game->>'season' ~ '^[0-9]+$')
     or pg_catalog.jsonb_typeof(game->'week') is distinct from 'number' or not (game->>'week' ~ '^[0-9]+$')
     or game->'week' is distinct from measurement->'week' or game->>'state' is distinct from 'FINAL'
     or doc->>'event_id' is distinct from 'nfl:'||(game->>'season')||':game:'||(game->>'game_id')
     or pg_catalog.jsonb_typeof(game->'home') is distinct from 'string' or pg_catalog.btrim(game->>'home')=''
     or pg_catalog.jsonb_typeof(game->'away') is distinct from 'string' or pg_catalog.btrim(game->>'away')=''
     or game->>'home' = game->>'away'
     or not (game->>'kickoff_utc' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
     or (game->>'kickoff_utc')::timestamptz is distinct from started then
    raise sqlstate 'PT422' using message='observation_game_identity_mismatch';
  end if;
  if measurement->>'status'='FINAL' then
    primary_source := 'nflverse.stats_player';
    if measurement->'played' is distinct from 'true'::jsonb
       or pg_catalog.jsonb_typeof(measurement->'actual') is distinct from 'number'
       or (measurement->>'actual')::double precision in ('Infinity'::double precision,'-Infinity'::double precision,'NaN'::double precision)
       or pg_catalog.jsonb_typeof(measurement->'hit') is distinct from 'boolean'
       or (measurement->>'hit')::boolean is distinct from ((measurement->>'actual')::numeric >= (contract->>4)::numeric)
       or measurement->'reason' is distinct from 'null'::jsonb
       or pg_catalog.jsonb_typeof(measurement->'participation_basis') is distinct from 'string'
       or measurement->>'participation_basis' not in ('stat_row','r106_positive_offensive_snaps_no_stat_row') then
      raise sqlstate 'PT422' using message='invalid_observation_grade';
    end if;
    if measurement->>'participation_basis'='r106_positive_offensive_snaps_no_stat_row'
       and ((measurement->>'actual')::numeric <> 0 or not (components ? 'nflverse.snap_counts')) then
      raise sqlstate 'PT422' using message='missing_observation_participation';
    end if;
  elsif measurement->>'status'='VOID' then
    primary_source := 'nflverse.snap_counts';
    if measurement->'played' is distinct from 'false'::jsonb or measurement->'actual' is distinct from 'null'::jsonb
       or measurement->'hit' is distinct from 'null'::jsonb or measurement->>'reason' is distinct from 'did_not_play'
       or measurement->>'participation_basis' is distinct from 'r106_team_snap_absence_inference' then
      raise sqlstate 'PT422' using message='invalid_observation_void';
    end if;
  else raise sqlstate 'PT422' using message='invalid_observation_status'; end if;
  if pg_catalog.jsonb_typeof(components) is distinct from 'object' or not (components ? 'espn.scoreboard')
     or not (components ? primary_source) then
    raise sqlstate 'PT422' using message='missing_observation_components';
  end if;
  maximum_stamp := started;
  for source,component in select key,value from pg_catalog.jsonb_each(components) loop
    if source not in ('espn.scoreboard','nflverse.stats_player','nflverse.snap_counts','nflverse.rosters')
       or pg_catalog.jsonb_typeof(component) is distinct from 'object'
       or (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(component) key)
          is distinct from array['observation_id','observed_utc','source']::text[]
       or component->>'source' is distinct from source
       or pg_catalog.jsonb_typeof(component->'observation_id') is distinct from 'string'
       or not (component->>'observation_id' ~ '^sha256:[0-9a-f]{64}$')
       or pg_catalog.jsonb_typeof(component->'observed_utc') is distinct from 'string'
       or not (component->>'observed_utc' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$') then
      raise sqlstate 'PT422' using message='invalid_source_observation_component';
    end if;
    stamp := (component->>'observed_utc')::timestamptz;
    if stamp < started or stamp > pg_catalog.clock_timestamp() then
      raise sqlstate 'PT422' using message='invalid_source_observation_clock';
    end if;
    maximum_stamp := greatest(maximum_stamp,stamp);
  end loop;
  game_stamp := (components->'espn.scoreboard'->>'observed_utc')::timestamptz;
  if exists(select 1 from pg_catalog.jsonb_each(components) c
            where c.key <> 'espn.scoreboard' and (c.value->>'observed_utc')::timestamptz < game_stamp)
     or pg_catalog.jsonb_typeof(doc->'observed_utc') is distinct from 'string'
     or not (doc->>'observed_utc' ~ '(Z|[+-][0-9]{2}:[0-9]{2})$')
     or (doc->>'observed_utc')::timestamptz is distinct from maximum_stamp then
    raise sqlstate 'PT422' using message='observation_clock_mismatch';
  end if;
  return doc;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_observation_evidence';
end $$;

create or replace function selflearn_private.guard_observed_revision_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  doc json := selflearn_private.require_canonical_v1(new.payload_json,262144);
  value jsonb := doc::jsonb->'resolution'; proof jsonb; proof_json json;
  source text; component jsonb; maximum_stamp timestamptz; expected text; k text;
  previous_head jsonb; chain_watermarks jsonb;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  if value->>'evidence_basis' is distinct from 'nfl.observed_resolution_v2' then
    if exists(select 1 from selflearn_private.journal_observations where receipt_id=new.receipt_id)
       or exists(select 1 from selflearn_private.journal_revisions r where r.receipt_id=new.receipt_id
                 and r.payload_json::jsonb->'resolution'->>'evidence_basis'='nfl.observed_resolution_v2') then
      raise sqlstate 'PT409' using message='observed_receipt_cannot_downgrade_to_legacy';
    end if;
    return new;
  end if;
  if exists(select 1 from selflearn_private.journal_revisions r where r.receipt_id=new.receipt_id
            and r.payload_json::jsonb->'resolution'->>'evidence_basis' is distinct from 'nfl.observed_resolution_v2') then
    raise sqlstate 'PT409' using message='incompatible_existing_observed_history';
  end if;
  select payload_json::jsonb->'resolution' into previous_head from selflearn_private.journal_revisions
    where receipt_id=new.receipt_id order by journal_seq desc limit 1;
  if previous_head is not null and value->>'measurement_id'=previous_head->>'measurement_id' then
    raise sqlstate 'PT409' using message='unchanged_measurement_requires_original_revision';
  end if;
  if previous_head is not null
     and (value->>'resolved_utc')::timestamptz <= (previous_head->>'resolved_utc')::timestamptz then
    raise sqlstate 'PT409' using message='stale_conflicting_observation';
  end if;
  select payload_json::json,payload_json::jsonb into proof_json,proof
    from selflearn_private.journal_observations
    where receipt_id=new.receipt_id and evidence_observation_id=value->>'evidence_observation_id';
  if not found then raise sqlstate 'PT422' using message='missing_indexed_observation_proof'; end if;
  foreach k in array array['receipt_id','contract_id','event_id','measurement_id','evidence_observation_id'] loop
    if value->>k is distinct from proof->>k then
      raise sqlstate 'PT422' using message='observed_revision_proof_identity_mismatch';
    end if;
  end loop;
  if selflearn_private.canonical_json_v1(doc->'resolution'->'measurement') is distinct from
       selflearn_private.canonical_json_v1(proof_json->'measurement')
     or value->'source_observations' is distinct from proof->'source_observations'
     or value->>'resolved_utc' is distinct from proof->>'observed_utc'
     or value->>'evidence_observed_utc' is distinct from proof->>'observed_utc'
     or value->'status' is distinct from proof->'measurement'->'status'
     or value->'actual' is distinct from proof->'measurement'->'actual'
     or value->'hit' is distinct from proof->'measurement'->'hit'
     or value->'reason' is distinct from proof->'measurement'->'reason'
     or value->'participation_basis' is distinct from proof->'measurement'->'participation_basis'
     or value->>'source' is distinct from (case when proof->'measurement'->>'status'='VOID'
                                              then 'nflverse.snap_counts' else 'nflverse.stats_player' end)
     or pg_catalog.jsonb_typeof(value->'observation_watermarks') is distinct from 'object' then
    raise sqlstate 'PT422' using message='observed_revision_proof_mismatch';
  end if;
  perform selflearn_private.validate_roster_trace_v1(value,proof->'source_observations');
  expected := 'nfl.observed_resolution_v2:'||selflearn_private.digest_json_v1(pg_catalog.json_build_object(
    'receipt_id',new.receipt_id,'observation_id',proof->>'evidence_observation_id','supersedes',doc->'supersedes'));
  if new.revision_id is distinct from expected then raise sqlstate 'PT422' using message='observed_revision_id_mismatch'; end if;
  if exists(select 1 from selflearn_private.journal_revisions r where r.receipt_id=new.receipt_id
            and r.payload_json::jsonb->'resolution'->>'evidence_basis'='nfl.observed_resolution_v2'
            and r.payload_json::jsonb->'resolution'->>'evidence_observation_id'=proof->>'evidence_observation_id') then
    raise sqlstate 'PT409' using message='observation_already_has_revision';
  end if;
  -- This field is the frozen OUTCOME-CHAIN union, not the independent maximum
  -- over unchanged observation records. Absent sources retain the previous
  -- outcome's component; present sources still face every indexed observation.
  chain_watermarks := coalesce(previous_head->'observation_watermarks','{}'::jsonb)
                      || (proof->'source_observations');
  if value->'observation_watermarks' is distinct from chain_watermarks then
    raise sqlstate 'PT409' using message='invalid_outcome_chain_observation_fence';
  end if;
  for source,component in select c.key,c.value from pg_catalog.jsonb_each(proof->'source_observations') c loop
    select pg_catalog.max((o.payload_json::jsonb->'source_observations'->source->>'observed_utc')::timestamptz)
      into maximum_stamp from selflearn_private.journal_observations o where o.receipt_id=new.receipt_id;
    if component is null or (component->>'observed_utc')::timestamptz is distinct from maximum_stamp
       or not exists(select 1 from selflearn_private.journal_observations o where o.receipt_id=new.receipt_id
                     and o.payload_json::jsonb->'source_observations'->source=component) then
      raise sqlstate 'PT409' using message='stale_or_missing_observation_fence';
    end if;
  end loop;
  return new;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_observed_revision';
end $$;

create or replace function selflearn_private.guard_capture_delivery_v1() returns trigger
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
     or pg_catalog.jsonb_array_length(envelope->'raw_objects') not between 1 and 21
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
     or pg_catalog.jsonb_array_length(manifest->'sources') not between
          pg_catalog.jsonb_array_length(manifest->'weeks')+2 and pg_catalog.jsonb_array_length(manifest->'weeks')+3
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
    elsif ordinal=pg_catalog.jsonb_array_length(manifest->'weeks')+2 then
      profile := 'nflverse_snap_counts_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_'||(manifest->>'season')||'.csv';
    else
      profile := 'nflverse_rosters_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_'||(manifest->>'season')||'.csv';
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
    if profile='nflverse_rosters_v1' then
      perform selflearn_private.capture_audit_v2(request->'envelope'->'observations'->source_id->'audit',profile);
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
    if ((proof->'source_observations') ? 'nflverse.rosters') is distinct from
       (exists(select 1 from pg_catalog.jsonb_array_elements(manifest->'sources') r where r->>'profile'='nflverse_rosters_v1')) then
      raise sqlstate 'PT422' using message='capture_observation_request_provenance_mismatch';
    end if;
    for k,component in select key,value from pg_catalog.jsonb_each(proof->'source_observations') loop
      profile := case k when 'espn.scoreboard' then 'espn_scoreboard_v1' when 'nflverse.stats_player' then 'nflverse_stats_player_v1'
                    when 'nflverse.snap_counts' then 'nflverse_snap_counts_v1'
                    when 'nflverse.rosters' then 'nflverse_rosters_v1' else null end;
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
  perform selflearn_private.capture_roster_revisions_v1(envelope,batch);
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

create or replace function selflearn_private.capture_audit_v2(audit json,profile text) returns void
language plpgsql security invoker set search_path = '' as $$
declare keys text[]; expected text[]; k text; maximum integer; value json; count bigint;
begin
  if pg_catalog.json_typeof(audit) is distinct from 'object' then raise sqlstate 'PT422' using message='invalid_capture_application_request'; end if;
  select pg_catalog.array_agg(key order by key) into keys from pg_catalog.json_object_keys(audit) key;
  if profile in ('nflverse_stats_player_v1','nflverse_snap_counts_v1','nflverse_rosters_v1') then
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

create or replace function selflearn_private.capture_request_v2(payload text) returns json
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
     or pg_catalog.jsonb_array_length(envelope->'raw_objects') not between 1 and 21
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
     or pg_catalog.jsonb_array_length(manifest->'sources') not between
          pg_catalog.jsonb_array_length(manifest->'weeks')+2 and pg_catalog.jsonb_array_length(manifest->'weeks')+3
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
    elsif ordinal=pg_catalog.jsonb_array_length(manifest->'weeks')+2 then
      profile := 'nflverse_snap_counts_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_'||(manifest->>'season')||'.csv';
    else
      profile := 'nflverse_rosters_v1'; wanted_week := 'null'::jsonb;
      expected_url := 'https://github.com/nflverse/nflverse-data/releases/download/rosters/roster_'||(manifest->>'season')||'.csv';
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

create or replace function selflearn_private.capture_components_v2(request json,components json,week json) returns void
language plpgsql security invoker set search_path = '' as $$
declare k text; component json; profile text; ref json; observation json;
begin
  if pg_catalog.json_typeof(components) is distinct from 'object' then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  if (components::jsonb ? 'nflverse.rosters') is distinct from
     (exists(select 1 from pg_catalog.json_array_elements(request->'envelope'->'manifest'->'sources') r
              where r->>'profile'='nflverse_rosters_v1')) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  for k,component in select key,value from pg_catalog.json_each(components) loop
    profile:=case k when 'espn.scoreboard' then 'espn_scoreboard_v1' when 'nflverse.stats_player' then 'nflverse_stats_player_v1'
      when 'nflverse.snap_counts' then 'nflverse_snap_counts_v1'
      when 'nflverse.rosters' then 'nflverse_rosters_v1' else null end;
    select value into ref from pg_catalog.json_array_elements(request->'envelope'->'manifest'->'sources')
      where value->>'profile'=profile and (profile<>'espn_scoreboard_v1' or value->>'week' is not distinct from week#>>'{}');
    observation:=request->'envelope'->'observations'->(ref->>'observation_id');
    if profile is null or ref is null or component->>'observation_id' is distinct from ref->>'observation_id'
       or component->>'observed_utc' is distinct from observation->>'observed_utc' then
      raise sqlstate 'PT422' using message='invalid_capture_application';
    end if;
  end loop;
end $$;

create or replace function selflearn_private.guard_capture_application_batch_v2() returns trigger
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
    perform selflearn_private.capture_roster_revisions_v1(app.request_json::jsonb->'capture_request'->'envelope',batch::jsonb);
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
