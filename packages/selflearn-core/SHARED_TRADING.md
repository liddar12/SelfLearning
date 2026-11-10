# Shared project storage and learning

`selflearn_core.store.TradingSupabaseStore` is an opt-in implementation of the
existing `StorageBackend`. `SqliteStore` remains the default. The new adapter
addresses only the fixed `trade_bot_*` RPCs backed by the separate trading
schema; it has no direct-table path or fallback to the shared NFL tables.

## Prerequisites and credentials

The reviewed `shared-trading-v1` database migration must be applied. A real
Supabase Auth user must be privately provisioned and enabled as a worker in
the trading principal allowlist. Owner sessions cannot use this worker
adapter. Database setup alone does not create an Auth account or session.

The constructor requires the standard HTTPS project URL, its explicit expected
project reference, a publishable API key (or legacy anon key), and a callable
`access_token_provider`. The callable must return a current authenticated-user
access token for that same project on each request. The calling application
owns private sign-in, refresh and secret storage; this adapter neither obtains
nor persists sessions. A long-running worker is not ready until that session
integration is configured and tested.

Elevated project keys and service-role bearer tokens are rejected. Local JWT
claim checks narrow the accepted credential shape; they do not authenticate
the user. Supabase validates signatures and the database enforces the worker
allowlist and permissions. No secret values or upstream error bodies appear
in adapter errors.

`check_ready()` verifies the RPC schema version and worker principal. Store
operations check this before requesting their fixed RPC. HTTP requests have
timeouts and response-size bounds, refuse redirects, and are never retried.
A write timeout can occur after a successful insert. The caller must reconcile
the record ID before deciding whether to retry; the adapter does not infer
that a transport failure means no record exists.

## Evidence reads

Resolved and unresolved scans use descending database receipt time plus ID
keyset pagination, a fixed database-issued receipt cutoff, bounded pages and
strict task/row/relationship checks. Invalid pages or a page-limit overflow
raise an error instead of returning partial results. Supported trading tasks
are `scanner`, `trade_decision`, `price_action_trend`, and the `trading.*`
namespace. NFL tasks are rejected by this adapter.

The receipt cutoff is **not an exported PostgreSQL MVCC snapshot** across
multiple HTTP requests. A transaction can assign a receipt before the cutoff
and commit later. Repeatable evaluations therefore require ingestion to finish
and pending transactions to settle, or a separately persisted evidence
manifest. This adapter does not claim to solve concurrent evaluation snapshots.

## Shared learning, separate evidence

`fit_calibrator(records)` retains its existing single-task behavior. It now
rejects mixed-task input unless the caller explicitly supplies `task=...`.
This prevents accidental NFL/trading pooling; a numerical fit still does not
verify provenance or authorize promotion.

`selflearn_core.scoring.evaluate_tasks` reuses the current task/cohort/window
scorer and optional update policy for explicitly named tasks. Each returned
`TaskEvaluation` distinguishes descriptive scores from eligible-evidence
scores and pending proposals. Monitoring is the default policy. No registry
mutation, risk change or automatic promotion occurs.

Evidence is eligible only when a caller-supplied independent verifier returns
true. That verifier must check trusted forecast/observation/receipt and outcome
evidence; a boolean supplied in record metadata is not verification. Without
a verifier, all results remain descriptive. Terminal imports are excluded
even if a supplied verifier accepts them. A worker submission is not by itself
proof of verified forward generation. The actual forward-evidence verifier
and durable runtime orchestration remain separate integration work.

The existing L2 ensemble, L3 prompt optimization and L4 meta-model policies
remain unimplemented. This change does not train foundation-model weights,
enable trading schedules or grant broker authority.
