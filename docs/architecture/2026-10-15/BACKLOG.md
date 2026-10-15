# SelfLearning delivery backlog

R1. Evidence: 2026-10-15 15:19 CDT (America/Chicago). Export: October 15, 2026 at 3:44 PM America/Chicago.

12 epics, 31 features, 31 user stories, 93 tasks and 92 acceptance criteria. Test identifiers below are required coverage, not a claim that every named test exists or passes. No production authorization is implied.

Use one file-owned task brief per implementation partition. Story-level prerequisites override aggregate epic integration dependencies: R130/S012 and existing eligibility/S016 are independent NFL correctness work. S028 defines the release process; each release manifest must bind every included story and its acceptance evidence, not assume all milestones are complete. Independent QA verifies exact revisions. See TECHNICAL_PLAN.md for assumptions, current evidence, rollout and timing.

## E00 Reproducible hosted foundation and least privilege

Milestone: M0. Owner: Shared-engine lane. Depends on: none.

Features: E00-F1 Source and deployment identity; E00-F2 Schema reconciliation and security; E00-F3 Credential readiness.

### S001 Reproduce the hosted service from source

Feature: E00-F1. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: none; independently deliverable.

As an operator, I can identify and rebuild the exact API so local success corresponds to a deployable revision.

Tasks:

- S001-T1: Inventory and isolate unpublished hosted files without overwriting user changes.
- S001-T2: Integrate hosted foundation with the receipt adapter and pin dependencies.
- S001-T3: Expose backend SHA, package/contract versions and capability flags.

Acceptance and required evidence:

- S001-AC1: A clean checkout installs and passes hosted/core tests using the pinned toolchain. Required test: FOUNDATION-01 clean-checkout test.
- S001-AC2: Authenticated status identifies the running revision; unwired NFL ingestion and promotion remain false. Required test: FOUNDATION-02 deployed identity acceptance.

### S002 Reconcile the live schema without recreating tables

Feature: E00-F2. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S001.

As the owner, I can approve one reviewed migration with existing data and required trigger behavior preserved.

Tasks:

- S002-T1: Compare live hosted_foundation migration/schema with the local different migration ID.
- S002-T2: Review rls_auto_enable function grants, ownership and event-trigger use; create a new scoped migration through CLI.
- S002-T3: Test forward migration, least-privilege access and compatible rollback on a test database.

Acceptance and required evidence:

- S002-AC1: Existing tables/records survive migration and the original CREATE TABLE setup is not blindly reapplied. Required test: DB-01 migration fixture.
- S002-AC2: Public roles cannot execute unintended privileged helpers; required automatic RLS behavior still works. Required test: SEC-01 function privilege matrix.
- S002-AC3: Service-only tables remain unavailable to unauthorized browser roles, with explicit grant tests. Required test: SEC-02 table access matrix.

### S003 Verify scoped credentials without disclosing them

Feature: E00-F3. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S001.

As the owner, I can see whether the backend is ready without copying production secrets into chat.

Tasks:

- S003-T1: Retain the already-configured Production operator token; verify required variable names privately.
- S003-T2: Validate backend URL/key and implement separate NFL-ingest/evaluator capability checks.
- S003-T3: Document environment isolation and authorized rotation/revocation procedure.

Acceptance and required evidence:

- S003-AC1: API rejects missing, wrong, revoked or wrong-scope credentials without logging secret values. Required test: SEC-03 credential/scope and log-redaction tests.
- S003-AC2: Preview cannot read or write Production data; the NFL PWA has no backend secret. Required test: SEC-04 preview isolation and browser-secret scan.
- S003-AC3: A token present only in Vercel is not reported as a configured GitHub pipeline credential. Required test: SEC-05 pipeline-credential readiness.

## E01 Prospective forecasts and publication lineage

Milestone: M1. Owner: NFL producer lane. Depends on: E00.

Features: E01-F1 Generation journal; E01-F2 Public observation; E01-F3 Exact generation semantic gate.

### S004 Capture learning evidence before display dedupe

Feature: E01-F1. Status: implemented locally; focused tests passed; review pending. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: none; independently deliverable.

As an evaluator, I retain each valid new forecast even when its displayed card already exists.

Tasks:

- S004-T1: Capture all supported pool receipt vintages before as_of and card-ID early returns.
- S004-T2: Store immutable generation envelopes and content-addressed source snapshots.
- S004-T3: Keep candidate-pool and actually offered populations distinguishable.

Acceptance and required evidence:

- S004-AC1: Reoffering an existing display card captures a new valid receipt without changing historical cards. Required test: JOURNAL-01 duplicate-display/new-receipt regression.
- S004-AC2: Exact replay creates no extra receipt; late, malformed and unsupported rows have explicit rejection reasons. Required test: JOURNAL-02 rejection/idempotency matrix.
- S004-AC3: Concurrent distinct generation publishes survive; conflicting content under one ID fails loudly. Required test: JOURNAL-03 publisher race test.

### S005 Separate generated receipts from public availability

Feature: E01-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S004.

As a user, I know whether a prediction was actually available before its event, rather than merely generated internally.

Tasks:

- S005-T1: Define immutable generation and publication observation contracts.
- S005-T2: Verify deployed content hashes and deployment identity after publication.
- S005-T3: Record actual observation time and migration from display identifiers without historical backfill.

Acceptance and required evidence:

- S005-AC1: Internal capture alone never produces verified-publication status. Required test: PUB-01 generated-vs-public test.
- S005-AC2: A mismatched deployment/content hash cannot attest the generation; a late observation cannot prove earlier availability. Required test: PUB-02 hash/time mismatch test.
- S005-AC3: A historical missing base/publication timestamp remains missing rather than reconstructed. Required test: PUB-03 legacy non-backfill test.

### S006 Block incoherent generations before publication

Feature: E01-F3. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S004.

As the release owner, I can prevent a mixed-source or ineligible slate from being published.

Tasks:

- S006-T1: Bind the gate to the exact source hashes, code/model/policy versions and output manifest.
- S006-T2: Validate source freshness, eligibility joins, game/week identity and exactly-once calibration.
- S006-T3: Wire the prepublication gate while preserving R102 postpublication and R106 archive protection.

Acceptance and required evidence:

- S006-AC1: A stale injury source, invalid identity, changed input hash or double-calibrated receipt rejects the generation. Required test: GEN-01 semantic fault matrix.
- S006-AC2: A valid generation with a row-local unknown status keeps unrelated eligible players. Required test: GEN-02 row quarantine acceptance.
- S006-AC3: Gate evidence names the precise generation and does not certify moving main. Required test: GEN-03 immutable-generation binding.

## E02 Durable ingestion and outcome corrections

Milestone: M2. Owner: Shared-engine lane. Depends on: E00, E01.

Features: E02-F1 Pending prediction intake; E02-F2 Retry-safe persistence; E02-F3 Outcome revisions.

### S007 Ingest pending predictions before results

Feature: E02-F1. Status: implemented locally; focused producer-parity tests passed; review pending. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S004.

As an evaluator, my training population is not selected according to which bets later have known outcomes.

Tasks:

- S007-T1: Extend the adapter to accept unresolved cards, pools and immutable generation envelopes.
- S007-T2: Validate real source clocks, kickoff evidence and independent NFL producer parity.
- S007-T3: Persist receipts independently of FINAL observations.

Acceptance and required evidence:

- S007-AC1: A pending eligible forecast is accepted with no fabricated outcome. Required test: ADAPTER-01 pending intake.
- S007-AC2: Source/serving/hash mismatch, late internal observation and unknown source times are explicitly quarantined. Required test: ADAPTER-02 provenance rejection matrix.
- S007-AC3: Actual learning_receipts and journal producer outputs pass cross-repository integration tests. Required test: ADAPTER-03 real-producer parity.

### S008 Acknowledge only durable immutable records

Feature: E02-F2. Status: local SQLite contract implementation in progress; hosted Supabase/export path planned. Effort: 1.5–2.5 engineer-days. Risk: High. Prerequisite stories: S002, S003, S007.

As an operator, I can retry failed uploads without losing or overwriting forecasts.

Tasks:

- S008-T1: Implement versioned append-only store contracts and additive Supabase persistence.
- S008-T2: Build per-record acknowledgements and a durable NFL outbox/exporter.
- S008-T3: Test crashes, partial invalid batches, concurrent writers and backlog alerts.

Acceptance and required evidence:

- S008-AC1: Same ID/same payload is a no-op; same ID/different payload is a conflict, not an upsert. Required test: INGEST-01 duplicate/conflict tests.
- S008-AC2: Acknowledgement follows committed persistence; a crash before commit leaves an unacknowledged retry. Required test: INGEST-02 crash/ack test.
- S008-AC3: An engine outage preserves the outbox and serving does not wait synchronously for the engine. Required test: INGEST-03 concurrent writer/outage test.

### S009 Append authoritative outcome revisions

Feature: E02-F3. Status: planned. Effort: 0.5–1 engineer-day. Risk: High. Prerequisite stories: S008.

As an analyst, I can explain corrections without changing what the original forecast or earlier evaluation said.

Tasks:

- S009-T1: Validate FINAL source evidence and exact receipt/contract/event joins.
- S009-T2: Append immutable revisions with immediate predecessor, reason and server receipt time.
- S009-T3: Pin revision IDs in evaluations and expose correction history.

Acceptance and required evidence:

- S009-AC1: Pending, in-progress, void and missing observations never become a false binary loss. Required test: OUTCOME-01 status/join tests.
- S009-AC2: Conflicting revision IDs, missing predecessors and forks fail explicitly. Required test: OUTCOME-02 revision chain/concurrency tests.
- S009-AC3: A correction changes the latest resolution view while preserving original records and prior evaluation manifests. Required test: OUTCOME-03 pinned-history test.

## E03 Chronological evaluation and trustworthy records

Milestone: M3. Owner: Evaluation and UX lane. Depends on: E02.

Features: E03-F1 Common cohort and scores; E03-F2 Temporal and grouped evaluation; E03-F3 Honest NFL record.

### S010 Match NFL and engine membership and scores

Feature: E03-F1. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S007, S009.

As the owner, I can compare evaluations without hidden denominator or probability changes.

Tasks:

- S010-T1: Define canonical first-eligible contract selection and explicit exclusion reasons.
- S010-T2: Compare exact receipt/outcome revision sets across producer and engine.
- S010-T3: Verify served probability, log loss and Brier against independent numerical oracles.

Acceptance and required evidence:

- S010-AC1: Both systems select identical IDs on the same supported cohort and match declared numeric tolerances. Required test: EVAL-01 membership parity.
- S010-AC2: Later vintages, duplicate cards and overlapping snapshots do not multiply independent samples. Required test: EVAL-02 scalar oracle/tolerance.
- S010-AC3: Missing/legacy bases and unresolved outcomes reduce reported coverage, not silently the denominator. Required test: EVAL-03 duplicate/exclusion accounting.

### S011 Freeze evidence before fitting or evaluating

Feature: E03-F2. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S010.

As an analyst, I can reproduce a candidate result without post-event information leaking into it.

Tasks:

- S011-T1: Freeze training/evaluation cutoffs, received-time constraints, dataset hashes and revisions.
- S011-T2: Implement chronological held-out folds and game-clustered paired intervals.
- S011-T3: Report per-position/market/week effects and honest insufficient-evidence states.

Acceptance and required evidence:

- S011-AC1: Changing held-out outcomes cannot change earlier fitted coefficients or cohort membership. Required test: EVAL-04 lookahead perturbation.
- S011-AC2: No game/correlation group crosses train and evaluation boundaries unnoticed. Required test: EVAL-05 group leakage test.
- S011-AC3: The report gives event/group counts; an independent-card interval is never attached to overlapping cards. Required test: EVAL-06 clustered interval oracle.

### S012 Show pre-kickoff accuracy separately from settlement

Feature: E03-F3. Status: local implementation in progress; full-history reference verification unavailable. Effort: 1–2 engineer-days. Risk: Medium. Prerequisite stories: none; independently deliverable.

As an NFL user, I can distinguish genuine forecasts from retrospective or reduced-leg results.

Tasks:

- S012-T1: Recover last pre-kickoff offered cards from sufficient Git history and canonical grades.
- S012-T2: Expose as-offered and settlement views with push/void and late-exclusion accounting.
- S012-T3: Update mobile record presentation with coverage and provenance warnings.

Acceptance and required evidence:

- S012-AC1: With complete reference history, Weeks 1–5 as-offered headline is 17/66,13/66,21/66,22/65,14/63. Required test: R130-01 full-history reference replay.
- S012-AC2: Week 5 settlement remains separately labeled: 16/65 incl. push; Week cards 1 full +3 reduced +19 losses on23. Required test: R130-02 settlement oracle.
- S012-AC3: Shallow or insufficient evidence displays unavailable/partial, never reference numbers hardcoded as measured results. Required test: R130-03 unavailable-history/browser test.

## E04 Compatible candidates and controlled adoption

Milestone: M3. Owner: Evaluation and UX lane with NFL producer lane. Depends on: E03.

Features: E04-F1 Typed NFL calibration; E04-F2 Independent NFL gate; E04-F3 Activation and rollback.

### S013 Train the correct transform from immutable bases

Feature: E04-F1. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S010, S011.

As a model owner, I can propose a correction without changing coefficient meanings or calibrating twice.

Tasks:

- S013-T1: Define the NFL base-logit artifact with input stage, coefficients, clamp/round and base version.
- S013-T2: Fit only earlier eligible immutable bases, leaving generic confidence-Platt behavior unchanged.
- S013-T3: Evaluate exactly the rounded transform the NFL producer will serve.

Acceptance and required evidence:

- S013-AC1: Generic sigmoid(a*confidence+b) artifacts are rejected at the NFL boundary. Required test: CAL-01 generic artifact rejection.
- S013-AC2: Applied probabilities match learning_receipts.serve_probability across edge and ordinary inputs. Required test: CAL-02 actual-producer transform parity.
- S013-AC3: A served probability cannot substitute for the base input and a mismatched base model cannot activate. Required test: CAL-03 once-only/base-version tests.

### S014 Require comparative evidence before approval

Feature: E04-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: High. Prerequisite stories: S013.

As the owner, I can see why a candidate is held even when its aggregate score looks better.

Tasks:

- S014-T1: Evaluate against the actual as-served incumbent on the identical held-out cohort.
- S014-T2: Preserve existing task gates; propose a separately versioned 0.0015 additional margin for the shared return gate.
- S014-T3: Return a shadow artifact with manifest and rejection reasons, not an executable serving instruction.

Acceptance and required evidence:

- S014-AC1: Worse incumbent comparison, subgroup harm or insufficient evidence produces a hold; current MY requires100legs and2held-out week folds, normally3receipt weeks. Required test: PROMOTE-01 margin/incumbent tests.
- S014-AC2: R127 promotion requires at least two newly graded weeks, clustered upper CI below0 and no position worse. Required test: PROMOTE-02 subgroup/CI/sufficiency tests.
- S014-AC3: No evaluation or receipt upload automatically promotes a model; a changed margin policy needs explicit review. Required test: PROMOTE-03 shadow-only test.

### S015 Adopt only an approved pinned artifact

Feature: E04-F3. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S014, S028.

As an operator, I can trace and reverse a future model change without rewriting historical forecasts.

Tasks:

- S015-T1: Implement explicit owner approval/adoption events and exact artifact hash checks.
- S015-T2: Materialize the approved compatible artifact for a new NFL generation.
- S015-T3: Exercise two feedback cycles, outage hold and compatible rollback.

Acceptance and required evidence:

- S015-AC1: Unapproved, stale, incompatible or tampered artifacts leave serving unchanged. Required test: RETURN-01 rejection matrix.
- S015-AC2: A newly approved artifact is applied once and its effective version is visible in the next receipt/UI. Required test: RETURN-02 two-cycle end-to-end.
- S015-AC3: Rollback restores future serving while old receipts, revisions and evaluation manifests remain byte-identical. Required test: RETURN-03 outage/rollback test.

## E05 NFL eligibility and fresh source authority

Milestone: M4. Owner: NFL producer lane. Depends on: E01.

Features: E05-F1 Availability semantics; E05-F2 Truncation and missed-game evidence.

### S016 Preserve row-local quarantine and RETIRED semantics

Feature: E05-F1. Status: core fixes already merged; current baseline regression required. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: none; independently deliverable.

As an NFL user, I do not receive an ineligible prop or lose unrelated candidates because of one unknown feed status.

Tasks:

- S016-T1: Revalidate current Week6 eligible/withheld players under the merged vocabulary.
- S016-T2: Test every RETIRED consumer and duplicate conflicting source rows.
- S016-T3: Enforce and expose the 12-hour freshness/authority reason at generation.

Acceptance and required evidence:

- S016-AC1: Fresh ok input withholds no player without a documented eligibility reason. Required test: ELIG-01 current-input diff.
- S016-AC2: An identifiable unknown status quarantines that row, not800 unrelated rows. Required test: ELIG-02 status/quarantine matrix.
- S016-AC3: RETIRED never means healthy and uncertain source authority never becomes fabricated medical zero. Required test: ELIG-03 freshness/duplicate test.

### S017 Detect incomplete injury reports honestly

Feature: E05-F2. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S016.

As a model owner, I do not treat a player dropped from a capped report as newly healthy.

Tasks:

- S017-T1: Inventory ESPN pagination/25-row cap and source completeness evidence.
- S017-T2: Shadow a last-FINAL did-not-play guard with newer clearance precedence.
- S017-T3: Replay eligibility changes before authorizing production behavior.

Acceptance and required evidence:

- S017-AC1: A capped feed is not labeled complete merely because it has800 rows. Required test: ELIG-04 capped-feed fixture.
- S017-AC2: A dropped IR/missed-last-game fixture remains ineligible absent documented newer clearance. Required test: ELIG-05 clearance precedence.
- S017-AC3: False-positive withholding and unknown coverage are reported before activation. Required test: ELIG-06 frozen eligibility replay.

## E06 Slate selection and concentrated parlay risk

Milestone: M4. Owner: Evaluation and UX lane. Depends on: E01, E03.

Features: E06-F1 Frozen reselection control; E06-F2 Explicit portfolio objective.

### S018 Replay selection rather than reprice shipped legs

Feature: E06-F1. Status: planned. Effort: 1–2 engineer-days. Risk: Medium. Prerequisite stories: S011.

As an analyst, I can distinguish a genuine selection comparison from scoring the same bets twice.

Tasks:

- S018-T1: Freeze recovered candidates, source scope and control/selection versions.
- S018-T2: Run R126 vs R74-consistent control on post-R74 weeks; inventory missing full-pool evidence.
- S018-T3: Add slate-reselection regression gate and per-week coverage report.

Acceptance and required evidence:

- S018-AC1: Reference recovered on-card replay reproduces59/188→72/189 without claiming a complete original universe. Required test: REPLAY-01 frozen control reproduction.
- S018-AC2: A candidate losing winners in any required control week fails its declared regression gate. Required test: REPLAY-02 never-regress fixture.
- S018-AC3: Unavailable inputs are not replaced with current injuries, ratings or observed weather. Required test: REPLAY-03 provenance coverage audit.

### S019 Make diversification an explicit choice

Feature: E06-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S018.

As the owner, I can weigh highest individual hit probability against reducing the damage of one upset.

Tasks:

- S019-T1: Compare highest-hit and diversified policies on identical Game/Week/MY candidates.
- S019-T2: Show shared leg/game exposure, expected winners and model-implied probability of zero winners.
- S019-T3: Record owner objective; enforce approved caps or report infeasible without duplicate filler.

Acceptance and required evidence:

- S019-AC1: Default highest-hit behavior remains unchanged unless the owner approves a different objective. Required test: PORTFOLIO-01 common-candidate comparison.
- S019-AC2: The comparison shows reduced exposure and any lower expected winning-card count. Required test: PORTFOLIO-02 shared-loss/browser test.
- S019-AC3: One losing shared leg has a visible blast radius; impossible caps do not create duplicate cards. Required test: PORTFOLIO-03 infeasible/no-filler test.

## E07 Prop feature and weather shadow experiments

Milestone: M4. Owner: NFL producer lane with evaluator. Depends on: E01, E03.

Features: E07-F1 R127 feature parity; E07-F2 Weather provenance.

### S020 Observe train-shaped features prospectively

Feature: E07-F1. Status: planned. Effort: 1–2 engineer-days plus observation. Risk: High. Prerequisite stories: S011, S016.

As a model owner, I can test train/serve skew without fitting to Week5 misses.

Tasks:

- S020-T1: Freeze incumbent and training-shaped mu on each supported pregame contract.
- S020-T2: Grade both on the same future observations and report per-position paired loss.
- S020-T3: Keep candidate weight zero/held until the full prospective adoption gate passes.

Acceptance and required evidence:

- S020-AC1: Shadow execution cannot alter live probabilities or selection. Required test: R127-01 no-serving-change test.
- S020-AC2: At least two new graded weeks plus clustered CI/margin/subgroup criteria are required, not automatic promotion after two weeks. Required test: R127-02 prospective gate.
- S020-AC3: Week5 and single-TNF outcomes do not choose new coefficients. Required test: R127-03 cutoff perturbation.

### S021 Represent rain and roof uncertainty explicitly

Feature: E07-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S011.

As an analyst, I can evaluate heavy-rain effects using what was forecast before kickoff.

Tasks:

- S021-T1: Persist forecast issue/receipt time, venue, roof state and full game window.
- S021-T2: Distinguish missing rainfall from zero and indoor/closed-roof from outdoor exposure.
- S021-T3: Predeclare shadow interactions and compare using grouped held-out outcomes.

Acceptance and required evidence:

- S021-AC1: Missing rain/roof values stay unknown; an observed postgame report cannot masquerade as a pregame forecast. Required test: WEATHER-01 unknown/roof/window matrix.
- S021-AC2: Forecasts issued/received after cutoff are excluded from prospective features. Required test: WEATHER-02 vintage rejection.
- S021-AC3: Weather experiments remain shadow with reported coverage and no undocumented probability adjustment. Required test: WEATHER-03 shadow parity.

## E08 Operator dashboard and application MCP

Milestone: M4. Owner: Evaluation and UX lane. Depends on: E00, E02.

Features: E08-F1 Status and forecast trace; E08-F2 Evaluation and candidate review; E08-F3 Scoped chat access.

### S022 See the actual connection and forecast lineage

Feature: E08-F1. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S003, S007.

As the owner, I can see the last durable ingest, source freshness and reason a prediction was withheld.

Tasks:

- S022-T1: Build mobile-first connection/status and searchable forecast trace screens.
- S022-T2: Expose generation, publication, event, outcome revision and model version states.
- S022-T3: Add empty, delayed, unauthorized and offline recovery states.

Acceptance and required evidence:

- S022-AC1: UI never calls management MCP connected status a working learning loop. Required test: UI-01 status/trace browser journey.
- S022-AC2: A trace links user-visible forecast to implementation/receipt and evidence without exposing secrets. Required test: UI-02 empty/error/mobile test.
- S022-AC3: Pending/unavailable and measured/estimated states remain distinguishable at mobile widths. Required test: UI-03 secret exposure scan.

### S023 Review candidates without accidental promotion

Feature: E08-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S014, S015.

As the owner, I can compare common-cohort evidence and understand a hold before approving a change.

Tasks:

- S023-T1: Show incumbent/candidate metrics, subgroup harm, coverage and manifest references.
- S023-T2: Separate shadow, eligible-for-review, approved, active and rolled-back states.
- S023-T3: Require a distinct authorized confirmation action and immutable audit for approval.

Acceptance and required evidence:

- S023-AC1: A better pooled score with a worse protected subgroup visibly remains held. Required test: UI-04 candidate hold journey.
- S023-AC2: Reading a report or pressing refresh cannot approve or activate a candidate. Required test: UI-05 read-vs-approve authorization.
- S023-AC3: Displayed active version matches the artifact consumed by NFL, not merely the latest proposal. Required test: UI-06 effective-version acceptance.

### S024 Ask chat for status and evidence safely

Feature: E08-F3. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S003, S022.

As an Astra/chat user, I can inspect predictions through narrow application tools rather than database-owner access.

Tasks:

- S024-T1: Expose read-only status, trace, report and candidate-compare MCP tools.
- S024-T2: Authenticate scoped sessions and test supported chat-client transport.
- S024-T3: Keep intake/approval writes separate and constrain untrusted tool content.

Acceptance and required evidence:

- S024-AC1: Application MCP cannot execute arbitrary SQL or read another namespace. Required test: MCP-01 scope/tool allowlist.
- S024-AC2: A disconnected/unsupported chat client has a dashboard fallback and no false integration claim. Required test: MCP-02 client/dashboard acceptance.
- S024-AC3: Prompt instructions inside receipts/uploads cannot authorize a write or promotion. Required test: MCP-03 prompt-injection test.

## E09 Screenshot intake and evidence based questions

Milestone: M6. Owner: Shared-engine lane with UI. Depends on: E08.

Features: E09-F1 Private upload and parsed draft; E09-F2 Evidence backed question workflow.

### S025 Confirm a screenshot before analyzing a ticket

Feature: E09-F1. Status: planned. Effort: 2–4 engineer-days; optional LLM cost approval. Risk: High. Prerequisite stories: S022, S024.

As the owner, I can upload a parlay screenshot, correct the parsed details and inspect its model comparison.

Tasks:

- S025-T1: Validate image size/type and store privately with authenticated short-lived access.
- S025-T2: Add optional OCR/vision parsing into a draft, not trusted event evidence.
- S025-T3: Require canonical player/game/line/direction/stake/quote confirmation before analysis.

Acceptance and required evidence:

- S025-AC1: An ambiguous name/line stays unresolved until confirmed; file text cannot override authorization. Required test: INTAKE-01 upload/security test.
- S025-AC2: Uploads never establish pre-event publication or FINAL outcomes. Required test: INTAKE-02 ambiguous parse confirmation.
- S025-AC3: Deleting/retaining uploads follows an explicit policy and access is tested with another identity. Required test: INTAKE-03 provenance/retention test.

### S026 Separate answers from objective training labels

Feature: E09-F2. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S024.

As a chat user, I can ask questions and understand which claims are supported without teaching the engine unverified facts.

Tasks:

- S026-T1: Represent question/evidence/answer provenance and optional owner review status.
- S026-T2: Route predictive questions to a domain contract only when horizon and outcome are definable.
- S026-T3: Keep subjective feedback separate from objective observations.

Acceptance and required evidence:

- S026-AC1: An answer with missing sources says so rather than creating a statistical confidence measurement. Required test: QA-01 provenance/horizon test.
- S026-AC2: A thumbs-up or uploaded claimed win cannot become an authoritative training outcome. Required test: QA-02 label authority test.
- S026-AC3: Non-predictive questions do not create fake resolved predictions. Required test: QA-03 non-prediction routing.

## E10 Operational reliability and release safety

Milestone: M0–M5. Owner: Coordinator and independent QA. Depends on: E00.

Features: E10-F1 Jobs and observability; E10-F2 Release evidence and rollback.

### S027 Operate resumable bounded learning jobs

Feature: E10-F1. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S008, S009, S011.

As an operator, I can recover a failed run and see stale ingestion before a game starts.

Tasks:

- S027-T1: Add ingestion-completion and manual evaluation workflow triggers with persistent leases/checkpoints.
- S027-T2: Emit namespace-scoped batch/run metrics and alerts without sensitive content.
- S027-T3: Test duplicate triggers, expired leases, late cron and cost/runtime ceilings.

Acceptance and required evidence:

- S027-AC1: A duplicate trigger produces one logical evaluation or a clear no-op, not duplicate training samples. Required test: OPS-01 retry/lease test.
- S027-AC2: Killed jobs resume from durable evidence; no essential state lives only in a Vercel filesystem. Required test: OPS-02 crash/resume test.
- S027-AC3: Backlog age, last successful ingest/evaluation and explicit failure reason are visible; targets are labeled proposed until measured. Required test: OPS-03 stale/backlog alert drill.

### S028 Release the exact tested generation

Feature: E10-F2. Status: planned. Effort: 1–2 engineer-days. Risk: High. Prerequisite stories: S001, S002, S003.

As the owner, I can authorize a release with known code, data, schema and rollback targets.

Tasks:

- S028-T1: Create an integration manifest with both SHAs, data hashes, schema and effective artifact.
- S028-T2: Run one NFL full gate and independent core/API/browser acceptance on exact revisions.
- S028-T3: Exercise test-environment migration/rollback and verify served hashes after owner-approved release.

Acceptance and required evidence:

- S028-AC1: No red/interrupted/unexecuted suite is reported as green and no moving main is substituted for the tested SHA. Required test: RELEASE-01 exact-SHA gate manifest.
- S028-AC2: Production migration/deploy/promotion waits for explicit owner authorization. Required test: RELEASE-02 deployment verification.
- S028-AC3: Rollback preserves immutable forecasts and revisions, with schema compatibility demonstrated. Required test: RELEASE-03 compatible rollback drill.

## E11 Reusable core and additional domain adapters

Milestone: M6. Owner: Shared-engine lane with domain evaluator. Depends on: E03, E04, E10.

Features: E11-F1 Portability contract; E11-F2 Sports and elections; E11-F3 Financial research.

### S029 Prove the core is not hardcoded to NFL

Feature: E11-F1. Status: planned. Effort: 0.5–1 engineer-day. Risk: Medium. Prerequisite stories: S010, S014.

As a platform owner, I can use a second domain without copying NFL eligibility or settlement logic into the core.

Tasks:

- S029-T1: Publish versioned adapter interfaces and a deterministic synthetic election fixture.
- S029-T2: Run identical idempotency/revision/cohort/candidate-boundary tests in both domains.
- S029-T3: Keep domain feature, market-policy and label definitions in adapters.

Acceptance and required evidence:

- S029-AC1: Generic persistence/scoring works for NFL and the synthetic domain without NFL imports. Required test: DOMAIN-01 second-domain contract.
- S029-AC2: Domain/canonical IDs cannot collide across adapters or owners. Required test: DOMAIN-02 namespace collision.
- S029-AC3: NFL market-price restrictions do not silently become a global financial-feature restriction. Required test: DOMAIN-03 adapter-policy isolation.

### S030 Pilot one additional real prediction task

Feature: E11-F2. Status: planned. Effort: 2–5 engineer-days per bounded adapter, excluding data procurement. Risk: Medium. Prerequisite stories: S029.

As the owner, I can add F1, soccer, NCAA or elections through a measured task-specific adapter.

Tasks:

- S030-T1: Select one domain after core acceptance and inventory licensed timestamped data/outcomes.
- S030-T2: Define its event horizon, canonical identities, baseline, label revisions and grouped evaluation.
- S030-T3: Run shadow collection/evaluation before enabling any serving change.

Acceptance and required evidence:

- S030-AC1: The adapter declares exactly which task/outcome it supports and rejects unsupported bets or questions. Required test: DOMAIN-04 real-source contract/rights audit.
- S030-AC2: Historical coverage/vintages and rights are documented; current data never fills missing past inputs. Required test: DOMAIN-05 frozen-vintage replay.
- S030-AC3: A held-out baseline comparison and domain-specific subgroup checks precede activation. Required test: DOMAIN-06 domain acceptance.

### S031 Design financial predictions as a separate research pilot

Feature: E11-F3. Status: planned. Effort: separate scope; materially larger than a sports adapter. Risk: High. Prerequisite stories: S029.

As the owner, I can investigate shorts and puts with clear data, horizon and risk boundaries.

Tasks:

- S031-T1: Specify price/return/option-event target, corporate action handling and timestamped quote chain requirements.
- S031-T2: Define transaction costs, slippage, expiries, survivorship/lookahead controls and risk metrics.
- S031-T3: Build paper-only evaluation and require separate execution authorization and compliance review if pursued.

Acceptance and required evidence:

- S031-AC1: A directional forecast is never presented as proven option profitability without appropriate quote/cost evidence. Required test: FIN-01 instrument/horizon contract.
- S031-AC2: No order, trade, bet or account mutation occurs from the learning pilot. Required test: FIN-02 cost/leakage backtest.
- S031-AC3: Paper performance includes held-out periods, drawdown/calibration and data limitations rather than guaranteed returns. Required test: FIN-03 execution isolation.
