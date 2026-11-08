# NFL corrections planner v1

Draft API contract for NFL2026 learned parlay adjustments. This does **not**
promise improved predictions. With little graded data, adjustments stay near
zero or are refused.

**Activation remains blocked for learned `applied=true`.** M1 forecast capture
(`generated_unverified`) is allowed and is **fit-ineligible**. Runtime
application requires `artifact.source=receipt_journal_as_of_v1` from
`fit_corrections_from_journal` over `verified_publication` receipts plus
authoritative revisions as-of cutoff. Caller-list / digest-only artifacts
echo baseline with `untrusted_artifact_source`. No invented served enum.

Pinned ingest fixtures for NFL producer conformance:
`fixtures/nfl-ingest-m1/` (see `index.json` / `contract.json`).

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

### How the flag is read / redeploy (not rapid)

The mode is read **per request** from the process environment (`os.environ`).
On Vercel, changing an env var still requires a **redeploy** before workers see
the new value. Engine `off` does **not** rewrite already-served NFL static JSON.

NFL served site is Netlify static `data/*.json` — consumer OFF requires
**rebuild + publish + Netlify deploy** (minutes; `/data/*` SWR ≤120s).

**There is no viable rapid-disable today on either side.** Do not invent a
no-redeploy kill switch or call deploy-based OFF immediate.

## Artifact storage (no migration)

Fit offline with `tools/fit_nfl_corrections.py` or
`fit_corrections_artifact(...)`.

Caller-supplied grade lists are integrity-hashed only — **not** authenticated
provenance. Default `source` is `graded_caller_list_untrusted` and **cannot**
set `applied=true`. Trusted fits use `fit_corrections_from_journal` →
`source=receipt_journal_as_of_v1` (existing revision/`_latest_as_of` primitives).
Digests detect mutation; they are **not** signatures.

Store the sealed artifact **body** (all fields except nested `artifact_id`)
as an existing receipt-journal **`source_snapshot`** object whose
`object_id = fingerprint(payload) = artifact_id`. Helper:
`services.web.nfl_corrections.artifact_snapshot_payload`. Pin it with:

`SELFLEARN_CORRECTIONS_ARTIFACT_ID=sha256:…`

No new Supabase migration is required.

## Method

Per-**segment** additive bias with empirical-Bayes shrinkage toward zero.
Segment key is `market|selection` (case-sensitive market/selection text as
submitted; void/push status matching is casefold):

`delta = mean(hit - baseline_prob) * n / (n + prior_strength)`

then capped at ±`leg_cap_pp` (hard ceiling 3pp).

- Minimum **200** eligible graded legs overall, else nothing applies
  (`insufficient_graded_data`).
- Minimum **50** eligible graded legs **per segment**, else that segment is
  refused (`insufficient_segment_data`). No inherited global correction.
- `eligible_for_application` must equal `n_eligible >= min_graded_per_market`;
  `n_graded` must equal the sum of segment `n_eligible`; `sufficient_total`
  must equal `n_graded >= min_graded_total`.
- `eligibility_rules` must equal the canon list exactly (reject empty/fake).
- Clock order required: `trained_through <= fitted_at <= expires_at`.
- Eligibility: `served_at` before kickoff, binary outcome, exclude push/void
  (casefold), `graded_at <= trained_through`, required `selection`.
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

Response `artifact` (when present) includes full public provenance:
`artifact_id`, `model_version`, `method`, clocks, `n_graded`, `source`,
`eligibility_rules`, `fit_inputs_hash`, floors/caps/clamp, and `segments`
(keyed `market|selection`).

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
    "source": "graded_caller_list_untrusted",
    "eligibility_rules": ["served_at_before_kickoff", "exclude_push_void",
      "exclude_after_trained_through", "baseline_prob_as_served_in_(0,1)",
      "binary_outcome_only", "segment_market_and_selection"],
    "fit_inputs_hash": "sha256:...",
    "prior_strength": 50.0,
    "min_graded_total": 200,
    "min_graded_per_market": 50,
    "leg_cap_pp": 0.03,
    "parlay_cap_pp": 0.03,
    "parlay_cap_rel": 0.15,
    "clamp": [0.01, 0.99],
    "segments": {
      "qb_pass_yds|over": {
        "n_eligible": 60, "raw_bias": -0.08, "delta": -0.0436,
        "eligible_for_application": true
      }
    },
    "sufficient_total": false
  },
  "legs": [
    {"leg_id": "L1", "baseline_prob": 0.55, "adjusted_prob": 0.55,
     "delta": 0.0, "applied": false, "reason": "insufficient_graded_data",
     "cap_pp": 0.03}
  ],
  "parlays": [
    {"parlay_id": "P1", "baseline_prob": 0.55, "adjusted_prob": 0.55,
     "delta": 0.0, "applied": false, "reason": "insufficient_graded_data",
     "cap_pp": 0.03}
  ]
}
```

Duplicate or missing `leg_id` references force affected legs/parlays to
`applied=false` with exact baseline echoes. `request_id` and all baseline
fields are echoed exactly.

## NFL owner relay — minimum evidence gaps (read-only)

Scoped read-only pin (do not modify NFL repo / secrets):

`https://github.com/jliddar-omilia/NFL2026/blob/0612b161d955ea78a6231f5a8d3e8fbf28d271ef/docs/pilot/selflearning-parlay-corrections-20261106.md`

Document sha256 (raw): `e418482db0ff3526d9201c0f66889bbfb84e4292155575b9b532ae02e3fcf379`.

Reported candidates (counts **unverified** here; independently verify identity,
uniqueness, `graded_at`, revision/as-of, and actual served proof before
accepting any record):

| Candidate | Gap vs fit-ready |
|-----------|------------------|
| `data/estimates/parlays_2026.json` + `data/parlay_leg_scores.json` | Flat ledger missing `baseline_model_version`; lock ≠ proof actually served |
| `data/my_cards/2026_wk02..09.json` + `data/my_card_scores.json` | Has `learning_receipt` fields, but TEAM-seed reconstructed offerings — not all browser cards |
| Engine journal grades | Owner: none captured via engine yet |

Do **not** fit or upload a production artifact from this relay.

## Rollback

Set `SELFLEARN_CORRECTIONS_MODE=off` (or unset it) and **redeploy**. That is
not immediate. NFL static JSON OFF is separately rebuild+publish+Netlify.
Do not merge this draft until explicitly approved. Activation blocked.

## Limits

- No accuracy or weekly improvement guarantee.
- `applied=true` only from a fitted artifact over verified graded legs.
- Heuristics are never labeled as learned.
- Fail closed to baseline on error, timeout, missing/stale data, NaN, or cap
  violation — never serve an error body in place of picks once the request is
  valid (invalid requests still return 422).
- Liar timestamps / future cutoffs without authenticated provenance remain
  activation blockers (caller lists are untrusted).
