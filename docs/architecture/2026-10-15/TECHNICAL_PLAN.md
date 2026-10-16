# SelfLearning architecture and implementation plan

Revision 1. Evidence checked October 15, 2026, 3:19 p.m. CDT, America/Chicago. Proposed delivery plan for the owner and Claude Code; not a production authorization.

Implementation note, October 15, 2026, 5:44 p.m. CDT: the R1 visual remains the
design baseline, not a live deployment diagram. See IMPLEMENTATION_HANDOFF.md
for the later locally tested journal/configuration checkpoint and remaining
release evidence. Backlog estimates describe the original scope, not newly
measured remaining effort.

## Recommendation

Keep NFL2026 on Netlify, the existing Python SelfLearning API on Vercel, and durable learning evidence in the existing Supabase project. Run bounded Python evaluation jobs through GitHub Actions initially. Use the same authenticated API for an operator dashboard and application MCP tools. Do not replace the NFL frontend, move training into browser requests, or add another hosting provider for the first release.

SelfLearning is a reusable prediction and evaluation engine, not an LLM that becomes more accurate merely by reading more prompts. It needs forecasts captured before events, trustworthy outcomes, comparable evaluations and a controlled way to return improvements. Questions and screenshots can supply proposed inputs; they cannot establish truth without evidence and, where necessary, owner confirmation.

Start the shared feedback loop with MY yardage props, which already have a receipt adapter. Capture Game and Week publication evidence concurrently, but do not apply a MY prop calibrator to moneylines, spreads, anytime touchdowns or joint parlay probabilities. Extend those tasks with their own adapters and grading contracts.

Product priority remains Parlays first, Slate second and Players third. Improve source/eligibility truth across all three, but validate the Parlay selection, grading and shared-loss experience first. Slate then exposes the same source authority and game-level traces; Players reuses the projection/feature lineage rather than growing another model truth source.

### How improvements feed the repositories

Generic receipt, persistence, evaluation and candidate-contract enhancements belong in `liddar12/SelfLearning`, with reusable tests and versioned interfaces. NFL injury/line/settlement rules belong in the NFL adapter or `liddar12/NFL2026`, not the generic core. NFL pins a tested SelfLearning package/commit and imports only supported artifacts/contracts. Learned candidates and evaluation records persist in Supabase; an approved non-sensitive serving artifact is pinned in NFL's reproducible generation inputs. Learning does not autonomously rewrite source code or push its own unreviewed changes. A new domain must use the shared core instead of copying an NFL-specific engine.

## Current state and evidence

| Component | Verified or reported state | Remaining requirement |
| --- | --- | --- |
| NFL source | Local release baseline `32c5bfbe`; PR #172 correctness integration is merged. Earlier production verification identified data refresh `674382a`. Current fetch attempts fail because this environment cannot resolve GitHub. | Refresh the exact source/data revision before release; do not call the local baseline the latest remote head. |
| NFL frontend | Existing vanilla JavaScript PWA; valid production address is `https://nfl2026.netlify.app`. | Preserve no-build architecture and generated JSON interface. |
| SelfLearning source | Published adapter checkpoint `4587231`; the dirty hosted foundation based on `aa5bbae` was preserved and reconciled into isolated local commit `b9ecd3f`. Later local journal work is detailed in the handoff. | Refresh and reconcile current remote main, then publish only after separate authorization. Local commits are not proof of deployed source parity. |
| Vercel operator credential | Owner confirms `SELFLEARN_OPERATOR_TOKEN` is configured for Production. | Test the deployed code's authenticated behavior. Do not recreate, disclose or download this token. |
| Supabase | Native project-scoped MCP verified `omavgimpnvrpuxqhfxpf`. Tables: `predictions`, `outcomes`, `registry`, `registry_scores`; RLS enabled on all four. | This proves management visibility, not API ingestion or learning. Exact application row counts were not verified; metadata reports zero. |
| Database migrations | Live history lists `20260921025211 hosted_foundation`. Local foundation is `20260921012817_hosted_foundation.sql`; local permission-fix file is empty. | Compare schema and migration contents before applying anything; never rerun CREATE TABLE setup blindly. |
| Security | Advisor warns that `public.rls_auto_enable()` is SECURITY DEFINER and callable by `anon` and `authenticated`. Four no-policy notices match the intended service-only foundation, subject to grant verification. | Review helper ownership, event-trigger use and grants; fix narrowly and test. RLS does not constrain a service key that bypasses it. |
| Existing integration | Local source has scoped NFL object/generation intake, retry-safe export, a FINAL bridge and typed shadow evaluation. Production connection, scheduled jobs and automatic promotion remain unverified or disabled. | Complete authoritative outcome capture/transport, scheduled evaluation, persisted candidate return and operating controls below. |
| Local persistence | SQLite contract tests plus a new canonical-text Supabase journal, additive migration, explicit backend selection and actual PostgreSQL-WASM tests exist locally. | Native concurrent-transaction CI, live PostgREST/grants, migration-history reconciliation and owner-approved deployment remain required. Never use SQLite as Vercel production state. |

Local isolated branches: NFL `build/week6-learning-runtime-20261015`; SelfLearning `build/nfl-feedback-runtime-20261015`. Existing dirty checkouts are preserved. No production migration, deployment, promotion or push is part of this plan delivery.

## System responsibilities

| Boundary | Owner and state authority | Inputs and outputs | Failure behavior |
| --- | --- | --- | --- |
| NFL producer and publisher | NFL2026 repository; authoritative offered contracts and serving policy | As-of injuries, schedule, projections, model versions → frozen offers, receipts, generation manifest and static JSON | Withhold only for documented eligibility reasons. Failed semantics block that generation; do not fabricate fallback data. |
| NFL export job | NFL Actions workflow; durable immutable outbox until acknowledged | Frozen generation envelopes → authenticated batches; response contains per-record acknowledgements/rejections | Retry same IDs; preserve unacknowledged records. No browser service key. An engine outage must not silently erase forecasts. |
| Shared API | SelfLearning repository; authentication, authorization and contract validation | Domain adapter records → durable transaction, status and read-only results | Reject conflicting IDs, unsupported schemas, mismatched namespaces and oversized requests; acknowledge only after commit. |
| Supabase store | Database; authoritative received times, immutable evidence, revisions and audit | Receipts, observations, publications, evaluation manifests, candidates, approvals | Atomic inserts, unique constraints, least-privilege roles and explicit correction chains; no destructive history rewrite. |
| Evaluation job | Versioned SelfLearning Python package; owns reproducible analysis, not NFL serving | Pinned receipt/outcome revision set + declared policy → metrics and shadow candidate | Stop or report partial coverage on missing vintages. Do not train on held-out outcomes or multiply samples by card copies. |
| NFL candidate consumer | NFL repository; final compatibility, promotion and rollback authority | Typed candidate + evaluation evidence + explicit approval → pinned artifact for a future generation | Reject incompatible/unapproved/stale artifacts; retain last approved compatible artifact or the explicitly unadjusted base. |
| Dashboard and application MCP | Shared API; read by default, scoped intake actions | Owner question/upload → proposed structured inputs; trace/status/evaluation results | Explain missing evidence and quarantine. Never treat a screenshot, market quote or chat answer as an authoritative outcome. |

MCP distinction: `supabase-selflearning` is a development/management connection. The proposed application MCP server exposes narrow learning tools, not arbitrary SQL or database-owner permissions to chat clients.

## Technical design

### Hosting and runtime

- Reuse FastAPI on Vercel for bounded HTTP ingestion, status, result retrieval and MCP. Use the existing small dashboard rather than a new frontend framework.
- Use Supabase Postgres and private Storage for persistence. Production must not use a local SQLite file or process memory as state authority. Keep the API near the database region; verify actual region and connection strategy before deployment.
- Start evaluation as a bounded, resumable Python Actions job triggered after successful ingestion plus `workflow_dispatch`. Use durable run manifests and leases, not a claim of exactly-once scheduling.
- Keep GitHub cron as a best-effort trigger. Pregame manual dispatch/checks and freshness alerts cover late runs. If measured runtime, concurrency or retry needs exceed this design, evaluate a durable workflow/queue as a separate change; no new paid service is needed to begin.
- Preserve the NFL stdlib gate and no-build PWA. Pin hosted production dependencies and lockfiles; keep test/render tools outside the NFL runtime dependency path.

### Credentials and authorization

| Credential | Location | Status or planned use |
| --- | --- | --- |
| `SELFLEARN_OPERATOR_TOKEN` | Vercel Production, server-side | Already configured per owner. Bootstrap owner-only operator API; never a Supabase key or automatic pipeline credential. |
| `SUPABASE_URL` | Vercel backend configuration | Must resolve to `https://omavgimpnvrpuxqhfxpf.supabase.co`; may already exist. Verify, do not assume missing. |
| `SUPABASE_SECRET_KEY` or supported legacy `SUPABASE_SERVICE_ROLE_KEY` | Vercel backend secret only | Presence and validity must be verified by a backend health/authorization test. Service role bypasses RLS; grants and server authorization still matter. |
| Proposed NFL ingest credential | Vercel verifier plus NFL GitHub Actions secret | Separate, revocable credential scoped to the owner, NFL adapter and receipt/outcome ingestion. Do not reuse the operator master token as the long-term pipeline identity. |
| Proposed evaluator credential | SelfLearning Actions and backend verifier | Read a declared dataset and append evaluations/candidates only; cannot approve or promote. |
| Future operator session | Dashboard/application MCP | Short-lived scoped session or user auth, replacing repeated bootstrap-token entry. Owner-only first; multi-user login requires tested tenant policies. |

Vercel Production secrets do not automatically appear in GitHub Actions or Preview. The current pilot is fixed, single-owner: it does **not** implement tenant/namespace isolation. Preview must use a separate staging database/project and credentials, not a presumed namespace in the production journal. Do not weaken a secret's visibility to make it downloadable. Environment changes require a deployment that actually consumes the new configuration; presence in settings is not proof of a working API.

The locally implemented journal additionally requires explicit
`SELFLEARN_JOURNAL_BACKEND=supabase`. Its default is disabled; existing backend
credentials alone never enable ingestion. After an authorized migration and
release, each configured request probes the journal schema before an ACK;
missing migration, permissions or configuration fails closed with 503. Do not
enable the switch now as a substitute for the release checks.

### Core contracts

The versioned generic envelope contains namespace, domain, task, canonical contract/event IDs, correlation group, base and served probabilities, input/output stages, model/calibrator versions, frozen features, source hashes/timestamps, forecast creation time and internal first observation. Domain adapters validate their own identity and settlement rules before sealing it.

Three clocks must stay distinct: source issue/availability time, forecast generation time and database receipt time. A later database import does not prove pre-event capture; an internally generated receipt does not prove public availability. Publication acknowledgements are separate immutable observations tied to the exact deployed generation and fetched content hashes. Hashes establish content integrity, not authentication or independently verified timestamps.

The NFL generation journal must run before display-card deduplication. Store immutable per-generation files and content-addressed source snapshots so concurrent publishers preserve both writers or fail loudly. Display IDs, learning receipt IDs and public-generation IDs have different lifecycles. Existing cards keep their original contracts and missing historical bases remain missing.

### Logical persistence model

This is a proposed schema model, not executable migration SQL. Reconcile existing tables first, then use reviewed additive migrations.

| Entity | Identity and invariants |
| --- | --- |
| `forecast_receipts` | Unique namespace + receipt ID; canonical content hash; append only; DB-owned received time; immutable base/served lineage. |
| `source_snapshots` | Content-addressed immutable source payload or private Storage pointer, checksum, coverage and source vintage. |
| `generation_manifests` | Exact code SHA, input hashes, schedule, eligibility audit, selection/joint policies and receipt IDs. |
| `publication_observations` | Generation + observed deployment + content hash + observer/time; generated-only and verified-publication states separate. |
| `ingestion_batches` and acknowledgements | Stable batch hash, per-record result, durable commit reference; same payload retry is a no-op, conflicting ID is rejected. |
| `outcome_revisions` | Receipt/contract/event link, immutable revision ID, immediate predecessor, authoritative source, resolved time and DB receipt time; correction does not overwrite the original. |
| `outcome_observations` | Immutable receipt/contract/event + measurement + component observation IDs/clocks, including unchanged confirmations. Authoritative indexed enumeration and a committed read watermark are required; append observations and any outcome revisions atomically. A local list of object IDs is not this store. |
| `evaluation_runs` | Pinned receipt IDs, outcome revision IDs, train/evaluation cutoffs, code/policy versions, exclusions and dataset manifest hash. |
| `candidate_artifacts` | Immutable typed transform, base compatibility, training manifest, evaluation references, artifact hash and shadow state. |
| `adoption_events` | Separate proposed/approved/active/held/rolled-back events with actor, reason and exact artifact; candidate creation is not permission to serve. |
| `job_leases` and run events | Bounded ownership with expiry and resumable checkpoint; prevent duplicate concurrent work without assuming trigger delivery exactly once. |
| `intake_items` | Private upload/question, provenance, parsed draft, owner confirmation and retention state; never a trusted label by default. |

Server-derived owner/namespace and task authorization is a requirement for a
future shared/multi-tenant service, **not a current pilot capability**. The
single-owner pilot uses a dedicated credential boundary and private journal
schema. Its receipt/object/generation/revision tables preserve canonical JSON
text, database-owned clocks and immediate-predecessor corrections. A global
transaction advisory lock serializes writers; keyset reads pin one committed
watermark across receipt, generation and revision views. The subsequent local
D1 checkpoint `3ed4cae` adds indexed observations and an observation-aware
global fence/atomic commit; it is not selected by the hosted API. This is a bounded
pilot, not a high-throughput or multi-tenant claim. Evaluation/candidate,
publication, adoption and job-lease entities above remain proposed.

RLS protects exposed tables; secret/service-role access also needs narrow grants
and server checks because it bypasses RLS. No anonymous policies are needed for
a service-only table. Avoid a new public SECURITY DEFINER function as a
permission workaround. The new migration does not repair the unrelated
existing `rls_auto_enable()` advisor finding or reconcile old migration IDs.

### API and adapter surface

Proposed versioned routes, not a claim these already exist:

Local implementation now provides `/api/v1/nfl/objects` and
`/api/v1/nfl/generations`; existing intake is `/api/uploads`. Generic batch,
outcome-transport, evaluation, candidate and approval routes below remain
proposals. A pure FINAL adapter and database revision method do not establish
an automated source-to-API outcome pipeline.

| Route or interface | Behavior |
| --- | --- |
| `GET /api/health` | Minimal public liveness; no secrets or record details. |
| `GET /api/status` | Authenticated code SHA, schema version, connected adapters, last durable ingest/evaluation and explicit capability flags. |
| `POST /api/v1/receipts/batches` | Validate supported adapter/schema; commit forecast receipts independently of outcomes; per-record acknowledgement. |
| `POST /api/v1/outcome-revisions/batches` | FINAL evidence and revision-chain validation; void/push/unknown are explicit domain states, not fabricated binary losses. |
| `GET /api/v1/evaluations/{id}` | Immutable manifest and metrics in authorized namespace. |
| `GET /api/v1/candidates` | Shadow/approved artifacts with explicit input stage and compatibility; no implicit promotion. |
| `POST /api/v1/intake` | Private validated question/image upload; bounded size/type, draft parse and confirmation state. |
| Adapter contract | Canonicalize → validate source/as-of → capture → resolve/revise → score → propose compatible candidate. NFL-specific rules stay outside generic persistence/scoring. |

The first production integration should support only declared tasks, reject unknown adapters and report unsupported coverage. Retry transport failures/429/5xx with bounded backoff; quarantine validation/conflict failures for repair. A batch with some invalid rows must not lose valid independent rows, but each acknowledgement must correspond to a committed record.

## Pipelines and observable behavior

### Forecast and publication pipeline

1. Fetch inputs and record real source vintages, status and freshness. Unknown injury status quarantines the identifiable row, not the whole feed; unlocatable corruption makes source authority explicitly unverified.
2. Generate projections and probabilities from approved model/base artifacts. Keep NFL sportsbook/prediction-market prices display/benchmark only.
3. Freeze supported receipts before MY display deduplication. Freeze raw Game/Week offers and their selection/joint-model versions without claiming shared calibration coverage.
4. Run a prepublication semantic gate bound to the exact generation: eligible identities, source freshness, game/week joins, base-feature parity, correction exactly once, no duplicate filler and manifest integrity. Keep R102 postpublication checks and R106 archive protection.
5. Publish the tested NFL code/data generation. Verify fetched production hashes and append a separate publication observation; never retrofit the original generated receipt to claim public publication.
6. Export immutable envelopes to SelfLearning. Persist acknowledgements; alert on unacknowledged records approaching kickoff. An outage leaves the outbox intact and the original offered probabilities unchanged.

### Outcome and learning pipeline

1. Resolve only authoritative FINAL game/stat evidence. Pending, in-progress, void and missing observations remain explicit exclusions or task-specific settlements.
2. Append outcome revisions with source and observed time; late corrections supersede by revision chain, not overwriting. Evaluation runs pin the revisions actually used.
3. Select canonical contracts using a declared first-eligible forecast policy. Preserve later vintages, but do not count repeated cards or snapshots as independent observations. Flag contradictory earliest forecasts/outcomes.
4. Compare identical frozen cohorts locally and in the engine: contract membership, served probability, log loss and Brier. The generation journal captures candidate-pool rungs; the existing NFL learner uses displayed MY offers. Pin the population explicitly and never substitute the larger pool for the offered cohort. Report excluded/unreconstructable coverage separately.
5. Train a shadow correction on immutable base inputs using only outcomes available before its declared cutoff. Split chronologically; group uncertainty by game and show per-position/market effects.
6. Return a typed candidate. NFL independently checks compatibility, validation evidence and explicit approval before a future generation may consume it. Historical forecasts are never repriced to make the record look better.

#### Observation-clock implementation boundary (local follow-up)

NFL can now capture one immutable, content-addressed input set: requested ESPN
scoreboards first, then nflverse player-stat and snap-count CSV bytes. Real
retrieval clocks, source profiles, audits and hashes are retained; unknown
upstream issue/update and exact game-finish times stay unknown. New opt-in
consumer paths use the capture without refetch/fallback, preserve required
aggregate history and FINAL-gate every supported market. The daily workflow is
not switched by this local work. Provider schedules differ and later corrections
remain possible: retrieval after observed FINAL does not prove a complete or
last provider revision. See the [nflverse source schedule](https://nflreadr.nflverse.com/articles/nflverse_data_schedule.html).

`scripts/build_learning_outcomes.py` joins original journal receipts to the exact
frozen pool/schedule, not today's roster or display-only score files. Required
yardage remains unknown when malformed; unjoined aliases/identities cannot be
turned into zero. R106 positive-snap/absent-stat zero and team-sheet absence are
explicit inferences, not official sportsbook settlement. Named zero-offense
participation remains ambiguous in this new mode.

The separate `nfl.observed_resolution_v2` adapter accepts observation-based
evidence without weakening strict provider-clock v1. Conservative outcome
availability is the maximum required captured source clock, while database
insertion remains a separate training-availability boundary. Stable measurement
identity excludes unrelated season-CSV changes, scores, and fetch timestamps.
Every forecast vintage retains its original base/served lineage.

**Observation records are distinct from outcome revisions.** If measurement A
is observed at 04:20, confirmed unchanged at 06:00, then an older conflicting B
from 05:00 arrives, outcome history alone cannot reject B: it still records
04:20. The v2 interface therefore emits an immutable observation record for
every accepted capture, including no-change captures. Later analysis consumes
the complete record set to fence stale component clocks. Exact old delivery
replays never revert a head; a genuinely newer A after B can be a new correction.
Missing observation proof for an existing v2 revision fails closed.

This interface and subsequent local D1 checkpoint are **not a complete hosted
pipeline**. Commit `3ed4cae` implements an indexed observation table, complete
fenced reads and one transaction committing observations with revisions before
ACK. Its explicit `NFLObservedJournal` and internal CAS job remain in the NFL
adapter layer; generic stores/scoring do not import NFL policy. New actual
Python-to-SQL fixtures enumerate the index, unlike the earlier fixture-owned ID
list. Native separate-connection concurrency and hosted grants/PostgREST remain
unverified; the required disposable-native CI gate is authored but not run here.
Neither local SQLite nor an in-memory list may become Vercel's state authority.

Outcome transport must also bind raw capture provenance, scope credentials and
bounded payloads. Large source captures do not fit a single API request: use
reviewed immutable chunk/private-storage contracts, not a raised HTTP-body
limit. Batch by a validated underlying player/game/stat group so contradictory
rungs or model vintages cannot be acknowledged independently. Hosted delivery,
scheduled evaluation, candidate return and NFL adoption remain separate gates.

### Calibration contract that must not be confused

Generic SelfLearning Platt currently means `sigmoid(a * confidence + b)`. NFL MY serving means `sigmoid(a + b * logit(base_probability))`, with input clipping, output clamp to `[0.05, 0.95]` and four-decimal rounding. The adapter currently exposes served probability as confidence. Copying generic coefficients would change the equation and can apply calibration twice.

Add a distinct typed NFL base-logit artifact: task/markets, input stage, coefficient meaning, base model version, clamp/rounding, training cutoff, receipt/outcome manifest and artifact hash. Keep the generic calibrator's meaning unchanged. Source-review correction: the existing MY layer requires at least 100 graded legs **in total**, at least two held-out week folds, strict pooled improvement against the unadjusted base and no held-out week worse against either base or the as-served incumbent. Incumbent ties are allowed; there is no per-fold 100-leg training minimum or 0.0015 margin. Its fitter is `scripts.backtest_parlay.fit_logistic`. A shared shadow fitter with different regularization, a 100-training-contract fold minimum or strict pooled incumbent improvement is a proposed policy, not an exact reproduction of R100. The platform uses the practical 0.0015 margin elsewhere. Recommend a versioned additional 0.0015 margin for the new shared return gate, subject to explicit policy review, rather than silently claiming it already governs MY. Preserve all existing task gates and never weaken a margin to force adoption.

With no qualifying legacy receipts, two held-out weekly folds normally require at least three chronological receipt weeks: one initial training week and two later evaluation weeks. If supported collection begins in Week 6, MY's earliest two-fold evidence is after Week 8, and may still be insufficient. That is distinct from R127's minimum two newly graded prospective weeks.

R127 train/serve feature work and weather stay shadow-only. R127 promotion requires at least two newly graded prospective weeks, game-clustered 95% interval for candidate-minus-incumbent log loss below zero, no position worse and the applicable reviewed practical improvement policy. Two weeks is an earliest checkpoint, not guaranteed statistical sufficiency. No refit on Week 5 or one TNF result to target 15/18.

## NFL accuracy work that runs alongside integration

- Preserve R125/RETIRED semantics, row-level quarantine and 12-hour injury freshness; review the ESPN 25-row cap and missing-last-FINAL-player guard separately.
- Preserve R126 top joint-probability Game-card ranking and spread fallback. Its documented retrospective replay is 59/188 to 72/189 on Weeks 2–5 recovered on-card candidate legs, not a full historical candidate universe.
- R130 headline uses actual pre-kickoff offer evidence, separate from settlement accounting. Acceptance reference: Weeks 1–5 all-scope as-offered 17/66, 13/66, 21/66, 22/65, 14/63. Week 5 archive settlement includes 16/65 counting pushes; Week cards show 1 full + 3 reduced + 19 losses on 23 cards. Missing full history must display unavailable/partial, not hardcoded reference counts as a computation.
- Keep highest individual hit probability as the default. Compare diversified alternatives on the same frozen candidates and expose shared-leg/game risk. A cap can reduce one upset's blast radius while lowering expected winners; owner approval is required to change the objective. Never fill missing slots with duplicates.
- Report effective Elo k = 20 without activating 25 to match stale metadata. Weather must preserve forecast issue/receipt time, game window and roof state; missing rain is unknown, not zero.

## Operator and chat experience

The initial operator pilot has connection/status, forecast trace, evaluation/cohort comparison, and candidate review/adoption history. Status and R130 reporting can run early; candidate views require the evaluation/return contracts. Private intake/confirmation and OCR belong to the later screenshot milestone, not the base estimate. Every screen distinguishes measured vs estimated, generated vs publication-verified, pending vs FINAL, and shadow vs active. An owner action maps to an implementation and visible evidence in the backlog.

Application MCP begins with `learning_status`, `prediction_trace`, `evaluation_report` and `candidate_compare` as read-only tools. Intake is an explicitly scoped write action. Promotion is not exposed as an unrestricted chat tool. The chat client's image support is optional because the upload dashboard remains available.

A screenshot follows upload → draft parse → owner confirmation of game/player/line/direction/stake/quote → canonical contract join → analysis with uncertainty. File metadata is untrusted. A parsed ticket does not become a pre-event published forecast or a FINAL result. Begin with the foundation's 3 MiB image limit; use private storage, short-lived access and explicit retention policy. OCR/LLM integration is a separate optional feature, not required for receipt capture or statistical calibration.

Questions without objective outcomes are retrieval/evidence tasks. Store answer provenance and review status if requested; do not call conversational feedback an objective training label. Financial shorts/options, elections and sports need their own event horizons, labels, baselines and domain policies. NFL's market-price input ban stays in the NFL adapter; it does not globally ban time-stamped financial prices from a future financial model. No bets, trades, payments or order execution are authorized.

## Team and parallel delivery

This session has four execution slots, not an unlimited workforce. Use a coordinator plus three file-owned implementation lanes; reuse freed slots for independent QA. Claude Code is not a live connected collaborator here. Its handoff/review role is a proposed cross-session workflow, supported by committed artifacts and exact SHAs.

| Role | Ownership | Handoff and collision rule |
| --- | --- | --- |
| Technical lead/coordinator | Architecture, contracts, dependencies, integration manifest and owner questions | Sole editor of shared contract schema in each change; freezes interfaces before fan-out. |
| NFL producer lane | Availability, generation journal, semantic gate, exporter and consumer | Own NFL producer/publisher paths; do not edit engine persistence. |
| Shared-engine lane | Adapter, append-only store/API, authorization and migration proposal | Own SelfLearning runtime/store paths; cloud DDL only after owner approval. |
| Evaluation and UX lane | Honest cohort metrics, frozen replay, candidate evaluator, dashboard/MCP status | Partition scoring vs UI files as separate tasks; no shared-file parallel edits. |
| Independent QA/reviewer | Fault injection, actual-producer parity, browser acceptance and adversarial review | Rotate into a freed slot; reviewer did not author the change being approved. |
| Release coordinator | Claude Code per repository workflow, or another explicitly designated operator | Reconcile current main, run the full gate on exact SHA, prepare rollback; owner approves production. |

Write one task brief per partition with allowed files, contracts, commands and acceptance criteria. Integrate in dependency order, not as one giant branch. Never run two NFL full gates/browser servers simultaneously. Parallelism helps independent implementations; it cannot compress the required prospective observation period.

## Roadmap and effort

Estimates are engineering effort including focused tests, not promises or measured velocity. Four-slot execution can overlap independent work, but integration, cloud permissions and observation periods remain sequential.

| Milestone | Dependency and exit criteria | Effort and risk |
| --- | --- | --- |
| M0 Reproducible foundation | Reconcile hosted dirty work + adapter; verify secret names privately, code SHA, schema history and helper permissions in test environment | 1–2 engineer-days; risk: uncommitted/deployed drift and migration duplication. |
| M1 Capture before outcomes | M0 contract frozen; generation journal independent of display IDs, immutable snapshots, exact-generation gate and visible coverage | 2–3 days; risk: retroactive evidence or concurrent publisher data loss. |
| M2 Durable shadow loop | M0 + M1; authenticated pending ingest, acknowledgement/outbox retry, Supabase persistence, FINAL revisions and outage recovery | 3–4 days; risk: acknowledgement before commit, ID conflicts or missing forecasts. |
| M3 Trustworthy evaluation and return | M2; cross-repo cohort/score parity, compatible base-logit candidate, chronological evaluation, independent NFL rejection/approval/rollback tests | 3–5 days; risk: leakage, equation mismatch, dependence and subgroup regression. |
| M4 Operator pilot and NFL completion | R130 can ship independently of shared ingestion; status starts with foundation readiness. Concentration/R127/weather analysis needs E03 evidence, and candidate views need E04 return contracts. | 2–4 days for bounded pilot work; risk: scope creep and incomplete historical evidence. |
| M5 Controlled adoption | M3 + independent QA + sufficient prospective evidence + owner authorization | 0.5–1 day engineering plus statistical observation: MY normally needs at least three receipt weeks for two held-out folds; R127 needs at least two newly graded weeks. Either may need longer. |
| M6 New domain adapters | Stable core and second synthetic domain fixture first; one real domain pilot at a time | 2–5 days per bounded adapter before data acquisition/rights and validation; finance/options materially larger. |

The supported NFL MY shadow-to-controlled-return build is approximately 10–18 engineer-days, with overlap. The dashboard, screenshot understanding and additional domains extend scope; do not promise all domains within that estimate. The first useful milestone is durable prospective collection, not a claimed immediate accuracy increase. Week 6's already-issued regime remains intact.

## Acceptance and release gates

Each backlog story names observable tests; a passing test is evidence only for its stated scope. Require complete acceptance-criterion-to-test traceability and at least 90% measured coverage of new critical-path code in each implemented story before its QA sign-off. Establish appropriate test-only instrumentation where missing without adding runtime dependencies to the NFL gate. Unmeasured coverage remains an open acceptance item. A percentage is not a substitute for required fault/edge tests and no unmeasured coverage percentage is claimed.

Required end-to-end demonstration: pending NFL receipt → semantic validation → durable upload acknowledgement → duplicate retry → authoritative FINAL revision → identical NFL/engine cohort and scores → shadow candidate from earlier immutable bases → incompatible candidate rejected → explicit approved compatible artifact used exactly once on a new forecast → second outcome/evaluation cycle → outage and rollback with old history unchanged.

Run NFL `python3 scripts/validate_data.py`, `bash tests/smoke.sh`, `node --test tests/feature/*.mjs` and the configured Playwright/full gate once at a time on the exact candidate SHA. Run the SelfLearning core suite in the documented Python environment and hosted API/security/store tests on a reproducible checkout. Include non-NFL synthetic domain tests, batch partial-failure, concurrent writer, crash/retry, revision forks, unauthorized namespace, invalid timestamps and sensitive-data log tests.

Before release record both repository SHAs, package/contract versions, generated data hashes, DB migration version, effective model/calibrator, deployment ID, environment and rollback target. Verify deployed public hashes, authenticated status and allowed/non-allowed database actions. A smoke fixture that writes durable cloud data is a separately authorized test; do not disguise it as a read-only probe.

Rollback changes future serving to the last approved compatible artifact or reverts the application code with schema compatibility verified. It does not erase migrations, original offers or outcome revisions. Restore tests and migration forward-fix plans are required before production activation.

## Risks and mitigations

| Risk or single point of failure | Mitigation and observable check |
| --- | --- |
| Supabase outage | Durable immutable NFL outbox, bounded retry and backlog alert; fault test proves no loss or false acknowledgement. NFL serving does not require a synchronous engine response. |
| Vercel API outage or cold start | Retry transport failures; small idempotent batches; status separates API liveness from persistence readiness. |
| Actions cron delay | Trigger after pipeline completion, explicit pregame dispatch/check and deadline alerts; never call schedule an exact SLA. |
| Broad operator/service credential | Separate ingest/evaluator capabilities, grants, server-derived namespace, secret redaction and rotation test; no browser/agent arbitrary SQL. |
| Concurrent Claude/pipeline changes | Isolated worktrees, disjoint ownership, immutable generation paths and exact-SHA rebase/re-gate before release. |
| Wrong or revised outcome | FINAL/source validation and append-only correction chain; old evaluations retain their original outcome revision IDs. |
| Hindsight or duplicate sample | Real observation/publication evidence, canonical contract policy, chronological splits and game-clustered uncertainty. |
| Candidate makes a subgroup worse | Per-position/market/week gates and practical margin; hold if evidence is insufficient even when pooled mean improves. |
| One loss defeats many cards | Explicit exposure/P(0 winners) display and frozen diversified comparison; objective choice remains the owner's. |
| Upload prompt injection or false labels | Treat file/text content as untrusted proposed input, require identity/quote confirmation, separate parser from authorized actions and truth sources. |

## What the owner can do now

No additional operator-token setup is needed. Do not paste token values into chat. If convenient, confirm the backend environment contains `SUPABASE_URL` and one supported backend secret key; they may already be installed. No need to recreate the Supabase project, rerun table setup, buy another provider or enable automatic promotion.

The remaining manual gates are approval of the reconciled database migration/production release, a narrow credential handoff only if automation cannot configure it under authorized access, and later the choice of diversified objective or candidate activation. Connection verification should be performed by the implementation team where permissions permit. This session's Vercel connector cannot access the named team, its deployment fetch requires unavailable approval, and shell network resolution is restricted; those checks remain unverified rather than a demand to recreate credentials.

Useful links: [Supabase project](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf), [Security Advisor](https://supabase.com/dashboard/project/omavgimpnvrpuxqhfxpf/advisors), [Vercel environment settings](https://vercel.com/liddar-terminal/self-learning/settings/environment-variables), [NFL2026 repository](https://github.com/liddar12/NFL2026), [SelfLearning repository](https://github.com/liddar12/SelfLearning).

## Sources and traceability

- NFL `docs/WEEK6_CLAUDE_TO_CODEX_HANDOFF.md` revision 2 and `docs/qa/RCA_2026-10-15_PARLAY_ACCURACY.md`, local release baseline `32c5bfbe`.
- NFL `scripts/learning_receipts.py`, `scripts/build_my_cards.py`, `scripts/build_leg_pool.py`, `scripts/backtest_leg_pool.py`, and existing contract schemas.
- SelfLearning `selflearn_core/adapters/nfl.py`, `probability_receipts.py`, `scoring/calibration.py`, checkpoint `4587231`; local unpublished `services/web/application.py` and hosted foundation migration.
- Native project-scoped Supabase table/migration/security-advisor results, October 15, 2026; owner statement about the Production operator token.
- [Vercel FastAPI hosting](https://vercel.com/docs/frameworks/backend/fastapi) supports the existing Python API choice; [environment configuration](https://vercel.com/docs/environment-variables) explains environment scoping.
- [Supabase API security](https://supabase.com/docs/guides/api/securing-your-api) and [API keys](https://supabase.com/docs/guides/getting-started/api-keys) support the separate backend-key/grants/RLS design.
- [GitHub scheduled workflow behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule) supports best-effort scheduling rather than precise pregame timing guarantees.

See `BACKLOG.md` and `backlog-source.mjs` for task-level acceptance and dependency mapping. `architecture.html` is the editable visual source; `SelfLearning_Architecture_R1.pdf` is the reviewed visual export. Planning approval is not completion of the implementation backlog.
