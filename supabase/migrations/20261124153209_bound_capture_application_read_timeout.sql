-- Capture report pages can outlast the default budget while the journal is
-- busy. Keep the existing bounds, watermark and service-role-only grants.
begin;

alter function public.selflearn_journal_read_capture_application_v2(text,text,text,text,integer,text)
  set statement_timeout to '18s';
notify pgrst,'reload schema';

commit;
