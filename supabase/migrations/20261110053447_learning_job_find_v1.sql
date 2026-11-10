-- Scoped lookup distinguishes absence from all read/transport/validation errors.
-- The existing read RPC and its complete-history validation remain unchanged.
begin;
create function public.selflearn_jobs_find_v1(p_authority_id text,p_job_id text) returns json
language plpgsql security invoker set search_path='' as $$
begin
 if pg_catalog.current_setting('transaction_isolation')<>'read committed'
    or p_authority_id is null or p_authority_id !~ '^sha256:[0-9a-f]{64}$'
    or p_job_id is null or p_job_id !~ '^sha256:[0-9a-f]{64}$' then
  raise sqlstate 'PT422' using message='invalid_learning_job_read';
 end if;
 perform pg_catalog.pg_advisory_xact_lock(619045,pg_catalog.hashtext(p_authority_id||p_job_id));
 if not exists(select 1 from selflearn_private.learning_jobs_v1
               where authority_id=p_authority_id and job_id=p_job_id) then
  return 'null'::json;
 end if;
 return public.selflearn_jobs_read_v1(p_authority_id,p_job_id);
end $$;
revoke execute on function public.selflearn_jobs_find_v1(text,text) from public,anon,authenticated;
grant execute on function public.selflearn_jobs_find_v1(text,text) to service_role;
commit;
