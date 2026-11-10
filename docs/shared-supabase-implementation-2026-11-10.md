# Shared SelfLearningAI implementation, November 10, 2026

## Decision and target

Jimmy approved using the same Supabase project for NFL and trading after
reviewing the shared-learning design. This supersedes the separate-project
proposal in the earlier access record. It does not authorize live orders,
autonomy promotion, project-wide Auth changes or changes to NFL data.

- Project: SelfLearningAI, `omavgimpnvrpuxqhfxpf`.
- Organization: SelfLearningAI, `oukmfzwhkgiabisneuyr`.
- Region: `us-east-2`.
- No additional Supabase project or paid tier is required by this implementation.

## Implementation

The additive migration lives under `tools/automation/supabase-shared` and
creates the `trading` and `trading_private` schemas. It does not reuse or alter
the NFL public tables or `selflearn_private` journals. Public `trade_bot_*`
functions are fixed security-invoker entry points; no new exposed-schema
setting or privileged definer function is needed.

Trading calls use authenticated Supabase user tokens, a publishable key and
an operator-managed principal allowlist. Broad project service/secret keys
are not accepted by the trading clients and have no new trading object grants.
The allowlist starts empty. Real Auth identity verification must precede
enrollment; a Supabase dashboard or connector login is not an application user.

| Identity | Allowed application actions |
| --- | --- |
| Unlisted or anonymous | No trading access |
| Owner | Read trading evidence; append explicitly unverified imported predictions |
| Worker | Read and append predictions/outcomes within trading |
| Owner or worker | No evidence update/delete/truncate, principal enrollment, registry promotion or NFL access through the trading entry points |

The terminal verifies the owner through Auth before every private route and
keeps its bearer outside serializable user objects. It uses fixed RPC names,
validates trading task scope and requires `TERMINAL_STORAGE_BACKEND=shared-trading-v1`.
Its owner data flow no longer requires a database service secret.

The Python store is an explicit opt-in implementation of the existing storage
interface. SQLite remains the default. Its worker token provider is supplied
by the caller; this change does not provision credentials or implement a
scheduled worker. Pagination uses a fixed receipt-time cutoff and validated
keyset cursors. It is not an exported database MVCC snapshot across requests;
reproducible evaluation should follow completed ingestion.

Shared evaluation uses the existing task scoring and change-proposal code.
Mixed-task calibration is rejected unless a task is explicitly selected.
Unverified imports cannot support learning proposals. Worker submission alone
is not proof of verified forward evidence. No updater applies changes, edits
risk limits, or implements the still-unfinished higher learning levels.

## Verification record

Local verification completed before publication:

- Combined Python regression gate: 372 tests passed (271 selflearn-core,
  60 execution-core, 5 signals and 36 tooling); walk-forward smoke passed.
- Shared database migration: 17 disposable PostgreSQL/PGlite security tests
  passed against an NFL-shaped existing database. The rollback-only verification
  script also passed after its final adjustment to remove temporary DDL.
- Terminal: 37 unit/build regression tests, the production build and one full
  mocked browser flow passed in GitHub CI on Node 22.23.4 at
  `e060b69c1e94886a6df94f94d5eb20a380b247a9`:
  https://github.com/liddar12/liddar-terminal/actions/runs/42924298479.
- The terminal implementation preview `dpl_BjjCbsRwiQ1uAr9X469vFA4kgZCe`
  is READY at that exact commit. Its hosted `/api/session` returns HTTP 503
  with `Owner authentication is not configured.`, the expected closed state
  before owner identity configuration.
- Independent database permission review found no blocking defect. It
  checked invoker functions, object/column grants, RLS, forced provenance,
  immutability and preservation of existing NFL objects.

The backend implementation is published at
`27b2ed9cb3891c9f1263812946929d9d8a01a308`, with source tree
`3fb85539fda45852b36b7f275cb59552bc2d7463` matching the reviewed local tree.
All six GitHub CI jobs passed, including the shared database job:
https://github.com/liddar12/SelfLearning/actions/runs/42924813163.

### Hosted migration receipt

The approved additive migration was applied to `omavgimpnvrpuxqhfxpf` through
the connected Supabase migration tool on November 10, 2026. The tool returned
success and migration history records:

- Hosted version: `20261110233747`.
- Name: `shared_trading_isolation`.
- Reviewed source: `tools/automation/supabase-shared/supabase/migrations/20261110232506_shared_trading_isolation.sql`.
- Source SHA-256: `ac6e87012246a46f62c0b97c527686cd6e4180b3116dd9942592b85dc1d39444`.

The hosted tool assigned its own version; it differs from the CLI-generated
source filename. This is the same reviewed SQL, not an unapplied second
migration. Do not blindly run `db push` against this folder or remove its
existing-object guard. Reconcile the recorded version mapping first.

### Hosted verification

- The complete DML-only `verify-rollback.sql` request passed its assertions
  using the actual hosted `anon`, `authenticated` and `service_role` roles.
  It exercised owner/worker operations, denied escalation, forced provenance,
  immutable evidence, NFL denial, task scope, receipt cutoffs and disablement.
- The response after `ROLLBACK` confirms all five new tables are empty.
  No synthetic principals, Auth users or evidence remain.
- Catalog inspection confirms five RLS-enabled tables and seven invoker RPCs.
  All seven allow authenticated execution and deny anonymous/service-role
  execution; enabled owner/worker counts are both zero.
- Original NFL relation, column, function and global-default-grant fingerprints
  exactly match their pre-migration values. Public table counts also match:
  one prediction and zero outcomes, registry rows or registry-score rows.
- Supabase security advisors returned the same 19 INFO findings for existing
  NFL tables with RLS enabled and no browser policies, and no warning/error
  findings. The new trading tables were not flagged. These existing denials
  were preserved, not loosened to silence the
  [informational advisor](https://supabase.com/docs/guides/database/database-linter?lint=0008_rls_enabled_no_policy).
- NFL production `/api/health` returned HTTP 200 with `status=ok`,
  `release=hosted-foundation-v1`, `mode=L0` after the migration. This is a
  routing/health check, not a complete NFL ingestion audit.

Hosted application Auth remains unverified: there are still zero application
Auth users and no enrolled principals. Database role tests and the mocked
browser flow do not establish a real owner session or unattended worker.

## Remaining private identity step

The preflight found zero Supabase application Auth users. Create the owner's
application login through the project's
[Auth user administration](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf/auth/users),
choosing the password privately. Agents can then verify the Auth UUID, enroll
the owner, configure public/server Auth settings on the terminal preview, and
verify the real session. Do not paste a password or token into chat.

A separate worker Auth identity and approved token refresh/secret mechanism
remain required before hosted Python ingestion can run unattended. Neither a
successful migration nor mocked sign-in tests prove those identities exist.

## Related records

- [Original access inspection and superseded project proposal](supabase-access-and-isolation-2026-11-10.md)
- [Vercel repair](vercel-production-repair-2026-11-10.md)
- [Current next steps](codex-grace-next-steps.md)
- [Service readiness](automation-service-readiness.md)
- [Mobile and Grace setup](mobile-grace-setup.md)
