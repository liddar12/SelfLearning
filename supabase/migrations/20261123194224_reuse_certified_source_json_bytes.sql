-- Reuse exact child JSON bytes only after its generation certificate is verified.
begin;

create or replace function selflearn_private.capture_game_members_proven_v1(game_scope json) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; binding record; header record; doc json; item json; source json; sourceb jsonb; generic json; genericb jsonb;
  generation_season json; generation_week json; generation_receipts json; proof_policy text; source_index jsonb;
  source_id text; source_fields jsonb; generic_fields jsonb; k text;
  parity_fields constant text[] := array['base_prob','served_prob','base_model_version','calibrator_version',
    'forecast_created_utc','source_snapshot_id','source_timestamps','features','publication_status'];
  accepted json; scoped jsonb; seen text[] := array[]::text[]; seen_keys jsonb;
  rid text; sid text; player text; key json; scope jsonb; member jsonb;
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
    accepted := rec.ack_json::json->'receipts';
    if not exists(select 1 from pg_catalog.json_array_elements(accepted) a where scoped ? (a->>'receipt_id')) then continue; end if;
    doc := rec.payload_json::json;
    perform selflearn_private.capture_require_generation_proof_v1(rec.object_id,proof_policy);
    generation_season:=null; generation_week:=null; generation_receipts:=null;
    for header in select e.key,e.value from pg_catalog.json_each(doc) e where e.key in ('season','week','receipts') loop
      case header.key
        when 'season' then generation_season:=header.value;
        when 'week' then generation_week:=header.value;
        when 'receipts' then generation_receipts:=header.value;
      end case;
    end loop;
    for binding in
      with originals as materialized (
        select value->>'receipt_id' source_id,value->'learning_receipt' source
        from pg_catalog.json_array_elements(generation_receipts)
      )
      select a.value item,o.source,f.receipt_id forecast_id,f.source_receipt_id,f.payload_json
      from pg_catalog.json_array_elements(accepted) a
      join originals o on o.source_id=a.value->>'source_receipt_id'
      left join selflearn_private.journal_receipts f on f.receipt_id=a.value->>'receipt_id'
      where scoped ? (a.value->>'receipt_id')
    loop
      item:=binding.item;
      rid := item->>'receipt_id'; sid := item->>'source_receipt_id';
      if binding.forecast_id is null or binding.source_receipt_id is distinct from sid then
        raise sqlstate 'PT422' using message='capture_receipt_mapping_mismatch';
      end if;
      source:=binding.source;
      sourceb := source::jsonb;
      generic:=binding.payload_json::json; genericb:=generic::jsonb;
      if pg_catalog.json_typeof(source) is distinct from 'object' then
        raise sqlstate 'PT422' using message='unlocatable_original_capture_member';
      end if;
      -- A trusted generation proof has verified recursive byte-level
      -- canonicality, and the current original envelope/ACK hashes were
      -- verified above. Its nested JSON values retain those exact bytes.
      -- Hash the same UTF-8 key-ordered, top-level receipt-id exclusion
      -- without recursively re-encoding each already-canonical child.
      select 'sha256:'||pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
          '{'||coalesce(pg_catalog.string_agg(selflearn_private.json_string_v1(e.key)||':'||e.value::text,
            ',' order by e.key collate "C") filter (where e.key<>'receipt_id'),'')||'}','UTF8')),'hex'),
        pg_catalog.jsonb_object_agg(e.key,e.value::text) filter (where e.key=any(parity_fields))
      into source_id,source_fields from pg_catalog.json_each(source) e;
      player := coalesce(nullif(sourceb->>'player_id',''),nullif(sourceb->>'gsis_id',''));
      if pg_catalog.jsonb_typeof(sourceb->'version') is distinct from 'number' or sourceb->>'version' is distinct from '1'
         or source_id is distinct from sid
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
         or sourceb->>'contract_id' is distinct from genericb->>'contract_id'
         or genericb->>'event_id' is distinct from 'nfl:'||selected_season::text||':game:'||(sourceb->>'game_id') then
        raise sqlstate 'PT422' using message='capture_member_contract_mismatch';
      end if;
      select pg_catalog.jsonb_object_agg(e.key,e.value::text) into generic_fields
        from pg_catalog.json_each(generic) e where e.key=any(parity_fields);
      foreach k in array parity_fields loop
        if source_fields->>k is distinct from generic_fields->>k
           and selflearn_private.canonical_json_v1((source_fields->>k)::json) is distinct from
               selflearn_private.canonical_json_v1((generic_fields->>k)::json) then
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
        'source_json',source::text,'kickoff_utc',genericb->>'event_start_utc'));
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

commit;
