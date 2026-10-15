# SelfLearning and NFL2026 implementation handoff

Revision 1. October 15, 2026, America/Chicago. Read TECHNICAL_PLAN.md and BACKLOG.md before assigning work. No production push, merge, database write, deployment or promotion is authorized by this handoff.

## Source locations and preservation

- NFL implementation worktree: `/Users/jliddar/code/NFL2026-learning-runtime`, branch `build/week6-learning-runtime-20261015`, starting at merged release `32c5bfbe`.
- Shared engine worktree: `/Users/jliddar/code/SelfLearning-feedback-runtime`, branch `build/nfl-feedback-runtime-20261015`, starting at adapter checkpoint `4587231`.
- Preserve dirty hosted foundation at `/Users/jliddar/code/SelfLearning` and all concurrent Claude/user work. It is not included merely because the adapter branch exists.
- PR #172 is already merged. Do not propose merging that old draft again. Before a future release, refresh current main, reconcile new data/history and re-gate the exact new revision. This environment's GitHub fetch currently fails DNS resolution.

Local committed checkpoints (not pushed): NFL journal `7ed3ae5e`, NFL R130 `6a04c2d6`, NFL exporter `ed2f9668`, SelfLearning prospective journal `30fde77`. The architecture package is a separate documentation commit on the SelfLearning branch. These SHAs are local references until the owner separately authorizes a push.

## Current bounded implementation slices

| Slice | Files | Evidence and limits |
| --- | --- | --- |
| NFL journal | `scripts/learning_journal.py`, `scripts/build_my_cards.py`, `tests/feature/week6_learning_journal.test.mjs` | Captures supported pool receipts before both display-dedupe boundaries, snapshots exact consumer inputs, uses immutable paths and publisher conflict protection. Source null/empty timestamp mismatch was independently found and fixed. No public-publication claim or live probability change. |
| Prospective adapter and local persistence | `selflearn_core/adapters/nfl.py`, `store/receipt_journal.py`, `tests/test_nfl_prospective.py`, `tests/test_receipt_journal.py`, `docs/nfl-adapter.md` | Captures unresolved receipts and journals; validates actual NFL producer parity. SQLite append-only store tests receipt/revision idempotency and rejects mutation/forks. This is a local contract implementation, not the hosted Supabase port. |
| R130 report | `scripts/as_offered_record.py`, `scripts/build_review.py`, `app/review.js`, optional review schema and tests | Separates pre-kickoff cohort from settlement, push/reduced/refund/loss counts and late exclusions. Full-history headline unavailable in this shallow checkout. Actual frozen Week 5 settlement reproduces16/65 and Week1full+3reduced+19losses/23. |
| Architecture/backlog | This directory | Seven-page PDF plus editable sources, technical plan, pipeline/security model, roadmap and traced backlog. No assertion that the backlog is implemented. |

Focused verification reported by implementation and independent reviewers: 249 SelfLearning core tests passed with `NFL2026_CHECKOUT` enabled, 14 NFL journal/receipt tests passed after source-timestamp fixes, and 57 R130/related tests passed after correcting overtime finality. Root's data validation and complete NFL feature suite passed, including a rerun after that fix. The full configured browser gate remains unverified; do not equate dependency-free checks with a complete release gate.

### Subsequent local shadow-loop checkpoint

The next local slice reconciles the unpublished FastAPI/dashboard/Supabase legacy-store source into the isolated engine branch without modifying the original dirty checkout. New scoped NFL object/generation intake validates exact source bodies and commits an immutable accepted/rejected decision atomically. Production journal selection remains deliberately unconfigured: the API returns 503 rather than using Vercel's ephemeral filesystem. The legacy Supabase prediction/outcome store is not the receipt/generation/revision port.

The paired NFL exporter defaults to no-network/no-write dry-run. Explicit delivery uses its own ingest credential, HTTPS, bounded transient retries, immutable destination/operation-specific outbox and ACK files, strict identity partitions and sticky conflict records. Root regression found Python 3.9/3.12 differed on deeply nested JSON; an explicit depth limit now matches the API instead of relying on interpreter recursion behavior.

The FINAL bridge joins exact persisted envelopes, preserves correction history and authoritative VOID revisions, and quarantines contract-wide contradictory evidence. Calibration now uses canonical first forecasts before outcome joins, unresolved-forecast cutoffs, database-as-of revisions and committed generation ACKs for later offered membership. Historical rejection decisions cannot be reinterpreted under a newer adapter. Fold artifacts are explicitly retrospective reconstructions, not historically created/prospectively validated models. The shared ridge fitter and stricter numeric screen are proposed policies, not current R100 parity.

Actual local current-input producer→exporter→ASGI→SQLite verification accepted 1,088 receipts with 0 rejections, 840 generated cards and 197 offered forecast contracts. All remained unresolved; no outcomes or accuracy were invented. Source/generation requests were 1,673,331/2,287,126 bytes, below the 3 MiB limit. All generated evidence was temporary, not hand-edited into `data/` or uploaded.

Latest full engine/API measurement: 380 tests passed, 0 skips, plus 4 coverage-tool tests separately passed. All 8 critical Python targets exceed 90% executable-line coverage; aggregate 1,578/1,660 = 95.0602%. See `docs/reviews/2026-10-15-critical-coverage.json` for commands, before/after source hashes, missing lines and limitations. This is not branch coverage, full acceptance completion, live database verification or statistical evidence of accuracy improvement. Root also ran the NFL gate with Python 3.12: data validation, smoke, 2,157 feature tests and all four model-regression gates passed (exit 0); browser step 8 was explicitly skipped because Playwright is absent. A complete exact-release-SHA browser gate and current-main reconciliation remain required before release. The new hosted CI job uses the checkout's package source and prints integration skips explicitly; its GitHub execution is unverified until published.

Partial backlog progress: S001 foundation source reconciliation; S008 retry/ACK boundary; S009 pure FINAL/revision bridge; S010–S011 cohort/chronological analysis; S013 typed base-logit reconstruction. Do not close those stories as production-complete: Supabase transactional journal/migration, outcome transport, scheduled evaluation, persisted candidates, independent NFL approval/consumer/rollback and publication evidence remain. Weather/R127 and portfolio-objective changes are not activated. No push, cloud write, migration, deploy or model activation has occurred.

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
- Preserve current MY gates: at least100legs total, at least2held-out week folds, strict pooled base improvement and no held-out week worse vs base/as-served (incumbent ties allowed). There is no current per-fold100-training-leg minimum. Normally that needs3receiptweeks. Shared fitter/stricter fold or incumbent rules and an additional0.0015shared-return margin are proposed policies, not exact existing MY behavior.
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
