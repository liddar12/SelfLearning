"""Read-only retrospective evaluation of the NFL additive correction kernel."""
from collections import Counter

from ..probability_receipts import finite_number, fingerprint, instant
from .nfl_calibration import _cluster_interval, _latest_as_of, _loss, _metrics
from .nfl_corrections import _leg_result, validate_artifact
from .nfl_corrections_fit import _authorize_snapshot, _revision_histories, _select_publication_cohort
from .nfl_corrections_policy import CorrectionsError, LINEAGE_VERSION, lineage_pair
from .nfl_label_qualification import label_exclusion_reason

POSITIONS = {"qb_pass_yds": "QB", "rb_rush_yds": "RB", "wr_rec_yds": "WR"}


def seal_evaluation_policy(*, registered_utc, outcome_cutoff_utc, minimum_games, minimum_weeks, minimum_position_games,
                           minimum_coverage, minimum_adjusted_fraction, improvement_margin,
                           position_loss_tolerance, bootstrap_replicates, rationale):
    """Seal declared thresholds; the supplied clock is not independent time evidence."""
    if instant(registered_utc) >= instant(outcome_cutoff_utc):
        raise CorrectionsError("invalid_evaluation_policy_clock")
    counts = (minimum_games, minimum_weeks, minimum_position_games)
    if (any(type(count) is not int or count < 2 for count in counts)
            or type(bootstrap_replicates) is not int or not 200 <= bootstrap_replicates <= 10000
            or not all(finite_number(value) and 0 < value <= 1
                       for value in (minimum_coverage, minimum_adjusted_fraction))
            or not finite_number(improvement_margin) or improvement_margin <= 0
            or not finite_number(position_loss_tolerance) or position_loss_tolerance < 0
            or type(rationale) is not str or not rationale.strip() or len(rationale) > 2048):
        raise CorrectionsError("invalid_evaluation_policy")
    body = {
        "version": 1, "registered_utc": registered_utc, "outcome_cutoff_utc": outcome_cutoff_utc,
        "minimum_games": minimum_games,
        "minimum_weeks": minimum_weeks, "minimum_position_games": minimum_position_games,
        "minimum_coverage": minimum_coverage, "minimum_adjusted_fraction": minimum_adjusted_fraction,
        "improvement_margin": improvement_margin, "position_loss_tolerance": position_loss_tolerance,
        "bootstrap_replicates": bootstrap_replicates, "rationale": rationale,
    }
    return {**body, "policy_id": fingerprint(body)}


def _verify_policy(policy, start_utc, end_utc):
    if policy is None:
        return
    try:
        supplied = {key: value for key, value in policy.items() if key not in {"version", "policy_id"}}
        expected = seal_evaluation_policy(**supplied)
        if (type(policy["version"]) is not int or policy != expected
                or instant(policy["registered_utc"]) >= instant(start_utc)):
            raise CorrectionsError("policy_must_precede_holdout")
        if instant(policy["outcome_cutoff_utc"]) < instant(end_utc):
            raise CorrectionsError("outcome_cutoff_precedes_holdout_end")
    except (AttributeError, KeyError, TypeError, OverflowError):
        raise CorrectionsError("invalid_evaluation_policy") from None


def _week_interval(rows, replicates):
    interval = _cluster_interval(
        [{**row, "correlation_group": row["week_group"]} for row in rows], replicates)
    if "method" in interval:
        interval["method"] = interval["method"].replace("by game", "by week")
        interval["scope"] = "conditional on one fixed artifact and sampled complete week blocks"
    elif interval.get("reason") == "insufficient_games":
        interval["reason"] = "insufficient_weeks"
    return interval


def _evidence(rows, forecast_count, policy, game_interval, week_interval, positions, weeks):
    adjusted = [row for row in rows if row["served_prob"] != row["candidate_prob"]]
    coverage = len(rows) / forecast_count if forecast_count else 0.0
    adjusted_fraction = len(adjusted) / len(rows) if rows else 0.0
    failures = []
    if policy is None:
        failures.append("evaluation_policy_missing")
    else:
        if coverage < policy["minimum_coverage"]:
            failures.append("qualified_coverage_below_minimum")
        if adjusted_fraction < policy["minimum_adjusted_fraction"]:
            failures.append("adjustment_coverage_below_minimum")
        if game_interval["groups"] < policy["minimum_games"]:
            failures.append("independent_game_support_below_minimum")
        if week_interval["groups"] < policy["minimum_weeks"]:
            failures.append("independent_week_support_below_minimum")
        for period, interval in (("game", game_interval), ("week", week_interval)):
            if interval["high"] is None or interval["high"] > -policy["improvement_margin"]:
                failures.append(f"{period}_interval_does_not_meet_improvement_margin")
        for position, detail in positions.items():
            for period, minimum in (("game", policy["minimum_position_games"]),
                                    ("week", policy["minimum_weeks"])):
                interval = detail[f"{period}_interval"]
                if interval["groups"] < minimum:
                    failures.append(f"{position}_{period}_support_below_minimum")
                if interval["high"] is None or interval["high"] > policy["position_loss_tolerance"]:
                    failures.append(f"{position}_{period}_nonregression_not_established")
    comparable_weeks = [(metrics["delta_log_loss"], week) for week, metrics in weeks.items()
                        if metrics["delta_log_loss"] is not None]
    worst_week = max(comparable_weeks)[1] if comparable_weeks else None
    return {
        "status": "held" if failures else "retrospective_policy_met",
        "policy_id": policy["policy_id"] if policy else None,
        "failures": failures, "qualified_coverage": coverage,
        "adjusted_fraction": adjusted_fraction, "adjusted_only_metrics": _metrics(adjusted),
        "worst_week": worst_week,
        "fallback_counts": dict(Counter(row["correction"] for row in rows
                                        if row["served_prob"] == row["candidate_prob"])),
        "prospective_registration_verified": False,
    }


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


def _seal_membership(artifact, start_utc, end_utc, members, policy):
    body = {"version": 1, "artifact_id": artifact["artifact_id"],
            "start_utc": start_utc, "end_utc": end_utc, "members": members}
    if policy is not None:
        body["policy"] = policy
    return {**body, "holdout_id": fingerprint(body)}


def freeze_correction_holdout(journal, artifact, *, start_utc, end_utc, policy=None):
    """Freeze the first attested forecasts without using their outcome labels."""
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    _verify_policy(policy, start_utc, end_utc)
    _, _, members = _membership(snapshot, artifact, start_utc, end_utc)
    return _seal_membership(artifact, start_utc, end_utc, members, policy)


def _verify_artifact(snapshot, artifact):
    validate_artifact(artifact)
    if artifact["version"] != LINEAGE_VERSION:
        raise CorrectionsError("lineage_artifact_required")
    _authorize_snapshot(snapshot, artifact)


def evaluate_correction_holdout(journal, artifact, holdout, *, as_of_utc, bootstrap_replicates=None):
    """Evaluate one fixed fit. The report carries no serving or adoption authority."""
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    cutoff = instant(as_of_utc)
    start, end = holdout["start_utc"], holdout["end_utc"]
    policy = holdout.get("policy")
    _verify_policy(policy, start, end)
    if policy is not None and cutoff != instant(policy["outcome_cutoff_utc"]):
        raise CorrectionsError("evaluation_policy_outcome_cutoff_mismatch")
    locked_replicates = policy["bootstrap_replicates"] if policy else 2000
    if policy is not None and bootstrap_replicates is not None and bootstrap_replicates != locked_replicates:
        raise CorrectionsError("evaluation_policy_bootstrap_mismatch")
    if bootstrap_replicates is None:
        bootstrap_replicates = locked_replicates
    if cutoff < instant(end):
        raise CorrectionsError("outcome_cutoff_precedes_holdout_end")
    receipts, sources, members = _membership(snapshot, artifact, start, end)
    if holdout != _seal_membership(artifact, start, end, members, policy):
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

    game_interval = _cluster_interval(scored, bootstrap_replicates)
    week_interval = _week_interval(scored, bootstrap_replicates)
    positions = {}
    for position in POSITIONS.values():
        subset = [row for row in scored if row["position"] == position]
        positions[position] = {**_metrics(subset),
                               "game_interval": _cluster_interval(subset, bootstrap_replicates),
                               "week_interval": _week_interval(subset, bootstrap_replicates)}
    weeks = {week: _metrics([row for row in scored if row["week_group"] == week])
             for week in sorted({row["week_group"] for row in results})}
    body = {
        "version": 1, "basis": "retrospective_fixed_correction_kernel",
        "holdout_id": holdout["holdout_id"], "artifact_id": artifact["artifact_id"],
        "as_of_utc": as_of_utc, "snapshot_watermark": snapshot["watermark"],
        "forecast_count": len(members), "scored_count": len(scored),
        "exclusions": dict(Counter(row["exclusion"] for row in results if row["exclusion"])),
        "metrics": _metrics(scored),
        "positions": positions, "weeks": weeks,
        "game_interval": game_interval, "week_interval": week_interval, "rows": results,
        "evidence": _evidence(scored, len(members), policy, game_interval, week_interval, positions, weeks),
        "training_approved": False, "adoption_approved": False,
    }
    return {**body, "report_id": fingerprint(body)}
