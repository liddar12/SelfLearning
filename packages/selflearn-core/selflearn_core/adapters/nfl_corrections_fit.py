"""Offline fit for NFL v1 corrections artifacts. No serving side effects.

Caller-supplied grade lists remain integrity-hashed only — not authenticated
provenance. Digests ≠ signatures. Runtime ``applied=true`` requires
``source=receipt_journal_as_of_v1`` from ``fit_corrections_from_journal``.
``generated_unverified`` capture rows are never fit-eligible.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, timezone

from ..probability_receipts import fingerprint, finite_number, instant, validate_probability_receipt
from .nfl_corrections_policy import (
    CorrectionsError,
    DEFAULT_MIN_GRADED_PER_MARKET,
    DEFAULT_MIN_GRADED_TOTAL,
    DEFAULT_PRIOR_STRENGTH,
    ELIGIBILITY_RULES,
    HARD_CLAMP,
    HARD_LEG_CAP_PP,
    HARD_PARLAY_CAP_PP,
    HARD_PARLAY_CAP_REL,
    KIND,
    METHOD,
    MODEL_VERSION,
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
    _cap_pp,
    _prob,
    _text,
    is_void_status,
    segment_key,
)


def _eligible_record(row, *, trained_through):
    if not isinstance(row, dict):
        return None
    try:
        market = _text(row.get("market"), name="market", maximum=100)
        selection = _text(row.get("selection"), name="selection", maximum=100)
        baseline = _prob(row.get("baseline_prob"))
        served_at = instant(row.get("served_at"))
        kickoff_at = instant(row.get("kickoff_at"))
        graded_at = instant(row.get("graded_at"))
        cutoff = instant(trained_through)
        outcome = row.get("outcome")
        status = row.get("status")
    except (CorrectionsError, ValueError, TypeError):
        return None
    if is_void_status(status):
        return None
    if type(outcome) is not bool:
        return None
    if not (served_at < kickoff_at and graded_at <= cutoff):
        return None
    return {
        "market": market,
        "selection": selection,
        "segment": segment_key(market, selection),
        "baseline_prob": baseline,
        "outcome": outcome,
        "served_at": row["served_at"],
        "kickoff_at": row["kickoff_at"],
        "graded_at": row["graded_at"],
    }


def require_clock_order(*, trained_through, fitted_at, expires_at):
    trained = instant(trained_through)
    fitted = instant(fitted_at)
    expires = instant(expires_at)
    if not (trained <= fitted <= expires):
        raise CorrectionsError("invalid_artifact_clock_order")


def fit_corrections_artifact(graded_records, *, trained_through, expires_at,
                             fitted_at=None, source=UNTRUSTED_ARTIFACT_SOURCE,
                             prior_strength=DEFAULT_PRIOR_STRENGTH,
                             min_graded_total=DEFAULT_MIN_GRADED_TOTAL,
                             min_graded_per_market=DEFAULT_MIN_GRADED_PER_MARKET,
                             leg_cap_pp=HARD_LEG_CAP_PP):
    """Build a content-addressed corrections artifact from graded records.

    Storage without a migration: persist body (sans nested artifact_id) as an
    existing journal ``source_snapshot`` and pin with
    ``SELFLEARN_CORRECTIONS_ARTIFACT_ID``. Digests detect mutation only.
    Default ``source`` is untrusted and cannot yield runtime ``applied=true``.
    """
    if not isinstance(graded_records, list):
        raise CorrectionsError("invalid_graded_records")
    if not finite_number(prior_strength) or prior_strength <= 0:
        raise CorrectionsError("invalid_prior_strength")
    if type(min_graded_total) is not int or min_graded_total < 1:
        raise CorrectionsError("invalid_min_graded_total")
    if type(min_graded_per_market) is not int or min_graded_per_market < 1:
        raise CorrectionsError("invalid_min_graded_per_market")
    leg_cap_pp = _cap_pp(leg_cap_pp, hard=HARD_LEG_CAP_PP)
    fitted = fitted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    require_clock_order(trained_through=trained_through, fitted_at=fitted, expires_at=expires_at)
    eligible = []
    for row in graded_records:
        item = _eligible_record(row, trained_through=trained_through)
        if item is not None:
            eligible.append(item)
    by_segment = defaultdict(list)
    for row in eligible:
        by_segment[row["segment"]].append(row["outcome"] - row["baseline_prob"])
    segments = {}
    for key, residuals in sorted(by_segment.items()):
        n = len(residuals)
        raw = sum(residuals) / n
        delta = raw * (n / (n + float(prior_strength)))
        if abs(delta) > leg_cap_pp:
            delta = math.copysign(leg_cap_pp, delta)
        if not math.isfinite(delta):
            delta = 0.0
        segments[key] = {
            "n_eligible": n,
            "raw_bias": round(raw, 8),
            "delta": round(delta, 8),
            "eligible_for_application": n >= min_graded_per_market,
        }
    fit_inputs = {
        "trained_through": trained_through,
        "eligibility_rules": list(ELIGIBILITY_RULES),
        "eligible": [{k: r[k] for k in ("market", "selection", "baseline_prob", "outcome",
                                        "served_at", "kickoff_at", "graded_at")}
                     for r in eligible],
        "method": METHOD,
        "prior_strength": float(prior_strength),
        "min_graded_total": min_graded_total,
        "min_graded_per_market": min_graded_per_market,
        "leg_cap_pp": leg_cap_pp,
    }
    body = {
        "version": 1,
        "kind": KIND,
        "model_version": MODEL_VERSION,
        "method": METHOD,
        "trained_through": trained_through,
        "fitted_at": fitted,
        "expires_at": expires_at,
        "n_graded": len(eligible),
        "source": _text(source, name="source", maximum=120),
        "prior_strength": float(prior_strength),
        "min_graded_total": min_graded_total,
        "min_graded_per_market": min_graded_per_market,
        "leg_cap_pp": leg_cap_pp,
        "parlay_cap_pp": HARD_PARLAY_CAP_PP,
        "parlay_cap_rel": HARD_PARLAY_CAP_REL,
        "clamp": list(HARD_CLAMP),
        "eligibility_rules": list(ELIGIBILITY_RULES),
        "fit_inputs_hash": fingerprint(fit_inputs),
        "segments": segments,
        "sufficient_total": len(eligible) >= min_graded_total,
    }
    return {**body, "artifact_id": fingerprint(body)}


def fit_corrections_from_journal(journal, *, trained_through, expires_at, fitted_at=None,
                                 prior_strength=DEFAULT_PRIOR_STRENGTH,
                                 min_graded_total=DEFAULT_MIN_GRADED_TOTAL,
                                 min_graded_per_market=DEFAULT_MIN_GRADED_PER_MARKET,
                                 leg_cap_pp=HARD_LEG_CAP_PP):
    """Fit from immutable journal receipts + authoritative revisions as-of cutoff.

    Uses existing ``nfl_calibration._latest_as_of`` chain rules (recorded_utc <
    trained_through; supersession; no future corrections). Only
    ``publication_status=verified_publication`` receipts are considered;
    ``generated_unverified`` M1 capture rows are explicitly excluded. Missing
    market/selection on features skips the row (no invention). Empty eligible
    sets still seal an untrusted-ineligible artifact with trusted source label
    and n_graded=0 (runtime floors refuse application).
    """
    from .nfl_calibration import _latest_as_of

    cutoff = instant(trained_through)
    graded = []
    for receipt in journal.receipts():
        try:
            validate_probability_receipt(receipt)
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if receipt.get("publication_status") != "verified_publication":
            continue
        features = receipt.get("features")
        if not isinstance(features, dict):
            continue
        market = features.get("market")
        selection = features.get("selection") or features.get("direction")
        if not isinstance(market, str) or not isinstance(selection, str):
            continue
        try:
            head = _latest_as_of(receipt, journal.resolution_history(receipt["receipt_id"]), cutoff)
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if head is None:
            continue
        resolution = head.get("resolution")
        if (not isinstance(resolution, dict) or resolution.get("status") == "VOID"
                or type(resolution.get("hit")) is not bool):
            continue
        baseline = receipt.get("served_prob")
        if not finite_number(baseline) or not 0.0 < float(baseline) < 1.0:
            continue
        graded.append({
            "market": market,
            "selection": selection,
            "baseline_prob": float(baseline),
            "served_at": receipt["first_seen_utc"],
            "kickoff_at": receipt["event_start_utc"],
            "graded_at": resolution["resolved_utc"],
            "outcome": resolution["hit"],
            "receipt_id": receipt["receipt_id"],
            "revision_id": head.get("revision_id"),
        })
    return fit_corrections_artifact(
        graded,
        trained_through=trained_through,
        expires_at=expires_at,
        fitted_at=fitted_at,
        source=TRUSTED_ARTIFACT_SOURCE,
        prior_strength=prior_strength,
        min_graded_total=min_graded_total,
        min_graded_per_market=min_graded_per_market,
        leg_cap_pp=leg_cap_pp,
    )
