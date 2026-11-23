-- Scope and order receipt/history row IDs before wrapping immutable JSON.
-- Complete scoped history, the global watermark and the legacy RPCs stay intact.
begin;

create or replace function public.selflearn_journal_read_observed_game_v1(
  p_view text,p_key text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare scope json; ceiling bigint; after_seq bigint:=0; rows json; more boolean:=false; next_id text;
  event_key text; scoped_cursor refcursor; candidate record; packed json; items json[]:=array[]::json[]; row_count integer:=0; page_wire_bytes bigint:=0; item_bytes bigint;
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
  event_key:=selflearn_private.capture_game_event_v1(scope);
  if p_view='receipts' then
    -- The partial event/sequence index can seek directly to the page start.
    open scoped_cursor for
      select r.receipt_id,r.payload_json,r.journal_seq,r.stored_utc
      from selflearn_private.journal_receipts r
      where r.domain='nfl' and r.task='nfl.parlay_leg' and r.payload_json::jsonb->>'event_id'=event_key
        and r.journal_seq>after_seq and r.journal_seq<=ceiling
      order by r.journal_seq limit p_limit+1;
  elsif p_view='revisions' then
    open scoped_cursor for
      with selected as materialized (
        select r.receipt_id from selflearn_private.journal_receipts r
        where r.domain='nfl' and r.task='nfl.parlay_leg' and r.payload_json::jsonb->>'event_id'=event_key
          and r.journal_seq<=ceiling
      ), bounded as materialized (
        select r.revision_id,r.journal_seq from selected s
        join selflearn_private.journal_revisions r on r.receipt_id=s.receipt_id
        where r.journal_seq>after_seq and r.journal_seq<=ceiling
        order by r.journal_seq limit p_limit+1
      )
      select r.revision_id,r.payload_json,b.journal_seq,r.stored_utc from bounded b
      join selflearn_private.journal_revisions r on r.revision_id=b.revision_id
      order by b.journal_seq;
  else
    open scoped_cursor for
      with selected as materialized (
        select r.receipt_id from selflearn_private.journal_receipts r
        where r.domain='nfl' and r.task='nfl.parlay_leg' and r.payload_json::jsonb->>'event_id'=event_key
          and r.journal_seq<=ceiling
      ), bounded as materialized (
        select o.observation_id,o.journal_seq from selected s
        join selflearn_private.journal_observations o on o.receipt_id=s.receipt_id
        where o.journal_seq>after_seq and o.journal_seq<=ceiling
        order by o.journal_seq limit p_limit+1
      )
      select o.observation_id,o.receipt_id,o.payload_json,b.journal_seq,o.stored_utc from bounded b
      join selflearn_private.journal_observations o on o.observation_id=b.observation_id
      order by b.journal_seq;
  end if;
  -- Only the history records, never the underlying older receipt IDs, are
  -- lower-bounded by the cursor. Corrections and all earlier history survive.
  loop
    fetch scoped_cursor into candidate;
    exit when not found;
    if row_count=p_limit or (row_count>0 and page_wire_bytes+pg_catalog.octet_length(candidate.payload_json)>6291456) then
      more:=true; exit;
    end if;
    if p_view='receipts' then
      packed:=pg_catalog.json_build_object('receipt_id',candidate.receipt_id,'payload_json',candidate.payload_json,
        'journal_seq',candidate.journal_seq::text,
        'received_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    elsif p_view='revisions' then
      packed:=pg_catalog.json_build_object('revision_id',candidate.revision_id,'payload_json',candidate.payload_json,
        'journal_seq',candidate.journal_seq::text,
        'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    else
      packed:=pg_catalog.json_build_object('observation_id',candidate.observation_id,'receipt_id',candidate.receipt_id,
        'payload_json',candidate.payload_json,'journal_seq',candidate.journal_seq::text,
        'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    end if;
    item_bytes:=pg_catalog.octet_length(packed::text)::bigint+1;
    if row_count>0 and page_wire_bytes+item_bytes>6291456 then more:=true; exit; end if;
    items:=pg_catalog.array_append(items,packed);
    page_wire_bytes:=page_wire_bytes+item_bytes;
    row_count:=row_count+1;
    next_id:=candidate.journal_seq::text;
  end loop;
  close scoped_cursor;
  select coalesce(pg_catalog.json_agg(v.item order by v.ordinality),'[]'::json)
    into rows from pg_catalog.unnest(items) with ordinality v(item,ordinality);
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,'watermark',ceiling::text);
end $$;

-- Preserve the scoped limit installed by the preceding migration.
alter function public.selflearn_journal_read_observed_game_v1(text,text,text,integer,text)
  set statement_timeout to '18s';
notify pgrst,'reload schema';
commit;
