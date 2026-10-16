"""Descriptive binary receipt scores with explicit dependence and exclusions.

No Wilson interval on overlapping cards: correlation groups are reported, not
assumed independent. This module neither fits nor promotes any correction.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math

from ..probability_receipts import instant, validate_probability_receipt, validate_resolution

EPS = 1e-6  # documented NFL scalar-score parity; does not change stored p
COHORT_POLICY = "first_valid_forecast_before_outcome_join_v2"


def score_binary_receipts(receipts, outcomes):
    """Score each canonical contract once, using its earliest valid receipt.

    Freeze the forecast population BEFORE looking for outcomes. An unresolved
    first forecast cannot be replaced by a later, more conveniently graded
    vintage. Conflicting outcomes for the selected receipt or simultaneous
    distinct first forecasts are quarantined, never resolved by list order.
    Later-vintage labels cannot change the selected forecast or its score.

    This is a descriptive score of the caller's explicit outcome snapshot,
    not a revision/as-of selector. Supply pinned latest resolutions, not an
    unfiltered correction history. Receipt IDs must match exactly.
    """
    reasons = Counter()
    indexed = defaultdict(list)
    for out in outcomes or []:
        if isinstance(out, dict) and isinstance(out.get("receipt_id"), str):
            indexed[out["receipt_id"]].append(out)
        else:
            reasons["invalid_outcome"] += 1
    candidates = defaultdict(list)
    for receipt in receipts or []:
        try:
            validate_probability_receipt(receipt)
        except (ValueError, TypeError, OverflowError) as exc:
            reasons[str(exc)] += 1
            continue
        key = (receipt["domain"], receipt["task"], receipt["contract_id"])
        candidates[key].append(receipt)
    rows, duplicates = [], 0
    for key in sorted(candidates):
        versions = candidates[key]
        versions.sort(key=lambda r: (instant(r["forecast_created_utc"]), r["receipt_id"]))
        first = instant(versions[0]["forecast_created_utc"])
        earliest = [r for r in versions if instant(r["forecast_created_utc"]) == first]
        if len({(r["base_model_version"], r["calibrator_version"], r["base_prob"], r["served_prob"],
                 r["event_id"], r["correlation_group"]) for r in earliest}) != 1:
            reasons["conflicting_first_forecast"] += len(versions)
            continue
        receipt = versions[0]
        duplicates += len(versions) - 1
        matching = indexed.get(receipt["receipt_id"], [])
        if not matching:
            reasons["unresolved"] += 1
            continue
        valid = []
        for out in matching:
            try:
                validate_resolution(receipt, out)
            except (ValueError, TypeError, OverflowError) as exc:
                reasons[str(exc)] += 1
                continue
            valid.append(out)
        if not valid:
            continue
        if len({out["hit"] for out in valid}) != 1:
            reasons["conflicting_outcomes"] += len(valid)
            continue
        out = valid[0]
        p, y = receipt["served_prob"], int(out["hit"])
        q = min(max(p, EPS), 1 - EPS)
        rows.append({"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
                     "task": receipt["task"], "domain": receipt["domain"],
                     "event_id": receipt["event_id"], "correlation_group": receipt["correlation_group"],
                     "served_prob": p, "hit": bool(y), "brier": (p - y) ** 2,
                     "log_loss": -math.log(q if y else 1 - q),
                     "base_model_version": receipt["base_model_version"],
                     "calibrator_version": receipt["calibrator_version"],
                     "publication_status": receipt["publication_status"]})
    n = len(rows)
    groups = Counter((r["domain"], r["correlation_group"]) for r in rows)
    return {"version": 1, "cohort_policy": COHORT_POLICY,
            "outcome_policy": "caller-pinned exact selected-receipt resolutions; no implicit revision selection",
            "canonical_contracts_considered": len(candidates),
            "n": n, "hits": sum(r["hit"] for r in rows),
            "hit_rate": sum(r["hit"] for r in rows) / n if n else None,
            "mean_served_prob": sum(r["served_prob"] for r in rows) / n if n else None,
            "expected_hits": sum(r["served_prob"] for r in rows) if n else None,
            "log_loss": sum(r["log_loss"] for r in rows) / n if n else None,
            "brier": sum(r["brier"] for r in rows) / n if n else None,
            "unique_events": len({(r["domain"], r["event_id"]) for r in rows}),
            "correlation_groups": len(groups), "largest_group": max(groups.values(), default=0),
            "independent_sample_size": None, "ci_low": None, "ci_high": None,
            "interval_policy": "not estimated: events/groups may be dependent; no independent-card CI",
            "duplicate_or_later_copies": duplicates, "excluded": dict(sorted(reasons.items())),
            "log_loss_clip": EPS, "rows": rows}
