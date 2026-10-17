"""Real frozen NFL producer → observed resolver → append-only local journal."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import DNP_BASIS, resolve_observed
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.store.receipt_journal import ReceiptJournal


CHECKOUT = os.environ.get("NFL2026_CHECKOUT")
AVAILABLE = bool(CHECKOUT and (Path(CHECKOUT) / "scripts/build_learning_outcomes.py").is_file())
PROCESSING = "2026-10-01T00:00:00Z"  # Separate from the original September source captures.

SCRIPT = r'''
import importlib.util,json,tempfile
from pathlib import Path
from scripts import build_learning_outcomes as producer
spec=importlib.util.spec_from_file_location('outcome_fixture',Path('tests/fixtures/week6_outcomes.py'))
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)
with tempfile.TemporaryDirectory() as directory:
    snapshots=Path(directory)/'snapshots'
    fixture.generation(snapshots)
    fixture.generation(snapshots,created='2026-09-01T12:05:00Z')
    plan=fixture.plan(snapshots)
    documents=[entry['request']['payload'] for entry in plan['generations']]
    captures=[
      fixture.bundle(value='80',observed='2026-09-03T12:00:00Z'),
      fixture.bundle(value='30',observed='2026-09-04T12:00:00Z'),
      fixture.bundle(value='80',observed='2026-09-05T12:00:00Z'),
      fixture.bundle(stats_rows=[fixture.stats_row('80'),fixture.stats_row('999',week='5')],
                     observed='2026-09-06T12:00:00Z'),
      fixture.bundle(stats_rows=[fixture.stats_row('5',player_display_name='Other Person')],
                     observed='2026-09-07T12:00:00Z'),
      fixture.bundle(stats_rows=[fixture.stats_row('5',player_display_name='Other Person')],
                     snap_rows=[fixture.snap_row(player='Other Person')],observed='2026-09-08T12:00:00Z'),
    ]
    batches=[producer.build(plan,capture) for capture in captures]
    print(json.dumps({'journal_docs':documents,'batches':batches},allow_nan=False))
'''


@unittest.skipUnless(AVAILABLE, "Set NFL2026_CHECKOUT to verify the real frozen outcome producer")
class RealObservedProducerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = subprocess.run([sys.executable, "-c", SCRIPT], cwd=CHECKOUT,
                             text=True, capture_output=True, timeout=30, check=False)
        if run.returncode:
            raise AssertionError("Actual NFL outcome fixture failed: " + run.stderr)
        cls.payload = json.loads(run.stdout)

    def test_two_vintages_two_rungs_corrections_retries_inferred_zero_and_DNP(self):
        pending = adapt_prospective(journal_docs=self.payload["journal_docs"])
        self.assertEqual(pending["rejections"], [])
        self.assertEqual(len(pending["receipts"]), 4)
        frozen = json.dumps({r["receipt_id"]: r for r in pending["receipts"]}, sort_keys=True)
        batches = self.payload["batches"]
        with ReceiptJournal(":memory:") as journal:
            observations = {}  # Persistence seam; not a claim that SQLite stores fences.
            for receipt in pending["receipts"]:
                journal.append_receipt(receipt)

            def resolve(batch):
                self.assertEqual(batch["rejections"], [])
                history = [row for receipt in journal.receipts()
                           for row in journal.resolution_history(receipt["receipt_id"])]
                result = resolve_observed(journal.receipts(), batch["source_receipts"], batch["observed_legs"],
                    batch["game_observations"], observed_utc=PROCESSING, existing_revisions=history,
                    existing_observations=list(observations.values()))
                self.assertEqual(result["rejections"], [], result)
                self.assertEqual(len(result["proposals"]), 4)
                for proposal in result["proposals"]:
                    journal.append_resolution(**proposal)
                for observation in result["observation_proposals"]:
                    observations[observation["observation_id"]] = observation
                return result

            first = resolve(batches[0])
            second = resolve(batches[1])
            self.assertTrue(all(row["reason"] is not None for row in second["proposals"]))
            retried = resolve(batches[0])
            self.assertEqual(retried["proposals"], first["proposals"])
            self.assertEqual({r["receipt_id"]: r for r in journal.latest_resolutions()},
                             {p["resolution"]["receipt_id"]: p["resolution"] for p in second["proposals"]})
            third = resolve(batches[2])
            self.assertNotEqual(third["proposals"][0]["revision_id"], first["proposals"][0]["revision_id"])
            stable = resolve(batches[3])
            self.assertEqual(stable["proposals"], third["proposals"])
            zeros = resolve(batches[4])
            self.assertTrue(all(p["resolution"]["actual"] == 0 for p in zeros["proposals"]))
            self.assertTrue(all(p["resolution"]["participation_basis"] == "r106_positive_offensive_snaps_no_stat_row"
                                for p in zeros["proposals"]))
            voided = resolve(batches[5])
            self.assertTrue(all(p["resolution"]["participation_basis"] == DNP_BASIS for p in voided["proposals"]))
            self.assertEqual(score_binary_receipts(journal.receipts(), journal.latest_resolutions())["n"], 0)
            self.assertEqual(json.dumps({r["receipt_id"]: r for r in journal.receipts()}, sort_keys=True), frozen)
            self.assertTrue(all(len(journal.resolution_history(r["receipt_id"])) == 5 for r in journal.receipts()))
            trace = voided["proposals"][0]["resolution"]["producer_trace"]
            self.assertTrue(trace["pool_snapshot_ids"] and trace["generation_ids"])
            self.assertEqual(trace["evidence_quality"], "observed_source_snapshot")
            self.assertNotIn("source_updated_utc", voided["proposals"][0]["resolution"])
