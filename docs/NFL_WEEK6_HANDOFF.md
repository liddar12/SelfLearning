# NFL Week 6 shared-engine checkpoint

Current implementation update: October 17, 2026, 10:10 a.m. CDT.
Read [the current implementation handoff](architecture/2026-10-15/IMPLEMENTATION_HANDOFF.md)
first. The text below is the original October 14 contracts checkpoint, not the
current implementation or release status. Later local runtime work passes 1,379
core/API tests, but the latest NFL/history integration still needs final paired
gates and live acceptance. No current integration branch has been published:
GitHub writing was explicitly refused under the session's `never` approval
policy. No actual model was approved or activated.

Revision 1 — October 14, 2026, 9:05 p.m. CDT (America/Chicago).

The owner paused implementation and authorized publishing the unfinished work so Claude Code can continue. This branch is an offline receipt/adapter/scoring checkpoint, with no production integration or promotion claim.

## Related work

- This branch: [`build/nfl-feedback-contracts-20261014`](https://github.com/liddar12/SelfLearning/tree/build/nfl-feedback-contracts-20261014), based on `aa5bbae`.
- NFL branch and full continuation instructions: [`NFL2026/docs/CLAUDE_WEEK6_HANDOFF.md`](https://github.com/liddar12/NFL2026/blob/build/week6-correctness-20261014/docs/CLAUDE_WEEK6_HANDOFF.md).
- NFL RCA: [`docs/reviews/2026-10-14-week6-rca/README.md`](https://github.com/liddar12/NFL2026/blob/build/week6-correctness-20261014/docs/reviews/2026-10-14-week6-rca/README.md).

Keep generic receipt/scoring rules here and NFL identity, availability and settlement rules in the NFL adapter. The original local `SelfLearning` hosted-foundation checkout has unrelated dirty work; it was preserved and is not included in this checkpoint.

## Code and boundaries

Under `packages/selflearn-core/`:

- `selflearn_core/probability_receipts.py`: domain-neutral immutable receipt identity, source/as-of validation and FINAL binary-resolution validation.
- `selflearn_core/adapters/nfl.py`: independent validation of NFL v1 frozen learning receipts, exact card/leg identities, one-time probability transformation parity, explicit FINAL evidence, deduplication and quarantine.
- `selflearn_core/scoring/binary_receipts.py`: descriptive served-probability log loss/Brier, unique contract/event/group counts, duplicate/conflict exclusions and no naive independent-card confidence interval.
- `tests/test_probability_receipts.py` and `tests/test_nfl_adapter.py`: 19 new stdlib unittest cases, including a non-NFL election fixture and independent scalar score expectations.

`adapt_my_cards(card_docs, scores_doc, game_evidence)` returns predictions, outcomes, receipts, resolutions, rejections and a summary. `score_binary_receipts(batch['receipts'], batch['resolutions'])` computes the scalar parity report. The `outcomes` list is the sports record contract; `resolutions` is the generic receipt scorer contract.

The adapter currently supports frozen MY yardage props only. Game/Week parlays, ATD, game winners and player-season estimates are outside its implemented scope. Internal pre-event timestamps do not prove public availability. Legacy rows without adequate immutable features or source timestamps are quarantined rather than reconstructed using today's model.

The CLI prints JSON and does not write to a store:

```bash
PYTHONPATH=packages/selflearn-core python3 -m selflearn_core.adapters.nfl \
  --cards /path/to/cards.json \
  --scores /path/to/scores.json \
  --games /path/to/final-evidence.json
```

`--cards` is repeatable. The games input must explicitly supply keyed FINAL evidence and kickoff/final timestamps; current NFL data must be transformed through a verified evidence adapter instead of inventing those times.

## Evidence and remaining work

Observed before publication: all 19 new unittest cases passed and `git diff --check` passed. No full SelfLearning suite, actual NFL-producer integration, store write, database migration, deployment or live feedback test was completed.

Focused checks:

```bash
PYTHONPATH=packages/selflearn-core python3 -m unittest discover -s packages/selflearn-core/tests -p 'test_probability_receipts.py' -v
PYTHONPATH=packages/selflearn-core python3 -m unittest discover -s packages/selflearn-core/tests -p 'test_nfl_adapter.py' -v
```

Next implementer:

1. Independently review malformed/unhashable input handling and quarantine behavior.
2. Test against the actual NFL `scripts/learning_receipts.py` producer, including corrected serving, current schemas, deduplication and cohort/score parity. Hand-built fixture agreement does not establish cross-repository interoperability.
3. Run the full suite using the existing CI environment and complete adapter documentation (`docs/nfl-adapter.md` remains unwritten).
4. Add idempotent persistence with immutable forecast receipts and explicit outcome revisions. Existing store/updater code was not changed in this checkpoint.
5. Add controlled candidate generation/return, chronological evaluation, task-specific promotion evidence and rollback. NFL must validate candidates before production use; the shared engine must not directly overwrite live predictions.
6. Prove reusable contracts using another domain fixture without initiating financial/election production forecasting. Broader backend/API/MCP rollout is a separate milestone.

Historical NFL bases are largely unavailable under the new strict receipt contract. New valid observations or auditable historical recovery are required before learning can be evaluated. Do not promise that adapter ingestion immediately activates useful learning for Week 6. The owner authorized publishing this work to a feature branch; production deployment and main-branch integration are separate actions.
