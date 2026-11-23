-- Bound scoped generation and object serialization to rows on the current page.
-- Preserve the exact RPC, permissions, original bytes, byte budgets and cursors.
begin;

create or replace function public.selflearn_journal_read_observed_game_v1(
  p_view text,p_key text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare scope json; ceiling bigint; after_seq bigint:=0; rows json; more boolean:=false; next_id text;
  candidate record; packed json; items json[]:=array[]::json[]; row_count integer:=0; page_wire_bytes bigint:=0; item_bytes bigint;
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
  if p_view='game_generations' then
    -- Fetch ordered candidates by key without JSON-encoding their large payloads.
    -- Only the current page and at most its first non-fitting row are encoded.
    for candidate in
      select c.journal_seq,c.object_id,o.payload_json,c.ack_json,c.stored_utc
      from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
      where c.journal_seq>after_seq and c.journal_seq<=ceiling
        and exists(select 1 from selflearn_private.capture_game_generation_members_v1 m
          where m.object_id=c.object_id and m.season=(scope->>'season')::integer and m.game_id=scope->>'game_id'
            and m.journal_seq<=ceiling)
      order by c.journal_seq limit p_limit+1
    loop
      -- Each escaped payload contains at least its original bytes. A row too
      -- large even before escaping is a cursor lookahead without serialization.
      if row_count=p_limit or (row_count>0 and page_wire_bytes+pg_catalog.octet_length(candidate.payload_json)::bigint
          +pg_catalog.octet_length(candidate.ack_json)>6291456) then
        more:=true; exit;
      end if;
      packed:=pg_catalog.json_build_object('object_id',candidate.object_id,'payload_json',candidate.payload_json,'ack_json',candidate.ack_json,
        'journal_seq',candidate.journal_seq::text,
        'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
      item_bytes:=pg_catalog.octet_length(packed::text)::bigint+1;
      -- Preserve the existing first-row exception for one complete generation.
      -- The client still enforces its separate 8 MiB transport bound.
      if row_count>0 and page_wire_bytes+item_bytes>6291456 then more:=true; exit; end if;
      items:=pg_catalog.array_append(items,packed);
      page_wire_bytes:=page_wire_bytes+item_bytes;
      row_count:=row_count+1;
      next_id:=candidate.journal_seq::text;
    end loop;
    select coalesce(pg_catalog.json_agg(v.item order by v.ordinality),'[]'::json)
      into rows from pg_catalog.unnest(items) with ordinality v(item,ordinality);
    return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
      'watermark',ceiling::text);
  end if;
  with selected as materialized (
    select * from selflearn_private.journal_receipts
      where domain='nfl' and task='nfl.parlay_leg' and journal_seq<=ceiling
        and payload_json::jsonb->>'event_id'=selflearn_private.capture_game_event_v1(scope)
  ), candidates as (
    select r.journal_seq,pg_catalog.json_build_object('receipt_id',r.receipt_id,'payload_json',r.payload_json,
      'journal_seq',r.journal_seq::text,'received_utc',pg_catalog.to_char(r.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) item
      from selected r where p_view='receipts'
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

create or replace function public.selflearn_journal_read_objects_v1(
  p_view text,p_key text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare ids json; wanted text[]; ceiling bigint; after_seq bigint:=0;
  rows json; more boolean:=false; next_id text; candidate record; item json;
  items json[]:=array[]::json[]; row_count integer:=0; wire_bytes bigint:=0; item_bytes bigint;
begin
  ids:=selflearn_private.require_canonical_v1(p_key,32768);
  if p_view is distinct from 'objects' or pg_catalog.json_typeof(ids) is distinct from 'array'
     or pg_catalog.json_array_length(ids) not between 1 and 256
     or exists(select 1 from pg_catalog.json_array_elements(ids) id
       where pg_catalog.json_typeof(id) is distinct from 'string' or id#>>'{}' !~ '^sha256:[0-9a-f]{64}$') then
    raise sqlstate 'PT422' using message='invalid_object_read_ids';
  end if;
  select pg_catalog.array_agg(id order by id collate "C") into wanted
    from (select distinct value#>>'{}' id from pg_catalog.json_array_elements(ids)) unique_ids;
  if selflearn_private.canonical_json_v1(pg_catalog.to_json(wanted)) is distinct from p_key then
    raise sqlstate 'PT422' using message='object_read_ids_must_be_unique_sorted';
  end if;
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
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
  for candidate in
    select o.journal_seq,o.object_id,o.kind,o.payload_json,o.stored_utc
    from selflearn_private.journal_objects o
    where o.object_id=any(wanted) and o.journal_seq>after_seq and o.journal_seq<=ceiling
    order by o.journal_seq limit p_limit+1
  loop
    if row_count=p_limit then more:=true; exit; end if;
    -- Reserve 256 bytes for enclosing metadata as before. TOAST raw length
    -- is a lower bound before escaping the immutable payload into a wrapper.
    if wire_bytes+pg_catalog.octet_length(candidate.payload_json)::bigint>6291200 then
      if row_count=0 then raise sqlstate 'PT422' using message='object_exceeds_batch_page_byte_limit'; end if;
      more:=true; exit;
    end if;
    item:=pg_catalog.json_build_object('object_id',candidate.object_id,'kind',candidate.kind,'payload_json',candidate.payload_json,
      'journal_seq',candidate.journal_seq::text,
      'received_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    item_bytes:=pg_catalog.octet_length(item::text)::bigint+1;
    if wire_bytes+item_bytes>6291200 then
      if row_count=0 then raise sqlstate 'PT422' using message='object_exceeds_batch_page_byte_limit'; end if;
      more:=true; exit;
    end if;
    items:=pg_catalog.array_append(items,item);
    wire_bytes:=wire_bytes+item_bytes;
    row_count:=row_count+1;
    next_id:=candidate.journal_seq::text;
  end loop;
  select coalesce(pg_catalog.json_agg(v.item order by v.ordinality),'[]'::json)
    into rows from pg_catalog.unnest(items) with ordinality v(item,ordinality);
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
    'watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_object_read';
end $$;

commit;
