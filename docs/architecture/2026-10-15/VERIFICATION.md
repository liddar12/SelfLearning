# Architecture and local checkpoint verification

Revision 1. October 15, 2026, America/Chicago. This verifies the planning package and bounded local implementation. It does not establish a deployed backend, complete historical backtest, passing browser release gate or accuracy improvement.

Latest local integrity evidence, October 15, 2026, 10:16 p.m. CDT:
[IMPLEMENTATION_HANDOFF.md](IMPLEMENTATION_HANDOFF.md#independent-raw-capture-verification-checkpoint-1016-pm-cdt)
supersedes the historical code-checkpoint counts and corresponding outstanding
items below. R1 planning/PDF acceptance is unchanged. Runtime source `41d7063`:
637 core/hosted tests pass; all 14 critical Python targets exceed 90%
executable-line coverage (3,336/3,496 = 95.4233%). The new raw-capture verifier
is 413/439 = 94.0774%; focused verifier/tool checks pass 23 tests. Earlier D1 source `3ed4cae` passed
32 PostgreSQL-WASM SQL and 14 Python/API/producer/job SQL tests; those unchanged
SQL files were not rerun for the unrelated pure chunk addition. Five new native safety
guards pass, but its actual transaction class is skipped (three cases); the
combined old/new/tooling run is 17 passed and 7 native cases skipped. The helper
adds no native or hosted evidence. NFL
dependency-free gate on replay source `5a4b057b` passed with 2,243 feature
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
