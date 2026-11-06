"""NFL v1 learned-corrections planner. Fail closed to the submitted baseline.

Method: per-market additive probability bias with empirical-Bayes shrinkage
toward zero (see ``nfl_corrections_fit``). Segments below the per-market floor
get no correction; a global pooled correction is never inherited.
"""
from __future__ import annotations

import logging
import math

from datetime import datetime, timezone

from ..probability_receipts import fingerprint, finite_number, instant
from .nfl_corrections_fit import fit_corrections_artifact
from .nfl_corrections_policy import (
    ARTIFACT_PUBLIC_KEYS,
    CorrectionsError,
    HARD_CLAMP,
    HARD_LEG_CAP_PP,
    HARD_PARLAY_CAP_PP,
    HARD_PARLAY_CAP_REL,
    KIND,
    SCHEMA_VERSION,
    _cap_pp,
    _text,
    corrections_mode,
)
from .nfl_corrections_request import validate_request

LOG = logging.getLogger(__name__)

__all__ = [
    "CorrectionsError",
    "corrections_mode",
    "fit_corrections_artifact",
    "plan_corrections",
    "public_artifact",
    "validate_artifact",
    "validate_request",
]


def validate_artifact(artifact):
    if not isinstance(artifact, dict) or artifact.get("kind") != KIND:
        raise CorrectionsError("invalid_artifact")
    if type(artifact.get("version")) is not int or artifact["version"] != 1:
        raise CorrectionsError("invalid_artifact")
    for key in ("artifact_id", "model_version", "method", "trained_through",
                "fitted_at", "expires_at", "source", "fit_inputs_hash"):
        _text(artifact.get(key), name=key)
    body = {k: v for k, v in artifact.items() if k != "artifact_id"}
    if fingerprint(body) != artifact["artifact_id"]:
        raise CorrectionsError("artifact_hash_mismatch")
    if type(artifact.get("n_graded")) is not int or artifact["n_graded"] < 0:
        raise CorrectionsError("invalid_artifact_n_graded")
    _cap_pp(artifact.get("leg_cap_pp"), hard=HARD_LEG_CAP_PP)
    if (artifact.get("parlay_cap_pp") != HARD_PARLAY_CAP_PP
            or artifact.get("parlay_cap_rel") != HARD_PARLAY_CAP_REL
            or artifact.get("clamp") != list(HARD_CLAMP)
            or type(artifact.get("segments")) is not dict
            or type(artifact.get("sufficient_total")) is not bool
            or type(artifact.get("min_graded_total")) is not int
            or type(artifact.get("min_graded_per_market")) is not int
            or type(artifact.get("eligibility_rules")) is not list):
        raise CorrectionsError("invalid_artifact_policy")
    for market, row in artifact["segments"].items():
        _text(market, name="market", maximum=100)
        if (not isinstance(row, dict) or type(row.get("n_eligible")) is not int
                or row["n_eligible"] < 0 or not finite_number(row.get("delta"))
                or not finite_number(row.get("raw_bias"))
                or type(row.get("eligible_for_application")) is not bool):
            raise CorrectionsError("invalid_segment")
    instant(artifact["trained_through"])
    instant(artifact["fitted_at"])
    instant(artifact["expires_at"])
    return artifact


def public_artifact(artifact):
    if artifact is None:
        return None
    validate_artifact(artifact)
    return {key: artifact[key] for key in ARTIFACT_PUBLIC_KEYS}


def _echo_leg(leg, *, reason, cap_pp):
    baseline = leg["baseline_prob"]
    return {
        "leg_id": leg["leg_id"],
        "baseline_prob": baseline,
        "adjusted_prob": baseline,
        "delta": 0.0,
        "applied": False,
        "reason": reason,
        "cap_pp": cap_pp,
    }


def _echo_parlay(parlay, *, reason, cap_pp):
    baseline = parlay["baseline_prob"]
    return {
        "parlay_id": parlay["parlay_id"],
        "baseline_prob": baseline,
        "adjusted_prob": baseline,
        "delta": 0.0,
        "applied": False,
        "reason": reason,
        "cap_pp": cap_pp,
    }


def _apply_leg_delta(baseline, delta, *, cap_pp):
    if not math.isfinite(delta) or not math.isfinite(baseline):
        raise CorrectionsError("non_finite_adjustment")
    capped = max(-cap_pp, min(cap_pp, float(delta)))
    if abs(capped) > HARD_LEG_CAP_PP + 1e-15:
        raise CorrectionsError("cap_violation")
    tentative = baseline + capped
    low, high = HARD_CLAMP
    adjusted = min(max(tentative, low), high)
    applied_delta = adjusted - baseline
    if abs(applied_delta) > cap_pp + 1e-15:
        raise CorrectionsError("clamp_exceeds_cap")
    if capped != 0.0 and applied_delta * capped <= 0.0:
        raise CorrectionsError("clamp_flips_direction")
    if not math.isfinite(adjusted):
        raise CorrectionsError("invalid_adjusted")
    return adjusted, applied_delta


def _leg_result(leg, *, mode, artifact, now, cap_pp):
    if mode == "off":
        return _echo_leg(leg, reason="corrections_off", cap_pp=cap_pp)
    if leg["duplicate"]:
        return _echo_leg(leg, reason="duplicate_leg_id", cap_pp=cap_pp)
    if artifact is None:
        return _echo_leg(leg, reason="missing_artifact", cap_pp=cap_pp)
    try:
        if instant(now) > instant(artifact["expires_at"]):
            return _echo_leg(leg, reason="stale_artifact", cap_pp=cap_pp)
        if (not artifact.get("sufficient_total")
                or artifact["n_graded"] < artifact["min_graded_total"]):
            return _echo_leg(leg, reason="insufficient_graded_data", cap_pp=cap_pp)
        segment = artifact["segments"].get(leg["market"])
        if segment is None or not segment.get("eligible_for_application"):
            return _echo_leg(leg, reason="insufficient_segment_data", cap_pp=cap_pp)
        delta = segment["delta"]
        if not finite_number(delta) or delta == 0.0:
            return _echo_leg(leg, reason="zero_correction", cap_pp=cap_pp)
        adjusted, applied_delta = _apply_leg_delta(leg["baseline_prob"], delta, cap_pp=cap_pp)
    except (CorrectionsError, ValueError, TypeError, KeyError, OverflowError):
        return _echo_leg(leg, reason="fallback_baseline", cap_pp=cap_pp)
    if mode == "shadow":
        LOG.info("corrections_shadow leg_id=%s market=%s baseline=%s would_adjusted=%s delta=%s",
                 leg["leg_id"], leg["market"], leg["baseline_prob"], adjusted, applied_delta)
        return _echo_leg(leg, reason="shadow_baseline", cap_pp=cap_pp)
    return {
        "leg_id": leg["leg_id"],
        "baseline_prob": leg["baseline_prob"],
        "adjusted_prob": adjusted,
        "delta": applied_delta,
        "applied": True,
        "reason": "learned_segment_bias",
        "cap_pp": cap_pp,
    }


def _parlay_result(parlay, *, legs_by_id, mode, cap_pp):
    if mode == "off":
        return _echo_parlay(parlay, reason="corrections_off", cap_pp=cap_pp)
    baseline = parlay["baseline_prob"]
    reasons, ratios = [], []
    for leg_id in parlay["leg_ids"]:
        row = legs_by_id.get(leg_id)
        if row is None:
            reasons.append("missing_leg_id")
            continue
        if not row["applied"]:
            reasons.append(row["reason"])
            continue
        ratios.append(row["adjusted_prob"] / row["baseline_prob"])
    if mode == "shadow":
        if not reasons and len(ratios) == len(parlay["leg_ids"]):
            product = 1.0
            for ratio in ratios:
                product *= ratio
            LOG.info("corrections_shadow parlay_id=%s baseline=%s would_adjusted=%s",
                     parlay["parlay_id"], baseline, baseline * product)
        return _echo_parlay(parlay, reason="shadow_baseline", cap_pp=cap_pp)
    if reasons or len(ratios) != len(parlay["leg_ids"]):
        return _echo_parlay(parlay, reason=reasons[0] if reasons else "parlay_legs_not_applied",
                            cap_pp=cap_pp)
    try:
        product = 1.0
        for ratio in ratios:
            if not math.isfinite(ratio) or ratio <= 0.0:
                raise CorrectionsError("invalid_ratio")
            product *= ratio
        tentative = baseline * product
        bound = min(cap_pp, abs(baseline) * HARD_PARLAY_CAP_REL)
        delta = tentative - baseline
        if abs(delta) > bound:
            delta = math.copysign(bound, delta)
        adjusted = baseline + delta
        low, high = HARD_CLAMP
        clamped = min(max(adjusted, low), high)
        applied_delta = clamped - baseline
        if abs(applied_delta) > bound + 1e-15:
            raise CorrectionsError("parlay_clamp_exceeds_cap")
        if delta != 0.0 and applied_delta * delta <= 0.0:
            raise CorrectionsError("parlay_clamp_flips_direction")
        if not math.isfinite(clamped):
            raise CorrectionsError("non_finite_parlay")
    except (CorrectionsError, ValueError, OverflowError):
        return _echo_parlay(parlay, reason="fallback_baseline", cap_pp=cap_pp)
    return {
        "parlay_id": parlay["parlay_id"],
        "baseline_prob": baseline,
        "adjusted_prob": clamped,
        "delta": applied_delta,
        "applied": True,
        "reason": "learned_parlay_product",
        "cap_pp": cap_pp,
    }


def plan_corrections(request, *, mode="off", artifact=None, now=None):
    """Plan corrections. Invalid requests raise; serving faults echo baseline."""
    mode = corrections_mode(mode)
    parsed = validate_request(request)
    now = now or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if mode == "off":
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": parsed["request_id"],
            "corrections_mode": "off",
            "artifact": None,
            "legs": [_echo_leg(leg, reason="corrections_off", cap_pp=HARD_LEG_CAP_PP)
                     for leg in parsed["legs"]],
            "parlays": [_echo_parlay(row, reason="corrections_off", cap_pp=HARD_PARLAY_CAP_PP)
                        for row in parsed["parlays"]],
        }
    try:
        if artifact is not None:
            validate_artifact(artifact)
        leg_cap = HARD_LEG_CAP_PP if artifact is None else _cap_pp(
            artifact["leg_cap_pp"], hard=HARD_LEG_CAP_PP)
        legs = [_leg_result(leg, mode=mode, artifact=artifact, now=now, cap_pp=leg_cap)
                for leg in parsed["legs"]]
        by_id = {}
        for row in legs:
            by_id.setdefault(row["leg_id"], row)
        parlays = [_parlay_result(row, legs_by_id=by_id, mode=mode, cap_pp=HARD_PARLAY_CAP_PP)
                   for row in parsed["parlays"]]
        try:
            art = public_artifact(artifact)
        except CorrectionsError:
            art = None
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": parsed["request_id"],
            "corrections_mode": mode,
            "artifact": art,
            "legs": legs,
            "parlays": parlays,
        }
    except Exception:
        LOG.exception("corrections_plan_fallback")
        return {
            "schema_version": SCHEMA_VERSION,
            "request_id": parsed["request_id"],
            "corrections_mode": mode,
            "artifact": None,
            "legs": [_echo_leg(leg, reason="fallback_baseline", cap_pp=HARD_LEG_CAP_PP)
                     for leg in parsed["legs"]],
            "parlays": [_echo_parlay(row, reason="fallback_baseline", cap_pp=HARD_PARLAY_CAP_PP)
                        for row in parsed["parlays"]],
        }
