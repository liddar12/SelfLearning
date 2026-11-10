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

Hosted application Auth remains unverified: the preflight found zero Auth
users and no principals have been enrolled. The hosted migration, rollback-only
permission test, catalog comparison and exact-commit CI results are recorded
below when completed. Local fixtures are not evidence of hosted sign-in or
an unattended worker.

## Remaining private identity step

The preflight found zero Supabase application Auth users. Create the owner's
application login through the project's normal Auth administration flow,
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
