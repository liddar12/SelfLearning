-- Keep the existing canonical bytes while skipping per-character work for printable ASCII JSON strings.
begin;

create or replace function selflearn_private.json_string_v1(value text) returns text
language plpgsql immutable strict security invoker set search_path = '' as $$
declare
  raw text := pg_catalog.to_json(value)::text;
  answer text := ''; ch text; n integer; i integer;
begin
  -- Python's ensure_ascii profile, including DEL and surrogate pairs.
  if raw collate "C" ~ '^[ -~]*$' then
    return raw;
  end if;
  for i in 1..pg_catalog.char_length(raw) loop
    ch := pg_catalog.substr(raw, i, 1); n := pg_catalog.ascii(ch);
    if n >= 127 and n <= 65535 then
      answer := answer || E'\\u' || pg_catalog.lpad(pg_catalog.to_hex(n), 4, '0');
    elsif n > 65535 then
      n := n - 65536;
      answer := answer || E'\\u' || pg_catalog.to_hex(55296 + (n / 1024))
                       || E'\\u' || pg_catalog.to_hex(56320 + (n % 1024));
    else answer := answer || ch;
    end if;
  end loop;
  return answer;
end $$;

alter function public.selflearn_journal_commit_generation_v1(text,text,text) reset statement_timeout;
notify pgrst, 'reload schema';

commit;
