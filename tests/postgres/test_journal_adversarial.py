"""Independent adversarial adapter → actual PostgreSQL WASM checks.

No cloud writes, HTTP service, source data changes or model refits. This is not
proof of native PostgreSQL concurrent-connection or hosted Supabase behavior.
"""
import copy
import math
import random
import struct

import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from selflearn_core.store.supabase_store import StoreError
from test_adapter_integration import PostgresBridge, db, forecast, result


@pytest.fixture(scope="module")
def bridge():
    transport = PostgresBridge()
    try:
        yield transport
    finally:
        transport.close()


def test_independent_ieee_unicode_integer_oracle_retains_exact_bytes_and_replay(bridge):
    rng = random.Random(152024)
    values = [-0.0, 0.0, 1.0, -1.0, 1e-4, 1e-5, 1e15, 1e16, 1e17,
              math.nextafter(1e-4, 0), math.nextafter(1e16, 0)]
    while len(values) < 2500:
        value = struct.unpack(">d", rng.getrandbits(64).to_bytes(8, "big"))[0]
        if math.isfinite(value):
            values.append(value)
    payload = {
        "ieee_probes": values,
        "unicode": {"🚀": "🚀", "é": "é", "\x7f": "\x7f", "\u2028": "\u2028", "\x01": "\x01"},
        "integers": [0, -1, 2**53 + 1, 10**120],
        "distinct_types": [True, 1, 1.0, False, 0, 0.0],
    }
    journal = db(bridge)
    oid = fingerprint(payload)
    assert journal.append_object("source_snapshot", oid, payload) is True
    restored = journal.get_object(oid)["payload"]
    assert fingerprint(restored) == oid
    assert journal.append_object("source_snapshot", oid, copy.deepcopy(payload)) is False
    assert [type(v) for v in restored["distinct_types"]] == [bool, int, float, bool, int, float]


def test_same_official_source_revision_cannot_change_then_new_void_is_append_only(bridge):
    journal = db(bridge)
    receipt = forecast("adversarial-official-revision")
    journal.append_receipt(receipt)
    initial = {**result(receipt), "actual": 80, "source_revision_id": "stats-r1",
               "source_updated_utc": "2026-09-02T04:15:00Z",
               "evidence_observed_utc": "2026-09-02T05:00:00Z"}
    assert journal.append_resolution(initial, revision_id="adversarial-official-v1") is True
    before = journal.resolution_history(receipt["receipt_id"])
    with pytest.raises(JournalConflict):
        journal.append_resolution({**initial, "actual": 30, "hit": False},
            revision_id="adversarial-changed-official-id", supersedes="adversarial-official-v1",
            reason="cannot rewrite the same official revision")
    assert journal.resolution_history(receipt["receipt_id"]) == before
    void = {**initial, "status": "VOID", "hit": None, "actual": None,
            "source_revision_id": "stats-r2", "authoritative": True,
            "final_evidence_status": "FINAL", "reason": "official corrected nonparticipation",
            "resolved_utc": "2026-09-02T06:00:00Z",
            "source_updated_utc": "2026-09-02T05:50:00Z",
            "evidence_observed_utc": "2026-09-02T06:10:00Z"}
    assert journal.append_resolution(void, revision_id="adversarial-official-v2",
        supersedes="adversarial-official-v1", reason="official correction") is True
    history = journal.resolution_history(receipt["receipt_id"])
    assert history[:1] == before
    assert [r["resolution"]["hit"] for r in history] == [True, None]
    assert journal.append_resolution(initial, revision_id="adversarial-official-v1") is False
    assert journal.resolution_history(receipt["receipt_id"]) == history


def test_sql_refuses_future_observation_removed_clock_and_regressed_source_clock(bridge):
    journal = db(bridge)
    receipt = forecast("adversarial-evidence-clocks")
    journal.append_receipt(receipt)
    initial = {**result(receipt), "source_updated_utc": "2026-09-02T04:00:00Z",
               "evidence_observed_utc": "2026-09-02T05:00:00Z"}
    journal.append_resolution(initial, revision_id="adversarial-clock-v1")
    updates = [
        {**initial, "evidence_observed_utc": "2099-01-01T00:00:00Z"},
        {k: v for k, v in initial.items() if k != "source_updated_utc"},
        {**initial, "resolved_utc": "2026-09-02T06:00:00Z",
         "source_updated_utc": "2026-09-02T03:59:00Z",
         "evidence_observed_utc": "2026-09-02T06:10:00Z"},
        {**initial, "evidence_observed_utc": "2026-09-02T04:59:00Z"},
    ]
    for index, value in enumerate(updates):
        with pytest.raises(StoreError) as error:
            journal.append_resolution(value, revision_id="adversarial-bad-clock-" + str(index),
                supersedes="adversarial-clock-v1", reason="invalid clock correction")
        assert error.value.status == 422
    assert len(journal.resolution_history(receipt["receipt_id"])) == 1


def test_correction_committed_between_snapshot_views_does_not_change_pinned_cohort(bridge):
    journal = db(bridge)
    receipt = forecast("adversarial-inflight-snapshot")
    journal.append_receipt(receipt)
    initial = result(receipt)
    journal.append_resolution(initial, revision_id="adversarial-snapshot-v1")
    void = {**initial, "status": "VOID", "hit": None, "authoritative": True,
            "final_evidence_status": "FINAL", "reason": "official fixture withdrawal"}

    class AppendBetweenViews:
        appended = False

        def request(self, method, path, *, data):
            response = bridge.request(method, path, data=data)
            if (not self.appended and path.endswith("selflearn_journal_read_v1")
                    and data["p_view"] == "receipts" and data["p_watermark"] is None):
                self.appended = True
                assert journal.append_resolution(void, revision_id="adversarial-snapshot-v2",
                    supersedes="adversarial-snapshot-v1", reason="official correction")
            return response

    transport = AppendBetweenViews()
    fenced = SupabaseReceiptJournal("https://offline.supabase.invalid", "sb_secret_offline_fixture",
                                   transport=transport, page_size=2)
    snapshot = fenced.read_snapshot()
    assert transport.appended
    versions = [r for r in snapshot["revisions"] if r["resolution"]["receipt_id"] == receipt["receipt_id"]]
    assert [r["revision_id"] for r in versions] == ["adversarial-snapshot-v1"]
    assert versions[0]["resolution"]["hit"] is True
    current = [r for r in journal.latest_resolutions() if r["receipt_id"] == receipt["receipt_id"]]
    assert len(current) == 1 and current[0]["status"] == "VOID"
    assert int(journal.status()["watermark"]) > int(snapshot["watermark"])
