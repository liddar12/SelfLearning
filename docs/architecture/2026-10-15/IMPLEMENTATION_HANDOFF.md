# SelfLearning and NFL2026 implementation handoff

Revision 1 design, implementation follow-up October 15, 2026,
7:09 p.m. CDT (America/Chicago). Read TECHNICAL_PLAN.md and BACKLOG.md before
assigning work. No production push, merge, database write, deployment or
promotion is authorized by this handoff. The generated R1 backlog is the
planning baseline; the progress table below supersedes its older status text.

## Source locations and preservation

- NFL implementation worktree: `/Users/jliddar/code/NFL2026-learning-runtime`, branch `build/week6-learning-runtime-20261015`, starting at merged release `32c5bfbe`.
- Shared engine worktree: `/Users/jliddar/code/SelfLearning-feedback-runtime`, branch `build/nfl-feedback-runtime-20261015`, starting at adapter checkpoint `4587231`.
- Preserve dirty hosted foundation at `/Users/jliddar/code/SelfLearning` and all concurrent Claude/user work. It is not included merely because the adapter branch exists.
- PR #172 is already merged. Do not propose merging that old draft again. Before a future release, refresh current main, reconcile new data/history and re-gate the exact new revision. This environment's GitHub fetch currently fails DNS resolution.

Local committed checkpoints (not pushed): NFL journal `7ed3ae5e`, NFL R130 `6a04c2d6`, NFL exporter `ed2f9668`, NFL evidence-grading fix `ac8b1083`; SelfLearning prospective journal `30fde77`, architecture package `46f87cf`, hosted/shadow checkpoint `b9ecd3f`, Supabase journal/configuration/native-gate source `7225b84`. These SHAs are local references until the owner separately authorizes a push.

Later local checkpoints: NFL immutable capture/producer/opt-in consumers
`1bd076ac`, evidence/handoff `34fb9ea1`; SelfLearning observed-clock kernel
`7204ebd`; indexed observation/atomic CAS checkpoint `3ed4cae`. No production
behavior is enabled by those commits. NFL public-content observation runtime
`4cd7e1e6`, evidence/handoff `d85de971`, is also local only.

## Audited completion by epic

October 15, 2026, 7:09 p.m. CDT (America/Chicago). **38/99 = 38.4% local-build
task completion**, not production readiness, completed story acceptance or an
accuracy gain. Each whole task has equal weight; partial work gets no credit.
The original R1 plan is 35/93 (37.6%); six explicitly added D1/D2 tasks expand
E02 and the total denominator. Do not hide those tasks to inflate progress.
The ID-by-ID classification and evidence are in [progress.json](progress.json).

| Epic | Local complete | Percentage | Main remaining boundary |
| --- | ---: | ---: | --- |
| E00 Foundation/security | 2/9 | 22% | Live schema/history, helper permissions, credentials and deployed identity |
| E01 Forecast/publication lineage | 5/9 | 56% | Deployment identity, live observation and exact-generation prepublication gate |
| E02 Ingestion/outcome corrections | 11/15 | 73% | D2 provenance-bound transport, native/hosted checks and full acceptance |
| E03 Evaluation/honest records | 8/9 | 89% | Independent full NFL/engine same-cohort comparator; real historical coverage remains unavailable |
| E04 Candidates/controlled adoption | 6/9 | 67% | Persisted approvals, independent NFL consumer, actual two-cycle rollback |
| E05 Eligibility/freshness | 3/6 | 50% | Current-input eligibility verification, clearance guard and frozen replay |
| E06 Parlay selection/concentration | 0/6 | 0% | Persisted reproducible R126 control replay and full Game/Week/MY objective comparison |
| E07 Prospective R127/weather | 0/6 | 0% | Twin prospective collector/grader; weather issue/game-window/roof authority |
| E08 Dashboard/application MCP | 1/9 | 11% | Searchable journal trace, evaluation/candidate journeys and sessions |
| E09 Screenshots/questions | 0/6 | 0% | Private lifecycle, OCR/draft confirmation and evidence-based question routing |
| E10 Jobs/operations/release | 0/6 | 0% | Resumable leases/checkpoints, alert drills, exact-SHA browser/hosted release |
| E11 Reusable/new domains | 2/9 | 22% | Full second-domain boundary suite and one real-domain pilot; finance separate |

Zero completed tasks can include substantial partial code. In particular R126
ranking is already a shipped baseline, but its ephemeral scratch replay is not
the persisted reproducible control artifact required by this backlog. The real
synthetic election fixture is built/tested; a real election adapter is not.
The progress audit does not award deployment credit to local branches.

### Current parallel team

Four concurrent agents maximum: one coordinator/integrator and three file-owned
lanes. D1 and the bounded publication slice are committed; the lanes rotate to
strict frozen-candidate replay and generic bounded evidence-chunk primitives. Independent QA
reviews the new authority boundaries while the coordinator integrates and
maintains audited evidence. Do not increase shared-file collisions or run two
NFL gates. Claude Code is not a live connected fifth lane.

Keep building locally while the owner is away; production push, migration,
deployment, portfolio-policy change and model activation remain separately
authorized actions. No weekly manual SQL is required by the intended pipeline.

## Current bounded implementation slices

| Slice | Files | Evidence and limits |
| --- | --- | --- |
| NFL journal | `scripts/learning_journal.py`, `scripts/build_my_cards.py`, `tests/feature/week6_learning_journal.test.mjs` | Captures supported pool receipts before both display-dedupe boundaries, snapshots exact consumer inputs, uses immutable paths and publisher conflict protection. Source null/empty timestamp mismatch was independently found and fixed. No public-publication claim or live probability change. |
| Content observations | NFL `scripts/publication_observations.py`, feature/fixture tests; runtime `4cd7e1e6` | Exact served prop-leg membership and real fetch-completion time, separate from original receipt. No deployment identity/whole-card attestation or historical backfill. Default local plan never fetches or writes. 38 related tests and the 2,235-feature dependency-free gate pass; browser skipped. The actual 7 MB Week 6 archive has zero verifiable members: 7,817 prop occurrences lack original receipts and 4,378 market occurrences are unsupported; these are not independent bets. |
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
| S009 outcome authority | Pure FINAL bridge, frozen capture/producer, observed-v2 kernel and reviewed local D1 indexed journal/atomic CAS at `3ed4cae`. | D2 provenance-bound public transport, native concurrency and hosted verification. Do not invent upstream update or exact game-finish clocks. |
| S010/S011/S013/S014 evaluation/typed return | Canonical first-forecast cohort, database-as-of revision fence, chronological shadow reconstruction, typed immutable-base-logit transform and synthetic second cycle. | Actual future source cohort parity, scheduled/persisted runs and candidates, independently implemented NFL approval/consumer/rollback. No production adoption. |
| S005/S006 publication/generation | Generation journal plus reviewed exact public-content observation contract at NFL `4cd7e1e6`; no live observation performed. | Deployment identity, hosted public observation and complete pipeline prepublication gate. Existing legacy cards cannot be retroactively attested. |

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

Earlier journal-checkpoint verification (all commands exit 0 unless noted;
superseded by the later outcome checkpoint where the scope overlaps):

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

### Local outcome-capture checkpoint: implemented, not activated

The NFL branch now includes `scripts/capture_outcome_evidence.py` and
`scripts/build_learning_outcomes.py`. One immutable capture preserves raw ESPN
scoreboard, nflverse stats and snap-count bytes, source profiles, hashes,
retrieval clocks and quarantine audits. A completed capture is not proof of
provider completeness, an official sportsbook settlement or a public forecast.
Unknown provider update and actual game-finish times remain unknown.

All three existing NFL grading/facts consumers have an **opt-in**
`--evidence-capture` path. It cannot combine legacy input routes or silently
refetch; it refuses a partial capture that would erase required aggregate
history. Explicit FINAL/OT, complete scores and frozen game identities remain
mandatory. Invalid/ambiguous statistics and participation remain pending.
Measured zero/negative yardage remains valid; named zero offensive snaps are
not proof of inactivity. Recognizable aliases and damaged identities cannot
be silently removed and relabeled absent. Default pipeline routes are unchanged.

The new producer joins original generation receipts to the frozen player pool
and schedule, not today's roster or display-only settlement files. Multiple
forecast vintages and rungs retain their original identities, base/served
probabilities and feature lineage. R106 absence/positive-snap labels are
explicit inferences; they must never be presented as official book decisions.

The separate pure `nfl_observed_resolution.py` kernel preserves strict v1
semantics. It emits immutable observation records for accepted captures,
including unchanged confirmations, distinct from outcome revisions. Complete
observation history prevents a newer no-change confirmation being forgotten
when an older conflicting feed later arrives. Exact old replay cannot revert
a head; genuine newer corrections append history. This is a **local kernel**,
not a durable hosted observation service.

The SQL fixture persists these bodies as generic immutable source objects but
uses a fixture-owned ID list. It does not prove an authoritative index,
concurrent stale-write prevention or atomic fence/revision commit. The API is
not wired to observed-v2; no daily workflow, cloud migration, live model or
historical result has been changed by this checkpoint.

Root outcome-checkpoint evidence (exit 0 unless a skip is stated):

- Full core/hosted measurement with the real NFL checkout: **540 passed,
  zero skips**, one existing Starlette/AnyIO deprecation warning; **2,451/2,567
  = 95.4811% executable-line coverage** over ten critical Python targets,
  every target above 90%. Observed-v2 alone: **430/451 = 95.3437%**.
  `docs/reviews/2026-10-15-critical-coverage.json` pins measured file hashes;
  its precommit HEAD metadata is not a claim that dirty files were already
  committed or that later D1 files were measured. Source hashes were stable.
- **16** actual PostgreSQL-WASM SQL tests and **9** Python adapter/API/producer
  SQL tests passed. The additional fixture exercises four original receipts
  across two vintages/rungs, unchanged confirmations, corrections, replay,
  restoration, inferred VOID and pinned history. Its fixture-owned observation
  index is explicitly not D1 production persistence.
- Separate coverage-tool/native-preflight run: **11 passed** (4 tool + 7
  guards), **4 native tests skipped** because no native PostgreSQL is available.
- NFL Python 3.12 frozen full dependency-free gate: **2,220 passed**, data,
  smoke and four model-regression gates passed; **browser step 8 skipped**.
  New capture/producer line coverage: **518/575 = 90.087%**, **316/346 =
  91.3295%**; five measured fixture suites **57 passed**, **69 Python shards**,
  source hashes stable. Six coverage-tool self-tests passed. Evidence is on
  the NFL branch in `docs/reviews/2026-10-15-outcome-coverage.json`.

Independent review found and fixed malformed participation becoming false DNP,
unjoined statistics becoming zero, stale corrections after no-change evidence,
equal-clock contradictions and report hardlink overwrites. Tests demonstrate
those boundaries, not universal bug-freedom or higher observed NFL hit rate.

The 6:35 p.m. CDT counts above are historical. D1 was subsequently reviewed,
verified and locally committed as described next; its tests are not silently
included in that older measurement.

### D1 indexed-observation and atomic-CAS checkpoint

October 15, 2026, 7:02 p.m. CDT. Local runtime commit **`3ed4cae`** adds:

- CLI-generated additive migration
  `supabase/migrations/20261015233134_observed_journal_v1.sql`, SHA256
  `991e701a0b7e6d150378e3c4222f554309886d884d72ecec6cd02ddfb321bdd2`.
  Append-only observation index, database-owned clocks, private service-only
  grants, invoker RPCs, complete pinned pagination and shared writer lock/CAS.
  The old migration/read RPC remains unchanged.
- Explicit `selflearn_core.adapters.nfl_observed_journal.NFLObservedJournal`.
  The default API still selects the legacy journal. NFL validation remains in
  the adapter, not the reusable store; an AST test protects that boundary.
- Internal `resolve_and_commit_observed`: complete durable history, bounded
  re-read/re-resolve on explicit CAS conflict and exact two-stream ACK counts.
  A lost response never becomes a durable-success claim. Raw capture validation,
  authentication and complete stat-group assembly remain caller prerequisites.

Root verification, exit **0** for each executed suite:

- **601** core/hosted tests with the real NFL checkout, zero skips; **12** critical
  Python targets above 90%, **2,747/2,876 = 95.5146%** executable-line coverage.
  New journal **230/240 = 95.8333%**, internal job **66/69 = 95.6522%**.
  Coverage tooling self-tests: **5 passed** separately. Source hashes are pinned
  in `docs/reviews/2026-10-15-critical-coverage.json`.
- **32** actual PostgreSQL-WASM SQL tests (16 baseline + 16 additive), including
  a 720-case independent Python `.12g` identity oracle. **14** Python-to-SQL
  adapter/producer/job integration tests. Four original receipts across two
  vintages/two rungs produce 20 indexed observations and 16 immutable revisions;
  unchanged confirmations, corrections, historical replay, restoration, inferred
  VOID, pinned views and a real global-CAS conflict are covered.
- **8** legacy adapter/adversarial tests rerun with the additive migration
  enabled. No legacy reader or original source receipt was rewritten.
- **5** new native safety/fixture guards pass. The actual native test class is
  explicitly skipped here (three unexecuted transaction cases); combined old/new
  native and tooling run: **17 passed, 7 native cases skipped**. A required fresh
  PostgreSQL 17 CI job is authored, not executed on GitHub.

Independent review closed malformed-record SQL/Python disagreements,
historical-measurement ACK bypasses, missing/stale proof, legacy downgrade and
false partial ACK paths. These are bounded local tests, not universal safety,
hosted PostgREST proof, statistical improvement or completed D1 story acceptance.
Do not apply the migration or select the new backend before release review.
The public API and daily pipeline remain unwired to observed-v2 outcomes.

### S009 implementation-delta work packages

These extend the R1 story without silently regenerating or changing the
12-epic/31-story design baseline. D1 whole local-build tasks are complete at
`3ed4cae`; its native/hosted acceptance remains open. D2 is remaining
implementation. Planning estimates are not measured delivery velocity.

**S009-D1 — durable observation authority.** Shared-engine/storage lane;
dependency: reviewed journal schema and observed-v2 contract; risk High;
estimated 2–3 engineer-days including focused native concurrency tests.

As an evaluator, I can retrieve every accepted observation at a committed
watermark, so delayed evidence cannot rewrite newer known measurements.

Tasks:

- D1-T1: Design an append-only indexed observation entity with canonical text,
  exact receipt/contract/event links, immutable component IDs/clocks and
  database-owned receipt time. Generate an additive migration with the CLI;
  review service-only grants, RLS and old migration compatibility.
- D1-T2: Add complete bounded keyset enumeration at a committed watermark.
  Read all known observations and revisions under the same authoritative
  fence; never trust a client-supplied subset or fixture ID list.
- D1-T3: Validate and append observations plus any corresponding revisions in
  one transaction under the writer lock. Check staleness and contradictions
  again against current state before ACK; retries retain the same identities.

Acceptance and required evidence:

- D1-AC1 / OBS-01: A04 → unchanged A06 → late conflicting B05 is rejected;
  A04 exact retry cannot revert a later head; newer genuine A→B→A preserves
  every outcome revision and original forecast.
- D1-AC2 / OBS-02: Contradictory measurements at one primary source clock,
  one source ID with different clocks, or a missing observation proof fail
  closed in any row order. Same-measurement confirmations are retained.
- D1-AC3 / OBS-03: Native concurrent unchanged-refresh/correction writers
  cannot accept stale evidence. Crash/fault between observation and revision
  yields neither a partial commit nor a durable ACK. Retry is idempotent.
- D1-AC4 / OBS-04: Pagination never omits a fence at the pinned watermark;
  caller-supplied subsets, cross-receipt links and unauthorized roles cannot
  weaken the history. No UPDATE/DELETE/TRUNCATE mutates immutable evidence.

**S009-D2 — bounded, provenance-bound outcome transport.** NFL producer lane
and shared API lane with disjoint files; depends on D1 transaction contract;
risk High; estimated 1–2 engineer-days after the interface freeze.

As an operator, I can deliver a verified capture once, safely retry it, and
see exactly what was durably accepted without exposing a master credential.

Tasks:

- D2-T1: Specify immutable chunks/private-storage pointers for captures up to
  64 MiB while keeping the 3 MiB API request contract. Bind raw bytes, manifests,
  allowlisted profiles, observations and normalized labels end to end.
- D2-T2: Implement scoped authenticated sender/receiver, strict content/size
  checks, bounded retries and immutable destination-specific outbox/ACKs.
  Never authorize a caller-provided network URL or silently raise body limits.
- D2-T3: Batch/validate the complete underlying player/game/stat group against
  durable receipt membership, across rungs and model vintages; persist safe
  rejection/status reports and recovery instructions.

Acceptance and required evidence:

- D2-AC1 / OUT-01: Tampered bytes, stale/missing chunks, manifest/row/profile
  mismatches, bad clocks and caller-controlled fetch URLs are refused before
  labels are accepted. Unknown source dates remain unknown.
- D2-AC2 / OUT-02: A contradictory rung/vintage cannot evade group quarantine
  by batch order, partial delivery or retry. Independent valid groups survive.
- D2-AC3 / OUT-03: Duplicate/lost-reply delivery yields the original durable
  result; wrong-scope auth, outages and oversized payloads disclose no secrets
  or false ACKs. Both observation and revision streams commit before ACK.
- D2-AC4 / OUT-04: A future authorized staging demonstration crosses actual
  NFL capture → API → database → pinned evaluator with equal cohort/metrics;
  disconnected/unwired capabilities remain visibly false until verified.

The independent reviewer must not author the acceptance fixes being approved.
Line coverage is only one check: consumer-change coverage, native concurrency,
hosted transport, browser acceptance and the full two-cycle workflow remain
separate evidence. No D1/D2 production readiness is inferred from the local
fixture or pure-kernel pass.

#### D2 reviewed implementation boundary, October 15, 7:09 p.m. CDT

The first bounded build is a **generic pure chunk/pointer integrity helper**,
not a mounted intake route or durable upload service: 1 MiB raw chunks,
3 MiB maximum serialized request, 64 MiB object/aggregate cap, strict canonical
base64, per-chunk and ordered whole-object SHA256. Streaming callbacks are
staging only; bytes can reach a staging sink before final digest verification.
Any result remains `uploaded_only`, never authenticated, accepted training
evidence or a durable ACK. D2-T1 remains partial until capture-to-label binding
is implemented and independently tested.

The complete receiver must package a small versioned NFL raw-evidence verifier
inside the engine adapter, independently checking producer parity against raw
fixtures. Do not import a live NFL checkout, serving model or uploaded code.
Read frozen player/schedule identities from durable generation/source objects.
Hashes and allowlisted profiles establish integrity/interpretation, not source
authenticity or a provider clock. A separately scoped authenticated capture
producer attests retrieval; server received time stays separate. Screenshots
and general uploads can never provide this authority.

Derive group membership server-side at one watermark for
`(season, game_id, player_id, market, side)` across all rungs and model vintages.
On CAS conflict, re-read and recompute membership. Initially block any complete
group beyond 500 items / 8 MiB; never ACK fragments. An immutable delivery/group
result ledger must commit in the same transaction as observations/revisions;
"commit outcomes, then save ACK" cannot recover the original lost response.
Quota, retention/reference-safe GC and measured runtime/memory are required
before hosted activation. Private Postgres chunks versus private Storage remain
a persistence decision, not a newly provisioned resource or R1 visual redesign.

Historical R126 source inspection also found that the earlier 59/188 → 72/189
comparison used the union of on-card legs, not a complete archived candidate
universe. A new read-only replay/audit lane must keep missing historical metrics
null and explicitly label synthetic fixtures. Git timestamps are internal
claims, not independent proof of pre-kickoff public availability. No ranking
policy, refit, current calibration substitution or archive rewrite is authorized.

### Next delivery order and owner actions

1. **Foundation/storage lane:** publish only with authorization; run native CI,
   reconcile real Supabase schema/history, review helper permissions, test an
   isolated staging PostgREST round trip and backup/recovery. Prepare additive
   production migration and exact-SHA release; owner approves execution.
2. **NFL pipeline lane (parallel):** review/integrate the locally implemented
   capture, producer and opt-in consumers; complete D1/D2 durable outcome
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
do not recreate production tables/project. Authorize a separate staging
resource if none exists and access or billing requires an owner decision.
Routine ingestion/evaluation should eventually
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
