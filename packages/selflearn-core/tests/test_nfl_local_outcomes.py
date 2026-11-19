import pytest

from selflearn_core.adapters.nfl_local_outcomes import NFLLocalObservedJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from test_nfl_capture_labels import journal_fixture
from test_nfl_file_feedback import capture


def seed(journal):
    fixture = journal_fixture(vintages=1)
    for source in fixture.objects.values():
        journal.append_object("source_snapshot", source["object_id"], source["payload"])
    for record in fixture.snapshot["generation_records"]:
        journal.append_object("generation", record["object_id"], record["payload"])
        journal.commit_generation(record["object_id"], fixture.snapshot["receipts"], [])


def test_local_capture_preserves_initial_grade_on_retry_and_appends_a_correction(tmp_path):
    path = tmp_path / "observed.sqlite"
    original_capture = capture()
    corrected_capture = capture(20, day=20)
    with NFLLocalObservedJournal(path) as journal:
        seed(journal)
        preview = journal.apply_capture(*original_capture)
        assert preview["new_revisions"] == 2 and not preview["committed"]
        assert journal.read_publication_fit_snapshot()["revisions"] == []
        first = journal.apply_capture(*original_capture, write=True)
        assert first["new_revisions"] == first["new_observations"] == 2
        assert first["committed"] and first["adoption_approved"] is False
        initial = journal.read_publication_fit_snapshot()
    with NFLLocalObservedJournal(path) as journal:
        assert journal.apply_capture(*original_capture, write=True)["new_revisions"] == 0
        corrected = journal.apply_capture(*corrected_capture, write=True)
        assert corrected["new_revisions"] == corrected["new_observations"] == 2
        assert journal.apply_capture(*original_capture, write=True)["new_revisions"] == 0
        final = journal.read_publication_fit_snapshot()
        assert initial["receipts"] == final["receipts"]
        assert initial["generation_records"] == final["generation_records"]
        old = {row["revision_id"]: row for row in initial["revisions"]}
        new = {row["supersedes"]: row for row in final["revisions"] if row["supersedes"]}
        assert set(new) == set(old)
        assert all(row["resolution"]["hit"] is False for row in new.values())
        assert all(row["resolution"]["hit"] is True for row in old.values())
    with NFLLocalObservedJournal(path, read_only=True) as journal:
        assert journal.apply_capture(*corrected_capture)["new_revisions"] == 0
        with pytest.raises(ValueError, match="local_outcome_journal_read_only"):
            journal.apply_capture(*corrected_capture, write=True)


def test_concurrent_journal_growth_refuses_stale_outcome_binding(tmp_path):
    database = tmp_path / "observed.sqlite"
    with NFLLocalObservedJournal(database) as journal:
        seed(journal)
        envelope, reader = capture()
        updated = False

        def changed_reader(key):
            nonlocal updated
            if not updated:
                with NFLLocalObservedJournal(database) as other:
                    payload = {"fixture": "concurrent-source"}
                    other.append_object("source_snapshot", fingerprint(payload), payload)
                updated = True
            return reader(key)

        with pytest.raises(JournalConflict, match="local_outcome_snapshot_changed_rebind"):
            journal.apply_capture(envelope, changed_reader, write=True)
        assert journal.read_publication_fit_snapshot()["revisions"] == []
        assert journal.apply_capture(envelope, reader, write=True)["new_revisions"] == 2
