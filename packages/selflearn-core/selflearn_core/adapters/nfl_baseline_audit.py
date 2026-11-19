"""Read-only score coverage and calibration audit for attested NFL forecasts."""
from collections import Counter, defaultdict
from statistics import fmean

from ..probability_receipts import fingerprint, instant
from .nfl_calibration import _cluster_interval, _latest_as_of, _loss
from .nfl_corrections_fit import _revision_histories, _select_publication_cohort
from .nfl_corrections_policy import (
    DEFAULT_MIN_GRADED_PER_MARKET,
    DEFAULT_MIN_GRADED_TOTAL,
    CorrectionsError,
    FIRST_DURABLE_PUBLICATION,
    lineage_pair,
    segment_key,
)
from .nfl_label_qualification import label_exclusion_reason


def _summarize(forecasts):
    graded = [row for row in forecasts if row["exclusion"] is None]
    summary = {
        "forecast_count": len(forecasts),
        "graded_count": len(graded),
        "forecast_games": len({row["correlation_group"] for row in forecasts}),
        "graded_games": len({row["correlation_group"] for row in graded}),
        "graded_weeks": len({row["week"] for row in graded}),
        "exclusions": dict(Counter(row["exclusion"] for row in forecasts if row["exclusion"] is not None)),
    }
    for key in ("base_log_loss", "served_log_loss", "base_brier", "served_brier"):
        summary[key] = fmean(row[key] for row in graded) if graded else None
    summary["served_minus_base_log_loss"] = fmean(row["delta_loss"] for row in graded) if graded else None
    return summary


def audit_baseline(journal, *, as_of_utc, bootstrap_replicates=2000):
    """Score the raw and served probabilities against the same qualified results."""
    cutoff = instant(as_of_utc)
    snapshot = journal.read_publication_fit_snapshot()
    receipts, members = _select_publication_cohort(snapshot, as_of_utc, cohort_policy=FIRST_DURABLE_PUBLICATION)
    histories = _revision_histories(snapshot)
    rows, by_market, by_week = [], defaultdict(list), defaultdict(list)
    by_lineage = defaultdict(list)
    for rid, proof, source, _ in members:
        receipt = receipts[rid]
        market = source["market"]
        week = f'{source["season"]}:{source["week"]:02d}'
        segment = segment_key(market, source["direction"])
        revision_id = None
        try:
            head = _latest_as_of(receipt, histories[rid], cutoff)
            revision_id = head["revision_id"] if head else None
            exclusion = (label_exclusion_reason(receipt, source, histories[rid], head, cutoff)
                         if head else "unresolved_outcome")
        except (ValueError, TypeError, KeyError, OverflowError):
            exclusion = "invalid_outcome_history"
        try:
            lineage = lineage_pair(receipt)
        except CorrectionsError:
            lineage = (None, None)
        base, served = receipt["base_prob"], receipt["served_prob"]
        row = {
            "receipt_id": rid, "attestation_id": proof["attestation_id"], "revision_id": revision_id,
            "market": market, "week": week, "segment": segment,
            "correlation_group": receipt["event_id"], "exclusion": exclusion, "lineage": lineage,
            "base_and_served_differ": base != served,
            "fit_probability_interior": 0 < served < 1,
            "calibrator_configured": source.get("calibrator") is not None,
        }
        if exclusion is None:
            hit = head["resolution"]["hit"]
            base_loss, served_loss = _loss(base, hit), _loss(served, hit)
            row.update(base_log_loss=base_loss, served_log_loss=served_loss, delta_loss=served_loss - base_loss,
                       base_brier=(base - hit) ** 2, served_brier=(served - hit) ** 2)
        rows.append(row)
        by_market[market].append(row)
        by_week[week].append(row)
        by_lineage[lineage].append(row)

    graded = [row for row in rows if row["exclusion"] is None]
    fit_eligible = [row for row in graded if row["fit_probability_interior"]]
    graded_lineages = {row["lineage"] for row in fit_eligible}
    segment_counts = Counter(row["segment"] for row in fit_eligible)
    fit_reasons = []
    if len(fit_eligible) < DEFAULT_MIN_GRADED_TOTAL:
        fit_reasons.append("insufficient_graded_total")
    if (None, None) in graded_lineages or not graded_lineages:
        fit_reasons.append("missing_graded_baseline_lineage")
    if len(graded_lineages) > 1:
        fit_reasons.append("mixed_graded_baseline_lineage")
    segments = {key: {"graded_count": segment_counts[key],
                      "count_floor_met": segment_counts[key] >= DEFAULT_MIN_GRADED_PER_MARKET}
                for key in sorted({row["segment"] for row in rows})}
    if not any(segment["count_floor_met"] for segment in segments.values()):
        fit_reasons.append("no_segment_meets_graded_floor")

    game_interval = _cluster_interval(graded, bootstrap_replicates)
    if game_interval.get("scope"):
        game_interval["scope"] = "paired on identical attested forecasts and qualified outcomes"
    report = {
        "version": 1, "basis": "publicly_attested_baseline", "as_of_utc": as_of_utc,
        "training_cohort_policy": FIRST_DURABLE_PUBLICATION,
        "snapshot_watermark": snapshot["watermark"],
        "membership_id": fingerprint([[r["receipt_id"], r["attestation_id"], r["revision_id"]] for r in rows]),
        "total": _summarize(rows),
        "by_market": {key: _summarize(group) for key, group in sorted(by_market.items())},
        "by_week": {key: _summarize(group) for key, group in sorted(by_week.items())},
        "lineages": [
            {"base_model_version": pair[0], "calibrator_version": pair[1], **_summarize(group),
             "forecasts_with_different_base_and_served": sum(row["base_and_served_differ"] for row in group),
             "forecasts_with_calibrator": sum(row["calibrator_configured"] for row in group)}
            for pair, group in sorted(by_lineage.items(), key=lambda item: tuple(v or "" for v in item[0]))
        ],
        "served_minus_base_game_interval": game_interval,
        "correction_fit_support": {"minimum_graded_total": DEFAULT_MIN_GRADED_TOTAL,
                                   "minimum_graded_per_segment": DEFAULT_MIN_GRADED_PER_MARKET,
                                   "fit_eligible_graded_count": len(fit_eligible),
                                   "probability_boundary_count": len(graded) - len(fit_eligible),
                                   "segments": segments, "hold_reasons": fit_reasons},
        "adoption_approved": False,
    }
    return {**report, "report_id": fingerprint(report)}
