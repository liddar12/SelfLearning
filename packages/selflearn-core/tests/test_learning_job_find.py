"""Scoped absence is distinct from invalid input, corrupt snapshots and errors."""
import datetime as dt

import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from selflearn_core.store.supabase_learning_jobs import SupabaseLearningJobJournal
from selflearn_core.store.supabase_store import StoreError
from test_learning_job_discovery import AUTHORITY, seed


def test_sqlite_find_is_validated_authority_scoped_and_read_keeps_absent_error(tmp_path):
    path = tmp_path / "jobs.db"
    with LearningJobJournal(path, authority_id=AUTHORITY) as journal:
        assert journal.find_job(fingerprint("absent")) is None
        with pytest.raises(ValueError, match="learning_job_absent"):
            journal.read_job(fingerprint("absent"))
        with pytest.raises(ValueError):
            journal.find_job("bad")
        initial = seed(journal, "find", dt.datetime.now(dt.timezone.utc))
        job_id = initial["definition"]["job_id"]
        changes = journal._db.total_changes
        found = journal.find_job(job_id)
        assert found["definition"] == initial["definition"] and found["events"] == []
        assert journal._db.total_changes == changes
        # Existing records still traverse complete_snapshot rather than a raw row.
        journal._snapshot = lambda _: (_ for _ in ()).throw(ValueError("corrupt_snapshot"))
        with pytest.raises(ValueError, match="corrupt_snapshot"):
            journal.find_job(job_id)
    with LearningJobJournal(path, authority_id=fingerprint("different-authority")) as other:
        assert other.find_job(job_id) is None


def test_supabase_find_only_null_is_absence_and_never_masks_422():
    calls = []
    def transport(method, path, *, data):
        calls.append((method, path, data))
        return None
    journal = SupabaseLearningJobJournal("https://offline.supabase.invalid", "sb_secret_find_fixture",
        authority_id=AUTHORITY, transport=transport)
    job_id = fingerprint("absent")
    assert journal.find_job(job_id) is None
    assert calls == [("POST", "/rest/v1/rpc/selflearn_jobs_find_v1",
                      {"p_authority_id": AUTHORITY, "p_job_id": job_id})]
    for value in ({}, False, [], {"definition": {"job_id": job_id}}):
        journal._request = lambda *a, **k: value
        with pytest.raises(StoreError) as error:
            journal.find_job(job_id)
        assert error.value.status == 502
    def failed(*a, **k):
        raise StoreError(422)
    journal._request = failed
    with pytest.raises(StoreError) as error:
        journal.find_job(job_id)
    assert error.value.status == 422

