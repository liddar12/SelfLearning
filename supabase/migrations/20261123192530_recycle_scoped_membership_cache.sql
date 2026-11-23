-- Game membership cache entries are reproducible from immutable journal
-- authority. Recycle only these derived rows, under the journal write fence.
begin;

create or replace function selflearn_private.guard_capture_game_membership_cache_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare weeks json; game_scope json; proven jsonb;
begin
  perform selflearn_private.capture_lock_v2();
  weeks:=selflearn_private.require_canonical_v1(new.weeks_json,1024);
  game_scope:=selflearn_private.capture_game_scope_v1(selflearn_private.require_canonical_v1(new.game_scope_json,1024));
  if new.season is distinct from (game_scope->>'season')::integer or new.weeks_json is distinct from '['||(game_scope->>'week')||']' then
    raise sqlstate 'PT422' using message='capture_game_cache_period_mismatch';
  end if;
  if new.season is null or new.season not between 2000 and 2100 or pg_catalog.json_typeof(weeks) is distinct from 'array'
     or pg_catalog.json_array_length(weeks) not between 1 and 18
     or exists(select 1 from pg_catalog.json_array_elements(weeks) w where pg_catalog.json_typeof(w) is distinct from 'number'
       or w::text !~ '^[1-9][0-9]?$' or w::text::integer>18)
     or selflearn_private.canonical_json_v1(weeks) is distinct from selflearn_private.canonical_json_v1(
       (select pg_catalog.json_agg(w order by w) from (select distinct value::text::integer w from pg_catalog.json_array_elements(weeks)) sorted)) then
    raise sqlstate 'PT422' using message='invalid_capture_membership_cache';
  end if;
  new.authority_watermark:=selflearn_private.capture_game_authority_v1(game_scope); new.policy_id:=selflearn_private.capture_game_policy_v1();
  new.cache_id:=selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',3,'kind','nfl.capture_game_membership_cache.v1','game_scope',game_scope,
    'season',new.season,'weeks',weeks,'authority_watermark',new.authority_watermark::text,'policy_id',new.policy_id));
  proven:=selflearn_private.capture_game_members_proven_v1(game_scope);
  new.members:=proven->'members';
  new.groups:=selflearn_private.capture_game_groups_v1(new.members)::jsonb;
  new.membership_id:=selflearn_private.capture_game_membership_id_v1(new.groups::json,game_scope);
  new.source_index:=proven->'source_index';
  new.payload_sha256:=pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
    pg_catalog.jsonb_build_array(new.members,new.groups,new.source_index)::text,'UTF8')),'hex');
  new.stored_utc:=pg_catalog.clock_timestamp();
  -- Actual serialized full row, including escaped original-source text and
  -- metadata; 1KiB conservatively covers this counter's own representation.
  new.accounted_bytes:=pg_catalog.octet_length(pg_catalog.row_to_json(new)::text)+1024;
  return new;
end $$;

-- The compute trigger finishes before this trigger by name. Only it sets
-- accounting and identity. Callers have input-only INSERT and SELECT grants.
create function selflearn_private.recycle_capture_game_membership_cache_v1() returns trigger
language plpgsql security definer set search_path = '' as $$
declare cache_rows bigint; cache_bytes bigint; needed_rows bigint; needed_bytes bigint;
begin
  perform selflearn_private.capture_lock_v2();
  if new.accounted_bytes is null or new.accounted_bytes<1 or new.accounted_bytes>134217728 then
    raise sqlstate 'PT503' using message='capture_membership_cache_capacity_exceeded';
  end if;
  -- Also protect direct INSERT ... ON CONFLICT DO NOTHING from evicting
  -- rows when the existing exact cache entry already serves this request.
  if exists(select 1 from selflearn_private.capture_game_membership_cache_v1 c where c.cache_id=new.cache_id) then
    return new;
  end if;
  delete from selflearn_private.capture_game_membership_cache_v1 c
    where c.policy_id<>new.policy_id or
       (c.game_scope_json=new.game_scope_json and c.authority_watermark<>new.authority_watermark);
  select pg_catalog.count(*),coalesce(pg_catalog.sum(c.accounted_bytes),0) into cache_rows,cache_bytes
    from selflearn_private.capture_game_membership_cache_v1 c;
  needed_rows:=cache_rows+1-2048;
  needed_bytes:=cache_bytes+new.accounted_bytes-134217728;
  if needed_rows>0 or needed_bytes>0 then
    -- Inactive scopes first, then oldest derived entries. Preserve active
    -- applications when possible. Their authority can always be regenerated
    -- from the original journal if eviction is necessary under pressure.
    with ranked as (
      select c.cache_id,pg_catalog.row_number() over victims as position,
        pg_catalog.sum(c.accounted_bytes) over (victims rows between unbounded preceding and 1 preceding) as bytes_before
      from selflearn_private.capture_game_membership_cache_v1 c
      window victims as (order by (exists(
        select 1 from selflearn_private.capture_applications_v2 a where a.game_scope_json=c.game_scope_json
          and not exists(select 1 from selflearn_private.capture_application_results_v2 r where r.application_id=a.application_id)
      )),c.stored_utc,c.cache_id)
    )
    delete from selflearn_private.capture_game_membership_cache_v1 c using ranked v
      where c.cache_id=v.cache_id and (v.position<=needed_rows or coalesce(v.bytes_before,0)<needed_bytes);
  end if;
  return new;
end $$;
revoke execute on function selflearn_private.recycle_capture_game_membership_cache_v1() from public,anon,authenticated,service_role;

-- Ordinary callers, including service_role, cannot DELETE or invoke the
-- maintenance function. Even the owning role cannot DELETE at trigger depth
-- one. The narrow exception is a nested retirement by the owner-only trigger.
create function selflearn_private.guard_capture_game_cache_retirement_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
begin
  if pg_catalog.pg_trigger_depth()<2 or current_user is distinct from (
    select pg_catalog.pg_get_userbyid(c.relowner) from pg_catalog.pg_class c where c.oid=tg_relid
  ) then
    raise sqlstate 'PT409' using message='immutable_journal_evidence';
  end if;
  return old;
end $$;
revoke execute on function selflearn_private.guard_capture_game_cache_retirement_v1() from public,anon,authenticated,service_role;

drop trigger game_cache_immutable on selflearn_private.capture_game_membership_cache_v1;
create trigger game_cache_immutable before update on selflearn_private.capture_game_membership_cache_v1
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger game_cache_retirement before delete on selflearn_private.capture_game_membership_cache_v1
  for each row execute function selflearn_private.guard_capture_game_cache_retirement_v1();
create trigger z_game_cache_capacity before insert on selflearn_private.capture_game_membership_cache_v1
  for each row execute function selflearn_private.recycle_capture_game_membership_cache_v1();

commit;
