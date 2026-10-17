"""Read-only binding of a real owner journal to reproducible NFL shadow evidence.

The injected journal is trusted application configuration, never an uploaded
snapshot or URL. This adapter opens no connections, appends no events and cannot
activate a model. A reconstructed walk-forward POLICY is not prospective proof
for its final fixed artifact. Source/label completeness and actual NFL serving
remain unverified even when every content and numerical comparison succeeds.
"""
from __future__ import annotations

from datetime import datetime, timezone
import inspect
import json
import math
import re
import time

from .. import owner_intents as owner
from ..probability_receipts import fingerprint, instant
from . import nfl_calibration as calibration

NS = {"domain": "nfl", "task": "nfl.parlay_leg", "producer_id": "nfl2026"}
ROLES = frozenset(("candidate", "report", "artifact", "policy", "context", "current", "evaluation_inputs"))
MAX_BYTES, MAX_NODES, MAX_RECEIPTS, MAX_REVISIONS = 16 * 1024 * 1024, 500000, 2000, 8000
ESCAPES = re.compile(r'["\\\x00-\x1f\x7f-\U0010ffff]')
FLAGS = {"owner_authorization_verified": False, "source_authority_verified": False,
         "label_authority_verified": False, "fixed_artifact_prospective_verified": False,
         "current_serving_verified": False, "model_adoption": False,
         "working_learning_loop": False, "activation": False, "release_authorized": False,
         "writes": False, "network_implemented_by_adapter": False}
ERRORS = frozenset("""owner_evidence_work_limit invalid_owner_evidence_json invalid_owner_evidence_shape
owner_evidence_hash_mismatch owner_evidence_runtime_unavailable owner_evidence_history_unavailable
owner_evidence_history_mismatch owner_evidence_actor_mismatch owner_evidence_engine_code_mismatch
owner_evidence_no_current_intent owner_evidence_current_intent_revoked owner_evidence_manifest_not_current
owner_evidence_object_mismatch owner_evidence_manifest_mismatch owner_evidence_role_partition_mismatch
owner_evidence_artifact_mismatch owner_evidence_report_mismatch owner_evidence_contract_mismatch
owner_evidence_runtime_pin_mismatch owner_evidence_domain_pin_mismatch owner_evidence_invalid_age_policy
owner_evidence_clock_or_expiry_hold owner_evidence_input_mismatch owner_evidence_evaluation_replay_mismatch
owner_evidence_head_changed owner_evidence_runtime_changed""".split())


class OwnerEvidenceError(ValueError):
    """Static domain codes only; never backend diagnostics or input content."""


def _need(condition, code):
    if not condition:
        raise OwnerEvidenceError(code)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _copy(value):
    """Count expanded canonical bytes before constructing a detached copy."""
    used = nodes = 0
    def charge(n):
        nonlocal used
        used += n
        _need(used <= MAX_BYTES, "owner_evidence_work_limit")
    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        _need(nodes <= MAX_NODES and depth <= 24, "owner_evidence_work_limit")
        if type(item) is dict:
            charge(2 + max(0, len(item) - 1) + len(item))
            for key, child in item.items():
                _need(type(key) is str, "invalid_owner_evidence_json")
                visit(key, depth + 1); visit(child, depth + 1)
        elif type(item) is list:
            charge(2 + max(0, len(item) - 1))
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            charge(2 + len(item))
            for match in ESCAPES.finditer(item):
                char = match.group(); code = ord(char)
                _need(code != 0 and not 0xD800 <= code <= 0xDFFF, "invalid_owner_evidence_json")
                charge(1 if char in '\\"\b\f\n\r\t' else 11 if code > 0xFFFF else 5)
        else:
            _need(item is None or type(item) in (bool, int, float), "invalid_owner_evidence_json")
            _need(not (type(item) is int and item.bit_length() > 1024
                       or type(item) is float and not math.isfinite(item)), "invalid_owner_evidence_json")
            charge(len(_canonical(item)))
    visit(value, 0)
    return json.loads(_canonical(value))


def _keys(value, names):
    _need(type(value) is dict and set(value) == set(names), "invalid_owner_evidence_shape")


def _sealed(value, key):
    return {**value, key: fingerprint(value)}


def _hashed(value, key):
    _need(type(value) is dict and type(value.get(key)) is str
          and value[key] == fingerprint({k: v for k, v in value.items() if k != key}), "owner_evidence_hash_mismatch")
    owner.digest(value[key])


def code_identity():
    """Pin this adapter and actual fitter source, not a caller's engine label.

    This is a conservative local source identity, not authentication of deployed
    execution, the interpreter, transitive dependencies or the backend.
    """
    return fingerprint({"profile": "nfl_owner_shadow_evidence_v1",
                        "adapter": inspect.getsource(inspect.getmodule(code_identity)),
                        "fitter": inspect.getsource(calibration)})


def artifact(candidate):
    calibration.validate_candidate(candidate)
    return _sealed({"version": 1, "kind": "nfl.calibrator_artifact.v1",
        "candidate_id": candidate["candidate_id"],
        **{k: candidate[k] for k in ("base_model_version", "receipt_policy", "transform", "coefficients")}}, "artifact_id")


def evaluation_policy_id(report):
    return fingerprint({"evaluation_kind": report["evaluation_kind"],
        "shared_gate": {k: v for k, v in report["shared_shadow_numeric_gate"].items() if k not in ("passed", "failures")},
        "proposed_gate": {k: v for k, v in report["proposed_shared_return_gate"].items() if k not in ("passed", "failures")},
        "current_nfl_rules_reference": report["current_nfl_rules_reference"]})


def prepare_manifest(documents):
    """Prepare strict plain-JSON objects/pins; never stage or approve anything.

    In-process tuple conveniences must first take the same explicit JSON round
    trip as a stored/API report. Arbitrary Python objects are not normalized.
    """
    documents = _copy(documents); _keys(documents, ROLES)
    roles = {key: fingerprint(value) for key, value in documents.items()}
    manifest = {"version": 1, "kind": "nfl.owner_shadow_evidence_manifest.v1", "namespace": dict(NS), "roles": roles}
    manifest_id = fingerprint(manifest)
    return {"manifest_id": manifest_id, "manifest": manifest,
            "objects": {manifest_id: manifest, **{roles[k]: v for k, v in documents.items()}}}


class NFLOwnerEvidenceReader:
    """Explicit trusted port; no API/environment discovery or serving consumer.

    `runtime_reader` is configured program code returning expected context/base/
    code/current IDs, not an input JSON field. Matching it is compatibility with
    that configuration, NOT a verified read of the actual NFL serving state.
    A second read detects changes during verification but does not create an
    atomic publication/adoption transaction or remove the final read-use race.
    """
    def __init__(self, journal, *, authority_id, actor_ref, runtime_reader, clock=None):
        self.journal = journal
        self.authority_id, self.actor_ref = owner.digest(authority_id), owner.actor(actor_ref)
        _need(callable(runtime_reader), "owner_evidence_runtime_unavailable")
        self.runtime_reader = runtime_reader
        self.clock = clock or (lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))

    def _head(self):
        value = owner.snapshot(self.journal.read_owner_intents(dict(NS)))
        _need(type(value) is dict, "owner_evidence_history_unavailable")
        expected = owner.complete_snapshot(value["events"], authority_id=self.authority_id,
            expected_namespace=NS, watermark=value["watermark"], observed_utc=value["observed_utc"])
        _need(_canonical(value) == _canonical(expected), "owner_evidence_history_mismatch")
        _need(all(e["actor_ref"] == self.actor_ref for e in value["events"]), "owner_evidence_actor_mismatch")
        return expected

    def verify(self, manifest_id):
        """Return a held read-only diagnostic, optionally with reproduced evidence.

        Ports can perform their own bounded IO. Cancellation propagates. No
        backend exception text or untrusted reason string enters the result.
        Even a successful bind remains held for independent outcome authority,
        fixed-artifact prospective validation and an atomic NFL consumer.
        """
        result = {"version": 1, "kind": "nfl.owner_shadow_evidence_diagnostic.v1", "status": "held",
            "manifest_id": None, "head_event_id": None, "owner_head_content_verified": False,
            "domain_pins_bound": False, "evaluation_reexecuted": False,
            "runtime_configuration_matched": False, "reason_codes": [], **FLAGS}
        try:
            owner.digest(manifest_id); started = instant(self.clock()); local_code = code_identity()
            expected_runtime = _copy(self.runtime_reader())
            _keys(expected_runtime, {"base_model_version", "nfl_code_id", "engine_code_id", "current_id", "context_id"})
            for value in expected_runtime.values():
                owner.digest(value)
            _need(expected_runtime["engine_code_id"] == local_code, "owner_evidence_engine_code_mismatch")
            head = self._head(); head_checked = instant(self.clock())
            _need(head["events"], "owner_evidence_no_current_intent")
            event = head["events"][-1]; command = event["command"]; pins = command["pins"]
            _need(command["action"] in ("approve", "rollback"), "owner_evidence_current_intent_revoked")
            _need(manifest_id in pins["evidence_object_ids"], "owner_evidence_manifest_not_current")
            recorded = instant(event["recorded_utc"])
            objects, total_bytes = {}, 0
            for identity in pins["evidence_object_ids"]:
                record = self.journal.get_object(identity)
                _keys(record, {"object_id", "kind", "payload", "received_utc"})
                payload = _copy(record["payload"])
                total_bytes += len(_canonical(payload))
                _need(total_bytes <= 4 * MAX_BYTES, "owner_evidence_work_limit")
                _need(record["object_id"] == identity and record["kind"] == "source_snapshot"
                      and fingerprint(payload) == identity and instant(record["received_utc"]) <= recorded,
                      "owner_evidence_object_mismatch")
                objects[identity] = payload
            manifest = objects[manifest_id]
            _keys(manifest, {"version", "kind", "namespace", "roles"})
            _need(type(manifest["version"]) is int and manifest["version"] == 1
                  and manifest["kind"] == "nfl.owner_shadow_evidence_manifest.v1" and manifest["namespace"] == NS,
                  "owner_evidence_manifest_mismatch")
            _keys(manifest["roles"], ROLES)
            _need(set(objects) == {manifest_id, *manifest["roles"].values()}, "owner_evidence_role_partition_mismatch")
            documents = {role: objects[identity] for role, identity in manifest["roles"].items()}
            candidate, report, policy, context, current, inputs = [documents[k] for k in
                ("candidate", "report", "policy", "context", "current", "evaluation_inputs")]
            calibration.validate_candidate(candidate, expected_base_version=expected_runtime["base_model_version"])
            _keys(policy, "version kind base_model_version nfl_code_id engine_code_id evaluation_policy_id max_candidate_age_seconds max_authority_age_seconds policy_id".split())
            _keys(context, "version kind season week forecast_created_utc first_kickoff_utc base_model_version source_snapshot_id nfl_code_id engine_code_id context_id".split())
            _keys(current, "version kind base_model_version adjustment calibrator_version artifact_id materialization_id current_id".split())
            _hashed(report, "report_id"); _hashed(policy, "policy_id"); _hashed(context, "context_id"); _hashed(current, "current_id")
            _need(_canonical(documents["artifact"]) == _canonical(artifact(candidate)), "owner_evidence_artifact_mismatch")
            _need(report["candidate"] == candidate and report["evaluation_kind"] ==
                "refitting_policy_walk_forward_not_fixed_artifact_prospective_validation", "owner_evidence_report_mismatch")
            _need(policy["kind"] == "nfl.calibrator_materializer_policy.v1" and type(policy["version"]) is int and policy["version"] == 1
                and context["kind"] == "nfl.future_generation_context.v1" and type(context["version"]) is int and context["version"] == 1
                and current["kind"] == "nfl.current_calibrator_claim.v1" and type(current["version"]) is int and current["version"] == 1,
                "owner_evidence_contract_mismatch")
            _need(type(context["season"]) is int and 2000 <= context["season"] <= 2100
                  and type(context["week"]) is int and 1 <= context["week"] <= 18,
                  "owner_evidence_contract_mismatch")
            owner.digest(context["source_snapshot_id"])
            _need(current["calibrator_version"] == fingerprint(current["adjustment"])
                  and (current["artifact_id"] is None) == (current["materialization_id"] is None), "owner_evidence_contract_mismatch")
            if current["adjustment"] is not None:
                calibration.serve_base(.5, current["adjustment"])
            for key in ("artifact_id", "materialization_id"):
                if current[key] is not None:
                    owner.digest(current[key])
            for key in ("base_model_version", "nfl_code_id", "engine_code_id"):
                _need(context[key] == policy[key] == expected_runtime[key], "owner_evidence_runtime_pin_mismatch")
            _need(current["base_model_version"] == expected_runtime["base_model_version"]
                and current["current_id"] == expected_runtime["current_id"] and context["context_id"] == expected_runtime["context_id"],
                "owner_evidence_runtime_pin_mismatch")
            expected_pins = {"candidate_id": candidate["candidate_id"], "artifact_id": documents["artifact"]["artifact_id"],
                "evaluation_report_id": report["report_id"], "policy_id": policy["policy_id"], "generation_context_id": context["context_id"],
                "expected_previous_current_id": current["current_id"], "evidence_object_ids": sorted(objects)}
            _need(pins == expected_pins and policy["evaluation_policy_id"] == evaluation_policy_id(report), "owner_evidence_domain_pin_mismatch")
            created, kickoff = instant(context["forecast_created_utc"]), instant(context["first_kickoff_utc"])
            age, candidate_age = policy["max_authority_age_seconds"], policy["max_candidate_age_seconds"]
            _need(type(age) is int and 1 <= age <= 3600 and type(candidate_age) is int and 1 <= candidate_age <= 90 * 86400,
                  "owner_evidence_invalid_age_policy")
            _need(started <= head_checked and recorded <= head_checked <= created < kickoff
                  and instant(candidate["fit_as_of_utc"]) <= recorded
                  and 0 <= (head_checked - instant(head["observed_utc"])).total_seconds() <= age
                  and 0 <= (head_checked - instant(candidate["fit_as_of_utc"])).total_seconds() <= candidate_age
                  and instant(event["effective_from_utc"]) <= created < instant(command["expires_utc"]),
                  "owner_evidence_clock_or_expiry_hold")
            _keys(inputs, {"version", "kind", "receipts", "source_receipts", "revisions", "generation_records",
                           "as_of_utc", "population", "bootstrap_replicates"})
            _need(type(inputs["version"]) is int and inputs["version"] == 1 and inputs["kind"] == "nfl.owner_shadow_evaluation_input.v1",
                  "owner_evidence_input_mismatch")
            for key, limit in (("receipts", MAX_RECEIPTS), ("source_receipts", MAX_RECEIPTS), ("revisions", MAX_REVISIONS)):
                _need(type(inputs[key]) is list and len(inputs[key]) <= limit, "owner_evidence_work_limit")
            _need(inputs["generation_records"] is None or type(inputs["generation_records"]) is list and len(inputs["generation_records"]) <= 256,
                  "owner_evidence_work_limit")
            _need(type(inputs["bootstrap_replicates"]) is int and 200 <= inputs["bootstrap_replicates"] <= 2000,
                  "owner_evidence_work_limit")
            replayed = calibration.walk_forward_report(inputs["receipts"], inputs["source_receipts"], inputs["revisions"],
                as_of_utc=inputs["as_of_utc"], population=inputs["population"],
                bootstrap_replicates=inputs["bootstrap_replicates"], generation_records=inputs["generation_records"])
            _need(_canonical(replayed) == _canonical(report), "owner_evidence_evaluation_replay_mismatch")
            final_head, final_runtime = self._head(), _copy(self.runtime_reader())
            for key in ("authority_id", "namespace", "watermark", "head_event_id", "head_sequence", "events"):
                _need(_canonical(head[key]) == _canonical(final_head[key]), "owner_evidence_head_changed")
            finished = instant(self.clock())
            _need(final_runtime == expected_runtime and code_identity() == local_code, "owner_evidence_runtime_changed")
            _need(started <= finished <= created
                  and instant(head["observed_utc"]) <= instant(final_head["observed_utc"])
                  and 0 <= (finished - instant(final_head["observed_utc"])).total_seconds() <= age,
                  "owner_evidence_clock_or_expiry_hold")
            reasons = ["fixed_artifact_prospective_evidence_missing", "independent_source_and_label_authority_unverified",
                       "atomic_NFL_adoption_consumer_missing", "owner_authority_write_capacity_unverified"]
            if head["head_sequence"] >= owner.MAX_EVENTS:
                reasons.append("owner_authority_event_capacity_exhausted")
            return _sealed({**result, "manifest_id": manifest_id, "head_event_id": head["head_event_id"],
                "owner_head_content_verified": True, "domain_pins_bound": True, "evaluation_reexecuted": True,
                "runtime_configuration_matched": True, "candidate_id": candidate["candidate_id"], "report_id": report["report_id"],
                "evaluation_policy_id": policy["evaluation_policy_id"], "evaluation_input_id": manifest["roles"]["evaluation_inputs"],
                "owner_watermark": head["watermark"], "checked_utc": finished.isoformat().replace("+00:00", "Z"), "reason_codes": reasons,
                "numeric_gate_passed": report["proposed_shared_return_gate"]["passed"],
                "authority_basis": "configured_single_owner_journal_content_not_atomic_serving_authority"}, "diagnostic_id")
        except OwnerEvidenceError as error:
            reason = error.args[0] if type(error) is OwnerEvidenceError and len(error.args) == 1 else None
            reason = reason if type(reason) is str and reason in ERRORS else "owner_evidence_unavailable_or_invalid"
            return _sealed({**result, "reason_codes": [reason]}, "diagnostic_id")
        except Exception:
            return _sealed({**result, "reason_codes": ["owner_evidence_unavailable_or_invalid"]}, "diagnostic_id")
