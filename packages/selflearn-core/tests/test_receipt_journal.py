"""Independent-domain persistence and correction-chain regression tests."""
import copy
import sqlite3
import tempfile
from pathlib import Path
import unittest

from selflearn_core.store.receipt_journal import JournalConflict, ReceiptJournal
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from test_probability_receipts import receipt, outcome, reseal


class ReceiptJournalTest(unittest.TestCase):
    def test_durable_receipt_replay_and_immutable_source_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            r = receipt()
            with ReceiptJournal(path) as store:
                self.assertTrue(store.append_receipt(r))
                self.assertFalse(store.append_receipt(copy.deepcopy(r)))
                with self.assertRaises(JournalConflict):
                    store.append_receipt(reseal(r, first_seen_utc="2026-11-01T12:00:02Z"))
                corrupted = copy.deepcopy(r)
                corrupted["served_prob"] = .1
                with self.assertRaises(ValueError):
                    store.append_receipt(corrupted)
            with ReceiptJournal(path) as store:
                self.assertEqual(store.receipts(), [r])
                self.assertEqual(store.get_receipt(r["receipt_id"]), r)
                self.assertIsNone(store.get_receipt("absent"))

    def test_outcome_replay_correction_history_and_final_only_scoring(self):
        r = receipt()
        with ReceiptJournal(":memory:") as store:
            store.append_receipt(r)
            self.assertEqual(store.latest_resolutions(), [])
            self.assertEqual(score_binary_receipts(store.receipts(), store.latest_resolutions())["n"], 0)
            self.assertTrue(store.append_resolution(outcome(r), revision_id="official-v1"))
            self.assertFalse(store.append_resolution(outcome(r), revision_id="official-v1"))
            correction = {**outcome(r, False), "source": "official corrected result"}
            self.assertTrue(store.append_resolution(correction, revision_id="official-v2",
                                                    supersedes="official-v1", reason="official correction"))
            self.assertFalse(store.append_resolution(outcome(r), revision_id="official-v1"))
            self.assertFalse(store.append_resolution(correction, revision_id="official-v2",
                                                     supersedes="official-v1", reason="official correction"))
            history = store.resolution_history(r["receipt_id"])
            self.assertEqual(len(history), 2)
            self.assertTrue(history[0]["resolution"]["hit"])
            self.assertFalse(history[1]["resolution"]["hit"])
            self.assertTrue(all(h["recorded_utc"].endswith("Z") for h in history))
            self.assertEqual(store.latest_resolutions(), [correction])
            score = score_binary_receipts(store.receipts(), store.latest_resolutions())
            self.assertEqual(score["n"], 1)
            self.assertAlmostEqual(score["brier"], .7 ** 2)

    def test_changed_id_fork_missing_reason_cross_receipt_and_regressed_time_fail(self):
        a, b = receipt("a"), receipt("b")
        with ReceiptJournal(":memory:") as store:
            store.append_receipt(a)
            store.append_receipt(b)
            store.append_resolution(outcome(a), revision_id="a1")
            for value, kwargs in [
                (outcome(a, False), {"revision_id": "a1"}),
                (outcome(a, False), {"revision_id": "a2"}),
                (outcome(a, False), {"revision_id": "a2", "supersedes": "a1"}),
                (outcome(b), {"revision_id": "b1", "supersedes": "a1", "reason": "wrong chain"}),
                ({**outcome(a), "resolved_utc": "2026-11-03T13:00:00Z"},
                 {"revision_id": "a2", "supersedes": "a1", "reason": "regression"}),
            ]:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    store.append_resolution(value, **kwargs)
            store.append_resolution(outcome(a, False), revision_id="a2", supersedes="a1", reason="fixed")
            with self.assertRaises(JournalConflict):
                store.append_resolution(outcome(a), revision_id="a3", supersedes="a1", reason="fork")
            self.assertEqual(len(store.resolution_history(a["receipt_id"])), 2)

    def test_final_evidence_and_exact_receipt_linkage_are_required(self):
        r = receipt()
        with ReceiptJournal(":memory:") as store:
            with self.assertRaisesRegex(ValueError, "unknown_receipt"):
                store.append_resolution(outcome(r), revision_id="v1")
            store.append_receipt(r)
            for update in ({"status": "LIVE"}, {"status": "VOID"}, {"hit": 1},
                           {"source": ""}, {"event_id": "other"}, {"contract_id": "other"}):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    store.append_resolution({**outcome(r), **update}, revision_id="bad")
            self.assertEqual(store.latest_resolutions(), [])

    def test_two_connections_serialize_chains_and_database_blocks_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            r = receipt()
            with ReceiptJournal(path) as a, ReceiptJournal(path) as b:
                a.append_receipt(r)
                self.assertFalse(b.append_receipt(r))
                a.append_resolution(outcome(r), revision_id="v1")
                b.append_resolution(outcome(r, False), revision_id="v2", supersedes="v1", reason="correction")
                with self.assertRaises(JournalConflict):
                    a.append_resolution(outcome(r), revision_id="v3", supersedes="v1", reason="stale reader")
                with sqlite3.connect(path) as db:
                    for sql in ("UPDATE receipt_journal_receipts SET payload='{}'",
                                "DELETE FROM receipt_journal_receipts",
                                "UPDATE receipt_journal_revisions SET payload='{}'",
                                "DELETE FROM receipt_journal_revisions"):
                        with self.assertRaises(sqlite3.IntegrityError):
                            db.execute(sql)
                self.assertEqual(len(a.latest_resolutions()), 1)


if __name__ == "__main__":
    unittest.main()
