# liddar-ai

Monorepo for three workstreams that share one self-learning spine.

For reproducible local tooling and the current SHADOW-only setup restrictions,
read [Automation development setup](docs/automation-setup.md) and [AGENTS.md](AGENTS.md).
Use [Codex and Grace next steps](docs/codex-grace-next-steps.md) for the owner
handoff, and [service readiness](docs/automation-service-readiness.md) for the
dated account-access findings.

| Workstream | Package / app | What it is | Status |
|---|---|---|---|
| **B (spine)** | `packages/selflearn-core` | Log predictions, resolve outcomes, score, feed back. The honesty layer every other flow depends on. | Built on main: store, scorer + CIs, isotonic/Platt calibration, registry, L0/L1 updater, walk-forward, feature packs (price action, macro, commodities, breadth + VIX regime). No hosted store yet. |
| **A (research)** | `apps/power2026-backtest` | Backtests of the Power 2026 electricity-market claims (H1..H4). | H1 logic built; its `data/` clients were never committed (see `.gitignore` fix) |
| **C (execution)** | `apps/liddar-execution` + `packages/execution-core` | Trading bot: signals → LLM analyst/skeptic → code risk gate → broker, every decision scored. Paper on Alpaca first; live broker selection remains open. | execution-core built (broker port, SimBroker, OMS, risk gate incl. trade cap / drawdown / collar / earnings / options max-loss, staged autonomy SHADOW→SIM→APPROVE→AUTO, approval queue, Alpaca adapter). App and runner not built. |

The React **Liddar Terminal** is the web front end. The current handoff targets
the Vercel project `liddar-terminal-2tpb`; preserve the older March project and
domain. It reads scored results over a small JSON/HTTP boundary and does not import Python.

## Sports adapters

The spine's second family of adapters is sports prediction markets — `liddar12/NFL2026` (live) and
`liddar12/wc2026-tracker` (prior work). See `docs/sports-roadmap.md`; the task shapes every sports adapter must emit
(`nfl.game`, `nfl.player_week`, `nfl.parlay_leg`, `nfl.player_season`, `wc.match`) are in `docs/sports-task-contract.md`.

## Where to start

1. Read `docs/power2026-build.md` — the living project brief (three workstreams, gates, guardrails).
2. Read `docs/solution-architecture.md` — system-level architecture and the business drivers (latency, accuracy, profitability, tax-loss harvesting).
3. Read `docs/tech-design.md` — concrete tech choices, data contracts, and the execution/OMS/risk design.

## Build order (recommended)

`selflearn-core` first (Gate 1), then H1 backtest, then scanner integration, then a reviewed broker integration. Execution is last on purpose: nothing trades real money until a signal proves a calibrated, net-of-cost-and-tax edge in the core.

## Gates

Stop and confirm at each. See `docs/power2026-build.md` Section 7.

## Not financial advice

Research and informational only. See `docs/power2026-build.md` Section 6.
