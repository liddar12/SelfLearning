"""Actual opt-in NFL shadow evaluation on durable, fenced generic jobs.

No route, scheduler, credential discovery, serving writes or owner approval.
This evaluates the refitting POLICY, not prospective fixed-artifact accuracy.
The store/caller and execution environment are trusted. Computation may repeat
after a crash; only its result reference is fenced. Unreferenced immutable
objects are possible if a lease expires between object append and completion.
Database deadlines refuse late commits; this function does not preempt a
blocking fitter/IO callback or claim a hard process/CPU/cost ceiling.
"""
import json
from dataclasses import dataclass

from .. import learning_jobs as jobs
from ..probability_receipts import fingerprint, instant
from . import nfl_calibration as calibration
from . import nfl_owner_evidence as evidence

NS = {"domain": "nfl", "task": "nfl.parlay_leg", "producer_id": "nfl2026"}
OPERATION = "nfl.shadow_calibration.v1"
FALSE_FLAGS = {"activation": False, "model_adoption": False, "current_serving_verified": False,
               "provider_authenticated": False, "complete_labels_verified": False,
               "accuracy_improvement_verified": False, "working_live_loop": False}
QUALIFIED_REPORT_FIELDS = {
    "version", "kind", "population", "as_of_utc", "evaluation_kind", "cohort_id", "cohort_n",
    "excluded", "forecast_dataset_id", "captured_forecast_n", "membership_policy",
    "offering_membership_coverage", "folds", "metrics", "per_position", "paired_game_interval",
    "shared_shadow_numeric_gate", "current_nfl_rules_reference", "proposed_shared_return_gate",
    "candidate", "rows", "automatic_promotion", "public_serving_verified", "publication",
    "outcome_admission", "label_coverage", "generation_coverage", "report_id",
}


def policy(*, population="eligible_pool", bootstrap_replicates=200, execution_profile=None,
           outcome_admission=calibration.EVENT_BOUND_LABELS):
    """New jobs qualify labels explicitly; legacy research must opt in.

    V1/v2 documents retain their exact historical shapes and hashes. V2 is a
    process-resource policy, not the event-bound-label policy introduced in v3.
    Neither profile proves a public offer or authorizes model adoption.
    """
    jobs.need(outcome_admission in (calibration.LEGACY_LABELS, calibration.EVENT_BOUND_LABELS))
    value = {"version": 1, "kind": "nfl.shadow_job_policy.v1", "population": population, "bootstrap_replicates": bootstrap_replicates}
    if execution_profile is not None:
        from ..compute_process import validate_profile
        value.update(version=2,kind="nfl.shadow_job_policy.v2",execution_profile=validate_profile(execution_profile))
    if outcome_admission == calibration.EVENT_BOUND_LABELS:
        value.update(version=3, kind="nfl.shadow_job_policy.v3",
                     outcome_admission=outcome_admission, execution_profile=execution_profile)
    _policy(value)
    return value


def _policy(value):
    jobs.need(type(value) is dict and type(value.get("version")) is int and value["version"] in (1,2,3))
    expected={"version", "kind", "population", "bootstrap_replicates"}
    if value["version"]==3:
        expected.update(("outcome_admission", "execution_profile"))
        jobs.need(value.get("outcome_admission") == calibration.EVENT_BOUND_LABELS)
    if value["version"]==2 or value["version"]==3 and value.get("execution_profile") is not None:
        from ..compute_process import validate_profile
        expected.add("execution_profile");validate_profile(value.get("execution_profile"))
    jobs.need(set(value)==expected and value["kind"] == "nfl.shadow_job_policy.v%d"%value["version"])
    jobs.need(value["population"] in ("eligible_pool", "offered_my") and type(value["bootstrap_replicates"]) is int
        and 200 <= value["bootstrap_replicates"] <= 2000)


def bounded_policy(value):
    _policy(value)
    return value.get("execution_profile") is not None


def outcome_admission(value):
    _policy(value)
    return calibration.EVENT_BOUND_LABELS if value["version"] == 3 else calibration.LEGACY_LABELS


def _object(journal, identity, cutoff):
    value = journal.get_object(identity)
    jobs.need(type(value) is dict and value.get("kind") == "source_snapshot", "shadow_job_object_absent")
    payload = evidence._copy(value["payload"])  # Existing NFL strict-JSON/work profile, not generic job metadata.
    jobs.need(fingerprint(payload) == identity and instant(value["received_utc"]) <= instant(cutoff), "shadow_job_object_mismatch")
    return payload


def _inputs(value, cutoff):
    jobs.need(type(value) is dict and set(value) == {"version", "kind", "receipts", "source_receipts", "revisions", "generation_records", "as_of_utc"})
    jobs.need(type(value["version"]) is int and value["version"] == 1 and value["kind"] == "nfl.shadow_job_inputs.v1")
    for key, maximum in (("receipts", 2000), ("source_receipts", 2000), ("revisions", 8000)):
        jobs.need(type(value[key]) is list and len(value[key]) <= maximum)
    jobs.need(value["generation_records"] is None or type(value["generation_records"]) is list and len(value["generation_records"]) <= 256)
    jobs.need(jobs.clock(value["as_of_utc"]) <= jobs.clock(cutoff), "shadow_job_future_cutoff")


def _command(current, action, worker_id, authority_id, *, object_id=None, reason=None):
    return jobs.seal_command({"version": 1, "kind": "selflearn.job_command.v1", "job_id": current["definition"]["job_id"],
        "expected_head_event_id": current["head_event_id"], "action": action, "worker_id": worker_id,
        "lease_id": None if action == "claim" else current["state"]["lease_id"], "object_id": object_id,
        "reason_code": reason}, authority_id=authority_id)


def _checkpoint(spec):
    return {"version": 1, "kind": "nfl.shadow_job_checkpoint.v1", "job_id": spec["job_id"], "stage": "inputs_verified",
        **{k: spec[k] for k in ("definition_id", "input_object_id", "policy_object_id", "code_id")}}


def _live(journal, job_id, expected_head, lease_id):
    current = journal.read_job(job_id)
    if (current["effective_status"] != "running" or current["head_event_id"] != expected_head
            or current["state"]["lease_id"] != lease_id):raise jobs.JobConflict("shadow_job_lease_changed")
    return current


@dataclass(frozen=True)
class ComputedReport:
    report: dict
    execution: dict


def _qualified_coverage(value):
    reason = "shadow_job_qualified_coverage_inconsistent"
    labels, generations = value.get("label_coverage"), value.get("generation_coverage")
    jobs.need(type(labels) is dict and set(labels) == {
        "version", "label_policy", "forecast_cohort_count", "graded_count",
        "excluded_count", "exclusion_reasons",
    } and type(generations) is dict and set(generations) == {
        "version", "forecast_cohort_count", "verified_count", "missing_count",
    }, reason)
    jobs.need(type(labels["version"]) is int and labels["version"] == 1
              and type(generations["version"]) is int and generations["version"] == 1
              and labels["label_policy"] == "stable_player_ids_exact_fixture_v2", reason)
    cohort, graded, excluded = (labels[key] for key in ("forecast_cohort_count", "graded_count", "excluded_count"))
    generation_cohort, verified, missing = (generations[key] for key in (
        "forecast_cohort_count", "verified_count", "missing_count"))
    jobs.need(all(type(count) is int and count >= 0 for count in (
        cohort, graded, excluded, generation_cohort, verified, missing)), reason)
    exclusions = labels["exclusion_reasons"]
    jobs.need(type(exclusions) is dict and all(type(key) is str and key and type(count) is int and count > 0
              for key, count in exclusions.items()), reason)
    jobs.need(cohort == graded + excluded == generation_cohort == verified + missing
              and excluded == sum(exclusions.values()), reason)
    return cohort, graded, verified > 0 and missing == 0


def _gate_failures(value, policy_fields):
    reason = "shadow_job_qualified_gate_inconsistent"
    jobs.need(type(value) is dict and set(value) == {"passed", "failures", "outcome_admission", *policy_fields}
              and value["outcome_admission"] == calibration.EVENT_BOUND_LABELS
              and all(value[key] == expected for key, expected in policy_fields.items()), reason)
    failures = value["failures"]
    jobs.need(type(failures) is list and all(type(item) is str and item for item in failures)
              and len(failures) == len(set(failures)) and value["passed"] is (not failures), reason)
    return set(failures)


def _qualified_report(report, settings):
    jobs.need(settings is not None and set(report) == QUALIFIED_REPORT_FIELDS
              and type(report["version"]) is int and report["version"] == 2
              and report["kind"] == "nfl.my_props.walk_forward.v2"
              and report["outcome_admission"] == calibration.EVENT_BOUND_LABELS,
              "shadow_job_qualified_report_required")
    jobs.need(report["population"] == settings["population"], "shadow_job_report_population_mismatch")
    rules = report["current_nfl_rules_reference"]
    jobs.need(report["public_serving_verified"] is False and report["automatic_promotion"] is False
              and report["publication"] == "internally captured; public serving not verified"
              and report["evaluation_kind"] == "refitting_policy_walk_forward_not_fixed_artifact_prospective_validation"
              and type(rules) is dict and rules.get("parity_executed") is False,
              "shadow_job_report_authority_claim")
    cohort, graded, generation_complete = _qualified_coverage(report)
    reason = "shadow_job_qualified_coverage_inconsistent"
    jobs.need(type(report["captured_forecast_n"]) is int and cohort == report["captured_forecast_n"]
              and type(report["cohort_n"]) is int and graded == report["cohort_n"]
              and type(report["folds"]) is list and type(report["rows"]) is list
              and type(report["metrics"]) is dict, reason)
    scored, scored_folds, incomplete_fold = 0, 0, False
    for fold in report["folds"]:
        jobs.need(type(fold) is dict and fold.get("status") in ("scored", "skipped", "held"), reason)
        _, fold_graded, fold_generation_complete = _qualified_coverage(fold)
        count, fit = fold.get("n"), fold.get("fit_n")
        jobs.need(type(count) is int and 0 < count <= graded and type(fit) is int and 0 <= fit <= fold_graded, reason)
        if fold["status"] == "scored":
            jobs.need(fit >= calibration.MIN_LEGS, reason)
            scored += count
            scored_folds += 1
            incomplete_fold = incomplete_fold or not fold_generation_complete
    measured = report["metrics"].get("n")
    jobs.need(type(measured) is int and measured == scored == len(report["rows"]) <= graded, reason)
    interval = report["paired_game_interval"]
    groups = interval.get("groups") if type(interval) is dict else None
    jobs.need(type(groups) is int and 0 <= groups <= measured
              and (groups < 2 and "replicates" not in interval
                   or groups >= 2 and interval.get("replicates") == settings["bootstrap_replicates"]),
              "shadow_job_report_bootstrap_mismatch")

    shadow = _gate_failures(report["shared_shadow_numeric_gate"], {
        "min_legs": calibration.MIN_LEGS, "min_training_contracts_per_fold": calibration.MIN_LEGS,
        "min_held_out_week_folds": calibration.MIN_HELD_OUT,
        "status": "proposed_shared_fitter_and_stricter_numeric_policy_not_current_nfl_R100_parity",
    })
    proposed = _gate_failures(report["proposed_shared_return_gate"], {
        "additional_margin": calibration.PROPOSED_MARGIN,
        "status": "proposed_policy_not_production_authorization",
    })
    required = {failure for present, failure in (
        (not graded, "no_qualified_outcomes"),
        (graded < calibration.MIN_LEGS, "fewer_than_100_canonical_contracts"),
        (scored_folds < calibration.MIN_HELD_OUT, "fewer_than_two_held_out_week_folds"),
        (not scored, "no_pooled_base_improvement"),
    ) if present}
    jobs.need(required <= shadow <= proposed
              and (generation_complete or "committed_generation_evidence_unverified" in proposed)
              and (not incomplete_fold or "training_fold_generation_evidence_unverified" in proposed),
              "shadow_job_qualified_gate_inconsistent")
    candidate = report["candidate"]
    if candidate is None:
        jobs.need("no_valid_final_fit" in shadow, "shadow_job_qualified_gate_inconsistent")
        return
    jobs.need(type(candidate) is dict and set(candidate) == {
        "version", "kind", "task", "state", "automatic_promotion", "evidence_status",
        "receipt_policy", "base_model_version", "transform", "coefficients", "fit_as_of_utc",
        "training", "candidate_id",
    }, "shadow_job_qualified_candidate_mismatch")
    training = candidate["training"]
    jobs.need(type(training) is dict and set(training) == {
        "population", "membership_policy", "cutoff_utc", "n", "fit_rule",
        "ridge", "dataset_id", "rows", "outcome_admission",
    } and training["outcome_admission"] == calibration.EVENT_BOUND_LABELS
              and training["population"] == report["population"]
              and training["membership_policy"] == report["membership_policy"]
              and training["cutoff_utc"] == report["as_of_utc"]
              and training["dataset_id"] == report["cohort_id"] and training["n"] == graded
              and "no_valid_final_fit" not in shadow, "shadow_job_qualified_candidate_mismatch")


def _result(value, spec, settings=None):
    jobs.need(type(value) is dict and type(value.get("version")) is int and value["version"] in (1,2,3))
    keys={"version", "kind", "job_id", "definition_id", "namespace", "input_object_id", "policy_object_id", "code_id", "report", "artifact", *FALSE_FLAGS}
    if value["version"] in (2,3):keys.add("execution")
    jobs.need(set(value)==keys and value["kind"] == "nfl.shadow_job_result.v%d"%value["version"])
    jobs.need(value["namespace"] == NS and all(value[k] is False for k in FALSE_FLAGS))
    jobs.need(all(value[k] == spec[k] for k in ("job_id", "definition_id", "input_object_id", "policy_object_id", "code_id")))
    report = value["report"]
    jobs.need(type(report) is dict and report.get("report_id") == fingerprint({k:v for k,v in report.items() if k != "report_id"}))
    if settings is not None:
        _policy(settings)
        jobs.need(value["version"] == settings["version"] and fingerprint(settings) == spec["policy_object_id"],
                  "shadow_job_result_policy_mismatch")
    if value["version"] == 3:
        _qualified_report(report, settings)
    expected = evidence.artifact(report["candidate"]) if report.get("candidate") is not None else None
    jobs.need(fingerprint(expected) == fingerprint(value["artifact"]))
    if value["version"]==2 or value["version"]==3 and bounded_policy(settings):
        from .nfl_bounded_evaluation import validate_execution_receipt
        jobs.need(type(settings) is dict and bounded_policy(settings))
        validate_execution_receipt(value["execution"],spec=spec,report=report,profile=settings["execution_profile"])
    elif value["version"]==3:jobs.need(value["execution"] is None, "shadow_job_inline_execution_mismatch")
    return value


def run_shadow_job(journal, job_id, *, authority_id, worker_id, max_phases=2, compute=None):
    """Run preflight then actual fitter, or resume a durable preflight checkpoint.

    `max_phases=1` explicitly pauses after preflight. Repeating a completed job
    returns its original stored result without fitting again. The input object
    is immutable but source/label authenticity remains unverified. A completed
    generic job means computation completed, NOT that numeric/model gates pass.
    """
    authority_id = jobs.digest(authority_id);jobs.name(worker_id)
    jobs.need(type(max_phases) is int and 1 <= max_phases <= 2)
    jobs.need(getattr(journal, "authority_id", None) == authority_id)
    current = journal.read_job(jobs.digest(job_id));spec = current["definition"]
    jobs.need(spec["namespace"] == NS and spec["operation"] == OPERATION and spec["max_steps"] >= 2, "shadow_job_incompatible_definition")
    settings = _object(journal,spec["policy_object_id"],current["registered_utc"]);_policy(settings)
    if current["effective_status"] == "succeeded":
        # Read-only recovery retains the original policy/code and cannot grant
        # new qualification. Never rerun an old job under the current package.
        value = _result(_object(journal,current["state"]["result_object_id"],current["events"][-1]["recorded_utc"]),spec,settings)
        return {"status": "completed_shadow", "job_id": job_id, "result_object_id": current["state"]["result_object_id"],
            "result": value, "recovered_original": True, **FALSE_FLAGS}
    jobs.need(spec["code_id"] == jobs.code_identity(), "shadow_job_code_changed")
    if bounded_policy(settings):
        jobs.need(callable(compute) and getattr(compute,"profile_id",None)==fingerprint(settings["execution_profile"]),"shadow_job_bounded_executor_required")
    else:jobs.need(compute is None,"shadow_job_inline_policy_cannot_claim_bounded_execution")
    jobs.need(not (max_phases == 1 and current["state"]["checkpoint_object_id"] is None
        and spec["max_attempts"] - current["state"]["attempt"] < 2), "shadow_job_pause_would_exhaust_attempts")
    claim = _command(current,"claim",worker_id,authority_id)
    ack = journal.append_job_command(claim)
    current = _live(journal,job_id,ack["event_id"],claim["command_id"])
    inputs = _object(journal,spec["input_object_id"],current["registered_utc"])
    _inputs(inputs,current["registered_utc"])
    checkpoint = _checkpoint(spec);checkpoint_id = fingerprint(checkpoint)
    if current["state"]["checkpoint_object_id"] is not None:
        previous = _object(journal,current["state"]["checkpoint_object_id"],current["observed_utc"])
        jobs.need(fingerprint(previous) == checkpoint_id, "shadow_job_checkpoint_mismatch")
    else:
        jobs.need(spec["code_id"] == jobs.code_identity(), "shadow_job_code_changed")
        journal.append_object("source_snapshot",checkpoint_id,checkpoint)
        ack = journal.append_job_command(_command(current,"checkpoint",worker_id,authority_id,object_id=checkpoint_id))
        current = _live(journal,job_id,ack["event_id"],claim["command_id"])
        if max_phases == 1:
            journal.append_job_command(_command(current,"release",worker_id,authority_id,reason="paused"))
            return {"status": "paused", "job_id": job_id, "checkpoint_object_id": checkpoint_id, **FALSE_FLAGS}
    execution=None
    if bounded_policy(settings):
        computed=compute(inputs,settings,spec,current)
        jobs.need(type(computed) is ComputedReport,"shadow_job_invalid_compute_result")
        report,execution=computed.report,computed.execution
    else:
        report = calibration.walk_forward_report(inputs["receipts"],inputs["source_receipts"],inputs["revisions"],
            as_of_utc=inputs["as_of_utc"],population=settings["population"],bootstrap_replicates=settings["bootstrap_replicates"],
            generation_records=inputs["generation_records"],outcome_admission=outcome_admission(settings))
    # Explicit serialization of the fitter's in-process tuple convenience;
    # uploaded arbitrary Python objects are never normalized this way.
    report = evidence._copy(json.loads(json.dumps(report,allow_nan=False)))
    version=settings["version"]
    value = _result({"version": version, "kind": "nfl.shadow_job_result.v%d"%version, "job_id": job_id, "namespace": dict(NS),
        **{k:spec[k] for k in ("definition_id", "input_object_id", "policy_object_id", "code_id")}, "report": report,
        "artifact": evidence.artifact(report["candidate"]) if report.get("candidate") is not None else None,
        **({"execution":execution}if version in (2,3) else{}), **FALSE_FLAGS},spec,settings)
    jobs.need(spec["code_id"] == jobs.code_identity(), "shadow_job_code_changed")
    current = _live(journal,job_id,current["head_event_id"],claim["command_id"])
    result_id = fingerprint(value);journal.append_object("source_snapshot",result_id,value)
    ack = journal.append_job_command(_command(current,"complete",worker_id,authority_id,object_id=result_id))
    # Never return an invented result merely because append returned durable.
    final = journal.read_job(job_id)
    jobs.need(final["effective_status"] == "succeeded" and final["head_event_id"] == ack["event_id"]
        and final["state"]["result_object_id"] == result_id, "shadow_job_completion_unverified")
    return {"status": "completed_shadow", "job_id": job_id, "result_object_id": result_id,
        "result": value, "recovered_original": False, **FALSE_FLAGS}
