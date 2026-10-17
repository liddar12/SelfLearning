# Owner-intent journal — local implementation boundary

Revision 6 · October 16, 2026, 9:50 p.m. America/Chicago (CDT).

This additive journal records an operator's intent. It does **not** approve a
statistical conclusion, activate an NFL calibrator or change serving. Local
implementation and tests do not establish a deployed Supabase connection.

## Local NFL consumer follow up

Engine `405a84f` adds a ReceiptJournal-compatible `get_object` to the file port.
It independently verifies bounded exact evidence bytes, object identity and
receipt time, and returns a detached document. A real synthetic 330-receipt
composition test runs the existing NFL evidence reader/fitter; its v1 diagnostic
still holds adoption and preserves all false activation flags. Sixteen file-port
and composition tests pass, exit 0.

NFL `5c11a6af` adds a separate v2 Week 6 scope and actual local producer consumer.
It reexecutes saved raw/frozen evidence before applying an explicitly approved
fixed candidate, stamps new receipts, supports repeated future refreshes and
explicit compatible rollback, and preserves old forecasts. Revocation does not
need the old fitter/raw history or a healthy current pool. Two real-producer
scenario tests, including 21 refusal cases, pass; a separate real local Git test
holds publication until the exact-tree v2 semantic gate exists.

This is trusted-local-operator integration, not authenticated hosted approval,
public serving or accuracy evidence. Production/default workflow activation,
final Git publication/conflict handling, UI effective version/expiry and browser/
fresh-pipeline acceptance remain incomplete. Engine `74a31a6` pins 36 NFL
provenance files, including the new consumer. The full frozen pair with NFL
`5c11a6af` finishes exit **0**: **1,379 core/API pass**, zero skips,
**49/49 critical files >90%**, stable **96.4906%** executable lines; file port
179/179. NFL passes **2,496 features**, zero failures/skips, contracts/smoke/four
model gates; browser step 8 skips. [Report](../../reviews/2026-10-16-critical49-period-return-coverage.json).
The earlier full measurements below retain their exact revisions. S015 remains
partial; whole-task credit stays 50/99.

## Repository compatible file persistence

Local engine `19a80d9` adds `store/file_owner_intents.py` for the owner-selected
Actions-first pilot. It reuses the existing generic version-1 command, event,
history and acknowledgement validators; it does not broaden an NFL approval's
scope. The configured application owns the directory, actor, authority, clock
and exact-byte evidence reader. No token, domain adapter or environment setting
is discovered by the port, and construction/empty reads create no repository.

Events are canonical, content-addressed immutable files. A small per-authority/
namespace head is replaced last under a nonblocking cooperating-process lock.
Readers pin one head and validate the complete chain. A missing or altered event
fails the read; a late append does not change an already pinned snapshot. Exact
retry returns the original historical acknowledgement without consulting a new
clock or evidence source, and does not undo a later revocation. Directory sync
also handles a lost reply after head replacement. New evidence is checked before
publishing a head. An interrupted event write may leave an unreferenced event;
if head replacement completed before a later sync failure, retry recovers the
original event without appending another. Owned temporary files are cleaned up.

The port's `durable` acknowledgement means local filesystem persistence, not
survival of a new Actions runner, a Git push, hosted storage or current approval.
The caller must publish the complete files through the controlled data pipeline.
File ownership is part of the trusted boundary; hashes are not an authenticated
operator signature or protection from a privileged writer restoring an old
valid head. The local NFL consumer adds domain binding separately; publication
and authenticated approval workflow remain separate work.

Limits retain 256 events and the existing roughly 3 MiB complete-history budget;
events are at most 128 KiB plus 4 KiB framing. Evidence is bounded to 128 MiB per
returned byte body and 256 MiB total per new append, with at most 16 references.
These are not global memory, disk, retention or execution-time ceilings. A full
or unavailable authority must hold activation; do not claim that a read alone
proves capacity for a subsequent revoke/rollback.

**14 new file-port tests** and **112 related checks** pass, zero skips, exit 0;
the latter includes 32 ledger/coverage-tool checks outside the core/API suite.
Tests include actual separate-process lock/restart, a complete 256-event chain,
stale-head conflict, old retry after revoke, failed sync/rename, pointer-race
refusal, missing/altered/nonregular files and malformed evidence. Full frozen
engine `19a80d9edf67bbf4d8da10edcbd702435b3b31b4` finishes exit **0**:
**1,377 core/API tests pass**, zero skips, **49/49 critical files >90%**,
**8,543/8,855 = 96.4766%** executable-line coverage, stable source/test/tool/head
identities. The new file port reaches **171/172 = 99.4186%**; this is line,
not branch or production acceptance. [Pinned report](../../reviews/2026-10-16-critical49-file-owner-coverage.json)
uses NFL dependency `7808de815f5b17a20b12c920b44d8deac8d71ceb` and 35 source
hashes. No S015 whole-task or release acceptance is awarded by this persistence layer.

The owner selected one exact candidate for eligible future **2026 Week 6**
refreshes. That requires a separately versioned NFL scope and consumer, not
reinterpretation of existing exact-generation events. The current diagnostic
reader below stays read-only and always held.

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
authority/namespace. A new event must also keep canonical ASCII history within
3,137,536 bytes, leaving 8 KiB for complete-snapshot framing inside its 3 MiB
limit. Unicode escaping, keys and containers are counted before serialization.
The count ceiling is not a promise that every maximal-size command fits.
Read pages have at most 100 events and a 3 MiB response limit. The existing
application boundary independently bounds actual streamed
request bytes to 256 KiB. Capacity refusal does not remove old history. These
limits are not a total disk-quota, retention or garbage-collection policy.
Capacity can prevent any new intent, including revocation; no silent rollover
or deletion is implemented. Before any adoption integration, the consumer must
define fail-closed handling for an unwritable authority instead of implying that
revocation is always available or falling back to an older approval.

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
unchanged negative regression passes. Migration SHA-256 at that checkpoint:
`68be3ab4c02801ceb70fa4046c820e0e703fbe0c531c0015b8b9d3a66263cf98`.

Independent SQLite checks include real separate-connection competing writers
(one saved head, one conflict), complete 256-event history, original retry at
capacity, separate namespaces and post-expiry revocation. SQLite concurrency
and PostgreSQL-WASM are not native Postgres concurrency or hosted authorization
proof. These focused additions do not replace a fresh complete SQL regression
run, current-main reconciliation or release/browser acceptance.

### Independent follow-up after the first checkpoint

The complete SQL rerun passed at the first foundation revision: 85 Node tests
(no skips) and 96 Python tests (seven native-only skips), both exit 0. SQL and
test harnesses were unchanged throughout those runs.

Further independent review reproduced three defects: typed provider errors
could contain private status/message text; aliased oversized inputs could
allocate their fully expanded JSON before the cap; and large Unicode reasons
could exhaust the full-history read budget before the event-count limit. The
follow-up reconstructs public errors with only bounded integer statuses and
static text, charges canonical bytes before encoding, and refuses unreadable
new heads before inserting or consuming a Postgres sequence value. Original
retries still precede capacity, expiry and current-head checks.

Root full Python suite at the hardening checkpoint: 1,156 passed, none skipped, exit 0. Independent
29-case follow-up and related core/API set: 97 passed, exit 0. A seeded genuine
230-event SQLite prefix remains readable; its overcapacity append fails without
a write and the first original acknowledgement is still recoverable. The SQL
analogue verifies pages, both RPC/direct-insert refusal and unchanged rows,
watermark and sequence. No history is silently truncated.

An initial worst-capacity local PostgreSQL-WASM RPC took 12,204.860 ms, exceeding
the existing 12-second HTTP budget. Removing duplicate validation in the new
public function reduced the measured local rejection to 7,297.118 ms. New writes
still undergo complete validation in the mandatory insert trigger under the
same lock; exact retries validate history before returning their old result.
All 12 focused SQL tests pass. These are in-process local timings, not native,
HTTP or hosted Supabase performance proof; the timeout was not increased.

The 1,127-test/38-file report above is retained as a **pre-follow-up** checkpoint.
The fresh hardened run at engine `45b2a93` passes **1,156 core/API tests, zero
skips**, **38/38 critical files >90%**, **7,262/7,568 = 95.9567%** executable
lines, with stable measured source/test/tool/head hashes. It uses detached NFL
`398d3b54`; current injury/hook edits are not included.
[Hardened storage report](../../reviews/2026-10-16-critical38-owner-hardened-coverage.json).
Complete hardened SQL reruns pass **86 Node tests, no skips**, and **96 Python
tests / seven native-only skips**, exit 0. The storage foundation is local;
domain authorization, a live consumer, native concurrency and hosted release
acceptance remain incomplete.

## Typed NFL shadow-evidence reader — local, always held

Engine `58ea096` adds `adapters/nfl_owner_evidence.py`, separately documented in
[NFL_OWNER_SHADOW_EVIDENCE.md](NFL_OWNER_SHADOW_EVIDENCE.md). Explicitly configured
authority/actor/runtime readers load and rehash the complete owner history and
seven-role immutable evidence manifest, then reexecute the actual NFL fitter
rather than trust an uploaded PASS. End reads detect changed owner/runtime/code
pins. They are **not atomic adoption/CAS** and leave a final read/use race; no
production API, workflow or NFL serving consumer is connected.

Every result remains held: authenticated complete source/label authority,
prospective evidence for the fixed artifact, atomic NFL consumption and durable
write capacity are not established. Generic recorded intent and original retry
after revoke are never treated as current approval. Fit/report/content agreement
can be true while adoption, activation, serving and release flags stay false.

The 26 new cases use real SQLite and synthetic 330-receipt fitter inputs. A
complete source-frozen run records **1,182 core/API tests passed, zero skipped,
exit 0**; **39/39 critical files >90%**, **7,480/7,790 = 96.0205%** executable
lines, report committed as `5314ca8` with measured engine `58ea096` and detached
NFL `27b43fd5`. [New report](../../reviews/2026-10-16-critical39-owner-evidence-coverage.json).
The older SQL suites do not establish this new reader through the Supabase
port, native transactions, hosted authentication or a working adoption loop.
No S015 task/story credit is granted for this foundation.
