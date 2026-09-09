# liddar-ai

Monorepo for three workstreams that share one self-learning spine.

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
