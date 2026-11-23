begin;
alter function public.selflearn_journal_read_observed_game_v1(text,text,text,integer,text)
  set statement_timeout to '18s';
notify pgrst,'reload schema';
commit;
