# NFL corrections planner v1

Draft API contract for NFL2026 learned parlay adjustments. This does **not**
promise improved predictions. With little graded data, adjustments stay near
zero or are refused.

## Endpoint

`POST /api/v1/nfl/corrections` (server-side only).

Auth: `Authorization: Bearer <SELFLEARN_NFL_CORRECTIONS_TOKEN>`.

- Token must be at least 32 characters.
- Must differ from operator, ingest, and outcome tokens.
- Compared with `hmac.compare_digest`.
- Missing/invalid/duplicate-scope configuration fails closed with **503**.

## Flag: `SELFLEARN_CORRECTIONS_MODE`

Values: `off` | `shadow` | `on`. Unset or invalid → **`off`**.

| Mode | Served values | `applied` | Notes |
|------|---------------|-----------|-------|
| `off` | Exact submitted baselines | `false` | Kill switch. Artifact ignored. |
| `shadow` | Exact submitted baselines | `false` | Computes would-be adjustments and logs them. |
| `on` | Adjusted when learned rules pass | `true` only for learned fits | Still fails closed to baseline on any fault. |

### How the flag is read / redeploy

The mode is read **per request** from the process environment (`os.environ`),
so no code change is required to flip it. On Vercel, changing an env var still
requires a **redeploy** before workers see the new value. A no-redeploy kill
switch is not available in this v1 without an external config store; use
`off` + redeploy, or leave the mode unset (defaults to off).

## Artifact storage (no migration)

Fit offline with `tools/fit_nfl_corrections.py` or
`fit_corrections_artifact(...)`.

Store the sealed artifact **body** (all fields except nested `artifact_id`)
as an existing receipt-journal **`source_snapshot`** object whose
`object_id = fingerprint(payload) = artifact_id`. Helper:
`services.web.nfl_corrections.artifact_snapshot_payload`. Pin it with:

`SELFLEARN_CORRECTIONS_ARTIFACT_ID=sha256:…`

No new Supabase migration is required.

## Method

Per-market additive bias with empirical-Bayes shrinkage toward zero:

`delta = mean(hit - baseline_prob) * n / (n + prior_strength)`

then capped at ±`leg_cap_pp` (hard ceiling 3pp).

- Minimum **200** eligible graded legs overall, else nothing applies
  (`insufficient_graded_data`).
- Minimum **50** eligible graded legs **per market segment**, else that
  segment is refused (`insufficient_segment_data`). No inherited global
  correction.
- Eligibility: `served_at` before kickoff, binary outcome, exclude push/void,
  `graded_at <= trained_through`.
- Expired `expires_at` → `stale_artifact`.

## Caps

- Leg: ±3 percentage points (configurable, never above hard ceiling).
- Parlay: `baseline_parlay * Π(adj_leg / base_leg)`, then tighter of ±3pp
  absolute and 15% relative.
- Clamp `[0.01, 0.99]` applies **only** when `applied=true`. If clamping would
  exceed the cap or flip direction, refuse (`applied=false`, echo baseline).
- When `applied=false`, `adjusted_prob` equals the submitted `baseline_prob`
  exactly (including values outside `[0.01, 0.99]`).

## Request / response

Request:

```json
{
  "schema_version": 1,
  "request_id": "...",
  "season": 2026,
  "week": 9,
  "baseline_model_version": "sha256:...",
  "generated_at": "2026-11-06T12:00:00Z",
  "legs": [
    {"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
     "selection": "over", "baseline_prob": 0.55}
  ],
  "parlays": [
    {"parlay_id": "P1", "leg_ids": ["L1"], "baseline_prob": 0.55}
  ]
}
```

Response:

```json
{
  "schema_version": 1,
  "request_id": "...",
  "corrections_mode": "on",
  "artifact": {
    "artifact_id": "sha256:...",
    "model_version": "corrections_v1",
    "method": "market_bias_shrinkage_v1",
    "trained_through": "...",
    "fitted_at": "...",
    "expires_at": "...",
    "n_graded": 180,
    "source": "graded_journal_receipts"
  },
  "legs": [
    {"leg_id": "L1", "baseline_prob": 0.55, "adjusted_prob": 0.53,
     "delta": -0.02, "applied": true, "reason": "learned_segment_bias",
     "cap_pp": 0.03}
  ],
  "parlays": [
    {"parlay_id": "P1", "baseline_prob": 0.55, "adjusted_prob": 0.53,
     "delta": -0.02, "applied": true, "reason": "learned_parlay_product",
     "cap_pp": 0.03}
  ]
}
```

Duplicate or missing `leg_id` references force affected legs/parlays to
`applied=false` with exact baseline echoes. `request_id` and all baseline
fields are echoed exactly.

## Rollback

Set `SELFLEARN_CORRECTIONS_MODE=off` (or unset it) and redeploy. Served picks
return to exact baselines. Do not merge this draft until explicitly approved.

## Limits

- No accuracy or weekly improvement guarantee.
- `applied=true` only from a fitted artifact over verified graded legs.
- Heuristics are never labeled as learned.
- Fail closed to baseline on error, timeout, missing/stale data, NaN, or cap
  violation — never serve an error body in place of picks once the request is
  valid (invalid requests still return 422).
