# Codex handoff: AI investing bot, 2026-11-10

Read this before touching `liddar12/SelfLearning` or `liddar12/liddar-terminal`. Planning context lives in the Financial Terminal project docs: `ai-invest-bot-discovery.md`, `ai-invest-bot-architecture.md` (v2), `ai-invest-bot-consolidation.md`.

## 1. What we are building

A trading bot that runs a small sleeve, built inside `SelfLearning/apps/liddar-execution` on the existing execution core and self-learning spine. Pipeline: code signals → LLM analyst and skeptic (annotate, can veto) → code risk gate → broker. Every decision is logged as a prediction and scored against SPY/RSP. Stages: S0 shadow (log only) → S1 Alpaca paper → S2 live with per-order approval → S3 capped automatic. Promotion is manual.

## 2. Current state (verified today)

| Repo | `main` | State |
|---|---|---|
| SelfLearning | `46be142` (PR #14 merged) | CI green: selflearn-core 220, execution-core 60, signals 5 tests |
| liddar-terminal | `6f4af51` (PR #12 merged) | CI green (`npm test` 7 + build); live at `liddar-terminal-2tpb.vercel.app` |

Shipped today:
- `execution_core/risk.py`: trade cap, drawdown kill switch + latched `kill_switch_tripped`, limit-only + `collar_pct`, earnings blackout (rejects unknown dates), options gate (`allow_options`, `defined_risk`, `max_loss_per_trade`, `min_pop_credit`), `multiplier` in notional. All new limits default off.
- `execution_core/control.py`: `Autonomy` = SHADOW / SIM / APPROVE / AUTO. AUTO raises `AutoLockedError` unless `ControlState(allow_auto=True)`. `can_place_live(approved=...)`.
- `execution_core/approvals.py`: `ApprovalQueue` (expiring, single-use, named decider).
- `execution_core/sizing.py`: `daily_sigma_pct` (vol / sqrt(252)), `vol_scaled_qty`, `unusual_move` (prior-close vol, 2 sigma).
- `execution_core/adapters/alpaca.py`: equities/ETFs, paper default, injectable `transport`, idempotent on `client_order_id` incl. the 422 race. Endpoints verified against docs.alpaca.markets.
- `selflearn_core/features/regime.py`: `BreadthPack` (port of terminal `api/breadth.js`, parity fixture generated from the JS), `VolRegimePack`. New `FeatureFamily.REGIME`.
- Terminal: production V2 scanner prompt restored in `src/scannerPrompt.js`; new `api/quotes.js` (server-side Yahoo, no keys); predictions now carry entry, strike, expiry, target, stop; horizon = expiry.
- Repo hygiene: `.gitignore` `data/` anchored to root; CI now runs execution-core and signals; `SelfLearning/CLAUDE.md` holds the hard constraints.

## 3. Do not do these

- **Do not merge to SelfLearning `main` until Jimmy fixes Vercel.** The `self-learning` Vercel project publishes `main` to production. PR #14's merge replaced the NFL runtime build (`dpl_GxxQXJhijdURfsXUZyCAPBvy4cd3`, branch `release/optimized-nfl-runtime-20261018`). Rollback and the production-branch change are pending with Jimmy.
- Do not push to `main` in either repo. Open PRs. Merging liddar-terminal `main` deploys `liddar-terminal-2tpb` production.
- Do not touch the `liddar-terminal` Vercel project or the `liddar-terminal.vercel.app` domain (still the March build).
- No LLM output may set quantity, edit `Limits`, or bypass `risk.check`. The learning loop proposes `Change`s; it never applies them.
- No lookahead: every feature carries `obs_ts <= as_of_ts` (`validate_no_lookahead`).
- Fail flat: missing data, model timeout or broker error means no trade and an explicit error. No silent fallbacks, no invented prices.
- Secrets only from env vars. Never commit or print them.
- If you change `compute_breadth` or `api/breadth.js`, change both and regenerate `tests/fixtures_breadth_parity.json` from the JS.

## 4. Regression gate (must be green before a PR is ready)

```sh
# SelfLearning
(cd packages/selflearn-core && pip install -e ".[dev]" && python -m pytest -q && python -m examples.walkforward_demo)
(cd packages/execution-core && pip install -e ".[dev]" && python -m pytest -q)
(cd packages/signals && pip install -e ".[dev]" && python -m pytest -q)
# liddar-terminal
npm ci && npm test && npm run build
```

## 5. Next tasks, unblocked (in order)

Each is one PR, tests first, no keys or decisions needed.

1. **Reconciliation** (`execution_core/reconcile.py`): pure function comparing broker positions/open orders with the OMS's expected state; returns a diff and a halt flag. Acceptance: any qty or symbol mismatch beyond a tolerance halts; tests cover missing, extra, partial-fill and rounding cases.
2. **Signal registry + NEVER-REGRESS gate** (`selflearn_core/signals_registry.py`, `selflearn_core/scoring/never_regress.py`): every signal registered at weight 0; a candidate config is adopted only if held-out loss improves by a fixed margin (mirror NFL2026 `scripts/optimize/never_regress.py`, margin configurable). Acceptance: registry test asserts all weights start at 0; gate rejects ties and regressions.
3. **Options odds engine** (`apps/liddar-execution/options/odds.py`): probability of profit and max-loss for verticals from (a) Monte Carlo calibrated to supplied quotes/IV with an optional earnings jump and (b) historical bootstrap of daily returns; report the stricter. Output feeds `intent.meta["pop"]` and `["max_loss"]`. Acceptance: deterministic with a seed; known-answer tests against Black-Scholes N(d2) for the no-jump lognormal case.
4. **Judgment provider interface** (`apps/liddar-execution/judgment/`): `Provider` protocol with `claude`, `grok` (xAI, `grok-4.6`), `openai` (`gpt-5.6-terra`) adapters behind injectable transports; `analyst.py` and `skeptic.py` return validated JSON (thesis, catalysts, risks; skeptic adds `veto: bool` and `invalidation_price`). Every call returns a receipt with provider, model id, latency and raw text. Acceptance: fixture tests, schema validation, a malformed response yields `veto=None` and an error, never a guess.
5. **Shadow runner skeleton** (`apps/liddar-execution/runner/`): `premarket.py` assembles regime + price features for the universe (`config/universe.json`), runs judgment, runs `risk.check` in SHADOW, and writes `trade_decision` predictions to `SqliteStore`. Add `.github/workflows/bot-shadow.yml` with `workflow_dispatch` only (no schedule until the hosted store exists). Acceptance: end-to-end test with in-memory bars and fake providers produces stamped predictions and zero orders.
6. **Terminal `/api/series` + `/api/ratio`** (backlog P0-1): factor `fetchDaily()` and SMA helpers out of `api/breadth.js` into `api/_yahoo.js`; `/api/breadth` output must be byte-identical (snapshot test).

## 6. Blocked (do not start)

| Item | Waiting on |
|---|---|
| Hosted prediction store (Supabase) + `SUPABASE_*` env on `liddar-terminal-2tpb` | Jimmy: create or connect project |
| Extract `SupabaseStore`, owner-intent and learning-job journals from the NFL release branch | Jimmy: Gate 1 decision 7 |
| `SchwabBroker` | Schwab developer app + OAuth credentials |
| Alpaca paper run | `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` (paper) |
| Fundamentals in the scanner prompt | Rotated Alpha Vantage key (old one is public in the March bundle) |
| `power2026_backtest/data/` (EIA/ISO clients) | Source exists only where it was written; never committed |
| Scheduled runs, autonomy promotion, live money | Gate 1 decisions 1 to 8 |

## 7. Gate 1 decisions still open (Jimmy)

1. Brokers: Alpaca paper for S0/S1, Schwab live from S2 (recommended).
2. Instruments: stocks/ETFs + defined-risk verticals under a $1,500 max loss (recommended).
3. LLM lineup in shadow: Claude, Grok 4.6, GPT-5.6 Terra, or a subset.
4. Risk table values and live sleeve size.
5. Fitness: excess return vs SPY after costs, NEVER-REGRESS adoption.
6. Store: new Supabase project (recommended) or existing.
7. Release branch: extract generic pieces to main (recommended).
8. Repo: SelfLearning `apps/liddar-execution` (default).

Not financial advice. Research and engineering only.
