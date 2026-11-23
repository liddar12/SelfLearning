begin;
alter function public.selflearn_journal_register_capture_application_v3(text,text,text)
  set statement_timeout to '18s';
notify pgrst,'reload schema';
commit;
