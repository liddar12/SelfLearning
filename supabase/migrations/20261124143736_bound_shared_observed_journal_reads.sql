-- Use one indexed sweep of the committed tails and bound the selected
-- immutable rows before encoding them. Legacy RPC and journal writes stay intact.
begin;

create function selflearn_private.journal_legacy_tail_v1() returns bigint
language sql stable security invoker set search_path = '' as $$
  select greatest(
    (select r.journal_seq from selflearn_private.journal_receipts r order by r.journal_seq desc limit 1),
    (select o.journal_seq from selflearn_private.journal_objects o order by o.journal_seq desc limit 1),
    (select c.journal_seq from selflearn_private.journal_generation_commits c order by c.journal_seq desc limit 1),
    (select r.journal_seq from selflearn_private.journal_revisions r order by r.journal_seq desc limit 1))
$$;
revoke execute on function selflearn_private.journal_legacy_tail_v1() from public,anon,authenticated;
grant execute on function selflearn_private.journal_legacy_tail_v1() to service_role;

create or replace function selflearn_private.observed_watermark_v1() returns bigint
language sql stable security invoker set search_path = '' as $$
  select coalesce(greatest(selflearn_private.journal_legacy_tail_v1(),
    (select o.journal_seq from selflearn_private.journal_observations o order by o.journal_seq desc limit 1)),0)
$$;

create or replace function public.selflearn_journal_read_observed_v1(p_view text,p_key text,p_after text,p_limit integer,p_watermark text)
returns json language plpgsql security invoker set search_path = '' as $$
declare
  ceiling bigint; legacy_tail bigint; page_ceiling bigint; after_seq bigint:=0;
  page_cursor refcursor; candidate record; packed json; items json[]:=array[]::json[];
  rows json; more boolean:=false; next_id text; row_count integer:=0; wire_bytes bigint:=0; item_bytes bigint;
begin
  if p_limit is null or p_limit not between 1 and 500 then raise sqlstate 'PT422' using message='invalid_page_limit'; end if;
  -- Read both tails in one statement/snapshot. Status needs no repeated
  -- legacy lookup; other views reuse the legacy tail they just read.
  with tails as materialized (
    select selflearn_private.journal_legacy_tail_v1() legacy,
      (select o.journal_seq from selflearn_private.journal_observations o order by o.journal_seq desc limit 1) observations
  ) select t.legacy,coalesce(greatest(t.legacy,t.observations),0) into legacy_tail,ceiling from tails t;
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
  if p_view='status' then
    return pg_catalog.json_build_object('version',1,'rows',pg_catalog.json_build_array(
      pg_catalog.json_build_object('schema_version',1,'backend','postgres','journal_seq',ceiling::text)),
      'next_after',null,'watermark',ceiling::text);
  end if;
  if p_view is null or p_view not in ('object','receipt','receipts','generation_ack','generations',
      'resolution_history','revisions','latest_resolutions','observations','observation_history') then
    raise sqlstate 'PT422' using message='unsupported_journal_view';
  end if;
  if p_view in ('object','receipt','generation_ack','resolution_history','observation_history') and (p_key is null or p_key='') then
    raise sqlstate 'PT422' using message='journal_key_required';
  end if;
  if p_view='generations' and p_limit<>1 then raise sqlstate 'PT422' using message='generation_page_limit_is_one'; end if;
  page_ceiling:=case when p_view in ('observations','observation_history') then ceiling else least(ceiling,coalesce(legacy_tail,0)) end;
  if after_seq>page_ceiling then
    return pg_catalog.json_build_object('version',1,'rows','[]'::json,'next_after',null,'watermark',ceiling::text);
  end if;
  -- Choose the concrete index path before encoding a single large JSON row.
  case p_view
    when 'object' then
      open page_cursor for select o.object_id,o.kind,o.payload_json,o.journal_seq,o.stored_utc
        from selflearn_private.journal_objects o where o.object_id=p_key and o.journal_seq>after_seq and o.journal_seq<=page_ceiling;
    when 'receipt' then
      open page_cursor for select r.receipt_id,r.payload_json,r.journal_seq,r.stored_utc
        from selflearn_private.journal_receipts r where r.receipt_id=p_key and r.journal_seq>after_seq and r.journal_seq<=page_ceiling;
    when 'receipts' then
      open page_cursor for select r.receipt_id,r.payload_json,r.journal_seq,r.stored_utc
        from selflearn_private.journal_receipts r where r.journal_seq>after_seq and r.journal_seq<=page_ceiling
        order by r.journal_seq limit p_limit+1;
    when 'generation_ack' then
      open page_cursor for select c.object_id,c.ack_json as payload_json,c.journal_seq
        from selflearn_private.journal_generation_commits c where c.object_id=p_key and c.journal_seq>after_seq and c.journal_seq<=page_ceiling;
    when 'generations' then
      open page_cursor for select c.object_id,o.payload_json,c.ack_json,c.journal_seq,c.stored_utc
        from selflearn_private.journal_generation_commits c join selflearn_private.journal_objects o using(object_id)
        where c.journal_seq>after_seq and c.journal_seq<=page_ceiling order by c.journal_seq limit 2;
    when 'resolution_history' then
      open page_cursor for select r.revision_id,r.payload_json,r.journal_seq,r.stored_utc
        from selflearn_private.journal_revisions r where r.receipt_id=p_key and r.journal_seq>after_seq and r.journal_seq<=page_ceiling
        order by r.journal_seq limit p_limit+1;
    when 'revisions' then
      open page_cursor for select r.revision_id,r.payload_json,r.journal_seq,r.stored_utc
        from selflearn_private.journal_revisions r where r.journal_seq>after_seq and r.journal_seq<=page_ceiling
        order by r.journal_seq limit p_limit+1;
    when 'latest_resolutions' then
      open page_cursor for select r.revision_id,r.payload_json,r.journal_seq,r.stored_utc
        from selflearn_private.journal_revisions r where r.journal_seq>after_seq and r.journal_seq<=page_ceiling
          and not exists(select 1 from selflearn_private.journal_revisions n where n.supersedes=r.revision_id and n.journal_seq<=page_ceiling)
        order by r.journal_seq limit p_limit+1;
    when 'observation_history' then
      open page_cursor for select o.observation_id,o.receipt_id,o.payload_json,o.journal_seq,o.stored_utc
        from selflearn_private.journal_observations o where o.receipt_id=p_key and o.journal_seq>after_seq and o.journal_seq<=page_ceiling
        order by o.journal_seq limit p_limit+1;
    else
      open page_cursor for select o.observation_id,o.receipt_id,o.payload_json,o.journal_seq,o.stored_utc
        from selflearn_private.journal_observations o where o.journal_seq>after_seq and o.journal_seq<=page_ceiling
        order by o.journal_seq limit p_limit+1;
  end case;
  loop
    fetch page_cursor into candidate;
    exit when not found;
    if row_count=p_limit or (row_count>0 and wire_bytes+pg_catalog.octet_length(candidate.payload_json)>6291456) then
      more:=true; exit;
    end if;
    case
      when p_view='object' then
        packed:=pg_catalog.json_build_object('object_id',candidate.object_id,'kind',candidate.kind,'payload_json',candidate.payload_json,
          'journal_seq',candidate.journal_seq::text,
          'received_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
      when p_view in ('receipts','receipt') then
        packed:=pg_catalog.json_build_object('receipt_id',candidate.receipt_id,'payload_json',candidate.payload_json,
          'journal_seq',candidate.journal_seq::text,
          'received_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
      when p_view='generation_ack' then
        packed:=pg_catalog.json_build_object('object_id',candidate.object_id,'ack_json',candidate.payload_json,'journal_seq',candidate.journal_seq::text);
      when p_view='generations' then
        packed:=pg_catalog.json_build_object('object_id',candidate.object_id,'payload_json',candidate.payload_json,'ack_json',candidate.ack_json,
          'journal_seq',candidate.journal_seq::text,
          'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
      when p_view in ('observations','observation_history') then
        packed:=pg_catalog.json_build_object('observation_id',candidate.observation_id,'receipt_id',candidate.receipt_id,
          'payload_json',candidate.payload_json,'journal_seq',candidate.journal_seq::text,
          'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
      else
        packed:=pg_catalog.json_build_object('revision_id',candidate.revision_id,'payload_json',candidate.payload_json,
          'journal_seq',candidate.journal_seq::text,
          'recorded_utc',pg_catalog.to_char(candidate.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"'));
    end case;
    item_bytes:=pg_catalog.octet_length(packed::text)::bigint+1;
    -- Retain the existing byte budget and single oversized first row rule.
    if row_count>0 and wire_bytes+item_bytes>6291456 then more:=true; exit; end if;
    items:=pg_catalog.array_append(items,packed);
    wire_bytes:=wire_bytes+item_bytes;
    row_count:=row_count+1;
    next_id:=candidate.journal_seq::text;
  end loop;
  close page_cursor;
  select coalesce(pg_catalog.json_agg(v.item order by v.ordinality),'[]'::json)
    into rows from pg_catalog.unnest(items) with ordinality v(item,ordinality);
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,'watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_observed_journal_page';
end $$;

commit;
