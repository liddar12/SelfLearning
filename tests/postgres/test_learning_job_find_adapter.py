"""Actual Python find port -> executed PostgreSQL-WASM, no hosted/native claim."""
import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_learning_jobs import SupabaseLearningJobJournal
from selflearn_core.store.supabase_store import StoreError
from test_learning_jobs_adapter import AUTHORITY, actual, definition


def test_actual_find_null_snapshot_wrong_authority_and_unchanged_read(actual):
    journal, bridge = actual
    absent = fingerprint("absent")
    assert journal.find_job(absent) is None
    assert bridge.calls[-1]["path"].endswith("selflearn_jobs_find_v1")
    with pytest.raises(StoreError):
        journal.read_job(absent)
    spec = definition(journal)
    initial = journal.register_job(spec)
    found = journal.find_job(spec["job_id"])
    assert found["definition"] == initial["definition"] and found["events"] == []
    assert found["authority_id"] == AUTHORITY
    other = SupabaseLearningJobJournal("https://offline.supabase.invalid", "sb_secret_find_fixture",
        authority_id=fingerprint("wrong-authority"), transport=bridge)
    assert other.find_job(spec["job_id"]) is None
