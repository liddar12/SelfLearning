"""Pure pinned producer/job delivery contract; no IO or source authentication.

Complete generic event histories are replayed by learning_jobs. These checks
bind declared identities and original evidence; they do not remotely attest
which source commit ran or refresh an earlier public HTTP observation.
"""
from __future__ import annotations

from datetime import timedelta
import re

from .. import learning_jobs as dto
from ..probability_receipts import fingerprint, instant, finite_number
from .nfl_publication import validate_policy, validate_publication_attestation, ROW_KEYS, MARKETS

NAMESPACE = {"domain":"nfl", "task":"publication.recording", "producer_id":"nfl2026"}
OPERATION = "nfl.publication.record"
LEASE_SECONDS, MAX_RUNTIME_SECONDS, MAX_ATTEMPTS, MAX_STEPS = 60, 3600, 8, 1
RECOVERY_SECONDS = 3600
FALSE_FLAGS = {"learning_enabled":False, "model_adoption":False, "fitting_run":False,
               "browser_display_verified":False, "hard_execution_limit":False}
MINUTE_KICKOFF = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2})(Z|[+-](?:0[0-9]|1[0-5]):[0-5][0-9])\Z")


def _kickoff_clock(value):
    """Validate provider kickoff times without changing the signed evidence."""
    match = MINUTE_KICKOFF.fullmatch(value) if type(value) is str else None
    return dto.clock(f"{match[1]}:00{match[2]}" if match else value)


def settings(policy):
    validate_policy(policy)
    return {"version":1, "kind":"nfl.publication_job_policy.v1", "publication_policy":dict(policy),
        "lease_seconds":LEASE_SECONDS, "max_runtime_seconds":MAX_RUNTIME_SECONDS,
        "max_attempts":MAX_ATTEMPTS, "max_steps":MAX_STEPS, "recovery_seconds":RECOVERY_SECONDS}


def definition(object_id, generation_id, generation_recorded_utc, event_start_utcs,
               policy, code_id, authority_id):
    dto.digest(object_id); dto.digest(generation_id); dto.digest(code_id); dto.digest(authority_id)
    dto.clock(generation_recorded_utc)
    dto.need(type(event_start_utcs) in (list, tuple) and 0 < len(event_start_utcs) <= 10000,
             "publication_job_membership_invalid")
    latest_kickoff = max(map(_kickoff_clock, event_start_utcs))
    policy_settings = settings(policy)
    inputs = {"version":1, "kind":"nfl.publication_job_input.v1", "object_id":object_id,
              "generation_id":generation_id, "policy_id":fingerprint(policy)}
    spec = dto.seal_spec({"version":1, "kind":"selflearn.job_definition.v1",
        "namespace":dict(NAMESPACE), "operation":OPERATION,
        "input_object_id":fingerprint(inputs), "policy_object_id":fingerprint(policy_settings), "code_id":code_id,
        "not_before_utc":generation_recorded_utc,
        "not_after_utc":dto.stamp(latest_kickoff + timedelta(seconds=RECOVERY_SECONDS)),
        "lease_seconds":LEASE_SECONDS, "max_runtime_seconds":MAX_RUNTIME_SECONDS,
        "max_attempts":MAX_ATTEMPTS, "max_steps":MAX_STEPS}, authority_id=authority_id)
    return spec, inputs, policy_settings


def validate_snapshot(value, expected_spec, authority_id):
    expected_spec = dto.validate_spec(expected_spec, authority_id=authority_id)
    dto.need(type(value) is dict, "publication_job_snapshot_invalid")
    checked = dto.complete_snapshot(value["definition"], value["events"], authority_id=authority_id,
        registered_utc=value["registered_utc"], observed_utc=value["observed_utc"])
    dto.need(dto.canonical(value) == dto.canonical(checked)
        and dto.canonical(checked["definition"]) == dto.canonical(expected_spec),
        "publication_job_snapshot_invalid")
    return checked


def validate_declaration(value, expected_policy, authority_id):
    dto.need(type(value) is dict and set(value) == {"version","authority_id","code_id","policy","settings"}
        and type(value["version"]) is int and value["version"] == 2,
        "publication_job_declaration_invalid")
    dto.digest(authority_id); dto.digest(value["code_id"])
    dto.need(value["authority_id"] == authority_id
        and dto.canonical(value["policy"]) == dto.canonical(validate_policy(expected_policy))
        and dto.canonical(value["settings"]) == dto.canonical(settings(expected_policy)),
        "publication_job_declaration_invalid")
    return value


def object_wrapper(identity, payload):
    dto.digest(identity)
    dto.need(fingerprint(payload) == identity, "publication_job_reference_invalid")
    return {"object_id":identity,"kind":"source_snapshot","payload":payload}


def _payload(wrapper, identity):
    dto.need(type(wrapper) is dict and set(wrapper) == {"object_id","kind","payload"}
        and wrapper["kind"] == "source_snapshot" and wrapper["object_id"] == identity,
        "publication_job_reference_invalid")
    object_wrapper(identity, wrapper["payload"])
    return wrapper["payload"]


def validate_proof(value,inputs,gen,expected,policy):
    validate_publication_attestation(value)
    dto.need(value["object_id"] == inputs["object_id"] and value["generation_id"] == gen["generation_id"]
        and value["source_pool_id"] == gen["source_snapshots"]["leg_pool"]
        and value["policy_id"] == fingerprint(policy) and value["policy"] == policy,
        "publication_job_proof_changed")
    actual = {r["source_receipt_id"]:r["receipt_id"] for r in value["receipts"]+value["rejections"]}
    dto.need(actual == {sid:r["receipt_id"] for sid,r in expected.items()}, "publication_job_proof_membership")
    completed,recorded = instant(value["observation"]["observed_completed_utc"]),instant(value["recorded_utc"])
    dto.need(instant(gen["recorded_utc"])<=instant(value["observation"]["observed_started_utc"]),
             "publication_job_proof_before_generation")
    for row in value["receipts"]:
        dto.need(dto.canonical(row) == dto.canonical(expected[row["source_receipt_id"]]), "publication_job_proof_member")
    for row in value["rejections"]:
        kickoff = instant(expected[row["source_receipt_id"]]["event_start_utc"])
        dto.need(completed >= kickoff or recorded >= kickoff, "publication_job_false_late_rejection")
    return value


def result(spec,inputs,proof):
    return {"version":1,"kind":"nfl.publication_job_result.v1",
        **{k:spec[k] for k in ("job_id","definition_id","input_object_id","policy_object_id","code_id")},
        "object_id":inputs["object_id"],"generation_id":inputs["generation_id"],
        "attestation_id":proof["attestation_id"],"publication_status":proof["status"],
        "accepted_receipts":len(proof["receipts"]),"rejected_receipts":len(proof["rejections"]),
        "recording_complete":True, **FALSE_FLAGS}



def validate_delivery(view, expected_spec, inputs, policy_settings, members, generation, authority_id):
    """Validate an original job/result/proof, including non-success states.

    Caller pins generation and accepted generic/source membership from its
    original durable M1 ACK; neither current policy nor local code is inferred.
    """
    dto.need(type(view) is dict and set(view) == {"version","snapshot","input","policy","result","attestation"}
        and type(view["version"]) is int and view["version"] == 2,
        "publication_job_delivery_invalid")
    current = validate_snapshot(view["snapshot"], expected_spec, authority_id)
    spec = current["definition"]
    policy = policy_settings["publication_policy"]
    expected = members if type(members) is dict else {r["source_receipt_id"]:r for r in members}
    dto.need(type(expected) is dict and 0 < len(expected) <= 10000
        and len(expected) == len(members), "publication_job_membership_invalid")
    generic_ids = set()
    for sid, row in expected.items():
        dto.need(type(row) is dict and set(row) == ROW_KEYS and row["source_receipt_id"] == sid
            and row["market"] in MARKETS and row["direction"] == "over"
            and finite_number(row["served_prob"]) and 0 < row["served_prob"] < 1,
            "publication_job_membership_invalid")
        dto.digest(sid); dto.digest(row["receipt_id"]); _kickoff_clock(row["event_start_utc"])
        dto.need(row["receipt_id"] not in generic_ids, "publication_job_membership_invalid")
        generic_ids.add(row["receipt_id"])
    rebuilt, rebuilt_input, rebuilt_policy = definition(inputs["object_id"], generation["generation_id"],
        generation["recorded_utc"], [r["event_start_utc"] for r in expected.values()],
        policy, spec["code_id"], authority_id)
    dto.need(dto.canonical(rebuilt) == dto.canonical(spec)
        and dto.canonical(rebuilt_input) == dto.canonical(inputs)
        and dto.canonical(rebuilt_policy) == dto.canonical(policy_settings)
        and dto.canonical(_payload(view["input"], spec["input_object_id"])) == dto.canonical(inputs)
        and dto.canonical(_payload(view["policy"], spec["policy_object_id"])) == dto.canonical(policy_settings),
        "publication_job_definition_changed")
    proof = view["attestation"]
    if proof is not None:
        validate_proof(proof, inputs, generation, expected, policy)
    if current["effective_status"] == "succeeded":
        dto.need(proof is not None and view["result"] is not None, "publication_job_result_missing")
        dto.need(instant(proof["recorded_utc"]) <= instant(current["events"][-1]["recorded_utc"]),
                 "publication_job_result_before_proof")
        value = _payload(view["result"], current["state"]["result_object_id"])
        dto.need(dto.canonical(value) == dto.canonical(result(spec, inputs, proof)),
                 "publication_job_result_changed")
    else:
        dto.need(view["result"] is None, "publication_job_uncommitted_result")
    return view


def validate_progress(previous, current, expected_spec, authority_id):
    """Accept only a nonregressing extension of the last durable snapshot."""
    old = validate_snapshot(previous, expected_spec, authority_id)
    new = validate_snapshot(current, expected_spec, authority_id)
    dto.need(old["registered_utc"] == new["registered_utc"]
        and instant(new["observed_utc"]) >= instant(old["observed_utc"])
        and len(new["events"]) >= len(old["events"])
        and dto.canonical(new["events"][:len(old["events"])]) == dto.canonical(old["events"]),
        "publication_job_history_regressed")
    return new
