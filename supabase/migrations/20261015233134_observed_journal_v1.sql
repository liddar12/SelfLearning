-- S009-D1: additive local-only observed proof index and atomic fence/outcome
-- journal. Not applied to any hosted database. Existing v1 RPCs remain intact.
begin;

create table selflearn_private.journal_observations (
  observation_id text primary key check (observation_id ~ '^sha256:[0-9a-f]{64}$'),
  receipt_id text not null references selflearn_private.journal_receipts(receipt_id),
  evidence_observation_id text not null check (evidence_observation_id ~ '^sha256:[0-9a-f]{64}$'),
  payload_json text not null,
  journal_seq bigint not null unique,
  stored_utc timestamptz not null,
  unique (receipt_id, evidence_observation_id)
);
create index journal_observations_receipt_seq on selflearn_private.journal_observations(receipt_id,journal_seq);
create index journal_revisions_receipt_seq on selflearn_private.journal_revisions(receipt_id,journal_seq desc);
create unique index journal_observed_revision_proof on selflearn_private.journal_revisions
  (receipt_id, ((payload_json::jsonb)->'resolution'->>'evidence_observation_id'))
  where payload_json::jsonb->'resolution'->>'evidence_basis' = 'nfl.observed_resolution_v2';
alter table selflearn_private.journal_observations enable row level security;
alter table selflearn_private.journal_observations force row level security;
revoke all on selflearn_private.journal_observations from public,anon,authenticated,service_role;
grant select on selflearn_private.journal_observations to service_role;
grant insert(observation_id,receipt_id,evidence_observation_id,payload_json)
  on selflearn_private.journal_observations to service_role;

create function selflearn_private.observed_watermark_v1() returns bigint
language sql stable security invoker set search_path = '' as $$
  select coalesce(pg_catalog.max(v),0) from (
    select pg_catalog.max(journal_seq) v from selflearn_private.journal_receipts union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_objects union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_generation_commits union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_revisions union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_observations
  ) committed
$$;

-- PostgreSQL17 float8 output at DBL_DIG(15)-3 uses the same 12-significant
-- digit general format as Python format(float(token),'.12g'). This setting is
-- LOCAL TO THE FUNCTION; canonical JSON hashing keeps its original profile.
create function selflearn_private.nfl_line_token_v1(value text) returns text
language plpgsql immutable strict security invoker set search_path = '' set extra_float_digits = -3 as $$
declare parsed double precision := value::double precision;
begin
  if parsed<=0 or parsed='Infinity'::double precision or parsed='NaN'::double precision then
    raise sqlstate 'PT422' using message='invalid_canonical_line_token';
  end if;
  return pg_catalog.float8out(parsed)::text;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_canonical_line_token';
end $$;

create function selflearn_private.validate_observation_v1(payload text) returns jsonb
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
    if source not in ('espn.scoreboard','nflverse.stats_player','nflverse.snap_counts')
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

create function selflearn_private.guard_observation_insert_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  doc jsonb; old jsonb; source text; component jsonb; previous jsonb;
  primary_source text; old_primary jsonb; old_clock timestamptz;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  doc := selflearn_private.validate_observation_v1(new.payload_json);
  if (new.observation_id,new.receipt_id,new.evidence_observation_id) is distinct from
     (doc->>'observation_id',doc->>'receipt_id',doc->>'evidence_observation_id') then
    raise sqlstate 'PT422' using message='observation_columns_mismatch';
  end if;
  primary_source := case when doc->'measurement'->>'status'='VOID'
                         then 'nflverse.snap_counts' else 'nflverse.stats_player' end;
  for old in select payload_json::jsonb from selflearn_private.journal_observations where receipt_id=new.receipt_id loop
    if selflearn_private.canonical_json_v1((new.payload_json::json)->'measurement'->'game') is distinct from
       selflearn_private.canonical_json_v1((old::text)::json->'measurement'->'game') then
      raise sqlstate 'PT409' using message='changed_observation_game_identity';
    end if;
    for source,component in select key,value from pg_catalog.jsonb_each(doc->'source_observations') loop
      previous := old->'source_observations'->source;
      if previous is null then continue; end if;
      if component->>'observation_id'=previous->>'observation_id'
         and component->>'observed_utc' is distinct from previous->>'observed_utc' then
        raise sqlstate 'PT409' using message='source_observation_clock_mutated';
      end if;
      if (component->>'observed_utc')::timestamptz < (previous->>'observed_utc')::timestamptz then
        raise sqlstate 'PT409' using message='stale_source_observation';
      end if;
      if source=primary_source and doc->>'measurement_id' is distinct from old->>'measurement_id'
         and (component->>'observation_id'=previous->>'observation_id'
              or (component->>'observed_utc')::timestamptz <= (previous->>'observed_utc')::timestamptz) then
        raise sqlstate 'PT409' using message='stale_conflicting_primary_observation';
      end if;
    end loop;
  end loop;
  return new;
end $$;
create trigger a_observation_validate before insert on selflearn_private.journal_observations
  for each row execute function selflearn_private.guard_observation_insert_v1();
create trigger journal_stamp before insert on selflearn_private.journal_observations
  for each row execute function selflearn_private.stamp_insert_v1();
create trigger journal_immutable before update or delete on selflearn_private.journal_observations
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger journal_no_truncate before truncate on selflearn_private.journal_observations
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function selflearn_private.guard_observed_revision_v1() returns trigger
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
create trigger a_observed_revision_proof before insert on selflearn_private.journal_revisions
  for each row execute function selflearn_private.guard_observed_revision_v1();

create function selflearn_private.observation_has_outcome_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  if not exists(select 1 from selflearn_private.journal_revisions r where r.receipt_id=new.receipt_id
       and r.payload_json::jsonb->'resolution'->>'evidence_basis'='nfl.observed_resolution_v2'
       and r.payload_json::jsonb->'resolution'->>'measurement_id'=new.payload_json::jsonb->>'measurement_id'
       and not exists(select 1 from selflearn_private.journal_revisions n
                      where n.receipt_id=r.receipt_id and n.journal_seq>r.journal_seq)) then
    raise sqlstate 'PT422' using message='observation_without_matching_outcome';
  end if;
  return null;
end $$;
create constraint trigger observation_requires_outcome after insert on selflearn_private.journal_observations
  deferrable initially deferred for each row execute function selflearn_private.observation_has_outcome_v1();

create function public.selflearn_journal_commit_observed_v1(p_batch_json text,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare
  doc json; item json; row jsonb; old text; ack json; expected bigint;
  added_observations integer := 0; added_revisions integer := 0;
begin
  perform pg_catalog.pg_advisory_xact_lock(619044,1);
  if p_watermark is null or p_watermark !~ '^(0|[1-9][0-9]{0,18})$' then
    raise sqlstate 'PT422' using message='invalid_observed_commit_watermark';
  end if;
  expected := p_watermark::bigint;
  if expected is distinct from selflearn_private.observed_watermark_v1() then
    raise sqlstate 'PT409' using message='stale_observed_commit_watermark';
  end if;
  doc := selflearn_private.require_canonical_v1(p_batch_json,8388608);
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(doc) key)
       is distinct from array['observations','revisions','version']::text[]
     or doc->>'version' is distinct from '1' or pg_catalog.json_typeof(doc->'version') <> 'number'
     or pg_catalog.json_typeof(doc->'observations') is distinct from 'array'
     or pg_catalog.json_typeof(doc->'revisions') is distinct from 'array'
     or pg_catalog.json_array_length(doc->'observations')>500 or pg_catalog.json_array_length(doc->'revisions')>500 then
    raise sqlstate 'PT422' using message='invalid_observed_commit_batch';
  end if;
  for item in select value from pg_catalog.json_array_elements(doc->'observations') loop
    row := selflearn_private.validate_observation_v1(item::text);
    select payload_json into old from selflearn_private.journal_observations
      where observation_id=row->>'observation_id' or
        (receipt_id=row->>'receipt_id' and evidence_observation_id=row->>'evidence_observation_id');
    if found then
      if old is distinct from item::text then raise sqlstate 'PT409' using message='observation_id_conflict'; end if;
    else
      insert into selflearn_private.journal_observations(observation_id,receipt_id,evidence_observation_id,payload_json)
        values(row->>'observation_id',row->>'receipt_id',row->>'evidence_observation_id',item::text);
      added_observations := added_observations+1;
    end if;
  end loop;
  for item in select value from pg_catalog.json_array_elements(doc->'revisions') loop
    if item->'resolution'->>'evidence_basis' is distinct from 'nfl.observed_resolution_v2' then
      raise sqlstate 'PT422' using message='observed_batch_requires_observed_revision';
    end if;
    ack := public.selflearn_journal_append_resolution_v1(item::text);
    if (ack->>'added')::boolean then added_revisions := added_revisions+1; end if;
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

create function public.selflearn_journal_read_observed_v1(p_view text,p_key text,p_after text,p_limit integer,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare
  ceiling bigint; legacy_ceiling bigint; after_seq bigint := 0; answer json;
  rows json; more boolean; next_id text;
begin
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
  ceiling := selflearn_private.observed_watermark_v1();
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
  if p_view in ('observations','observation_history') then
    if p_view='observation_history' and (p_key is null or p_key='') then
      raise sqlstate 'PT422' using message='journal_key_required';
    end if;
    with bounded as (
      select journal_seq,pg_catalog.json_build_object('observation_id',observation_id,'receipt_id',receipt_id,
        'payload_json',payload_json,'journal_seq',journal_seq::text,
        'recorded_utc',pg_catalog.to_char(stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) item
        from selflearn_private.journal_observations where journal_seq>after_seq and journal_seq<=ceiling
          and (p_view='observations' or receipt_id=p_key) order by journal_seq limit p_limit+1
    ), measured as (
      select *,pg_catalog.row_number() over(order by journal_seq) row_number,
        pg_catalog.sum(pg_catalog.octet_length(item::text)+1) over(order by journal_seq) wire_bytes from bounded
    ), page as (
      select * from measured where wire_bytes<=6291456 or row_number=1 order by journal_seq limit p_limit
    )
    select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
      (select pg_catalog.count(*)>(select pg_catalog.count(*) from page) from bounded),
      (select pg_catalog.max(journal_seq)::text from page) into rows,more,next_id;
    return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
                                       'watermark',ceiling::text);
  end if;
  if p_view is null or p_view not in ('status','object','receipt','receipts','generation_ack','generations',
                                     'resolution_history','revisions','latest_resolutions') then
    raise sqlstate 'PT422' using message='unsupported_journal_view';
  end if;
  select coalesce(pg_catalog.max(v),0) into legacy_ceiling from (
    select pg_catalog.max(journal_seq) v from selflearn_private.journal_receipts union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_objects union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_generation_commits union all
    select pg_catalog.max(journal_seq) from selflearn_private.journal_revisions) committed;
  legacy_ceiling := least(legacy_ceiling,ceiling);
  if p_view='status' then
    return pg_catalog.json_build_object('version',1,'rows',pg_catalog.json_build_array(
      pg_catalog.json_build_object('schema_version',1,'backend','postgres','journal_seq',ceiling::text)),
      'next_after',null,'watermark',ceiling::text);
  end if;
  if p_view in ('object','receipt','generation_ack','resolution_history') and (p_key is null or p_key='') then
    raise sqlstate 'PT422' using message='journal_key_required';
  end if;
  if p_view='generations' and p_limit<>1 then raise sqlstate 'PT422' using message='generation_page_limit_is_one'; end if;
  if after_seq>legacy_ceiling then
    return pg_catalog.json_build_object('version',1,'rows','[]'::json,'next_after',null,'watermark',ceiling::text);
  end if;
  answer := public.selflearn_journal_read_v1(p_view,p_key,p_after,p_limit,legacy_ceiling::text);
  return pg_catalog.json_build_object('version',1,'rows',answer->'rows','next_after',answer->'next_after','watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_observed_journal_page';
end $$;

revoke execute on function selflearn_private.observed_watermark_v1() from public,anon,authenticated;
revoke execute on function selflearn_private.nfl_line_token_v1(text) from public,anon,authenticated;
revoke execute on function selflearn_private.validate_observation_v1(text) from public,anon,authenticated;
revoke execute on function selflearn_private.guard_observation_insert_v1() from public,anon,authenticated;
revoke execute on function selflearn_private.guard_observed_revision_v1() from public,anon,authenticated;
revoke execute on function selflearn_private.observation_has_outcome_v1() from public,anon,authenticated;
grant execute on function selflearn_private.observed_watermark_v1() to service_role;
grant execute on function selflearn_private.nfl_line_token_v1(text) to service_role;
grant execute on function selflearn_private.validate_observation_v1(text) to service_role;
grant execute on function selflearn_private.guard_observation_insert_v1() to service_role;
grant execute on function selflearn_private.guard_observed_revision_v1() to service_role;
grant execute on function selflearn_private.observation_has_outcome_v1() to service_role;
revoke execute on function public.selflearn_journal_commit_observed_v1(text,text) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_read_observed_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_commit_observed_v1(text,text) to service_role;
grant execute on function public.selflearn_journal_read_observed_v1(text,text,text,integer,text) to service_role;
commit;
