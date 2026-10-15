# NFL receipt adapter and local journal

Implemented checkpoint — October 15, 2026 (America/Chicago). This is local
capture/persistence code, not a hosted deployment or enabled learning loop.

## Ownership and implemented boundaries

NFL2026 owns player/game identity, eligibility, kickoff evidence, frozen model
features, settlement and its immutable generation/source snapshots. SelfLearning
owns generic sealed receipts, immutable storage, revision history and scoring.
Neither a digest nor a caller's timestamp proves public availability.

The supported markets remain MY QB passing, RB rushing and WR receiving yardage
props. Game/Week parlays, ATD, moneylines and whole-card joint probabilities are
not supported by this adapter. Existing `adapt_my_cards(...)` remains the
settled-only offline compatibility interface. It is not the prospective intake.

## Prospective API

```python
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.store.receipt_journal import ReceiptJournal

with ReceiptJournal("/explicit/local/path/receipts.sqlite") as journal:
    batch = adapt_prospective(
        journal_docs=immutable_nfl_generation_documents,
        existing_receipts=journal.receipts(),
    )
    for receipt in batch["receipts"]:
        created = journal.append_receipt(receipt)
    # Report batch['rejections']; never silently discard quarantine counts.
```

`adapt_prospective(card_docs=(), pool_docs=(), game_evidence=None, *,
observed_utc=None, journal_docs=(), existing_receipts=())` is pure: no filesystem,
network, scoring, fitting or promotion writes. It returns `receipts`, sports
`predictions`, explicit `rejections`/counts, and empty `outcomes`/`resolutions`.
It accepts unresolved forecasts; it does not require a hit/miss or final game.

For raw card/pool inputs, the caller must provide an actual timezone-aware
`observed_utc` and a game-id keyed evidence map containing `kickoff_utc`, a
nonempty `source`, and `status` equal to `SCHEDULED` or `STATUS_SCHEDULED`.
Explicit season/week fields must agree. Pool generation time/base version must
agree with its receipts. Missing timestamps, future source timestamps, unknown
kickoff, already-started new captures, invalid eligibility, identity mismatches
and parity failures quarantine the affected record. This API cannot establish
the authenticity of evidence supplied by its caller.

For NFL immutable journal v1, supply the documents under
`data/snapshots/learning_journal/<season>_wk<NN>/`. The adapter checks the envelope
digest, generation manifest digest, generation/receipt membership, original
capture timestamp, strict pre-kickoff ordering and source receipt independently.
It uses the row's **original** `recorded_utc`; importing a journal later does not
turn the import time into an alleged pregame observation. Exact source files
remain in NFL's `data/snapshots/learning_sources/`; the adapter validates their
digest references but does not fetch or authenticate their bytes. A production
importer must verify those files before admitting a batch as audited evidence.

All distinct frozen source receipts are retained, including later model vintages
for the same contract. Copies are deduplicated by source receipt, not by display
card or settled outcome. `existing_receipts` preserves the original sealed first
capture on subsequent calls. Conflicting kickoff/source/model evidence is not
silently rewritten. A later-discovered earlier copy does not replace a stored
receipt; source generation files remain available for provenance review.

The receipt's capture provenance retains its first generation ID, source schedule
digest, population label and relevant offered-card IDs. Full cross-generation
card membership remains authoritative in the NFL generation documents. **Pool
candidates and actually offered cards are different evaluation populations**;
do not report pool scores as card accuracy. Both remain `generated_unverified`;
public serving confirmation is a separate, still-unimplemented evidence path.

## Append-only local persistence

`ReceiptJournal(path)` owns separate `receipt_journal_*` SQLite tables (schema
version 1), not the existing generic `predictions`/`outcomes` store. Use a context
manager or `close()`. It has no hosted database dependency.

- `append_receipt(receipt)` returns `True` for a new row and `False` for an exact
  replay. Reusing the receipt or domain/task/source ID with changed content fails.
- `get_receipt(id)` returns a detached dictionary or `None`; `receipts()` returns
  detached sealed records in stable order.
- `append_resolution(resolution, revision_id=..., supersedes=None, reason=None)`
  validates explicit FINAL boolean outcomes or authoritative FINAL voids against
  the exact receipt, contract and event. A revision ID replay is a no-op only for
  identical content.
- Corrections require a **new** revision ID, the immediately preceding revision
  ID in `supersedes`, a reason, nonempty source and non-regressing resolution time.
  Cross-receipt chains, forks and changed-ID overwrites fail. Old rows remain.
- `resolution_history(receipt_id)` includes revision links and database-owned
  insertion times. `latest_resolutions()` emits one current FINAL or VOID
  resolution per resolved receipt, suitable for
  `score_binary_receipts(journal.receipts(), journal.latest_resolutions())`;
  unresolved and void receipts never become losses.

The existing settled-only `adapt_my_cards` may produce a different sealed
envelope ID because its observation/provenance fields differ. Its output must
not be attached by substituting IDs. Use the prospective resolution bridge below
to join the original source ID to the exact stored envelope. No hosted two-cycle
integration or authoritative live-source exporter is established by local tests.

## Hosted shadow intake checkpoint

The reconciled FastAPI foundation now has dedicated
`POST /api/v1/nfl/objects` and `POST /api/v1/nfl/generations` routes. They require
the separate server-side `SELFLEARN_NFL_INGEST_TOKEN`; the existing operator token
cannot ingest NFL generations, and the ingest token cannot use operator/MCP
routes. No token values belong in source, a browser or chat. Configuring the
owner's existing Production operator token does not configure this new scoped
pipeline identity or a receipt-aware database store.

Objects are content-addressed, bounded to 3 MiB per request and immutable. The
generation route joins the exact stored pool/schedule/correlation objects,
validates source and receipt semantics, and atomically commits accepted receipts
plus the complete accepted/rejected partition. A retry returns that original
durable decision, not a new decision under today's adapter. Independent invalid
receipt semantics quarantine the affected row; invalid shared evidence fails
the generation. This does not establish public publication.

`ReceiptJournal.generations()` returns only committed generation objects with
their immutable `ack` and database-owned `recorded_utc`. Mere uploaded objects
are not offered-membership evidence. The first generic receipt stays immutable
when that same source forecast is offered in a later generation.

Local integration tests inject a real temporary SQLite journal. Production
`create_app()` does **not** select SQLite or an in-memory store: without a
receipt-aware durable journal it returns 503 and acknowledges nothing. The
ported `SupabaseStore` still serves only the legacy prediction/outcome tables;
it is not the new journal implementation. The Supabase receipt/object/revision
schema, transactional port, deployment and deployed round trip remain open.

## Typed shadow calibration and offline job boundary

`selflearn_core.adapters.nfl_calibration` provides `build_cohort`,
`fit_shadow_candidate` and `walk_forward_report`. Pass original NFL sources,
persisted generic receipts, complete revision histories and
`generation_records=journal.generations()`. For `offered_my`, membership comes
from the committed ACK's accepted IDs and exact generation card membership,
including later offerings of an unchanged source forecast. Rejected records
cannot be resurrected by a changed validator. Without generation records, the
conservative first-envelope membership is explicitly incomplete and cannot pass
the proposed shared return gate. `eligible_pool` is a separate named population.

The canonical first eligible forecast is selected **before** joining outcomes.
Missing earliest-source lineage quarantines the contract; a later resolved
forecast cannot silently replace it. Unresolved forecasts still constrain a
week's training cutoff. Revisions use database observation time as of the fit,
not backdated result time; forks, contradictory vintages and future/regressing
evidence clocks are excluded. An authoritative VOID supersedes an old binary
result without turning into a loss or leaving the old hit training-eligible.

Candidates use `sigmoid(a + b * logit(immutable_base_prob))`, with the NFL input
clip, output clamp, rounded coefficients and four-decimal output applied once.
Generic confidence-Platt is unchanged. The new ridge fitter and stricter numeric
screen are **proposed shared shadow policies**, not an exact recreation of the
current NFL R100 fitter/gate. R100's 100-leg requirement is total, not per fold;
it requires strict pooled base improvement and no week worse against base or
as-served, allowing incumbent ties. This distinction is explicit in the report.

Fold artifacts are labeled `retrospective_reconstruction` with a hypothetical
`fit_as_of_utc`, never a fabricated historical `created_utc`. The final refit is
not itself a prospectively tested fixed candidate. Game-cluster intervals are
conditional on those fits and do not cover training uncertainty or shared
week/regime shocks; a mathematically computable two-game interval is not an
approved sufficiency policy. All artifacts stay shadow, with no promotion or
serving authorization.

The read-only command below accepts an explicit JSON bundle containing exactly
`version:1`, `receipts`, `source_receipts`, `revisions` and `generation_records`
arrays. It prints a hashed report, writes no state and requires no credentials.
Database extraction, scheduled evaluation, candidate persistence and the
independent approved NFL consumer remain separate work.

```sh
PYTHONPATH=packages/selflearn-core python -m selflearn_core.adapters.nfl_calibration \
  --bundle /explicit/local/evidence-bundle.json \
  --as-of-utc 2026-10-15T18:00:00Z --population offered_my
```

The timestamp above is an example cutoff, not a claim that an evaluation or
candidate existed then. Legacy records lacking required evidence remain
unavailable; none is backfilled to claim prospective accuracy.

Writer transactions serialize ID/head checks. Foreign keys, unique constraints
and update/delete-blocking triggers protect this path. This is not protection
against an operator who can replace the SQLite file or alter its schema. The
database timestamps measure local insertion, not public availability. An append
is atomic; an entire multi-receipt batch is not, so retry the same immutable IDs.

## FINAL evidence and correction bridge

`selflearn_core.adapters.nfl_resolution.resolve_prospective(receipts,
source_receipts, scored_legs, game_evidence, *, observed_utc,
existing_revisions=())` is a pure adapter. Supply the exact persisted sealed
receipts, original frozen NFL source receipts and canonical graded legs. It
returns append-ready `proposals`, explicit `rejections` and counts. It does not
read files, fetch evidence or write a store.

Each scored leg must carry:

- Exact `season`, `week`, `game_id`, `gsis_id`/`player_id`, market, line, side,
  direction and comparison operator; `source_receipt_id` is the original NFL
  receipt ID (`receipt_id` is accepted as an alias, never as a newly sealed ID).
- `status` of `FINAL`, `STATUS_FINAL` or `STATUS_FINAL_OVERTIME`,
  `authoritative: true`, a named `source`, immutable `source_revision_id` and
  timezone-aware `source_updated_utc`.
- Binary grades require explicit `played: true`, finite `actual` yardage and
  `result` of `hit`/`miss`. The adapter independently checks the original gte line.
  Real negative/zero yardage is permitted; missing stats are never coerced to zero.

`game_evidence` is keyed by exact game ID and must contain one of those FINAL
statuses, `kickoff_utc`, `final_utc`, `source` and `source_updated_utc`. Any supplied
game/season/week identity must match. Source updates must be at or after final
and no later than `observed_utc`; kickoff must match the stored forecast exactly.
The bridge independently validates the original source hash, contract, features,
base/served probabilities and model versions against the persisted envelope.

`authoritative` is a contract assertion, not authentication or proof. The trusted
NFL evidence exporter must verify official sources before setting it. Current
legacy scored-card files do not necessarily supply all these fields and must
not be upgraded by inventing final/participation/source timestamps.

For each stored receipt, flatten its `resolution_history(receipt_id)` into
`existing_revisions`. Apply accepted proposals with
`journal.append_resolution(**proposal)`. IDs derive deterministically from the
source revision and exact evidence payload. Replays preserve their original
observation timestamp and predecessor, including replay of an older revision
after a correction. Changing an official source revision's payload conflicts;
a real correction needs a new source revision ID and nonempty
`correction_reason`. The proposal extends the validated current chain head.
The store performs the final concurrent-head check; retry against fresh history
after a racing correction, never overwrite it.

Supply history available by the stated observation time. A future existing
revision, regressing official stat clock, changed immutable source revision or
malformed chain is not accepted as a valid correction. If normalized
`source_updated_utc` or `evidence_observed_utc` clocks are present on any generic
resolution, they must respectively be no later than and no earlier than its
resolution time. Older binary evidence need not invent those optional clocks.

Pending, missing and unproven DNP rows produce no revision. An **explicit
authoritative FINAL void**, however, must be recorded: `result: "void"`,
`actual: null`, and `reason` of `did_not_play`, `official_void` or `push`.
`did_not_play` additionally requires `played: false`. A later void revision
supersedes the earlier binary result without deleting it. Generic
`validate_journal_resolution` accepts that normalized VOID evidence with
`hit: null`; the unchanged binary scorer excludes it. Missing stats and ordinary
pending rows cannot invalidate or fabricate a prior official settlement.

Conflicting duplicate grades or contradictory outcomes across vintages of the
same contract quarantine the affected contracts, independent of input order.
Invalid evidence or a revision-chain failure for one supplied vintage also
withholds every proposed update to that contract in the batch. The trusted
resolver must supply all stored vintages and their complete histories, and fan
official corrections out to their exact original source IDs; updating only a
later model vintage would leave earlier training evidence stale.
The bridge does not filter by offered-card membership, select only winners or
discard unresolved forecasts: every original receipt remains in storage.

## Verification

Run the stdlib tests or the repository's existing pytest environment:

```sh
PYTHONPATH=packages/selflearn-core python -m pytest \
  packages/selflearn-core/tests/test_receipt_journal.py \
  packages/selflearn-core/tests/test_nfl_prospective.py \
  packages/selflearn-core/tests/test_nfl_adapter.py \
  packages/selflearn-core/tests/test_nfl_resolution.py \
  packages/selflearn-core/tests/test_probability_receipts.py
```

The actual-producer integration test is opt-in: set `NFL2026_CHECKOUT` to an
explicit NFL checkout that contains `scripts/learning_receipts.py` and
`scripts/learning_journal.py`. It uses a bounded subprocess to call those pure
producers on fixtures, then independently verifies parity through this adapter.
It does not invoke the NFL pipeline, download data, or write source artifacts.
Without that variable the integration test is explicitly skipped, not passed.

## Remaining release work

Hosted receipt/revision schema and API integration; authenticated reliable
transport; source-snapshot byte verification; deployment/publication receipts;
independent end-to-end acceptance; scheduled authoritative FINAL evidence transport; scoring
and chronological holdouts; calibrated candidate generation; approval, NFL
compatibility validation, promotion and rollback. Supabase management MCP access
does not implement any of those data flows. No improvement in prediction
accuracy or Week 6 performance is established by this checkpoint.
