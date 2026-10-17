// Editable backlog source. All acceptance tests are requirements, not claims of completion.
export const revision = 'R1';
export const evidenceTime = '2026-10-15 15:19 CDT (America/Chicago)';
function story(id, feature, title, userStory, tasks, acceptance, tests, options = {}) {
  return { id, feature, title, user_story: userStory,
    status: options.status || 'planned', effort: options.effort || '0.5–1 engineer-day',
    risk: options.risk || 'Medium', depends_on: options.depends_on || [],
    tasks: tasks.map((action, i) => ({ id: `${id}-T${i + 1}`, action })),
    acceptance: acceptance.map((criterion, i) => ({ id: `${id}-AC${i + 1}`, criterion,
      test: tests[i % tests.length] })), tests };
}
export const epics = [
  { id: 'E00', title: 'Reproducible hosted foundation and least privilege', milestone: 'M0', owner: 'Shared-engine lane', depends_on: [],
    features: { F1: 'Source and deployment identity', F2: 'Schema reconciliation and security', F3: 'Credential readiness' }, stories: [
      story('S001', 'F1', 'Reproduce the hosted service from source', 'As an operator, I can identify and rebuild the exact API so local success corresponds to a deployable revision.',
        ['Inventory and isolate unpublished hosted files without overwriting user changes.', 'Integrate hosted foundation with the receipt adapter and pin dependencies.', 'Expose backend SHA, package/contract versions and capability flags.'],
        ['A clean checkout installs and passes hosted/core tests using the pinned toolchain.', 'Authenticated status identifies the running revision; unwired NFL ingestion and promotion remain false.'],
        ['FOUNDATION-01 clean-checkout test', 'FOUNDATION-02 deployed identity acceptance']),
      story('S002', 'F2', 'Reconcile the live schema without recreating tables', 'As the owner, I can approve one reviewed migration with existing data and required trigger behavior preserved.',
        ['Compare live hosted_foundation migration/schema with the local different migration ID.', 'Review rls_auto_enable function grants, ownership and event-trigger use; create a new scoped migration through CLI.', 'Test forward migration, least-privilege access and compatible rollback on a test database.'],
        ['Existing tables/records survive migration and the original CREATE TABLE setup is not blindly reapplied.', 'Public roles cannot execute unintended privileged helpers; required automatic RLS behavior still works.', 'Service-only tables remain unavailable to unauthorized browser roles, with explicit grant tests.'],
        ['DB-01 migration fixture', 'SEC-01 function privilege matrix', 'SEC-02 table access matrix'], { risk: 'High', effort: '1–2 engineer-days' }),
      story('S003', 'F3', 'Verify scoped credentials without disclosing them', 'As the owner, I can see whether the backend is ready without copying production secrets into chat.',
        ['Retain the already-configured Production operator token; verify required variable names privately.', 'Validate backend URL/key and implement separate NFL-ingest/evaluator capability checks.', 'Document environment isolation and authorized rotation/revocation procedure.'],
        ['API rejects missing, wrong, revoked or wrong-scope credentials without logging secret values.', 'Preview cannot read or write Production data; the NFL PWA has no backend secret.', 'A token present only in Vercel is not reported as a configured GitHub pipeline credential.'],
        ['SEC-03 credential/scope and log-redaction tests', 'SEC-04 preview isolation and browser-secret scan', 'SEC-05 pipeline-credential readiness'])
    ] },
  { id: 'E01', title: 'Prospective forecasts and publication lineage', milestone: 'M1', owner: 'NFL producer lane', depends_on: ['E00'],
    features: { F1: 'Generation journal', F2: 'Public observation', F3: 'Exact generation semantic gate' }, stories: [
      story('S004', 'F1', 'Capture learning evidence before display dedupe', 'As an evaluator, I retain each valid new forecast even when its displayed card already exists.',
        ['Capture all supported pool receipt vintages before as_of and card-ID early returns.', 'Store immutable generation envelopes and content-addressed source snapshots.', 'Keep candidate-pool and actually offered populations distinguishable.'],
        ['Reoffering an existing display card captures a new valid receipt without changing historical cards.', 'Exact replay creates no extra receipt; late, malformed and unsupported rows have explicit rejection reasons.', 'Concurrent distinct generation publishes survive; conflicting content under one ID fails loudly.'],
        ['JOURNAL-01 duplicate-display/new-receipt regression', 'JOURNAL-02 rejection/idempotency matrix', 'JOURNAL-03 publisher race test'], { status: 'implemented locally; focused tests passed; review pending' }),
      story('S005', 'F2', 'Separate generated receipts from public availability', 'As a user, I know whether a prediction was actually available before its event, rather than merely generated internally.',
        ['Define immutable generation and publication observation contracts.', 'Verify deployed content hashes and deployment identity after publication.', 'Record actual observation time and migration from display identifiers without historical backfill.'],
        ['Internal capture alone never produces verified-publication status.', 'A mismatched deployment/content hash cannot attest the generation; a late observation cannot prove earlier availability.', 'A historical missing base/publication timestamp remains missing rather than reconstructed.'],
        ['PUB-01 generated-vs-public test', 'PUB-02 hash/time mismatch test', 'PUB-03 legacy non-backfill test']),
      story('S006', 'F3', 'Block incoherent generations before publication', 'As the release owner, I can prevent a mixed-source or ineligible slate from being published.',
        ['Bind the gate to the exact source hashes, code/model/policy versions and output manifest.', 'Validate source freshness, eligibility joins, game/week identity and exactly-once calibration.', 'Wire the prepublication gate while preserving R102 postpublication and R106 archive protection.'],
        ['A stale injury source, invalid identity, changed input hash or double-calibrated receipt rejects the generation.', 'A valid generation with a row-local unknown status keeps unrelated eligible players.', 'Gate evidence names the precise generation and does not certify moving main.'],
        ['GEN-01 semantic fault matrix', 'GEN-02 row quarantine acceptance', 'GEN-03 immutable-generation binding'], { risk: 'High', effort: '1–2 engineer-days' })
    ] },
  { id: 'E02', title: 'Durable ingestion and outcome corrections', milestone: 'M2', owner: 'Shared-engine lane', depends_on: ['E00', 'E01'],
    features: { F1: 'Pending prediction intake', F2: 'Retry-safe persistence', F3: 'Outcome revisions' }, stories: [
      story('S007', 'F1', 'Ingest pending predictions before results', 'As an evaluator, my training population is not selected according to which bets later have known outcomes.',
        ['Extend the adapter to accept unresolved cards, pools and immutable generation envelopes.', 'Validate real source clocks, kickoff evidence and independent NFL producer parity.', 'Persist receipts independently of FINAL observations.'],
        ['A pending eligible forecast is accepted with no fabricated outcome.', 'Source/serving/hash mismatch, late internal observation and unknown source times are explicitly quarantined.', 'Actual learning_receipts and journal producer outputs pass cross-repository integration tests.'],
        ['ADAPTER-01 pending intake', 'ADAPTER-02 provenance rejection matrix', 'ADAPTER-03 real-producer parity'], { status: 'implemented locally; focused producer-parity tests passed; review pending' }),
      story('S008', 'F2', 'Acknowledge only durable immutable records', 'As an operator, I can retry failed uploads without losing or overwriting forecasts.',
        ['Implement versioned append-only store contracts and additive Supabase persistence.', 'Build per-record acknowledgements and a durable NFL outbox/exporter.', 'Test crashes, partial invalid batches, concurrent writers and backlog alerts.'],
        ['Same ID/same payload is a no-op; same ID/different payload is a conflict, not an upsert.', 'Acknowledgement follows committed persistence; a crash before commit leaves an unacknowledged retry.', 'An engine outage preserves the outbox and serving does not wait synchronously for the engine.'],
        ['INGEST-01 duplicate/conflict tests', 'INGEST-02 crash/ack test', 'INGEST-03 concurrent writer/outage test'], { status: 'local SQLite contract implementation in progress; hosted Supabase/export path planned', risk: 'High', effort: '1.5–2.5 engineer-days' }),
      story('S009', 'F3', 'Append authoritative outcome revisions', 'As an analyst, I can explain corrections without changing what the original forecast or earlier evaluation said.',
        ['Validate FINAL source evidence and exact receipt/contract/event joins.', 'Append immutable revisions with immediate predecessor, reason and server receipt time.', 'Pin revision IDs in evaluations and expose correction history.'],
        ['Pending, in-progress, void and missing observations never become a false binary loss.', 'Conflicting revision IDs, missing predecessors and forks fail explicitly.', 'A correction changes the latest resolution view while preserving original records and prior evaluation manifests.'],
        ['OUTCOME-01 status/join tests', 'OUTCOME-02 revision chain/concurrency tests', 'OUTCOME-03 pinned-history test'], { risk: 'High' })
    ] },
  { id: 'E03', title: 'Chronological evaluation and trustworthy records', milestone: 'M3', owner: 'Evaluation and UX lane', depends_on: ['E02'],
    features: { F1: 'Common cohort and scores', F2: 'Temporal and grouped evaluation', F3: 'Honest NFL record' }, stories: [
      story('S010', 'F1', 'Match NFL and engine membership and scores', 'As the owner, I can compare evaluations without hidden denominator or probability changes.',
        ['Define canonical first-eligible contract selection and explicit exclusion reasons.', 'Compare exact receipt/outcome revision sets across producer and engine.', 'Verify served probability, log loss and Brier against independent numerical oracles.'],
        ['Both systems select identical IDs on the same supported cohort and match declared numeric tolerances.', 'Later vintages, duplicate cards and overlapping snapshots do not multiply independent samples.', 'Missing/legacy bases and unresolved outcomes reduce reported coverage, not silently the denominator.'],
        ['EVAL-01 membership parity', 'EVAL-02 scalar oracle/tolerance', 'EVAL-03 duplicate/exclusion accounting']),
      story('S011', 'F2', 'Freeze evidence before fitting or evaluating', 'As an analyst, I can reproduce a candidate result without post-event information leaking into it.',
        ['Freeze training/evaluation cutoffs, received-time constraints, dataset hashes and revisions.', 'Implement chronological held-out folds and game-clustered paired intervals.', 'Report per-position/market/week effects and honest insufficient-evidence states.'],
        ['Changing held-out outcomes cannot change earlier fitted coefficients or cohort membership.', 'No game/correlation group crosses train and evaluation boundaries unnoticed.', 'The report gives event/group counts; an independent-card interval is never attached to overlapping cards.'],
        ['EVAL-04 lookahead perturbation', 'EVAL-05 group leakage test', 'EVAL-06 clustered interval oracle'], { risk: 'High', effort: '1–2 engineer-days' }),
      story('S012', 'F3', 'Show pre-kickoff accuracy separately from settlement', 'As an NFL user, I can distinguish genuine forecasts from retrospective or reduced-leg results.',
        ['Recover last pre-kickoff offered cards from sufficient Git history and canonical grades.', 'Expose as-offered and settlement views with push/void and late-exclusion accounting.', 'Update mobile record presentation with coverage and provenance warnings.'],
        ['With complete reference history, Weeks 1–5 as-offered headline is 17/66,13/66,21/66,22/65,14/63.', 'Week 5 settlement remains separately labeled: 16/65 incl. push; Week cards 1 full +3 reduced +19 losses on23.', 'Shallow or insufficient evidence displays unavailable/partial, never reference numbers hardcoded as measured results.'],
        ['R130-01 full-history reference replay', 'R130-02 settlement oracle', 'R130-03 unavailable-history/browser test'], { status: 'local implementation in progress; full-history reference verification unavailable', effort: '1–2 engineer-days' })
    ] },
  { id: 'E04', title: 'Compatible candidates and controlled adoption', milestone: 'M3', owner: 'Evaluation and UX lane with NFL producer lane', depends_on: ['E03'],
    features: { F1: 'Typed NFL calibration', F2: 'Independent NFL gate', F3: 'Activation and rollback' }, stories: [
      story('S013', 'F1', 'Train the correct transform from immutable bases', 'As a model owner, I can propose a correction without changing coefficient meanings or calibrating twice.',
        ['Define the NFL base-logit artifact with input stage, coefficients, clamp/round and base version.', 'Fit only earlier eligible immutable bases, leaving generic confidence-Platt behavior unchanged.', 'Evaluate exactly the rounded transform the NFL producer will serve.'],
        ['Generic sigmoid(a*confidence+b) artifacts are rejected at the NFL boundary.', 'Applied probabilities match learning_receipts.serve_probability across edge and ordinary inputs.', 'A served probability cannot substitute for the base input and a mismatched base model cannot activate.'],
        ['CAL-01 generic artifact rejection', 'CAL-02 actual-producer transform parity', 'CAL-03 once-only/base-version tests'], { risk: 'High', effort: '1–2 engineer-days' }),
      story('S014', 'F2', 'Require comparative evidence before approval', 'As the owner, I can see why a candidate is held even when its aggregate score looks better.',
        ['Evaluate against the actual as-served incumbent on the identical held-out cohort.', 'Preserve existing task gates; propose a separately versioned 0.0015 additional margin for the shared return gate.', 'Return a shadow artifact with manifest and rejection reasons, not an executable serving instruction.'],
        ['Worse incumbent comparison, subgroup harm or insufficient evidence produces a hold; current MY requires100legs and2held-out week folds, normally3receipt weeks.', 'R127 promotion requires at least two newly graded weeks, clustered upper CI below0 and no position worse.', 'No evaluation or receipt upload automatically promotes a model; a changed margin policy needs explicit review.'],
        ['PROMOTE-01 margin/incumbent tests', 'PROMOTE-02 subgroup/CI/sufficiency tests', 'PROMOTE-03 shadow-only test'], { risk: 'High' }),
      story('S015', 'F3', 'Adopt only an approved pinned artifact', 'As an operator, I can trace and reverse a future model change without rewriting historical forecasts.',
        ['Implement explicit owner approval/adoption events and exact artifact hash checks.', 'Materialize the approved compatible artifact for a new NFL generation.', 'Exercise two feedback cycles, outage hold and compatible rollback.'],
        ['Unapproved, stale, incompatible or tampered artifacts leave serving unchanged.', 'A newly approved artifact is applied once and its effective version is visible in the next receipt/UI.', 'Rollback restores future serving while old receipts, revisions and evaluation manifests remain byte-identical.'],
        ['RETURN-01 rejection matrix', 'RETURN-02 two-cycle end-to-end', 'RETURN-03 outage/rollback test'], { risk: 'High', effort: '1–2 engineer-days' })
    ] },
  { id: 'E05', title: 'NFL eligibility and fresh source authority', milestone: 'M4', owner: 'NFL producer lane', depends_on: ['E01'],
    features: { F1: 'Availability semantics', F2: 'Truncation and missed-game evidence' }, stories: [
      story('S016', 'F1', 'Preserve row-local quarantine and RETIRED semantics', 'As an NFL user, I do not receive an ineligible prop or lose unrelated candidates because of one unknown feed status.',
        ['Revalidate current Week6 eligible/withheld players under the merged vocabulary.', 'Test every RETIRED consumer and duplicate conflicting source rows.', 'Enforce and expose the 12-hour freshness/authority reason at generation.'],
        ['Fresh ok input withholds no player without a documented eligibility reason.', 'An identifiable unknown status quarantines that row, not800 unrelated rows.', 'RETIRED never means healthy and uncertain source authority never becomes fabricated medical zero.'],
        ['ELIG-01 current-input diff', 'ELIG-02 status/quarantine matrix', 'ELIG-03 freshness/duplicate test'], { status: 'core fixes already merged; current baseline regression required' }),
      story('S017', 'F2', 'Detect incomplete injury reports honestly', 'As a model owner, I do not treat a player dropped from a capped report as newly healthy.',
        ['Inventory ESPN pagination/25-row cap and source completeness evidence.', 'Shadow a last-FINAL did-not-play guard with newer clearance precedence.', 'Replay eligibility changes before authorizing production behavior.'],
        ['A capped feed is not labeled complete merely because it has800 rows.', 'A dropped IR/missed-last-game fixture remains ineligible absent documented newer clearance.', 'False-positive withholding and unknown coverage are reported before activation.'],
        ['ELIG-04 capped-feed fixture', 'ELIG-05 clearance precedence', 'ELIG-06 frozen eligibility replay'], { risk: 'High', effort: '1–2 engineer-days' })
    ] },
  { id: 'E06', title: 'Slate selection and concentrated parlay risk', milestone: 'M4', owner: 'Evaluation and UX lane', depends_on: ['E01', 'E03'],
    features: { F1: 'Frozen reselection control', F2: 'Explicit portfolio objective' }, stories: [
      story('S018', 'F1', 'Replay selection rather than reprice shipped legs', 'As an analyst, I can distinguish a genuine selection comparison from scoring the same bets twice.',
        ['Freeze recovered candidates, source scope and control/selection versions.', 'Run R126 vs R74-consistent control on post-R74 weeks; inventory missing full-pool evidence.', 'Add slate-reselection regression gate and per-week coverage report.'],
        ['Reference recovered on-card replay reproduces59/188→72/189 without claiming a complete original universe.', 'A candidate losing winners in any required control week fails its declared regression gate.', 'Unavailable inputs are not replaced with current injuries, ratings or observed weather.'],
        ['REPLAY-01 frozen control reproduction', 'REPLAY-02 never-regress fixture', 'REPLAY-03 provenance coverage audit'], { effort: '1–2 engineer-days' }),
      story('S019', 'F2', 'Make diversification an explicit choice', 'As the owner, I can weigh highest individual hit probability against reducing the damage of one upset.',
        ['Compare highest-hit and diversified policies on identical Game/Week/MY candidates.', 'Show shared leg/game exposure, expected winners and model-implied probability of zero winners.', 'Record owner objective; enforce approved caps or report infeasible without duplicate filler.'],
        ['Default highest-hit behavior remains unchanged unless the owner approves a different objective.', 'The comparison shows reduced exposure and any lower expected winning-card count.', 'One losing shared leg has a visible blast radius; impossible caps do not create duplicate cards.'],
        ['PORTFOLIO-01 common-candidate comparison', 'PORTFOLIO-02 shared-loss/browser test', 'PORTFOLIO-03 infeasible/no-filler test'])
    ] },
  { id: 'E07', title: 'Prop feature and weather shadow experiments', milestone: 'M4', owner: 'NFL producer lane with evaluator', depends_on: ['E01', 'E03'],
    features: { F1: 'R127 feature parity', F2: 'Weather provenance' }, stories: [
      story('S020', 'F1', 'Observe train-shaped features prospectively', 'As a model owner, I can test train/serve skew without fitting to Week5 misses.',
        ['Freeze incumbent and training-shaped mu on each supported pregame contract.', 'Grade both on the same future observations and report per-position paired loss.', 'Keep candidate weight zero/held until the full prospective adoption gate passes.'],
        ['Shadow execution cannot alter live probabilities or selection.', 'At least two new graded weeks plus clustered CI/margin/subgroup criteria are required, not automatic promotion after two weeks.', 'Week5 and single-TNF outcomes do not choose new coefficients.'],
        ['R127-01 no-serving-change test', 'R127-02 prospective gate', 'R127-03 cutoff perturbation'], { risk: 'High', effort: '1–2 engineer-days plus observation' }),
      story('S021', 'F2', 'Represent rain and roof uncertainty explicitly', 'As an analyst, I can evaluate heavy-rain effects using what was forecast before kickoff.',
        ['Persist forecast issue/receipt time, venue, roof state and full game window.', 'Distinguish missing rainfall from zero and indoor/closed-roof from outdoor exposure.', 'Predeclare shadow interactions and compare using grouped held-out outcomes.'],
        ['Missing rain/roof values stay unknown; an observed postgame report cannot masquerade as a pregame forecast.', 'Forecasts issued/received after cutoff are excluded from prospective features.', 'Weather experiments remain shadow with reported coverage and no undocumented probability adjustment.'],
        ['WEATHER-01 unknown/roof/window matrix', 'WEATHER-02 vintage rejection', 'WEATHER-03 shadow parity'])
    ] },
  { id: 'E08', title: 'Operator dashboard and application MCP', milestone: 'M4', owner: 'Evaluation and UX lane', depends_on: ['E00', 'E02'],
    features: { F1: 'Status and forecast trace', F2: 'Evaluation and candidate review', F3: 'Scoped chat access' }, stories: [
      story('S022', 'F1', 'See the actual connection and forecast lineage', 'As the owner, I can see the last durable ingest, source freshness and reason a prediction was withheld.',
        ['Build mobile-first connection/status and searchable forecast trace screens.', 'Expose generation, publication, event, outcome revision and model version states.', 'Add empty, delayed, unauthorized and offline recovery states.'],
        ['UI never calls management MCP connected status a working learning loop.', 'A trace links user-visible forecast to implementation/receipt and evidence without exposing secrets.', 'Pending/unavailable and measured/estimated states remain distinguishable at mobile widths.'],
        ['UI-01 status/trace browser journey', 'UI-02 empty/error/mobile test', 'UI-03 secret exposure scan']),
      story('S023', 'F2', 'Review candidates without accidental promotion', 'As the owner, I can compare common-cohort evidence and understand a hold before approving a change.',
        ['Show incumbent/candidate metrics, subgroup harm, coverage and manifest references.', 'Separate shadow, eligible-for-review, approved, active and rolled-back states.', 'Require a distinct authorized confirmation action and immutable audit for approval.'],
        ['A better pooled score with a worse protected subgroup visibly remains held.', 'Reading a report or pressing refresh cannot approve or activate a candidate.', 'Displayed active version matches the artifact consumed by NFL, not merely the latest proposal.'],
        ['UI-04 candidate hold journey', 'UI-05 read-vs-approve authorization', 'UI-06 effective-version acceptance']),
      story('S024', 'F3', 'Ask chat for status and evidence safely', 'As an Astra/chat user, I can inspect predictions through narrow application tools rather than database-owner access.',
        ['Expose read-only status, trace, report and candidate-compare MCP tools.', 'Authenticate scoped sessions and test supported chat-client transport.', 'Keep intake/approval writes separate and constrain untrusted tool content.'],
        ['Application MCP cannot execute arbitrary SQL or read another namespace.', 'A disconnected/unsupported chat client has a dashboard fallback and no false integration claim.', 'Prompt instructions inside receipts/uploads cannot authorize a write or promotion.'],
        ['MCP-01 scope/tool allowlist', 'MCP-02 client/dashboard acceptance', 'MCP-03 prompt-injection test'])
    ] },
  { id: 'E09', title: 'Screenshot intake and evidence based questions', milestone: 'M6', owner: 'Shared-engine lane with UI', depends_on: ['E08'],
    features: { F1: 'Private upload and parsed draft', F2: 'Evidence backed question workflow' }, stories: [
      story('S025', 'F1', 'Confirm a screenshot before analyzing a ticket', 'As the owner, I can upload a parlay screenshot, correct the parsed details and inspect its model comparison.',
        ['Validate image size/type and store privately with authenticated short-lived access.', 'Add optional OCR/vision parsing into a draft, not trusted event evidence.', 'Require canonical player/game/line/direction/stake/quote confirmation before analysis.'],
        ['An ambiguous name/line stays unresolved until confirmed; file text cannot override authorization.', 'Uploads never establish pre-event publication or FINAL outcomes.', 'Deleting/retaining uploads follows an explicit policy and access is tested with another identity.'],
        ['INTAKE-01 upload/security test', 'INTAKE-02 ambiguous parse confirmation', 'INTAKE-03 provenance/retention test'], { risk: 'High', effort: '2–4 engineer-days; optional LLM cost approval' }),
      story('S026', 'F2', 'Separate answers from objective training labels', 'As a chat user, I can ask questions and understand which claims are supported without teaching the engine unverified facts.',
        ['Represent question/evidence/answer provenance and optional owner review status.', 'Route predictive questions to a domain contract only when horizon and outcome are definable.', 'Keep subjective feedback separate from objective observations.'],
        ['An answer with missing sources says so rather than creating a statistical confidence measurement.', 'A thumbs-up or uploaded claimed win cannot become an authoritative training outcome.', 'Non-predictive questions do not create fake resolved predictions.'],
        ['QA-01 provenance/horizon test', 'QA-02 label authority test', 'QA-03 non-prediction routing'])
    ] },
  { id: 'E10', title: 'Operational reliability and release safety', milestone: 'M0–M5', owner: 'Coordinator and independent QA', depends_on: ['E00'],
    features: { F1: 'Jobs and observability', F2: 'Release evidence and rollback' }, stories: [
      story('S027', 'F1', 'Operate resumable bounded learning jobs', 'As an operator, I can recover a failed run and see stale ingestion before a game starts.',
        ['Add ingestion-completion and manual evaluation workflow triggers with persistent leases/checkpoints.', 'Emit namespace-scoped batch/run metrics and alerts without sensitive content.', 'Test duplicate triggers, expired leases, late cron and cost/runtime ceilings.'],
        ['A duplicate trigger produces one logical evaluation or a clear no-op, not duplicate training samples.', 'Killed jobs resume from durable evidence; no essential state lives only in a Vercel filesystem.', 'Backlog age, last successful ingest/evaluation and explicit failure reason are visible; targets are labeled proposed until measured.'],
        ['OPS-01 retry/lease test', 'OPS-02 crash/resume test', 'OPS-03 stale/backlog alert drill']),
      story('S028', 'F2', 'Release the exact tested generation', 'As the owner, I can authorize a release with known code, data, schema and rollback targets.',
        ['Create an integration manifest with both SHAs, data hashes, schema and effective artifact.', 'Run one NFL full gate and independent core/API/browser acceptance on exact revisions.', 'Exercise test-environment migration/rollback and verify served hashes after owner-approved release.'],
        ['No red/interrupted/unexecuted suite is reported as green and no moving main is substituted for the tested SHA.', 'Production migration/deploy/promotion waits for explicit owner authorization.', 'Rollback preserves immutable forecasts and revisions, with schema compatibility demonstrated.'],
        ['RELEASE-01 exact-SHA gate manifest', 'RELEASE-02 deployment verification', 'RELEASE-03 compatible rollback drill'], { risk: 'High', effort: '1–2 engineer-days' })
    ] },
  { id: 'E11', title: 'Reusable core and additional domain adapters', milestone: 'M6', owner: 'Shared-engine lane with domain evaluator', depends_on: ['E03', 'E04', 'E10'],
    features: { F1: 'Portability contract', F2: 'Sports and elections', F3: 'Financial research' }, stories: [
      story('S029', 'F1', 'Prove the core is not hardcoded to NFL', 'As a platform owner, I can use a second domain without copying NFL eligibility or settlement logic into the core.',
        ['Publish versioned adapter interfaces and a deterministic synthetic election fixture.', 'Run identical idempotency/revision/cohort/candidate-boundary tests in both domains.', 'Keep domain feature, market-policy and label definitions in adapters.'],
        ['Generic persistence/scoring works for NFL and the synthetic domain without NFL imports.', 'Domain/canonical IDs cannot collide across adapters or owners.', 'NFL market-price restrictions do not silently become a global financial-feature restriction.'],
        ['DOMAIN-01 second-domain contract', 'DOMAIN-02 namespace collision', 'DOMAIN-03 adapter-policy isolation']),
      story('S030', 'F2', 'Pilot one additional real prediction task', 'As the owner, I can add F1, soccer, NCAA or elections through a measured task-specific adapter.',
        ['Select one domain after core acceptance and inventory licensed timestamped data/outcomes.', 'Define its event horizon, canonical identities, baseline, label revisions and grouped evaluation.', 'Run shadow collection/evaluation before enabling any serving change.'],
        ['The adapter declares exactly which task/outcome it supports and rejects unsupported bets or questions.', 'Historical coverage/vintages and rights are documented; current data never fills missing past inputs.', 'A held-out baseline comparison and domain-specific subgroup checks precede activation.'],
        ['DOMAIN-04 real-source contract/rights audit', 'DOMAIN-05 frozen-vintage replay', 'DOMAIN-06 domain acceptance'], { effort: '2–5 engineer-days per bounded adapter, excluding data procurement' }),
      story('S031', 'F3', 'Design financial predictions as a separate research pilot', 'As the owner, I can investigate shorts and puts with clear data, horizon and risk boundaries.',
        ['Specify price/return/option-event target, corporate action handling and timestamped quote chain requirements.', 'Define transaction costs, slippage, expiries, survivorship/lookahead controls and risk metrics.', 'Build paper-only evaluation and require separate execution authorization and compliance review if pursued.'],
        ['A directional forecast is never presented as proven option profitability without appropriate quote/cost evidence.', 'No order, trade, bet or account mutation occurs from the learning pilot.', 'Paper performance includes held-out periods, drawdown/calibration and data limitations rather than guaranteed returns.'],
        ['FIN-01 instrument/horizon contract', 'FIN-02 cost/leakage backtest', 'FIN-03 execution isolation'], { risk: 'High', effort: 'separate scope; materially larger than a sports adapter' })
    ] }
];

// Story-level prerequisites take precedence over the aggregate epic integration
// dependencies. In particular, R130 is an independent NFL correctness release.
const prerequisites = {
  S001: [], S002: ['S001'], S003: ['S001'], S004: [],
  S005: ['S004'], S006: ['S004'], S007: ['S004'],
  S008: ['S002','S003','S007'], S009: ['S008'],
  S010: ['S007','S009'], S011: ['S010'], S012: [],
  S013: ['S010','S011'], S014: ['S013'],
  S015: ['S014','S028'], S016: [], S017: ['S016'],
  S018: ['S011'], S019: ['S018'], S020: ['S011','S016'],
  S021: ['S011'], S022: ['S003','S007'],
  S023: ['S014','S015'], S024: ['S003','S022'],
  S025: ['S022','S024'], S026: ['S024'],
  S027: ['S008','S009','S011'], S028: ['S001','S002','S003'],
  S029: ['S010','S014'], S030: ['S029'], S031: ['S029']
};
for (const e of epics) for (const s of e.stories) s.depends_on=prerequisites[s.id];
