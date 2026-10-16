# liddar-ai

Monorepo for three workstreams that share one self-learning spine.

NFL integration checkpoint, October 16, 2026, 12:50 p.m. America/Chicago (CDT):
[50/99 whole local-build tasks (50.5%)](docs/architecture/2026-10-15/progress.json).
The reopened current-input eligibility task is reaccepted locally after the
ID-first correction and 29 passing frozen/current/adversarial checks. The
local NFL gate passes 2,453 feature tests plus data/smoke/model checks, exit 0;
browser step 8 was skipped, so release acceptance remains unverified.
New [durable evaluation jobs](docs/architecture/2026-10-15/LEARNING_JOBS.md)
persist/resume actual NFL shadow reports with an opt-in bounded isolated fitter:
1,353 core/API tests, 93 Node SQL and 101 Python-to-SQL tests pass; seven native-only
tests skip. All 46 critical Python files exceed 90% executable-line coverage.
Complete journal assembly, triggers, evaluator authority, alerts and hosted
rollout remain incomplete, so this does not add whole-task or live-loop credit.
See the [overnight handoff](docs/architecture/2026-10-15/OVERNIGHT_2026-10-16.md)
and [verification boundaries](docs/architecture/2026-10-15/VERIFICATION.md).
This work is local and unreleased; task completion is not production readiness,
an operational self-learning loop or a prediction-accuracy claim.

| Workstream | Package / app | What it is | Status |
|---|---|---|---|
| **B (spine)** | `packages/selflearn-core` | Log predictions, resolve outcomes, score, feed back. The honesty layer every other flow depends on. | Gate 1 scaffolded |
| **A (research)** | `apps/power2026-backtest` | Backtests of the Power 2026 electricity-market claims (H1..H4). | Stub |
| **C (execution)** | `apps/liddar-execution` | Web-based trading app that connects to Schwab and (eventually) executes proven signals with tax-loss harvesting. | Stub |

The React **Liddar Terminal** (deployed at liddar-terminal.vercel.app) is the web front end. It reads scored results over a small JSON/HTTP boundary and does not import Python.

## Sports adapters

The spine's second family of adapters is sports prediction markets — `liddar12/NFL2026` (live) and
`liddar12/wc2026-tracker` (prior work). See `docs/sports-roadmap.md`; the task shapes every sports adapter must emit
(`nfl.game`, `nfl.player_week`, `nfl.parlay_leg`, `nfl.player_season`, `wc.match`) are in `docs/sports-task-contract.md`.

## Where to start

1. Read `docs/power2026-build.md` — the living project brief (three workstreams, gates, guardrails).
2. Read `docs/solution-architecture.md` — system-level architecture and the business drivers (latency, accuracy, profitability, tax-loss harvesting).
3. Read `docs/tech-design.md` — concrete tech choices, data contracts, and the execution/OMS/risk design.

## Build order (recommended)

`selflearn-core` first (Gate 1), then H1 backtest, then scanner integration, then Schwab execution. Execution is last on purpose: nothing trades real money until a signal proves a calibrated, net-of-cost-and-tax edge in the core.

## Gates

Stop and confirm at each. See `docs/power2026-build.md` Section 7.

## Not financial advice

Research and informational only. See `docs/power2026-build.md` Section 6.
