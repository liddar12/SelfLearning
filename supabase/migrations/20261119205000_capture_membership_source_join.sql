-- Join frozen receipt sources once before validating membership and parity.
begin;

create or replace function selflearn_private.capture_members_v2(season integer,weeks jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare
  rec record; forecast record; binding record; doc json; item json; source json; sourceb jsonb; generic json;
  generation_season json; generation_week json;
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
    generation_season := doc->'season';
    generation_week := doc->'week';
    -- Read the multi-MiB envelope once and join its members by source ID.
    -- Use JSON for receipt content to preserve numeric lexemes for hashing.
    for binding in
      with sources as materialized (
        select value->>'receipt_id' as sid, value->'learning_receipt' as source
        from pg_catalog.json_array_elements(doc->'receipts')
      )
      select a.value as item, sources.source
      from pg_catalog.json_array_elements(rec.ack_json::json->'receipts') a
      left join sources on sources.sid = a.value->>'source_receipt_id'
    loop
      item := binding.item;
      rid := item->>'receipt_id'; sid := item->>'source_receipt_id';
      if not (rid=any(scoped)) then continue; end if;
      select * into forecast from selflearn_private.journal_receipts where receipt_id=rid;
      if not found or forecast.source_receipt_id is distinct from sid then
        raise sqlstate 'PT422' using message='capture_receipt_mapping_mismatch';
      end if;
      source := binding.source;
      sourceb := source::jsonb;
      generic := forecast.payload_json::json;
      player := coalesce(nullif(sourceb->>'player_id',''),nullif(sourceb->>'gsis_id',''));
      if pg_catalog.json_typeof(source) is distinct from 'object'
         or pg_catalog.jsonb_typeof(sourceb->'version') is distinct from 'number' or sourceb->>'version' is distinct from '1'
         or selflearn_private.digest_json_v1(source,'receipt_id') is distinct from sid
         or sourceb->>'receipt_id' is distinct from sid
         or pg_catalog.jsonb_typeof(sourceb->'season') is distinct from 'number' or sourceb->>'season' is distinct from season::text
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
      key := pg_catalog.json_build_array(season,sourceb->>'game_id',player,sourceb->>'market',
               selflearn_private.nfl_line_token_v1(sourceb->>'line'),sourceb->>'side','over','gte');
      if selflearn_private.digest_json_v1(key) is distinct from sourceb->>'contract_id'
         or sourceb->>'contract_id' is distinct from generic->>'contract_id'
         or generic->>'event_id' is distinct from 'nfl:'||season::text||':game:'||(sourceb->>'game_id') then
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

commit;
