-- Gating remains exact; replace quadratic receipt-array scans with key lookup.
begin;

create or replace function selflearn_private.capture_game_members_proven_v1(game_scope json) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; binding record; forecast record; doc json; item json; source json; sourceb jsonb; generic json;
  generation_season json; generation_week json; proof_policy text; source_index jsonb;
  accepted jsonb; scoped jsonb; seen text[] := array[]::text[]; seen_keys jsonb;
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
  -- The indexed event predicate already proves the validated NFL scope.
  -- Test each ACK against sorted JSONB keys instead of scanning a 3k+ ID array.
  select coalesce(pg_catalog.jsonb_object_agg(receipt_id,true),'{}'::jsonb)
    into scoped
    from selflearn_private.journal_receipts
    where domain='nfl' and task='nfl.parlay_leg' and payload_json::jsonb->>'event_id'=selected_event;
  if (select pg_catalog.count(*)>256 or coalesce(pg_catalog.sum(pg_catalog.octet_length(o.payload_json)),0)>67108864
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id) where exists(select 1 from selflearn_private.capture_game_generation_members_v1 m where m.object_id=c.object_id and m.season=selected_season and m.game_id=game_scope->>'game_id')) then
    raise sqlstate 'PT422' using message='capture_generations_exceed_work_limit';
  end if;
  for rec in select c.*,o.payload_json from selflearn_private.journal_generation_commits c
      join selflearn_private.journal_objects o using(object_id) where exists(select 1 from selflearn_private.capture_game_generation_members_v1 m where m.object_id=c.object_id and m.season=selected_season and m.game_id=game_scope->>'game_id') order by c.journal_seq loop
    accepted := rec.ack_json::jsonb->'receipts';
    if not exists(select 1 from pg_catalog.jsonb_array_elements(accepted) a where scoped ? (a->>'receipt_id')) then continue; end if;
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
      where scoped ? (a.value->>'receipt_id')
    loop
      item:=binding.item;
      rid := item->>'receipt_id'; sid := item->>'source_receipt_id';
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
  select coalesce(pg_catalog.jsonb_object_agg(rid_key,true),'{}'::jsonb)
    into seen_keys from pg_catalog.unnest(seen) rid_key;
  if exists(select 1 from pg_catalog.jsonb_object_keys(scoped) x where not (seen_keys ? x)) then
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

create or replace function selflearn_private.capture_get_membership_v3(season integer,weeks json,game_scope json)
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
  -- Composite .*, when applied to a function call, can evaluate the entire
  -- row conversion once for each projected column. Select these exact columns
  -- directly, then run the same immutable certificate checks below.
  select c.season,c.weeks_json,c.authority_watermark,c.policy_id,c.cache_id,c.membership_id,
      c.members,c.groups,c.source_index,c.payload_sha256,c.accounted_bytes,c.stored_utc
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

commit;
