"""Real local journal binding; no hosted, source-authentication or adoption proof."""
import copy
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch

import pytest

from selflearn_core import owner_intents as owner
from selflearn_core.adapters import nfl_owner_evidence as evidence
from selflearn_core.adapters.nfl_calibration import walk_forward_report
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from test_nfl_calibration import corpus

AUTHORITY, ACTOR = fingerprint("configured-nfl-owner-evidence-test"), "configured-local-owner"


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def seal(value, key):
    return {**value, key: fingerprint(value)}


@pytest.fixture(scope="module")
def experiment():
    receipts, sources, revisions = corpus(n=110)
    inputs = {"version": 1, "kind": "nfl.owner_shadow_evaluation_input.v1", "receipts": receipts,
        "source_receipts": sources, "revisions": revisions, "generation_records": None,
        "as_of_utc": "2026-10-15T00:00:00Z", "population": "eligible_pool", "bootstrap_replicates": 200}
    report = walk_forward_report(receipts, sources, revisions, as_of_utc=inputs["as_of_utc"],
                                population=inputs["population"], bootstrap_replicates=200)
    assert report["candidate"] is not None
    # The immutable object store receives JSON, not the fitter's in-process
    # tuple-valued fit-week convenience representation. Its fingerprint uses
    # this same JSON encoding; no score/probability or identity is changed.
    return inputs, json.loads(json.dumps(report, allow_nan=False))


def documents(experiment):
    inputs, report = copy.deepcopy(experiment)
    candidate = report["candidate"]; base = candidate["base_model_version"]
    now = datetime.now(timezone.utc)
    current = seal({"version": 1, "kind": "nfl.current_calibrator_claim.v1", "base_model_version": base,
        "adjustment": None, "calibrator_version": fingerprint(None), "artifact_id": None,
        "materialization_id": None}, "current_id")
    policy = seal({"version": 1, "kind": "nfl.calibrator_materializer_policy.v1", "base_model_version": base,
        "nfl_code_id": fingerprint("configured-local-nfl-code-claim"), "engine_code_id": evidence.code_identity(),
        "evaluation_policy_id": evidence.evaluation_policy_id(report), "max_candidate_age_seconds": 30 * 86400,
        "max_authority_age_seconds": 120}, "policy_id")
    context = seal({"version": 1, "kind": "nfl.future_generation_context.v1", "season": 2026, "week": 6,
        "forecast_created_utc": stamp(now + timedelta(hours=1)), "first_kickoff_utc": stamp(now + timedelta(days=1)),
        "base_model_version": base, "source_snapshot_id": fingerprint("unverified-future-source-claim"),
        "nfl_code_id": policy["nfl_code_id"], "engine_code_id": policy["engine_code_id"]}, "context_id")
    return {"candidate": candidate, "report": report, "artifact": evidence.artifact(candidate),
        "policy": policy, "context": context, "current": current, "evaluation_inputs": inputs}


def command(docs, bundle, *, head=None, action="approve"):
    return owner.seal_command({"version": 1, "kind": "selflearn.owner_intent_command.v1", "namespace": dict(evidence.NS),
        "action": action, "expected_head_event_id": head,
        "pins": {"candidate_id": docs["candidate"]["candidate_id"], "artifact_id": docs["artifact"]["artifact_id"],
            "evaluation_report_id": docs["report"]["report_id"], "policy_id": docs["policy"]["policy_id"],
            "generation_context_id": docs["context"]["context_id"],
            "expected_previous_current_id": docs["current"]["current_id"], "evidence_object_ids": sorted(bundle["objects"])},
        "effective_from_utc": None,
        "expires_utc": None if action == "revoke" else docs["context"]["first_kickoff_utc"],
        "reason": "Record local shadow-evidence review only; never model activation"}, authority_id=AUTHORITY)


def setup(tmp_path, experiment, mutate=None):
    docs = documents(experiment)
    if mutate:
        mutate(docs)
    bundle = evidence.prepare_manifest(docs)
    journal = OwnerIntentJournal(tmp_path / "owner.sqlite", authority_id=AUTHORITY, actor_ref=ACTOR)
    for oid, value in bundle["objects"].items():
        journal.append_object("source_snapshot", oid, value)
    original = command(docs, bundle); ack = journal.append_owner_intent(original)
    runtime = {k: docs["context"][k] for k in ("base_model_version", "nfl_code_id", "engine_code_id", "context_id")}
    runtime["current_id"] = docs["current"]["current_id"]
    reader = evidence.NFLOwnerEvidenceReader(journal, authority_id=AUTHORITY, actor_ref=ACTOR,
                                            runtime_reader=lambda: copy.deepcopy(runtime))
    return docs, bundle, journal, original, ack, runtime, reader


def test_actual_owner_history_objects_and_evaluation_are_bound_but_never_activate(tmp_path, experiment):
    docs, bundle, journal, original, ack, runtime, reader = setup(tmp_path, experiment)
    try:
        before = fingerprint(docs)
        result = reader.verify(bundle["manifest_id"])
        assert result["owner_head_content_verified"] is True, result
        assert result["domain_pins_bound"] is True and result["evaluation_reexecuted"] is True
        assert result["runtime_configuration_matched"] is True and result["head_event_id"] == ack["event_id"]
        assert result["status"] == "held" and all(result[k] is False for k in evidence.FLAGS)
        assert "fixed_artifact_prospective_evidence_missing" in result["reason_codes"]
        assert "owner_authority_write_capacity_unverified" in result["reason_codes"]
        assert fingerprint(docs) == before
        assert journal.receipts() == [] and journal.latest_resolutions() == [] and journal.generations() == []
        assert journal.read_owner_intents(evidence.NS)["head_sequence"] == 1
        assert result["diagnostic_id"] == fingerprint({k: v for k, v in result.items() if k != "diagnostic_id"})
    finally:
        journal.close()


def test_original_retry_cannot_resurrect_approval_after_actual_revocation(tmp_path, experiment):
    docs, bundle, journal, original, ack, _, reader = setup(tmp_path, experiment)
    try:
        journal.append_owner_intent(command(docs, bundle, head=ack["event_id"], action="revoke"))
        assert journal.append_owner_intent(original) == ack
        result = reader.verify(bundle["manifest_id"])
        assert result["reason_codes"] == ["owner_evidence_current_intent_revoked"]
        assert result["domain_pins_bound"] is False and result["activation"] is False
    finally:
        journal.close()


def test_rehashed_better_report_is_reexecuted_not_trusted(tmp_path, experiment):
    def forge(docs):
        report = docs["report"]
        report["metrics"]["candidate_log_loss"] = 0.0
        report["report_id"] = fingerprint({k: v for k, v in report.items() if k != "report_id"})
    docs, bundle, journal, _, _, _, reader = setup(tmp_path, experiment, forge)
    try:
        with patch.object(evidence.calibration, "walk_forward_report", wraps=walk_forward_report) as replay:
            result = reader.verify(bundle["manifest_id"])
        assert replay.call_count == 1
        assert result["reason_codes"] == ["owner_evidence_evaluation_replay_mismatch"]
        assert result["evaluation_reexecuted"] is False and result["activation"] is False
    finally:
        journal.close()


def test_head_changed_during_real_evaluation_never_uses_cached_approval(tmp_path, experiment):
    docs, bundle, journal, _, ack, _, reader = setup(tmp_path, experiment)
    def replay(*args, **kw):
        result = walk_forward_report(*args, **kw)
        journal.append_owner_intent(command(docs, bundle, head=ack["event_id"], action="revoke"))
        return result
    try:
        with patch.object(evidence.calibration, "walk_forward_report", side_effect=replay):
            result = reader.verify(bundle["manifest_id"])
        assert result["reason_codes"] == ["owner_evidence_head_changed"]
        assert result["owner_head_content_verified"] is False and result["activation"] is False
    finally:
        journal.close()


def test_callback_errors_are_static_and_cancellation_propagates(tmp_path, experiment):
    _, bundle, journal, _, _, _, reader = setup(tmp_path, experiment)
    try:
        for failure in (RuntimeError("PRIVATE-BACKEND-DETAIL"), evidence.OwnerEvidenceError("PRIVATE-BACKEND-DETAIL")):
            with patch.object(journal, "read_owner_intents", side_effect=failure):
                result = reader.verify(bundle["manifest_id"])
            assert result["reason_codes"] == ["owner_evidence_unavailable_or_invalid"]
            assert "PRIVATE-BACKEND-DETAIL" not in json_text(result)
        with patch.object(journal, "read_owner_intents", side_effect=KeyboardInterrupt()):
            with pytest.raises(KeyboardInterrupt):
                reader.verify(bundle["manifest_id"])
    finally:
        journal.close()


def json_text(value):
    import json
    return json.dumps(value, sort_keys=True)


def test_expanded_alias_bytes_are_refused_before_full_serialization():
    small = {"payload": "x" * (1024 * 1024)}
    with patch.object(evidence, "_canonical", side_effect=AssertionError("must not allocate expanded JSON")):
        with pytest.raises(evidence.OwnerEvidenceError, match="work_limit"):
            evidence._copy([small] * 17)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), {True: 1}, object(), "\x00", "\ud800"])
def test_invalid_plain_json_never_acquires_an_evidence_identity(value):
    with pytest.raises(evidence.OwnerEvidenceError):
        evidence._copy(value)


@pytest.mark.parametrize("role,field,value", [
    ("context", "season", True), ("context", "week", 0), ("context", "source_snapshot_id", "bad"),
    ("policy", "version", True), ("policy", "max_authority_age_seconds", 0),
    ("policy", "max_candidate_age_seconds", 100 * 86400),
    ("policy", "evaluation_policy_id", fingerprint("other-policy")),
    ("current", "calibrator_version", fingerprint("other-coefficients")),
    ("artifact", "extra", "unreviewed-field"), ("evaluation_inputs", "bootstrap_replicates", True),
    ("evaluation_inputs", "population", "unsupported-game-task"),
])
def test_rehashed_and_genuinely_recorded_bad_domain_objects_are_held(tmp_path, experiment, role, field, value):
    def change(docs):
        docs[role][field] = value
        identity = {"context": "context_id", "policy": "policy_id", "current": "current_id"}.get(role)
        if identity:
            docs[role][identity] = fingerprint({k: v for k, v in docs[role].items() if k != identity})
    _, bundle, journal, _, _, _, reader = setup(tmp_path, experiment, change)
    try:
        result = reader.verify(bundle["manifest_id"])
        assert result["domain_pins_bound"] is False and result["evaluation_reexecuted"] is False, result
        assert all(result[k] is False for k in evidence.FLAGS)
        assert len(result["reason_codes"]) == 1
    finally:
        journal.close()


def test_changed_runtime_and_wrong_configured_actor_are_not_current_serving_authority(tmp_path, experiment):
    _, bundle, journal, _, _, runtime, reader = setup(tmp_path, experiment)
    try:
        first = copy.deepcopy(runtime); second = {**runtime, "current_id": fingerprint("changed-incumbent")}
        with patch.object(reader, "runtime_reader", side_effect=[first, second]):
            result = reader.verify(bundle["manifest_id"])
        assert result["reason_codes"] == ["owner_evidence_runtime_changed"]
        reader.actor_ref = "other-configured-actor"
        assert reader.verify(bundle["manifest_id"])["reason_codes"] == ["owner_evidence_actor_mismatch"]
        reader.actor_ref = ACTOR
        assert reader.verify(fingerprint("non-current-manifest"))["reason_codes"] == ["owner_evidence_manifest_not_current"]
    finally:
        journal.close()


def test_real_object_corruption_and_missing_immutable_object_are_never_numeric_evidence(tmp_path, experiment):
    _, bundle, journal, _, _, _, reader = setup(tmp_path, experiment)
    get = journal.get_object
    try:
        def changed(oid):
            record = get(oid)
            if oid == bundle["manifest_id"]:
                record["payload"]["namespace"]["domain"] = "foreign-domain"
            return record
        with patch.object(journal, "get_object", side_effect=changed):
            result = reader.verify(bundle["manifest_id"])
        assert result["reason_codes"] == ["owner_evidence_object_mismatch"]
        with patch.object(journal, "get_object", return_value=None):
            result = reader.verify(bundle["manifest_id"])
        assert result["domain_pins_bound"] is False
        assert journal.read_owner_intents(evidence.NS)["head_sequence"] == 1
    finally:
        journal.close()


def test_complete_owner_snapshot_and_future_context_cannot_be_forged_by_old_success(tmp_path, experiment):
    docs, bundle, journal, _, _, _, reader = setup(tmp_path, experiment)
    get = journal.read_owner_intents
    try:
        def truncated(namespace):
            result = get(namespace); result["head_sequence"] += 1
            return result
        with patch.object(journal, "read_owner_intents", side_effect=truncated):
            assert reader.verify(bundle["manifest_id"])["reason_codes"] == ["owner_evidence_history_mismatch"]
        reader.clock = lambda: docs["context"]["first_kickoff_utc"]
        result = reader.verify(bundle["manifest_id"])
        assert result["reason_codes"] == ["owner_evidence_clock_or_expiry_hold"]
    finally:
        journal.close()
