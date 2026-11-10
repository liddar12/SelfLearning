# Supabase access and trading isolation, November 10, 2026

## Result and scope

The reconnection succeeded. Connected-tool reads now reach the intended
SelfLearningAI project and organization. This record supersedes the earlier
inaccessible-project observations in setup documents and the historical
Vercel repair record. The Vercel repair itself remains valid and unchanged.

Inspection was limited to project/organization metadata, database catalogs,
migration metadata and advisory summaries. No prediction or journal contents,
Auth user records, passwords or API secrets were read. No hosted writes,
settings changes, project creation, broker actions or trading schedules occurred.

## Verified existing project

| Field | Observed value |
| --- | --- |
| Project | `SelfLearningAI` / `omavgimpnvrpuxqhfxpf` |
| Organization | `SelfLearningAI` / `oukmfzwhkgiabisneuyr` |
| Status | `ACTIVE_HEALTHY` |
| Region | `us-east-2` |
| PostgreSQL | `17.11.0.003` |
| Public learning tables | `predictions`, `outcomes`, `registry`, `registry_scores` |
| Private journals | 15 tables in `selflearn_private`, including receipt, observation, capture, owner-intent and learning-job journals |
| Migration history | Nine entries; latest version `20261104225429`, name `revoke_rls_auto_enable_execute_from_api_roles` |

All four public tables and the 15 private journal tables have RLS enabled.
Both predictions and outcomes have `received_at`. The expected foreign keys
exist from `outcomes.prediction_id` to `predictions.id`, and from
`registry_scores.version` to `registry.version`.

No policies were listed for the four public tables. Effective table and
column privilege checks found no SELECT/INSERT/UPDATE/DELETE/TRUNCATE access
for `anon` or `authenticated`. `service_role` can SELECT all four tables and
has column INSERT access on predictions/outcomes only. It has no UPDATE,
DELETE, TRUNCATE or column UPDATE privilege on these tables. Both receipt
timestamps default to `clock_timestamp()`, and the service role cannot
explicitly INSERT or UPDATE `received_at`. These observed application grants
support append-only writes with database-owned receipt times.

No noninternal triggers were listed for predictions or outcomes; absence of
those triggers does not itself make the observed grant model unsafe.
`public.selflearn_status()` is a stable security-invoker function, executable
by `service_role` but not by `anon` or `authenticated`. These catalog checks
are not a hosted write/integration test or an audit of every callable function.

The security advisor returned only informational RLS Enabled No Policy notices.
The health advisor returned no notices. These are bounded observations, not
proof of correct Auth, all privileges, backup readiness, ingestion or runtime
behavior. The separate AYSO project remains outside this setup and untouched.

## Why propose a separate trading project

The existing project already supports shared learning/NFL state. The prepared
terminal's `api/_store.js` reads public predictions and outcomes using a
service credential and only an optional task filter. Imports accept a task
name supplied by the caller. Owner authentication does not partition those
records from NFL data. Both Auth verification and storage also use the same
`SUPABASE_URL`; a different Auth/storage topology needs a reviewed code change.

The fresh-project candidate under `tools/automation/supabase` deliberately
refuses an existing public schema or status function. Its immutable triggers,
column-scoped inserts and registry restrictions are not a reviewed upgrade
for existing NFL writers. Do not remove that guard or apply the candidate to
the current shared database. Also avoid changing the shared project's signup
settings merely because the isolated owner-auth runbook says to disable signup.

## Concrete proposal and next owner action

| Proposed field | Value |
| --- | --- |
| New project name | `liddar-trading` |
| Organization | Existing SelfLearningAI organization, `oukmfzwhkgiabisneuyr` |
| Region | `us-east-2` |
| Initial use | SHADOW/paper development with isolated prediction evidence and owner Auth |
| Existing NFL database | Preserve as-is |
| Cost and eligibility | Not yet quoted; no guarantee of a free project |
| Creation status | Not created; specific organization choice still required |

**Next owner action:** "Use the SelfLearningAI organization for the separate
trading project."

The Supabase `get_cost` and `create_project` tool descriptions explicitly
require the user to choose the organization before invocation. The proposal
makes that choice concrete; it does not record that choice as already made.
After selection, obtain the current quote and check eligibility against the
owner's shared $50/month additional automation budget. Do not assume a new
$50 budget for this bot or accept an unreviewed paid tier.

## Delegated setup after organization selection

1. Verify current project cost and eligibility, then provision the reviewed
   isolated project within the owner's authorization.
2. Apply the appropriate reviewed fresh-project schema and verify hosted
   PostgREST joins, server receipt ownership and application-role permissions.
3. Configure owner Auth on the selected isolated project without storage or
   paid research secrets first. Verify anonymous and wrong-owner rejection,
   then the owner's session. Provision passwords privately.
4. Configure server-only storage/provider credentials and perform disposable
   prediction, outcome and score checks. Keep preview/test data isolated.
5. Implement/review the hosted Python store and runner integration. The current
   backend exports `StorageBackend` and `SqliteStore`; SQL setup alone does not
   connect a trading runner or establish recovery behavior.

The prepared schema previously passed seven disposable SQL tests and three
checker tests. No unchanged tests were rerun for this documentation update.
Hosted trading-project Auth/storage integration remains pending. No live
broker authority, autonomy promotion or new trading schedule is enabled here.

## Related records

- [Current next steps](codex-grace-next-steps.md)
- [Service readiness](automation-service-readiness.md)
- [Unapplied database candidate](../tools/automation/supabase/README.md)
- [Historical Vercel repair](vercel-production-repair-2026-11-10.md)
