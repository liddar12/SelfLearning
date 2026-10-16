# Architecture and local checkpoint verification

Revision 1. October 15, 2026, America/Chicago. This verifies the planning package and bounded local implementation. It does not establish a deployed backend, complete historical backtest, passing browser release gate or accuracy improvement.

## Current follow-up — October 16, 2026, 3:10 a.m. CDT

Task credit is **49/99 (49.5%)** after reopening S016-T1. Original 33 documented
holds are retained, but a genuine same-ID OUT report was missed through the
legacy name join. Full frozen input/code blobs are verified; producer correction
and independent same-input comparative audit remain unfinished.

NFL `db531f44` participation mechanics: 14 focused tests passed, no skips.
Generation foundation `398d3b54`: 17 author/independent tests passed, no skips.
The combined full NFL gate passes 2,412 feature tests, contracts, smoke and four
model gates, exit 0. Browser step 8 is skipped; this is not a release pass.
New uncommitted injury/capture-hook changes need their own exact-revision gate.

Engine foundation `1ac7325`, evidence `a5fc61a`: 1,127 core/API tests passed,
38/38 critical files >90%, 7,235/7,538 = 95.9804% executable lines. Subsequent
review fixed typed-error redaction, serialization allocation and readable-history
capacity. Current full Python suite is 1,156 passed, zero skipped, exit 0;
the fresh hardened report at engine `45b2a93` passes **38/38 critical files >90%**,
**7,262/7,568 = 95.9567%**, with all measured source/test/tool/head identities
stable. NFL dependencies are frozen at detached `398d3b54`.
[Current coverage](../../reviews/2026-10-16-critical38-owner-hardened-coverage.json).
Complete hardened SQL: **86 Node passed, zero skipped**; **96 Python passed /
seven native-only skipped**, both exit 0. The local 7,297.118 ms near-capacity
rejection is not native/HTTP/hosted performance acceptance; no timeout changed.

All source remains local, off by default where applicable, and unactivated.
The earlier dated sections below describe historical, not current, task credit.

## Earlier frozen combined checkpoint — October 16, 2026, 2:02 a.m. CDT

NFL source `0ea4b003` (including preview `a40393a3`, privacy `3fe68c73` and
current-input `1a27db5f`): full gate exit **0**, **2,381 feature tests**, data
contracts/smoke/four model gates pass. Browser step 8 **SKIP**, not a release pass.

Engine source `8d2a52c`, evidence `2b9e247`: **1,054 core/API tests passed,
zero skipped**; all **34/34** designated critical Python files exceed 90%
executable-line coverage, **6,816/7,116 = 95.7841%**, stable source/test/tool
hashes. [Pinned report](../../reviews/2026-10-16-critical34-coverage.json).
Complete Python SQL suite: **93 passed / 7 native-only skipped**, exit 0,
142.17 seconds. The earlier 74-test Node SQL checkpoint remains unchanged;
no native/hosted/concurrency/real-Storage/browser-layout claim is established.

Root focused checks: 30 preview/materializer/eligibility cases, 53 engine
attachment/tool cases, 80 exporter/intake cases and actual three SQL intake
cases all pass. New preview and draft materialization do **not** complete an
active learning loop or authenticate owner/model/data claims. Materializer
mechanics are not a fitted-model performance evaluation.

The ledger remains **50/99 local tasks (50.5%)**. Next owner-intent persistence,
original participation capture and generation-bound semantic checks are being
built outside this checkpoint; none earns completion credit or release approval
merely by existing. No push, production migration/deploy or activation occurred.

## Earlier task and focused checks — October 16, 2026, 1:43 a.m. CDT

Task ledger: **50/99 (50.5%)**, original 44/93; 34 partial and 15 open.
S016-T1 now has independent current-main revalidation: exact 800-row injury /
300-player Week6 projection, 33 documented withheld players, four new / 20
related tests passed. This is not a full candidate-pool or provider-completeness
audit. Source and limits: NFL `1a27db5f`, [current handoff](IMPLEMENTATION_HANDOFF.md#current-input-and-privacy-checkpoint).

NFL `3fe68c73`: **80/80** related exporter/intake tests and **3/3** actual
empty-journal SQL intake tests pass, zero skips. Original-result and typed
deadline behavior remain intact after credential-response/error hardening.
Engine `624dc83` preserves these actual SQL tests; `744cef8` preserves the
bridge-only EOF/read-only assertion. Root reruns exit 0. Actual browser remains
blocked. No hosted/native/real-Storage/model-adoption claim.

The latest full NFL gate is the earlier `165402d2`: **2,340 feature tests**,
contracts/smoke/four model gates pass, browser step 8 skipped, exit 0. Newer
privacy/current-input/preview/materializer files need a combined full re-run.
The full engine measurement below is a named historical checkpoint; the newer
34-target inventory and attachment CLI are being verified separately.

## Full measured checkpoint — October 16, 2026, 1:18 a.m. CDT

Source: engine `47ee1e4`, NFL `4838daf0`, plus explicitly hashed test/evidence
files in the [coverage report](../../reviews/2026-10-16-critical-coverage.json).
See the [overnight handoff](OVERNIGHT_2026-10-16.md) for unreleased work.

| Check | Exit | Result and boundary |
| --- | ---: | --- |
| Full core/hosted coverage run | 0 | **1,033 passed, 0 skipped**; **33/33 critical files >90%**, 6,570/6,863 executable lines = **95.7307%**. Source, test inventory and measurement-tool hashes stable. |
| Complete Python PostgreSQL suite | 0 | **89 passed, 7 native-concurrency tests skipped** because a native test database is not configured. Real single-connection PostgreSQL-WASM; not hosted/native sign-off. |
| Complete Node SQL suite | 0 | **74 passed, 0 skipped**; existing v1 tests retained, new v2 registration/batch/terminal/cache tests included. |
| New read-view HTTP tests | 0 | **9 passed** independently; with existing API tests, 18 passed and API executable coverage 154/154. View fixtures are explicit fakes; SQL suites separately verify persistence. |
| Actual large integration and comparison | 0 | Original NFL producer 1,088 receipts/185 groups → sender → ASGI → SQL/quota → 400/398/290 whole-group batches. Full SQL snapshot yields **370** canonical contracts with exact independent producer/engine parity. Private Storage is fake. |
| R127 pure shadow collector/grader | 0 | **20 passed**, including unchanged independent adversarial regressions; 351/361 = 97.2299% source lines. No durable live collector, real future graded weeks, complete prior inventory or model activation. |
| Real browser/layout | blocked | Listener creation denied; no-listener ASGI harness reaches Chromium macOS sandbox startup failure. No viewport assertions/screenshots ran and no security restrictions were bypassed. |

The 66.83-second large-run timing probe predates its final parity assertions.
The final full Python SQL suite includes those assertions. Neither probe is a
hosted latency/concurrency acceptance test. SQL timeouts remain 12 seconds.
V2 is opt-in; the default protocol and live review are unchanged.

The new intake orchestrator NFL `165402d2` passes **24/24** author/independent
tests after three credential-echo fixes; root's combined intake/shadow run
passes **44/44**. A full NFL gate passed as a pre-final baseline (2,339 feature
tests, contracts, smoke and four model gates, exit 0; browser step 8 skipped);
the final frozen-source gate is running. No release pass,
live learning, source/namespace authentication, model adoption or accuracy gain
is established. Local task credit is **49/99**, with all six D1/D2 implementation
tasks and supported exact cohort comparison complete; story/release/native/
retention gates remain open. E03's 9/9 local tasks is not 100% story acceptance.

## Historical checkpoints

Local integrity evidence, October 15, 2026, 11:43 p.m. CDT:
[IMPLEMENTATION_HANDOFF.md](IMPLEMENTATION_HANDOFF.md#first-forecast-and-configuration-checkpoint-1143-pm-cdt).
Engine `a5c4707`: **913 core/API, 47 Python-to-SQL, 43 SQL and 15 functional
DOM tests pass**; coverage/progress tools pass 21. All **26 critical Python
targets** exceed 90% executable-line coverage: **5,119/5,355 = 95.5929%**,
stable source hashes/heads. Generic scoring now freezes the first valid
forecast before joining outcomes; identical NFL/synthetic-election boundary
tests pass. The explicit capture-backend setting is wired but default-off;
no environment, quota or bucket was provisioned. Status is configuration-only,
not live learning. No universal owner namespace, real election pilot, hosted
round trip, native concurrency, browser layout acceptance or accuracy claim.
New uncommitted v2 execution/comparator files are outside this checkpoint.

Historical local integrity evidence, October 15, 2026, 11:25 p.m. CDT:
[IMPLEMENTATION_HANDOFF.md](IMPLEMENTATION_HANDOFF.md#scoped-capture-and-forecast-trace-checkpoint-1125-pm-cdt).
Engine `44895b9` / NFL `8561210c`: **808 core/API tests, 37 Python-to-SQL,
43 SQL and 15 functional DOM tests pass**, plus five coverage-tool and sixteen
progress-ledger tests. All **24 critical Python targets** exceed 90%
executable-line coverage: **4,936/5,166 = 95.5478%**, with stable source hashes
and heads. NFL contracts/smoke/four model gates and **2,277 feature tests pass**;
**browser step 8 skips**, so no full browser/release pass. Python-to-SQL uses
PostgreSQL-WASM, in-process HTTP and explicit fake private Storage, not native
concurrent sessions, real network HTTP, Supabase Storage or hosted credentials.
No production migration, activation, two-cycle approved learner or accuracy gain
is established. The default receiver is still disabled; >500-member execution
is held whole, and the separate capacity planner makes no durable ACK.

Historical local integrity evidence, October 15, 2026, 10:26 p.m. CDT:
[IMPLEMENTATION_HANDOFF.md](IMPLEMENTATION_HANDOFF.md#complete-raw-to-label-internal-composition-1026-pm-cdt)
supersedes the historical code-checkpoint counts and corresponding outstanding
items below. R1 planning/PDF acceptance is unchanged. Runtime source `e154f84`:
680 core/hosted tests pass; all 17 critical Python targets exceed 90%
executable-line coverage (3,893/4,098 = 94.9976%). Binder/job are
415/458 = 90.6114% and 63/65 = 96.9231%; focused binder/job/private-storage
checks pass 21/9/13 and coverage-tool tests pass 5. New actual raw-to-
PostgreSQL-WASM tests pass 3, or 5 with unchanged atomic lifecycle tests;
no native/hosted/authenticated-source or original delivery-ACK proof. Earlier D1 source `3ed4cae` passed
32 PostgreSQL-WASM SQL and 14 Python/API/producer/job SQL tests; those unchanged
SQL files were not rerun for the unrelated pure chunk addition. Five new native safety
guards pass, but its actual transaction class is skipped (three cases); the
combined old/new/tooling run is 17 passed and 7 native cases skipped. The helper
adds no native or hosted evidence. NFL
dependency-free gate on on-card replay source `bed4a3b4` passed with 2,252 feature
tests; browser step 8 skipped.
The two new NFL capture/producer targets separately meet 90% (518/575 and
316/346); all 57 measured fixture tests and 6 tooling self-tests passed.
The D1 checkpoint includes indexed observation reads and atomic CAS source;
it does not certify later publication/transport work or consumer branch coverage.
No hosted migration/round trip, native concurrency, complete browser gate,
candidate adoption or accuracy gain is established by those checks.

## Planning artifact acceptance

| Requirement | Evidence |
| --- | --- |
| Openable architecture visual | `SelfLearning_Architecture_R1.pdf`, seven vector pages; native PDF page-object count: 7. |
| Editable synchronized source | `architecture.html`, `render.mjs`, `export-vector-pdf.mjs`; PDF generated from the same HTML text/diagrams. Revision and America/Chicago export time on every page. |
| Readability | Every PDF page rendered through macOS `sips` and visually inspected. Explicit white page background, no detected clipping; authentication arrow corrected to pass through API. Geometry checks report zero out-of-bounds text runs. |
| Architecture coverage | PDF pages 1–2: purpose, authority, components; 3: detailed pipelines/failures; 4: learning state/equation/timing; 5: operator/UX/recovery; 6: team/roadmap; 7: acceptance/manual gates. TECHNICAL_PLAN.md adds persistence, contracts, source authority, security and tradeoffs. |
| Complete delivery backlog | Machine validation: 12 epics, 31 features, 31 stories, 93 tasks, 92 ACs; unique IDs and feature references; every AC names required test evidence. Epic and story dependency graphs have no cycles/missing references. |
| Priorities and sequencing | Parlay first, Slate second, Players third. Independent R130 work does not wait for shared ingestion. Candidate UI depends on evaluation/return; screenshot OCR is later scope. |
| Honest statistical policy | Existing MY minimum of 100 legs and two held-out folds is distinct from the proposed additional 0.0015 margin. Normally three receipt weeks for two folds; R127 separately needs two newly graded weeks and prospective criteria. No 15/18 guarantee. |
| Manual steps | Operator token already present per owner; optional private URL/backend-key confirmation, reviewed production approvals and later policy/model decisions. No credential values requested. |

Commands, exit 0:

```sh
node docs/architecture/2026-10-15/render.mjs
node docs/architecture/2026-10-15/export-vector-pdf.mjs
git diff --check
```

The optional browser export was attempted and failed at Chromium launch with SIGABRT/EPERM. No browser-layout pass is claimed. The portable vector export and actual rendered PDF previews are the delivered visual evidence.

## Historical initial code checkpoints and regression results

NFL local source head `6a04c2d6`, including journal `7ed3ae5e`, on `build/week6-learning-runtime-20261015`. Shared source `30fde77` on `build/nfl-feedback-runtime-20261015`. Source changes were committed only after the listed checks; no generated sports data was regenerated or hand-edited. The review contract schema is code, not a rewritten result history.

| Check | Exit | Result and scope |
| --- | --- | --- |
| NFL `python3 scripts/validate_data.py`, Python3.12 | 0 | All committed data contracts and registered cross-file invariants valid; expected runner-built inactives file absent/skipped. |
| NFL `node --test tests/feature/*.mjs` | 0 | 2,139 tests passed before the independent overtime edge-case fix. |
| NFL `node --test --test-reporter=dot tests/feature/*.mjs`, after overtime fix | 0 | Entire feature suite rerun passed on final code; no claim this executes Playwright. |
| NFL `bash tests/smoke.sh`, Python3.12 | 0 | Selftests, canonical JSON and core invariants passed; temporary fixture refusals are expected assertions, not production fits. |
| NFL journal + receipt focused suite | 0 | 14passed including strict empty/null source-time quarantine and actual temporary-repository publish races. |
| NFL R130 and related focused suites | 0 | 57passed after shared overtime-finality fix; real temporary Git chronology, void/settlement and shallow-history refusal covered. |
| SelfLearning full core suite with actual NFL checkout enabled | 0 | Root rerun:249passed; real receipt and generation producers exercised, not skipped. |
| Both worktrees `git diff --check` | 0 | No whitespace errors. |

Cross-repository command from SelfLearning worktree:

```sh
NFL2026_CHECKOUT=/Users/jliddar/code/NFL2026-learning-runtime \
PYTHONPATH=packages/selflearn-core \
/Users/jliddar/code/SelfLearning/.venv/bin/python -m pytest packages/selflearn-core/tests -q
```

Independent review found two real edge cases absent from earlier passing tests: unknown source timestamps admitted by the NFL journal but rejected by the engine, and valid overtime finals graded pending by R130. Both were corrected and locked by new regressions. This is not a claim that passing suites found every bug.

## Initial outstanding evidence (see updated handoff above)

- Full configured Playwright/browser acceptance and final full release gate, including exact-SHA CI.
- Historical R130 as-offered series from sufficient full Git history. This checkout is shallow and correctly reports unavailable; the expected 17/66, 13/66, 21/66, 22/65, 14/63 series was not reverified here.
- Frozen Week 5 settlement is independently verified: 16/65 including push; its 23 Week cards contain 1 full win + 3 reduced successes + 19 losses. That is a different cohort/definition from the as-offered headline.
- At least 90% measured coverage per implemented critical-path story; no coverage percentage was measured or claimed in this checkpoint.
- Hosted foundation reconciliation, migration content/grants, authenticated API/database round trip, Supabase receipt/revision persistence and durable export acknowledgements.
- FINAL resolver joining original source ID to the exact persisted prospective envelope; the old settled adapter's envelope IDs are different.
- Publication observations, exact-generation semantic gate, pinned evaluation/candidate return, two-cycle hosted demonstration, controlled NFL adoption and rollback.
- New prospective performance evidence, no-position-regression/statistical gates, optional screenshot understanding and real non-NFL adapters.

There were no pushes, production migrations, cloud fixture writes, deploys, refits on Week5/TNF or model promotions.
