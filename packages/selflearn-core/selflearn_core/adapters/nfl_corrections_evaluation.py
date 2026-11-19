"""Read-only retrospective evaluation of the NFL additive correction kernel."""
from collections import Counter

from ..probability_receipts import fingerprint, instant
from .nfl_calibration import _cluster_interval, _latest_as_of, _loss, _metrics
from .nfl_corrections import _leg_result, validate_artifact
from .nfl_corrections_fit import _authorize_snapshot, _revision_histories, _select_publication_cohort
from .nfl_corrections_policy import CorrectionsError, LINEAGE_VERSION, lineage_pair
from .nfl_label_qualification import label_exclusion_reason

POSITIONS = {"qb_pass_yds": "QB", "rb_rush_yds": "RB", "wr_rec_yds": "WR"}


def _membership(snapshot, artifact, start_utc, end_utc):
    start, end = instant(start_utc), instant(end_utc)
    if start >= end or instant(artifact["fitted_at"]) >= start:
        raise CorrectionsError("holdout_must_follow_fit")
    training_receipts, training = _select_publication_cohort(snapshot, artifact["trained_through"])
    training_events = {training_receipts[rid]["event_id"] for rid, *_ in training}
    receipts, selected = _select_publication_cohort(snapshot, end_utc)
    members, sources = [], {}
    for rid, proof, source, completed in selected:
        receipt = receipts[rid]
        if not start <= instant(receipt["forecast_created_utc"]) < end:
            continue
        if source["market"] not in POSITIONS:
            continue
        if receipt["event_id"] in training_events:
            raise CorrectionsError("training_holdout_event_overlap")
        sources[rid] = source
        members.append({
            "receipt_id": rid, "source_receipt_id": source["receipt_id"],
            "event_id": receipt["event_id"], "attestation_id": proof["attestation_id"],
            "generation_object_id": proof["object_id"],
            "season": source["season"], "week": source["week"],
            "publication_observed_utc": completed,
        })
    return receipts, sources, members


def _seal_membership(artifact, start_utc, end_utc, members):
    body = {"version": 1, "artifact_id": artifact["artifact_id"],
            "start_utc": start_utc, "end_utc": end_utc, "members": members}
    return {**body, "holdout_id": fingerprint(body)}


def freeze_correction_holdout(journal, artifact, *, start_utc, end_utc):
    """Freeze the first attested forecasts without using their outcome labels."""
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    _, _, members = _membership(snapshot, artifact, start_utc, end_utc)
    return _seal_membership(artifact, start_utc, end_utc, members)


def _verify_artifact(snapshot, artifact):
    validate_artifact(artifact)
    if artifact["version"] != LINEAGE_VERSION:
        raise CorrectionsError("lineage_artifact_required")
    _authorize_snapshot(snapshot, artifact)


def evaluate_correction_holdout(journal, artifact, holdout, *, as_of_utc, bootstrap_replicates=2000):
    """Evaluate one fixed fit. The report carries no serving or adoption authority."""
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    cutoff = instant(as_of_utc)
    start, end = holdout["start_utc"], holdout["end_utc"]
    if cutoff < instant(end):
        raise CorrectionsError("outcome_cutoff_precedes_holdout_end")
    receipts, sources, members = _membership(snapshot, artifact, start, end)
    if holdout != _seal_membership(artifact, start, end, members):
        raise CorrectionsError("frozen_holdout_membership_mismatch")

    histories = _revision_histories(snapshot)
    scored, results = [], []
    for member in members:
        rid = member["receipt_id"]
        receipt = receipts[rid]
        source = sources[rid]
        market = source["market"]
        position = POSITIONS[market]
        week = f'{member["season"]}:{member["week"]:02d}'
        try:
            head = _latest_as_of(receipt, histories[rid], cutoff)
            exclusion = (label_exclusion_reason(receipt, source, histories[rid], head, cutoff)
                         if head else "unresolved_outcome")
        except (ValueError, TypeError, KeyError, OverflowError):
            head, exclusion = None, "invalid_outcome_history"
        row = {**member, "position": position, "week_group": week,
               "revision_id": head["revision_id"] if head else None, "exclusion": exclusion}
        results.append(row)
        if exclusion is not None:
            continue

        baseline = receipt["served_prob"]
        lineage_hold = None
        try:
            if lineage_pair(receipt) != lineage_pair(artifact["lineage"]):
                lineage_hold = "incompatible_baseline_lineage"
        except CorrectionsError:
            lineage_hold = "missing_baseline_lineage"
        leg = {"leg_id": rid, "duplicate": False, "market": market, "selection": source["direction"],
               "baseline_prob": baseline}
        prediction = _leg_result(leg, mode="on", artifact=artifact,
                                 now=receipt["forecast_created_utc"], cap_pp=artifact["leg_cap_pp"],
                                 binder_verified=True, lineage_hold=lineage_hold)
        candidate, hit = prediction["adjusted_prob"], head["resolution"]["hit"]
        incumbent_loss, candidate_loss = _loss(baseline, hit), _loss(candidate, hit)
        row.update(served_prob=baseline, candidate_prob=candidate, hit=hit, correction=prediction["reason"])
        scored.append({**row, "incumbent_loss": incumbent_loss, "candidate_loss": candidate_loss,
                       "base_loss": _loss(receipt["base_prob"], hit),
                       "delta_loss": candidate_loss - incumbent_loss,
                       "correlation_group": receipt["event_id"]})

    week_interval = _cluster_interval(
        [{**row, "correlation_group": row["week_group"]} for row in scored], bootstrap_replicates)
    if "method" in week_interval:
        week_interval["method"] = week_interval["method"].replace("by game", "by week")
        week_interval["scope"] = "conditional on one fixed artifact and sampled complete week blocks"
    elif week_interval.get("reason") == "insufficient_games":
        week_interval["reason"] = "insufficient_weeks"
    body = {
        "version": 1, "basis": "retrospective_fixed_correction_kernel",
        "holdout_id": holdout["holdout_id"], "artifact_id": artifact["artifact_id"],
        "as_of_utc": as_of_utc, "snapshot_watermark": snapshot["watermark"],
        "forecast_count": len(members), "scored_count": len(scored),
        "exclusions": dict(Counter(row["exclusion"] for row in results if row["exclusion"])),
        "metrics": _metrics(scored),
        "positions": {position: _metrics([row for row in scored if row["position"] == position])
                      for position in POSITIONS.values()},
        "weeks": {week: _metrics([row for row in scored if row["week_group"] == week])
                  for week in sorted({row["week_group"] for row in results})},
        "game_interval": _cluster_interval(scored, bootstrap_replicates),
        "week_interval": week_interval, "rows": results,
        "training_approved": False, "adoption_approved": False,
    }
    return {**body, "report_id": fingerprint(body)}
