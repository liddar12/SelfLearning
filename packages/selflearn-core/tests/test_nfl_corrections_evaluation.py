"""Exercise publication membership and outcome cutoffs on the real journal."""
import copy
import sqlite3
from types import SimpleNamespace

import pytest

from publication_fit_fixture import seed_publication_journal
from selflearn_core.adapters import nfl_corrections_evaluation as evaluation
from selflearn_core.adapters.nfl_corrections_fit import fit_corrections_from_journal
from selflearn_core.adapters.nfl_corrections_policy import (
    LINEAGE_ARTIFACT_SOURCE,
    CorrectionsError,
)
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal


def evaluation_policy(**changes):
    settings = {"registered_utc": "2026-08-31T12:00:00Z",
                "outcome_cutoff_utc": "2026-09-02T05:00:01Z",
                "minimum_games": 4, "minimum_weeks": 4, "minimum_position_games": 4,
                "minimum_coverage": 0.8, "minimum_adjusted_fraction": 0.2, "improvement_margin": 0.005,
                "position_loss_tolerance": 0.0, "bootstrap_replicates": 200,
                "rationale": "Synthetic control thresholds for four independent week blocks."}
    return evaluation.seal_evaluation_policy(**{**settings, **changes})


@pytest.mark.parametrize("legacy", [False, True])
def test_registration_reserves_one_candidate_using_the_database_clock(tmp_path, monkeypatch, legacy):
    fit_time = "2090-01-01T00:00:00Z"
    start = "2090-01-02T00:00:00Z"
    end = "2090-01-20T00:00:00Z"
    score_at = "2090-01-22T00:00:00Z"
    policy = evaluation_policy(registered_utc=fit_time, outcome_cutoff_utc=score_at)
    path = tmp_path / "registration.sqlite"
    with NFLPublicationJournal(path) as journal:
        seed_publication_journal(journal)
        artifact = fit_corrections_from_journal(
            journal, trained_through=fit_time, fitted_at=fit_time, expires_at=end, artifact_version=2,
        )
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: fit_time)
        options = {"start_utc": start, "end_utc": end}
        original = None
        if legacy:
            original = journal.commit_correction_registration(
                {**options, "artifact_id": artifact["artifact_id"], "fitted_at": fit_time, "policy": policy},
            )
        claim = evaluation.register_correction_holdout(journal, artifact, **options, policy=policy)
        if legacy:
            assert claim == original
        else:
            assert claim["plan"]["cohort_policy"] == evaluation.FIRST_DURABLE_PUBLICATION
        rid = claim["registration_id"]
        assert claim["recorded_utc"] == claim["durable_observed_utc"] == fit_time
        assert journal.get_correction_registration(rid) == claim
        other_policy = evaluation_policy(registered_utc=fit_time, outcome_cutoff_utc=score_at, minimum_games=8)
        with pytest.raises(ValueError, match="overlapping_correction_registration"):
            evaluation.register_correction_holdout(journal, artifact, **options, policy=other_policy)
        with pytest.raises(CorrectionsError, match="registered_evaluation_cutoff_not_reached"):
            evaluation.freeze_correction_holdout(journal, artifact, **options, registration_id=rid)
        with pytest.raises(sqlite3.IntegrityError, match="immutable correction registration"):
            journal._db.execute("UPDATE nfl_correction_registrations SET payload='{}'")
        journal._db.rollback()

    with NFLPublicationJournal(path) as journal:
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: end)
        assert evaluation.register_correction_holdout(journal, artifact, **options, policy=policy) == claim
        with pytest.raises(ValueError, match="registration_must_follow_fit_and_precede_holdout"):
            evaluation.register_correction_holdout(journal, artifact, **options, policy=other_policy)
        with pytest.raises(CorrectionsError, match="correction_registration_mismatch"):
            evaluation.freeze_correction_holdout(journal, artifact, **options,
                                                 registration_id=rid, policy=other_policy)
        holdout = evaluation.freeze_correction_holdout(journal, artifact, **options, registration_id=rid)
        assert holdout["version"] == (1 if legacy else 2)
        with pytest.raises(CorrectionsError, match="registered_evaluation_cutoff_not_reached"):
            evaluation.evaluate_correction_holdout(journal, artifact, holdout, as_of_utc=score_at)
    with NFLPublicationJournal(path, read_only=True) as journal:
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: score_at)
        report = evaluation.evaluate_correction_holdout(journal, artifact, holdout, as_of_utc=score_at)
        assert report["evidence"].get("cohort_policy") == (None if legacy else evaluation.FIRST_DURABLE_PUBLICATION)
        assert report["evidence"]["prospective_registration_verified"] is True
        assert report["evidence"]["registration_source"] == "local_sqlite_journal"
        assert report["evidence"]["status"] == "held"
        assert report["adoption_approved"] is False


def test_registration_written_before_start_but_observed_at_start_is_refused(tmp_path, monkeypatch):
    start = "2090-01-02T00:00:00Z"
    before = "2090-01-01T23:59:59Z"
    plan = dict(artifact_id="fixture", fitted_at=before, start_utc=start, end_utc="2090-01-04T00:00:00Z",
                policy={"registered_utc": before})
    with NFLPublicationJournal(tmp_path / "registration.sqlite") as journal:
        clocks = iter([before, start])
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: next(clocks))
        with pytest.raises(ValueError, match="durable_registration_not_observed_before_holdout"):
            journal.commit_correction_registration(plan)
        rid = journal._db.execute("SELECT registration_id FROM nfl_correction_registrations").fetchone()[0]
        assert journal.get_correction_registration(rid) is None
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: start)
        with pytest.raises(ValueError, match="durable_registration_not_observed_before_holdout"):
            journal.commit_correction_registration(plan)


def test_fixed_kernel_replay_keeps_original_members_and_excludes_late_labels(tmp_path, monkeypatch):
    database = tmp_path / "journal.sqlite"
    with NFLPublicationJournal(database) as journal:
        seeded = seed_publication_journal(journal)
        snapshot = journal.read_publication_fit_snapshot()
    receipt = seeded["receipts"][0]
    # Historical fixture clocks are test inputs, not estimates of deployment
    # activity. Keep the original sealed documents, and move the journal test seam.
    for record in snapshot["generation_records"]:
        record["recorded_utc"] = "2026-09-01T14:01:00Z"
    for revision in snapshot["revisions"]:
        revision["recorded_utc"] = "2026-09-02T05:00:00Z"
    artifact = {
        "artifact_id": "fixture-artifact",
        "source": LINEAGE_ARTIFACT_SOURCE,
        "trained_through": "2026-08-30T12:00:00Z",
        "fitted_at": "2026-08-31T12:00:00Z",
        "expires_at": "2026-09-03T12:00:00Z",
        "lineage": {k: receipt[k] for k in ("base_model_version", "calibrator_version")},
        "leg_cap_pp": 0.03,
        "n_graded": 300,
        "min_graded_total": 200,
        "min_graded_per_market": 50,
        "sufficient_total": True,
        "segments": {"qb_pass_yds|OVER": {"eligible_for_application": True, "n_eligible": 100, "delta": 0.03}},
    }
    from selflearn_core.adapters.nfl_corrections_policy import segment_key
    artifact["segments"] = {segment_key("qb_pass_yds", "over"): next(iter(artifact["segments"].values()))}
    # Fit authorization and byte-compatible artifact recomputation have their
    # own actual-journal tests. This seam isolates replay from training data.
    monkeypatch.setattr(evaluation, "_verify_artifact", lambda *_: None)
    reader = SimpleNamespace(read_publication_fit_snapshot=lambda: snapshot)
    holdout = evaluation.freeze_correction_holdout(
        reader, artifact, start_utc="2026-09-01T00:00:00Z", end_utc="2026-09-02T00:00:00Z",
        policy=evaluation_policy())
    assert len(holdout["members"]) == 3
    early_holdout = evaluation.freeze_correction_holdout(
        reader, artifact, start_utc=holdout["start_utc"], end_utc=holdout["end_utc"],
        policy=evaluation_policy(outcome_cutoff_utc="2026-09-02T05:00:00Z"))
    assert early_holdout["members"] == holdout["members"]
    early = evaluation.evaluate_correction_holdout(reader, artifact, early_holdout,
                                                   as_of_utc="2026-09-02T05:00:00Z", bootstrap_replicates=200)
    assert early["forecast_count"] == 3 and early["scored_count"] == 0
    assert early["exclusions"] == {"unresolved_outcome": 3}
    graded = evaluation.evaluate_correction_holdout(reader, artifact, holdout,
                                                    as_of_utc="2026-09-02T05:00:01Z", bootstrap_replicates=200)
    assert graded["scored_count"] == 3
    assert graded["game_interval"]["groups"] == graded["week_interval"]["groups"] == 1
    assert graded["week_interval"]["reason"] == "insufficient_weeks"
    assert graded["adoption_approved"] is False
    assert graded["evidence"]["status"] == "held"
    assert "independent_game_support_below_minimum" in graded["evidence"]["failures"]
    assert "independent_week_support_below_minimum" in graded["evidence"]["failures"]
    assert "qualified_coverage_below_minimum" in early["evidence"]["failures"]
    assert graded["evidence"]["adjusted_only_metrics"]["n"] == 1
    with pytest.raises(CorrectionsError, match="evaluation_policy_bootstrap_mismatch"):
        evaluation.evaluate_correction_holdout(reader, artifact, holdout,
                                               as_of_utc="2026-09-02T05:00:01Z", bootstrap_replicates=201)
    with pytest.raises(CorrectionsError, match="evaluation_policy_outcome_cutoff_mismatch"):
        evaluation.evaluate_correction_holdout(reader, artifact, holdout, as_of_utc="2026-09-03T05:00:00Z")
    changed = copy.deepcopy(holdout)
    changed["policy"]["minimum_weeks"] = 2
    with pytest.raises(CorrectionsError, match="policy_must_precede_holdout"):
        evaluation.evaluate_correction_holdout(reader, artifact, changed, as_of_utc="2026-09-02T05:00:01Z")
    qb, = [row for row in graded["rows"] if row["position"] == "QB"]
    assert qb["correction"] == "learned_segment_bias"
    assert qb["candidate_prob"] == pytest.approx(qb["served_prob"] + 0.03)
    rb, = [row for row in graded["rows"] if row["position"] == "RB"]
    assert rb["candidate_prob"] == rb["served_prob"]
    tampered = copy.deepcopy(holdout)
    tampered["members"].pop()
    with pytest.raises(CorrectionsError, match="frozen_holdout_membership_mismatch"):
        evaluation.evaluate_correction_holdout(reader, artifact, tampered, as_of_utc="2026-09-02T05:00:01Z")

    options = {"start_utc": holdout["start_utc"], "end_utc": holdout["end_utc"]}
    with NFLPublicationJournal(database) as journal:
        monkeypatch.setattr(journal, "read_publication_fit_snapshot", lambda: snapshot)
        clock = "2026-08-31T12:00:00Z"
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: clock)
        registration = evaluation.register_correction_holdout(journal, artifact, **options,
                                                               policy=evaluation_policy())
        rid = registration["registration_id"]
        clock = "2026-09-01T14:01:55Z"
        assert evaluation.record_correction_shadow(journal, artifact, registration_id=rid,
                                                    **options)["durable_decision_count"] == 0
        clock = "2026-09-01T14:03:00Z"
        captured = evaluation.record_correction_shadow(journal, artifact, registration_id=rid, **options)
        assert captured["durable_decision_count"] == 3
        clock = "2026-09-01T14:04:00Z"
        assert evaluation.record_correction_shadow(journal, artifact, registration_id=rid, **options) == captured
    with NFLPublicationJournal(database, read_only=True) as journal:
        clock = "2026-09-02T05:00:01Z"
        monkeypatch.setattr(journal, "correction_evaluation_utc", lambda: clock)
        monkeypatch.setattr(journal, "read_publication_fit_snapshot", lambda: snapshot)
        registered = evaluation.freeze_correction_holdout(journal, artifact, **options, registration_id=rid)
        observed = evaluation.evaluate_correction_holdout(journal, artifact, registered, as_of_utc=clock,
                                                           recorded_shadow=True)
        assert observed["basis"] == "recorded_pre_kickoff_shadow_decisions"
        assert observed["scored_count"] == observed["evidence"]["verified_shadow_decision_count"] == 3
        assert {row["shadow_decision_id"] for row in observed["rows"]} == set(captured["decision_ids"])
        assert observed["evidence"]["status"] == "held"
        assert observed["adoption_approved"] is False
        decisions = journal.correction_shadow_decisions(rid)
        edited = [decisions[2], {**decisions[0], "candidate_prob": 0.001},
                  {**decisions[1], "durable_observed_utc": decisions[1]["event_start_utc"]}]
        monkeypatch.setattr(journal, "correction_shadow_decisions", lambda _: edited)
        incomplete = evaluation.evaluate_correction_holdout(journal, artifact, registered, as_of_utc=clock,
                                                             recorded_shadow=True)
        assert incomplete["scored_count"] == 1
        assert incomplete["exclusions"] == {"shadow_decision_missing_or_invalid": 2}
        assert "incomplete_recorded_shadow_coverage" in incomplete["evidence"]["failures"]


def test_pooled_improvement_cannot_hide_a_position_regression():
    def result(receiving_candidate):
        forecast_rows = []
        for week in range(1, 5):
            for position in ("QB", "RB", "WR"):
                candidate = receiving_candidate if position == "WR" else 0.83
                incumbent_loss = evaluation._loss(0.80, True)
                candidate_loss = evaluation._loss(candidate, True)
                forecast_rows.append({
                    "position": position, "week_group": str(week), "correlation_group": str(week),
                    "served_prob": 0.80, "candidate_prob": candidate, "hit": True,
                    "incumbent_loss": incumbent_loss, "base_loss": incumbent_loss,
                    "candidate_loss": candidate_loss, "delta_loss": candidate_loss - incumbent_loss,
                    "correction": "learned_segment_bias",
                })
        replicates = 200
        positions = {}
        for position in ("QB", "RB", "WR"):
            subset = [row for row in forecast_rows if row["position"] == position]
            positions[position] = {
                "game_interval": evaluation._cluster_interval(subset, replicates),
                "week_interval": evaluation._week_interval(subset, replicates),
            }
        return evaluation._evidence(
            forecast_rows, len(forecast_rows), evaluation_policy(),
            evaluation._cluster_interval(forecast_rows, replicates),
            evaluation._week_interval(forecast_rows, replicates), positions, {},
        )

    pooled_but_regressing = result(0.79)
    assert pooled_but_regressing["status"] == "held"
    assert pooled_but_regressing["failures"] == [
        "WR_game_nonregression_not_established", "WR_week_nonregression_not_established",
    ]
    uniform_gain = result(0.83)
    assert uniform_gain["status"] == "retrospective_policy_met"
    assert uniform_gain["prospective_registration_verified"] is False
