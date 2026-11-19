"""Exercise publication membership and outcome cutoffs on the real journal."""
import copy
from types import SimpleNamespace

import pytest

from publication_fit_fixture import seed_publication_journal
from selflearn_core.adapters import nfl_corrections_evaluation as evaluation
from selflearn_core.adapters.nfl_corrections_policy import (
    LINEAGE_ARTIFACT_SOURCE,
    CorrectionsError,
)
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal


def test_fixed_kernel_replay_keeps_original_members_and_excludes_late_labels(tmp_path, monkeypatch):
    with NFLPublicationJournal(tmp_path / "journal.sqlite") as journal:
        seeded = seed_publication_journal(journal)
        snapshot = journal.read_publication_fit_snapshot()
    receipt = seeded["receipts"][0]
    # Historical fixture clocks are test inputs, not estimates of deployment
    # activity. Keep actual signed documents, and move the journal test seam.
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
        reader, artifact, start_utc="2026-09-01T00:00:00Z", end_utc="2026-09-02T00:00:00Z")
    assert len(holdout["members"]) == 3
    early = evaluation.evaluate_correction_holdout(reader, artifact, holdout,
                                                   as_of_utc="2026-09-02T05:00:00Z", bootstrap_replicates=200)
    assert early["forecast_count"] == 3 and early["scored_count"] == 0
    assert early["exclusions"] == {"unresolved_outcome": 3}
    graded = evaluation.evaluate_correction_holdout(reader, artifact, holdout,
                                                    as_of_utc="2026-09-02T05:00:01Z", bootstrap_replicates=200)
    assert graded["scored_count"] == 3
    assert graded["game_interval"]["groups"] == graded["week_interval"]["groups"] == 1
    assert graded["week_interval"]["reason"] == "insufficient_weeks"
    assert graded["adoption_approved"] is False
    qb, = [row for row in graded["rows"] if row["position"] == "QB"]
    assert qb["correction"] == "learned_segment_bias"
    assert qb["candidate_prob"] == pytest.approx(qb["served_prob"] + 0.03)
    rb, = [row for row in graded["rows"] if row["position"] == "RB"]
    assert rb["candidate_prob"] == rb["served_prob"]
    tampered = copy.deepcopy(holdout)
    tampered["members"].pop()
    with pytest.raises(CorrectionsError, match="frozen_holdout_membership_mismatch"):
        evaluation.evaluate_correction_holdout(reader, artifact, tampered, as_of_utc="2026-09-03T05:00:00Z")
