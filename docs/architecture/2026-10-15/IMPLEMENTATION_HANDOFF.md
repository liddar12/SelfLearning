# SelfLearning and NFL2026 implementation handoff

Revision 1 design, implementation follow-up October 16, 2026,
8:53 p.m. CDT (America/Chicago). Read TECHNICAL_PLAN.md and BACKLOG.md before
assigning work. No production push, merge, database write, deployment or
promotion is authorized by this handoff. The generated R1 backlog is the
planning baseline; the progress table below supersedes its older status text.

## Source locations and preservation

- NFL implementation worktree: `/Users/jliddar/code/NFL2026-learning-runtime`, branch `build/week6-learning-runtime-20261015`, starting at merged release `32c5bfbe`.
- Shared engine worktree: `/Users/jliddar/code/SelfLearning-feedback-runtime`, branch `build/nfl-feedback-runtime-20261015`, starting at adapter checkpoint `4587231`.
- Preserve dirty hosted foundation at `/Users/jliddar/code/SelfLearning` and all concurrent Claude/user work. It is not included merely because the adapter branch exists.
- PR #172 is already merged. Do not propose merging that old draft again. Before a future release, refresh current main, reconcile new data/history and re-gate the exact new revision. This environment's GitHub fetch currently fails DNS resolution.

Later read-only update, October 15, 10:30 p.m. CDT: the connected GitHub
integration could verify remote main without shell fetch. NFL main is
`0d49a94868142b4e6e888b927363d42270ed23c0`, seven commits after the local
release baseline, with 55 changed paths **all under data/** and no code/workflow
changes. SelfLearning main is `aa5bbaeddca4138399a21c1894b4aed4bacb52b4`.
See `docs/reviews/2026-10-15-remote-baseline-check.json`. No remote data was
merged locally and the exact newer data tree was not release-gated. This
point-in-time read is not deploy verification or permission to publish.

Latest read-only GitHub check, October 16, 10:29 a.m. CDT: NFL main is
`45b3cac4a0356b7e91b11121265ae1b84067fe93`, four commits after `0d49a948`;
56 changed paths comprise 55 data files and one RCA document, no runtime or
workflow changes. Engine main remains `aa5bbaeddca4138399a21c1894b4aed4bacb52b4`.
Thirteen exact new raw files are frozen in the NFL test fixtures with Git,
SHA256 and length checks. The removed latest `inactives.json` is not replaced
with the old TNF feed. No remote data was merged into the worktree or deployed.

At 12:01 p.m. CDT, another authenticated read-only check found both remote
main SHAs unchanged. [Recorded heads](../../reviews/2026-10-16-1201-remote-heads.json).
This is source-state evidence, not a deploy check or authorization to publish.

Latest read-only check, **October 16 at 8:53 p.m. CDT**: NFL main is now
`d0f99be248003f109c9b6394feb2ab14921205cb`, two commits after `45b3cac4`.
All **51** changed paths are data; no runtime/workflow change. Engine main is
unchanged. [Exact heads and path inventory](../../reviews/2026-10-16-2053-remote-heads.json).
No new remote data was imported, merged or release-gated; no deploy identity
was checked. Reconcile current main again before an authorized release.

Local committed checkpoints (not pushed): NFL journal `7ed3ae5e`, NFL R130 `6a04c2d6`, NFL exporter `ed2f9668`, NFL evidence-grading fix `ac8b1083`; SelfLearning prospective journal `30fde77`, architecture package `46f87cf`, hosted/shadow checkpoint `b9ecd3f`, Supabase journal/configuration/native-gate source `7225b84`. These SHAs are local references until the owner separately authorizes a push.

Later local checkpoints: NFL immutable capture/producer/opt-in consumers
`1bd076ac`, evidence/handoff `34fb9ea1`; SelfLearning observed-clock kernel
`7204ebd`; indexed observation/atomic CAS checkpoint `3ed4cae`. No production
behavior is enabled by those commits. NFL public-content observation runtime
`4cd7e1e6`, evidence/handoff `d85de971`, is also local only.
Generic evidence-chunk integrity is locally committed as `62defd6`; it does
not expose an upload route or complete D2 provenance-bound transport.
NFL frozen-candidate audit/experiment tool is locally committed as `5a4b057b`;
its historical full-pool metrics remain unavailable. The separately required
recovered on-card reference replay is a subsequent in-progress slice.

## Overnight target and completion rules

### Owner selected Sunday NFL pilot A

October 16, 2026, 1:50 p.m. CDT: prioritize NFL's existing Actions/Netlify path
with the shared Python engine pinned to a published commit. Requested milestone:
**Sunday, October 18 at 10:00 a.m. CDT**. Full Vercel/Supabase rollout follows;
it is not the Sunday critical path. This does not authorize a push/release or
change the 99-task denominator; current whole-task completion stays **50/99**.

Engine `0e5b697` / `8460a71` adds shared frozen-generation validation and pure
repository feedback preparation: original import clocks, complete membership,
independent raw grading/corrections and the actual calibration kernel. NFL
`903d5ff6` / `92b2b5a0` adds default-off pinned Actions execution, immutable
state/report/run files, complete-success pointer published last and final
source-graph validation on publisher attempts. The old injury validator and
its independently reviewed hashes remain unchanged. No new fit formula, API
dependency, cloud migration or market repricing is introduced.

The first stable engine run passes **1,363 core/API tests**, zero skips, exit 0;
**48/48 critical files >90%**, **8,372/8,683 = 96.4183%** executable lines.
[First pilot report](../../reviews/2026-10-16-critical48-nfl-pilot-coverage.json)
pins engine `8460a71` and NFL `903d5ff6`. Complete SQL: **93 Node passed**, zero
skips, and **101 Python passed / seven native-only skipped**, exit 0. Actual
provider identity, native concurrency, hosted and browser acceptance remain
separate.

The first NFL full gate on `903d5ff6` is **red**, exit 1: 2,450 passed, four
failed, one skipped; contracts/smoke/four model gates pass and browser step 8
skips. R88 dependency placement/single-line/stage-name integration, unrelated
injury-validator source changes and a Git temporary cleanup race are corrected
on `92b2b5a0`; **66 focused checks pass**. Full fixed-revision reruns on engine
`1468da7` and NFL `92b2b5a0` both finish exit **0**: **1,363 core/API pass**,
**48/48 critical files >90%**, the same stable **96.4183%** executable lines;
NFL **2,468 feature tests pass**, zero failed/skipped, contracts/smoke/four model
gates pass, browser step 8 skips. [Corrected paired report](../../reviews/2026-10-16-critical48-nfl-pilot-r2-coverage.json).
Preserve the original red log; do not replace the old result with a later pass.

The follow-up NFL checkpoint `49bcd2ac` fixes exact-commit publication checking,
retains raw captures in the repository, and adds MODEL's saved shadow metrics,
cohort counts, hold reason and evaluation age. Failed/skipped attempts cannot
restamp an old evaluation; a numeric pass remains measure-only. The standalone
capture exporter still refuses sports-data writes; the explicit controller sink
fixes the real checkout path, tested with the actual producer/shared evaluator
and fixture transport, not an actual provider fetch. Committed Git data is
checked through bounded private temporary materialization; this is not provider
authentication or Netlify serving proof.

Frozen pair engine `9738ead346a0e529ad31145ab3bb7457e07f177c` / NFL
`49bcd2ac532f0714b1a8723fdb0be2dfee182a19`: **1,363 core/API pass**, zero skips,
**48/48 critical files >90%**, **96.4183%** stable executable-line coverage;
**2,482 NFL features pass**, zero failed/skipped, contracts/smoke/four model
gates pass. Both terminal exits are **0**; browser step 8 skips. The coverage
report pins 35 NFL provenance files, without dropping any engine target.
[Latest paired report](../../reviews/2026-10-16-critical48-committed-status-coverage.json).

Next: actual compatible future-generation consumer plus owner approval/rollback;
Review and remaining Model subgroup/manifest visibility; browser/current-main,
fresh-pipeline and served-site acceptance. S023-T1 is partial, with no new
whole-task credit.

NFL `c63f34fe` adds Game/MY repeated-selection and shared-game counts, with exact
loss exposure, separate game warnings and identity/empty-state handling. Cards,
ranking and prices remain unchanged; Week's numeric panel is unchanged. **66
focused regressions pass**, zero skips; three browser cases are authored and
syntax-checked but not executed. The single full NFL rerun finishes exit **0**:
**2,493 features pass**, zero failed/skipped, contracts/smoke/four model gates;
browser step 8 skips. Log: `/private/tmp/nfl-concentration-full-gate-root-20261016.log`.
The engine report above retains its original dependency SHA; no new core/API
run is claimed for this UI-only change. S019-T2 stays partial: full cross-policy
joint risk/evaluation is not completed by counts.

The owner chose the recommended approval scope: one pinned candidate may cover
eligible future **2026 Week 6** refreshes, with base/version, freshness, kickoff,
expiry and rollback checks on every generation. A new candidate needs new
approval. Build this as a versioned scope, preserving v1 exact-generation events;
the scope choice does not approve an actual candidate or a production release.
Test cutoff per offered future event: completed TNF must neither block an
eligible Sunday refresh nor receive rewritten forecasts. Positive acceptance
requires actual new pool probabilities/receipts, a second feedback cycle,
expired/incompatible/refused candidates and rollback with old history identical.
The local data snapshot has zero new-format learning journals. New production
generations must create real records; old card archives cannot substitute.
Expect a correct HOLD when sufficient multiweek evidence is unavailable.
NFL's `docs/PARLAY_FEEDBACK_PILOT.md` carries operator/release instructions.

### Original overnight target

Owner-requested target: **Friday, October 16, 2026, 7:00 a.m. CDT
(America/Chicago)**. That target was **not met**. The full 99-task scope remains
intact; neither the requested time nor the local builds authorize a release.

Prioritize the complete NFL capture → bounded transport → independently
verified raw/frozen-identity labels → atomic observation/revision persistence →
pinned evaluation → shadow candidate/typed NFL return flow. In parallel, finish
the recovered selection control and exact-generation semantic checks. Keep the
default highest-hit objective, held calibration/weather policies and production
behavior unchanged until separately approved release gates are met.

The morning handoff must show the task-by-task epic ledger, actual local source
commits, commands/exit codes, working end-to-end boundaries and unfinished work.
Do not award fractional or unverified completion to meet the time target.
Production writes/deployments still require separate authorization; genuine
prospective multiweek evidence cannot be created by overnight coding or refits.

## Audited completion by epic

October 16, 2026, 12:50 p.m. CDT (America/Chicago). **50/99 = 50.5% local-build
task completion**, not production readiness, completed story acceptance or an
accuracy gain. Each whole task has equal weight; partial work gets no credit.
The original R1 plan is 44/93 (47.3%); six explicitly added D1/D2 tasks expand
E02 and the total denominator. Do not hide those tasks to inflate progress.
The ID-by-ID classification and evidence are in [progress.json](progress.json).

| Epic | Local complete | Percentage | Main remaining boundary |
| --- | ---: | ---: | --- |
| E00 Foundation/security | 2/9 | 22% | Live schema/history, helper permissions, credentials and deployed identity |
| E01 Forecast/publication lineage | 5/9 | 56% | Deployment identity, live observation and exact-generation prepublication gate |
| E02 Ingestion/outcome corrections | 14/15 | 93% | Runtime activation, retention and native/hosted acceptance; sealed multi-batch executor verified locally |
| E03 Evaluation/honest records | 9/9 | 100% | Local tasks complete, not story/live acceptance: default reporting hookup and historical coverage still limited |
| E04 Candidates/controlled adoption | 6/9 | 67% | Persisted approvals, independent NFL consumer, actual two-cycle rollback |
| E05 Eligibility/freshness | 4/6 | 67% | ID-first fix/current comparative audit accepted locally; FINAL/clearance/provider completeness still incomplete |
| E06 Parlay selection/concentration | 3/6 | 50% | Full Game/Week/MY objective comparison and owner portfolio-policy decision |
| E07 Prospective R127/weather | 0/6 | 0% | Twin prospective collector/grader; weather issue/game-window/roof authority |
| E08 Dashboard/application MCP | 4/9 | 44% | Real-browser/mobile acceptance, evaluation/candidate journeys and sessions |
| E09 Screenshots/questions | 0/6 | 0% | Private lifecycle, OCR/draft confirmation and evidence-based question routing |
| E10 Jobs/operations/release | 0/6 | 0% | Durable jobs and bounded shadow fitter built locally; complete datasets, triggers, alerts and browser/native/hosted release remain |
| E11 Reusable/new domains | 3/9 | 33% | Universal cross-owner/adapter identity story acceptance and one real-domain pilot; finance separate |

Zero completed tasks can include substantial partial code. R126 ranking was
already shipped; its recovered reference replay is now persisted and tested,
but complete original candidate history remains unavailable. The real
synthetic election fixture and identical two-domain boundary tests are built/tested;
a real election adapter is not. S029-T2 is testing-task credit, not S029 story
acceptance: legacy prediction IDs still require domain-qualified task names and
do not establish universal cross-owner isolation.
The progress audit does not award deployment credit to local branches. S016-T1
was reopened for a genuine missed same-ID OUT and is now reaccepted only as a
local current-eligibility task: 29 current/frozen/adversarial cases pass, no
skips, exit 0. Historical `0d49a948` 33 holds become 34 on common inputs; newer
`45b3cac4` 32 become 33, only Deebo OUT added and no previous refusal relaxed.
The actual pool producer removes his seven receiving rungs and one ATD leg,
preserving other numeric prices within that pair. Rounded/scaled fantasy inputs
and shared neutral Elo are controlled comparisons, not original upstream reads.
An original rung's one-ULP cross-runtime difference remains unblessed. Preserve
the original audit and all raw hashes; see the NFL injury-identity RCA revision 2.
Provider completeness, independently rederived depth and full publication are
not accepted by this task result.

### Current parallel team

The planned maximum remains four concurrent agents. The three workers now fail
with “workspace is out of credits”; the coordinator continues alone. Do not
claim parallel throughput, repeatedly retry stopped workers or change billing.
The earlier independent QA evidence remains historical; coordinator follow-ups
are not new independent signoff. Never run two NFL gates concurrently. Claude
Code is not a live connected lane.

Keep building locally while the owner is away; production push, migration,
deployment, portfolio-policy change and model activation remain separately
authorized actions. No weekly manual SQL is required by the intended pipeline.

## Current bounded worker checkpoint October 16 12:50 p.m. CDT

Engine runtime `419c73a`, test hardening `e087541`, evidence `605afd4`: a v2 immutable policy runs the fixed
NFL shadow fitter in isolated Python with explicit CPU/wall/pipe bounds and no
database credential inheritance. Real timeout, CPU termination, file-growth
refusal, exited-leader descendant cleanup, cancellation and durable recovery
tests pass. Normal failures record static reasons and release only a still-live
matching lease; cancellation performs no later database write. V1 inline policy
and result shapes remain unchanged and never claim these execution controls.

Frozen broad results on `e087541`, terminal exit **0**: **1,353 core/API passed / zero
skips**, **46/46 critical files >90%**, **8,181/8,492 = 96.3377%** executable
lines with source/test/tool/head stability. Separate SQL on `419c73a`, all exit
**0**: **93 Node SQL passed / zero skips**;
**101 Python-to-SQL passed / seven native-only skipped**. The latter includes
an actual isolated 330-receipt fitter persisted/recovered through the complete
SQL chain. The existing dependency warning is not suppressed. Child execution
is tested separately, not counted by in-process trace coverage.
[Report](../../reviews/2026-10-16-critical46-bounded-compute-r2-coverage.json) pins
NFL `383fbd63`; its 2,453-feature source gate is unchanged, not rerun or promoted.
Runtime, services and PostgreSQL tests are byte-identical between the two engine
test heads. CPU proof now requires a started-loop marker and CPU-limit signal;
file-growth proof requires EFBIG. The earlier weaker test inventory is preserved,
not retroactively called the stronger proof.

S027 remains partial and completion stays **50/99 (50.5%)**. Next: assemble
complete authoritative journal cohorts with scalable immutable chunks and
independent parity; connect ingestion/manual triggers to canonical job identity;
add a distinct evaluator credential/API, namespace metrics/alerts and hosted/
native worker acceptance. No arbitrary-code sandbox, whole-machine cost/memory
ceiling, autonomous model adoption, live learning loop or accuracy gain is
claimed. [Contracts and verification](LEARNING_JOBS.md). Nothing was pushed,
migrated or deployed. Later evidence/handoff commits are not exact-tested SHAs.

## Earlier durable jobs checkpoint October 16 12:07 p.m. CDT

Source `d3a5693a6dbed2436f00191a89f93257cde8ef70`, evidence `59d54a6`, adds
durable generic SQLite/Supabase jobs and actual NFL shadow-fitter report/artifact
persistence. The supported operation is MY-yardage calibration, not Game/Week/
ML/ATD. Real crash/checkpoint recovery, fresh-worker resume, original-result
recovery without refitting, stale-worker refusal and immutable deadline controls
pass. No serving, forecast, outcome or approval is written.
[Contracts, bounds, evidence and recovery](LEARNING_JOBS.md).

Frozen broad verification, all terminal exit 0:

- **1,232 core/API passed, zero skips**; **43/43 critical files >90%**,
  **7,884/8,194 = 96.2167%** executable lines; measured hashes/heads stable.
- **93 Node SQL passed, zero skips**, including full migration-chain coexistence.
- **100 Python-to-SQL passed / seven native-only skipped**. Four new actual job
  port cases include a synthetic 330-receipt real fitter pause/resume/recovery.
- **32 progress/coverage-tool tests passed** separately. Task counts unchanged.

[Report](../../reviews/2026-10-16-critical43-durable-jobs-coverage.json) pins NFL
dependency head `383fbd6388084103e54628bdcfd4f1c03cc3d7d6`. Later evidence and
handoff commits are not new exact-tested release SHAs. NFL runtime source remains
unchanged from its earlier 2,453-feature gate; no main/data merge was performed.

S027 remains partial. Next work: a hard-bounded worker; canonical complete-journal
dataset assembly with independent cohort proof and scalable immutable delivery;
a distinct scoped evaluator API/credential and ingestion-completion/manual
triggers reusing one definition; namespace metrics and alert drills. Do not
reuse operator/forecast authority or put a broad database key in the browser.
Current database deadlines refuse late commits, not CPU/IO preemption; object
append/completion can leave an unreferenced object on expiry. Never truncate a
multiweek/vintage cohort to fit current caps. Native job concurrency, hosted and
browser acceptance, source/label authority, model adoption and prospective graded
weeks remain open. No migration, workflow, configuration, GitHub push or deploy
was enabled. The full 99-task objective is unchanged.

## Earlier follow-up — October 16, 2026, 11:18 a.m. CDT

NFL ID-first correction `02612c4f`, latest-data audit `41a56a09`, cleanup-fixture
fix `2f6e5dcf`, capture dependency fix `6aafdfcf` are local commits. The capture
now pins all local Python files, including transitive injury-duration helpers,
rejects same-byte symlink modes, and batch-reads exact HEAD blobs without raising
the existing limits. Fourteen focused capture cases and one separate batch-byte
case pass. Earlier frozen full run `27b43fd5` passed 2,446 features; `41a56a09`
then passed 2,449/2,450 but failed the cleanup fixture, exit 1. Six focused cleanup
cases subsequently pass with unchanged production code/timeouts. One frozen
full gate on `6aafdfcf4b321f1085837766f3489c6340818c11` now passes **2,453
features, zero failed/skipped**, data contracts, smoke and four model gates,
terminal exit **0**. Browser step 8 **SKIP**, not waived. Log:
`/private/tmp/nfl-injury-capture-full-gate-root-r5-20261016.log`.
No runtime/test/data edits or HEAD changes occurred during that run; a later
handoff-only document edit is outside its exact source SHA. No new S006/S028
whole-task credit or current-main release-data reconciliation is claimed.

Engine `58ea096` adds a read-only typed owner-shadow-evidence reader; evidence
commit `5314ca8` records **1,182 core/API passed, zero skips, exit 0**;
**39/39 critical files >90%**, **7,480/7,790 = 96.0205%** executable lines.
[Pinned report](../../reviews/2026-10-16-critical39-owner-evidence-coverage.json)
uses detached NFL `27b43fd5` and 29 enumerated source hashes, not current NFL
inventory changes. Actual SQLite history plus a synthetic 330-receipt fitter
prove local reexecution/tamper refusal. This new reader has not run through the
Supabase port or an atomic NFL consumer; every result still holds adoption.
All owner/model/serving/release authority flags remain false. No S015 task credit.
Separate ledger/coverage-tool tests initially had 31 pass / one stale expected
inventory-count failure; after retaining exact counts and requiring the new
owner-evidence/capture-runtime entries, all **32 pass**, exit 0. The measurement
tool and runtime did not change. Those 32 are not included in the 1,182 count.

Existing storage suites remain 86 Node passed / 96 Python passed with seven
native-only skips, exit 0. They do not prove the new evidence reader's hosted
composition. Hosted/native concurrency, real private Storage, source/label
authority, browser acceptance, current-main release reconciliation and genuine
future graded weeks remain open. Next implementation lane is resumable bounded
jobs, separate from forecasts/outcomes/owner intent; no job migration or workflow
has been created or enabled. All changes are local and unpushed.

## Earlier follow-up — October 16, 2026, 3:10 a.m. CDT

Local NFL participation mechanics `db531f44` pass 14 focused tests; generation
binding foundation `398d3b54` passes 17 author/independent tests. Their combined
NFL gate passed: 2,412 feature tests, contracts, smoke and four model gates,
exit 0; browser step 8 explicitly skipped. Subsequent ID-first injury and opt-in
capture-hook edits are outside that frozen gate and need another combined run.
They are not hooked into production;
QB depth and in-window inactive policies remain held, not invented.

Owner-intent hardening is committed as `615199d`; expanded cross-repository
source inventory as `45b2a93`. The fresh full run passes **1,156 core/API tests,
zero skips**, with **38/38 critical files >90%**, **7,262/7,568 = 95.9567%**
executable lines and stable source/test/tool/head identities. Its NFL dependency
checkout is a detached, unchanged `398d3b54`, not the concurrently edited runtime
worktree. [Pinned report](../../reviews/2026-10-16-critical38-owner-hardened-coverage.json).
Complete hardened SQL regressions: **86 Node passed, zero skips**; **96 Python
passed / seven native-only skipped**, both exit 0. After removal of duplicate
validation, the local worst-capacity rejection measured 7,297.118 ms instead of
12,204.860 ms; the 12-second HTTP timeout and mandatory insert guard are unchanged.
These local PostgreSQL-WASM timings are not HTTP/native/hosted acceptance.

All new source is local and unreleased. No new whole-task credit is awarded for
these foundations. See `OWNER_INTENT_JOURNAL.md`, `progress.json` and the newest
verification section; earlier dated checkpoints below are historical.

## Earlier frozen combined checkpoint

October 16, 2026, 2:02 a.m. CDT. NFL `0ea4b003` passes the complete dependency-
free gate: **2,381 feature tests**, contracts, smoke and four model regression
gates, exit **0**; browser step 8 explicitly skipped. Engine `8d2a52c`, evidence
`2b9e247`: **1,054 core/API tests, zero skips**, **34/34 critical files >90%**,
**6,816/7,116 = 95.7841%** executable lines, stable hashes. Complete Python SQL
suite **93 passed / 7 native-only skipped**, exit 0. Storage remains a fake in
relevant ASGI/SQL tests. [Coverage](../../reviews/2026-10-16-critical34-coverage.json).

The opt-in local JSON review preview is NFL `a40393a3`; it preserves all legacy
grades and only publishes a new file outside source/data authorities. Historical
analytical cut, snapshot export and code-owned verification clock are distinct.
Prepublication failure/cancellation leaves no output; publication is an atomic
exclusive local commit, so a lost notification afterward may leave a valid file.
It is not a live-site report or proof of namespace/provider authenticity.

Draft-only materializer `0ea4b003` independently checks supplied typed candidate,
report, context and complete owner-intent-claim mechanics. Two synthetic cycles
and explicit rollback retain old bytes and apply the actual NFL transform once.
Backdated event history and negative absolute loss controls now hold. Base/code
IDs are equality-checked claims, not recomputed deployed compatibility; evaluation
is not re-executed and owner authority is unverified. Every output remains
non-active, with model adoption/serving/release authorization false.

Task credit stays **50/99**. The next separate owner-intent journal, original
FINAL-participation capture and exact-generation prepublication binding are
in progress, not part of this measured checkpoint. S015 authority/consumer and
S017 empirical guard/replay acceptance remain unfinished. No production action.

## Current-input and privacy checkpoint

October 16, 2026, 1:43 a.m. CDT. S016-T1 is independently verified against
current pinned main `0d49a948`: exact projection of all 800 injury rows and 300
weekly players; all four eligibility-consumer source blobs match main. The
current gate holds 33 players: 19 canonical exact-ID status matches (4 OUT,
5 DOUBTFUL, 10 IR) and 14 documented depth decisions, no unexplained gate.
Four new / 20 related tests pass. Unknown-status injection affects only the
identified player (33→34); absent provider time/wrong week remains source
uncertainty and does not rewrite projected points. NFL commit `1a27db5f`.

This completes the exact local revalidation task, not injury story acceptance:
depth-chart authority was not independently rederived, the >1 MiB leg pool was
not downloaded, and no full pipeline/offered-card audit or provider completeness
is claimed. The existing 25-per-team cap still needs a last-FINAL/clearance
shadow guard and replay. Totals: **50/99**, E05 **4/6 local tasks**.

The privacy follow-up `3fe68c73` hardens standalone forecast/v1/v2 exporters,
including cached/original replies and a chosen credential equal to an allowed
error code. Typed abort propagation prevents post-deadline protocol writes.
Root's **80/80 related tests** and **3/3 empty-journal actual SQL intake tests**
pass, no skips. This supersedes the earlier narrower new-runner-only privacy
claim. The last full NFL gate remains `165402d2`: **2,340 feature tests**, data,
smoke and four model gates, exit 0; browser skipped. A later combined full gate
is required after the preview/materializer sources freeze.

New engine commits: `0cb777d` pure display-only attachment; `624dc83` empty SQL
→ both real senders → authenticated ASGI → SQL/quota integration; `744cef8`
explicit read-only trace bridge completion. Private Storage/generic dashboard
store remain test fakes. Root's bridge-only SQL check passes; no browser launch,
layout or hosted acceptance is inferred.

## V2 whole-group and full-cohort checkpoint

October 16, 2026, 1:18 a.m. CDT. This section supersedes earlier checkpoint
counts and earlier statements that D2-T3 is incomplete. Historical sections
below describe their named source revisions only.

The v2 execution chain is locally committed: engine `682d400`, `c757b19`,
`aa3a558`, `47ee1e4`; NFL sender `e7e76355`, checker `4838daf0` and independent
oracle `5ecc3bc4`. No changes have been pushed or deployed. The additive v2
protocol seals complete player/game/stat/side groups across rungs and model
vintages, persists per-batch and terminal results, and recovers original
operations after lost replies. V1 functions and default protocol stay unchanged.
See [CAPTURE_APPLICATION_V2.md](CAPTURE_APPLICATION_V2.md).

Actual original-producer evidence contains 1,088 receipts and 185 groups.
Sender → in-process ASGI → real PostgreSQL-WASM → original-result recovery
passes with whole-group batches **400/398/290**. Real quota is charged before
explicit fake private Storage. Pausing after one batch, resuming in a fresh
invocation, lost registration/middle/final replies, original source-free
recovery, omitted groups and rehashed undercounted terminals are tested. The
last two refuse full success; corrected transport recovers the original.

The complete observed SQL snapshot, including unchanged confirmations, is
validated before projection into the independent NFL/engine comparator. Its
1,088 receipts produce **370 canonical first-eligible contracts** with identical
sets and numeric scores. Scope is eligible MY player-yardage forecasts, not
Game/Week cards or an improved historical hit count. Supplied namespace,
watermark and completeness are not authenticated by a successful comparison.
Default `build_review` and app reporting are not replaced. Independent audit
confirms S010-T2's exact compare-sets task is locally complete; requiring a
live-report hookup for this task would conflate task text with broader story
acceptance. No live/historical accuracy or authenticated completeness is credited.

Current checks: **1,033 core/API tests, zero skips**; **33/33 designated critical
Python files >90%**, **6,570/6,863 = 95.7307%** executable lines, stable hashes.
Full Python SQL suite **89 passed / 7 native-only skipped**; full Node SQL
suite **74 passed / zero skipped**. The Python SQL run includes the final
1,088→370 assertions. See [VERIFICATION.md](VERIFICATION.md) and the
[generated coverage report](../../reviews/2026-10-16-critical-coverage.json).

**D2-T3 receives one whole local-build task credit**, independently reviewed.
Together with S010-T2 the total is **49/99 (49.5%)**, E02 **14/15 (93.3%)**,
E03 **9/9 local tasks**. It is not full D2
story acceptance: native concurrent sessions, hosted PostgREST/permissions,
real private Storage, retention/GC, production configuration and activation are
still required. Finite cache accounting is 256 rows/128 MiB serialized, not
physical disk usage; at capacity cold work fails closed. There is no GC policy
or hosted performance acceptance. The measured local cold prewarm of 10.844s
remains close to the unchanged 12s SQL transport timeout.

The new R127 pure shadow collector/grader, committed locally as NFL `25fcafbc`,
passed **20** author/independent tests
after fixes for invalid earliest receipts, quarantine denominator loss and
hidden unknown metadata. It is inactive at this checkpoint:
weight **0.0**, activation **false**, complete prior-game inventory unverified.
No live pre-kickoff persistence or real two-week prospective evidence exists.
The opt-in intake runner is committed as NFL `165402d2`. Independent QA found
three credential echoes could reach child ACK files; all three are now blocked
before persistence. Its author/independent tests pass **24/24** and root's
combined intake/shadow run passes **44/44**. A final full NFL gate is running.
This is actual-child/wire-fake local evidence, not actual SQL-orchestrator or
live-learning verification. Neither slice earns extra task credit here.

Real browser verification remains blocked by the host environment. A
no-listener browser→ASGI harness initializes four forecasts/twelve SQL revisions,
but Chromium fails at macOS sandbox startup; no UI assertions/screenshots ran.
Existing functional DOM tests are not a browser/layout acceptance substitute.
The pre-final full NFL gate passed **2,339 feature tests**, data contracts,
smoke and all four model gates, exit **0**. Browser step 8 skipped. Intake
privacy hardening still needs a final frozen-source re-run; no release claim.

## Historical bounded implementation slices

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

Local runtime **`62defd6`** implements this helper in
`selflearn_core/evidence_chunks.py` without opening resources or changing SQL,
API routes, credentials, backend selection or workflows. Caller-supplied reads
and callbacks may perform caller-owned I/O and propagate their exceptions;
the future HTTP boundary must sanitize failures and enforce incoming wire size.
Final source SHA256
`c3a49771c93ce58a79a31110abaecf71119ce65f65b23afcff9755d5a475943f`.

Root final measurement: **619 core/hosted tests passed**, zero skips, one
existing Starlette/AnyIO warning; all **13** critical targets exceed 90%
executable-line coverage, **2,923/3,057 = 95.6166%** aggregate. The new helper
is **176/181 = 97.2376%**. Its **18** focused tests and **13** independent
adversarial probes cover canonical base64, actual 64 MiB streaming/overflow,
ordering, immutable pointer snapshots, callback mutation and incomplete staging.
Coverage tooling separately passes **5** tests; all commands exit **0**.
The measured report pins runtime `62defd6`, NFL `d85de971` and stable file hashes.

Root helper-only local probe streamed and verified **67,108,864 bytes** in 64
distinct generated chunks without retaining the capture: **8,759,573 peak
Python-traced bytes**, largest serialized request **1,398,313 bytes**, elapsed
**0.7796 s**. This is not RSS, native I/O, hosted latency, capacity, provider
provenance, durability, upload acceptance or a production performance promise.
The complete D2 task remains partial, so completion is still **38/99**.

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

The accepted first replay tool at NFL `5a4b057b` audits four retained source
classes per week and supports explicit synthetic/local-Git experiments with
exact R74/R126 selection, thin-card behavior, actual-contract dedupe and separate
full-hit/settlement metrics. Root focused tests: **10 passed**; independently
verified new source **323/342 = 94.4444%** executable-line coverage. Full NFL
dependency-free gate: **2,243 feature tests**, data, smoke and four model gates
passed, exit **0**; browser step 8 skipped. Builders/data/workflows unchanged.
Generated `docs/reviews/2026-10-15-r126-source-audit.json` on the NFL branch binds
actual input hashes and explicitly null weeks 2–5 complete-candidate metrics.

Original S018-AC1 separately requires recovery of the on-card reference replay;
the first audit tool does not substitute for that acceptance. Retained Git has
366 slate revisions with a shallow boundary on August 16, before the required
weeks, so a clearly labeled available-history reconstruction is now being
attempted. This does not remove the full-history/publication limitations or
upgrade that union into the complete candidate universe. Preserve observed
discrepancies rather than force 59/188 → 72/189.

### Independent raw-capture verification checkpoint, 10:16 p.m. CDT

Runtime `41d7063` adds `adapters/nfl_capture_evidence.py`, independently parsing
the declared ESPN scoreboard and NFL stats/participation profiles from bounded,
hash-verified raw frames. It validates the exact capture/manifest/observation
closure, period, source order, FINAL/OT evidence and declared clocks without
opening URLs or importing a serving model. It freezes metadata before source
callbacks and imposes CSV field/header/record limits in addition to byte limits.
Independent review caught and corrected a repeated-header memory amplification
before acceptance. Unsupported receiver work limits hold evidence; they do not
establish that a provider is corrupt.

The returned scope is **capture_integrity_only**: source authentication is not
performed, labels are not accepted and no durable learning ACK is emitted.
Player labels still require a separately checked complete durable generation
population, frozen identity joins and full raw-source rereads. Storage and API
acceptance are not implied by this pure verifier.

Root stable full measurement: **637 core/hosted tests passed, zero skips**,
one existing Starlette/AnyIO warning. All **14 critical Python targets** exceed
90% executable-line coverage: **3,336/3,496 = 95.4233%** aggregate; new verifier
**413/439 = 94.0774%**. Focused verifier/tool tests: **23 passed**. Report
`docs/reviews/2026-10-15-critical-coverage.json` pins stable runtime `41d7063`
and NFL `7f72d750`. An earlier run changed HEAD midmeasurement and was discarded;
the committed report is the successful stable rerun. No SQL/native/hosted or
browser evidence was added. D2 remains partial; no whole-task credit yet.

The private-Storage adapter at `9f29350` follows the R1 architecture, using an injected
server-side client, an explicitly private bucket and hash-only namespace. It
requires authenticated exact-byte readback even on duplicate-upload errors,
and reports only uploaded staging, never accepted learning. Root and independent
review pass 13 offline tests; source coverage is 77/79 = 97.4684%. Independent
fake-opener checks exercise the actual existing HTTP client's request path
without networking. No hosted behavior is established. No bucket, route, environment setting,
cloud write or production default is being created or enabled. Quotas, safe GC,
scoped intake and atomic durable result persistence remain activation gates.

### Recovered on-card selection acceptance, 10:19 p.m. CDT

NFL runtime `bed4a3b4` and evidence `0dd2bf48` complete S018's three original
local-build tasks, not a full-candidate prospective backtest. The original
backlog explicitly requires the narrower recovered on-card reference as AC1.
The pinned RCA tree and its retained pre-kickoff correlations reproduce
**59/188 → 72/189 settlement winners** across 63 games, zero pending/excluded.
Weekly controls/candidates are **12→20, 20→21, 14→16, 13→15**; full-contract
hits are **48→61**, separately from reduced settlement wins. Mutable Git clocks,
legacy inferred VOID and missing complete candidate history stay explicit.

Root checks: **19 related tests** pass; source coverage **320/337 = 94.9555%**.
The opt-in diagnostic gate succeeds on actual retained data and refuses planted
losses, missing weeks and pending outcomes. Full NFL gate passes data, smoke,
**2,252 feature tests** and four model gates, exit **0**; browser step 8 skips.
No builder/data/workflow changes. Persisted report and per-game hashes are on
the NFL branch in `docs/reviews/2026-10-15-on-card-reference.json`.

Audited ledger is now **41/99 = 41.4%**, E06 **3/6 = 50%**. Raw verification
and private staging alone add no D2 whole-task credit. No accuracy promise,
release readiness or production deployment is inferred.

### Complete raw-to-label internal composition, 10:26 p.m. CDT

Runtime `e154f84`, independently reviewed, adds the frozen-generation label
binder and the internal capture commit job. It accepts raw bytes, not uploaded
normalized truth. One complete durable generation snapshot establishes all
accepted rungs/model vintages. Exact immutable source objects and ACK partitions
are checked before reading raw evidence; malformed locatable membership holds
the whole underlying player/game/stat group. Contradictory frozen names are held
even when measured numbers coincide. Only the pinned name-normalization policy
can equate known canonical nickname variants.

The job gives binder and resolver the same pinned snapshot. On an explicit CAS
conflict it rereads the journal and repeats the complete raw integrity/label
reread; it never retries a stale member subset. Current capture batches must fit
500 observations/revisions and 8 MiB; an oversized capture is held whole, not
fragmented or acknowledged. A later raw/hash failure, malformed ACK or lost
response produces no success. Internal persistence is not an original durable
delivery/group-result ACK, and source authentication remains unperformed.

Root full stable measurement on `e154f84` and NFL `0dd2bf48`: **680 core/hosted
tests passed, zero skips**, one existing dependency warning. All **17 critical
Python targets** exceed 90% line coverage: **3,893/4,098 = 94.9976%**. Binder
**415/458 = 90.6114%**, job **63/65 = 96.9231%**. Focused binder/job/private
storage tests pass **21/9/13**; five coverage-tool tests pass. Report identifies
exact stable source hashes; these are not branch or acceptance coverage.

New actual PostgreSQL-WASM chain tests **3 pass**; combined with the unchanged
atomic lifecycle suite **5 pass**, exit **0**. Dynamic actual NFL fixtures supply
raw captures and original forecasts, never producer-normalized outcome labels.
The tested correction/confirmation/retry/restore/VOID cycle persists **20
observations and 16 revisions**, preserving original forecasts and pinned
history. A second adapter admits a third vintage in actual SQL: the first CAS
fails, the next fully rebinds **4 → 6 members**, making **5 → 10 raw-source reads**.
Tampered staging never reaches an observation-commit RPC. Chunk storage in that
SQL suite is explicitly an in-memory fake, not Supabase Storage durability.

Independent task audit now completes **D2-T1's local-build task** (raw chunks,
private pointers, manifests, fixed profiles and normalized labels bound end to
end), moving the ledger to **42/99 = 42.4%**, E02 **12/15 = 80%**. A 64 MiB
aggregate transport ceiling does not promise acceptance of arbitrary source
shapes or hosted performance. D2-T2/T3 and full story acceptance remain
incomplete: authenticated capture-producer/sender/receiver, immutable original
delivery/group results, safe recovery/statuses, quota/retention/GC, independent
oversized-group survival and hosted/native evidence. No API route, backend
selection, cloud resource or production behavior is enabled by this checkpoint.

### Scoped capture and forecast trace checkpoint, 11:25 p.m. CDT

October 15, 2026, America/Chicago. Independently reviewed runtime commits:
engine `483bd95` (immutable original delivery result), `33e09c1` (lost-reply
recovery), `b8a70e3` (offline complete-group capacity planner), `bd29ff7`
(durable staging quota), `44895b9` (scoped capture API and forecast trace);
NFL `ca18132f` (archive semantic diagnostic correction), `8561210c`
(fixed-origin, exact-route, redacted outcome sender). All remain local only.

| Root verification on engine `44895b9` / NFL `8561210c` | Exit | Result / limit |
| --- | ---: | --- |
| `tools/measure_critical_coverage.py --output docs/reviews/2026-10-15-critical-coverage.json`, actual NFL checkout enabled | 0 | **808 core/hosted tests**, zero skips; **24/24 targets above 90%**, **4,936/5,166 = 95.5478%** executable lines; source hashes and both heads stable. Not branch/acceptance coverage. |
| Eleven explicit non-native `tests/postgres/test_*.py` suites | 0 | **37 passed**, including the actual NFL sender → in-process API → SQL quota + immutable delivery ledger. Storage is a named fake. |
| `node --test tests/postgres/journal.test.mjs tests/postgres/observed_journal.test.mjs tests/postgres/capture_delivery.test.mjs tests/postgres/capture_staging.test.mjs` | 0 | **43 PostgreSQL-WASM tests**, zero skips. Not native concurrent sessions or hosted PostgREST. |
| `node --test tests/web/nfl-trace-ui.test.mjs` | 0 | **15 functional DOM tests**; trace module V8 **9/9 functions, 42/42 block ranges**. Static responsive/CSP checks are not actual mobile/browser layout acceptance. |
| NFL focused exporter suite with `SELFLEARNING_CHECKOUT` set | 0 | **14 passed**, zero skips; measured source **324/329 = 98.4802%**. No actual network send. |
| NFL `bash tests/run_gate.sh`, Python 3.12, explicit shared-engine checkout | 0 | **2,277 feature tests**, contracts, smoke and four model gates pass. **Browser step 8 skipped**: Playwright is absent. This is not a full release/browser acceptance pass. |
| Coverage-tool tests / progress arithmetic tests | 0 | **5 / 16 passed**. Progress validation pins the exact original task-to-epic mapping and prevents missing tasks, duplicate IDs, inflated counts and silently shrinking the frozen 99-task denominator; it does not certify substantive completion. |

`docs/reviews/2026-10-15-critical-coverage.json` is generated from the exact
runtime checkpoint above. Later documentation or in-progress configuration work
is not silently included in that measurement. One existing Starlette/anyio
deprecation warning remains. No native suite or real Storage test was run.

The scoped API requires a **third**, distinct
`SELFLEARN_NFL_OUTCOME_INGEST_TOKEN`; owner and forecast-ingest credentials cannot
write or inspect captures. Tokens alone do not enable the current receiver:
its server-owned factory is still explicit injection/default disabled. The
next local slice is an explicit default-off backend selector, not an automatic
production rollout. Do not add the operator key to NFL or enable a backend
before reviewing migrations, private storage, quota provisioning and release.

Staging first verifies exact chunk bytes and durably reserves namespace space,
then performs private authenticated Storage upload/readback. Staging ACKs stay
`learning_accepted:false` / `durable_ack:false`. Failed/orphan uploads remain
charged; there is **no refund, quota reset, retention job or GC claim**. Admin
limit changes must quiesce the producer and acquire transaction advisory lock
`(619044,3)` before editing the exact namespace row; arbitrary administrator
updates are not an instant credential-revocation or linearizable-config API.
The quota rejects higher transaction isolation instead of assuming a waiting
advisory lock refreshes an old snapshot. Its intended READ COMMITTED/volatile
function behavior follows the [PostgreSQL snapshot contract](https://www.postgresql.org/docs/17/xfunc-volatility.html);
native multi-session verification is still outstanding.

The delivery transaction stores observations, revisions **and** the original
exhaustive group result together. Lost replies recover that original immutable
result **before** today's clock/raw reads/CAS; accepting a later vintage or
correction never reinterprets or overwrites the old ACK. Actual SQL tests cover
the initial/confirmation/correction/restore/VOID chain (**20 observations,
16 revisions**), a pinned original **4/4** history, and later membership
expansion **4 → 6**. These are synthetic raw fixtures processed by the actual
NFL producer/sender, not proof of real provider authenticity or live accuracy.

**D2-T3 remains incomplete.** The current executor still holds a complete
capture above 500 members. The independently reviewed planner processes one
full raw/frozen-membership bind, then previews complete groups without splitting
rungs/vintages. An actual-producer synthetic workload shaped as 1,088 rungs /
185 groups / maximum eight members produces **496 / 496 / 96** capacity batches;
largest modeled request **2,337,708 bytes**, manifest **475,051 bytes**. Its
prospective multi-batch RPC is explicitly **not implemented**, execution/ACK
flags are false, and no preview is sent to the old whole-capture RPC. A sealed
application/per-batch transaction/recovery protocol is still required.

The read-only dashboard/API/MCP exposes original forecasts, sealed generation
and ACK membership, event/model claims and the full correction/VOID chain.
Publication, source freshness, approved adoption and production connection
remain explicitly unverified. QA found and fixed a re-sealed envelope attaching
new policy claims to an unchanged generation ID, plus stale sign-in rejection,
success and 401 continuations crossing a newer auth epoch. The sender's
unexpected-exception redaction and exported transport authority-override gaps
were also independently reproduced and fixed before this final gate.

NFL's S006 diagnostic initially applied live-card Q-chip rules to the closed MY
archive format. That was a **diagnostic false alarm**, not missing live chips:
actual live rendering showed **155 Questionable legs / 155 Q chips**. The
diagnostic now checks the exact target frozen pool and reports archive/UI
coverage honestly, without altering historic records. Current input's sparse
weekly eligibility fields remain **unknown**, not 185 invented injuries or a
green eligibility result. The prepublication gate is not yet fully wired.

Independent task audit credits only **S022-T1/T2/T3 and D2-T2** for this slice:
**46/99 = 46.5%** whole local-build completion, original scope **41/93**, E02
**13/15**, E08 **4/9**. No story/browser/hosted/activation/accuracy percentage is
inferred. R127/weather remain shadow; do not refit Week-5/TNF outcomes or claim
15/18. R1 PDF/backlog design remains unchanged; no routine whiteboard rebuild.

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

## First-forecast and configuration checkpoint, 11:43 p.m. CDT

October 15, 2026, America/Chicago. Runtime source **`a5c4707`**, NFL runtime
unchanged at `8561210c` (later NFL documentation head `1181c824`). Local only.
This supersedes earlier aggregate engine counts, not their historical evidence.

The generic binary scorer previously joined outcomes before selecting the
canonical forecast. That allowed an unresolved original forecast to disappear
and a later resolved vintage to enter its place. It now chooses the earliest
valid `(domain, task, contract)` forecast before matching any outcomes. Tied
first forecasts with incompatible scoring signatures are withheld. Later
vintages cannot change the selected probability or denominator. The result
declares `first_valid_forecast_before_outcome_join_v2`. This is descriptive
scoring of caller-pinned latest resolutions, **not** an outcome-revision/as-of
selector; passing unfiltered correction history is not the API contract.
No model was refitted or activated and this fixes evaluation integrity, not a
proven increase in future wins.

The identical nine-method journal/revision/cohort boundary contract now runs
against SQLite and actual `SupabaseReceiptJournal` on PostgreSQL-WASM for both
NFL and deterministic synthetic elections. Candidate checks exercise the real
generic candidate-only proposal/register paths and typed NFL rejection, not
an invented universal election fitter. S029-T2 receives local testing-task
credit only. S029 story AC2 remains unproven: legacy `prediction_id` omits
domain and requires domain-qualified task names; universal cross-owner IDs,
real election data, fitting and serving are not implemented.

`services/web/capture_configuration.py` is wired into the real app. Explicit
`SELFLEARN_CAPTURE_BACKEND=supabase` plus server credentials and a private
evidence bucket configure the factory; default is still disabled. Selection
does no HTTP or transport construction. Opening an operation uses two
read-only journal/ledger probes, then the existing quota-before-Storage
boundary. No quota/bucket/migration is provisioned and no environment value
was changed. The private status field `nfl_outcome_intake` stays
`configured_unverified`, `production_connection_verified=false`,
`working_learning_loop=false`, and `model_adoption=unverified`, even after
successful staging. Explicitly invalid preferred secrets never fall back to
legacy credentials in this new selector. Existing journal-selector behavior
is preserved. See [capture rollout steps](CAPTURE_ROLLOUT.md).

Root verification, all exit **0**:

- Full core/hosted suite: **913 passed, zero skipped**, one existing Starlette
  deprecation warning. Critical measurement: **26/26 targets above 90%**,
  **5,119/5,355 = 95.5929% executable lines**, stable hashes/heads;
  [generated report](../../reviews/2026-10-15-critical-coverage.json).
- Explicit existing plus cross-domain Python-to-SQL suite: **47 passed**.
  SQL/WASM is real PostgreSQL execution, but single-connection; HTTP is
  in-process and private Storage is an explicit fake.
- Existing four Node SQL suites: **43 passed**, zero skips.
- Functional DOM interface regressions: **15 passed**, zero skips; no real
  browser/mobile layout claim.
- Coverage-tool/progress-ledger tests: **21 passed** (5 + 16).
- Focused root configuration/app/intake/cross-domain run: **115 passed**;
  independent reviewer configuration run **85**, runtime/app/cohort run **114**,
  plus adversarial first-forecast permutations and pre-probe auth/bounds checks.
- Latest NFL runtime gate remains `8561210c`: **2,277 feature tests**, data,
  smoke and four model gates pass; browser step 8 **SKIP**, not full acceptance.

New v2 SQL/contract/comparator files being built after this source checkpoint
are not included in these counts or task credit. **D2-T3 remains incomplete**
until its actual sealed multi-batch sender/API/atomic persistence chain passes.
No production release, cloud DDL, native concurrency, authenticated provider
source, model adoption, or accuracy improvement is established.
