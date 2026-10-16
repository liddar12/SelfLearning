-- Opt-in all-time staging reservations, not a hosted enablement or retention job.
-- Administrator provisioning is required; no namespace/config row is seeded.
-- Reservations charge failed/abandoned uploads too. There is no automatic
-- refund/reset/delete: exhaustion safely stops intake until audited retention.
begin;

create table selflearn_private.capture_staging_limits (
  namespace_id text primary key check (namespace_id ~ '^sha256:[0-9a-f]{64}$'),
  max_total_bytes bigint not null check (max_total_bytes between 1 and 8589934592),
  max_chunks integer not null check (max_chunks between 1 and 65536),
  enabled boolean not null default false
);
create table selflearn_private.capture_staging_reservations (
  namespace_id text not null references selflearn_private.capture_staging_limits(namespace_id),
  chunk_id text not null check (chunk_id ~ '^sha256:[0-9a-f]{64}$'),
  raw_bytes integer not null check (raw_bytes between 1 and 1048576),
  result_json text not null,
  primary key(namespace_id,chunk_id)
);
alter table selflearn_private.capture_staging_limits enable row level security;
alter table selflearn_private.capture_staging_limits force row level security;
alter table selflearn_private.capture_staging_reservations enable row level security;
alter table selflearn_private.capture_staging_reservations force row level security;
revoke all on selflearn_private.capture_staging_limits from public,anon,authenticated,service_role;
revoke all on selflearn_private.capture_staging_reservations from public,anon,authenticated,service_role;
grant select on selflearn_private.capture_staging_limits to service_role;
grant select on selflearn_private.capture_staging_reservations to service_role;
grant insert(namespace_id,chunk_id,raw_bytes) on selflearn_private.capture_staging_reservations to service_role;

create function selflearn_private.guard_capture_staging_reservation_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare
  quota selflearn_private.capture_staging_limits%rowtype;
  used_bytes bigint; used_chunks bigint; reserved_utc text;
begin
  if pg_catalog.current_setting('transaction_isolation')<>'read committed' then
    raise sqlstate 'PT422' using message='capture_staging_requires_read_committed';
  end if;
  -- Separate from the journal CAS lock. A coarse staging-only writer lock
  -- keeps concurrent/direct INSERTs from overspending a shared namespace.
  perform pg_catalog.pg_advisory_xact_lock(619044,3);
  select * into quota from selflearn_private.capture_staging_limits where namespace_id=new.namespace_id;
  if not found or not quota.enabled then
    raise sqlstate 'PT503' using message='capture_staging_not_provisioned';
  end if;
  if new.chunk_id is null or new.chunk_id !~ '^sha256:[0-9a-f]{64}$'
     or new.raw_bytes is null or new.raw_bytes not between 1 and 1048576 then
    raise sqlstate 'PT422' using message='invalid_capture_staging_reservation';
  end if;
  select coalesce(pg_catalog.sum(raw_bytes),0),pg_catalog.count(*) into used_bytes,used_chunks
    from selflearn_private.capture_staging_reservations where namespace_id=new.namespace_id;
  if used_bytes+new.raw_bytes>quota.max_total_bytes or used_chunks+1>quota.max_chunks then
    raise sqlstate 'PT429' using message='capture_staging_budget_exhausted';
  end if;
  reserved_utc := pg_catalog.to_char(pg_catalog.clock_timestamp() at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"');
  new.result_json := selflearn_private.canonical_json_v1(pg_catalog.json_build_object(
    'version',1,'kind','selflearn.capture_stage_reservation.v1',
    'namespace_id',new.namespace_id,'chunk_id',new.chunk_id,'raw_bytes',new.raw_bytes,
    'reservation_id',selflearn_private.digest_json_v1(pg_catalog.json_build_object(
      'version',1,'namespace_id',new.namespace_id,'chunk_id',new.chunk_id,'raw_bytes',new.raw_bytes)),
    'reserved_total_bytes',used_bytes+new.raw_bytes,'reserved_chunks',used_chunks+1,
    'recorded_utc',reserved_utc,'scope','staging_budget_only','quota_reserved',true,
    'learning_accepted',false,'durable_ack',false));
  return new;
end $$;
create trigger capture_staging_reservation_insert before insert on selflearn_private.capture_staging_reservations
  for each row execute function selflearn_private.guard_capture_staging_reservation_v1();
create trigger capture_staging_reservation_immutable before update or delete on selflearn_private.capture_staging_reservations
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger capture_staging_reservation_no_truncate before truncate on selflearn_private.capture_staging_reservations
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function public.selflearn_reserve_capture_stage_v1(p_namespace_id text,p_chunk_id text,p_raw_bytes integer)
returns json language plpgsql security invoker set search_path = '' as $$
declare
  quota selflearn_private.capture_staging_limits%rowtype;
  prior selflearn_private.capture_staging_reservations%rowtype;
  answer text;
begin
  if pg_catalog.current_setting('transaction_isolation')<>'read committed' then
    raise sqlstate 'PT422' using message='capture_staging_requires_read_committed';
  end if;
  perform pg_catalog.pg_advisory_xact_lock(619044,3);
  if p_namespace_id is null or p_namespace_id !~ '^sha256:[0-9a-f]{64}$'
     or p_chunk_id is null or p_chunk_id !~ '^sha256:[0-9a-f]{64}$'
     or p_raw_bytes is null or p_raw_bytes not between 1 and 1048576 then
    raise sqlstate 'PT422' using message='invalid_capture_staging_reservation';
  end if;
  select * into quota from selflearn_private.capture_staging_limits where namespace_id=p_namespace_id;
  if not found or not quota.enabled then
    raise sqlstate 'PT503' using message='capture_staging_not_provisioned';
  end if;
  select * into prior from selflearn_private.capture_staging_reservations
    where namespace_id=p_namespace_id and chunk_id=p_chunk_id;
  if found then
    if prior.raw_bytes<>p_raw_bytes then raise sqlstate 'PT409' using message='capture_staging_identity_conflict'; end if;
    return prior.result_json::json;
  end if;
  insert into selflearn_private.capture_staging_reservations(namespace_id,chunk_id,raw_bytes)
    values(p_namespace_id,p_chunk_id,p_raw_bytes) returning result_json into answer;
  return answer::json;
end $$;

revoke execute on function selflearn_private.guard_capture_staging_reservation_v1() from public,anon,authenticated;
grant execute on function selflearn_private.guard_capture_staging_reservation_v1() to service_role;
revoke execute on function public.selflearn_reserve_capture_stage_v1(text,text,integer) from public,anon,authenticated;
grant execute on function public.selflearn_reserve_capture_stage_v1(text,text,integer) to service_role;
commit;
