"""Internal composition/fault tests; fake journal is not SQL atomicity proof."""
import copy
import io
import unittest
from unittest.mock import patch

from selflearn_core import evidence_chunks as ev
from selflearn_core.adapters import nfl_capture_job as job
from selflearn_core.adapters.nfl_observed_job import ObservedCommitError
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from test_nfl_capture_labels import Journal, good_capture, journal_fixture
from test_nfl_capture_evidence import stream
from test_supabase_evidence import FakeStorage

PROCESSING = "2026-10-21T00:00:00Z"


class MemoryJournal(Journal):
    def __init__(self, *, vintages=2):
        fixture = journal_fixture(vintages=vintages)
        super().__init__(fixture.snapshot, fixture.objects)
        self.writes = []
        self.before_commit = None
        self.lose_response = False

    def commit_observed(self, observations, proposals, *, watermark):
        self.writes.append(copy.deepcopy((observations, proposals, watermark)))
        if self.before_commit:
            self.before_commit(self)
        if watermark != self.snapshot["watermark"]:
            raise JournalConflict("concurrent update")
        added_obs, added_revs = 0, 0
        for key, incoming, identity in (("observations", observations, "observation_id"),
                                        ("revisions", proposals, "revision_id")):
            known = {row[identity] for row in self.snapshot[key]}
            for row in incoming:
                if row[identity] not in known:
                    self.snapshot[key].append(copy.deepcopy(row))
                    known.add(row[identity])
                    if key == "observations":
                        added_obs += 1
                    else:
                        added_revs += 1
        self.snapshot["watermark"] = str(int(watermark) + added_obs + added_revs)
        if self.lose_response:
            raise TimeoutError("lost response after durable commit")
        return {"version": 1, "added_observations": added_obs, "added_revisions": added_revs,
                "watermark": self.snapshot["watermark"]}


def run(journal, envelope, objects, **kwargs):
    return job.verify_and_commit_capture(journal, envelope, lambda key: stream(objects[key]),
                                         clock=lambda: PROCESSING, **kwargs)


class CaptureJobTests(unittest.TestCase):
    def test_raw_bytes_to_complete_vintages_and_atomic_port_replay(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        original = copy.deepcopy(journal.snapshot["receipts"])
        first = run(journal, envelope, objects)
        self.assertEqual(first["status"], "observations_persisted")
        self.assertIs(first["durable_ack"], False)
        self.assertEqual(first["source_authentication"], "not_performed")
        self.assertEqual(first["persistence"]["commit"]["added_revisions"], 4)
        self.assertEqual(journal.reads, 1)
        self.assertEqual(len(journal.snapshot["observations"]), 4)
        replay = run(journal, envelope, objects)
        self.assertEqual(replay["persistence"]["commit"]["added_revisions"], 0)
        self.assertEqual(replay["persistence"]["commit"]["added_observations"], 0)
        self.assertEqual(journal.snapshot["receipts"], original)

    def test_true_membership_expansion_on_conflict_rebinds_every_rung_and_vintage(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        def expand(authority):
            authority.before_commit = None
            newer = journal_fixture(vintages=3, watermark="200")
            authority.snapshot, authority.objects = newer.snapshot, newer.objects
        journal.before_commit = expand
        reads = []
        def raw(key):
            reads.append(key)
            return stream(objects[key])
        result = job.verify_and_commit_capture(journal, envelope, raw, clock=lambda: PROCESSING)
        self.assertEqual(result["attempts"], 2)
        self.assertEqual(journal.reads, 2)
        self.assertEqual([len(w[0]) for w in journal.writes], [4, 6])
        self.assertEqual([w[2] for w in journal.writes], ["100", "200"])
        self.assertEqual(result["staged"]["membership_watermark"], "200")
        self.assertEqual(len(result["staged"]["groups"][0]["receipt_ids"]), 6)
        self.assertEqual(len(journal.snapshot["observations"]), 6)
        self.assertEqual(len(reads), 10, "all three integrity sources + two label rereads per attempt")

    def test_lost_response_has_no_success_and_later_stable_retry_recovers(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        journal.lose_response = True
        with self.assertRaises(TimeoutError):
            run(journal, envelope, objects)
        self.assertEqual(len(journal.snapshot["revisions"]), 4)
        journal.lose_response = False
        replay = run(journal, envelope, objects)
        self.assertEqual(replay["persistence"]["commit"]["added_revisions"], 0)
        self.assertFalse(replay["durable_ack"], "no original delivery/group result ledger yet")

    def test_conflicts_are_bounded_and_raw_corruption_never_commits(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        def conflict(authority):
            authority.snapshot["watermark"] = str(int(authority.snapshot["watermark"]) + 1)
        journal.before_commit = conflict
        with self.assertRaisesRegex(JournalConflict, "^capture_commit_conflict_retry_exhausted$"):
            run(journal, envelope, objects, max_attempts=2)
        self.assertEqual(len(journal.writes), 2)
        self.assertEqual(journal.snapshot["observations"], [])
        journal = MemoryJournal()
        with self.assertRaises(ValueError):
            job.verify_and_commit_capture(journal, envelope, lambda _: iter([b"corrupt"]), clock=lambda: PROCESSING)
        self.assertEqual(journal.writes, [])

    def test_no_supported_or_withheld_membership_returns_no_acceptance(self):
        journal, (envelope, objects) = MemoryJournal(vintages=0), good_capture()
        result = run(journal, envelope, objects)
        self.assertEqual(result["status"], "no_accepted_evidence")
        self.assertEqual(result["staged"]["status"], "no_supported_membership")
        self.assertEqual(journal.writes, [])

    def test_untrusted_ack_is_not_reported_as_persisted(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        journal.commit_observed = lambda *a, **k: {"durable": True}
        with self.assertRaisesRegex(job.CaptureCommitError, "capture_observation_persistence_unverified"):
            run(journal, envelope, objects)
        with patch.object(job, "resolve_and_commit_observed", side_effect=ObservedCommitError("secret")):
            with self.assertRaisesRegex(job.CaptureCommitError, "^capture_observation_persistence_unverified$"):
                run(journal, envelope, objects)

    def test_limits_fail_before_any_io_and_complete_groups_are_not_fragmented(self):
        journal, (envelope, objects) = MemoryJournal(), good_capture()
        for attempts in (True, 0, 6, 1.5):
            with self.assertRaises(ValueError):
                run(journal, envelope, objects, max_attempts=attempts)
        for args in ((journal, envelope, None), (None, envelope, lambda _: ())):
            with self.assertRaises(ValueError):
                job.verify_and_commit_capture(*args)
        for limit in (True, -1, 64 * 1024 * 1024 + 1):
            with self.assertRaises(ValueError):
                run(journal, envelope, objects, max_total_bytes=limit)
        self.assertEqual(journal.reads, 0)
        with patch.object(job, "MAX_BATCH_ITEMS", 3):
            with self.assertRaisesRegex(job.CaptureCommitError, "complete_underlying_group_exceeds_item_limit"):
                run(journal, envelope, objects)
        self.assertEqual(journal.writes, [])

    def test_pinned_boundary_refuses_wrong_watermark_and_oversized_payloads(self):
        journal = MemoryJournal()
        pinned = job._PinnedJournal(journal, journal.snapshot)
        with self.assertRaisesRegex(job.CaptureCommitError, "watermark_mismatch"):
            pinned.commit_observed([], [], watermark="different")
        with self.assertRaisesRegex(job.CaptureCommitError, "item_limit"):
            pinned.commit_observed([{}] * 501, [], watermark="100")
        with patch.object(job, "MAX_BATCH_BYTES", 8):
            with self.assertRaisesRegex(job.CaptureCommitError, "byte_limit"):
                pinned.commit_observed([], [], watermark="100")
        self.assertEqual(journal.writes, [])

    def test_exact_private_staging_adapter_composes_without_network_or_normalized_labels(self):
        journal, (envelope, raw_objects) = MemoryJournal(), good_capture()
        transport = FakeStorage()
        storage = SupabaseEvidenceStore(transport, bucket="private-evidence", namespace="nfl-2026")
        pointers = {key: ev.chunk_stream(io.BytesIO(raw), storage.put_chunk) for key, raw in raw_objects.items()}
        result = job.verify_and_commit_capture(journal, envelope,
            lambda key: storage.object_chunks(pointers[key]), clock=lambda: PROCESSING)
        self.assertEqual(result["status"], "observations_persisted")
        self.assertEqual(len(journal.snapshot["revisions"]), 4)
        self.assertTrue(all(call[0] in ("GET", "POST") for call in transport.calls))
        self.assertIs(result["durable_ack"], False)


if __name__ == "__main__":
    unittest.main()
