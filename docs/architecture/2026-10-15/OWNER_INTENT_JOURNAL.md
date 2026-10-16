# Owner-intent journal — local implementation boundary

Revision 1 · October 16, 2026, 2:26 a.m. America/Chicago (CDT).

This additive journal records an operator's intent. It does **not** approve a
statistical conclusion, activate an NFL calibrator or change serving. Local
implementation and tests do not establish a deployed Supabase connection.

## What owns each decision

| Boundary | Owner and authority | What is not established |
| --- | --- | --- |
| HTTP authentication | Existing application `OwnerBoundary`; operator bearer only | Supabase Auth identity, multiple-user isolation or a signed owner event |
| Actor, authority and namespace | Explicit trusted server factory configuration | Caller-supplied actor/authority fields; a producer credential cannot substitute |
| Immutable event and receipt clock | SQLite transaction or Postgres trigger | Provider-issued evidence time or actual serving state |
| Evidence integrity | Existing `source_snapshot` object, rehashed inside the append transaction | Candidate/report/artifact semantics; a content hash is not a signature |
| NFL compatibility and adoption | Still incomplete, separate domain consumer and release workflow | No model is selected or promoted by this journal |

The SQLite database-file owner and the backend service credential remain in the
trusted computing boundary. This is a single-owner pilot, not a universal
tenant-authorization system.

## Implemented API

`GET /api/v1/owner-intents` returns the complete pinned history for the configured
authority and exact `{domain, task, producer_id}` namespace. `POST` accepts one
strict version-1 command. Both routes require the operator credential and return
`Cache-Control: no-store`. Forecast and outcome producer keys cannot use either
route. The MCP interface remains four read-only tools; no MCP approval tool was
added.

The application accepts only an explicitly injected `owner_intent_factory`.
There is no environment-variable enable switch, backend discovery, ephemeral
fallback or startup write. With no factory the authenticated route returns 503.
Setting a Supabase key alone does not enable it. The current production entry
point has not been configured to supply this factory.

A command binds its action, expected head, candidate, artifact, report, policy,
generation context, expected prior-current identity and 1–16 existing evidence
object hashes. Its content identity also binds the configured authority. The
server owns the actor reference, event time, namespace sequence and parent.
Commands cannot supply event metadata or choose another configured namespace.

## State and recovery

- A new command must compare-and-swap the exact current namespace head. A
  competing changed command returns conflict without creating a branch.
- An exact retry returns the original historical acknowledgement before testing
  the current head, current capacity or original expiry again. That
  acknowledgement is **not** a statement that the old approval is still current.
- Revocation is immediate and permanent for that event; it can be recorded after
  the previous intent expires. Consumers must never fall back to an older
  approval when the current intent is revoked, unavailable or invalid.
- Rollback intent requires a previously approved, non-revoked target in the same
  namespace and matching candidate/artifact/report/policy pins. It records an
  intent only; it does not restore a serving artifact.
- Complete reads pin a separate owner-journal watermark and validate every
  event's hash, parent, sequence, scope and chronology. Missing, truncated,
  reordered or conflicting history cannot become a complete successful read.
- A lost reply is recovered by retrying the identical command. If a backend
  acknowledgement is malformed, the API reports failure even if the write
  committed; the exact retry recovers the original event without another write.

Owner events use their own ledger and do not create forecasts, outcomes,
training labels or model-adoption records. Evidence objects must already exist
in the shared immutable object store; staging those objects is a separate action
and may advance that store's own watermark.

## Limits and failure behavior

Commands are limited to 128 KiB, references to 16 and history to 256 events per
authority/namespace. Read pages have at most 100 events and a 3 MiB response
limit. The existing application boundary independently bounds actual streamed
request bytes to 256 KiB. Capacity refusal does not remove old history. These
limits are not a total disk-quota, retention or garbage-collection policy.

The additive Postgres migration uses invoker functions, an empty search path,
private RLS-enabled tables, service-role input-column-only inserts,
server-computed output columns and mutation/truncate refusal. A pilot-wide
advisory lock serializes writes and pinned reads under READ COMMITTED. This
simple lock is a throughput tradeoff; native concurrency still needs its own
acceptance test. No cloud migration has been applied by this work.

Failures use static public messages. No SQL, source documents, bearer keys or
provider diagnostics are included. Disabled, unavailable, bad-origin,
wrong-scope, oversized, malformed, stale-head and capacity states are distinct.
Cancellation is not reported as a successful journal acknowledgement.

## Required next integration, not completed by storage

1. Stage the exact NFL candidate/report/artifact/context evidence and verify the
   domain meaning of every referenced hash. Re-execute required evaluation from
   trusted pinned inputs; do not trust an uploaded `passed: true` field.
2. Read the real configured owner head at the point of use. Check freshness,
   effective/expiry times, revocation, exact scope, expected incumbent and the
   current code/base-model compatibility. A JSON snapshot alone is not authority.
3. Materialize only a compatible, approved artifact for a new generation. Apply
   calibration once from immutable base probabilities, stamp its effective
   version and preserve every old receipt/revision/evaluation byte.
4. Exercise two feedback cycles, outage hold, revocation and compatible rollback
   through the actual producer consumer. The existing non-active materializer
   draft is not that operational consumer.
5. Verify native Postgres concurrency, hosted grants/PostgREST, real private
   Storage, source completeness and browser acceptance. Reconcile both current
   repository heads and release only the exact separately authorized revision.

No S015 whole-task completion, improved Week 5 result or prospective accuracy
gain is awarded merely for this storage/API foundation.

## Local verification checkpoint

The root rerun completed with exit 0: 1,127 core/API tests passed, none skipped;
38/38 designated critical Python files exceed 90% executable-line coverage,
7,235/7,538 = 95.9804% overall. All measured source, test, tool and repository
head identities were stable during the run. The report records the exact dirty
source hashes as well as its pre-commit repository head; that head alone does
not identify the new implementation. Evidence:
`docs/reviews/2026-10-16-critical38-owner-coverage.json`.

Independent focused SQL checks: 11 passed, none skipped, exit 0. Actual Python
port/application-to-PostgreSQL-WASM checks: 3 passed, none skipped, exit 0. An
independent raw-RPC test initially reproduced SQL accepting `24:00:00` while
Python rejected it. The SQL regex now limits hours/minutes explicitly, and the
unchanged negative regression passes. Final migration SHA-256:
`68be3ab4c02801ceb70fa4046c820e0e703fbe0c531c0015b8b9d3a66263cf98`.

Independent SQLite checks include real separate-connection competing writers
(one saved head, one conflict), complete 256-event history, original retry at
capacity, separate namespaces and post-expiry revocation. SQLite concurrency
and PostgreSQL-WASM are not native Postgres concurrency or hosted authorization
proof. These focused additions do not replace a fresh complete SQL regression
run, current-main reconciliation or release/browser acceptance.
