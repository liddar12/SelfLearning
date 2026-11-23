-- Additive exact-ID immutable object reads. No source evidence or old RPC is changed.
begin;

create function public.selflearn_journal_read_objects_v1(
  p_view text,p_key text,p_after text,p_limit integer,p_watermark text) returns json
language plpgsql security invoker set search_path = '' as $$
declare ids json; wanted text[]; ceiling bigint; after_seq bigint:=0;
  rows json; more boolean; next_id text; oversized boolean;
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
  with bounded as (
    select o.journal_seq,pg_catalog.json_build_object('object_id',o.object_id,'kind',o.kind,'payload_json',o.payload_json,
      'journal_seq',o.journal_seq::text,
      'received_utc',pg_catalog.to_char(o.stored_utc at time zone 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"')) item
    from selflearn_private.journal_objects o
    where o.object_id=any(wanted) and o.journal_seq>after_seq and o.journal_seq<=ceiling
    order by o.journal_seq limit p_limit+1
  ), measured as (
    select *,pg_catalog.row_number() over(order by journal_seq) row_number,
      pg_catalog.sum(pg_catalog.octet_length(item::text)+1) over(order by journal_seq) wire_bytes
    from bounded
  ), page as (
    -- Reserve 256 bytes for the enclosing page metadata. No oversized first-row
    -- exception: the batched endpoint stays below 6 MiB, not merely 8 MiB HTTP.
    select * from measured where wire_bytes<=6291200 order by journal_seq limit p_limit
  )
  select coalesce((select pg_catalog.json_agg(item order by journal_seq) from page),'[]'::json),
    (select pg_catalog.count(*)>(select pg_catalog.count(*) from page) from bounded),
    (select pg_catalog.max(journal_seq)::text from page),
    coalesce((select wire_bytes>6291200 from measured where row_number=1),false)
    into rows,more,next_id,oversized;
  if oversized then raise sqlstate 'PT422' using message='object_exceeds_batch_page_byte_limit'; end if;
  return pg_catalog.json_build_object('version',1,'rows',rows,'next_after',case when more then next_id else null end,
    'watermark',ceiling::text);
exception when data_exception then raise sqlstate 'PT422' using message='invalid_object_read';
end $$;
revoke execute on function public.selflearn_journal_read_objects_v1(text,text,text,integer,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_read_objects_v1(text,text,text,integer,text) to service_role;
notify pgrst, 'reload schema';
commit;

