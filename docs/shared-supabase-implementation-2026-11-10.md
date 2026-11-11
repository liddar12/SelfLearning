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
The migration creates an empty allowlist; one verified owner is now enrolled.
Real Auth identity verification must precede enrollment; a Supabase dashboard
or connector login is not an application user.

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
- Initial terminal implementation: 37 unit/build regression tests, the production build and one full
  mocked browser flow passed in GitHub CI on Node 22.23.4 at
  `e060b69c1e94886a6df94f94d5eb20a380b247a9`:
  https://github.com/liddar12/liddar-terminal/actions/runs/42924298479.
- Before owner configuration, terminal preview
  `dpl_BjjCbsRwiQ1uAr9X469vFA4kgZCe` was READY at that exact commit and its
  hosted `/api/session` returned HTTP 503 with
  `Owner authentication is not configured.` The later owner setup below
  supersedes that configuration status.
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
- The response after `ROLLBACK` confirmed all five new tables were empty
  before real owner enrollment. No synthetic principals, Auth users or
  evidence remained from the test.
- Catalog inspection confirms five RLS-enabled tables and seven invoker RPCs.
  All seven allow authenticated execution and deny anonymous/service-role
  execution. The allowlist was empty at this migration-verification stage;
  later real owner enrollment is recorded below.
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

### Owner enrollment and preview configuration

Jimmy created the private application login. A fresh Auth read found one
confirmed, non-anonymous application user. Agents verified that identity and
enrolled it as an enabled trading owner with a guarded write. Current enabled
principal counts are **one owner and zero workers**. Trading prediction,
outcome, registry and registry-score tables remain empty.

Six public/server configuration entries were created and read back on Vercel
project `liddar-terminal-2tpb`, scoped only to Preview and branch
`codex/terminal-owner-auth`: `VITE_SUPABASE_URL`,
`VITE_SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_URL`,
`SUPABASE_PUBLISHABLE_KEY`, `TERMINAL_OWNER_USER_ID`, and
`TERMINAL_STORAGE_BACKEND=shared-trading-v1`. Browser and server point to the
same selected project and publishable key. No project service/secret key is
used, and production configuration was not changed.

A fresh Git preview, `dpl_2WztU8hPjRpzV3g57XkutZkJBhnd`, reached READY at
`e060b69c1e94886a6df94f94d5eb20a380b247a9`. The owner entered credentials
privately in the preview's sign-in form, and Supabase recorded a successful
sign-in. The terminal then displayed `Verification unavailable`. Its request
transport omitted the cookie required by Vercel Preview protection. The fix
preserves that same-origin cookie while retaining bearer authorization,
local-path validation, no-store caching and redirect rejection. The repaired
preview passed the real owner acceptance below.

An authenticated-role database check using the verified owner's identity
returned the shared-trading status and empty evidence lists; direct NFL-table
read and principal-insert privileges remained denied. This is a database
authorization check, separate from the hosted browser acceptance. The
protected-deployment fetch tool's HTTP 401 also did not establish application
denial, because it stopped at Vercel protection. Direct browser navigation
with preview access reached the application's `Sign in to continue.` response.

### Hosted owner acceptance: passed

| Evidence | Verified target/result |
| --- | --- |
| Terminal commit | `344921dd0da7a3b31737b40dbfd00cfab2bfa581` |
| Source tree | `0b1fc83b7a0627d7bd5d72bb81c398480095c317` |
| GitHub CI | [Run 42929541393](https://github.com/liddar12/liddar-terminal/actions/runs/42929541393): 37 tests passed, zero failed; production build and one cookie-gated mocked browser flow passed on Node 22.23.4 |
| Vercel preview | `dpl_G1Axg1jq3WnBSmuwjYgGnPvdrYas`, READY at the same commit |
| Immutable preview URL | https://liddar-terminal-2tpb-8p99j7x3w-liddar-terminal.vercel.app |
| Observed branch alias | https://liddar-terminal-2tpb-git-codex-terminal-cf1c5b-liddar-terminal.vercel.app/ |

With authorized Vercel preview access, the owner entered credentials through
the private browser sign-in mechanism; credential values were not exposed.
The following real hosted path passed:

1. Sign-in displayed the private terminal panels and **Sign out** control.
2. **Scores** reached the real shared trading RPC and displayed the empty
   resolved-predictions state.
3. Reload preserved the owner session; opening Scores again returned the same
   valid empty state.
4. Sign-out returned the login screen, and another reload remained signed out.
5. Direct browser navigation to `/api/session` with preview access but no
   application bearer returned `{"error":"Sign in to continue."}`.

The last observation records the application response body, not a browser-read
HTTP status. Wrong-owner denial passed the cookie-gated mocked CI flow; no
second real Auth user was created for a hosted wrong-owner test. The real
acceptance was read-only and created no prediction/outcome records. Imports
remain retained append-only evidence, not disposable test rows.

Final read-only checks confirmed the expected owner remains enabled, with one
owner, zero workers and zero rows in each of the four trading evidence tables.
NFL production health again returned HTTP 200 with `status=ok`,
`release=hosted-foundation-v1` and `mode=L0`.

Both URLs retain Vercel Preview protection. The branch alias can move with
future commits; the immutable URL identifies this tested deployment. Temporary
share-link tokens are deliberately excluded from this record. Normal private
sign-in may be needed when opening the preview in another browser.

## Remaining environment delegation and worker identity

Owner creation, enrollment, preview configuration and the scoped hosted owner
flow are complete. Do not recreate the owner account or paste passwords or
tokens into chat, logs or the repository. The next owner steps are publishing
the shared Codex environment and proving Grace's first delegated task using
the [mobile/Grace checklist](mobile-grace-setup.md).

A separate worker Auth identity and approved token refresh/secret mechanism
remain required before hosted Python ingestion can run unattended. Neither a
successful migration nor owner sign-in proves that worker identity exists.
Grace's environment access and delegated Codex task remain unverified. No
worker, trading schedule, live order or production promotion was enabled by
owner setup.

## Related records

- [Original access inspection and superseded project proposal](supabase-access-and-isolation-2026-11-10.md)
- [Vercel repair](vercel-production-repair-2026-11-10.md)
- [Current next steps](codex-grace-next-steps.md)
- [Service readiness](automation-service-readiness.md)
- [Mobile and Grace setup](mobile-grace-setup.md)
