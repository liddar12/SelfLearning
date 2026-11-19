-- Read-only catalog audit for the SelfLearning NFL collection deployment.
-- Does not query rows, credentials, provider data or apply any migration.
with expected_rpc(signature) as (
  values
    ('public.selflearn_jobs_append_v1(text,text)'),
    ('public.selflearn_jobs_discover_v1(text,text,text,text,text,integer)'),
    ('public.selflearn_jobs_find_v1(text,text)'),
    ('public.selflearn_jobs_read_v1(text,text)'),
    ('public.selflearn_jobs_register_v1(text,text)'),
    ('public.selflearn_journal_append_object_v1(text,text,text,text)'),
    ('public.selflearn_journal_append_owner_intent_v1(text,text,text)'),
    ('public.selflearn_journal_append_receipt_v1(text)'),
    ('public.selflearn_journal_append_resolution_v1(text)'),
    ('public.selflearn_journal_commit_capture_application_batch_v2(text,text,text,text,text)'),
    ('public.selflearn_journal_commit_capture_delivery_v1(text,text,text,text)'),
    ('public.selflearn_journal_commit_generation_v1(text,text,text)'),
    ('public.selflearn_journal_commit_observed_v1(text,text)'),
    ('public.selflearn_journal_commit_publication_v1(text,text)'),
    ('public.selflearn_journal_prepare_capture_membership_v2(integer,text)'),
    ('public.selflearn_journal_read_capture_application_v2(text,text,text,text,integer,text)'),
    ('public.selflearn_journal_read_capture_delivery_v1(text,text,integer)'),
    ('public.selflearn_journal_read_observed_v1(text,text,text,integer,text)'),
    ('public.selflearn_journal_read_owner_intents_v1(text,text,text,integer,text)'),
    ('public.selflearn_journal_read_publication_v1(text,text,text,integer,text)'),
    ('public.selflearn_journal_read_v1(text,text,text,integer,text)'),
    ('public.selflearn_journal_register_capture_application_v2(text,text,text)'),
    ('public.selflearn_journal_stop_capture_application_v2(text,text,text)'),
    ('public.selflearn_nfl_collection_handoff_v1(text,text,text,text,text,text,integer,text)'),
    ('public.selflearn_nfl_collection_publication_v1(text,text,text,text,integer,text)'),
    ('public.selflearn_reserve_capture_stage_v1(text,text,integer)')
),
expected_table(name) as (
  values
    ('selflearn_private.capture_application_batches_v2'),
    ('selflearn_private.capture_application_results_v2'),
    ('selflearn_private.capture_applications_v2'),
    ('selflearn_private.capture_membership_cache_v2'),
    ('selflearn_private.capture_staging_limits'),
    ('selflearn_private.capture_staging_reservations'),
    ('selflearn_private.journal_capture_deliveries'),
    ('selflearn_private.journal_generation_commits'),
    ('selflearn_private.journal_objects'),
    ('selflearn_private.journal_observations'),
    ('selflearn_private.journal_publication_attestations'),
    ('selflearn_private.journal_receipts'),
    ('selflearn_private.journal_revisions'),
    ('selflearn_private.learning_job_events_v1'),
    ('selflearn_private.learning_jobs_v1'),
    ('selflearn_private.nfl_collection_handoff_v1'),
    ('selflearn_private.nfl_collection_publication_v1'),
    ('selflearn_private.owner_intent_events_v1')
),
roles as (
  select to_regrole('anon') as anon_role,
         to_regrole('authenticated') as authenticated_role,
         to_regrole('service_role') as service_role
),
functions as (
  select signature, to_regprocedure(signature) as function_oid from expected_rpc
),
rpc_checks as (
  select f.signature, f.function_oid is not null as present,
         has_function_privilege(r.service_role, f.function_oid, 'EXECUTE') as service_execute,
         has_function_privilege(r.anon_role, f.function_oid, 'EXECUTE') as anon_execute,
         has_function_privilege(r.authenticated_role, f.function_oid, 'EXECUTE') as authenticated_execute,
         exists (
           select 1 from aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) acl
           where acl.grantee = 0 and acl.privilege_type = 'EXECUTE'
         ) as public_execute
  from functions f cross join roles r left join pg_proc p on p.oid = f.function_oid
),
table_checks as (
  select t.name, c.oid is not null as present, c.relrowsecurity as rls_enabled,
         has_table_privilege(r.anon_role, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as anon_access,
         has_table_privilege(r.authenticated_role, c.oid, 'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') as authenticated_access
  from expected_table t cross join roles r
  left join pg_class c on c.oid = to_regclass(t.name) and c.relkind in ('r', 'p')
),
rpc_failures as (
  select * from rpc_checks
  where not present or service_execute is distinct from true or anon_execute is distinct from false
         or authenticated_execute is distinct from false or public_execute
),
table_failures as (
  select * from table_checks
  where not present or rls_enabled is distinct from true
         or anon_access is distinct from false or authenticated_access is distinct from false
)
select jsonb_build_object(
  'checked_utc', clock_timestamp(),
  'postgres_version', current_setting('server_version'),
  'migration_history_table_available', to_regclass('supabase_migrations.schema_migrations') is not null,
  'roles_present', r.anon_role is not null and r.authenticated_role is not null and r.service_role is not null,
  'journal_schema_present', to_regnamespace('selflearn_private') is not null,
  'expected_rpc_count', (select count(*) from expected_rpc),
  'expected_table_count', (select count(*) from expected_table),
  'rpc_failures', coalesce((select jsonb_agg(to_jsonb(f) order by signature) from rpc_failures f), '[]'::jsonb),
  'table_failures', coalesce((select jsonb_agg(to_jsonb(f) order by name) from table_failures f), '[]'::jsonb),
  'catalog_contract_present', not exists (select 1 from rpc_failures) and not exists (select 1 from table_failures)
) as nfl_collection_preflight
from roles r;
