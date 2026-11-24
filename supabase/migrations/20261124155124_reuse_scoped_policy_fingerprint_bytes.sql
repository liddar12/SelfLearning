-- These fixed policy inputs are function signatures and hex SHA-256 digests.
-- Compose their exact canonical JSON once, without recursively encoding the
-- same policy components or reading overlapping definitions multiple times.
begin;

create or replace function selflearn_private.capture_game_policy_v1() returns text
language sql volatile security invoker set search_path = '' as $$
  with tracked as (
    select array['capture_members_v2','capture_groups_v2','capture_membership_id_v2','capture_source_index_v2',
      'capture_source_authority_v2','guard_capture_membership_cache_v2','capture_get_membership_v2',
      'capture_membership_policy_v2','capture_lock_v2','require_canonical_v1','canonical_json_v1','digest_json_v1',
      'json_number_v1','json_string_v1','nfl_line_token_v1'] base_names,
      array['capture_verify_generation_envelope_v1','capture_require_generation_proof_v1',
      'require_canonical_v1','canonical_json_v1','digest_json_v1','json_number_v1','json_string_v1'] proof_names,
      array['capture_game_members_proven_v1','capture_game_scope_v1','capture_game_members_v1','capture_game_authority_v1',
      'capture_game_membership_id_v1','capture_game_groups_v1','capture_game_source_index_v1',
      'index_capture_game_generation_v1','guard_capture_game_membership_cache_v1','capture_get_membership_v3'] game_names
  ), definitions as materialized (
    select p.oid,p.proname,p.proname=any(t.base_names) as base,p.proname=any(t.proof_names) as proof,
      p.proname=any(t.game_names) as game,p.oid::regprocedure::text as signature,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex') body_hash
    from pg_catalog.pg_proc p cross join tracked t
    where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(t.base_names||t.proof_names||t.game_names)
  ), entries as materialized (
    select proname,oid,base,proof,game,'['||selflearn_private.json_string_v1(signature)||',"'||body_hash||'"]' entry
    from definitions
  ), arrays as (
    select coalesce('['||pg_catalog.string_agg(entry,',' order by proname) filter(where base)||']','null') base_json,
      coalesce('['||pg_catalog.string_agg(entry,',' order by proname,oid) filter(where proof)||']','null') proof_json,
      coalesce('['||pg_catalog.string_agg(entry,',' order by proname) filter(where game)||']','null') game_json,
      pg_catalog.count(*) filter(where base)<>pg_catalog.count(distinct proname) filter(where base) base_overloads,
      pg_catalog.count(*) filter(where game)<>pg_catalog.count(distinct proname) filter(where game) game_overloads
    from entries
  ), parts as (
    select case when base_overloads then selflearn_private.capture_membership_policy_v2() else
      'sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
        '{"functions":'||base_json||',"kind":"nfl.capture_membership_policy.v2","version":2}','UTF8')),'hex') end base_digest,
      'sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
        '{"functions":'||proof_json||',"kind":"nfl.generation_envelope_proof.v1","version":1}','UTF8')),'hex') proof_digest,
      game_json, game_overloads
    from arrays
  )
  -- Existing policies ordered the base/game arrays only by function name.
  -- Preserve the old resolver if a future migration overloads a tracked name.
  select case when game_overloads then selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',3,
      'base_policy',base_digest,'generation_proof_policy',proof_digest,'functions',
      (select pg_catalog.json_agg(pg_catalog.json_build_array(signature,body_hash) order by proname)
       from definitions where game)))
    else 'sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
      '{"base_policy":"'||base_digest||'","functions":'||game_json||',"generation_proof_policy":"'||proof_digest||'","version":3}',
      'UTF8')),'hex') end
  from parts
$$;

commit;
