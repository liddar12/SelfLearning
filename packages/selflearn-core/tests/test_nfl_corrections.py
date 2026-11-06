"""Unit and property tests for NFL corrections v1. Pure; no network."""
from __future__ import annotations

import copy
import math
import random

import pytest

from selflearn_core.adapters.nfl_corrections import (
    CorrectionsError,
    corrections_mode,
    fit_corrections_artifact,
    plan_corrections,
    validate_artifact,
)
from selflearn_core.adapters.nfl_corrections_policy import (
    HARD_CLAMP,
    HARD_LEG_CAP_PP,
    HARD_PARLAY_CAP_PP,
    HARD_PARLAY_CAP_REL,
)


def _stamp(day=1, hour=12):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"


def graded_row(market="qb_pass_yds", baseline=0.55, hit=True, day=1, status=None,
               selection="over"):
    row = {
        "market": market,
        "selection": selection,
        "baseline_prob": baseline,
        "served_at": _stamp(day, 10),
        "kickoff_at": _stamp(day, 18),
        "graded_at": _stamp(day + 1, 8),
        "outcome": hit,
    }
    if status is not None:
        row["status"] = status
    return row


def make_graded(n_per_market=60, markets=("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"),
                bias=0.08):
    rows = []
    for index, market in enumerate(markets):
        for i in range(n_per_market):
            # Deterministic over-confidence: hit less often than baseline suggests.
            baseline = 0.60
            hit = (i % 10) < int(round((baseline - bias) * 10))
            rows.append(graded_row(market=market, baseline=baseline, hit=hit, day=1 + (i % 20)))
    return rows


def make_artifact(**kwargs):
    defaults = dict(
        graded_records=make_graded(),
        trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=150,
        min_graded_per_market=50,
        prior_strength=50.0,
    )
    defaults.update(kwargs)
    return fit_corrections_artifact(**defaults)


def make_request(*, legs=None, parlays=None, request_id="req-1"):
    if legs is None:
        legs = [
            {"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
             "selection": "over", "baseline_prob": 0.55},
            {"leg_id": "L2", "game_id": "G1", "market": "rb_rush_yds",
             "selection": "over", "baseline_prob": 0.52},
        ]
    if parlays is None:
        parlays = [{"parlay_id": "P1", "leg_ids": ["L1", "L2"], "baseline_prob": 0.286}]
    return {
        "schema_version": 1,
        "request_id": request_id,
        "season": 2026,
        "week": 9,
        "baseline_model_version": "sha256:" + ("a" * 64),
        "generated_at": "2026-11-06T12:00:00Z",
        "legs": legs,
        "parlays": parlays,
    }


def test_mode_defaults_off_for_unset_and_invalid():
    assert corrections_mode(None) == "off"
    assert corrections_mode("") == "off"
    assert corrections_mode("nope") == "off"
    assert corrections_mode("OFF") == "off"
    assert corrections_mode("shadow") == "shadow"
    assert corrections_mode("ON") == "on"


def test_off_is_byte_for_byte_baseline_echo():
    request = make_request()
    # Baseline outside clamp range must still echo exactly when applied=false.
    request["legs"][0]["baseline_prob"] = 0.005
    response = plan_corrections(request, mode="off", artifact=make_artifact())
    assert response["corrections_mode"] == "off"
    assert response["artifact"] is None
    assert response["request_id"] == "req-1"
    for src, out in zip(request["legs"], response["legs"]):
        assert out["baseline_prob"] == src["baseline_prob"]
        assert out["adjusted_prob"] == src["baseline_prob"]
        assert out["applied"] is False
        assert out["delta"] == 0.0
        assert out["reason"] == "corrections_off"
    for src, out in zip(request["parlays"], response["parlays"]):
        assert out["baseline_prob"] == src["baseline_prob"]
        assert out["adjusted_prob"] == src["baseline_prob"]
        assert out["applied"] is False


def test_shadow_serves_baseline_and_never_applied():
    artifact = make_artifact()
    response = plan_corrections(make_request(), mode="shadow", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    assert response["corrections_mode"] == "shadow"
    assert response["artifact"]["artifact_id"] == artifact["artifact_id"]
    assert all(row["applied"] is False for row in response["legs"])
    assert all(row["adjusted_prob"] == row["baseline_prob"] for row in response["legs"])
    assert all(row["applied"] is False for row in response["parlays"])


def test_on_applies_within_caps_and_clamp():
    artifact = make_artifact()
    response = plan_corrections(make_request(), mode="on", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    assert response["corrections_mode"] == "on"
    applied = [row for row in response["legs"] if row["applied"]]
    assert applied
    for row in applied:
        assert abs(row["delta"]) <= HARD_LEG_CAP_PP + 1e-12
        assert HARD_CLAMP[0] <= row["adjusted_prob"] <= HARD_CLAMP[1]
        assert row["reason"] == "learned_segment_bias"
        assert row["cap_pp"] <= HARD_LEG_CAP_PP
    for row in response["parlays"]:
        if row["applied"]:
            bound = min(HARD_PARLAY_CAP_PP, abs(row["baseline_prob"]) * HARD_PARLAY_CAP_REL)
            assert abs(row["delta"]) <= bound + 1e-12
            assert HARD_CLAMP[0] <= row["adjusted_prob"] <= HARD_CLAMP[1]


def test_insufficient_total_graded_blocks_everything():
    artifact = make_artifact(graded_records=make_graded(n_per_market=40),
                             min_graded_total=200, min_graded_per_market=30)
    response = plan_corrections(make_request(), mode="on", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    assert all(row["applied"] is False for row in response["legs"])
    assert all(row["reason"] == "insufficient_graded_data" for row in response["legs"])


def test_insufficient_segment_gets_no_inherited_global():
    # Plenty overall, but wr_rec_yds below 50; qb/rb at 60.
    rows = make_graded(n_per_market=60, markets=("qb_pass_yds", "rb_rush_yds"))
    rows += [graded_row(market="wr_rec_yds", baseline=0.6, hit=False, day=1 + i)
             for i in range(20)]
    artifact = make_artifact(graded_records=rows, min_graded_total=100,
                             min_graded_per_market=50)
    assert artifact["segments"]["wr_rec_yds|over"]["eligible_for_application"] is False
    assert artifact["segments"]["qb_pass_yds|over"]["eligible_for_application"] is True
    request = make_request(legs=[
        {"leg_id": "Lq", "game_id": "G1", "market": "qb_pass_yds",
         "selection": "over", "baseline_prob": 0.55},
        {"leg_id": "Lw", "game_id": "G1", "market": "wr_rec_yds",
         "selection": "over", "baseline_prob": 0.55},
    ], parlays=[{"parlay_id": "P1", "leg_ids": ["Lq", "Lw"], "baseline_prob": 0.3}])
    response = plan_corrections(request, mode="on", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    by_id = {row["leg_id"]: row for row in response["legs"]}
    assert by_id["Lq"]["applied"] is True
    assert by_id["Lw"]["applied"] is False
    assert by_id["Lw"]["reason"] == "insufficient_segment_data"
    assert by_id["Lw"]["adjusted_prob"] == 0.55
    assert response["parlays"][0]["applied"] is False
    assert response["parlays"][0]["adjusted_prob"] == 0.3


def test_stale_artifact_refuses_application():
    artifact = make_artifact(
        trained_through="2026-10-15T00:00:00Z",
        fitted_at="2026-10-15T01:00:00Z",
        expires_at="2026-11-01T00:00:00Z",
    )
    response = plan_corrections(make_request(), mode="on", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    assert all(row["reason"] == "stale_artifact" for row in response["legs"])
    assert all(row["applied"] is False for row in response["legs"])


def test_push_void_and_post_cutoff_excluded_from_fit():
    rows = make_graded(n_per_market=60)
    rows.append(graded_row(status="PUSH", hit=True))
    rows.append(graded_row(status="VOID", hit=False))
    late = graded_row(day=30)
    late["graded_at"] = "2026-11-15T00:00:00Z"
    rows.append(late)
    after_kickoff = graded_row(day=2)
    after_kickoff["served_at"] = "2026-10-02T19:00:00Z"
    after_kickoff["kickoff_at"] = "2026-10-02T18:00:00Z"
    rows.append(after_kickoff)
    artifact = make_artifact(graded_records=rows, min_graded_total=100)
    assert artifact["n_graded"] == 180
    assert "eligibility_rules" in artifact
    assert artifact["fit_inputs_hash"].startswith("sha256:")
    validate_artifact(artifact)


def test_duplicate_leg_ids_and_missing_parlay_refs():
    request = make_request(legs=[
        {"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
         "selection": "over", "baseline_prob": 0.55},
        {"leg_id": "L1", "game_id": "G2", "market": "qb_pass_yds",
         "selection": "under", "baseline_prob": 0.45},
        {"leg_id": "L2", "game_id": "G1", "market": "rb_rush_yds",
         "selection": "over", "baseline_prob": 0.52},
    ], parlays=[
        {"parlay_id": "P1", "leg_ids": ["L1", "L2"], "baseline_prob": 0.28},
        {"parlay_id": "P2", "leg_ids": ["L2", "Lmissing"], "baseline_prob": 0.2},
    ])
    response = plan_corrections(request, mode="on", artifact=make_artifact(),
                                now="2026-11-02T00:00:00Z")
    assert all(row["leg_id"] != "L1" or row["reason"] == "duplicate_leg_id"
               for row in response["legs"])
    assert all(row["applied"] is False for row in response["legs"] if row["leg_id"] == "L1")
    by_parlay = {row["parlay_id"]: row for row in response["parlays"]}
    assert by_parlay["P1"]["applied"] is False
    assert by_parlay["P1"]["adjusted_prob"] == 0.28
    assert by_parlay["P2"]["applied"] is False
    assert by_parlay["P2"]["reason"] == "missing_leg_id"
    assert by_parlay["P2"]["adjusted_prob"] == 0.2


def test_clamp_direction_flip_refuses_application():
    artifact = make_artifact()
    # Force a large negative segment delta via direct mutation of a copy.
    mutated = copy.deepcopy(artifact)
    body = {k: v for k, v in mutated.items() if k != "artifact_id"}
    body["segments"]["qb_pass_yds|over"]["delta"] = -0.03
    body["segments"]["qb_pass_yds|over"]["eligible_for_application"] = True
    body["n_graded"] = sum(row["n_eligible"] for row in body["segments"].values())
    body["sufficient_total"] = body["n_graded"] >= body["min_graded_total"]
    from selflearn_core.probability_receipts import fingerprint
    mutated = {**body, "artifact_id": fingerprint(body)}
    request = make_request(legs=[{
        "leg_id": "Ledge", "game_id": "G1", "market": "qb_pass_yds",
        "selection": "over", "baseline_prob": 0.005,
    }], parlays=[])
    response = plan_corrections(request, mode="on", artifact=mutated,
                                now="2026-11-02T00:00:00Z")
    row = response["legs"][0]
    assert row["applied"] is False
    assert row["adjusted_prob"] == 0.005
    assert row["reason"] == "fallback_baseline"


def test_parlay_product_formula_and_relative_cap():
    artifact = make_artifact()
    # Two legs with known applied deltas.
    mutated = copy.deepcopy(artifact)
    body = {k: v for k, v in mutated.items() if k != "artifact_id"}
    body["segments"] = {
        "qb_pass_yds|over": {
            "n_eligible": 100, "raw_bias": -0.05, "delta": -0.02,
            "eligible_for_application": True,
        },
        "rb_rush_yds|over": {
            "n_eligible": 100, "raw_bias": -0.05, "delta": -0.02,
            "eligible_for_application": True,
        },
    }
    body["n_graded"] = 200
    body["min_graded_total"] = 150
    body["min_graded_per_market"] = 50
    body["sufficient_total"] = True
    from selflearn_core.probability_receipts import fingerprint
    mutated = {**body, "artifact_id": fingerprint(body)}
    legs = [
        {"leg_id": "A", "game_id": "G1", "market": "qb_pass_yds",
         "selection": "over", "baseline_prob": 0.50},
        {"leg_id": "B", "game_id": "G1", "market": "rb_rush_yds",
         "selection": "over", "baseline_prob": 0.50},
    ]
    # Independent product baseline 0.25; product of ratios = 0.48/0.5 * 0.48/0.5.
    request = make_request(legs=legs, parlays=[
        {"parlay_id": "P", "leg_ids": ["A", "B"], "baseline_prob": 0.25},
    ])
    response = plan_corrections(request, mode="on", artifact=mutated,
                                now="2026-11-02T00:00:00Z")
    assert all(row["applied"] for row in response["legs"])
    expected = 0.25 * (0.48 / 0.50) * (0.48 / 0.50)
    bound = min(HARD_PARLAY_CAP_PP, 0.25 * HARD_PARLAY_CAP_REL)
    capped = 0.25 + math.copysign(min(abs(expected - 0.25), bound), expected - 0.25)
    parlay = response["parlays"][0]
    assert parlay["applied"] is True
    assert abs(parlay["adjusted_prob"] - capped) < 1e-12


def test_no_graded_data_yields_zero_adjustment():
    artifact = make_artifact(graded_records=[], min_graded_total=1, min_graded_per_market=1)
    assert artifact["n_graded"] == 0
    assert artifact["segments"] == {}
    response = plan_corrections(make_request(), mode="on", artifact=artifact,
                                now="2026-11-02T00:00:00Z")
    assert all(row["applied"] is False for row in response["legs"])


def test_request_id_and_baselines_echo_exactly():
    request = make_request(request_id="exact-id-42")
    request["legs"][0]["baseline_prob"] = 0.123456789
    response = plan_corrections(request, mode="off")
    assert response["request_id"] == "exact-id-42"
    assert response["legs"][0]["baseline_prob"] == 0.123456789
    assert response["legs"][0]["adjusted_prob"] == 0.123456789


def test_property_caps_and_fallbacks_hold_under_randomization():
    rng = random.Random(20261106)
    artifact = make_artifact()
    for _ in range(40):
        n_legs = rng.randint(1, 8)
        legs = []
        for i in range(n_legs):
            market = rng.choice(["qb_pass_yds", "rb_rush_yds", "wr_rec_yds", "unknown_mkt"])
            legs.append({
                "leg_id": f"L{i}",
                "game_id": f"G{i // 2}",
                "market": market,
                "selection": "over",
                "baseline_prob": rng.choice([0.005, 0.02, 0.15, 0.5, 0.85, 0.98, 0.995]),
            })
        if rng.random() < 0.2 and n_legs >= 2:
            legs.append(copy.deepcopy(legs[0]))  # duplicate id
        parlays = []
        if rng.random() < 0.8:
            ids = [leg["leg_id"] for leg in legs[: max(1, n_legs // 2)]]
            # Unique ids for parlay membership.
            uniq = list(dict.fromkeys(ids))
            if rng.random() < 0.3:
                uniq.append("missing")
            parlays.append({
                "parlay_id": "P0",
                "leg_ids": uniq,
                "baseline_prob": rng.choice([0.05, 0.2, 0.4, 0.7]),
            })
        mode = rng.choice(["off", "shadow", "on"])
        response = plan_corrections(make_request(legs=legs, parlays=parlays),
                                    mode=mode, artifact=artifact,
                                    now="2026-11-02T00:00:00Z")
        assert response["request_id"] == "req-1"
        for row in response["legs"]:
            assert row["cap_pp"] <= HARD_LEG_CAP_PP
            if row["applied"]:
                assert mode == "on"
                assert abs(row["delta"]) <= HARD_LEG_CAP_PP + 1e-12
                assert HARD_CLAMP[0] <= row["adjusted_prob"] <= HARD_CLAMP[1]
            else:
                # Exact baseline echo, including values outside the clamp.
                assert row["adjusted_prob"] == row["baseline_prob"]
                assert row["delta"] == 0.0
        for row in response["parlays"]:
            if row["applied"]:
                bound = min(HARD_PARLAY_CAP_PP, abs(row["baseline_prob"]) * HARD_PARLAY_CAP_REL)
                assert abs(row["delta"]) <= bound + 1e-12
                assert HARD_CLAMP[0] <= row["adjusted_prob"] <= HARD_CLAMP[1]
            else:
                assert row["adjusted_prob"] == row["baseline_prob"]


def test_invalid_request_raises():
    with pytest.raises(CorrectionsError):
        plan_corrections({"schema_version": 1})
