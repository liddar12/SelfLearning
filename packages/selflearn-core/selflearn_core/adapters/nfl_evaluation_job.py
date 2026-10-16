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


def policy(*, population="eligible_pool", bootstrap_replicates=200, execution_profile=None):
    value = {"version": 1, "kind": "nfl.shadow_job_policy.v1", "population": population, "bootstrap_replicates": bootstrap_replicates}
    if execution_profile is not None:
        from ..compute_process import validate_profile
        value.update(version=2,kind="nfl.shadow_job_policy.v2",execution_profile=validate_profile(execution_profile))
    _policy(value)
    return value


def _policy(value):
    jobs.need(type(value) is dict and type(value.get("version")) is int and value["version"] in (1,2))
    expected={"version", "kind", "population", "bootstrap_replicates"}
    if value["version"]==2:
        from ..compute_process import validate_profile
        expected.add("execution_profile");validate_profile(value.get("execution_profile"))
    jobs.need(set(value)==expected and value["kind"] == "nfl.shadow_job_policy.v%d"%value["version"])
    jobs.need(value["population"] in ("eligible_pool", "offered_my") and type(value["bootstrap_replicates"]) is int
        and 200 <= value["bootstrap_replicates"] <= 2000)


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


def _result(value, spec, settings=None):
    jobs.need(type(value) is dict and type(value.get("version")) is int and value["version"] in (1,2))
    keys={"version", "kind", "job_id", "definition_id", "namespace", "input_object_id", "policy_object_id", "code_id", "report", "artifact", *FALSE_FLAGS}
    if value["version"]==2:keys.add("execution")
    jobs.need(set(value)==keys and value["kind"] == "nfl.shadow_job_result.v%d"%value["version"])
    jobs.need(value["namespace"] == NS and all(value[k] is False for k in FALSE_FLAGS))
    jobs.need(all(value[k] == spec[k] for k in ("job_id", "definition_id", "input_object_id", "policy_object_id", "code_id")))
    report = value["report"]
    jobs.need(type(report) is dict and report.get("report_id") == fingerprint({k:v for k,v in report.items() if k != "report_id"}))
    expected = evidence.artifact(report["candidate"]) if report.get("candidate") is not None else None
    jobs.need(fingerprint(expected) == fingerprint(value["artifact"]))
    if value["version"]==2:
        from .nfl_bounded_evaluation import validate_execution_receipt
        jobs.need(type(settings) is dict and settings.get("version")==2)
        validate_execution_receipt(value["execution"],spec=spec,report=report,profile=settings["execution_profile"])
    elif settings is not None:jobs.need(settings["version"]==1)
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
    jobs.need(spec["code_id"] == jobs.code_identity(), "shadow_job_code_changed")
    settings = _object(journal,spec["policy_object_id"],current["registered_utc"]);_policy(settings)
    if settings["version"]==2:
        jobs.need(callable(compute) and getattr(compute,"profile_id",None)==fingerprint(settings["execution_profile"]),"shadow_job_bounded_executor_required")
    else:jobs.need(compute is None,"shadow_job_inline_policy_cannot_claim_bounded_execution")
    if current["effective_status"] == "succeeded":
        value = _result(_object(journal,current["state"]["result_object_id"],current["events"][-1]["recorded_utc"]),spec,settings)
        return {"status": "completed_shadow", "job_id": job_id, "result_object_id": current["state"]["result_object_id"],
            "result": value, "recovered_original": True, **FALSE_FLAGS}
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
    if settings["version"]==2:
        computed=compute(inputs,settings,spec,current)
        jobs.need(type(computed) is ComputedReport,"shadow_job_invalid_compute_result")
        report,execution=computed.report,computed.execution
    else:
        report = calibration.walk_forward_report(inputs["receipts"],inputs["source_receipts"],inputs["revisions"],
            as_of_utc=inputs["as_of_utc"],population=settings["population"],bootstrap_replicates=settings["bootstrap_replicates"],
            generation_records=inputs["generation_records"])
    # Explicit serialization of the fitter's in-process tuple convenience;
    # uploaded arbitrary Python objects are never normalized this way.
    report = evidence._copy(json.loads(json.dumps(report,allow_nan=False)))
    version=settings["version"]
    value = _result({"version": version, "kind": "nfl.shadow_job_result.v%d"%version, "job_id": job_id, "namespace": dict(NS),
        **{k:spec[k] for k in ("definition_id", "input_object_id", "policy_object_id", "code_id")}, "report": report,
        "artifact": evidence.artifact(report["candidate"]) if report.get("candidate") is not None else None,
        **({"execution":execution}if version==2 else{}), **FALSE_FLAGS},spec,settings)
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
