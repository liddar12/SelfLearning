-- Expand verified membership once and equijoin each source by envelope and receipt.
begin;

create or replace function selflearn_private.capture_game_source_index_v1(members jsonb) returns jsonb
language plpgsql security invoker set search_path = '' as $$
declare answer jsonb; conflicting boolean;
begin
  with membership as materialized (
    select m.key receipt_id,m.value->>'source_receipt_id' source_receipt_id,g.object_id
    from pg_catalog.jsonb_each(members) m
    cross join lateral pg_catalog.jsonb_array_elements_text(m.value->'generation_object_ids') g(object_id)
  ), objects as materialized (
    select o.object_id,o.payload_json
    from (select distinct object_id from membership) g
    join selflearn_private.journal_objects o using(object_id)
  ), selected as (
    select m.receipt_id,pg_catalog.min((s.value->'learning_receipt')::text) source_json,
      pg_catalog.count(distinct (s.value->'learning_receipt')::text)>1 conflicting
    from objects o
    cross join lateral pg_catalog.json_array_elements(o.payload_json::json->'receipts') s
    join membership m on m.object_id=o.object_id and m.source_receipt_id=s.value->>'receipt_id'
    group by m.receipt_id
  )
  select coalesce(pg_catalog.jsonb_object_agg(s.receipt_id,pg_catalog.jsonb_build_object(
      'source_json',s.source_json,'kickoff_utc',r.payload_json::json->>'event_start_utc')),'{}'::jsonb),
      coalesce(pg_catalog.bool_or(s.conflicting),false)
    into answer,conflicting from selected s join selflearn_private.journal_receipts r using(receipt_id);
  if conflicting or (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(answer))<>
      (select pg_catalog.count(*) from pg_catalog.jsonb_object_keys(members)) then
    raise sqlstate 'PT422' using message='invalid_capture_application';
  end if;
  return answer;
end $$;

commit;
