# Durable learning jobs and NFL shadow evaluation

Revision 3 · October 16, 2026, 12:50 p.m. America/Chicago (CDT).

The shared engine can now persist a bounded evaluation job, resume its saved
checkpoint with a replacement worker, and store an actual NFL shadow report and
typed artifact. An explicit v2 policy now runs the fixed fitter in a separate
bounded POSIX process, keeping database credentials in its parent. This is a
local Python and PostgreSQL-WASM implementation, not
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

This uses the existing MY-yardage `eligible_pool` / `offered_my` profile, not
Game/Week, moneyline or anytime-TD calibration. Dataset assembly from the
authoritative journal and its independent complete-cohort proof remain separate
integration work; uploaded immutable snapshots alone do not establish it.

The code ID conservatively hashes every local selflearn-core Python file plus
interpreter/platform labels. Added or changed transitive helpers invalidate it.
It does not authenticate execution, interpreter/stdlib binaries or dependencies
outside that package. All source/label authority, live-loop, adoption and
accuracy-improvement indicators remain false.

## Limits and operational gaps

### Bounded compute execution

`nfl_bounded_evaluation.run_bounded_shadow_job` requires a v2 policy with an
immutable execution profile. It never silently upgrades or falls back to the
legacy v1 inline worker. Profiles bound wall and CPU seconds to 1–300, request
bytes to 4 MiB, response bytes to 3 MiB and charged stderr to 16 KiB. The normal
profile uses 30 seconds. Effective compute time is capped by the earlier saved
server lease/runtime deadline, subtracting local preparation time. Parent
database calls are outside this compute deadline; their final lease checks
still reject late persistence.

The parent starts fixed trusted Python code with `-I -S -B -u`, an explicit
package root, a fresh working directory and only locale/timezone environment
variables. It passes no database credentials, storage path or uploaded command.
The child applies and reads back Unix CPU, zero core-dump and zero regular-file
growth limits before parsing the bounded input. The fitter uses exactly the
same source-pinned inputs, policy and chronological evaluation as v1. A v2
result binds the report to the job, definition, code and profile with pipe
hashes, effective budgets and observed limits. These are local observations,
not authenticated execution or clocks; those authority flags remain false.

The supervisor drains both output pipes while writing input and charges every
byte. Stderr content is discarded, including on success. It kills the owned
worker group and reaps its leader on success, refusal, timeout or cancellation, even if its
leader has exited and a descendant still holds a pipe. Ordinary compute
refusals return static reason codes and attempt a fenced `worker_error` release
while preserving the checkpoint. A changed/expired lease is never released by
the old worker. Cancellation propagates after process cleanup without a later
database write. An unsupported platform fails closed.

These are compute-process controls, not an arbitrary-code sandbox, aggregate
machine memory/cost ceiling or filesystem/network prohibition. OS process
creation itself may not be interruptible (see the
[Python subprocess timeout contract](https://docs.python.org/3.12/library/subprocess.html#subprocess.run)); late-created workers are killed and
their results refused. A deliberately escaping descendant is outside the
trusted-worker contract. Production worker/runtime support remains unverified.

### Durable job and dataset limits

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
Multiple forecast vintages and genuine multiweek input sets can exceed these
caps. They must use complete canonical-cohort/chunk contracts, not truncation
or a conveniently smaller sample merely to make a job pass.

V1 database clocks reject late state writes without preempting its inline
fitter. V2 adds the process controls above, but does not bound parent IO or total
memory/operating cost. Complete journal assembly, bounded trigger delivery,
a distinct evaluator credential/API, GitHub Actions wiring,
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

Source `419c73a057b8059a3c0d10065a958e14f2ad07a4` adds real child CPU termination
and file-growth refusal tests, real hung
worker and exited-leader descendant cleanup tests, credential isolation,
versioned-policy/receipt adversarial checks and original-result recovery.
After test hardening `e087541`, frozen broad verification completes with
terminal exit **0**:

- **1,353 core/API passed, zero skips**; **46/46 critical files >90%**,
  **8,181/8,492 = 96.3377%** executable lines, source/test/tool/head hashes stable.
- On source `419c73a`, **93 Node SQL passed, zero skips** across the full additive migration chain.
- On that same source, **101 Python-to-SQL passed / seven native-only skipped**, including five actual
  job-port cases and one isolated 330-receipt fitter with v2 result/receipt
  persistence and completed retry without another process. The existing
  dependency deprecation warning remains unsuppressed.

[Current coverage report](../../reviews/2026-10-16-critical46-bounded-compute-r2-coverage.json),
evidence commit `605afd4`, pins engine `e087541` and NFL dependency head
`383fbd63`. Runtime, services and PostgreSQL tests are byte-identical to the
earlier SQL-tested `419c73a`; those SQL suites were not rerun for test-only
hardening. Complete logs:
`/private/tmp/selflearning-critical46-bounded-compute-root-r2-20261016.log`,
`/private/tmp/selflearning-bounded-compute-all-node-sql-root-20261016.log` and
`/private/tmp/selflearning-bounded-compute-all-python-sql-root-20261016.log`.
Child execution is not counted by in-process line coverage; pure worker tests
with fake kernel limits and separate actual child enforcement tests serve
different purposes. Later documentation/evidence commits are not new tested
release SHAs. The earlier focused 167-test run preceded the final static parent
directory refusal case; the broad run includes that case too.

Final evidence review tightened the dedicated resource tests in `e087541`:
the CPU case must now observe a marker created after successful limit readback
and termination by SIGXCPU/SIGKILL after the busy loop starts. An import/resource
setup error can no longer count as CPU enforcement. The file-growth case must
observe EFBIG, not just any failed write. These 40 worker tests and the final
**1,353-test** core/API rerun pass, exit 0. The [earlier 419c73a report](../../reviews/2026-10-16-critical46-bounded-compute-coverage.json)
and `c0b552a` evidence remain the earlier test inventory, not silently relabeled
with the stronger proof.

### Earlier v1 checkpoint

Focused Python verification: **54 passed, zero skipped, exit 0**, including four
actual Python-to-SQL tests and a synthetic 330-receipt real-fitter run. The
separate SQL suite passes **seven tests, zero skips, exit 0**, including RLS,
function privileges, derived-column refusal and a real no-truncate-trigger
check with both FK-related tables. Separate progress/coverage-tool tests pass
32/32. Subsequent frozen broad verification at engine
`d3a5693a6dbed2436f00191a89f93257cde8ef70` completes:

- Core/API: **1,232 passed, zero skipped, pytest exit 0**.
- Critical executable-line coverage: **43/43 files >90%**,
  **7,884/8,194 = 96.2167%**, with measured source/test/tool/head hashes stable.
  Four new modules reach 404/404 executable lines; this is not branch coverage.
- Complete Node SQL: **93 passed, zero skipped, exit 0**.
- Complete Python-to-SQL: **100 passed / seven native-only skipped, exit 0**.
  One existing dependency deprecation warning remains; it was not suppressed.

[Coverage report](../../reviews/2026-10-16-critical43-durable-jobs-coverage.json),
committed as `59d54a6`, pins the NFL dependency checkout to
`383fbd6388084103e54628bdcfd4f1c03cc3d7d6`. Logs are
`/private/tmp/selflearning-critical43-durable-jobs-root-20261016.log`,
`/private/tmp/selflearning-durable-jobs-all-node-sql-root-20261016.log` and
`/private/tmp/selflearning-durable-jobs-all-python-sql-root-20261016.log`.
Later evidence/documentation commits are not new exact-tested release SHAs.

| Behavior | Visible result | Evidence |
| --- | --- | --- |
| Duplicate trigger or changed deadline | One logical job; changed immutable controls refuse | Local and SQL registration tests |
| Worker dies after checkpoint | New process reopens the saved checkpoint; stale lease cannot write | Actual local process exit plus SQL expiry/reclaim cases |
| NFL evaluation resumes | Real report and artifact persist; completed retry does not fit again | SQLite and Python-to-SQL 330-receipt tests |
| Package helper changes | Pinned job refuses changed code | New/changed/removed helper and symlink tests |
| Private state is forged or mutated | Request fails; complete history still reexecutes | RPC tamper, direct INSERT and SQL catalog tests |

The next implementation must assemble complete authoritative journal cohorts,
connect ingestion-completion and manual triggers to this same canonical job
definition, add scoped evaluator authority and safe observability, and test
failure/recovery with the intended hosted/native worker. S027 tasks
remain partial; the audited total stays **50/99 local-build tasks (50.5%)**.
This is backend implementation under the existing R1 architecture, not a new
architecture package or model promotion.
