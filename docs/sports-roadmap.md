# Sports Adapter Roadmap — the spine as a self-learning platform for sports prediction markets

**Date:** 2026-09-02 · **Horizon:** to Aug 2027 · **Companion:** `liddar12/NFL2026` `docs/ROADMAP_2026-27.md`
(the product-side plan; this file is the spine side). Status legend: ✅ done · 🟡 partial · ⬜ planned · 🔒 owner decision.

## 1. Thesis

The spine already does the four things a sports adapter needs: log a `Prediction` before its event,
resolve an `Outcome` after it, emit `Score` rows with confidence intervals, and propose `Change`s under
staged autonomy (L0 monitor → L1 calibration → L2 weights → L3/L4). NFL2026 has spent a season building
exactly those four things by hand, plus two pieces the spine does not have yet: a **never-regress
promotion gate** (margin + significance on held-out log-loss) and a **learning ledger** that scores every
weekly estimate as made. The roadmap moves NFL2026 onto the spine, ports those two pieces into it, and
then adds the second sports adapter from the prior WC2026 work — so that "agnostic" is proven by two
adapters sharing one store, one scorer and one promotion policy.

## 2. Task contract for sports (S1)

New `task` values and their shapes, all validated by `contracts.py` and the JSON schema:

| task | features (as-of ≤ kickoff) | prediction | outcome (realized) | horizon_s |
|---|---|---|---|---|
| `nfl.game` | elo_home, elo_away, hfa, rest, roof, week, adopted families | `{home_prob}` (vector sums to 1) | `{home_won, margin}` | kickoff → final (~4 h) |
| `nfl.player_week` | position, opp, dvp factor, weather, venue, availability, week | `{points, low, high}` (league-priced PPR) | `{points_ppr, played}` | kickoff → stats final |
| `nfl.parlay_leg` | market, line, mu, sd, z, p_team, correlation tags | `{model_prob}` | `{hit}` | as the leg's game |
| `nfl.player_season` | prior_ppg, projected_games, candidate signals | `{points, band}` | `{points_ppr}` | season |
| `wc.match` | elo, momentum, lineup, venue | `{home, draw, away}` | `{result, score}` | kickoff → FT |

`cohort` carries position / week / tier so scores split the way the MODEL tab already does. `model_version`
carries the shipped rule (`weekly_split_v2`, `candidate`, `parlay_v2`) so a rule change is a new version, never
an overwrite. Market prices are allowed in `meta` for measurement and never in `features`.

## 3. Releases

### Q3 2026 — read side first
- **S1 Sports task contract** ⬜ — shapes above, validator, docs. MoS: NFL2026 snapshots and ledger rows validate.
- **S2 NFL adapter (read)** ⬜ — `adapters/nfl.py` ingests `data/snapshots/*.json` and `data/estimates/*.json`
  from the NFL2026 repo (nightly, GitHub Action in this repo pulling the raw files) into the store. MoS: every
  locked NFL row is in the store with `ts ≤ kickoff`; nothing is fabricated when a file is missing.
- **S3 Scores for sports tasks** ⬜ — general scorer (Gate 3 ✅) run over the NFL tasks: MAE, bias, Brier,
  log-loss, calibration curve, per cohort, Wilson CIs; terminal Scores tab reads them. MoS: same number on the
  NFL2026 MODEL tab and the terminal for the same week.
- 🔒 **Supabase store (M1)** — a Vercel writer and a Python reader cannot share SQLite; the Postgres schema is
  merged (#4). Blocks live logging.

### Q4 2026 — learn from the record
- **S4 L1 calibration for sports** ⬜ — Platt/isotonic per task once `min_resolved` (30, 🔒) is met; emitted as
  `Change` rows, never auto-applied. MoS: `Change` rows for `nfl.parlay_leg` and `nfl.game` with rationale.
- **S5 Never-regress as the registry policy** ⬜ — port NFL2026's `promote_signals` rule (paired margin +
  Bonferroni-over-families significance, walk-forward) as the spine's promotion policy; NFL2026 keeps identical
  verdicts on its committed fixtures. MoS: one policy, two adapters, byte-identical verdicts.
- **S6 WC2026 corpus import** ⬜ — tournament predictions and results from `wc2026-tracker` become a resolved
  `wc.match` corpus (prior work reused without change). MoS: Brier/log-loss with CIs for `wc.match`.

### Q1 2027 — extract the platform
- **S7 Harness extraction** ⬜ — NFL2026 `scripts/harness/*` (snapshots, metrics, honesty, conformal), the
  never-regress gate, the signal registry and the ledger objective move into `selflearn-core`; NFL2026 pins the
  package. MoS: NFL2026's full gate green with byte-identical `data/*.json` before and after the swap.
- **S8 Second live adapter** ⬜ — WC2026 (or the next tournament) logs before kickoff and resolves from its
  results pipeline. MoS: two adapters, one store, one Scores view.
- **S9 Market yardstick service** ⬜ — closing lines / Kalshi / Polymarket ingested as measurement only
  (policy). MoS: market-vs-ours log-loss shown identically on MODEL and the terminal.

### Q2 2027 — second season ready
- **S10 L2 weights across adapters** ⬜ — ensemble/weight updater proposes reweights under S5. MoS: proposals with
  CIs, still human-applied.
- **S11 Multi-sport scores** ⬜ — learning curves per adapter (resolved n, MAE, calibration) from one store.
- **S12 Packaging (M4)** ⬜ — `selflearn-core` published as a zero-dep package; adapters pin a version.

## 4. What stays out of scope here
The power-market research (Workstream A) and execution (Workstream C) tracks keep their own gates in
`docs/roadmap.md`. Nothing in this file trades, and nothing here changes the "not financial advice" posture.

## 5. Owner decisions (🔒)
Supabase for the store · `min_resolved` and the L2 allow-list · which market sources may be measured ·
second adapter (recommendation: WC2026) · extraction in the Q1 2027 offseason (recommended).
