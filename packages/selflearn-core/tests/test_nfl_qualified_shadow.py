"""Only receipt-bound journal labels may enter the qualified shadow evaluation."""

from copy import deepcopy
from datetime import timedelta
import json
from unittest.mock import patch

from publication_fit_fixture import observed_revision
from selflearn_core.adapters import nfl_calibration as calibration
from selflearn_core.probability_receipts import fingerprint, instant
from test_nfl_calibration import case, corpus, reseal, stamp
from test_nfl_file_feedback import EVALUATED, POSTGAME, PREGAME, capture, graph
from test_nfl_owner_evidence import setup as owner_setup

AS_OF = "2026-10-15T00:00:00Z"
POPULATION = "eligible_pool"
QUALIFIED = {"outcome_admission": "event_bound_labels_v2"}


def coverage(cohort, graded, **reasons):
    return {"version": 1, "label_policy": "stable_player_ids_exact_fixture_v2",
            "forecast_cohort_count": cohort, "graded_count": graded,
            "excluded_count": sum(reasons.values()), "exclusion_reasons": reasons}


def generation_coverage(cohort, verified):
    return {"version": 1, "forecast_cohort_count": cohort, "verified_count": verified,
            "missing_count": cohort - verified}


def revision_for(receipt, source, *, hit, observed_utc, prior=None, legacy=False):
    return {"version": 1, "recorded_utc": stamp(instant(observed_utc) + timedelta(minutes=1)),
            **observed_revision(receipt, source, hit=hit, observed_utc=observed_utc, prior=prior, legacy=legacy)}


def shift_forecast(receipt, source, delta):
    """An independently frozen refresh of the same canonical contract."""
    source = deepcopy(source)
    first_seen = stamp(instant(receipt["first_seen_utc"]) + delta)
    source["forecast_created_utc"] = stamp(instant(source["forecast_created_utc"]) + delta)
    source["source_timestamps"] = {name: stamp(instant(at) + delta) for name, at in source["source_timestamps"].items()}
    source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
    receipt = reseal(receipt, forecast_created_utc=source["forecast_created_utc"],
                     first_seen_utc=first_seen, source_timestamps=source["source_timestamps"],
                     source_receipt_id=source["receipt_id"])
    return receipt, source


def qualified_corpus(n=110):
    receipts, sources, legacy = corpus(n=n)
    qualified = []
    for receipt, source, original in zip(receipts, sources, legacy, strict=True):
        value = original["resolution"]
        qualified.append(revision_for(receipt, source, hit=value["hit"], observed_utc=value["resolved_utc"]))
    return receipts, sources, legacy, qualified


def test_default_retrospective_report_preserves_legacy_measurement():
    report = calibration.walk_forward_report(*corpus(n=110), as_of_utc=AS_OF, population=POPULATION,
                                             generation_records=[], bootstrap_replicates=200)
    # Reproduces why a separate label policy is needed. Legacy labels can
    # support the existing numeric policy without proving event-bound labels.
    assert report["version"] == 1 and report["kind"] == "nfl.my_props.walk_forward.v1"
    assert report["captured_forecast_n"] == report["cohort_n"] == 330
    assert report["metrics"]["n"] == 220
    assert [(fold["fit_n"], fold["n"]) for fold in report["folds"]] == [(110, 110), (220, 110)]
    assert report["candidate"]["training"]["n"] == 330
    assert report["proposed_shared_return_gate"]["passed"] is True


def test_qualified_report_with_legacy_labels_holds_all_training_without_erasing_population():
    dataset = corpus(n=110)
    legacy = calibration.walk_forward_report(*dataset, as_of_utc=AS_OF, population=POPULATION,
                                             generation_records=[], bootstrap_replicates=200)
    report = calibration.walk_forward_report(*dataset, as_of_utc=AS_OF, population=POPULATION,
                                              generation_records=[], bootstrap_replicates=200, **QUALIFIED)
    assert report["version"] == 2 and report["kind"] == "nfl.my_props.walk_forward.v2"
    assert report["captured_forecast_n"] == 330
    assert report["forecast_dataset_id"] == legacy["forecast_dataset_id"]
    assert report["cohort_n"] == report["metrics"]["n"] == 0
    assert report["folds"] == report["rows"] == []
    assert report["candidate"] is None
    assert report["proposed_shared_return_gate"]["passed"] is False
    assert {"no_qualified_outcomes", "committed_generation_evidence_unverified"} <= set(
        report["proposed_shared_return_gate"]["failures"])
    assert report["excluded"]["legacy_outcome"] == 330
    assert report["label_coverage"] == coverage(330, 0, legacy_outcome=330)


def test_qualified_folds_and_final_fit_use_frozen_population_and_as_of_label_cutoffs():
    receipts, sources, legacy, revisions = qualified_corpus()
    # All four disqualified rows stay in the week-two forecast population. The
    # unresolved row is the first week-two forecast, so it fixes the cutoff.
    bad = 110, 111, 112, 113
    receipts[112], sources[112] = shift_forecast(receipts[112], sources[112], timedelta(days=-2))
    revisions[110] = legacy[110]
    revisions[111]["resolution"]["producer_trace"]["identity_crosswalk"]["player_id"] = "another-player"
    revisions[112] = None
    revisions[113] = revision_for(receipts[113], sources[113], hit=None,
                                  observed_utc=legacy[113]["resolution"]["resolved_utc"])
    # This otherwise eligible week-one label was first received after that
    # frozen cutoff. It enters the later fold and final candidate only.
    revisions[0]["recorded_utc"] = "2026-09-14T12:00:00Z"
    unique_revisions = [row for row in revisions if row is not None]
    report = calibration.walk_forward_report(
        [*receipts, deepcopy(receipts[0])], [*sources, deepcopy(sources[0])],
        [*unique_revisions, deepcopy(revisions[0])], as_of_utc=AS_OF, population=POPULATION,
        generation_records=[], bootstrap_replicates=200, **QUALIFIED,
    )
    assert report["captured_forecast_n"] == 330
    assert report["cohort_n"] == 326
    assert report["metrics"]["n"] == 216
    assert report["folds"][0]["training_cutoff_utc"] == receipts[112]["forecast_created_utc"]
    assert [(fold["fit_n"], fold["n"], fold["status"]) for fold in report["folds"]] == [
        (109, 106, "scored"), (216, 110, "scored")]
    assert report["folds"][0]["label_coverage"] == coverage(110, 109, unresolved_outcome=1)
    assert report["folds"][0]["generation_coverage"] == generation_coverage(110, 0)
    training = report["candidate"]["training"]
    assert training["n"] == 326
    assert training["outcome_admission"] == QUALIFIED["outcome_admission"]
    rejected = {receipts[index]["receipt_id"] for index in bad}
    assert not rejected.intersection(row["receipt_id"] for row in training["rows"] + report["rows"])
    assert training["n"] == len({row["receipt_id"] for row in training["rows"]})
    assert report["label_coverage"] == coverage(330, 326, legacy_outcome=1, unqualified_outcome=1,
                                                unresolved_outcome=1, void_outcome=1)
    assert "committed_generation_evidence_unverified" in report["proposed_shared_return_gate"]["failures"]


def test_invalid_nonempty_generation_records_cannot_satisfy_proposed_gate():
    receipts, sources, _, revisions = qualified_corpus()
    report = calibration.walk_forward_report(receipts, sources, revisions, as_of_utc=AS_OF,
        population=POPULATION, generation_records=[{}], bootstrap_replicates=200, **QUALIFIED)
    assert report["label_coverage"] == coverage(330, 330)
    assert report["generation_coverage"] == generation_coverage(330, 0)
    assert report["shared_shadow_numeric_gate"]["passed"] is True
    assert report["proposed_shared_return_gate"]["passed"] is False
    assert "committed_generation_evidence_unverified" in report["proposed_shared_return_gate"]["failures"]


def test_first_unresolved_receipt_cannot_borrow_a_qualified_refresh_label():
    receipt, source, old = case()
    later, later_source = shift_forecast(receipt, source, timedelta(hours=1))
    observed = old["resolution"]["resolved_utc"]
    later_revision = revision_for(later, later_source, hit=True, observed_utc=observed)
    cohort = calibration.build_cohort([later, receipt, later], [later_source, source],
        [later_revision, later_revision], as_of_utc=AS_OF, population=POPULATION, **QUALIFIED)
    assert cohort["label_coverage"] == coverage(1, 0, unresolved_outcome=1)
    assert [row["receipt_id"] for row in cohort["forecasts"]] == [receipt["receipt_id"]]

    initial_revision = revision_for(receipt, source, hit=True, observed_utc=observed)
    resolved = calibration.build_cohort([later, receipt], [later_source, source],
        [later_revision, initial_revision], as_of_utc=AS_OF, population=POPULATION, **QUALIFIED)
    assert resolved["forecast_dataset_id"] == cohort["forecast_dataset_id"]
    assert resolved["label_coverage"] == coverage(1, 1)
    assert [row["receipt_id"] for row in resolved["rows"]] == [receipt["receipt_id"]]


def test_unchanged_legacy_labels_stay_legacy_and_changed_qualified_corrections_are_as_of():
    receipt, source, original = case()
    known_legacy = revision_for(receipt, source, hit=True, legacy=True,
                                observed_utc=original["resolution"]["resolved_utc"])
    replay = observed_revision(receipt, source, hit=True, prior=known_legacy, observed_utc="2026-09-10T12:00:00Z")
    assert replay["revision_id"] == known_legacy["revision_id"]
    assert replay["resolution"] == known_legacy["resolution"]

    next_stamp = "2026-09-10T12:00:00Z"
    claimed = revision_for(receipt, source, hit=True, observed_utc=next_stamp)
    claimed.update(supersedes=known_legacy["revision_id"], reason="claimed same measurement with more evidence")
    claimed["revision_id"] = "nfl.observed_resolution_v2:" + fingerprint({"receipt_id": receipt["receipt_id"],
        "observation_id": claimed["resolution"]["evidence_observation_id"], "supersedes": known_legacy["revision_id"]})

    def build(revisions, cutoff):
        return calibration.build_cohort([receipt], [source], revisions,
                                        as_of_utc=cutoff, population=POPULATION, **QUALIFIED)

    replayed = build([known_legacy, deepcopy(known_legacy)], AS_OF)
    assert replayed["label_coverage"] == coverage(1, 0, legacy_outcome=1)
    assert build([known_legacy, claimed], AS_OF)["label_coverage"] == coverage(1, 0, unchanged_outcome_revision=1)

    correction = revision_for(receipt, source, hit=False, observed_utc=next_stamp, prior=known_legacy)
    early = build([known_legacy, correction], correction["recorded_utc"])
    late = build([correction, known_legacy], AS_OF)
    assert early["label_coverage"] == coverage(1, 0, legacy_outcome=1)
    assert early["forecast_dataset_id"] == late["forecast_dataset_id"]
    assert late["label_coverage"] == coverage(1, 1)
    assert [(row["hit"], row["revision_id"]) for row in late["rows"]] == [(False, correction["revision_id"])]


def test_file_feedback_selects_qualified_report_and_preserves_receipt_cohort():
    from selflearn_core.adapters import nfl_file_feedback as feedback

    source = graph()
    initial = feedback.prepare(source, observed_utc=PREGAME)["state"]
    unresolved = feedback.evaluate(initial, source, as_of_utc=EVALUATED)["report"]
    latest = feedback.prepare(source, previous=initial, captures=[capture()], observed_utc=POSTGAME)["state"]
    result = feedback.evaluate(latest, source, as_of_utc=EVALUATED)
    report = result["report"]
    assert report["kind"] == unresolved["kind"] == "nfl.my_props.walk_forward.v2"
    assert report["forecast_dataset_id"] == unresolved["forecast_dataset_id"]
    assert unresolved["label_coverage"] == coverage(2, 0, unresolved_outcome=2)
    assert report["label_coverage"] == coverage(2, 2)
    assert report["generation_coverage"] == unresolved["generation_coverage"] == generation_coverage(2, 2)
    assert all(result[flag] is False for flag in feedback.FLAGS)

    # A real but retrospective import verifies the commit linkage without
    # changing the separately reported pre-kickoff delivery evidence.
    late = feedback.prepare(source, captures=[capture()], observed_utc=POSTGAME)["state"]
    late_result = feedback.evaluate(late, source, as_of_utc=EVALUATED)
    assert late_result["report"]["generation_coverage"] == generation_coverage(2, 2)
    assert late_result["first_imported_before_kickoff"] == 0
    assert late_result["late_imported_receipts"] == 4


def test_generation_coverage_excludes_late_commits_and_ack_with_wrong_receipt():
    from selflearn_core.adapters import nfl_file_feedback as feedback

    source = graph()
    snapshot = feedback.prepare(source, captures=[capture()], observed_utc=POSTGAME)["state"]
    sources = [row["learning_receipt"] for gen in source["generations"]
               for row in gen["request"]["payload"]["receipts"]]

    def build(records):
        return calibration.build_cohort(snapshot["receipts"], sources, snapshot["revisions"],
            as_of_utc=EVALUATED, population=POPULATION, generation_records=records, **QUALIFIED)

    original = snapshot["generation_records"]
    assert build(original)["generation_coverage"] == generation_coverage(2, 2)
    future = [{**row, "recorded_utc": EVALUATED} for row in original]
    future_cohort = build(future)
    assert future_cohort["generation_coverage"] == generation_coverage(2, 0)
    wrong = deepcopy(original)
    for row in wrong:
        row["ack"]["receipts"][0]["receipt_id"] = fingerprint("unrelated persisted receipt")
    wrong_cohort = build(wrong)
    assert wrong_cohort["generation_coverage"] == generation_coverage(2, 0)
    assert wrong_cohort["label_coverage"] == future_cohort["label_coverage"] == coverage(2, 2)


def test_offline_cli_disallows_legacy_labels_in_shadow_evaluation(tmp_path, capsys):
    receipts, sources, revisions = corpus(n=110)
    bundle = tmp_path / "bundle.json"
    bundle.write_text(json.dumps({"version": 1, "receipts": receipts, "source_receipts": sources,
                                  "revisions": revisions, "generation_records": []}), encoding="utf-8")
    assert calibration.main(["--bundle", str(bundle), "--as-of-utc", AS_OF,
                             "--population", POPULATION, "--bootstrap-replicates", "200"]) == 0
    report = json.loads(capsys.readouterr().out)["report"]
    assert report["kind"] == "nfl.my_props.walk_forward.v2"
    assert report["label_coverage"] == coverage(330, 0, legacy_outcome=330)
    assert report["proposed_shared_return_gate"]["passed"] is False


def owner_experiment(*, qualified):
    if qualified:
        receipts, sources, _, revisions = qualified_corpus()
    else:
        receipts, sources, revisions = corpus(n=110)
    inputs = {"version": 1, "kind": "nfl.owner_shadow_evaluation_input.v1", "receipts": receipts,
        "source_receipts": sources, "revisions": revisions, "generation_records": [],
        "as_of_utc": AS_OF, "population": POPULATION, "bootstrap_replicates": 200}
    report = calibration.walk_forward_report(receipts, sources, revisions, as_of_utc=AS_OF, population=POPULATION,
        generation_records=[], bootstrap_replicates=200, **(QUALIFIED if qualified else {}))
    return inputs, json.loads(json.dumps(report, allow_nan=False))


def test_owner_legacy_report_still_replays_but_cannot_supply_a_passing_numeric_gate(tmp_path):
    experiment = owner_experiment(qualified=False)
    assert experiment[1]["proposed_shared_return_gate"]["passed"] is True
    _, bundle, journal, _, _, _, reader = owner_setup(tmp_path, experiment)
    try:
        result = reader.verify(bundle["manifest_id"])
        assert result["owner_head_content_verified"] is True
        assert result["evaluation_reexecuted"] is True
        assert result["numeric_gate_passed"] is False
        assert result["activation"] is False
        assert "legacy_shadow_report_requires_qualified_replay" in result["reason_codes"]
    finally:
        journal.close()


def test_owner_reader_reexecutes_qualified_report_under_its_strict_policy(tmp_path):
    experiment = owner_experiment(qualified=True)
    _, bundle, journal, _, _, _, reader = owner_setup(tmp_path, experiment)
    try:
        with patch.object(calibration, "walk_forward_report", wraps=calibration.walk_forward_report) as replay:
            result = reader.verify(bundle["manifest_id"])
        replay.assert_called_once()
        assert replay.call_args.kwargs["outcome_admission"] == QUALIFIED["outcome_admission"]
        assert result["owner_head_content_verified"] is True
        assert result["evaluation_reexecuted"] is True
        assert result["activation"] is False
        assert "legacy_shadow_report_requires_qualified_replay" not in result["reason_codes"]
    finally:
        journal.close()
