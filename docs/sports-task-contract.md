# Sports task contract (S1)

The five sports `task` values the spine accepts, their feature / prediction / outcome shapes, and the
two rules every sports record obeys. Companion to `docs/sports-roadmap.md` section 2 (the plan) and
`docs/data-contract.md` (the generic record every task is built on).

Status labels: **[verified]** (asserted by a test in this repo), **[inferred]**, **[VERIFY]**.

Source of truth: `packages/selflearn-core/selflearn_core/contracts_sports.py` — `SPORTS_TASKS` (the
registry), `validate_sports_record(rec)` and `validate_sports_outcome(task, outcome)`. Dependency-free,
like `contracts.py`. One JSON Schema per task under
`packages/selflearn-core/selflearn_core/schemas/sports/` mirrors the shapes for readers in other
languages; `tests/test_sports_contract.py` keeps each schema's required keys equal to the Python shape
**[verified]**.

## How a sports record is checked

`validate_sports_record` runs the generic `validate_prediction_record` first (required top-level fields
`id`, `ts`, `model_version`, `task`, `features`, `prediction`, `horizon_s`; `horizon_s >= 1`), then:

1. `task` is one of the five below.
2. `features` has every required key, of the declared type (`bool` is never accepted as a number).
   Extra feature keys are allowed — adapters may carry more than the minimum.
3. No market key anywhere inside `features` (see *Market rule*).
4. `prediction` has the task's shape and satisfies its value rule (probabilities in `[0, 1]`, vectors
   summing to 1, `low <= points <= high`).
5. `cohort` is free text or null (position / week / tier — whatever the score split needs).
6. When `meta.kickoff_ts` is present, `ts <= meta.kickoff_ts` (see *No-lookahead rule*).

Every error names the offending key, e.g. `nfl.game: features missing required key 'elo_home'`,
`nfl.player_week: prediction must satisfy low <= points <= high, got low=10.0 points=9.0 high=20.0`.

`model_version` names the shipped rule (`weekly_split_v2`, `candidate`, `parlay_v2`, `elo_v3`, ...). A
rule change is a new version, never an overwrite.

## The tasks

Types: `number` (int or float, finite), `int`, `bool`, `string`, `list`, `object`.

### `nfl.game`

| block | keys | rule |
|---|---|---|
| features | `elo_home` number, `elo_away` number, `hfa` number, `rest_home` number, `rest_away` number, `roof` string, `week` int, `families_applied` list | as-of `ts` |
| prediction | `{home_prob}` number in [0, 1] — **or** `{probs: [home, away]}`, each in [0, 1], summing to 1 | one of the two shapes |
| outcome | `home_won` bool, `margin` int (home minus away) | `home_won` must agree with the sign of `margin`; a tie is `home_won: false, margin: 0` |
| horizon | kickoff → final (~4 h); `horizon_s >= 1` | |

### `nfl.player_week`

| block | keys | rule |
|---|---|---|
| features | `position` string, `team` string, `opp` string, `week` int, `dvp_factor` number, `weather_factor` number, `venue_factor` number, `availability` string | as-of `ts` |
| prediction | `points` number, `low` number, `high` number | league-priced PPR; `low <= points <= high` (edges inclusive) |
| outcome | `points_ppr` number, `played` bool | |
| horizon | kickoff → stats final; `horizon_s >= 1` | |

### `nfl.parlay_leg`

| block | keys | rule |
|---|---|---|
| features | `market` string, `line` number, `mu` number, `sd` number, `z` number, `p_team` number, `correlation_tags` list | as-of `ts` |
| prediction | `model_prob` number in [0, 1] | |
| outcome | `hit` bool | an unresolved leg has **no** outcome — it is never resolved, never a miss; `hit: null` / `"unresolved"` are rejected |
| horizon | as the leg's game; `horizon_s >= 1` | |

### `nfl.player_season`

| block | keys | rule |
|---|---|---|
| features | `prior_ppg` number, `projected_games` number, `candidate_signals` object (signal name → value) | as-of `ts`; the market ban applies inside `candidate_signals` too |
| prediction | `points` number, `low` number, `high` number | `low <= points <= high` |
| outcome | `points_ppr` number | |
| horizon | season; `horizon_s >= 1` | |

### `wc.match`

| block | keys | rule |
|---|---|---|
| features | `elo_home` number, `elo_away` number, `momentum` number, `lineup_known` bool, `venue` string | as-of `ts` |
| prediction | `home` number, `draw` number, `away` number | each in [0, 1]; `home + draw + away = 1` (tolerance 1e-6) |
| outcome | `result` `"H"` / `"D"` / `"A"`, `score` `[home_goals, away_goals]` (non-negative ints) | `result` must agree with `score` |
| horizon | kickoff → full time; `horizon_s >= 1` | |

## Market rule — measured, never an input

Owner policy (roadmap section 2 and S9): market prices are a yardstick the scorer compares us against,
not a feature the model reads. The validator rejects any of these keys **at any depth** inside
`features` **[verified]**:

`implied_prob`, `market_prob`, `closing_line`, `moneyline`, `spread_line`, `kalshi`, `polymarket`

The same keys are accepted under `meta` (e.g. `meta.market_prob`, `meta.closing_line`) so S9 can compute
market-vs-ours log-loss from the record **[verified]**. The check is case-insensitive on the key name.

## No-lookahead rule

`ts` is the prediction time and the boundary for everything in `features`. For sports the boundary has
a natural anchor: the event's kickoff. When the adapter carries `meta.kickoff_ts` (unix seconds, integer)
the validator requires `ts <= meta.kickoff_ts` and rejects a record logged after kickoff **[verified]**.
Records without `meta.kickoff_ts` skip this check (the generic invariant still holds by construction in
the adapter). S2 sets `meta.kickoff_ts` on every NFL row it ingests, so "every locked NFL row is in the
store with `ts <= kickoff`" (the S2 measure of success) is enforced by the contract, not by convention.

## Example: `nfl.player_week` from an NFL2026 weekly row

The NFL2026 weekly estimate row shape is `{gsis_id, wk, opp, home, bye, pts}` plus `low` / `high`
**[inferred** from the S1 brief; **VERIFY** against `liddar12/NFL2026` `data/estimates/*.json` when S2
wires the adapter**]**. Mapping, with the row values illustrative:

| NFL2026 row | contract | note |
|---|---|---|
| `gsis_id: "00-0036355"` | `meta.gsis_id` (and part of `id`) | player identity is measurement context, not a feature |
| `wk: 2` | `features.week` | |
| `opp: "GB"` | `features.opp` | |
| `home: true` | `features.venue_factor` | the adapter's home/away multiplier; the raw flag may also ride along in `features.home` (extra keys are allowed) |
| `bye: false` | — | a bye-week row produces **no record** (nothing to predict, nothing to resolve) |
| `pts: 16.4` | `prediction.points` | |
| `low: 9.1`, `high: 24.8` | `prediction.low`, `prediction.high` | |
| position / team / factors / availability | `features.*` | from the NFL2026 snapshot the row was priced from |

```json
{
  "id": "nfl.player_week:2026:wk2:00-0036355",
  "ts": 1757896200,
  "model_version": "weekly_split_v2",
  "task": "nfl.player_week",
  "features": {
    "position": "WR",
    "team": "DET",
    "opp": "GB",
    "week": 2,
    "dvp_factor": 1.06,
    "weather_factor": 1.0,
    "venue_factor": 1.02,
    "availability": "active",
    "home": true
  },
  "prediction": { "points": 16.4, "low": 9.1, "high": 24.8 },
  "horizon_s": 14400,
  "confidence": null,
  "cohort": "WR",
  "meta": { "gsis_id": "00-0036355", "kickoff_ts": 1757899800, "market_prob": null }
}
```

Outcome, once the box score is final: `{"points_ppr": 18.3, "played": true}` →
`validate_sports_outcome("nfl.player_week", outcome)`.

## Who produces these

Partition **S2** (the NFL adapter, `adapters/nfl.py`) produces `nfl.*` records from the NFL2026
snapshots and estimates; **S6/S8** produce `wc.match` from the `wc2026-tracker` corpus and results
pipeline. This document and the validator are the seam they build against; S3 (scores) reads only
records that pass it.
