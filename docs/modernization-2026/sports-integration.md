# Shared sports learning integration blueprint

Prepared 8 September 2026 against SelfLearning `49ece24573ca0d9870fcdbc8cd3c774d082593e9`. This is proposed implementation design. No sports adapter, migration, agent promotion or live financial execution is enabled by this document.

## Responsibility

NFL2026 remains the forecasting and Sleeper experience application. SelfLearning receives immutable forecasts, resolves versioned outcomes, computes comparable scores and prepares evidence-backed proposals. The first integration is read-only import plus score parity. It does not move NFL feature generation or ingest market prices into the prediction model.

The canonical companion package is `liddar12/NFL2026/docs/modernization-2026/`: solution architecture, technical architecture, experience design, release backlog and proposed JSON schemas. Keep a contract version manifest here when the adapter ships; do not maintain divergent copies by hand.

## Repair prerequisites

1. S01 / F7: partition sports scores by immutable model version, scoring policy, task, horizon and cohort. Compare candidate and incumbent on identical events and outcome revisions. A perfect model and a poor model must not collapse into one unnamed score.
2. S02 / F8: enforce foreign keys on every SQLite connection; immutable prediction writes and append-only outcome corrections. Repeated identical imports are idempotent; conflicting payloads fail. Existing evaluation artifacts keep their original revision sets.
3. S03 / F9: restore the missing power-backtest import/package boundary and add that suite to clean-checkout CI. Financial execution stays separate; this repair restores regression visibility, not execution authorization.

## Proposed package seams

| Seam | Responsibility |
| --- | --- |
| `selflearn_core/adapters/nfl.py` | Read a versioned immutable manifest, validate source lineage and normalize supported NFL tasks |
| `selflearn_core/scoring/sports.py` | Points metrics, class probabilities, interval coverage and quote-specific paper settlement metrics |
| Existing store interface | Insert-only forecasts, outcome revision chain, import receipts and evaluation input snapshots |
| Existing registry/updater | Versioned eligibility checks, immutable proposals, expected-incumbent approval and rollback audit |
| Future HTTP read layer | Authenticated paginated records and explicitly sanitized public aggregate views |
| Future adapter for WC2026 | Independent task and settlement translation using the same envelope after that repository is audited |

These are proposed files/boundaries and must be reconciled with the actual package organization during implementation. Retain the current public interface until compatibility tests pass. SQLite remains the local replay store; Supabase Postgres is recommended for hosted shared records after a separate development migration and explicit production approval.

## Import contract and temporal evidence

Accept only recognized contract versions. Each forecast includes project/task/event/entity, issued time, lock deadline, source as-of and hash, model/code/scoring versions and a typed prediction. Preserve existing source IDs through a mapping table. Do not guess player identities from names. Keep fixtures, reconstructed history and verified pre-event evidence distinguishable.

For verified pre-event evidence require `source_max_as_of <= issued_at < lock_deadline`. A delayed import can be valid only if the original pre-event artifact is independently auditable. The server's ingest time is not the forecast's issue time. Reject unsupported outcome shapes rather than coercing them to a scalar. A points envelope maps explicitly to the current scalar scoring path; categorical tasks retain their full outcome dictionary.

An import receipt records source commit/hash, input contract, importer version and eligible/duplicate/late/reconstructed/unresolved/rejected counts. Persist the cursor only after the batch transaction commits. A crash/retry must not duplicate forecasts or lose earlier accepted rows. Import report failures cannot be reported as a successful empty corpus.

## Outcome and evaluation policy

Only authoritative final/void results settle records. Corrections append revisions with source identifiers, timestamps and reasons. An evaluation records exact prediction IDs, outcome revisions, policy/config hashes and event/cohort membership. Re-running after a stat correction creates a new evaluation and can invalidate a pending proposal, while retaining prior history.

Use points MAE/bias/rank/lineup decision metrics and interval coverage, probability Brier/log-loss/calibration, and separate quote-specific net paper returns. Compare paired events under the same model task, scoring version and horizon. Report independent week/game clusters and honest uncertainty. A minimum sample floor does not establish adequate independent evidence. Use temporal holdouts, account for repeated hypothesis search and preserve the current never-regress policy.

## Agent and promotion boundary

Agents can read health, prepare an allowlisted hypothesis, submit a deterministic evaluation job and explain its artifacts. They cannot edit metrics, lower the gate, promote directly, read secrets or place bets/trades. Configure bounded candidate count, time/token/cost budgets and schema-validated tool inputs outside the agent's control. Provider failure leaves the app and deterministic scoring available.

Approval is a server-authorized transaction checking the expected incumbent plus policy, evidence and artifact hashes. Record actor, decision, previous/new version and reason. Concurrent stale proposals conflict. Rollback points to an already validated prior artifact and appends an audit decision; it does not erase history. Owner overrides remain explicitly distinct from evidence-driven promotion.

## Delivery acceptance

Use backlog S01–S05, L02, A01–A03 and X01 from the NFL companion roadmap. First ship local NFL replay and score parity. Next add restricted hosted storage/read APIs, then outcome resolution and proposal controls. A second sports adapter starts only after NFL parity; soccer draw, advancement and extra-time settlement need separate task definitions. Brokerage, financial risk and tax systems are outside this sports plan.

Required CI includes core, shared package, sports adapter, store revision/isolation and power-backtest collection. Frozen fixtures cover model separation, orphan rejection, outcome correction, repeated and conflicting imports, late evidence, missing identities, tie/push/void results, cross-project isolation and approval concurrency. Record code/runtime versions and artifacts. This package's schema/example checks do not substitute for that future integration suite.
