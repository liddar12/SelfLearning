-- Native cold verification of 2,996 forecasts took 12.75s. Exempt only
-- this service-only one-game preparation RPC from the shared 8s REST cap.
begin;
alter function public.selflearn_journal_prepare_capture_membership_v3(integer,text,text)
  set statement_timeout to '18s';
notify pgrst,'reload schema';
commit;
