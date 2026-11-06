-- Applied 2026-11-04 4:54:29 PM CT (version 20261104225429) to omavgimpnvrpuxqhfxpf by Henry via Supabase apply_migration.
-- Event-trigger function; fired by the ensure_rls event trigger as owner (postgres). No API role needs EXECUTE.
-- Rollback: GRANT EXECUTE ON FUNCTION public.rls_auto_enable() TO PUBLIC, anon, authenticated;
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM PUBLIC;
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM anon;
REVOKE EXECUTE ON FUNCTION public.rls_auto_enable() FROM authenticated;
