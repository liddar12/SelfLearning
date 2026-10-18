# Optimized NFL runtime handoff

Revision R1 — October 18, 2026, 3:25 p.m. CDT (America/Chicago).

The owner authorized publishing code with learning disabled and a handoff for
Claude. This is a partial runtime publication candidate, not authorization to
merge SelfLearning main, deploy Vercel, apply migrations, activate a pilot, or
adopt a model. Existing dated handoffs describe older work and are historical;
use this document for the scope of this candidate.

## Exact source and scope

The local branch `release/optimized-runtime-publish-20261018` at
`/Users/jliddar/code/SelfLearning-runtime-publish-20261018` starts from existing
remote PR #10 head `c1bf3d4773312c99ef0f30563c791525389fd5af`, branch
`build/nfl-feedback-runtime-20261015`. Its entire `packages/selflearn-core/`
tree, including all core tests, exactly matches optimized source
`f342cc3b90f83dacea4f2c1645019d6a34d617b2` in
`/Users/jliddar/code/SelfLearning-serialization-release-20261018`.

Relative to c1bf3d4, only these six code paths change:

- `.github/workflows/ci.yml`: repository import path for core tests importing tools.
- `packages/selflearn-core/selflearn_core/adapters/nfl_file_feedback.py`:
  explicit bounded partition profile and reuse of each private row's canonical
  encoded size. Existing per-row and aggregate depth, node, type, and byte guards
  remain enforced; no history truncation or model formula changes.
- `packages/selflearn-core/tests/test_nfl_file_feedback_partitioned.py`.
- `packages/selflearn-core/tests/test_nfl_file_feedback_serialization.py`.
- `tests/test_measure_critical_coverage.py`.
- `tools/measure_critical_coverage.py`: selected-checkout source isolation and
  coverage tooling corrections.

The two additional handoff/metadata files describe this publication candidate.
All other c1bf3d4 files, including hosted app/API/UI and migrations, remain at
their existing remote-base contents. This branch does not carry the many later
local architecture edits or large generated coverage reports.

## Verification and limits

The [parity metadata](reviews/2026-10-18-optimized-runtime-publication.json)
records the package, runtime, and test Git tree IDs. Package tree
`aa1c98fb1ea464f3dced50fbac7f93b243344030` contains 143 tracked files. Compare
that subtree after fetching any remote publication; a changed Git commit ID
alone does not mean runtime code changed.

On f342cc3, the three focused serialization/partition/file-feedback test files
passed 66 tests, with one actual-producer rehearsal skipped because
`NFL2026_CHECKOUT` was not supplied (exit 0, 2.23 seconds). The real NFL consumer's
exact-SHA loader also imported the correct module/profile under `-I -S -B`.
Logs remain local at `/private/tmp/selflearning-serialization-release-focused-20261018.log`
and `/private/tmp/selflearning-serialization-release-import-20261018.log`.

An earlier optimized full-suite run on
`4dc47fa3dad9721595d19909354fb885213e763d` passed 1,436 tests; its runtime/core
tests are identical to f342cc3. Its report remains locally at
`/Users/jliddar/code/SelfLearning-feedback-runtime/docs/reviews/2026-10-18-row-serialization-full-verification.json`.
This is not a fresh full-suite pass of this partial publication tree or the
current separate NFL release. Hosted/cloud behavior, native database execution,
model improvement, and production activation are not established here.

## Publication status and next work

No remote release SHA or new PR exists for this candidate yet. The connected
`github_create_blob` tool rejected the operation with: "MCP tool call requires
approval, but approval policy is never". No inline-tree, alternate API, or push
workaround was attempted. Normal publication from an allowed operator terminal
is required. Do not treat local evidence as a remote CI pass.

From the local candidate checkout, push its committed HEAD to a new remote
branch such as `release/optimized-nfl-runtime-20261018`, then open a **draft** PR
with base `build/nfl-feedback-runtime-20261015`. A main-based PR would mix this
small runtime delta with the much larger existing PR #10 foundation. Do not
merge either PR or alter production main as part of this handoff.

After publication, verify the exact remote package/runtime/test trees against
the metadata, inspect CI, and record the published SHA. NFL's separate selected
release needs that exact SHA only if/when it is configured to consume this
runtime; keep `config/selflearning_pilot.json` `enabled=false`. Repository
variables override the enabled/engine pair together, so inspect their effective
values before claiming disabled behavior. Root's NFL handoff supplies its
independent source/PR/test evidence. The selected NFL public-main fixes do not
constitute the full unreleased pilot, and no learning activation is authorized.

For subsequent validation, use the existing shared environment or install this
package's development dependencies, then run its focused tests and required CI.
Coordinate heavy/full suites so only one process owns that workload. Preserve
real producer/history failures and report skips explicitly; do not weaken gates
to obtain a green run. Any later activation requires a separate decision and
end-to-end acceptance evidence.
