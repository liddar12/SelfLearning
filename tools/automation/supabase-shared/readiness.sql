-- READ ONLY: new trading metadata and enrollment counts; no evidence contents.
BEGIN TRANSACTION READ ONLY;
SELECT jsonb_build_object(
  'tables', (SELECT coalesce(jsonb_agg(jsonb_build_object(
      'schema',n.nspname,'table',c.relname,'rls',c.relrowsecurity) ORDER BY n.nspname,c.relname),'[]'::jsonb)
    FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname IN ('trading','trading_private') AND c.relkind='r'),
  'rpc', (SELECT coalesce(jsonb_agg(jsonb_build_object(
      'name',p.proname,'arguments',pg_get_function_identity_arguments(p.oid),
      'security_definer',p.prosecdef,'volatility',p.provolatile,'settings',p.proconfig,
      'anon_execute',has_function_privilege('anon',p.oid,'EXECUTE'),
      'authenticated_execute',has_function_privilege('authenticated',p.oid,'EXECUTE'),
      'service_execute',has_function_privilege('service_role',p.oid,'EXECUTE')) ORDER BY p.proname),'[]'::jsonb)
    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname LIKE 'trade_bot_%'),
  'role_grants', (SELECT coalesce(jsonb_agg(jsonb_build_object(
      'role',r.role_name,'schema',n.nspname,'table',c.relname,
      'table_select',has_table_privilege(r.role_name,c.oid,'SELECT'),
      'table_insert',has_table_privilege(r.role_name,c.oid,'INSERT'),
      'column_insert',has_any_column_privilege(r.role_name,c.oid,'INSERT'),
      'update',has_any_column_privilege(r.role_name,c.oid,'UPDATE'),
      'delete',has_table_privilege(r.role_name,c.oid,'DELETE'),
      'truncate',has_table_privilege(r.role_name,c.oid,'TRUNCATE')) ORDER BY r.role_name,n.nspname,c.relname),'[]'::jsonb)
    FROM (VALUES ('anon'),('authenticated'),('service_role')) r(role_name)
    CROSS JOIN pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname IN ('trading','trading_private') AND c.relkind='r'),
  'enabled_principals', (SELECT jsonb_build_object(
    'owners',count(*) FILTER (WHERE enabled AND principal_kind='owner'),
    'workers',count(*) FILTER (WHERE enabled AND principal_kind='worker'))
    FROM trading_private.principals)
) AS trading_readiness;
COMMIT;
