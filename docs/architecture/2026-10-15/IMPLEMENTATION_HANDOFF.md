# SelfLearning and NFL2026 implementation handoff

Revision 1 design, implementation status updated October 15, 2026,
5:44 p.m. CDT (America/Chicago). Read TECHNICAL_PLAN.md and BACKLOG.md before
assigning work. No production push, merge, database write, deployment or
promotion is authorized by this handoff. The generated R1 backlog is the
planning baseline; the progress table below supersedes its older status text.

## Source locations and preservation

- NFL implementation worktree: `/Users/jliddar/code/NFL2026-learning-runtime`, branch `build/week6-learning-runtime-20261015`, starting at merged release `32c5bfbe`.
- Shared engine worktree: `/Users/jliddar/code/SelfLearning-feedback-runtime`, branch `build/nfl-feedback-runtime-20261015`, starting at adapter checkpoint `4587231`.
- Preserve dirty hosted foundation at `/Users/jliddar/code/SelfLearning` and all concurrent Claude/user work. It is not included merely because the adapter branch exists.
- PR #172 is already merged. Do not propose merging that old draft again. Before a future release, refresh current main, reconcile new data/history and re-gate the exact new revision. This environment's GitHub fetch currently fails DNS resolution.

Local committed checkpoints (not pushed): NFL journal `7ed3ae5e`, NFL R130 `6a04c2d6`, NFL exporter `ed2f9668`, NFL evidence-grading fix `ac8b1083`; SelfLearning prospective journal `30fde77`, architecture package `46f87cf`, hosted/shadow checkpoint `b9ecd3f`, Supabase journal/configuration/native-gate source `7225b84`. These SHAs are local references until the owner separately authorizes a push.

## Current bounded implementation slices

| Slice | Files | Evidence and limits |
| --- | --- | --- |
| NFL journal | `scripts/learning_journal.py`, `scripts/build_my_cards.py`, `tests/feature/week6_learning_journal.test.mjs` | Captures supported pool receipts before both display-dedupe boundaries, snapshots exact consumer inputs, uses immutable paths and publisher conflict protection. Source null/empty timestamp mismatch was independently found and fixed. No public-publication claim or live probability change. |
| Prospective adapter and local persistence | `selflearn_core/adapters/nfl.py`, `store/receipt_journal.py`, `tests/test_nfl_prospective.py`, `tests/test_receipt_journal.py`, `docs/nfl-adapter.md` | Captures unresolved receipts and journals; validates actual NFL producer parity. SQLite append-only store tests receipt/revision idempotency and rejects mutation/forks. This is a local contract implementation, not the hosted Supabase port. |
| R130 report | `scripts/as_offered_record.py`, `scripts/build_review.py`, `app/review.js`, optional review schema and tests | Separates pre-kickoff cohort from settlement, push/reduced/refund/loss counts and late exclusions. Full-history headline unavailable in this shallow checkout. Actual frozen Week 5 settlement reproduces 16/65 including push; its Week cards contain 1 full + 3 reduced + 19 losses / 23. |
| Architecture/backlog | This directory | Seven-page PDF plus editable sources, technical plan, pipeline/security model, roadmap and traced backlog. No assertion that the backlog is implemented. |

Focused verification reported by implementation and independent reviewers: 249 SelfLearning core tests passed with `NFL2026_CHECKOUT` enabled, 14 NFL journal/receipt tests passed after source-timestamp fixes, and 57 R130/related tests passed after correcting overtime finality. Root's data validation and complete NFL feature suite passed, including a rerun after that fix. The full configured browser gate remains unverified; do not equate dependency-free checks with a complete release gate.

### Subsequent local shadow-loop checkpoint

The `b9ecd3f` slice reconciled the unpublished FastAPI/dashboard/Supabase legacy-store source into the isolated engine branch without modifying the original dirty checkout. New scoped NFL object/generation intake validates exact source bodies and commits an immutable accepted/rejected decision atomically. The following checkpoint adds explicit Supabase journal selection; it is disabled by default and never uses Vercel's ephemeral filesystem as fallback. The legacy Supabase prediction/outcome store is not the receipt/generation/revision port.

The paired NFL exporter defaults to no-network/no-write dry-run. Explicit delivery uses its own ingest credential, HTTPS, bounded transient retries, immutable destination/operation-specific outbox and ACK files, strict identity partitions and sticky conflict records. Root regression found Python 3.9/3.12 differed on deeply nested JSON; an explicit depth limit now matches the API instead of relying on interpreter recursion behavior.

The FINAL bridge joins exact persisted envelopes, preserves correction history and authoritative VOID revisions, and quarantines contract-wide contradictory evidence. Calibration now uses canonical first forecasts before outcome joins, unresolved-forecast cutoffs, database-as-of revisions and committed generation ACKs for later offered membership. Historical rejection decisions cannot be reinterpreted under a newer adapter. Fold artifacts are explicitly retrospective reconstructions, not historically created/prospectively validated models. The shared ridge fitter and stricter numeric screen are proposed policies, not current R100 parity.

Actual local current-input producer→exporter→ASGI→SQLite verification accepted 1,088 receipts with 0 rejections, 840 generated cards and 197 offered forecast contracts. All remained unresolved; no outcomes or accuracy were invented. Source/generation requests were 1,673,331/2,287,126 bytes, below the 3 MiB limit. All generated evidence was temporary, not hand-edited into `data/` or uploaded.

Latest full engine/API measurement: 380 tests passed, 0 skips, plus 4 coverage-tool tests separately passed. All 8 critical Python targets exceed 90% executable-line coverage; aggregate 1,578/1,660 = 95.0602%. See `docs/reviews/2026-10-15-critical-coverage.json` for commands, before/after source hashes, missing lines and limitations. This is not branch coverage, full acceptance completion, live database verification or statistical evidence of accuracy improvement. Root also ran the NFL gate with Python 3.12: data validation, smoke, 2,157 feature tests and all four model-regression gates passed (exit 0); browser step 8 was explicitly skipped because Playwright is absent. A complete exact-release-SHA browser gate and current-main reconciliation remain required before release. The new hosted CI job uses the checkout's package source and prints integration skips explicitly; its GitHub execution is unverified until published.

That paragraph's counts describe the earlier checkpoint only. The current
measurement and remaining evidence are below. Weather/R127 and
portfolio-objective changes are not activated. No push, cloud write, hosted
migration, deploy or model activation has occurred.

### Latest local journal and evidence checkpoint

| Scope | Implemented and verified locally | Still required |
| --- | --- | --- |
| S001/S003 foundation/readiness | Hosted source reconciled; distinct operator/ingest scopes; explicit `SELFLEARN_JOURNAL_BACKEND=supabase`; per-request schema probe, sanitized failure and no SQLite fallback. | Current-main reconciliation, deployed identity/authentication, environment isolation and actual backend round trip. |
| S008 durable journal | `SupabaseReceiptJournal`, canonical JSON text, atomic generation ACK/partition, server-owned timestamps, immutable receipts and correction history; additive CLI-generated migration `supabase/migrations/20261015222425_receipt_journal_v1.sql`. | Native CI results, hosted PostgREST/grants and persistence/recovery checks, production migration approval, exporter/workflow wiring and backlog alerting. |
| S009 outcome authority | Pure FINAL bridge, revision persistence and SQL correction/VOID tests. NFL missing/malformed required yardage stays pending; explicit FINAL/OT and complete integer scores required. | Capture-once immutable score/stats/snaps bundle with real observation clocks; supported transport route/exporter. Do not invent upstream update or exact game-finish clocks. |
| S010/S011/S013/S014 evaluation/typed return | Canonical first-forecast cohort, database-as-of revision fence, chronological shadow reconstruction, typed immutable-base-logit transform and synthetic second cycle. | Actual future source cohort parity, scheduled/persisted runs and candidates, independently implemented NFL approval/consumer/rollback. No production adoption. |
| S005/S006 publication/generation | Generation journal and local semantic checks exist. | Verified public first-observation, exact published manifest and complete pipeline prepublication gate. |

The new private tables are `journal_receipts`, `journal_objects`,
`journal_generation_commits` and `journal_revisions` in `selflearn_private`.
Public invoker-only RPCs are `selflearn_journal_append_receipt_v1`,
`selflearn_journal_append_object_v1`, `selflearn_journal_commit_generation_v1`,
`selflearn_journal_append_resolution_v1` and `selflearn_journal_read_v1`.
Only service-role execution/data-column insertion is granted. Canonical text
preserves `1`, `1.0` and `true` as distinct evidence; hashes and database clocks
do not prove public publication. Writers share a transaction advisory lock;
reads use bounded keyset pages with one committed watermark. This is a
single-owner pilot, **not** tenant isolation or high-throughput proof. Preview
needs a separate staging database/project and credentials.

Root verification on frozen source (all commands exit 0 unless noted):

- Full core/hosted measurement with real `NFL2026_CHECKOUT`: **511 passed,
  zero skips**; separate coverage-tool tests **4 passed**. Nine critical Python
  files: **2,021/2,116 = 95.5104% executable-line coverage**, every target above
  90%, source hashes stable. This is not branch, SQL, acceptance or live coverage.
  See `docs/reviews/2026-10-15-critical-coverage.json` for exact commands/hashes.
  The final rerun identifies runtime source `7225b84` and NFL source `ac8b1083`;
  this later handoff/evidence documentation commit does not change runtime code.
- Actual PostgreSQL-WASM migration/roles/RLS/hash/atomicity/revision/read tests:
  **16 passed**. Python adapter/API/adversarial SQL tests: **8 passed**;
  2,500 seeded float/Unicode/big-integer probes and pinned-read correction races
  included. This is real single-connection PostgreSQL SQL, not native concurrency
  or a hosted PostgREST transport.
- Native PostgreSQL CI job authored for disposable PostgreSQL 17; **7 local
  connection-safety guard tests passed, actual native class skipped** because
  no native server/client is available here. Independent review caught and
  corrected a Docker-NAT preflight assumption before commit; a container's
  server-local address need not be loopback even when the client target is.
  Its GitHub execution is unverified.
- Current-input SQL benchmark: **1,088 receipts, 3 source objects, 197 offered
  contracts, 0 outcomes**, source hashes unchanged. Slowest local WASM RPC
  **7.532 s**; maximum request/response **2,485,956 / 2,728,853 bytes**. Below
  local 12-second/8-MiB adapter limits, not a hosted latency/capacity guarantee.
- NFL Python 3.12 `bash tests/run_gate.sh`: **2,165 feature tests**, data,
  smoke and four model-regression gates passed; **browser step 8 skipped**
  (Playwright absent). No tracked data/workflow changes. New grading suite:
  **8 passed** on both Python 3.9 and 3.12; related suites **69 passed**.

The new migration is not applied remotely. It does not silently rerun the old
foundation or fix the unrelated `rls_auto_enable()` advisor issue. Reconcile
the different historical migration IDs, inspect existing grants/trigger
ownership and test a scoped forward fix separately. Do not create a fake empty
permission-fix migration or globally revoke unrelated app privileges.

### Next delivery order and owner actions

1. **Foundation/storage lane:** publish only with authorization; run native CI,
   reconcile real Supabase schema/history, review helper permissions, test an
   isolated staging PostgREST round trip and backup/recovery. Prepare additive
   production migration and exact-SHA release; owner approves execution.
2. **NFL pipeline lane (parallel):** capture one immutable outcome bundle for
   every consumer, version honest observation-clock semantics, add outcome
   transport and verified public-generation observations. Preserve original
   receipt IDs, R102/R106 and the existing Week-6 serving regime.
3. **Evaluation/UX lane (parallel):** persist pinned evaluation manifests and
   typed shadow candidates, add bounded jobs/leases and clear status/rejection
   views. Separate evaluator authorization from owner approval; do not apply
   generic Platt coefficients to NFL base probabilities.
4. **Integrate and independently QA:** wire durable NFL outbox delivery,
   exercise complete future two-cycle flow, outage/correction/rollback and
   exact-SHA browser gates. Candidate consumption requires the owner's later
   approval plus sufficient prospective evidence; do not fit Week-5/TNF
   outcomes to a desired hit count.

No further action with the existing operator token is needed. If actual access
prevents the implementation team configuring missing variables, the owner
should privately confirm/add `SUPABASE_URL` and one backend secret in
[Vercel](https://vercel.com/liddar-terminal/self-learning/settings/environment-variables),
then a distinct `SELFLEARN_NFL_INGEST_TOKEN` in Vercel and
[NFL Actions secrets](https://github.com/liddar12/NFL2026/settings/secrets/actions).
Do not paste values in chat. Enable the journal selector only as part of the
reviewed migration/release, not immediately. Supabase review starts in the
[existing project](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf)
and [Security Advisor](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf/advisors);
do not recreate tables/project. Routine ingestion/evaluation should eventually
need no weekly manual SQL; production approvals, portfolio-policy choices and
model activation remain deliberate owner actions.

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
