"""Shared machinery must not pool unrelated tasks or promote imported evidence."""

from dataclasses import replace

import pytest

from selflearn_core.registry import SqliteRegistry
from selflearn_core.scoring import TaskSpec, evaluate_tasks, fit_calibrator
from selflearn_core.store import SqliteStore
from selflearn_core.types import Outcome, Prediction, ResolvedPrediction
from selflearn_core.updater import Change, Level1Calibration, UpdatePolicy


def records(task, *, hit, n=6, imported=False):
    result = []
    for i in range(n):
        meta = {"_terminal": {"provenance": "unverified_import"}} if imported else {}
        prediction = Prediction(id=f"p{i}", ts=1000+i, model_version="fixture", task=task,
                                features={}, prediction={}, horizon_s=10,
                                confidence=.8, cohort="fixture", meta=meta)
        result.append(ResolvedPrediction(prediction, Outcome(prediction.id, 1100+i, {"hit": hit})))
    return result


def test_mixed_task_calibration_requires_an_explicit_choice():
    nfl = records("nfl.game", hit=False)
    trading = records("scanner", hit=True)
    with pytest.raises(ValueError, match="mixed-task"):
        fit_calibrator(iter(nfl+trading))
    assert fit_calibrator(nfl+trading, task="scanner").apply(.8) == 1
    assert fit_calibrator(nfl+trading, task="nfl.game").apply(.8) == 0
    assert fit_calibrator(trading).apply(.8) == 1  # existing single-task contract


def test_shared_evaluator_keeps_tasks_scores_and_proposals_separate():
    reports = evaluate_tasks(
        records("nfl.game", hit=False)+records("scanner", hit=True),
        [TaskSpec("nfl.game", "idea", ("all",)), TaskSpec("scanner", "idea", ("all",))],
        evidence_verifier=lambda r: True, policy=Level1Calibration(min_resolved=5),
    )
    assert [r.task for r in reports] == ["nfl.game", "scanner"]
    for report, expected in zip(reports, [0, 1]):
        assert report.observed_count == report.eligible_count == 6
        hit = next(s for s in report.eligible_scores if s.metric == "hit_rate" and s.cohort is None)
        assert hit.n == 6 and hit.value == expected
        assert len(report.proposals) == 1 and report.proposals[0].task == report.task


def test_default_is_descriptive_monitoring_even_with_self_asserted_verified_metadata():
    rs = records("scanner", hit=True)
    rs = [replace(r, prediction=replace(r.prediction, meta={"verified": True})) for r in rs]
    report, = evaluate_tasks(rs, [TaskSpec("scanner", "idea")], policy=Level1Calibration(min_resolved=5))
    assert report.observed_count == 6 and report.descriptive_scores
    assert report.eligible_count == 0 and report.eligible_scores == report.proposals == ()
    verified, = evaluate_tasks(rs, [TaskSpec("scanner", "idea")], evidence_verifier=lambda r: True)
    assert verified.eligible_count == 6 and verified.proposals == ()  # L0 remains the default


def test_imported_predictions_never_become_learning_evidence_even_if_verifier_accepts():
    report, = evaluate_tasks(records("scanner", hit=True, imported=True),
                             [TaskSpec("scanner", "idea")], evidence_verifier=lambda r: True,
                             policy=Level1Calibration(min_resolved=5))
    assert report.observed_count == 6
    assert report.descriptive_scores and report.eligible_scores == report.proposals == ()
    assert report.eligible_count == 0


def test_duplicate_or_cross_linked_evidence_refused():
    rs = records("scanner", hit=True)
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_tasks(rs+[rs[0]], [TaskSpec("scanner", "idea")])
    bad = replace(rs[0], outcome=replace(rs[0].outcome, prediction_id="nfl-record"))
    with pytest.raises(ValueError, match="mismatched"):
        evaluate_tasks([bad], [TaskSpec("scanner", "idea")])


def test_independent_verifier_errors_do_not_turn_into_acceptance():
    def broken(record):
        raise RuntimeError("private upstream diagnostic")
    with pytest.raises(ValueError, match="verification failed") as raised:
        evaluate_tasks(records("scanner", hit=True), [TaskSpec("scanner", "idea")], evidence_verifier=broken)
    assert "private" not in str(raised.value)


def test_policy_cannot_redirect_trading_evidence_into_nfl_proposal():
    class WrongPolicy(UpdatePolicy):
        level = 1
        def propose(self, scores):
            return [Change("nfl.game", "calibrate", {}, "wrong domain")]
    with pytest.raises(ValueError, match="another task"):
        evaluate_tasks(records("scanner", hit=True), [TaskSpec("scanner", "idea")],
                       evidence_verifier=lambda r: True, policy=WrongPolicy())


def test_evaluation_does_not_promote_and_registry_changes_stay_task_scoped(tmp_path):
    path = str(tmp_path / "learning.sqlite")
    SqliteStore(path).init_schema()
    registry = SqliteRegistry(path, now=lambda: 1000)
    nfl = registry.register("nfl.game", {"version": "nfl"})
    trading = registry.register("scanner", {"version": "trading"})
    registry.promote(nfl)
    evaluate_tasks(records("scanner", hit=True), [TaskSpec("scanner", "idea")],
                   evidence_verifier=lambda r: True, policy=Level1Calibration(min_resolved=5))
    assert registry.status(nfl) == "live"
    assert registry.status(trading) == "candidate"
    registry.promote(trading)
    assert registry.status(nfl) == registry.status(trading) == "live"
