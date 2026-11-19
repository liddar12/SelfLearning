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


def _membership(snapshot, artifact, start_utc, end_utc, *, known_at=None):
    start, end = instant(start_utc), instant(end_utc)
    if start >= end or instant(artifact["fitted_at"]) >= start:
        raise CorrectionsError("holdout_must_follow_fit")
    training_receipts, training = _select_publication_cohort(snapshot, artifact["trained_through"])
    training_events = {training_receipts[rid]["event_id"] for rid, *_ in training}
    receipts, selected = _select_publication_cohort(snapshot, known_at or end_utc)
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


def _registration_plan(artifact, start_utc, end_utc, policy):
    if policy is None:
        raise CorrectionsError("registration_requires_evaluation_policy")
    _verify_policy(policy, start_utc, end_utc)
    if not instant(artifact["fitted_at"]) < instant(start_utc) < instant(end_utc) <= instant(artifact["expires_at"]):
        raise CorrectionsError("registration_window_outside_artifact_lifetime")
    return {"artifact_id": artifact["artifact_id"], "fitted_at": artifact["fitted_at"],
            "start_utc": start_utc, "end_utc": end_utc, "policy": policy}


def register_correction_holdout(journal, artifact, *, start_utc, end_utc, policy):
    """Commit the fixed candidate and rules using the local journal clock."""
    _verify_artifact(journal.read_publication_fit_snapshot(), artifact)
    return journal.commit_correction_registration(_registration_plan(artifact, start_utc, end_utc, policy))


def _read_registration(journal, registration_id, artifact, start, end, policy, ready_at):
    record = journal.get_correction_registration(registration_id)
    if not record:
        raise CorrectionsError("correction_registration_missing")
    policy = record["plan"]["policy"] if policy is None else policy
    plan = _registration_plan(artifact, start, end, policy)
    recorded = record["recorded_utc"]
    observed = record["durable_observed_utc"]
    body = {"version": 1, "plan": plan, "recorded_utc": recorded}
    if (record != {**body, "registration_id": registration_id, "durable_observed_utc": observed}
            or fingerprint(body) != registration_id
            or not instant(artifact["fitted_at"]) <= instant(recorded) <= instant(observed) < instant(start)
            or instant(policy["registered_utc"]) > instant(recorded)):
        raise CorrectionsError("correction_registration_mismatch")
    if instant(journal.correction_evaluation_utc()) < instant(ready_at):
        raise CorrectionsError("registered_evaluation_cutoff_not_reached")
    return policy


def _seal_membership(artifact, start_utc, end_utc, members, policy, registration_id=None):
    body = {"version": 1, "artifact_id": artifact["artifact_id"],
            "start_utc": start_utc, "end_utc": end_utc, "members": members}
    if policy is not None:
        body["policy"] = policy
    if registration_id is not None:
        body["registration_id"] = registration_id
    return {**body, "holdout_id": fingerprint(body)}


def freeze_correction_holdout(journal, artifact, *, start_utc, end_utc, policy=None, registration_id=None):
    """Freeze the first attested forecasts without using their outcome labels."""
    if registration_id is not None:
        policy = _read_registration(journal, registration_id, artifact, start_utc, end_utc, policy, end_utc)
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    _verify_policy(policy, start_utc, end_utc)
    _, _, members = _membership(snapshot, artifact, start_utc, end_utc)
    return _seal_membership(artifact, start_utc, end_utc, members, policy, registration_id)


def _verify_artifact(snapshot, artifact):
    validate_artifact(artifact)
    if artifact["version"] != LINEAGE_VERSION:
        raise CorrectionsError("lineage_artifact_required")
    _authorize_snapshot(snapshot, artifact)


def _candidate_prediction(receipt, source, artifact, decision_utc):
    lineage_hold = None
    try:
        if lineage_pair(receipt) != lineage_pair(artifact["lineage"]):
            lineage_hold = "incompatible_baseline_lineage"
    except CorrectionsError:
        lineage_hold = "missing_baseline_lineage"
    leg = {"leg_id": receipt["receipt_id"], "duplicate": False, "market": source["market"],
           "selection": source["direction"], "baseline_prob": receipt["served_prob"]}
    return _leg_result(leg, mode="on", artifact=artifact, now=decision_utc,
                       cap_pp=artifact["leg_cap_pp"], binder_verified=True, lineage_hold=lineage_hold)


def _shadow_terms(receipt, source, member, artifact, decision_utc):
    prediction = _candidate_prediction(receipt, source, artifact, decision_utc)
    return {**member, "kernel": "additive_fixed_shadow_v1", "artifact_id": artifact["artifact_id"],
            "event_start_utc": receipt["event_start_utc"], "served_prob": receipt["served_prob"],
            "candidate_prob": prediction["adjusted_prob"], "correction": prediction["reason"]}


def record_correction_shadow(journal, artifact, *, registration_id, start_utc, end_utc):
    """Compute shadow decisions from forecasts independently observed so far."""
    _read_registration(journal, registration_id, artifact, start_utc, end_utc, None, start_utc)
    now = journal.correction_evaluation_utc()
    if not instant(start_utc) <= instant(now) < instant(end_utc):
        raise CorrectionsError("shadow_decision_outside_registered_window")
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    receipts, sources, members = _membership(snapshot, artifact, start_utc, end_utc, known_at=now)
    decisions = []
    for member in members:
        rid = member["receipt_id"]
        if instant(now) < instant(receipts[rid]["event_start_utc"]):
            decisions.append(_shadow_terms(receipts[rid], sources[rid], member, artifact, now))
    records = journal.commit_correction_shadow_decisions(registration_id, decisions, decided_utc=now)
    return {"registration_id": registration_id, "durable_decision_count": len(records),
            "decision_ids": [row["decision_id"] for row in records], "adoption_approved": False}


def _verified_shadow(record, receipt, source, member, artifact, registration_id, start, end):
    if record is None:
        return False
    decided, recorded, observed = (record[key] for key in ("decided_utc", "recorded_utc", "durable_observed_utc"))
    body = {**_shadow_terms(receipt, source, member, artifact, decided), "version": 1,
            "registration_id": registration_id, "decided_utc": decided, "recorded_utc": recorded}
    return (record == {**body, "decision_id": fingerprint(body), "durable_observed_utc": observed}
            and max(instant(start), instant(member["publication_observed_utc"])) <= instant(decided) <= instant(recorded)
            <= instant(observed) < min(instant(end), instant(receipt["event_start_utc"])))


def evaluate_correction_holdout(journal, artifact, holdout, *, as_of_utc, bootstrap_replicates=None,
                                recorded_shadow=False):
    """Evaluate one fixed fit. The report carries no serving or adoption authority."""
    cutoff = instant(as_of_utc)
    start, end = holdout["start_utc"], holdout["end_utc"]
    policy = holdout.get("policy")
    registration_id = holdout.get("registration_id")
    if recorded_shadow and registration_id is None:
        raise CorrectionsError("recorded_shadow_requires_registration")
    if registration_id is not None:
        policy = _read_registration(journal, registration_id, artifact, start, end, policy, as_of_utc)
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
    snapshot = journal.read_publication_fit_snapshot()
    _verify_artifact(snapshot, artifact)
    receipts, sources, members = _membership(snapshot, artifact, start, end)
    if holdout != _seal_membership(artifact, start, end, members, policy, registration_id):
        raise CorrectionsError("frozen_holdout_membership_mismatch")

    histories = _revision_histories(snapshot)
    decisions = {row["receipt_id"]: row for row in journal.correction_shadow_decisions(registration_id)} if recorded_shadow else {}
    verified_decisions = 0
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
        decision = decisions.get(rid)
        if recorded_shadow:
            if _verified_shadow(decision, receipt, source, member, artifact, registration_id, start, end):
                verified_decisions += 1
            elif exclusion is None:
                exclusion = "shadow_decision_missing_or_invalid"
        row = {**member, "position": position, "week_group": week,
               "revision_id": head["revision_id"] if head else None, "exclusion": exclusion}
        results.append(row)
        if exclusion is not None:
            continue

        baseline = receipt["served_prob"]
        if recorded_shadow:
            candidate, reason = decision["candidate_prob"], decision["correction"]
            row["shadow_decision_id"] = decision["decision_id"]
        else:
            prediction = _candidate_prediction(receipt, source, artifact, receipt["forecast_created_utc"])
            candidate, reason = prediction["adjusted_prob"], prediction["reason"]
        hit = head["resolution"]["hit"]
        incumbent_loss, candidate_loss = _loss(baseline, hit), _loss(candidate, hit)
        row.update(served_prob=baseline, candidate_prob=candidate, hit=hit, correction=reason)
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
    evidence = _evidence(scored, len(members), policy, game_interval, week_interval, positions, weeks)
    if registration_id is not None:
        evidence.update(prospective_registration_verified=True, registration_id=registration_id,
                        registration_source="local_sqlite_journal")
        if evidence["status"] == "retrospective_policy_met":
            evidence["status"] = "registered_policy_met"
    if recorded_shadow:
        evidence.update(verified_shadow_decision_count=verified_decisions,
                        missing_shadow_decision_count=len(members) - verified_decisions)
        if verified_decisions < len(members):
            evidence["failures"].append("incomplete_recorded_shadow_coverage")
            evidence["status"] = "held"
        elif evidence["status"] == "registered_policy_met":
            evidence["status"] = "recorded_shadow_policy_met"
    body = {
        "version": 1, "basis": ("recorded_pre_kickoff_shadow_decisions" if recorded_shadow
                                else "retrospective_fixed_correction_kernel"),
        "holdout_id": holdout["holdout_id"], "artifact_id": artifact["artifact_id"],
        "as_of_utc": as_of_utc, "snapshot_watermark": snapshot["watermark"],
        "forecast_count": len(members), "scored_count": len(scored),
        "exclusions": dict(Counter(row["exclusion"] for row in results if row["exclusion"])),
        "metrics": _metrics(scored),
        "positions": positions, "weeks": weeks,
        "game_interval": game_interval, "week_interval": week_interval, "rows": results,
        "evidence": evidence,
        "training_approved": False, "adoption_approved": False,
    }
    return {**body, "report_id": fingerprint(body)}
