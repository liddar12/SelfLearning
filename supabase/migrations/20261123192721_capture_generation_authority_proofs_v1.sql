-- Derived certificates prove immutable generation envelopes once per validator
-- policy. No existing source, journal row, outcome, watermark or v2 RPC changes.
-- Existing active scopes warm one generation per call; a partial warm is safe
-- to resume on the next scheduled attempt. Nothing trusts a caller proof.
create table selflearn_private.capture_generation_envelope_proofs_v1 (
  object_id text not null references selflearn_private.journal_objects(object_id),
  policy_id text not null check(policy_id ~ '^sha256:[0-9a-f]{64}$'),
  object_seq bigint not null,
  commit_seq bigint not null,
  payload_sha256 text not null check(payload_sha256 ~ '^[0-9a-f]{64}$'),
  ack_sha256 text not null check(ack_sha256 ~ '^[0-9a-f]{64}$'),
  receipt_count integer not null check(receipt_count>=0),
  rejection_count integer not null check(rejection_count>=0),
  verified_utc timestamptz not null default pg_catalog.clock_timestamp(),
  primary key(object_id,policy_id)
);
alter table selflearn_private.capture_generation_envelope_proofs_v1 enable row level security;
alter table selflearn_private.capture_generation_envelope_proofs_v1 force row level security;
revoke all on selflearn_private.capture_generation_envelope_proofs_v1 from public,anon,authenticated,service_role;
grant select on selflearn_private.capture_generation_envelope_proofs_v1 to service_role;
create trigger generation_proof_immutable before update or delete on selflearn_private.capture_generation_envelope_proofs_v1
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger generation_proof_no_truncate before truncate on selflearn_private.capture_generation_envelope_proofs_v1
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function selflearn_private.capture_generation_proof_policy_v1() returns text
language sql volatile security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',1,
    'kind','nfl.generation_envelope_proof.v1',
    'functions',pg_catalog.json_agg(pg_catalog.json_build_array(p.oid::regprocedure::text,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex')) order by p.proname,p.oid)))
  from pg_catalog.pg_proc p where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(array[
    'capture_verify_generation_envelope_v1','capture_require_generation_proof_v1',
    'require_canonical_v1','canonical_json_v1','digest_json_v1','json_number_v1','json_string_v1'])
$$;

create function selflearn_private.capture_require_generation_proof_v1(selected_object text,selected_policy text) returns void
language plpgsql security invoker set search_path = '' as $$
declare proof selflearn_private.capture_generation_envelope_proofs_v1%rowtype; original record;
begin
  select * into proof from selflearn_private.capture_generation_envelope_proofs_v1
    where object_id=selected_object and policy_id=selected_policy;
  if not found then raise sqlstate 'PT503' using message='capture_generation_proof_pending'; end if;
  select o.kind,o.journal_seq object_seq,c.journal_seq commit_seq,o.payload_json,c.ack_json into original
    from selflearn_private.journal_objects o join selflearn_private.journal_generation_commits c using(object_id)
    where o.object_id=selected_object;
  if not found or original.kind is distinct from 'generation'
     or proof.object_seq is distinct from original.object_seq or proof.commit_seq is distinct from original.commit_seq
     or proof.payload_sha256 is distinct from pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(original.payload_json,'UTF8')),'hex')
     or proof.ack_sha256 is distinct from pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(original.ack_json,'UTF8')),'hex') then
    raise sqlstate 'PT422' using message='capture_generation_proof_integrity_mismatch';
  end if;
end $$;

-- The sole certificate writer derives every field from original database rows.
-- SECURITY DEFINER permits only this checked derivation to insert; service_role
-- has neither INSERT nor UPDATE on the certificate table.
create function selflearn_private.capture_verify_generation_envelope_v1(selected_object text) returns boolean
language plpgsql security definer set search_path = '' as $$
declare
  original record; policy text; doc json; ack json; sources text[]; partition_ids text[];
  accepted_count integer; rejected_count integer; inserted_count integer;
begin
  if selected_object is null or selected_object !~ '^sha256:[0-9a-f]{64}$' then
    raise sqlstate 'PT422' using message='invalid_capture_generation_identity';
  end if;
  policy:=selflearn_private.capture_generation_proof_policy_v1();
  if exists(select 1 from selflearn_private.capture_generation_envelope_proofs_v1
      where object_id=selected_object and policy_id=policy) then
    perform selflearn_private.capture_require_generation_proof_v1(selected_object,policy);
    return false;
  end if;
  select o.kind,o.journal_seq object_seq,c.journal_seq commit_seq,o.payload_json,c.ack_json into original
    from selflearn_private.journal_objects o join selflearn_private.journal_generation_commits c using(object_id)
    where o.object_id=selected_object;
  if not found or original.kind is distinct from 'generation' or pg_catalog.octet_length(original.ack_json)>16777216 then
    raise sqlstate 'PT422' using message='invalid_capture_generation_original';
  end if;
  doc:=selflearn_private.require_canonical_v1(original.payload_json,3145728);
  if pg_catalog.json_typeof(doc) is distinct from 'object' or pg_catalog.json_typeof(doc->'version') is distinct from 'number'
     or doc->>'version' is distinct from '1' or doc->>'envelope_id' is distinct from selected_object
     or selflearn_private.digest_json_v1(doc,'envelope_id') is distinct from selected_object
     or pg_catalog.json_typeof(doc->'receipts') is distinct from 'array' then
    raise sqlstate 'PT422' using message='invalid_capture_generation_hash';
  end if;
  ack:=original.ack_json::json;
  if pg_catalog.json_typeof(ack) is distinct from 'object'
     or (select pg_catalog.array_agg(k order by k) from pg_catalog.json_object_keys(ack) k)
        is distinct from array['durable','object_id','receipts','rejections','version']::text[]
     or pg_catalog.json_typeof(ack->'version') is distinct from 'number' or ack->>'version' is distinct from '1'
     or pg_catalog.json_typeof(ack->'durable') is distinct from 'boolean' or ack->>'durable' is distinct from 'true'
     or ack->>'object_id' is distinct from selected_object
     or pg_catalog.json_typeof(ack->'receipts') is distinct from 'array'
     or pg_catalog.json_typeof(ack->'rejections') is distinct from 'array' then
    raise sqlstate 'PT422' using message='invalid_capture_generation_ack';
  end if;
  if exists(select 1 from pg_catalog.json_array_elements(doc->'receipts') s where
      pg_catalog.json_typeof(s) is distinct from 'object'
      or pg_catalog.json_typeof(s->'receipt_id') is distinct from 'string'
      or not(s->>'receipt_id' ~ '^sha256:[0-9a-f]{64}$'))
     or exists(select 1 from pg_catalog.json_array_elements(ack->'receipts') a where
      pg_catalog.json_typeof(a) is distinct from 'object'
      or (select pg_catalog.array_agg(k order by k) from pg_catalog.json_object_keys(a) k)
         is distinct from array['receipt_id','source_receipt_id','status']::text[]
      or pg_catalog.json_typeof(a->'source_receipt_id') is distinct from 'string'
      or not(a->>'source_receipt_id' ~ '^sha256:[0-9a-f]{64}$')
      or pg_catalog.json_typeof(a->'receipt_id') is distinct from 'string'
      or not(a->>'receipt_id' ~ '^sha256:[0-9a-f]{64}$')
      or a->>'status' is null or a->>'status' not in ('recorded','duplicate'))
     or exists(select 1 from pg_catalog.json_array_elements(ack->'rejections') r where
      pg_catalog.json_typeof(r) is distinct from 'object'
      or pg_catalog.json_typeof(r->'source_receipt_id') is distinct from 'string'
      or not(r->>'source_receipt_id' ~ '^sha256:[0-9a-f]{64}$')
      or pg_catalog.json_typeof(r->'reason') is distinct from 'string' or pg_catalog.btrim(r->>'reason')='') then
    raise sqlstate 'PT422' using message='invalid_capture_generation_partition';
  end if;
  select coalesce(pg_catalog.array_agg(s->>'receipt_id' order by s->>'receipt_id'),array[]::text[]) into sources
    from pg_catalog.json_array_elements(doc->'receipts') s;
  select coalesce(pg_catalog.array_agg(id order by id),array[]::text[]) into partition_ids from (
    select a->>'source_receipt_id' id from pg_catalog.json_array_elements(ack->'receipts') a union all
    select r->>'source_receipt_id' from pg_catalog.json_array_elements(ack->'rejections') r) p;
  if sources is distinct from partition_ids
     or pg_catalog.cardinality(sources)<>(select pg_catalog.count(distinct x) from pg_catalog.unnest(sources) x)
     or exists(select 1 from pg_catalog.json_array_elements(ack->'receipts') a
       left join selflearn_private.journal_receipts r on r.receipt_id=a->>'receipt_id'
       where r.receipt_id is null or r.source_receipt_id is distinct from a->>'source_receipt_id') then
    raise sqlstate 'PT422' using message='invalid_capture_generation_partition';
  end if;
  accepted_count:=pg_catalog.json_array_length(ack->'receipts');
  rejected_count:=pg_catalog.json_array_length(ack->'rejections');
  insert into selflearn_private.capture_generation_envelope_proofs_v1(
      object_id,policy_id,object_seq,commit_seq,payload_sha256,ack_sha256,receipt_count,rejection_count)
    values(selected_object,policy,original.object_seq,original.commit_seq,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(original.payload_json,'UTF8')),'hex'),
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(original.ack_json,'UTF8')),'hex'),accepted_count,rejected_count)
    on conflict(object_id,policy_id) do nothing;
  get diagnostics inserted_count=row_count;
  perform selflearn_private.capture_require_generation_proof_v1(selected_object,policy);
  return inserted_count=1;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_generation_proof';
end $$;

create function public.selflearn_journal_prepare_capture_generation_proofs_v1(p_game_scope_json text) returns json
language plpgsql security invoker set search_path = '' as $$
declare
  scope json; policy text; ids text[]; selected_object text; total integer; verified integer; added boolean:=false;
begin
  scope:=selflearn_private.capture_game_scope_v1(selflearn_private.require_canonical_v1(p_game_scope_json,1024));
  policy:=selflearn_private.capture_generation_proof_policy_v1();
  select coalesce(pg_catalog.array_agg(object_id order by object_id),array[]::text[]) into ids from (
    select distinct m.object_id from selflearn_private.capture_game_generation_members_v1 m
      where m.season=(scope->>'season')::integer and m.game_id=scope->>'game_id') selected;
  total:=pg_catalog.cardinality(ids);
  if total>256 then raise sqlstate 'PT422' using message='capture_generations_exceed_work_limit'; end if;
  select id into selected_object from pg_catalog.unnest(ids) id where not exists(
    select 1 from selflearn_private.capture_generation_envelope_proofs_v1 p where p.object_id=id and p.policy_id=policy)
    order by id limit 1;
  if selected_object is not null then
    added:=selflearn_private.capture_verify_generation_envelope_v1(selected_object);
  end if;
  select pg_catalog.count(*)::integer into verified from selflearn_private.capture_generation_envelope_proofs_v1 p
    where p.object_id=any(ids) and p.policy_id=policy;
  return pg_catalog.json_build_object('version',2,'kind','nfl.capture_generation_proofs.v1',
    'scope','derived_generation_envelope_proofs_only','game_scope',scope,'policy_id',policy,
    'total_generation_count',total,'verified_generation_count',verified,'remaining_generation_count',total-verified,
    'prepared_object_id',selected_object,'added',added,'durable_ack',false,'learning_accepted',false);
end $$;

create function selflearn_private.capture_game_members_proven_v1(game_scope json) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; binding record; forecast record; doc json; item json; source json; sourceb jsonb; generic json;
  generation_season json; generation_week json; proof_policy text; source_index jsonb;
  accepted jsonb; scoped text[] := array[]::text[]; seen text[] := array[]::text[];
  rid text; sid text; player text; k text; key json; scope jsonb; member jsonb;
  members jsonb := '{}'::jsonb;
  member_rows jsonb[]:=array[]::jsonb[];
  selected_season integer; weeks jsonb; selected_event text;
begin
  game_scope:=selflearn_private.capture_game_scope_v1(game_scope);
  proof_policy:=selflearn_private.capture_generation_proof_policy_v1();
  selected_season:=(game_scope->>'season')::integer; weeks:=pg_catalog.jsonb_build_array((game_scope->>'week')::integer);
  selected_event:=selflearn_private.capture_game_event_v1(game_scope);
  if (select pg_catalog.count(*)>10000 or coalesce(pg_catalog.sum(pg_catalog.octet_length(payload_json)),0)>67108864
      from selflearn_private.journal_receipts where domain='nfl' and task='nfl.parlay_leg' and payload_json::jsonb->>'event_id'=selected_event) then
    raise sqlstate 'PT422' using message='capture_membership_exceeds_work_limit';
  end if;
  for forecast in select * from selflearn_private.journal_receipts where domain='nfl' and task='nfl.parlay_leg' and payload_json::jsonb->>'event_id'=selected_event loop
    if forecast.payload_json::jsonb->>'event_id' !~ '^nfl:[0-9]{4}:game:.+$' then
      raise sqlstate 'PT422' using message='unlocatable_nfl_membership';
    end if;
    if pg_catalog.split_part(forecast.payload_json::jsonb->>'event_id',':',2)=selected_season::text then
      scoped := pg_catalog.array_append(scoped,forecast.receipt_id);
    end if;
  end loop;
  if (select pg_catalog.count(*)>256 or coalesce(pg_catalog.sum(pg_catalog.octet_length(o.payload_json)),0)>67108864
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id) where exists(select 1 from selflearn_private.capture_game_generation_members_v1 m where m.object_id=c.object_id and m.season=selected_season and m.game_id=game_scope->>'game_id')) then
    raise sqlstate 'PT422' using message='capture_generations_exceed_work_limit';
  end if;
  for rec in select c.*,o.payload_json from selflearn_private.journal_generation_commits c
      join selflearn_private.journal_objects o using(object_id) where exists(select 1 from selflearn_private.capture_game_generation_members_v1 m where m.object_id=c.object_id and m.season=selected_season and m.game_id=game_scope->>'game_id') order by c.journal_seq loop
    accepted := rec.ack_json::jsonb->'receipts';
    if not exists(select 1 from pg_catalog.jsonb_array_elements(accepted) a where a->>'receipt_id'=any(scoped)) then continue; end if;
    doc := rec.payload_json::json;
    perform selflearn_private.capture_require_generation_proof_v1(rec.object_id,proof_policy);
    generation_season:=doc->'season'; generation_week:=doc->'week';
    for binding in
      with originals as materialized (
        select value->>'receipt_id' source_id,value->'learning_receipt' source
        from pg_catalog.json_array_elements(doc->'receipts')
      )
      select a.value item,o.source from pg_catalog.json_array_elements(rec.ack_json::json->'receipts') a
      join originals o on o.source_id=a.value->>'source_receipt_id'
      where a.value->>'receipt_id'=any(scoped)
    loop
      item:=binding.item;
      rid := item->>'receipt_id'; sid := item->>'source_receipt_id';
      if not (rid=any(scoped)) then continue; end if;
      select * into forecast from selflearn_private.journal_receipts where receipt_id=rid;
      if not found or forecast.source_receipt_id is distinct from sid then
        raise sqlstate 'PT422' using message='capture_receipt_mapping_mismatch';
      end if;
      source:=binding.source;
      sourceb := source::jsonb;
      generic:=forecast.payload_json::json;
      player := coalesce(nullif(sourceb->>'player_id',''),nullif(sourceb->>'gsis_id',''));
      if pg_catalog.json_typeof(source) is distinct from 'object'
         or pg_catalog.jsonb_typeof(sourceb->'version') is distinct from 'number' or sourceb->>'version' is distinct from '1'
         or selflearn_private.digest_json_v1(source,'receipt_id') is distinct from sid
         or sourceb->>'receipt_id' is distinct from sid
         or pg_catalog.jsonb_typeof(sourceb->'season') is distinct from 'number' or sourceb->>'season' is distinct from selected_season::text
         or pg_catalog.jsonb_typeof(sourceb->'week') is distinct from 'number' or not (sourceb->>'week' ~ '^[1-9][0-9]?$')
         or (sourceb->>'week')::integer not between 1 and 18
         or generation_season::text is distinct from sourceb->>'season' or pg_catalog.json_typeof(generation_season) is distinct from 'number'
         or generation_week::text is distinct from sourceb->>'week' or pg_catalog.json_typeof(generation_week) is distinct from 'number'
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
      key := pg_catalog.json_build_array(selected_season,sourceb->>'game_id',player,sourceb->>'market',
               selflearn_private.nfl_line_token_v1(sourceb->>'line'),sourceb->>'side','over','gte');
      if selflearn_private.digest_json_v1(key) is distinct from sourceb->>'contract_id'
         or sourceb->>'contract_id' is distinct from generic->>'contract_id'
         or generic->>'event_id' is distinct from 'nfl:'||selected_season::text||':game:'||(sourceb->>'game_id') then
        raise sqlstate 'PT422' using message='capture_member_contract_mismatch';
      end if;
      foreach k in array array['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc',
          'source_snapshot_id','source_timestamps','features','publication_status'] loop
        if (source->k)::text is distinct from (generic->k)::text
           and selflearn_private.canonical_json_v1(source->k) is distinct from selflearn_private.canonical_json_v1(generic->k) then
          raise sqlstate 'PT422' using message='capture_member_original_parity_mismatch';
        end if;
      end loop;
      seen := pg_catalog.array_append(seen,rid);
      if not (weeks @> pg_catalog.jsonb_build_array((sourceb->>'week')::integer)) then
        raise sqlstate 'PT422' using message='capture_game_week_mismatch';
      end if;
      scope := pg_catalog.jsonb_build_array(selected_season,sourceb->>'game_id',player,sourceb->>'market',sourceb->>'side');
      member_rows:=pg_catalog.array_append(member_rows,pg_catalog.jsonb_build_object(
        'receipt_id',rid,'source_receipt_id',sid,'scope',scope,'generation_object_id',rec.object_id,
        'source_json',source::text,'kickoff_utc',generic->>'event_start_utc'));
    end loop;
  end loop;
  if exists(select 1 from pg_catalog.unnest(scoped) x where not (x=any(seen))) then
    raise sqlstate 'PT422' using message='unlocatable_missing_capture_generation';
  end if;
  -- Build the map once instead of copying the growing JSONB object for every
  -- receipt. Duplicate originals must still prove exactly one source/scope.
  if exists(select 1 from pg_catalog.unnest(member_rows) value group by value->>'receipt_id'
      having pg_catalog.count(distinct value->'scope')<>1 or pg_catalog.count(distinct value->>'source_receipt_id')<>1) then
    raise sqlstate 'PT422' using message='conflicting_capture_member_identity';
  end if;
  select coalesce(pg_catalog.jsonb_object_agg(receipt_id,pg_catalog.jsonb_build_object(
    'receipt_id',receipt_id,'source_receipt_id',source_receipt_id,'scope',member_scope,'generation_object_ids',generation_ids)),'{}'::jsonb)
    into members from (
      select value->>'receipt_id' receipt_id,value->>'source_receipt_id' source_receipt_id,value->'scope' member_scope,
        pg_catalog.jsonb_agg(distinct value->>'generation_object_id' order by value->>'generation_object_id') generation_ids
        from pg_catalog.unnest(member_rows) value group by value->>'receipt_id',value->>'source_receipt_id',value->'scope'
    ) grouped;
  if (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members))>10000 then
    raise sqlstate 'PT422' using message='complete_capture_exceeds_member_limit';
  end if;
  -- Preserve the earlier byte-level duplicate-original check. Canonical source
  -- parity above does not authorize choosing between different raw source text.
  if exists(select 1 from pg_catalog.unnest(member_rows) value group by value->>'receipt_id'
      having pg_catalog.count(distinct value->>'source_json')<>1) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  select coalesce(pg_catalog.jsonb_object_agg(receipt_id,pg_catalog.jsonb_build_object(
      'source_json',source_json,'kickoff_utc',kickoff_utc)),'{}'::jsonb) into source_index from (
    select value->>'receipt_id' receipt_id,pg_catalog.min(value->>'source_json') source_json,
      pg_catalog.min(value->>'kickoff_utc') kickoff_utc from pg_catalog.unnest(member_rows) value
    group by value->>'receipt_id') originals;
  return pg_catalog.jsonb_build_object('members',members,'source_index',source_index);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_membership';
end $$;

-- A compatibility wrapper still returns exactly the original membership map.
create or replace function selflearn_private.capture_game_members_v1(game_scope json) returns jsonb
language sql volatile security invoker set search_path = '' as $$
  select selflearn_private.capture_game_members_proven_v1(game_scope)->'members'
$$;

create or replace function selflearn_private.guard_capture_game_membership_cache_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare weeks json; game_scope json; current_bytes bigint; current_rows bigint; proven jsonb;
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
  select pg_catalog.count(*),coalesce(pg_catalog.sum(accounted_bytes),0) into current_rows,current_bytes
    from selflearn_private.capture_game_membership_cache_v1;
  if current_rows>=2048 then raise sqlstate 'PT503' using message='capture_membership_cache_capacity_exceeded'; end if;
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
  if current_bytes+new.accounted_bytes>134217728 then raise sqlstate 'PT503' using message='capture_membership_cache_capacity_exceeded'; end if;
  return new;
end $$;

create or replace function selflearn_private.capture_game_policy_v1() returns text
language sql volatile security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',3,
    'base_policy',selflearn_private.capture_membership_policy_v2(),
    'generation_proof_policy',selflearn_private.capture_generation_proof_policy_v1(),
    'functions',pg_catalog.json_agg(pg_catalog.json_build_array(p.oid::regprocedure::text,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex')) order by p.proname)))
  from pg_catalog.pg_proc p where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(array[
    'capture_game_members_proven_v1','capture_game_scope_v1','capture_game_members_v1','capture_game_authority_v1','capture_game_membership_id_v1',
    'capture_game_groups_v1','capture_game_source_index_v1',
    'index_capture_game_generation_v1','guard_capture_game_membership_cache_v1','capture_get_membership_v3'])
$$;

revoke execute on function selflearn_private.capture_generation_proof_policy_v1() from public,anon,authenticated;
revoke execute on function selflearn_private.capture_require_generation_proof_v1(text,text) from public,anon,authenticated;
revoke execute on function selflearn_private.capture_verify_generation_envelope_v1(text) from public,anon,authenticated;
revoke execute on function selflearn_private.capture_game_members_proven_v1(json) from public,anon,authenticated;
revoke execute on function public.selflearn_journal_prepare_capture_generation_proofs_v1(text) from public,anon,authenticated;
grant execute on function selflearn_private.capture_generation_proof_policy_v1() to service_role;
grant execute on function selflearn_private.capture_require_generation_proof_v1(text,text) to service_role;
grant execute on function selflearn_private.capture_verify_generation_envelope_v1(text) to service_role;
grant execute on function selflearn_private.capture_game_members_proven_v1(json) to service_role;
grant execute on function public.selflearn_journal_prepare_capture_generation_proofs_v1(text) to service_role;
notify pgrst,'reload schema';

