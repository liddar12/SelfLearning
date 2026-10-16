# NFL owner-journal evidence binding

Revision 1 · October 16, 2026, 10:35 a.m. America/Chicago (CDT).
Local read-only implementation. No route, workflow, production configuration,
database migration or model activation is enabled by this component.

## What now works locally

`selflearn_core.adapters.nfl_owner_evidence.NFLOwnerEvidenceReader` reads the
actual explicitly configured owner journal, rather than the older NFL
materializer's incompatible flattened `snapshot_claim.v1` JSON. Generic owner
storage remains domain-neutral; NFL artifact/evaluation semantics live in this
adapter. Nothing imports an NFL checkout into generic persistence or scoring.

The configured single-owner authority and actor are checked against the complete
typed history. The latest event must be an effective approve/rollback intent
for the exact manifest, not an old successful acknowledgement. A fresh read
after a real revoke holds even when the original command retry returns its old
ACK. Configured ports, their credentials, local database-file owner and runtime
callback are in the trusted computing boundary; hashes are not signatures or a
universal cross-owner authentication system.

One manifest maps seven named roles to exact immutable `source_snapshot`
objects: candidate, report, artifact, policy, future-generation context,
incumbent claim and evaluation inputs. Its own object plus exactly those role
objects must equal the owner's reference set. The reader rehashes every object
and checks its database receipt time is no later than the recorded intent.
Preparation returns objects/pins only; it never stages or approves them.
Inputs are strict plain JSON. In-process tuple conveniences in a fitter report
must take the same explicit JSON round trip used by stored/API reports first.

The typed base-logit artifact, exact report/candidate/policy/context identities,
time window and expected incumbent are checked. The engine source identity is
computed from this adapter and actual fitter source, not accepted as an arbitrary
label. That identity is explicitly narrower than deployed execution, interpreter
or complete transitive-dependency authentication. NFL/base/context/incumbent
pins match a trusted program callback; this is not a read of actual NFL serving.

The reader calls the real `walk_forward_report` on the exact stored inputs and
compares the complete canonical report. Rehashing a fabricated better metric or
setting a success flag cannot pass that reexecution. It rereads the real owner
head and runtime configuration at the end and holds if either changed.

## Why every result still remains held

A successful diagnostic sets only content/domain-binding, numerical-reexecution
and runtime-configuration-match indicators. It keeps all authorization,
adoption, activation, current-serving, release, working-loop and independent
source/label-authority indicators false. It returns explicit reasons:

- The current report evaluates a **refitting policy**, not prospective
  performance of its final fixed coefficients.
- The stored input's complete source/observation authority and independent raw
  NFL cohort parity are not established by its object hash or this replay.
- No atomic NFL owner-head/incumbent compare-and-swap adoption consumer exists.
  Two reads detect changes during verification but cannot remove the final
  read-use race or authorize publication after a subsequent revocation.
- Owner-journal write capacity/availability is not proven by a successful read.
  Capacity may block a revocation. A known 256-event ceiling is additionally
  reported, never silently rolled over or replaced by older approval.

No diagnostic is an executable serving instruction. There is no app/API/MCP
hook, environment discovery, ephemeral fallback, remote URL input or automatic
activation. Ports may perform their own IO; the adapter implements no network
or writes. BaseException cancellation propagates, and callback failure text is
replaced with allowlisted static public codes.

## Limits and evidence

Plain JSON is bounded before expanded serialization: 16 MiB/document, 64 MiB
aggregate object payloads, 500,000 nodes and depth 24. Evaluation inputs are
limited to 2,000 receipts and source receipts, 8,000 revisions, 256 generation
records and 200–2,000 deterministic bootstrap replicates. Larger inputs hold;
they are not truncated into an artificially passing cohort. These are work
bounds, not a hard overall cancellation or hosted latency guarantee.

Focused current checks: **26 new tests passed, zero skips**, using real local
SQLite owner events and immutable objects plus the actual synthetic 330-receipt
fitter. The related owner/core/API set passes **85 tests**, zero skips, exit 0.
Tests cover genuine revoke/original retry, forged rehashed metrics, mid-evaluation
head/runtime changes, actor/scope/content/expiry/type errors, no journal/training
mutation, pre-encoding alias expansion, static errors and cancellation.

The new adapter is added to the 39-target critical inventory. Full current
core/API coverage, native/hosted/Supabase-port integration, independent new-source
review and an actual NFL consumer are not established by those focused tests.
This component earns no S015 whole-task or accuracy credit on its own.
