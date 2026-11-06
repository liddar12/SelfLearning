"""Adversarial regressions for Beta HOLD findings (pin 37629ec → repaired).

Assert REQUIRED secure/contract behavior. Offline only; no network/DB.
"""
from __future__ import annotations

import pytest

from selflearn_core.adapters.nfl_corrections import (
    fit_corrections_artifact,
    plan_corrections,
    public_artifact,
    validate_artifact,
)
from selflearn_core.adapters.nfl_corrections_policy import ARTIFACT_PUBLIC_KEYS, ELIGIBILITY_RULES
from selflearn_core.probability_receipts import fingerprint


def _stamp(day=1, hour=12):
    return f"2026-10-{day:02d}T{hour:02d}:00:00Z"


def graded(market="qb_pass_yds", baseline=0.60, hit=True, day=1, status=None,
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


def honest_rows(n_per=60):
    rows = []
    for market in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"):
        for i in range(n_per):
            rows.append(graded(market=market, baseline=0.60, hit=(i % 10) < 4, day=1 + (i % 20)))
    return rows


def seal(body):
    return {**body, "artifact_id": fingerprint(body)}


def handcraft(**segment_overrides):
    segments = {
        "qb_pass_yds|over": {
            "n_eligible": 1, "raw_bias": 0.03, "delta": 0.03,
            "eligible_for_application": True,
        },
        "rb_rush_yds|over": {
            "n_eligible": 100, "raw_bias": 0.0, "delta": 0.0,
            "eligible_for_application": True,
        },
        "wr_rec_yds|over": {
            "n_eligible": 99, "raw_bias": 0.0, "delta": 0.0,
            "eligible_for_application": True,
        },
    }
    segments.update(segment_overrides)
    body = {
        "version": 1,
        "kind": "nfl.corrections_artifact.v1",
        "model_version": "corrections_v1",
        "method": "market_bias_shrinkage_v1",
        "trained_through": "2026-11-01T00:00:00Z",
        "fitted_at": "2026-11-01T01:00:00Z",
        "expires_at": "2026-12-01T00:00:00Z",
        "n_graded": sum(s["n_eligible"] for s in segments.values()),
        "source": "adversarial_handcraft",
        "prior_strength": 50.0,
        "min_graded_total": 200,
        "min_graded_per_market": 50,
        "leg_cap_pp": 0.03,
        "parlay_cap_pp": 0.03,
        "parlay_cap_rel": 0.15,
        "clamp": [0.01, 0.99],
        "eligibility_rules": list(ELIGIBILITY_RULES),
        "fit_inputs_hash": "sha256:" + ("0" * 64),
        "segments": segments,
        "sufficient_total": True,
    }
    return seal(body)


def request_leg(market="qb_pass_yds", selection="over", leg_id="L1", p=0.55):
    return {
        "schema_version": 1,
        "request_id": "adv-1",
        "season": 2026,
        "week": 9,
        "baseline_model_version": "sha256:" + ("a" * 64),
        "generated_at": "2026-11-06T12:00:00Z",
        "legs": [{
            "leg_id": leg_id, "game_id": "G1", "market": market,
            "selection": selection, "baseline_prob": p,
        }],
        "parlays": [],
    }


def test_hold_thin_segment_n_eligible_1_rejected():
    with pytest.raises(Exception):
        validate_artifact(handcraft())


def test_hold_empty_eligibility_rules_rejected():
    art = fit_corrections_artifact(
        honest_rows(), trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    body = {k: v for k, v in art.items() if k != "artifact_id"}
    body["eligibility_rules"] = []
    with pytest.raises(Exception):
        validate_artifact(seal(body))


def test_hold_fake_eligibility_rules_rejected():
    art = fit_corrections_artifact(
        honest_rows(), trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    body = {k: v for k, v in art.items() if k != "artifact_id"}
    body["eligibility_rules"] = ["totally_invented_rule", "also_fake"]
    with pytest.raises(Exception):
        validate_artifact(seal(body))


def test_hold_fitted_before_trained_through_rejected():
    with pytest.raises(Exception):
        fit_corrections_artifact(
            honest_rows(), trained_through="2026-11-01T00:00:00Z",
            expires_at="2026-12-01T00:00:00Z", fitted_at="2020-01-01T00:00:00Z",
            min_graded_total=100, min_graded_per_market=50)


def test_hold_fitted_after_expires_rejected():
    with pytest.raises(Exception):
        fit_corrections_artifact(
            honest_rows(), trained_through="2026-11-01T00:00:00Z",
            expires_at="2026-12-01T00:00:00Z", fitted_at="2027-01-01T00:00:00Z",
            min_graded_total=100, min_graded_per_market=50)


@pytest.mark.parametrize("status", ["Push", "Void", "pUsH", "vOiD"])
def test_hold_mixed_case_push_void_excluded(status):
    rows = honest_rows(60)
    rows.append(graded(status=status, hit=True, day=5))
    art = fit_corrections_artifact(
        rows, trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    assert art["n_graded"] == 180


def test_hold_over_delta_does_not_cross_apply_to_under():
    rows = []
    for i in range(60):
        rows.append(graded(market="qb_pass_yds", selection="over", baseline=0.60,
                           hit=False, day=1 + (i % 20)))
        rows.append(graded(market="rb_rush_yds", selection="over", baseline=0.55,
                           hit=True, day=1 + (i % 20)))
        rows.append(graded(market="wr_rec_yds", selection="over", baseline=0.55,
                           hit=True, day=1 + (i % 20)))
    art = fit_corrections_artifact(
        rows, trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    under_req = request_leg(market="qb_pass_yds", selection="under", p=0.55)
    response = plan_corrections(under_req, mode="on", artifact=art,
                                now="2026-11-02T00:00:00Z")
    row = response["legs"][0]
    assert row["applied"] is False
    assert row["adjusted_prob"] == 0.55


def test_hold_public_artifact_includes_provenance():
    art = fit_corrections_artifact(
        honest_rows(), trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    pub = public_artifact(art)
    for key in ("segments", "fit_inputs_hash", "eligibility_rules",
                "prior_strength", "min_graded_total", "min_graded_per_market", "leg_cap_pp"):
        assert key in pub
    assert set(ARTIFACT_PUBLIC_KEYS) <= set(pub)


def test_hold_void_status_excludes_even_with_hit_true():
    rows = honest_rows(60)
    liar = graded(hit=True, day=3, status="VOID")
    liar["revision_id"] = "rev-void-1"
    rows.append(liar)
    art = fit_corrections_artifact(
        rows, trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    assert art["n_graded"] == 180
