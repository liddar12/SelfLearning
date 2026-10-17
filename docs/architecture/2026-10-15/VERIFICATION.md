# Architecture and local checkpoint verification

Revision 1. October 15, 2026, America/Chicago. This verifies the planning package and bounded local implementation. It does not establish a deployed backend, complete historical backtest, passing browser release gate or accuracy improvement.

## Actual MY generation full regression

October 16, **11:41 p.m. CDT**. Frozen engine
`7d675f4cbee6a9d0ed9eb72222440516f2cf6bed` / NFL
`3905b1fb98ec6473ab7f6bbdc642c34424ab26e4` finish exit **0**:
**2,543 NFL features pass**, zero failed/skipped, contracts/smoke/four model
gates; browser 8 skips. **1,379 core/API pass**, zero skips; **49/49 critical
engine files >90%**, **8,551/8,862 = 96.4906%** lines, stable source/test/tool/
heads and 44 NFL source hashes. [Report](../../reviews/2026-10-16-critical49-period-MY-downstream-coverage.json).
Generated 11:41:27 p.m. CDT. Coverage is engine-only; the new MY module has no
separately measured line coverage.

NFL log `/private/tmp/nfl-period-MY-downstream-full-gate-root-20261016.log`,
SHA256 `ef8846a536a570308f99ee8321ebbfa4b76a1c30aed733e68f3c9a9ec9a08503`;
engine log `/private/tmp/selflearning-critical49-period-MY-downstream-root-20261016.log`,
SHA256 `95a658b74a1b11c8909a1dc339a165a042f2aa255e37dda81ee710f2483c71fc`.

This pair covers the original-writer/downstream code below and the real weekly
prediction shape correction: actual `week_game_rows`, schema validation and
explicit null/conflicting status refusal. The 33-check focused log SHA256 is
`7a7eeb0005253d54904d62189e8f63163cede4f37662023e44842b273697a952`.
New exact-commit MY composition remains outside this full measurement. Its
first exploratory run correctly refused a trusted-source change made during
the test; preserve the red log rather than call it a product pass. The frozen
rerun passes two scenarios, including eight real-Git negatives and eight final
clock refusals. The final `dd6ef72f`/`264b9a59` source adds the explicit operator
CLI, trusted exclusion/inventory checks and early refusal before the real frozen
refit. Three related tests pass, zero failures/skips, with ten real-Git/during-
replay negatives and the same eight final-clock refusals. Log
`/private/tmp/nfl-period-MY-committed-output-r3-root-20261016.log`, SHA256
`200a4578137fce1538e48ea940fc6f5e4c5238a4f37543f850472f343f919557`.
Its 80.7-second duration and the earlier CLI version's 123.5 seconds are single
local runs, not a controlled performance benchmark. Engine `bed79e1` pins 45 NFL
files; 32 tool/ledger tests pass separately. The new full pair is still required.
No final upstream eligibility, authenticated operator, browser or live release
acceptance is inferred.

## Earlier corrected MY status and history regression

October 16, **11:22 p.m. CDT**. Frozen engine
`bdf056459f0737b021d2b1736268662069e4e466` / NFL
`379486559a4c68c53a5be87fcafb3d4c9706f491` finish exit **0**:
**2,542 NFL features pass**, zero failed/skipped, contracts/smoke/four model
gates; browser 8 skips. **1,379 core/API pass**, zero skips; **49/49 critical
files >90%**, **8,551/8,862 = 96.4906%** executable lines, stable source/test/
tool/heads and 43 NFL source hashes. [Report](../../reviews/2026-10-16-critical49-my-display-history-r2-coverage.json)
was generated **10:54:02 p.m. CDT**. This is not browser or release acceptance.

NFL log `/private/tmp/nfl-my-display-history-r2-full-gate-root-20261016.log`,
SHA256 `4a23a185fc3f3d07580b7f4ed9cf9fccfc86fdcc01df69f3af5e4acd31655f4e`;
engine log `/private/tmp/selflearning-critical49-my-display-history-r2-root-20261016.log`,
SHA256 `17de0992673b328b0fbec91c81ddc23be2ab58a9dae29f5f4354bbb78e4ef956`.

Later source `c9ce721e` / `ed5c320b` adds actual MY original-read/writer binding
and exact downstream replay, not final publication approval. Original capture/
operator/recovery related run: **17 pass**, zero skips. After extraction of the
unchanged v1 binding checks, **16 related pass**; current legacy runtime source
SHA256 `1f2fc6904e8d913415dc3b9ff7b63b8591da939b4b88b63fcd5d9de705db79d5`
reaches **450/469 = 95.9488%** lines. A later real scenario passes with a fully
retained refresh and three rehashed forgery refusals. These overlapping counts
are not additions to the full run above; new module coverage is not measured.
The new full source needs its own paired run with 44 NFL provenance files.

Preserved capture-containment negative: `/private/tmp/nfl-period-MY-final-link-root-20261016.log`,
exit 1 (16 pass/one fail), SHA256 `3cc7600e87e52538c225cba0d9f314405bb6adfff3a4d9b219a5c335108cd06b`.
The fake authority root used `/var` while the destination normalized to
`/private/var`, so it reached a missing-clock error instead of the expected
containment rejection. The helper now normalizes its authority argument too.
Corrected 17-check log SHA256 `3a1fa43f6676dbcc65040dac02527cd8993fe4dc9dd069aae457b7aec529e277`;
downstream/legacy log SHA256 `8a1717fbc48243c340b24d0a41c84af0a0c4572f11dcbacaa20203027dd45ad9`;
later retained/forgery log SHA256 `f242a1c200ba7c7d368bd5c1d006b3b76f6c4662a97e8905c61da85ba704862c`.

Public serving, current owner authentication, upstream eligibility/freshness and
the final v2 publication gate remain unverified. The inactive-list policy question
is pending; the existing v1 holds are unchanged. No SQL run, actual candidate
approval, source push or production deployment is claimed.

## Earlier MY status first full regression October 16 10:45 p.m. CDT

Frozen engine `2483c1ee1adfae813cd1372c8ce1c80b5e0ff296` / NFL
`ffe7fd932a560577f1e7132b7707ad34b38e484b`: engine exit **0**, **1,379
core/API pass**, zero skips, **49/49 critical files >90%**, stable **96.4906%**
line coverage with 42 NFL source hashes. NFL full gate exit **1**, **2,532
features pass / one fails / zero skips**. The history matrix's single child
exhausts its 60-second limit; contracts, smoke and four model gates pass,
browser 8 skips. This is a red release gate, not a green pair.

[Engine report](../../reviews/2026-10-16-critical49-my-display-history-coverage.json).
NFL log `/private/tmp/nfl-my-display-history-full-gate-root-20261016.log`
SHA256 `c6a5f7c110b70c10ee0c0ebe00494b0d21919639f9361f5133b747c1d6f25970`;
engine log `/private/tmp/selflearning-critical49-my-display-history-root-20261016.log`
SHA256 `5de170226b9a6c23b97531423debd5e2468b334f5d944b7ed29a6e2e76802fef`.

Later canonical `e7ea3df6` partitions the same 32 history mutations into eight
four-case children and tightens exact refusal-reason assertions, without a
timeout increase or removed case. Sixteen focused tests pass. New local CLI
`dee3b5a3` passes four related tests including actual apply/inspect/reverify;
MY label correction `738af216` passes 55 checks. These later fixes require a
new full rerun. Operator log SHA256 `dfd129b480fd3ef2fbf25b9b11df027c5b13ea8d41bd6a17db488180adee847a`;
bounded-family log SHA256 `0936121990a56dda2508ba4c89d109ada33ebd95a2f089347770d50b963c29b0`.
No deployment, actual approval, browser or prospective-accuracy acceptance.

## Earlier committed application replay October 16 10:34 p.m. CDT

Frozen engine `98c21ab4e6d00c2c791ff7df86a1cc08cbe631d8` and NFL
`8bdfd0210960dad3038556a10cfbe379ce9780e5`: **1,379 core/API pass**, zero
skips, exit 0; **49/49 critical files >90%**, **8,551/8,862 = 96.4906%**
executable lines, stable source/test/tool/heads and 37 NFL provenance files.
[Report](../../reviews/2026-10-16-critical49-period-committed-replay-coverage.json)
was generated at **10:23:00 p.m. CDT**. NFL passes **2,496 features**, zero
failed/skipped, contracts/smoke/four model gates; full gate exit 0, browser
step 8 skipped. Logs:
`/private/tmp/nfl-period-committed-replay-full-gate-root-20261016.log`
(SHA256 `fdfcc68d60d40e7b606e49d02fdd8a4a365df031dfc926a272bbbcb6a3377c39`)
and `/private/tmp/selflearning-critical49-period-committed-replay-root-20261016.log`
(SHA256 `672a2a3637711c7c5217eda61458b7b56cd3bc41dcff16539c2163e65267f6ff`).

Later NFL `230baa69`/`142a3f3c` adds the MY saved-version/expiry display and
append-only publication history rules. Three actual producer/owner/fitter
scenario tests and 70 related display/legacy/reporting tests pass. Nine new
history tests and 21 existing publisher tests pass. These later changes are
not covered by the full pair above. Three new browser cases list successfully;
the browser server cannot bind its local port, so no executed browser or visual
acceptance is claimed. Engine `ec879a0` expands provenance to 42 NFL files;
32 separate ledger/tool tests pass after correcting an initial nonexistent
pytest file argument (that invocation exited 4 without running tests).

Whole-task credit remains 50/99; S023-T2 is partial, not complete. V2 final
MY-card semantics, operator/pipeline wiring, Model/Review completion and live
release acceptance remain. No SQL, actual approval, push or deployment occurs.

## Earlier original input replay October 16 10:14 p.m. CDT

Frozen engine `e1838c7c20434fd963fa1212aaedead7f8e022c5` and NFL
`cf5babf5e4b852cb0c457616f47575f001a0489e` both finish exit **0**:
**1,379 core/API pass**, zero skips; **49/49 critical files >90%**,
**8,551/8,862 = 96.4906%** executable lines, stable source/test/tool/heads and
36 NFL source hashes. [Report](../../reviews/2026-10-16-critical49-period-input-replay-coverage.json)
was generated **10:06:40 p.m. CDT**. NFL passes **2,496 features**, zero failed/
skipped, contracts/smoke/four model gates; browser 8 skips. Canonical local NFL
`7414ef07` has the identical tested Git tree
`af86f5a5f28ac288e6ec9d6f1fac47ee79516431`.

This adds exact consumed input/schedule byte retention, previous-pool lineage,
read-only real-producer replay, changed-owner refusal and mid-generation byte
drift detection. The fixture has 22 refusal cases and nonempty unchanged game/
ATD populations. It remains synthetic, not historical NFL or public accuracy.
Logs: `/private/tmp/nfl-period-input-replay-full-gate-root-20261016.log`
(SHA256 `2d931760b16e17a94a9e963297d21acc8eef02034d0077b759d66685776e004e`)
and `/private/tmp/selflearning-critical49-period-input-replay-root-20261016.log`
(SHA256 `0d49c8481fe7e4bcedae741844d62528191af98004c5211d2b53c7d60113fe96`).

Later NFL source `8bdfd021` adds a read-only committed-application replay tool.
Nine related tests pass, zero skips, including exact Git blobs, repaired-worktree
refusal, missing raw evidence, changed code, large-file fallback, bad mode/byte
budgets, corrupted batching and static CLI results. The existing feedback guard
covers 110/110 lines in its own suite. This later source is **not in the full
run above**. `MY_card_semantics_verified`, `whole_release_pass`, owner auth and
production permission all remain false. Do not replace the publisher's hold
with this diagnostic's exit 0. New source inventory `77d78a6` tracks 37 NFL
files; its subsequent full paired regression is recorded above. No new task, release or accuracy
credit; Sunday October 18, 10 a.m. CDT remains the requested pilot target.

## Earlier local period consumer October 16 9:50 p.m. CDT

Frozen engine `74a31a63b82e68ae94c7c5acb2260f79fc556af3` and NFL
`5c11a6af74ae692faafdd4e86b32c599962b5d98` both finish exit **0**.
**1,379 core/API tests pass**, zero skips; **49/49 critical files >90%**,
**8,551/8,862 = 96.4906%** executable lines, minimum 90.6114%. Source, test,
tool and both heads stay stable. The file owner port reaches 179/179 lines;
36 NFL provenance files are pinned. [Exact report](../../reviews/2026-10-16-critical49-period-return-coverage.json)
was generated at **9:49:37 p.m. CDT**. This is line coverage, not branch or
statistical/live acceptance. The existing dependency warning remains.

NFL passes **2,496 feature tests**, zero failures/skips, contracts, smoke and
four model gates; browser step 8 skips because Playwright is absent. Three new
tests cover the actual synthetic producer/raw-grader/fitter return flow,
repeated candidate A, separately approved B, compatible rollback, two feedback
cycles, 21 refusal cases, interrupted-write recovery and publication refusal in
a real temporary bare-Git origin. This proves local mechanics, not historical
NFL accuracy, public offering, operator authentication or Netlify serving.

Logs: `/private/tmp/selflearning-critical49-period-return-root-20261016.log`
(SHA256 `40aefdde91f88e123f0951d9637f1ea524689bd5710ae6eee11ac773a0f8a0b3`)
and `/private/tmp/nfl-period-return-full-gate-root-20261016.log`
(SHA256 `19088a4eb41a4ca625880698d51e0cb680fa04c82095f28912e60ceb7f6ecbde`).
Separate ledger/coverage-tool tests pass 32/32, exit 0, outside core/API counts.
No SQL rerun is claimed for these file-only changes; previous SQL evidence keeps
its original scope. Final publication, pipeline/operator activation, UI version/
expiry and browser/live checks remain. S015-T2 moves open to partial: **50/99
complete, 36 partial, 13 open**. No actual model approval, push or deployment.

## Earlier generic file owner journal October 16 9:13 p.m. CDT

Engine `19a80d9edf67bbf4d8da10edcbd702435b3b31b4`, NFL dependency
`7808de815f5b17a20b12c920b44d8deac8d71ceb`: full run finishes exit **0**,
**1,377 core/API tests pass**, zero skips; **49/49 critical files >90%**,
**8,543/8,855 = 96.4766%** executable lines, stable source/test/tool/head
identities. The added owner file port reaches **171/172 = 99.4186%** line
coverage. No existing target was removed. [Report](../../reviews/2026-10-16-critical49-file-owner-coverage.json)
was generated at **9:13:32 p.m. CDT** and retains 35 NFL provenance files.

Fourteen new file-port tests and the 112-check related run pass, zero skips,
exit 0; 32 of the latter are separate ledger/tool tests, not additions to the
core/API count. Real cooperating-process lock/restart, complete 256-event
history, old retry after revoke, stale-head conflict, lost head-sync reply and
fault cleanup are exercised. This is not OS power-loss, remote Git concurrency,
operator authentication, domain approval or actual model consumption.
Logs: `/private/tmp/selflearning-file-owner-related-r2-root-20261016.log` and
`/private/tmp/selflearning-critical49-file-owner-root-20261016.log`.

No NFL source change or fresh NFL/SQL/browser/hosted run accompanies this
file-only port. Existing SQL-facing modules and SQL tests are unchanged;
the previous SQL results keep their original revision/scope. Whole-task credit
stays **50/99**, with S015-T1 partial. No push, migration, deployment, actual
candidate approval or activation occurred. [Port and remaining work](OWNER_INTENT_JOURNAL.md).

## Earlier Game and MY concentration October 16 8:53 p.m. CDT

NFL `c63f34fe4cc78fc375a8e0d69940153293c4e9db`: **66 focused regressions pass**,
zero skips, and the single full gate finishes exit **0**: **2,493 feature tests
pass**, zero failed/skipped, contracts/smoke/four model gates. Browser step 8
skips; three new browser cases are authored/syntax-checked, not executed.
Actual MY builder output is unchanged; repeated selections and shared games
are independently counted, unknown identity is explicit and empty state clears
old warnings. No probability/joint-risk/selection model is added.
Logs: `/private/tmp/nfl-concentration-focused-root-20261016.log` and
`/private/tmp/nfl-concentration-full-gate-root-20261016.log`.

The earlier paired engine report retains NFL dependency `49bcd2ac`; it is not a
new engine/core/SQL measurement on the later UI revision. No source model or
shared runtime changed in the concentration follow-up. Read-only GitHub main
now `d0f99be2` has 51 data-only changes since `45b3cac4`, no runtime/workflows;
[recorded exact comparison](../../reviews/2026-10-16-2053-remote-heads.json).
That data was not imported/release-gated, and no deployment was checked.
Whole-task credit stays **50/99**; the owner chose candidate/Week 6 approval
scope, not actual activation. No external writes.

## Committed feedback and saved MODEL status October 16 2:41 p.m. CDT

Frozen engine `9738ead346a0e529ad31145ab3bb7457e07f177c` and NFL
`49bcd2ac532f0714b1a8723fdb0be2dfee182a19` both finish terminal exit **0**.
**1,363 core/API tests pass**, zero skips; all **48 critical engine files >90%**,
**8,372/8,683 = 96.4183%** executable lines, minimum **90.6114%**. Source,
test, tool and both Git head identities stay stable. The [paired report](../../reviews/2026-10-16-critical48-committed-status-coverage.json)
was generated at **2:41:45 p.m. CDT** and includes 35 NFL provenance files.
**2,482 NFL feature tests pass**, zero failed/skipped, plus contracts/smoke and
four model gates. Browser step 8 skips, not passes. Supplemental coverage-tool
and ledger tests: **32 pass**, exit 0, outside the core/API count.

Focused controller/guard/status tests: **29 pass**, zero skips, exit 0. The guard
and status reader cover 109/109 and 103/103 executable lines, not branch/layout
acceptance. Real local Git, NFL producer, raw-byte grader and shared evaluator
exercise concurrent working-tree removal, committed missing-source/raw refusal,
checkout-shaped outcome persistence, original external capture disappearance,
saved timestamps and shadow-only UI. Transport is synthetic; provider identity,
public offering time, actual Actions and served-site behavior are not attested.
The original injury validator and its reviewed source pins remain unchanged.

Logs:
`/private/tmp/nfl-committed-feedback-status-focused-r3-root-20261016.log`,
`/private/tmp/nfl-committed-feedback-status-full-gate-root-20261016.log`, and
`/private/tmp/selflearning-critical48-committed-status-root-20261016.log`.
SQL was not rerun for this NFL/tooling change: engine runtime, services and SQL
tests are byte-identical from `8460a71` through `9738ead` (diff exit 0). The
previous 93 Node/101 Python passes and seven native-only skips retain their
original scope. No push, deploy, migration, adoption or accuracy gain. Completion
stays **50/99**; S023-T1 moves from open to partial, not complete. Existing R1
visuals are unchanged.

## Earlier NFL first pilot verification October 16 1:50 p.m. CDT

Owner selected A, shared engine in NFL Actions, for the Sunday October 18
10:00 a.m. CDT target. Engine runtime `0e5b697`, tests `8460a71`, first evidence
`1468da7`: **1,363 core/API passed**, zero skips, exit **0**; **48/48 critical
files >90%**, **8,372/8,683 = 96.4183%** executable lines with stable source,
test, tool and head identities. Both new pure modules reach 100% line coverage;
that is not branch or live acceptance.
[First report](../../reviews/2026-10-16-critical48-nfl-pilot-coverage.json)
pins NFL dependency `903d5ff6`. Complete SQL reruns: **93 Node passed**, zero
skips; **101 Python passed / seven native-only skipped**, both exit **0**.
The dependency warning remains unsuppressed.

NFL `903d5ff6` combined gate is **red**, exit **1**: 2,450 features passed,
four failed, one skipped; contracts/smoke/four model gates pass; browser skipped.
Integration/fixture corrections on `92b2b5a0` pass **66 focused tests**, zero
skips. The corrected full pair `1468da7` + `92b2b5a0` finishes exit **0**:
**1,363 core/API pass**, zero skips, the same stable **48/48 / 96.4183%** coverage;
NFL **2,468 features pass**, zero failed/skipped, contracts/smoke/four model gates
pass. Browser step 8 skips. The [corrected paired report](../../reviews/2026-10-16-critical48-nfl-pilot-r2-coverage.json)
was generated at 1:46 p.m. CDT and pins both source heads. Later documentation/
evidence commits are not newly tested runtime revisions.
Logs: `/private/tmp/selflearning-critical48-nfl-pilot-root-20261016.log`,
`/private/tmp/nfl-parlay-feedback-full-gate-root-20261016.log`,
`/private/tmp/nfl-pilot-integration-fixes-focused-root-20261016.log`,
`/private/tmp/selflearning-nfl-pilot-all-node-sql-root-20261016.log` and
`/private/tmp/selflearning-nfl-pilot-all-python-sql-root-20261016.log`.
Corrected full logs:
`/private/tmp/selflearning-critical48-nfl-pilot-r2-root-20261016.log` and
`/private/tmp/nfl-parlay-feedback-full-gate-r2-root-20261016.log`.

No push/deploy/migration/actual Actions run, future-serving activation, new
public forecast evidence or measured accuracy improvement is accepted. Task
credit remains 50/99. Existing R1 visuals remain unchanged.

## Earlier bounded worker verification October 16 12:50 p.m. CDT

Runtime introduced in `419c73a`; frozen test-hardening source
`e087541d74747672bf59eccb43b0f110f25ed316`, evidence `605afd4`:
**1,353 core/API passed, zero skips**, pytest/process exit **0**;
**46/46 critical files >90%**, **8,181/8,492 = 96.3377%** executable lines with
source/test/tool/head stability. [Report](../../reviews/2026-10-16-critical46-bounded-compute-r2-coverage.json)
pins NFL dependency `383fbd6388084103e54628bdcfd4f1c03cc3d7d6`. Separate complete
SQL runs on `419c73a`: **93 Node passed, zero skips**, exit **0**; **101 Python passed / seven
native-only skipped**, exit **0**. Runtime, services and SQL tests are
byte-identical between both engine test heads; SQL was not rerun for test-only
hardening. The existing dependency warning remains.

Actual child tests enforce CPU termination, file-growth refusal, wall deadlines,
exited-leader descendant cleanup and cancellation. SQLite and Python-to-SQL
cases persist/recover the real 330-receipt shadow report and bounded execution
receipt without refitting completed jobs. Strict integer limits reject boolean
and floating zero substitutions. V1 behavior and default-disabled application
configuration remain unchanged. Parent database calls, native/hosted concurrency,
browser acceptance, complete source authority and live adoption are not covered
by these compute controls. Child code is not traced by the coverage tool;
direct pure-worker tests use fake limits, not fake claims of kernel enforcement.
The strengthened CPU test requires a marker after successful resource readback
and SIGXCPU/SIGKILL termination, not any nonzero exit. The file-growth test
requires EFBIG. The earlier report remains pinned to its original weaker tests.

Commands ran from the engine worktree using its existing Python 3.12 venv and
explicit installed PostgreSQL-WASM module, without installing dependencies:

```bash
NFL2026_CHECKOUT=/Users/jliddar/code/NFL2026-learning-runtime PYTHONPATH=packages/selflearn-core:. python tools/measure_critical_coverage.py --output docs/reviews/2026-10-16-critical46-bounded-compute-r2-coverage.json
SELFLEARN_PGLITE_MODULE=/Users/jliddar/.npm/_npx/ba4f1959e38407b5/node_modules/@electric-sql/pglite/dist/index.js node --test --test-concurrency=1 tests/postgres/*.test.mjs
SELFLEARN_PGLITE_MODULE=/Users/jliddar/.npm/_npx/ba4f1959e38407b5/node_modules/@electric-sql/pglite/dist/index.js NFL2026_CHECKOUT=/Users/jliddar/code/NFL2026-learning-runtime PYTHONPATH=packages/selflearn-core:. python -m pytest -q -rs tests/postgres
```

Logs are `/private/tmp/selflearning-critical46-bounded-compute-root-r2-20261016.log`,
`/private/tmp/selflearning-bounded-compute-all-node-sql-root-20261016.log` and
`/private/tmp/selflearning-bounded-compute-all-python-sql-root-20261016.log`.
[Job contracts and remaining integration](LEARNING_JOBS.md). S027 remains partial;
**50/99 (50.5%)** local tasks, no deployment or accuracy credit. Later evidence/
documentation commits are not newly tested releases. No production writes.

## Earlier durable jobs verification October 16 12:07 p.m. CDT

Source `d3a5693`, evidence `59d54a6`: **1,232 core/API passed, zero skips**,
**43/43 critical files >90%**, **7,884/8,194 = 96.2167%** executable lines and
stable measured hashes/heads. Four new modules reach 404/404 lines, not branch
or story acceptance. Complete SQL: **93 Node passed, zero skips**; **100 Python
passed / seven native-only skipped**, all terminal exit 0. One existing dependency
deprecation warning remains unsuppressed. Separate ledger/tool checks: **32 passed**.
[Pinned report](../../reviews/2026-10-16-critical43-durable-jobs-coverage.json)
uses NFL `383fbd63`. [Job behavior and limitations](LEARNING_JOBS.md) distinguish
real persistence/fitter recovery from the remaining triggers, hard worker bounds,
complete authoritative dataset assembly, credentials, alerts and hosted/native
acceptance. No new whole-task credit: **50/99 (50.5%)**. No accuracy gain or
operational live loop is claimed; evidence/docs commits are outside the tested
source SHA. Remote heads remained unchanged at 12:01 p.m. CDT; no cloud writes.

## Earlier follow-up — October 16, 2026, 11:18 a.m. CDT

Whole local-build task credit is **50/99 (50.5%)**, not story, live, statistical
or release completion. Only S016-T1 is reaccepted: ID-first injury producers and
an independently implemented validator pass 29 frozen/current/adversarial cases,
zero skips, exit 0. Full exact old/new data pins retain the historical audit;
common-input holds are 33→34 on `0d49a948`, 32→33 on `45b3cac4`, with only the
missed same-ID OUT added. Other numeric pool prices remain unchanged within the
controlled pair. Original upstream Elo/fantasy reads, provider completeness,
independently rederived depth, publication and accuracy are not proved.

NFL `6aafdfcf` adds complete conservative local-Python capture identity and
regular Git-mode checks. Fourteen focused capture cases plus one batch/framing
case pass; six cleanup cases pass without changing production timeouts. Full
run `27b43fd5` was green with 2,446 features, but subsequent `41a56a09` was red:
2,449 passed / one cleanup-fixture failure, exit 1. The new frozen full gate on
`6aafdfcf4b321f1085837766f3489c6340818c11` completes **2,453 feature tests,
zero failed/skipped**, contracts, smoke and all four model gates, terminal
exit **0**. Browser step 8 **SKIP**; no browser/native/hosted acceptance. Log:
`/private/tmp/nfl-injury-capture-full-gate-root-r5-20261016.log`. Later handoff-only
edits are not represented as another tested source/release generation.

Engine `58ea096`, coverage evidence `5314ca8`: **1,182 passed, zero skipped,
pytest exit 0**; **39/39 critical files >90%**, **7,480/7,790 = 96.0205%**
executable lines; all measured source/test/tool/head identities stable.
[Coverage report](../../reviews/2026-10-16-critical39-owner-evidence-coverage.json)
uses unchanged detached NFL `27b43fd5` and 29 enumerated source hashes. The new
typed reader's actual SQLite plus synthetic 330-receipt fitter reexecution does
not establish authenticated inputs or atomic adoption. It always returns held;
its Supabase-port composition is not verified by the older storage suites.
Those remain 86 Node passed / 96 Python passed with seven native-only skips,
exit 0. Separate progress/coverage-tool checks initially expose a stale count
assertion (31 pass / one fail); exact 39-target / 29-NFL-entry membership plus
explicit new-file requirements now pass all **32**, exit 0. They are not in the
1,182 count. No runtime/measurement tool change, new S015/S006/S028 task credit
or live-learning claim.

Three workers are out of workspace credits; only the coordinator continues.
The 7 a.m. CDT requested target was not met. No production migration, push,
deployment, source-data rewrite or model activation has occurred.

## Earlier follow-up — October 16, 2026, 3:10 a.m. CDT

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
