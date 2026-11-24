-- A read-only optimization. Every included page has the existing response
-- shape and bounds; the client still verifies the complete pinned report.
begin;

create function public.selflearn_journal_read_capture_application_bundle_v1(
  p_application_id text,p_ledger_watermark text,p_limit integer) returns json
language plpgsql security invoker set search_path = '' set statement_timeout = '18s' as $$
declare registration json; groups_page json; batches_page json; report json; fence text;
  fallback constant json:='{"version":2,"kind":"nfl.capture_application_report_bundle.v1","status":"page_required","pages":null}';
begin
  if p_limit is null or p_limit not between 1 and 100 then
    raise sqlstate 'PT422' using message='invalid_capture_application_page';
  end if;
  registration:=public.selflearn_journal_read_capture_application_v2(
    p_application_id,'registration',null,null,1,p_ledger_watermark);
  if registration->>'found'='false' then
    return pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_report_bundle.v1',
      'status','complete','pages',pg_catalog.json_build_object('registration',registration));
  end if;
  fence:=registration->>'ledger_watermark';
  groups_page:=public.selflearn_journal_read_capture_application_v2(p_application_id,'groups',null,null,p_limit,fence);
  if groups_page->>'next_after' is not null then return fallback; end if;
  batches_page:=public.selflearn_journal_read_capture_application_v2(p_application_id,'batches',null,null,p_limit,fence);
  if batches_page->>'next_after' is not null then return fallback; end if;
  if pg_catalog.octet_length(registration::text)+pg_catalog.octet_length(groups_page::text)
      +pg_catalog.octet_length(batches_page::text)>6291456 then return fallback; end if;
  report:=pg_catalog.json_build_object('version',2,'kind','nfl.capture_application_report_bundle.v1','status','complete',
    'pages',pg_catalog.json_build_object('registration',registration,'groups',groups_page,'batches',batches_page,
      'status',public.selflearn_journal_read_capture_application_v2(p_application_id,'status',null,null,1,fence),
      'terminal',public.selflearn_journal_read_capture_application_v2(p_application_id,'terminal',null,null,1,fence)));
  if pg_catalog.octet_length(report::text)>6291456 then return fallback; end if;
  return report;
end $$;

revoke execute on function public.selflearn_journal_read_capture_application_bundle_v1(text,text,integer) from public,anon,authenticated;
grant execute on function public.selflearn_journal_read_capture_application_bundle_v1(text,text,integer) to service_role;
notify pgrst,'reload schema';
commit;
