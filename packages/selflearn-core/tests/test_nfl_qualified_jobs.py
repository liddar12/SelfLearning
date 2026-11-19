"""Executed durable and bounded jobs; only synthetic forecasts/outcomes."""
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from selflearn_core import compute_process as process
from selflearn_core.adapters import nfl_bounded_evaluation as bounded
from selflearn_core.adapters import nfl_evaluation_job as jobs
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from test_nfl_evaluation_job import AUTHORITY, definition
from test_nfl_qualified_shadow import AS_OF, qualified_corpus


def staged(tmp_path, *, subprocess=False, legacy_labels=False):
    receipts, sources, old, qualified = qualified_corpus()
    inputs = {"version": 1, "kind": "nfl.shadow_job_inputs.v1", "receipts": receipts,
              "source_receipts": sources, "revisions": old if legacy_labels else qualified,
              "generation_records": [], "as_of_utc": AS_OF}
    settings = jobs.policy(execution_profile=process.profile() if subprocess else None)
    journal = LearningJobJournal(tmp_path / "qualified-jobs.db", authority_id=AUTHORITY)
    for payload in (inputs, settings):
        journal.append_object("source_snapshot", fingerprint(payload), payload)
    spec = definition(inputs, policy_object_id=fingerprint(settings))
    journal.register_job(spec)
    return journal, spec, settings, inputs


@pytest.mark.parametrize("subprocess", [False, True])
@pytest.mark.parametrize("legacy_labels", [False, True])
def test_new_jobs_execute_qualified_policy_and_recover_exact_result(tmp_path, subprocess, legacy_labels):
    journal, spec, settings, inputs = staged(tmp_path, subprocess=subprocess, legacy_labels=legacy_labels)
    run = bounded.run_bounded_shadow_job if subprocess else jobs.run_shadow_job
    with journal:
        first = run(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="first", max_phases=1)
        assert first["status"] == "paused"
    # Reopen durable state. Recovery must not reinterpret v3 as resource-only v2.
    with LearningJobJournal(tmp_path / "qualified-jobs.db", authority_id=AUTHORITY) as fresh:
        result = run(fresh, spec["job_id"], authority_id=AUTHORITY, worker_id="resume")
        with patch.object(process, "run_worker", side_effect=AssertionError("unexpected child")), \
                patch.object(jobs.calibration, "walk_forward_report", side_effect=AssertionError("unexpected refit")):
            recovered = run(fresh, spec["job_id"], authority_id=AUTHORITY, worker_id="retry")
        assert recovered["recovered_original"] is True
        assert recovered["result_object_id"] == result["result_object_id"]
        assert recovered["result"] == result["result"]
        value, report = result["result"], result["result"]["report"]
        assert value["version"] == settings["version"] == 3
        assert value["kind"] == "nfl.shadow_job_result.v3"
        expected = jobs.calibration.walk_forward_report(inputs["receipts"], inputs["source_receipts"], inputs["revisions"],
            as_of_utc=AS_OF, population="eligible_pool", bootstrap_replicates=200, generation_records=[],
            outcome_admission=jobs.calibration.EVENT_BOUND_LABELS)
        assert report == json.loads(json.dumps(expected))
        assert report["kind"] == "nfl.my_props.walk_forward.v2"
        assert report["outcome_admission"] == jobs.calibration.EVENT_BOUND_LABELS
        assert report["captured_forecast_n"] == 330
        assert report["cohort_n"] == (0 if legacy_labels else 330)
        if legacy_labels:
            assert report["label_coverage"]["exclusion_reasons"] == {"legacy_outcome": 330}
            assert report["candidate"] is value["artifact"] is None
        else:
            assert report["candidate"]["training"]["outcome_admission"] == jobs.calibration.EVENT_BOUND_LABELS
        assert report["proposed_shared_return_gate"]["passed"] is False
        assert "committed_generation_evidence_unverified" in report["proposed_shared_return_gate"]["failures"]
        assert all(result[key] is False and value[key] is False for key in jobs.FALSE_FLAGS)
        if subprocess:
            bounded.validate_execution_receipt(value["execution"], spec=spec, report=report,
                                               profile=settings["execution_profile"])
        else:
            assert value["execution"] is None


def test_policy_version_separates_qualified_labels_from_legacy_execution_limits():
    legacy_inline = {"version": 1, "kind": "nfl.shadow_job_policy.v1", "population": "eligible_pool", "bootstrap_replicates": 200}
    legacy_bounded = {**legacy_inline, "version": 2, "kind": "nfl.shadow_job_policy.v2", "execution_profile": process.profile()}
    assert jobs.policy(outcome_admission=jobs.calibration.LEGACY_LABELS) == legacy_inline
    assert jobs.policy(outcome_admission=jobs.calibration.LEGACY_LABELS, execution_profile=process.profile()) == legacy_bounded
    strict = jobs.policy()
    assert strict == {**legacy_inline, "version": 3, "kind": "nfl.shadow_job_policy.v3",
                      "outcome_admission": jobs.calibration.EVENT_BOUND_LABELS, "execution_profile": None}
    assert len({fingerprint(value) for value in (strict, legacy_inline, legacy_bounded)}) == 3
    assert jobs.outcome_admission(legacy_bounded) == jobs.calibration.LEGACY_LABELS
    for value in ({**strict, "outcome_admission": jobs.calibration.LEGACY_LABELS},
                  {**strict, "version": 2}, {k: v for k, v in strict.items() if k != "outcome_admission"},
                  {**strict, "execution_profile": {}}, {**legacy_inline, "outcome_admission": jobs.calibration.EVENT_BOUND_LABELS}):
        with pytest.raises(ValueError):
            jobs._policy(value)


def test_new_result_cannot_relabel_a_legacy_report_or_claim_inline_execution(tmp_path):
    journal, spec, settings, inputs = staged(tmp_path, legacy_labels=True)
    with journal:
        value = jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="test")["result"]
    legacy = jobs.calibration.walk_forward_report(inputs["receipts"], inputs["source_receipts"], inputs["revisions"],
        as_of_utc=AS_OF, population="eligible_pool", bootstrap_replicates=200, generation_records=[])
    changed = deepcopy(value)
    changed["report"] = json.loads(json.dumps(legacy))
    changed["artifact"] = jobs.evidence.artifact(legacy["candidate"])
    with pytest.raises(ValueError, match="shadow_job_qualified_report_required"):
        jobs._result(changed, spec, settings)
    changed["report"].update(version=2, kind="nfl.my_props.walk_forward.v2",
                             outcome_admission=jobs.calibration.EVENT_BOUND_LABELS)
    changed["report"]["report_id"] = fingerprint({k: v for k, v in changed["report"].items() if k != "report_id"})
    with pytest.raises(ValueError, match="shadow_job_qualified_report_required"):
        jobs._result(changed, spec, settings)
    with pytest.raises(ValueError, match="shadow_job_inline_execution_mismatch"):
        jobs._result({**value, "execution": {}}, spec, settings)
    with pytest.raises(ValueError, match="shadow_job_result_policy_mismatch"):
        jobs._result(value, spec, jobs.policy(bootstrap_replicates=201))


def test_qualified_result_refuses_inconsistent_rehashed_summaries(tmp_path):
    journal, spec, settings, _ = staged(tmp_path)
    with journal:
        result = jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="test")["result"]

    def change(section, key, value):
        revised = deepcopy(result)
        target = revised["report"]
        for part in section:
            target = target[part]
        target[key] = value
        report = revised["report"]
        if section == ("candidate", "training"):
            candidate = report["candidate"]
            candidate["candidate_id"] = fingerprint({k: v for k, v in candidate.items() if k != "candidate_id"})
            revised["artifact"] = jobs.evidence.artifact(candidate)
        report["report_id"] = fingerprint({k: v for k, v in report.items() if k != "report_id"})
        return revised

    cases = (
        ((), "population", "offered_my", "report_population_mismatch"),
        ((), "automatic_promotion", True, "report_authority_claim"),
        ((), "public_serving_verified", True, "report_authority_claim"),
        ((), "publication", "verified_publication", "report_authority_claim"),
        ((), "publication_verified", True, "qualified_report_required"),
        (("current_nfl_rules_reference",), "parity_executed", True, "report_authority_claim"),
        ((), "captured_forecast_n", 329, "coverage_inconsistent"),
        (("label_coverage",), "excluded_count", 1, "coverage_inconsistent"),
        (("generation_coverage",), "verified_count", 330, "coverage_inconsistent"),
        (("folds", 0, "generation_coverage"), "missing_count", 0, "coverage_inconsistent"),
        (("metrics",), "n", 0, "coverage_inconsistent"),
        (("paired_game_interval",), "replicates", 201, "report_bootstrap_mismatch"),
        (("shared_shadow_numeric_gate",), "outcome_admission", jobs.calibration.LEGACY_LABELS, "gate_inconsistent"),
        (("proposed_shared_return_gate",), "status", "authorized", "gate_inconsistent"),
        (("proposed_shared_return_gate",), "passed", True, "gate_inconsistent"),
        (("proposed_shared_return_gate",), "failures", ["training_fold_generation_evidence_unverified"], "gate_inconsistent"),
        (("candidate", "training"), "membership_policy", "first_envelope_capture_v1", "candidate_mismatch"),
    )
    for section, key, value, reason in cases:
        with pytest.raises(ValueError, match=reason):
            jobs._result(change(section, key, value), spec, settings)

    revised = deepcopy(result)
    report = revised["report"]
    candidate = report["candidate"]
    del candidate["training"]["outcome_admission"]
    candidate["candidate_id"] = fingerprint({k: v for k, v in candidate.items() if k != "candidate_id"})
    revised["artifact"] = jobs.evidence.artifact(candidate)
    report["report_id"] = fingerprint({k: v for k, v in report.items() if k != "report_id"})
    with pytest.raises(ValueError, match="candidate_mismatch"):
        jobs._result(revised, spec, settings)


def test_qualified_inline_policy_cannot_fall_through_to_bounded_execution(tmp_path):
    journal, spec, _, _ = staged(tmp_path)
    with journal:
        with pytest.raises(ValueError, match="bounded_shadow_policy_required"):
            bounded.run_bounded_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="test")
        with pytest.raises(ValueError, match="inline_policy_cannot_claim_bounded_execution"):
            jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="test",
                                compute=bounded.BoundedNFLEvaluator(process.profile()))
        assert journal.read_job(spec["job_id"])["events"] == []


@pytest.mark.parametrize("subprocess", [False, True])
def test_completed_legacy_jobs_remain_readable_after_code_changes_without_requalification(tmp_path, subprocess):
    from test_nfl_evaluation_job import staged as legacy_inline
    from test_nfl_bounded_evaluation import staged as legacy_bounded
    _, _, old, _ = qualified_corpus()
    receipts, sources, _, _ = qualified_corpus()
    inputs = {"version": 1, "kind": "nfl.shadow_job_inputs.v1", "receipts": receipts,
              "source_receipts": sources, "revisions": old, "generation_records": None, "as_of_utc": AS_OF}
    staged_legacy = legacy_bounded(tmp_path, inputs) if subprocess else legacy_inline(tmp_path, inputs)
    journal, spec = staged_legacy[:2]
    run = bounded.run_bounded_shadow_job if subprocess else jobs.run_shadow_job
    with journal:
        result = run(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="original")
        before = journal.read_job(spec["job_id"])
        with patch.object(jobs.jobs, "code_identity", return_value=fingerprint("updated package")), \
                patch.object(process, "run_worker", side_effect=AssertionError("unexpected child")), \
                patch.object(jobs.calibration, "walk_forward_report", side_effect=AssertionError("unexpected refit")):
            # Pure stored recovery needs no bounded executor, even for old v2.
            recovered = jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="reader")
        assert recovered["result"] == result["result"]
        assert recovered["result_object_id"] == result["result_object_id"]
        assert recovered["result"]["report"]["kind"] == "nfl.my_props.walk_forward.v1"
        assert recovered["result"]["version"] == (2 if subprocess else 1)
        assert recovered["recovered_original"] is True
        assert journal.read_job(spec["job_id"])["head_event_id"] == before["head_event_id"]
        assert all(recovered[key] is False for key in jobs.FALSE_FLAGS)


def test_old_incomplete_job_cannot_resume_with_new_code(tmp_path):
    journal, spec, _, _ = staged(tmp_path)
    with journal:
        jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="first", max_phases=1)
        before = journal.read_job(spec["job_id"])
        with patch.object(jobs.jobs, "code_identity", return_value=fingerprint("updated package")), \
                pytest.raises(ValueError, match="shadow_job_code_changed"):
            jobs.run_shadow_job(journal, spec["job_id"], authority_id=AUTHORITY, worker_id="new-code")
        assert journal.read_job(spec["job_id"])["head_event_id"] == before["head_event_id"]
