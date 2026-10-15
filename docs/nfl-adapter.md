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
  validates explicit FINAL boolean outcomes against the exact receipt, contract
  and event. A revision ID replay is a no-op only for identical content.
- Corrections require a **new** revision ID, the immediately preceding revision
  ID in `supersedes`, a reason, nonempty source and non-regressing resolution time.
  Cross-receipt chains, forks and changed-ID overwrites fail. Old rows remain.
- `resolution_history(receipt_id)` includes revision links and database-owned
  insertion times. `latest_resolutions()` emits one current FINAL resolution per
  resolved receipt, suitable for `score_binary_receipts(journal.receipts(),
  journal.latest_resolutions())`; unresolved receipts never become losses.

The existing settled-only `adapt_my_cards` may produce a different sealed
envelope ID because its observation/provenance fields differ. Its output must
not be attached by substituting IDs. The still-required NFL outcome bridge must
join the original `source_receipt_id` to the exact persisted prospective receipt,
validate its contract/event and official FINAL evidence, and then append a
resolution against that stored envelope. Local revision tests do not prove this
real NFL two-cycle integration.

Writer transactions serialize ID/head checks. Foreign keys, unique constraints
and update/delete-blocking triggers protect this path. This is not protection
against an operator who can replace the SQLite file or alter its schema. The
database timestamps measure local insertion, not public availability. An append
is atomic; an entire multi-receipt batch is not, so retry the same immutable IDs.

## Verification

Run the stdlib tests or the repository's existing pytest environment:

```sh
PYTHONPATH=packages/selflearn-core python -m pytest \
  packages/selflearn-core/tests/test_receipt_journal.py \
  packages/selflearn-core/tests/test_nfl_prospective.py \
  packages/selflearn-core/tests/test_nfl_adapter.py \
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
independent end-to-end acceptance; scheduled FINAL outcome resolution; scoring
and chronological holdouts; calibrated candidate generation; approval, NFL
compatibility validation, promotion and rollback. Supabase management MCP access
does not implement any of those data flows. No improvement in prediction
accuracy or Week 6 performance is established by this checkpoint.
