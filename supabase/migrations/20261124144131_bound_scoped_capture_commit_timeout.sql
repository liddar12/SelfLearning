-- Extend only the commit deadline of an already registered game application.
-- The v2 function and all sealing, mutation, CAS and replay guards are reused.
begin;

create function public.selflearn_journal_commit_capture_application_batch_v3(
  p_application_id text,p_step_id text,p_group_results_json text,p_batch_json text,p_watermark text) returns json
language plpgsql security invoker set search_path = '' set statement_timeout = '18s' as $$
declare scope_text text; scope json; registered_game text;
begin
  perform selflearn_private.capture_lock_v2();
  select a.game_scope_json,a.game_id into scope_text,registered_game
    from selflearn_private.capture_applications_v2 a where a.application_id=p_application_id;
  if not found or scope_text is null then
    raise sqlstate 'PT422' using message='scoped_capture_application_required';
  end if;
  scope:=selflearn_private.capture_game_scope_v1(selflearn_private.require_canonical_v1(scope_text,1024));
  if scope->>'game_id' is distinct from registered_game then
    raise sqlstate 'PT422' using message='scoped_capture_application_required';
  end if;
  return public.selflearn_journal_commit_capture_application_batch_v2(
    p_application_id,p_step_id,p_group_results_json,p_batch_json,p_watermark);
end $$;

revoke execute on function public.selflearn_journal_commit_capture_application_batch_v3(text,text,text,text,text) from public,anon,authenticated;
grant execute on function public.selflearn_journal_commit_capture_application_batch_v3(text,text,text,text,text) to service_role;
notify pgrst,'reload schema';
commit;
