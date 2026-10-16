"""One generic boundary contract for NFL and deterministic synthetic elections.

These fixtures establish neither provider authority nor a real election pilot.
SQLite is local persistence, not hosted/native PostgreSQL acceptance. The same
journal tests are reused by the explicit PostgreSQL-WASM test module. No owner
namespace, generic chronological fitter or election adoption API is invented.
"""
import copy
from contextlib import contextmanager
import math
from pathlib import Path
import tempfile
import unittest

from selflearn_core.adapters import nfl_calibration
from selflearn_core.adapters.nfl import _prospective_prediction
from selflearn_core.contracts import validate_prediction_record
from selflearn_core.outcome_revisions import validate_journal_resolution
from selflearn_core.probability_receipts import fingerprint, prediction_id, seal_receipt
from selflearn_core.registry.sqlite_registry import SqliteRegistry
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.scoring.calibration import PlattCalibrator
from selflearn_core.store.receipt_journal import JournalConflict, ReceiptJournal
from selflearn_core.store.sqlite_store import SqliteStore
from selflearn_core.types import Score
from selflearn_core.updater.policy import Level0Monitor, Level1Calibration

DOMAINS = (("nfl", "nfl.parlay_leg"), ("elections", "election.binary"))


def receipt(domain, task, name="one", *, probability=.8, later=False, **updates):
    # Deliberately share literal source/contract/event/group IDs across domains.
    created = "2026-09-01T12:00:00Z" if later else "2026-09-01T11:00:00Z"
    payload = {"version": 1, "domain": domain, "task": task, "contract_id": fingerprint(["contract", name]),
        "event_id": "synthetic-event-" + name, "correlation_group": "synthetic-group-" + name,
        "base_prob": .6, "served_prob": probability, "base_model_version": "synthetic-base-v1",
        "calibrator_version": "synthetic-cal-v1", "forecast_created_utc": created, "first_seen_utc": created,
        "event_start_utc": "2026-09-02T00:15:00Z", "source_receipt_id": fingerprint(["source", name, later]),
        "source_snapshot_id": fingerprint(["snapshot", name]), "source_timestamps": {"synthetic": "2026-09-01T10:00:00Z"},
        "features": {"synthetic_signal": 2.0}, "publication_status": "generated_unverified"}
    payload.update(copy.deepcopy(updates))
    return seal_receipt(payload)


def resolution(row, hit=True, *, status="FINAL", corrected=False):
    value = {"receipt_id": row["receipt_id"], "contract_id": row["contract_id"], "event_id": row["event_id"],
        "status": status, "hit": hit, "resolved_utc": "2026-09-04T04:00:00Z" if corrected else "2026-09-03T04:00:00Z",
        "source": "synthetic final evidence, not a live provider"}
    if status == "VOID":
        value.update(hit=None, authoritative=True, final_evidence_status="FINAL", reason="synthetic event void")
    return value


def rehashed(row, **updates):
    return seal_receipt({**{key: copy.deepcopy(value) for key, value in row.items() if key != "receipt_id"}, **updates})


class JournalBoundaryContract:
    """Identical methods/fixtures; subclasses supply only the real journal."""

    def test_receipt_exact_replay_source_conflict_and_original_payload(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                first = receipt(domain, task)
                self.assertIs(journal.append_receipt(first), True)
                self.assertIs(journal.append_receipt(copy.deepcopy(first)), False)
                conflict = rehashed(first, served_prob=.3)
                with self.assertRaises(JournalConflict):
                    journal.append_receipt(conflict)
                self.assertEqual(journal.receipts(), [first])
                self.assertEqual(journal.get_receipt(first["receipt_id"]), first)

    def test_same_source_contract_event_and_group_ids_remain_domain_scoped(self):
        with self.journal() as journal:
            rows = [receipt(domain, task) for domain, task in DOMAINS]
            self.assertEqual(len({row["source_receipt_id"] for row in rows}), 1)
            self.assertEqual(len({row["contract_id"] for row in rows}), 1)
            self.assertEqual(len({row["receipt_id"] for row in rows}), 2)
            self.assertEqual(len({prediction_id(row) for row in rows}), 2)
            for index, row in enumerate(rows):
                self.assertTrue(journal.append_receipt(row))
                journal.append_resolution(resolution(row, index == 0), revision_id="domain-" + row["domain"])
            report = score_binary_receipts(journal.receipts(), journal.latest_resolutions())
            self.assertEqual((report["n"], report["hits"], report["unique_events"], report["correlation_groups"]), (2, 1, 2, 2))
            self.assertAlmostEqual(report["brier"], (.2 ** 2 + .8 ** 2) / 2)

    def test_legacy_prediction_ids_require_domain_qualified_task_names(self):
        # This explicitly records a legacy limitation, not universal namespace
        # isolation: callers must use the domain-qualified tasks above before
        # moving receipts into the older prediction/label store.
        with self.journal() as journal:
            rows = [receipt(domain, "shared.binary") for domain, _ in DOMAINS]
            self.assertEqual(prediction_id(rows[0]), prediction_id(rows[1]))
            self.assertNotEqual(rows[0]["receipt_id"], rows[1]["receipt_id"])
            for index, row in enumerate(rows):
                journal.append_receipt(row)
                journal.append_resolution(resolution(row, index == 0), revision_id="shared-task-" + str(index))
            report = score_binary_receipts(journal.receipts(), journal.latest_resolutions())
            self.assertEqual((report["n"], report["hits"], report["canonical_contracts_considered"]), (2, 1, 2))

    def test_canonical_first_forecast_is_chosen_before_outcome_availability(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                first, later = receipt(domain, task), receipt(domain, task, probability=.3, later=True)
                for row in (first, later):
                    journal.append_receipt(row)
                journal.append_resolution(resolution(later), revision_id="later-only")
                for ordered in ([first, later], [later, first], [later, first, first]):
                    report = score_binary_receipts(ordered, journal.latest_resolutions())
                    self.assertEqual(report["n"], 0, "A resolved later vintage cannot replace the unresolved canonical first forecast")
                    self.assertIsNone(report["mean_served_prob"])
                    self.assertIsNone(report["brier"])
                    self.assertIsNone(report["log_loss"])
                journal.append_resolution(resolution(first), revision_id="first-final")
                good = score_binary_receipts(journal.receipts(), journal.latest_resolutions())
                self.assertEqual(good["n"], 1)
                self.assertAlmostEqual(good["mean_served_prob"], .8)
                self.assertAlmostEqual(good["brier"], .04)
                self.assertAlmostEqual(good["log_loss"], -math.log(.8))
                self.assertEqual(good["rows"][0]["receipt_id"], first["receipt_id"])

    def test_conflicting_simultaneous_first_forecast_holds_even_if_only_one_has_outcome(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                first = receipt(domain, task)
                other = rehashed(first, served_prob=.2, source_receipt_id=fingerprint("conflicting-first"))
                for row in (first, other):
                    journal.append_receipt(row)
                journal.append_resolution(resolution(first), revision_id="first-only")
                for ordered in ([first, other], [other, first]):
                    report = score_binary_receipts(ordered, journal.latest_resolutions())
                    self.assertEqual(report["n"], 0)
                    self.assertIn("conflicting_first_forecast", report["excluded"])

    def test_later_vintage_never_changes_first_probability_and_related_contracts_are_not_independent(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                first = receipt(domain, task)
                later = receipt(domain, task, probability=.3, later=True)
                related = receipt(domain, task, "other", probability=.3,
                    event_id=first["event_id"], correlation_group=first["correlation_group"])
                for row in (first, later, related):
                    journal.append_receipt(row)
                for index, row in enumerate((first, later, related)):
                    journal.append_resolution(resolution(row, row is not related), revision_id="revision-" + str(index))
                report = score_binary_receipts([later, first, related, first], journal.latest_resolutions())
                self.assertEqual((report["n"], report["unique_events"], report["correlation_groups"], report["largest_group"]), (2, 1, 1, 2))
                self.assertAlmostEqual(report["brier"], (.2 ** 2 + .3 ** 2) / 2)
                self.assertAlmostEqual(report["log_loss"], (-math.log(.8) - math.log(.7)) / 2)
                self.assertIsNone(report["independent_sample_size"])
                self.assertIsNone(report["ci_low"])
                self.assertIsNone(report["ci_high"])

    def test_correction_frozen_history_old_retry_void_and_pending_are_not_binary_zero(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                row = receipt(domain, task)
                journal.append_receipt(row)
                initial = resolution(row)
                journal.append_resolution(initial, revision_id="initial")
                frozen_history = copy.deepcopy(journal.resolution_history(row["receipt_id"]))
                frozen_score = score_binary_receipts([row], [frozen_history[-1]["resolution"]])
                corrected = resolution(row, False, corrected=True)
                journal.append_resolution(corrected, revision_id="corrected", supersedes="initial", reason="synthetic correction")
                self.assertFalse(journal.append_resolution(initial, revision_id="initial"))
                self.assertEqual(journal.latest_resolutions(), [corrected])
                self.assertEqual(journal.resolution_history(row["receipt_id"])[:1], frozen_history)
                self.assertEqual(score_binary_receipts([row], [frozen_history[-1]["resolution"]]), frozen_score)
                self.assertEqual(score_binary_receipts([row], journal.latest_resolutions())["hits"], 0)
                with self.assertRaises(JournalConflict):
                    journal.append_resolution(corrected, revision_id="fork", supersedes="initial", reason="invalid fork")
                void = resolution(row, status="VOID", corrected=True)
                journal.append_resolution(void, revision_id="void", supersedes="corrected", reason="synthetic void")
                self.assertEqual(journal.latest_resolutions(), [void])
                report = score_binary_receipts([row], journal.latest_resolutions())
                self.assertEqual(report["n"], 0)
                self.assertIsNone(report["brier"])
                self.assertIsNone(report["hit_rate"])
                self.assertIsNone(journal.latest_resolutions()[0]["hit"])
                for status, hit in (("PENDING", None), ("IN_PROGRESS", False), ("FINAL", 0), ("VOID", False)):
                    wrong = {**void, "status": status, "hit": hit}
                    with self.assertRaises(ValueError):
                        validate_journal_resolution(row, wrong)
                    self.assertEqual(score_binary_receipts([row], [wrong])["n"], 0)
                self.assertEqual(journal.get_receipt(row["receipt_id"]), row)

    def test_invalid_cross_receipt_and_contract_resolution_has_no_partial_history(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                first, other = receipt(domain, task), receipt(domain, task, "other")
                journal.append_receipt(first)
                journal.append_receipt(other)
                for key, value in (("receipt_id", fingerprint("missing")), ("contract_id", other["contract_id"]), ("event_id", other["event_id"])):
                    wrong = {**resolution(first), key: value}
                    with self.assertRaises(ValueError):
                        journal.append_resolution(wrong, revision_id="invalid-" + key)
                self.assertEqual(journal.resolution_history(first["receipt_id"]), [])
                self.assertEqual(journal.resolution_history(other["receipt_id"]), [])

    def test_generic_generation_failure_is_atomic_for_both_domains(self):
        for domain, task in DOMAINS:
            with self.subTest(domain=domain), self.journal() as journal:
                existing, new = receipt(domain, task), receipt(domain, task, "new")
                journal.append_receipt(existing)
                conflicting = rehashed(existing, served_prob=.3)
                doc = {"version": 1, "fixture": "generic journal partition, no domain source authority",
                       "receipts": [{"receipt_id": row["source_receipt_id"]} for row in (new, conflicting)]}
                doc["envelope_id"] = fingerprint(doc)
                journal.append_object("generation", doc["envelope_id"], doc)
                with self.assertRaises(JournalConflict):
                    journal.commit_generation(doc["envelope_id"], [new, conflicting], [])
                self.assertIsNone(journal.get_receipt(new["receipt_id"]))
                self.assertIsNone(journal.generation_ack(doc["envelope_id"]))
                self.assertEqual(journal.receipts(), [existing])


class SQLiteCrossDomainBoundaryTests(JournalBoundaryContract, unittest.TestCase):
    @contextmanager
    def journal(self):
        with ReceiptJournal(":memory:") as journal:
            yield journal


def typed_nfl_fixture():
    """Valid SHAPE only, not claimed fitted or approved candidate evidence."""
    base = fingerprint("synthetic-nfl-base")
    rows = []
    for index in range(100):
        event = "nfl:2026:game:synthetic-" + str(index)
        rows.append({"receipt_id": fingerprint(["receipt", index]), "source_receipt_id": fingerprint(["source", index]),
            "contract_id": fingerprint(["contract", index]), "revision_hash": fingerprint(["revision", index]),
            "revision_id": "synthetic-revision-" + str(index), "forecast_created_utc": "2026-09-01T11:00:00Z",
            "first_seen_utc": "2026-09-01T11:01:00Z", "event_start_utc": "2026-09-02T00:15:00Z",
            "resolved_utc": "2026-09-03T04:00:00Z", "outcome_recorded_utc": "2026-09-03T04:01:00Z",
            "season": 2026, "week": 1, "position": "WR", "event_id": event, "correlation_group": event,
            "base_model_version": base, "base_prob": .6, "served_prob": .6, "hit": bool(index % 2)})
    rows.sort(key=lambda row: row["contract_id"])
    value = {"version": 1, "kind": nfl_calibration.KIND, "task": nfl_calibration.TASK, "state": "shadow",
        "automatic_promotion": False, "evidence_status": "retrospective_reconstruction", "receipt_policy": "base_receipt_v1",
        "base_model_version": base, "transform": copy.deepcopy(nfl_calibration.TRANSFORM), "coefficients": {"a": 0., "b": 1.},
        "fit_as_of_utc": "2026-09-05T00:00:00Z", "training": {"population": "eligible_pool", "membership_policy": "first_envelope_capture_v1",
            "cutoff_utc": "2026-09-05T00:00:00Z", "n": 100, "fit_rule": "ridge_newton_logit_base_v1", "ridge": .001,
            "dataset_id": fingerprint(rows), "rows": rows}}
    return {**value, "candidate_id": fingerprint(value)}


class CrossDomainCandidateBoundaryTests(unittest.TestCase):
    def test_shared_legacy_proposal_and_register_never_activate_or_copy_nfl_transform(self):
        typed = typed_nfl_fixture()
        nfl_calibration.validate_candidate(typed)
        saved = copy.deepcopy(typed)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "registry.sqlite")
            SqliteStore(path).init_schema()
            registry = SqliteRegistry(path, now=lambda: 1788350400)
            versions = []
            for domain, task in DOMAINS:
                with self.subTest(domain=domain):
                    confidence = PlattCalibrator(1., 0.)
                    config = {"kind": "generic.confidence_platt.v1", "domain": domain, "task": task,
                        "input_stage": "confidence", "coefficients": {"a": confidence.a, "b": confidence.b}}
                    self.assertAlmostEqual(confidence.apply(.5), 1 / (1 + math.exp(-.5)))
                    with self.assertRaises(ValueError):
                        nfl_calibration.validate_candidate(config)
                    other_kind = {**copy.deepcopy(typed), "task": task,
                                  "kind": "elections.synthetic.v1" if domain == "elections" else "generic.confidence_platt.v1"}
                    other_kind["candidate_id"] = fingerprint({k: v for k, v in other_kind.items() if k != "candidate_id"})
                    with self.assertRaises(ValueError):
                        nfl_calibration.validate_candidate(other_kind)
                    scores = [Score(task, "hit_rate", .6, 30, "all", ci_low=.4, ci_high=.8)]
                    self.assertEqual(Level0Monitor().propose(scores), [])
                    changes = Level1Calibration().propose(scores)
                    self.assertEqual(len(changes), 1)
                    self.assertEqual(changes[0].task, task)
                    self.assertEqual(changes[0].detail, {"method": "isotonic", "n": 30})
                    version = registry.register(task, config)
                    versions.append(version)
                    self.assertEqual(registry.get(version), config)
                    self.assertEqual(registry.status(version), "candidate")
                    self.assertEqual(registry.live_scores(task), [])
                    self.assertNotIn("training", registry.get(version))
            self.assertNotEqual(versions[0], versions[1])
            self.assertEqual(typed, saved)

    def test_market_feature_restriction_stays_in_sports_adapter_not_generic_elections(self):
        from test_nfl_calibration import case
        nfl, source, _ = case(week=1)
        nfl = rehashed(nfl, features={**nfl["features"], "market_prob": .8})
        with self.assertRaises(ValueError):
            _prospective_prediction(nfl, source, 2026, 1)
        election = receipt("elections", "election.binary", features={"poll_margin": 2.0, "market_prob": .8})
        report = score_binary_receipts([election], [resolution(election)])
        self.assertEqual(report["n"], 1)
        record = {"id": prediction_id(election), "ts": 1788350400, "model_version": "synthetic-poll-v1",
                  "task": "election.binary", "features": election["features"], "prediction": {"probability": .8}, "horizon_s": 3600}
        validate_prediction_record(record)


if __name__ == "__main__":
    unittest.main()
