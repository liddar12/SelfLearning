-- Game-scoped capture: additive indexed reads and persisted membership/CAS.
-- No hosted application is performed by this migration file.
begin;

create function selflearn_private.capture_game_scope_v1(value json) returns json
language plpgsql immutable security invoker set search_path = '' as $$
begin
  if pg_catalog.json_typeof(value) is distinct from 'object'
     or (select pg_catalog.array_agg(key order by key) from pg_catalog.json_object_keys(value) key)
        is distinct from array['game_id','policy','season','week']::text[]
     or value->>'policy' is distinct from 'nfl.capture_game_scope_v1'
     or pg_catalog.json_typeof(value->'season') is distinct from 'number'
     or value->>'season' !~ '^[0-9]{4}$' or (value->>'season')::integer not between 2000 and 2100
     or pg_catalog.json_typeof(value->'week') is distinct from 'number'
     or value->>'week' !~ '^[1-9][0-9]?$' or (value->>'week')::integer not between 1 and 18
     or pg_catalog.json_typeof(value->'game_id') is distinct from 'string'
     or value->>'game_id' !~ '^[A-Za-z0-9_-]{1,200}$' then
    raise sqlstate 'PT422' using message='invalid_capture_game_scope';
  end if;
  return value;
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_game_scope';
end $$;

-- Receipt lookup includes an admitted receipt with missing original generation.
-- Such a receipt must fail membership validation, rather than disappear from it.
create index journal_nfl_event_sequence_v1 on selflearn_private.journal_receipts
  ((payload_json::jsonb->>'event_id'),journal_seq)
  where domain='nfl' and task='nfl.parlay_leg';

create table selflearn_private.capture_game_generation_members_v1 (
  object_id text not null references selflearn_private.journal_generation_commits(object_id),
  receipt_id text not null references selflearn_private.journal_receipts(receipt_id),
  season integer not null, week integer, game_id text not null, journal_seq bigint not null,
  primary key(object_id,receipt_id)
);
create index capture_game_generation_scope_v1 on selflearn_private.capture_game_generation_members_v1
  (season,game_id,journal_seq,object_id);
create index capture_game_generation_receipt_v1 on selflearn_private.capture_game_generation_members_v1
  (receipt_id,journal_seq);
alter table selflearn_private.capture_game_generation_members_v1 enable row level security;
alter table selflearn_private.capture_game_generation_members_v1 force row level security;
revoke all on selflearn_private.capture_game_generation_members_v1 from public,anon,authenticated,service_role;
grant select on selflearn_private.capture_game_generation_members_v1 to service_role;
create trigger game_member_immutable before update or delete on selflearn_private.capture_game_generation_members_v1
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger game_member_no_truncate before truncate on selflearn_private.capture_game_generation_members_v1
  for each statement execute function selflearn_private.refuse_mutation_v1();

-- Only the private trigger may write the projection. Its elevated privilege
-- is narrowly needed because service_role can insert source evidence but has
-- no INSERT/UPDATE/DELETE privilege on this DB-derived lookup table. It accepts
-- no caller parameters, reads the immutable committed row, and returns no data.
create function selflearn_private.index_capture_game_generation_v1() returns trigger
language plpgsql security definer set search_path = '' as $$
declare doc json; source_week integer;
begin
  select payload_json::json into doc from selflearn_private.journal_objects where object_id=new.object_id;
  source_week:=case when pg_catalog.json_typeof(doc->'week')='number'
    and doc->>'week' ~ '^[1-9][0-9]?$' then (doc->>'week')::integer else null end;
  insert into selflearn_private.capture_game_generation_members_v1(object_id,receipt_id,season,week,game_id,journal_seq)
    select new.object_id,r.receipt_id,pg_catalog.split_part(r.payload_json::json->>'event_id',':',2)::integer,
      source_week,pg_catalog.split_part(r.payload_json::json->>'event_id',':',4),new.journal_seq
    from pg_catalog.json_array_elements(new.ack_json::json->'receipts') a
    join selflearn_private.journal_receipts r on r.receipt_id=a->>'receipt_id' and r.source_receipt_id=a->>'source_receipt_id'
    where r.domain='nfl' and r.task='nfl.parlay_leg'
      and r.payload_json::json->>'event_id' ~ '^nfl:[0-9]{4}:game:[A-Za-z0-9_-]{1,200}$'
    on conflict do nothing;
  return new;
end $$;
revoke all on function selflearn_private.index_capture_game_generation_v1() from public,anon,authenticated,service_role;
create trigger capture_game_index after insert on selflearn_private.journal_generation_commits
  for each row execute function selflearn_private.index_capture_game_generation_v1();

-- One set-based pass, before any hosted release; no source evidence is changed.
-- MATERIALIZED prevents a generation's large ACK/document from being reparsed
-- separately for every accepted receipt.
with generations as materialized (
  select c.object_id,c.journal_seq,c.ack_json::json->'receipts' accepted,
    case when pg_catalog.json_typeof(o.payload_json::json->'week')='number'
      and o.payload_json::json->>'week' ~ '^[1-9][0-9]?$' then (o.payload_json::json->>'week')::integer else null end week
  from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
), committed as materialized (
  select g.object_id,g.journal_seq,g.week,a.value->>'receipt_id' receipt_id,a.value->>'source_receipt_id' source_receipt_id
  from generations g cross join lateral pg_catalog.json_array_elements(g.accepted) a
)
insert into selflearn_private.capture_game_generation_members_v1(object_id,receipt_id,season,week,game_id,journal_seq)
  select c.object_id,r.receipt_id,pg_catalog.split_part(r.payload_json::json->>'event_id',':',2)::integer,
    c.week,pg_catalog.split_part(r.payload_json::json->>'event_id',':',4),c.journal_seq
  from committed c join selflearn_private.journal_receipts r
    on r.receipt_id=c.receipt_id and r.source_receipt_id=c.source_receipt_id
  where r.domain='nfl' and r.task='nfl.parlay_leg'
    and r.payload_json::json->>'event_id' ~ '^nfl:[0-9]{4}:game:[A-Za-z0-9_-]{1,200}$';

create function selflearn_private.capture_game_event_v1(scope json) returns text
language sql immutable security invoker set search_path = '' as $$
  select 'nfl:'||(scope->>'season')||':game:'||(scope->>'game_id')
$$;
create function selflearn_private.capture_game_authority_v1(scope json) returns bigint
language sql volatile security invoker set search_path = '' as $$
  select coalesce(pg_catalog.max(n),0) from (
    select pg_catalog.max(journal_seq) n from selflearn_private.journal_receipts
      where domain='nfl' and task='nfl.parlay_leg' and payload_json::jsonb->>'event_id'=selflearn_private.capture_game_event_v1(scope)
    union all select pg_catalog.max(journal_seq) from selflearn_private.capture_game_generation_members_v1
      where season=(scope->>'season')::integer and game_id=scope->>'game_id'
  ) authority
$$;
create function selflearn_private.capture_game_membership_id_v1(groups json,scope json) returns text
language sql security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('binding_policy','nfl.capture_labels_v1',
    'groups',(select coalesce(pg_catalog.json_agg(value->>'membership_id' order by value->>'group_id'),'[]'::json)
      from pg_catalog.json_array_elements(groups)), 'game_scope_id',selflearn_private.digest_json_v1(scope)))
$$;

-- The bounded keyset stream is scoped in SQL, before pagination/transfer.
-- Every page retains the same global observed watermark. Generations retain
-- complete original payload/ACK text and can share a byte-bounded page.
create function public.selflearn_journal_read_observed_game_v1(
  p_view text,p_key text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare scope json; ceiling bigint; after_seq bigint:=0; rows json; more boolean; next_id text;
begin
  scope:=selflearn_private.capture_game_scope_v1(selflearn_private.require_canonical_v1(p_key,1024));
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
  if p_view is null or p_view not in ('receipts','game_generations','revisions','observations') then
    raise sqlstate 'PT422' using message='unsupported_game_journal_view';
  end if;
  ceiling:=selflearn_private.observed_watermark_v1();
  if p_watermark is not null then
    if p_watermark !~ '^(0|[1-9][0-9]{0,18})$' or p_watermark::bigint>ceiling then
      raise sqlstate 'PT422' using message='invalid_read_watermark';
    end if;
    ceiling:=p_watermark::bigint;
  end if;
  if p_after is not null then
    if p_after !~ '^(0|[1-9][0-9]{0,18})$' then raise sqlstate 'PT422' using message='invalid_page_cursor'; end if;
    after_seq:=p_after::bigint;
  end if;
  if after_seq>ceiling then raise sqlstate 'PT422' using message='cursor_after_watermark'; end if;
  with selected as materialized (
    select * from selflearn_private.journal_receipts
      where domain='nfl' and task='nfl.parlay_leg' and journal_seq<=ceiling
        and payload_json::jsonb->>'event_id'=selflearn_private.capture_game_event_v1(scope)
  ), candidates as (
    select r.journal_seq,pg_catalog.json_build_object('receipt_id',r.receipt_id,'payload_json',r.payload_json,
      'journal_seq',r.journal_seq::text,'received_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) item
      from selected r where p_view='receipts'
    union all
    select c.journal_seq,pg_catalog.json_build_object('object_id',c.object_id,'payload_json',o.payload_json,'ack_json',c.ack_json,
      'journal_seq',c.journal_seq::text,'recorded_utc',pg_catalog.to_char(c.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
      where p_view='game_generations' and exists(select 1 from selflearn_private.capture_game_generation_members_v1 m
        where m.object_id=c.object_id and m.season=(scope->>'season')::integer and m.game_id=scope->>'game_id'
          and m.journal_seq<=ceiling)
    union all
    select r.journal_seq,pg_catalog.json_build_object('revision_id',r.revision_id,'payload_json',r.payload_json,
      'journal_seq',r.journal_seq::text,'recorded_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selected s join selflearn_private.journal_revisions r on r.receipt_id=s.receipt_id where p_view='revisions'
    union all
    select o.journal_seq,pg_catalog.json_build_object('observation_id',o.observation_id,'receipt_id',o.receipt_id,
      'payload_json',o.payload_json,'journal_seq',o.journal_seq::text,
      'recorded_utc',pg_catalog.to_char(o.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
      from selected s join selflearn_private.journal_observations o on o.receipt_id=s.receipt_id where p_view='observations'
  ), bounded as (
    select * from candidates where journal_seq>after_seq and journal_seq<=ceiling order by journal_seq limit p_limit+1
  ), measured as (
    select *,pg_catalog.row_number() over(order by journal_seq) row_number,
      pg_catalog.sum(pg_catalog.octet_length(item::text)+1) over(order by journal_seq) wire_bytes from bounded
  ), page as (
    select * from measured where wire_bytes<=6291456 or row_number=1 order by journal_seq limit p_limit
  )
  select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
    (select pg_catalog.count(*)>(select pg_catalog.count(*) from page) from bounded),
    (select pg_catalog.max(journal_seq)::text from page) into rows,more,next_id;
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,'watermark',ceiling::text);
end $$;

alter table selflearn_private.capture_applications_v2 add column game_scope_json text;
alter table selflearn_private.capture_applications_v2 add column game_id text;
create index capture_application_game_v1 on selflearn_private.capture_applications_v2(game_id,application_seq)
  where game_id is not null;

create function selflearn_private.capture_game_members_v1(game_scope json) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; binding record; forecast record; doc json; item json; source json; sourceb jsonb;
  accepted jsonb; scoped text[] := array[]::text[]; seen text[] := array[]::text[];
  rid text; sid text; player text; k text; key json; scope jsonb; member jsonb;
  members jsonb := '{}'::jsonb; all_ids text[]; partition_ids text[];
  selected_season integer; weeks jsonb; selected_event text;
begin
  game_scope:=selflearn_private.capture_game_scope_v1(game_scope);
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
      player := coalesce(nullif(sourceb->>'player_id',''),nullif(sourceb->>'gsis_id',''));
      if pg_catalog.json_typeof(source) is distinct from 'object'
         or pg_catalog.jsonb_typeof(sourceb->'version') is distinct from 'number' or sourceb->>'version' is distinct from '1'
         or selflearn_private.digest_json_v1(source,'receipt_id') is distinct from sid
         or sourceb->>'receipt_id' is distinct from sid
         or pg_catalog.jsonb_typeof(sourceb->'season') is distinct from 'number' or sourceb->>'season' is distinct from selected_season::text
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
      key := pg_catalog.json_build_array(selected_season,sourceb->>'game_id',player,sourceb->>'market',
               selflearn_private.nfl_line_token_v1(sourceb->>'line'),sourceb->>'side','over','gte');
      if selflearn_private.digest_json_v1(key) is distinct from sourceb->>'contract_id'
         or sourceb->>'contract_id' is distinct from forecast.payload_json::jsonb->>'contract_id'
         or forecast.payload_json::jsonb->>'event_id' is distinct from 'nfl:'||selected_season::text||':game:'||(sourceb->>'game_id') then
        raise sqlstate 'PT422' using message='capture_member_contract_mismatch';
      end if;
      foreach k in array array['base_prob','served_prob','base_model_version','calibrator_version','forecast_created_utc',
          'source_snapshot_id','source_timestamps','features','publication_status'] loop
        if selflearn_private.canonical_json_v1(source->k) is distinct from selflearn_private.canonical_json_v1(forecast.payload_json::json->k) then
          raise sqlstate 'PT422' using message='capture_member_original_parity_mismatch';
        end if;
      end loop;
      seen := pg_catalog.array_append(seen,rid);
      if not (weeks @> pg_catalog.jsonb_build_array((sourceb->>'week')::integer)) then
        raise sqlstate 'PT422' using message='capture_game_week_mismatch';
      end if;
      scope := pg_catalog.jsonb_build_array(selected_season,sourceb->>'game_id',player,sourceb->>'market',sourceb->>'side');
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

create table selflearn_private.capture_game_membership_cache_v1 (
  like selflearn_private.capture_membership_cache_v2 including defaults including constraints,
  game_scope_json text not null,
  primary key(cache_id),
  unique(game_scope_json,authority_watermark,policy_id)
);
alter table selflearn_private.capture_game_membership_cache_v1 enable row level security;
alter table selflearn_private.capture_game_membership_cache_v1 force row level security;
revoke all on selflearn_private.capture_game_membership_cache_v1 from public,anon,authenticated,service_role;
grant select,insert(season,weeks_json,game_scope_json) on selflearn_private.capture_game_membership_cache_v1 to service_role;
create trigger game_cache_immutable before update or delete on selflearn_private.capture_game_membership_cache_v1
  for each row execute function selflearn_private.refuse_mutation_v1();
create trigger game_cache_no_truncate before truncate on selflearn_private.capture_game_membership_cache_v1
  for each statement execute function selflearn_private.refuse_mutation_v1();

create function selflearn_private.capture_game_policy_v1() returns text
language sql volatile security invoker set search_path = '' as $$
  select selflearn_private.digest_json_v1(pg_catalog.json_build_object('version',3,
    'base_policy',selflearn_private.capture_membership_policy_v2(),
    'functions',pg_catalog.json_agg(pg_catalog.json_build_array(p.oid::regprocedure::text,
      pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(pg_catalog.pg_get_functiondef(p.oid),'UTF8')),'hex')) order by p.proname)))
  from pg_catalog.pg_proc p where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(array[
    'capture_game_scope_v1','capture_game_members_v1','capture_game_authority_v1','capture_game_membership_id_v1',
    'index_capture_game_generation_v1','guard_capture_game_membership_cache_v1','capture_get_membership_v3'])
$$;

create function selflearn_private.guard_capture_game_membership_cache_v1() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare weeks json; game_scope json; current_bytes bigint; current_rows bigint;
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
  new.members:=selflearn_private.capture_game_members_v1(game_scope);
  new.groups:=selflearn_private.capture_groups_v2(new.members)::jsonb;
  new.membership_id:=selflearn_private.capture_game_membership_id_v1(new.groups::json,game_scope);
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

create trigger game_cache_insert before insert on selflearn_private.capture_game_membership_cache_v1
  for each row execute function selflearn_private.guard_capture_game_membership_cache_v1();

-- A single dispatch point preserves all unscoped v2 behavior. Every scoped
-- registration, batch and terminal comparison obtains this DB-derived row.
create function selflearn_private.capture_get_membership_v3(season integer,weeks json,game_scope json)
returns selflearn_private.capture_membership_cache_v2
language plpgsql volatile security invoker set search_path = '' as $$
declare answer selflearn_private.capture_membership_cache_v2%rowtype; fence bigint; policy text; encoded text;
begin
  if game_scope is null then return selflearn_private.capture_get_membership_v2(season,weeks); end if;
  perform selflearn_private.capture_lock_v2();
  game_scope:=selflearn_private.capture_game_scope_v1(game_scope);
  encoded:=selflearn_private.canonical_json_v1(game_scope);
  if season is distinct from (game_scope->>'season')::integer or selflearn_private.canonical_json_v1(weeks)
      is distinct from '['||(game_scope->>'week')||']' then
    raise sqlstate 'PT422' using message='capture_game_cache_period_mismatch';
  end if;
  fence:=selflearn_private.capture_game_authority_v1(game_scope); policy:=selflearn_private.capture_game_policy_v1();
  select (pg_catalog.jsonb_populate_record(null::selflearn_private.capture_membership_cache_v2,pg_catalog.to_jsonb(c))).*
    into answer from selflearn_private.capture_game_membership_cache_v1 c
    where c.game_scope_json=encoded and c.authority_watermark=fence and c.policy_id=policy;
  if not found then
    insert into selflearn_private.capture_game_membership_cache_v1 as inserted(season,weeks_json,game_scope_json)
      values(season,selflearn_private.canonical_json_v1(weeks),encoded)
      returning inserted.season,inserted.weeks_json,inserted.authority_watermark,inserted.policy_id,inserted.cache_id,
        inserted.membership_id,inserted.members,inserted.groups,inserted.source_index,
        inserted.payload_sha256,inserted.accounted_bytes,inserted.stored_utc into answer;
  end if;
  if answer.payload_sha256 is distinct from pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
       pg_catalog.jsonb_build_array(answer.members,answer.groups,answer.source_index)::text,'UTF8')),'hex')
     or answer.membership_id is distinct from selflearn_private.capture_game_membership_id_v1(answer.groups::json,game_scope)
     or answer.cache_id is distinct from selflearn_private.digest_json_v1(pg_catalog.json_build_object(
       'version',3,'kind','nfl.capture_game_membership_cache.v1','game_scope',game_scope,'season',season,'weeks',weeks,
       'authority_watermark',fence::text,'policy_id',policy)) then
    raise sqlstate 'PT422' using message='invalid_capture_game_membership_cache';
  end if;
  return answer;
end $$;

create function public.selflearn_journal_prepare_capture_membership_v3(p_season integer,p_weeks_json text,p_game_scope_json text) returns json
language plpgsql volatile security invoker set search_path = '' as $$
declare value selflearn_private.capture_membership_cache_v2%rowtype; weeks json; scope json;
begin
  weeks:=selflearn_private.require_canonical_v1(p_weeks_json,1024);
  scope:=selflearn_private.capture_game_scope_v1(selflearn_private.require_canonical_v1(p_game_scope_json,1024));
  value:=selflearn_private.capture_get_membership_v3(p_season,weeks,scope);
  return pg_catalog.json_build_object('version',2,'kind','nfl.capture_membership_cache.v2','scope','derived_membership_cache_only',
    'durable_ack',false,'learning_accepted',false,'season',value.season,'weeks',value.weeks_json::json,'game_scope',scope,
    'authority_watermark',value.authority_watermark::text,'policy_id',value.policy_id,'cache_id',value.cache_id,'membership_id',value.membership_id,
    'member_count',(select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(value.members)),'group_count',pg_catalog.jsonb_array_length(value.groups));
end $$;

create function selflearn_private.capture_source_vector_v3(request json,game_scope json) returns text
language plpgsql security invoker set search_path = '' as $$
declare answer json;
begin
  select pg_catalog.json_agg(pg_catalog.json_build_array(o.value->>'profile',(o.value->>'season')::integer,
       o.value->'week',o.value->>'raw_sha256',(o.value->>'raw_bytes')::bigint,
       pg_catalog.to_char((o.value->>'observed_utc')::timestamptz at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))
       order by o.value->>'profile',(o.value->>'season')::integer,coalesce((o.value->>'week')::integer,0))
    into answer from pg_catalog.json_each(request->'envelope'->'observations') o;
  if answer is null then raise sqlstate 'PT422' using message='invalid_capture_source_vector'; end if;
  if game_scope is null then return selflearn_private.digest_json_v1(answer); end if;
  return selflearn_private.digest_json_v1(pg_catalog.json_build_object('sources',answer,'game_scope',game_scope));
exception when data_exception then raise sqlstate 'PT422' using message='invalid_capture_source_vector';
end $$;
create or replace function selflearn_private.guard_capture_application_v2() returns trigger
language plpgsql security invoker set search_path = '' as $$
declare game_scope json; expected_weeks json; request json; rb jsonb; capture json; seal json; sb jsonb; identity json; population json; members jsonb;
  certificate selflearn_private.capture_membership_cache_v2%rowtype;
  g json; base json; assignment json; expected_step text; group_ids jsonb; seen text[]:=array[]::text[];
  previous text:=''; previous_ordinal integer:=-1; ordinal integer:=0; n integer; total integer; answer json; record json;
begin
  perform selflearn_private.capture_lock_v2();
  if pg_catalog.octet_length(pg_catalog.json_build_object('p_request_json',new.request_json,'p_seal_json',new.seal_json,
       'p_watermark',new.expected_watermark)::text)>8388608 then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  request:=selflearn_private.require_canonical_v1(new.request_json,3145728); rb:=request::jsonb;
  seal:=selflearn_private.require_canonical_v1(new.seal_json,3145728); sb:=seal::jsonb;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(rb-'game_scope') key) is distinct from
      array['application_id','capture_request','execution_policy','kind','version']::text[]
     or pg_catalog.jsonb_typeof(rb->'version') is distinct from 'number' or rb->>'version' is distinct from '2'
     or rb->>'kind' is distinct from 'nfl.capture_application_request.v2' or rb->>'execution_policy' is distinct from 'sealed_whole_groups_v2' then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  capture:=selflearn_private.capture_request_v2(selflearn_private.canonical_json_v1(request->'capture_request'));
  if rb ? 'game_scope' then
    game_scope:=selflearn_private.capture_game_scope_v1(request->'game_scope');
    if game_scope->>'season' is distinct from capture->'envelope'->'manifest'->>'season'
       or not ((capture->'envelope'->'manifest'->'weeks')::jsonb @> pg_catalog.jsonb_build_array((game_scope->>'week')::integer)) then
      raise sqlstate 'PT422' using message='capture_game_scope_outside_envelope';
    end if;
  end if;
  new.game_scope_json:=case when game_scope is null then null else selflearn_private.canonical_json_v1(game_scope) end;
  new.game_id:=game_scope->>'game_id';
  expected_weeks:=case when game_scope is null then capture->'envelope'->'manifest'->'weeks'
    else pg_catalog.json_build_array((game_scope->>'week')::integer) end;
  identity:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application.v2','capture_intent_id',capture->'delivery_id',
    'execution_policy','sealed_whole_groups_v2');
  if game_scope is not null then identity:=(identity::jsonb||pg_catalog.jsonb_build_object('game_scope',game_scope))::json; end if;
  new.application_id:=selflearn_private.digest_json_v1(identity);
  if rb->>'application_id' is distinct from new.application_id then raise sqlstate 'PT422' using message='invalid_capture_application'; end if;
  new.capture_intent_id:=case when game_scope is null then capture->>'delivery_id' else
    selflearn_private.digest_json_v1(pg_catalog.json_build_object('capture_intent_id',capture->'delivery_id','game_scope',game_scope)) end; new.request_id:=selflearn_private.digest_json_v1(request);
  new.capture_id:=capture->>'capture_id'; new.destination_id:=capture->>'destination_id'; new.producer_id:=capture->>'producer_id';
  new.binding_policy:='nfl.capture_labels_v1'; new.source_vector_id:=selflearn_private.capture_source_vector_v3(capture,game_scope);
  if exists(select 1 from selflearn_private.journal_capture_deliveries where delivery_id=capture->>'delivery_id') then
    raise sqlstate 'PT409' using message='capture_application_legacy_original_exists';
  end if;
  if exists(select 1 from selflearn_private.capture_applications_v2 a where a.destination_id=new.destination_id
      and a.producer_id=new.producer_id and a.binding_policy=new.binding_policy and a.source_vector_id=new.source_vector_id) then
    raise sqlstate 'PT409' using message='capture_application_source_vector_reused';
  end if;
  perform selflearn_private.capture_fence_v2(new.expected_watermark);
  certificate:=selflearn_private.capture_get_membership_v3((capture->'envelope'->'manifest'->>'season')::integer,expected_weeks,game_scope);
  members:=certificate.members; population:=certificate.groups::json; new.membership_id:=certificate.membership_id;
  if (select pg_catalog.array_agg(key order by key) from pg_catalog.jsonb_object_keys(sb-'game_scope') key) is distinct from
     array['application_id','batches','binding_policy','capture_id','capture_intent_id','execution_policy','groups','kind','limits',
       'membership_id','request_id','seal_id','season','source_vector_id','source_watermark','version','weeks']::text[]
     or (rb ? 'game_scope') is distinct from (sb ? 'game_scope')
     or selflearn_private.canonical_json_v1(seal->'game_scope') is distinct from new.game_scope_json
     or pg_catalog.jsonb_typeof(sb->'version') is distinct from 'number' or sb->>'version' is distinct from '2'
     or sb->>'kind' is distinct from 'nfl.capture_application_seal.v2' or sb->>'application_id' is distinct from new.application_id
     or sb->>'request_id' is distinct from new.request_id or sb->>'capture_intent_id' is distinct from new.capture_intent_id
     or sb->>'capture_id' is distinct from new.capture_id or sb->>'execution_policy' is distinct from 'sealed_whole_groups_v2'
     or sb->>'binding_policy' is distinct from new.binding_policy or sb->>'source_watermark' is distinct from new.expected_watermark
     or pg_catalog.json_typeof(seal->'source_watermark') is distinct from 'string'
     or sb->>'source_vector_id' is distinct from new.source_vector_id or sb->>'membership_id' is distinct from new.membership_id
     or selflearn_private.canonical_json_v1(seal->'season') is distinct from selflearn_private.canonical_json_v1(capture->'envelope'->'manifest'->'season')
     or selflearn_private.canonical_json_v1(seal->'weeks') is distinct from selflearn_private.canonical_json_v1(expected_weeks)
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
  certificate:=selflearn_private.capture_get_membership_v3((seal->>'season')::integer,seal->'weeks',app.game_scope_json::json);
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
create or replace function selflearn_private.guard_capture_application_result_v2() returns trigger
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
  certificate:=selflearn_private.capture_get_membership_v3((seal->>'season')::integer,seal->'weeks',app.game_scope_json::json);
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

create function public.selflearn_journal_register_capture_application_v3(p_request_json text,p_seal_json text,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare request json;
begin
  request:=selflearn_private.require_canonical_v1(p_request_json,3145728);
  perform selflearn_private.capture_game_scope_v1(request->'game_scope');
  return public.selflearn_journal_register_capture_application_v2(p_request_json,p_seal_json,p_watermark);
end $$;
revoke execute on function public.selflearn_journal_read_observed_game_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_read_observed_game_v1(text,text,text,integer,text) to service_role;
revoke execute on function public.selflearn_journal_prepare_capture_membership_v3(integer,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_prepare_capture_membership_v3(integer,text,text) to service_role;
revoke execute on function public.selflearn_journal_register_capture_application_v3(text,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_register_capture_application_v3(text,text,text) to service_role;
do $$ declare f record; begin
  for f in select p.oid::regprocedure signature from pg_catalog.pg_proc p
    where p.pronamespace='selflearn_private'::regnamespace and p.proname=any(array[
      'capture_game_scope_v1','capture_game_event_v1','capture_game_authority_v1','capture_game_membership_id_v1',
      'capture_game_members_v1','capture_game_policy_v1','guard_capture_game_membership_cache_v1',
      'capture_get_membership_v3','capture_source_vector_v3'])
  loop
    execute pg_catalog.format('revoke execute on function %s from public,anon,authenticated',f.signature);
    execute pg_catalog.format('grant execute on function %s to service_role',f.signature);
  end loop;
end $$;
commit;


