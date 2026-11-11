-- READ ONLY. Run only on the intended project after access is restored.
-- Inspect structure and privileges; this does not query prediction contents.
BEGIN TRANSACTION READ ONLY;

SELECT table_name, column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name IN ('predictions', 'outcomes', 'registry', 'registry_scores')
ORDER BY table_name, ordinal_position;

SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public' AND c.relname IN ('predictions', 'outcomes', 'registry', 'registry_scores');

SELECT schemaname, tablename, policyname, roles, cmd, qual, with_check
FROM pg_catalog.pg_policies
WHERE schemaname = 'public' AND tablename IN ('predictions', 'outcomes', 'registry', 'registry_scores');

SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'public' AND table_name IN ('predictions', 'outcomes', 'registry', 'registry_scores')
ORDER BY table_name, grantee, privilege_type;

SELECT grantee, table_name, column_name, privilege_type
FROM information_schema.column_privileges
WHERE table_schema = 'public' AND table_name IN ('predictions', 'outcomes')
  AND grantee IN ('anon', 'authenticated', 'service_role')
ORDER BY table_name, grantee, column_name, privilege_type;

SELECT n.nspname, p.proname, p.prosecdef, p.provolatile,
       has_function_privilege('anon', p.oid, 'EXECUTE') AS anon_execute,
       has_function_privilege('authenticated', p.oid, 'EXECUTE') AS authenticated_execute,
       has_function_privilege('service_role', p.oid, 'EXECUTE') AS service_execute
FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public' AND p.proname = 'selflearn_status';

SELECT c.relname, t.tgname, pg_get_triggerdef(t.oid) AS definition
FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid = t.tgrelid
JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
WHERE NOT t.tgisinternal AND n.nspname = 'public' AND c.relname IN ('predictions', 'outcomes');

COMMIT;
