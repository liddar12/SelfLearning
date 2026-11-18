# CLAUDE.md — liddar-ai (SelfLearning)

Python monorepo: `packages/selflearn-core` (self-learning spine), `packages/execution-core`
(broker port, OMS, risk gate, staged autonomy), `packages/signals`, `apps/liddar-execution`
(the trading bot), `apps/power2026-backtest`. The Vite/Vercel terminal is a separate repo
(`liddar12/liddar-terminal`) that reads results over JSON and never imports Python.

## Working agreement

- Substantial builds run four gates; stop and confirm at each: solution architecture, design
  direction, backlog, deploy. Scoped fixes skip to the regression gate and a PR.
- When asking for a decision, give options with a recommendation.
- Plain, learned tone. No em-dashes in prose written for Jimmy.
- Never push to `main` directly; open a PR. Never deploy red.

## Hard constraints for the bot (non-negotiable)

1. **Code decides size and limits. LLMs annotate and veto.** No LLM output may set quantity,
   change a `Limits` value, or bypass `risk.check`. Sizing lives in `execution_core/sizing.py`.
2. **The learning loop may propose, never apply.** Updaters return `Change` proposals; promotion
   and autonomy changes are manual. Nothing in the loop may edit the risk table.
3. **AUTO autonomy is locked** (`ControlState(allow_auto=False)` by default). Unlocking is the
   stage-S3 promotion, done by a person.
4. **Every decision is a logged prediction** with its feature snapshot, regime stamp, provider and
   model id, risk verdict and outcome. No log, no trade.
5. **No lookahead.** Features use only data with observation time <= `as_of_ts`
   (`validate_no_lookahead`). LLM layers are judged on forward (shadow/paper) results only;
   backtests on dates before a model's knowledge cutoff are not evidence.
6. **Fail visible, fail flat.** Missing data, a model timeout or a broker error means no new
   trade and an explicit error, never a guess or a silent fallback.
7. **Broker is truth.** Reconciliation mismatch halts trading.
8. **Signals enter at weight 0** and earn weight only by beating the current configuration
   out-of-sample (NFL2026's NEVER-REGRESS discipline).
9. **Secrets** come from env vars only (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, Supabase keys,
   model API keys). Never commit them; never print them.

## Regression gate

```sh
(cd packages/selflearn-core && python -m pytest -q && python -m examples.walkforward_demo)
(cd packages/execution-core && python -m pytest -q)
(cd packages/signals && python -m pytest -q)
```

All three must pass before a PR is marked ready. CI runs the same.

## Parity

`selflearn_core/features/regime.py::compute_breadth` mirrors `api/breadth.js` in the terminal.
Change both together; the fixture `tests/fixtures_breadth_parity.json` is generated from the JS.

## Not financial advice

Research and engineering only.
