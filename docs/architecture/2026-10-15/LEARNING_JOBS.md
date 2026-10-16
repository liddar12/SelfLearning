# Durable learning jobs and NFL shadow evaluation

Revision 1 · October 16, 2026, 11:56 a.m. America/Chicago (CDT).

The shared engine can now persist a bounded evaluation job, resume its saved
checkpoint with a replacement worker, and store an actual NFL shadow report and
typed artifact. This is a local Python and PostgreSQL-WASM implementation, not
a scheduled or deployed learning loop. The application still reports learning
jobs as not configured; model activation remains false.

## Job identity and state authority

A logical job ID binds configured authority, domain/task/producer namespace,
operation, immutable input and policy object IDs, and code identity. Manual and
ingestion-completion triggers must reuse that identity. Trigger times are not
part of it. A separate definition ID seals the original scheduling window and
attempt, step, lease and runtime budgets. Registering different controls for the
same logical job refuses with a definition conflict; it cannot silently extend
the original deadline or create a second run.

`LearningJobJournal` uses SQLite transactions for explicit local operation.
`SupabaseLearningJobJournal` uses three server-only RPCs and reconstructs every
transition from complete stored history before returning a result. Neither is
an automatic fallback for the other. The database file owner, configured server
credential and execution environment remain trusted; hashes are not signatures
or universal tenant authentication.

Job definitions and events are immutable and separate from forecast, outcome
and owner-intent tables. A claim's command ID is its lease ID. A replacement
claim gets a new ID, even for the same worker label. Every checkpoint, release,
failure and completion must extend the current head and carry the current
unexpired lease. Exact retries return their original acknowledgement before
checking the newer head, but that acknowledgement explicitly grants no current
lease authority and cannot resurrect an expired worker.

## NFL execution and recovery

The `nfl.shadow_calibration.v1` adapter accepts only its configured NFL
namespace. It checks immutable inputs, policy and the current package code ID,
then saves a preflight checkpoint. An explicit one-phase run pauses there;
a fresh worker can resume the same checkpoint. An intentional pause is refused
if it would consume the last available attempt and make completion impossible.

The next phase calls the real chronological `walk_forward_report`, serializes
its report and typed artifact, checks the code and lease again, and commits the
result reference through the current job head. Completed-job retries recover
the original stored result without another fit. No outcome, forecast, serving
configuration or approval is written. A completed job means the computation
finished, not that its statistical gates passed: an empty cohort completes with
an explicitly insufficient report and no artifact.

The code ID conservatively hashes every local selflearn-core Python file plus
interpreter/platform labels. Added or changed transitive helpers invalidate it.
It does not authenticate execution, interpreter/stdlib binaries or dependencies
outside that package. All source/label authority, live-loop, adoption and
accuracy-improvement indicators remain false.

## Limits and operational gaps

Metadata is strict ASCII JSON: 16 KiB per document and 256 KiB per complete
history. A definition permits at most eight claims, 32 work steps including
completion, a 1–300 second lease and a 1–3,600 second overall runtime. The runtime
starts at the first claim and includes downtime and pauses; retries do not reset
it. One step is reserved for completion, and an unchanged checkpoint cannot be
used merely to keep a lease alive. The maximum legal 47-event history is tested.

NFL inputs are bounded to 2,000 receipts and sources, 8,000 revisions, 256
generation records and 200–2,000 bootstrap replicates. The existing 3 MiB source
object transport limit is unchanged and can be stricter than the adapter's
16 MiB work profile; larger inputs need a separately reviewed delivery design.

Database clocks reject late state writes, but do not preempt a blocking fitter
or IO callback. Hard worker/process/cost limits, failure recording, bounded
trigger delivery, a distinct evaluator credential/API, GitHub Actions wiring,
backlog metrics and alerts remain S027 work. A lease can expire between immutable
object append and job completion, leaving an unreferenced object. Retention and
quota treatment remain required; exactly-once computation is not claimed.

## Database permissions and migration

CLI-created migration `20261016163115_learning_jobs_v1.sql` adds only the private
job tables, validation triggers and three RPCs. It changes no existing table or
grants. New tables enable and force RLS; anonymous/authenticated roles have no
table/RPC access. Service-role INSERT grants cover only input columns, so callers
cannot supply derived state, event IDs or database clocks. Mandatory triggers
also validate direct permitted-column inserts. Mutation and truncate guards
protect history. All 13 new functions use `SECURITY INVOKER`, an empty search
path and explicit execution grants, following the
[Supabase function security guidance](https://supabase.com/docs/guides/database/functions).

Per-job transaction advisory locks serialize short state transitions. Fitting
runs outside those transactions. The entire additive migration chain is exercised
together in the new PostgreSQL-WASM harness. Native concurrent transactions,
hosted PostgREST/credentials and production migration remain unverified. No
cloud migration or enablement was performed.

## Local verification and next integration

Focused Python verification: **54 passed, zero skipped, exit 0**, including four
actual Python-to-SQL tests and a synthetic 330-receipt real-fitter run. The
separate SQL suite passes **seven tests, zero skips, exit 0**, including RLS,
function privileges, derived-column refusal and a real no-truncate-trigger
check with both FK-related tables. Separate progress/coverage-tool tests pass
32/32. Broad regression and 43-target executable-line measurement are next;
these focused results do not replace their terminal outcomes.

| Behavior | Visible result | Evidence |
| --- | --- | --- |
| Duplicate trigger or changed deadline | One logical job; changed immutable controls refuse | Local and SQL registration tests |
| Worker dies after checkpoint | New process reopens the saved checkpoint; stale lease cannot write | Actual local process exit plus SQL expiry/reclaim cases |
| NFL evaluation resumes | Real report and artifact persist; completed retry does not fit again | SQLite and Python-to-SQL 330-receipt tests |
| Package helper changes | Pinned job refuses changed code | New/changed/removed helper and symlink tests |
| Private state is forged or mutated | Request fails; complete history still reexecutes | RPC tamper, direct INSERT and SQL catalog tests |

The next implementation must connect ingestion-completion and manual triggers
to this same canonical job definition, add hard execution bounds and safe
observability, and test failure/recovery with the intended worker. S027 tasks
remain partial; the audited total stays **50/99 local-build tasks (50.5%)**.
This is backend implementation under the existing R1 architecture, not a new
architecture package or model promotion.
