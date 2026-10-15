# SelfLearning and NFL2026 implementation handoff

Revision 1. October 15, 2026, America/Chicago. Read TECHNICAL_PLAN.md and BACKLOG.md before assigning work. No production push, merge, database write, deployment or promotion is authorized by this handoff.

## Source locations and preservation

- NFL implementation worktree: `/Users/jliddar/code/NFL2026-learning-runtime`, branch `build/week6-learning-runtime-20261015`, starting at merged release `32c5bfbe`.
- Shared engine worktree: `/Users/jliddar/code/SelfLearning-feedback-runtime`, branch `build/nfl-feedback-runtime-20261015`, starting at adapter checkpoint `4587231`.
- Preserve dirty hosted foundation at `/Users/jliddar/code/SelfLearning` and all concurrent Claude/user work. It is not included merely because the adapter branch exists.
- PR #172 is already merged. Do not propose merging that old draft again. Before a future release, refresh current main, reconcile new data/history and re-gate the exact new revision. This environment's GitHub fetch currently fails DNS resolution.

Local committed checkpoints (not pushed): NFL journal `7ed3ae5e`, NFL R130 `6a04c2d6`, SelfLearning prospective journal `30fde77`. The architecture package is a separate documentation commit on the SelfLearning branch. These SHAs are local references until the owner separately authorizes a push.

## Current bounded implementation slices

| Slice | Files | Evidence and limits |
| --- | --- | --- |
| NFL journal | `scripts/learning_journal.py`, `scripts/build_my_cards.py`, `tests/feature/week6_learning_journal.test.mjs` | Captures supported pool receipts before both display-dedupe boundaries, snapshots exact consumer inputs, uses immutable paths and publisher conflict protection. Source null/empty timestamp mismatch was independently found and fixed. No public-publication claim or live probability change. |
| Prospective adapter and local persistence | `selflearn_core/adapters/nfl.py`, `store/receipt_journal.py`, `tests/test_nfl_prospective.py`, `tests/test_receipt_journal.py`, `docs/nfl-adapter.md` | Captures unresolved receipts and journals; validates actual NFL producer parity. SQLite append-only store tests receipt/revision idempotency and rejects mutation/forks. This is a local contract implementation, not the hosted Supabase port. |
| R130 report | `scripts/as_offered_record.py`, `scripts/build_review.py`, `app/review.js`, optional review schema and tests | Separates pre-kickoff cohort from settlement, push/reduced/refund/loss counts and late exclusions. Full-history headline unavailable in this shallow checkout. Actual frozen Week 5 settlement reproduces16/65 and Week1full+3reduced+19losses/23. |
| Architecture/backlog | This directory | Seven-page PDF plus editable sources, technical plan, pipeline/security model, roadmap and traced backlog. No assertion that the backlog is implemented. |

Focused verification reported by implementation and independent reviewers: 249 SelfLearning core tests passed with `NFL2026_CHECKOUT` enabled, 14 NFL journal/receipt tests passed after source-timestamp fixes, and 57 R130/related tests passed after correcting overtime finality. Root's data validation and complete NFL feature suite passed, including a rerun after that fix. The full configured browser gate remains unverified; do not equate dependency-free checks with a complete release gate.

## First parallel work assignment

Keep one coordinator and at most three active implementation lanes in this session. Rotate independent QA into a freed slot. Do not edit the same schemas/workflows in two lanes.

1. **Foundation/API lane: S001–S003, S008–S009.** Reconcile unpublished hosted code, test migration history/grants in a test database, implement receipt-aware API and Supabase persistence, scoped auth, per-record commit acknowledgements and revision transport. Owner approval is required before any production DDL.
2. **NFL producer/export lane: S005–S006, exporter part of S008.** Add exact generation semantic validation/publication observations and durable exporter/retry/ack handling. Keep candidate-pool rungs distinct from displayed offers, existing cards immutable and NFL market prices display-only.
3. **Evaluation lane: S010–S014.** Specify/implement common-cohort parity, time-aware revision selection, chronological grouped evaluation and the distinct NFL base-logit candidate. Keep generic sigmoid(a*confidence+b) unchanged. Do not copy those coefficients into NFL.
4. **Independent early release:** S012/R130 and S016 eligibility regression can proceed without waiting for shared-engine ingestion. Candidate review UI waits for S014/S015; screenshot parsing is later scope.

Each brief must name allowed files, contract versions, edge cases, commands, expected exit codes, unsupported behavior and acceptance IDs. Root/coordinator owns cross-lane contract changes. Do not introduce cloud providers, a new NFL build step or a framework rewrite.

## Must-pass bridge and adoption checks

- Resolve FINAL observations against the exact previously persisted prospective envelope using its original `source_receipt_id`, canonical contract and event. The old settled adapter's generated envelope IDs are not interchangeable.
- Freeze exact source bytes and distinguish internal capture, database import and observed public publication times. A hash is not timestamp/authentication proof.
- Pin pool-rung versus displayed-offer population in every evaluator. Preserve all vintages but use the declared canonical first-eligible contract once for that cohort.
- Preserve current MY gates: at least100legs, at least2held-out week folds, strict pooled improvement and no held-out week worse vs base/as-served. Normally that needs3receiptweeks. A new additional0.0015shared-return margin is proposed policy, not an existing MY implementation.
- R127 separately stays shadow for at least2newgradedweeks plus clustered confidence/subgroup criteria. Missing rain is unknown, not0. No Week5/TNF refit to target15/18.
- Candidate/registry state never means active unless the NFL producer consumed the exact approved compatible artifact on a new forecast. Test second-cycle re-evaluation and rollback with original history unchanged.

## Commands for local verification

SelfLearning, from its isolated worktree, using the already available environment:

```sh
NFL2026_CHECKOUT=/Users/jliddar/code/NFL2026-learning-runtime \
PYTHONPATH=packages/selflearn-core \
/Users/jliddar/code/SelfLearning/.venv/bin/python -m pytest packages/selflearn-core/tests -q
```

NFL, with Python3.12 on PATH, one gate/browser server at a time:

```sh
python3 scripts/validate_data.py
bash tests/smoke.sh
node --test tests/feature/*.mjs
bash tests/run_gate.sh
npx playwright test --config tests/playwright.config.mjs tests/web/week6_risk.spec.mjs --project=web
```

Do not label an interrupted or browser-blocked gate as passed. Do not hand-edit generated data, seed synthetic cloud records or apply migrations as a test without explicit authorization. Record exact SHAs, commands, exit codes, suite counts, skips and remaining evidence in the integration manifest.

## Owner involvement

`SELFLEARN_OPERATOR_TOKEN` is already in Vercel Production per the owner. Verify the backend URL/key and authenticated DB round trip privately; do not request token values in chat. Vercel credentials do not automatically configure GitHub Actions, and management MCP is not application MCP. Ask only if a concrete required credential/permission is absent. Production releases/migrations and model activation remain owner decisions.
