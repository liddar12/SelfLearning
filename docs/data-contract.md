# Data contract: liddar-terminal → selflearn-core

The terminal app (writer) and the self-learning scorer (reader) live in two
repos. They are coupled by **one data contract**, not by shared code: the shape
of a prediction record. This document is that seam.

## The record

Canonical schema: [`packages/selflearn-core/selflearn_core/schemas/prediction_record.schema.json`](../packages/selflearn-core/selflearn_core/schemas/prediction_record.schema.json).
It mirrors `selflearn_core.types.Prediction` and the `predictions` table in
`store/schema.sql`. Required fields: `id`, `ts`, `model_version`, `task`,
`features`, `prediction`, `horizon_s` (plus optional `confidence`, `cohort`,
`meta`).

The no-lookahead invariant holds at `ts`: everything in `features` must be
knowable at prediction time.

## The breadth stamp

`features.breadth` records the RSP/SPY breadth regime at `ts` so the scorer can
split hit-rate and average return by regime — making the "stick with the wave"
thesis measurable instead of asserted. It is **optional**: when breadth is
unavailable the terminal omits it (never fabricates it), matching
`docs/breadth-integration.md` §3 and the `/api/breadth` fail-visible design.

```json
{
  "breadth": {
    "direction": "rising | falling | flat",
    "vs50dma": "above | below",
    "vs200dma": "above | below",
    "ratio": 0.28475
  }
}
```

All four values come straight from the terminal's `/api/breadth` payload:
`direction`, and `last` compared to `dma50` / `dma200`.

## Building and validating it

`selflearn_core.contracts` provides the builders and a dependency-free validator
(no JSON-Schema library needed):

```python
from selflearn_core import contracts

# from an /api/breadth payload (returns None if breadth is unavailable):
breadth = contracts.breadth_stamp_from_payload(api_breadth_json)

record = {
    "id": uuid,
    "ts": now_unix,
    "model_version": "scanner@2026-08-06",
    "task": "scanner",
    "features": {**other_features, **({"breadth": breadth} if breadth else {})},
    "prediction": {"side": "call", "symbol": "NVDA"},
    "horizon_s": 86_400,
    "confidence": 0.6,
    "cohort": "NVDA",
}
contracts.validate_prediction_record(record)  # raises ValueError if malformed
```

The Python vocabulary (`DIRECTIONS`, `VS_LEVELS`) is kept in sync with the JSON
Schema by a test, so the two can't drift.

## Scoring dimension this unlocks

Group hit-rate and mean signed return by `features.breadth.direction` and by
`features.breadth.vs200dma`, per scanner (`task` = calls vs puts). That is the
concrete measurement the breadth feature exists to enable.

## Status

Contract + validator + schema are in place (this change). Wiring the terminal to
emit these records, and the scorer to read them from a shared store, unlocks with
live prediction logging — the point at which a shared Postgres (Supabase) replaces
local SQLite, since the Vercel writer and the Python reader can't share a file.
