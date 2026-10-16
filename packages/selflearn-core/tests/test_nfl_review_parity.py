"""Local supplied-content diagnostics; SQLite fixture metadata is NOT PG proof."""
import base64
import copy
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import nfl_review_parity as review
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal

CHECKOUT = os.environ.get("NFL2026_CHECKOUT")
AVAILABLE = bool(CHECKOUT and (Path(CHECKOUT) / "scripts/pinned_cohort_oracle.py").is_file())

FIXTURE_SCRIPT = r'''
import base64,importlib.util,json,tempfile
from pathlib import Path
from scripts import build_learning_outcomes as p
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 f.generation(Path(directory));f.generation(Path(directory),'2026-09-01T13:00:00Z')
 plan=f.plan(Path(directory))
 captures=[f.bundle(value='80'),f.bundle(value='80',observed='2026-09-03T18:00:00Z'),
  f.bundle(value='30',observed='2026-09-04T12:00:00Z'),f.bundle(value='80',observed='2026-09-05T12:00:00Z'),
  f.bundle(stats_rows=[f.stats_row('5',player_display_name='Other Person')],
   snap_rows=[f.snap_row(player='Other Person')],observed='2026-09-06T12:00:00Z')]
 wire=[{'manifest':c['manifest'],'observations':c['observations'],
  'raw':{k:base64.b64encode(v).decode() for k,v in c['raw_objects'].items()}} for c in captures]
 print(json.dumps({'plan':plan,'captures':wire,'batches':[p.build(plan,c) for c in captures]}))
'''


def stamp():
    return datetime.now(timezone.utc).isoformat()


def actual_fixture(checkout):
    """Actual raw producer/kernel identities, with explicitly local test clocks."""
    run = subprocess.run([sys.executable, "-c", FIXTURE_SCRIPT], cwd=checkout, capture_output=True, text=True, timeout=30)
    if run.returncode:
        raise AssertionError("actual_local_NFL_fixture_failed")
    produced = json.loads(run.stdout)
    snapshots = []
    with ReceiptJournal(":memory:") as journal:
        documents = [r["request"]["payload"] for r in produced["plan"]["generations"]]
        adapted = adapt_prospective(journal_docs=documents)
        if adapted["rejections"]:
            raise AssertionError("fixture_rejected_forecasts")
        for obj in produced["plan"]["source_objects"]:
            journal.append_object(obj["kind"], obj["object_id"], obj["payload"])
        for doc in documents:
            journal.append_object("generation", doc["envelope_id"], doc)
            source_ids = {r["receipt_id"] for r in doc["receipts"]}
            journal.commit_generation(doc["envelope_id"], [r for r in adapted["receipts"] if r["source_receipt_id"] in source_ids], [])
        observations, records = {}, {}
        for batch in produced["batches"]:
            history = [r for receipt in journal.receipts() for r in journal.resolution_history(receipt["receipt_id"])]
            result = resolve_observed(journal.receipts(), batch["source_receipts"], batch["observed_legs"], batch["game_observations"],
                observed_utc=stamp(), existing_revisions=history, existing_observations=list(observations.values()))
            if result["rejections"]:
                raise AssertionError("fixture_rejected_observed_evidence")
            for observation in result["observation_proposals"]:
                oid = observation["observation_id"]
                observations[oid] = observation
                records.setdefault(oid, {"observation_id": oid, "payload": observation, "recorded_utc": stamp()})
            for proposal in result["proposals"]:
                journal.append_resolution(**proposal)
            snapshots.append({"version": 1, "watermark": "100",  # CLAIM only: fixture is explicitly not PostgreSQL.
                "receipts": journal.receipts(), "generation_records": journal.generations(),
                "revisions": [r for receipt in journal.receipts() for r in journal.resolution_history(receipt["receipt_id"])],
                "observations": copy.deepcopy(list(observations.values())), "observation_records": copy.deepcopy(list(records.values()))})
    exported = stamp()
    return [{"version": 1, "kind": "nfl.review_parity_input.v1", "namespace": dict(review.NAMESPACE),
        "snapshot_exported_utc": exported, "analysis": {"version": 1, "snapshot": snapshot, "export_plan": produced["plan"],
        "captures": produced["captures"], "as_of_utc": exported, "population": "eligible_pool"}} for snapshot in snapshots]


def empty():
    return {"version": 1, "kind": "nfl.review_parity_input.v1", "namespace": dict(review.NAMESPACE),
        "snapshot_exported_utc": "2026-10-01T12:00:00Z", "analysis": {"version": 1,
        "as_of_utc": "2026-10-01T12:00:00Z", "population": "offered_my", "export_plan": {}, "captures": [],
        "snapshot": {"version": 1, "watermark": "0", "receipts": [], "generation_records": [], "revisions": [],
                     "observations": [], "observation_records": []}}}


class ReviewInputUnitTests(unittest.TestCase):
    def call(self, value):
        return review.evaluate(value, expected_input_id=fingerprint(value), nfl_checkout="not-used-by-unit-mock", now_utc="2026-10-02T00:00:00Z")

    def test_unsupported_projection_is_explicit_and_contains_no_observation_drop_claim(self):
        value = empty()
        with patch.object(review.comparator, "compare", return_value={"status": "unsupported_population", "parity": False}) as compare:
            result = self.call(value)
        projection = compare.call_args.args[0]["snapshot"]
        self.assertEqual(set(projection), set(review.PROJECTION_KEYS))
        self.assertEqual(result["full_snapshot_id"], fingerprint(value["analysis"]["snapshot"]))
        self.assertEqual(result["projection_id"], fingerprint(projection))
        self.assertEqual(result["status"], "unsupported_population")
        self.assertIsNone(result["coverage"]["graded_fraction"])
        for flag in ("namespace_authenticated", "completeness_authenticated", "model_adoption", "live_review_replaced"):
            self.assertIs(result[flag], False)

    def test_metadata_strictness_and_full_snapshot_are_required_before_oracle(self):
        changes = [lambda v: v.update(version=True), lambda v: v.update(extra=1),
            lambda v: v["namespace"].update(producer_id="other"), lambda v: v["namespace"].update(destination_id=fingerprint("wrong")),
            lambda v: v["analysis"].update(version=True), lambda v: v["analysis"]["snapshot"].pop("observation_records"),
            lambda v: v["analysis"]["snapshot"].update(version=True), lambda v: v["analysis"]["snapshot"].update(watermark="00"),
            lambda v: v["analysis"]["snapshot"].update(watermark="9223372036854775808"),
            lambda v: v["analysis"].update(as_of_utc="2099-01-01T00:00:00Z"),
            lambda v: v.update(snapshot_exported_utc="2099-01-01T00:00:00Z")]
        with patch.object(review.comparator, "compare", side_effect=AssertionError("must_not_run")):
            for change in changes:
                value = empty(); change(value)
                with self.subTest(change=changes.index(change)), self.assertRaises((ValueError, KeyError)):
                    self.call(value)
            with self.assertRaises(ValueError):
                review.evaluate(empty(), expected_input_id=fingerprint("wrong"), nfl_checkout="unused", now_utc=stamp())

    def test_missing_watermark_is_never_parity_even_when_mock_comparator_agrees(self):
        value = empty(); value["analysis"]["snapshot"]["watermark"] = None
        with patch.object(review.comparator, "compare", return_value={"status": "supported_pinned_cohort_parity", "parity": True,
                         "n": 1, "first_eligible_forecasts": 1}):
            result = self.call(value)
        self.assertFalse(result["parity"]); self.assertEqual(result["status"], "unsupported_local_content_pin")
        self.assertIn("missing_supplied_database_watermark", result["reason_codes"])

    def test_file_json_limits_duplicates_nonfinite_and_cli_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            for text in ('{"SECRET":1,"SECRET":2}', '{"SECRET":NaN}'):
                path.write_text(text)
                with self.assertRaises(ValueError): review.read_input(path)
            path.write_text(json.dumps(empty()))
            with patch.object(review, "MAX_INPUT", 1), self.assertRaises(ValueError): review.read_input(path)
            with patch.object(review, "MAX_INPUT", 1), self.assertRaises(ValueError): self.call(empty())
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(review.main(["--bundle", str(path), "--expected-input-id", fingerprint("wrong"), "--nfl-checkout", directory]), 2)
            self.assertNotIn("SECRET", output.getvalue()); self.assertNotIn(directory, output.getvalue())


@unittest.skipUnless(AVAILABLE, "Explicit NFL2026_CHECKOUT required for actual raw-producer/oracle comparison")
class ActualReviewParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.values = actual_fixture(CHECKOUT)

    def value(self, index=0):
        return copy.deepcopy(self.values[index])

    def call(self, value):
        return review.evaluate(value, expected_input_id=fingerprint(value), nfl_checkout=CHECKOUT, now_utc=stamp())

    def test_actual_raw_receipt_revision_parity_and_all_unchanged_confirmation_records(self):
        first, unchanged = self.call(self.value()), self.call(self.value(1))
        self.assertTrue(first["parity"]); self.assertTrue(unchanged["parity"])
        self.assertEqual(first["coverage"]["graded_contracts"], 2)
        self.assertEqual(first["scope"], {"season":2026,"weeks":[6],"population":"eligible_pool","market_family":"MY_player_yardage_only"})
        self.assertEqual(first["nfl_set_id"], first["engine_set_id"])
        self.assertEqual(first["nfl_set_id"], unchanged["nfl_set_id"])
        self.assertEqual(unchanged["coverage"]["supplied_observations"], 8)
        self.assertEqual(unchanged["coverage"]["supplied_revisions"], 4)
        self.assertNotEqual(first["full_snapshot_id"], unchanged["full_snapshot_id"])
        self.assertFalse(first["namespace_authenticated"])

    def test_real_correction_restore_and_void_are_revision_aware_not_forecast_rewrites(self):
        reports = [self.call(self.value(i)) for i in (0, 2, 3, 4)]
        self.assertTrue(all(r["parity"] for r in reports[:3]))
        self.assertNotEqual(reports[0]["nfl_set_id"], reports[1]["nfl_set_id"])
        self.assertNotEqual(reports[0]["nfl_set_id"], reports[2]["nfl_set_id"])
        self.assertEqual(reports[0]["nfl_scalars"], reports[2]["nfl_scalars"])
        self.assertFalse(reports[3]["parity"]); self.assertIsNone(reports[3]["nfl_scalars"]["brier"])
        self.assertEqual(reports[3]["coverage"]["nfl_excluded"]["non_binary_latest_revision"], 2)

    def test_partial_graded_coverage_does_not_claim_complete_or_use_later_vintage(self):
        value = self.value(); snapshot = value["analysis"]["snapshot"]
        rid = min(snapshot["receipts"], key=lambda r: (r["forecast_created_utc"], r["first_seen_utc"], r["receipt_id"]))["receipt_id"]
        snapshot["revisions"] = [r for r in snapshot["revisions"] if r["resolution"]["receipt_id"] != rid]
        snapshot["observations"] = [r for r in snapshot["observations"] if r["receipt_id"] != rid]
        snapshot["observation_records"] = [r for r in snapshot["observation_records"] if r["payload"]["receipt_id"] != rid]
        result = self.call(value)
        self.assertTrue(result["parity"]); self.assertEqual(result["status"], "partial_pinned_cohort_parity")
        self.assertFalse(result["coverage"]["all_first_eligible_contracts_graded"])
        self.assertEqual(result["coverage"]["graded_fraction"], .5)

    def test_partial_and_tampered_streams_and_future_database_clocks_fail_before_comparator(self):
        changes = [lambda s: s["receipts"].pop(), lambda s: s["generation_records"].clear(),
            lambda s: s["generation_records"][0]["ack"].update(durable=False),
            lambda s: s["generation_records"][0].update(recorded_utc="2099-01-01T00:00:00Z"),
            lambda s: s["receipts"].append(copy.deepcopy(s["receipts"][0])),
            lambda s: s["revisions"].append(copy.deepcopy(s["revisions"][0])),
            lambda s: s["revisions"][0].update(recorded_utc="2099-01-01T00:00:00Z"),
            lambda s: s["revisions"][0]["resolution"].update(actual=999),
            lambda s: s["observations"][0]["measurement"].update(actual=999),
            lambda s: s["observation_records"].pop(),
            lambda s: s["observation_records"].append(copy.deepcopy(s["observation_records"][0])),
            lambda s: s["observation_records"][0].update(recorded_utc="2026-01-01T00:00:00Z")]
        with patch.object(review.comparator, "compare", side_effect=AssertionError("must_not_run")):
            for change in changes:
                value = self.value(1); change(value["analysis"]["snapshot"])
                with self.subTest(change=changes.index(change)), self.assertRaises((ValueError, KeyError)):
                    self.call(value)

    def test_supplied_full_input_is_not_mutated_and_pin_changes_if_whole_commit_disappears(self):
        value = self.value(); before = copy.deepcopy(value); result = self.call(value)
        self.assertEqual(value, before)
        value["analysis"]["snapshot"]["generation_records"].pop()
        with self.assertRaises(ValueError):
            review.evaluate(value, expected_input_id=result["input_id"], nfl_checkout=CHECKOUT, now_utc=stamp())

    def test_bad_raw_cannot_be_substituted_by_good_normalized_grades(self):
        value = self.value(); first = value["analysis"]["captures"][0]
        first["raw"][next(iter(first["raw"]))] = base64.b64encode(b"changed raw").decode()
        with self.assertRaises(ValueError): self.call(value)

    def test_cli_success_hold_and_output_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bundle.json"
            for index, code in ((0, 0), (4, 3)):
                value = self.value(index); path.write_text(json.dumps(value)); output = io.StringIO()
                with redirect_stdout(output):
                    self.assertEqual(review.main(["--bundle", str(path), "--expected-input-id", fingerprint(value),
                        "--nfl-checkout", CHECKOUT, "--now-utc", stamp()]), code)
                self.assertEqual(json.loads(output.getvalue())["input_id"], fingerprint(value))
            with patch.object(review, "MAX_OUTPUT", 1), self.assertRaises(ValueError): self.call(self.value())


if __name__ == "__main__":
    unittest.main()
