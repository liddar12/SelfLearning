# NFL corrections planner v1

Draft API contract for NFL2026 learned parlay adjustments. This does **not**
promise improved predictions. With little graded data, adjustments stay near
zero or are refused.

**Activation remains blocked for learned `applied=true`.** M1 forecast capture
(`generated_unverified`) alone is **fit-ineligible**. The local November 9
checkpoint adds a separate, immutable public-pool attestation; original receipts
keep their status and hash. Runtime application requires
`artifact.source=receipt_journal_publication_as_of_v2` from
`fit_corrections_from_journal` over a pinned journal snapshot joining those
attestations to original source/generic receipts, finalized generations and
authoritative outcome revisions as of the cutoff. A caller-provided
`verified_publication` enum is insufficient. Caller-list, digest-only and legacy
source artifacts echo baseline with `untrusted_artifact_source`.

See [publication evidence](nfl-publication-evidence.md) for configuration,
scope and remaining deployment/evaluation boundaries. This proves public pool
availability, not display or selection of every browser-built card.

Pinned ingest fixtures for NFL producer conformance:
`fixtures/nfl-ingest-m1/` (see `index.json` / `contract.json`).

## Endpoint

`POST /api/v1/nfl/corrections` (server-side only).

Auth: `Authorization: Bearer <SELFLEARN_NFL_CORRECTIONS_TOKEN>`.

- Token must be at least 32 characters.
- Must differ from operator, ingest, outcome, and publication tokens.
- Compared with `hmac.compare_digest`.
- Missing/invalid/duplicate-scope configuration fails closed with **503**.

## Flag: `SELFLEARN_CORRECTIONS_MODE`

Values: `off` | `shadow` | `on`. Unset or invalid → **`off`**.

| Mode | Served values | `applied` | Notes |
|------|---------------|-----------|-------|
| `off` | Exact submitted baselines | `false` | Kill switch. Artifact ignored. |
| `shadow` | Exact submitted baselines | `false` | Logs would-be adjustments for qualified v2 legs. |
| `on` | Qualified v2 legs may be adjusted | `true` only for matched, eligible legs | V1 and parlay probabilities echo baseline. |

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
set `applied=true`. The caller-list fitter and CLI **refuse** to seal
the trusted v2 artifact kind. Journal fits come from `fit_corrections_from_journal`.
The default v1 keeps its existing hashes and historical recomputation.
`artifact_version=2` seals `nfl.corrections_artifact.v2` with
`lineage_policy=exact_frozen_base_calibrator_v1` and the one verified
`base_model_version`/`calibrator_version` pair shared by every admitted graded
receipt. Missing or mixed training lineage prevents a v2 fit. The pair and
policy participate in both hashes. `authorize_corrections_artifact` independently
recomputes the artifact by version from the journal and compares the full result.

Store the sealed artifact **body** (all fields except nested `artifact_id`)
as an existing receipt-journal **`source_snapshot`** object whose
`object_id = fingerprint(payload) = artifact_id`. Helper:
`services.web.nfl_corrections.artifact_snapshot_payload`. Pin it with:

`SELFLEARN_CORRECTIONS_ARTIFACT_ID=sha256:…`

Artifact storage itself needs no new table. Publication-aware fitting requires
the separate publication-attestation migration and an explicitly composed NFL
publication journal; neither is deployed by this local checkpoint.

### Offline replay from a publication journal

Use an existing SQLite NFL publication journal, such as a local rehearsal
journal. Supply explicit UTC cutoffs and save the three JSON results locally.
`HOLDOUT_START` must follow `FITTED_AT`, and `AS_OF` must be at or after
`HOLDOUT_END`. The optional `--policy` on `freeze` accepts a document from
`seal_evaluation_policy`. With a policy, `AS_OF` must equal its sealed outcome
cutoff.

```sh
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" fit \
  --trained-through "$TRAINED_THROUGH" --fitted-at "$FITTED_AT" \
  --expires-at "$EXPIRES_AT" > correction.json
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" freeze \
  --artifact correction.json --start "$HOLDOUT_START" --end "$HOLDOUT_END" > holdout.json
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" score \
  --artifact correction.json --holdout holdout.json --as-of "$AS_OF" > report.json
```

Each command uses a read-only journal connection. The fit verifies publication,
label and lineage evidence and emits a version 2 artifact. Freeze records the
original forecast cohort, and score revalidates that membership, uses labels
known by the cutoff and reports paired log loss with game- and week-clustered
intervals. Inspect the forecast and scored counts and the hold reasons before
interpreting a result. This report is retrospective evidence. A local policy
document carries no independently verified registration time or serving
authority.

### Register a fixed holdout before kickoff

An existing local publication journal can reserve a holdout for one fitted
artifact and its sealed evaluation policy. Run `register` before `HOLDOUT_START`.
The artifact must already be fitted and stay valid through the full window.

```sh
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" register \
  --artifact correction.json --policy sealed-policy.json \
  --start "$HOLDOUT_START" --end "$HOLDOUT_END" > registration.json
```

`register` reserves the window in an append-only transaction, then records when
the committed plan was observed. Both times come from SQLite and must
precede the holdout. Running the same plan again returns the original
receipt. An overlapping plan, including one with different thresholds,
is refused. A registration that was never observed before the start
cannot be used by replay.

After `HOLDOUT_END`, pass the returned `registration_id` to `freeze` with
`--registration-id`. Run `score` after the sealed outcome cutoff. The report
checks the registration in the same journal and identifies SQLite as its
clock authority. This verifies local preregistration of the candidate and
rules. By default it replays candidate probabilities from frozen inputs.

### Score decisions that were recorded before kickoff

During an active registered window, run `capture-shadow` after each
independent publication observation. It uses only forecasts already known to
the local journal at decision time. Both the decision and the observation
of its committed record must precede the relevant game's kickoff. Retrying
keeps the first decision for each receipt. This command and `register` write
local evidence. Fit, freeze and score use read-only journal connections.

```sh
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" capture-shadow \
  --artifact correction.json --registration-id "$REGISTRATION_ID" \
  --start "$HOLDOUT_START" --end "$HOLDOUT_END"
python3 tools/replay_nfl_corrections.py --journal "$NFL_JOURNAL" score \
  --artifact correction.json --holdout holdout.json --as-of "$AS_OF" \
  --recorded-shadow > shadow-report.json
```

`--recorded-shadow` scores the recorded decisions matched to original
publication and outcome receipts. Missing, changed or late evidence holds
the decision and remains visible in the denominator. Hosted collection
and approval to serve a correction require separate evidence.

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
- An authorized artifact with `fitted_at` after the server decision clock → `artifact_not_yet_fitted`.

## Caps

- Leg: ±3 percentage points (configurable, never above hard ceiling).
- The parlay math kernel caps the product of leg ratios at the tighter of ±3pp
  absolute and 15% relative. The endpoint holds joint probability until the
  original joint price has a trusted binding.
- Clamp `[0.01, 0.99]` applies **only** when `applied=true`. If clamping would
  exceed the cap or flip direction, refuse (`applied=false`, echo baseline).
- When `applied=false`, `adjusted_prob` equals the submitted `baseline_prob`
  exactly (including values outside `[0.01, 0.99]`).

## Request / response

Historical v1 request:

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
     "delta": 0.0, "applied": false, "reason": "untrusted_artifact_source",
     "cap_pp": 0.03}
  ],
  "parlays": [
    {"parlay_id": "P1", "baseline_prob": 0.55, "adjusted_prob": 0.55,
     "delta": 0.0, "applied": false, "reason": "untrusted_artifact_source",
     "cap_pp": 0.03}
  ]
}
```

Duplicate or missing `leg_id` references force affected legs/parlays to
`applied=false` with exact baseline echoes. `request_id` and all baseline
fields are echoed exactly.

### Lineage-bound v2 requests

V2 uses `schema_version: 2` on the same route. Add `calibrator_version` and a
`baseline_generation` object containing `workflow`, `run_id`, `run_attempt`,
`intent_id`, `generation_object_id` and `generation_id`. Each leg carries its
`contract_id` and `source_receipt_id`. Use the original pool `generated_at`.
The declaration and receipt references are checked against the server's
collection repository, immutable intent, durably finalized generation and exact
frozen source objects. The generation commit must precede kickoff. A leg's
decision must also precede kickoff. An apply-eligible leg must have a known,
matching pair and the original receipt's exact market, direction, game,
contract and `served_prob`. The response echoes schema version 2 and includes
the artifact lineage when available.

V1 keeps the request and response shape for inspection. A trusted v1 fit in
on mode returns `missing_baseline_lineage` with baseline probabilities. Missing
or incompatible v2 lineage also leaves baseline intact. A selected parlay
echoes `unverified_parlay_baseline` when its legs qualify, or the applicable
leg refusal if a leg is held. A valid v2 binding qualifies the request for
the configured calculation. Prospective policy evaluation and serving approval
are separate decisions.

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
