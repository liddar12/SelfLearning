"""Real Python batch port over the local PostgreSQL-WASM migration."""
import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from selflearn_core.store.supabase_store import StoreError
from test_capture_application_adapter import ApplicationBridge


def test_real_object_batch_port_preserves_originals_and_refuses_missing_pinned_source():
    bridge = ApplicationBridge()
    try:
        with SupabaseReceiptJournal("https://offline.supabase.invalid", "sb_secret_object_batch_fixture",
                                     transport=bridge, page_size=2) as journal:
            payloads = [{"number": 1}, {"number": 1.0}, {"unicode": "é", "flag": True}]
            ids = [fingerprint(value) for value in payloads]
            for oid, payload in zip(ids, payloads):
                assert journal.append_object("source_snapshot", oid, payload)
            original = {oid: journal.get_object(oid) for oid in ids}
            watermark = journal.status()["watermark"]
            start = len(bridge.calls)
            result = journal.get_objects(list(reversed(ids)), watermark=watermark)
            assert result == [original[oid] for oid in sorted(ids)]
            calls = bridge.calls[start:]
            assert len(calls) == 2
            assert all(c["path"].endswith("selflearn_journal_read_objects_v1") for c in calls)
            later = {"after": watermark}
            journal.append_object("source_snapshot", fingerprint(later), later)
            with pytest.raises(StoreError) as error:
                journal.get_objects([fingerprint(later)], watermark=watermark)
            assert error.value.status == 502
            assert journal.get_object(fingerprint(later))["payload"] == later
    finally:
        bridge.close()

