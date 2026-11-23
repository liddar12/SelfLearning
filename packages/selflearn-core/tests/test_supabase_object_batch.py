"""Exact immutable-ID batching: canonical bytes, clocks, fences and completeness."""
import copy

import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_journal import _canonical
from selflearn_core.store.supabase_store import StoreError
from test_supabase_journal import journal, object_row, page, NOW


def test_exact_object_batch_pages_preserve_typed_payloads_clocks_and_input_fence():
    rows = [object_row({"value": 1}, "source_snapshot", "1"),
            object_row({"value": 1.0}, "source_snapshot", "2")]
    ids = [row["object_id"] for row in rows]
    db, wire = journal([page(rows[:1], next_after="1", watermark="70"),
                        page(rows[1:], watermark="70")], page_size=1)
    result = db.get_objects(list(reversed(ids)), watermark="70")
    assert [row["object_id"] for row in result] == sorted(ids)
    assert {_canonical(row["payload"]) for row in result} == {'{"value":1}', '{"value":1.0}'}
    assert all(row["received_utc"] == NOW and row["kind"] == "source_snapshot" for row in result)
    assert len(wire.calls) == 2
    assert all(call[1] == "/rest/v1/rpc/selflearn_journal_read_objects_v1" for call in wire.calls)
    assert all(call[2]["data"]["p_key"] == _canonical(sorted(ids)) for call in wire.calls)
    assert [call[2]["data"]["p_after"] for call in wire.calls] == [None, "1"]
    assert all(call[2]["data"]["p_watermark"] == "70" for call in wire.calls)


def test_empty_object_batch_and_invalid_ids_never_call_transport():
    db, wire = journal([])
    assert db.get_objects([]) == []
    for ids in (None, (), "one", [True], ["wrong"], [fingerprint(1)] * 2,
                [fingerprint(i) for i in range(257)]):
        with pytest.raises(ValueError):
            db.get_objects(ids)
    assert wire.calls == []


@pytest.mark.parametrize("fault", ["missing", "extra", "duplicate", "payload", "clock", "kind", "watermark"])
def test_object_batch_rejects_incomplete_or_changed_authority_without_single_read_fallback(fault):
    rows = [object_row({"value": 1}, "source_snapshot", "1"),
            object_row({"value": 2}, "source_snapshot", "2")]
    ids = [row["object_id"] for row in rows]
    damaged = copy.deepcopy(rows)
    responses = None
    if fault == "missing":
        damaged.pop()
    elif fault == "extra":
        damaged[1] = object_row({"unrequested": True}, "source_snapshot", "2")
    elif fault == "duplicate":
        damaged[1] = {**damaged[0], "journal_seq": "2"}
    elif fault == "payload":
        damaged[0]["payload_json"] = '{"value":1.0}'
    elif fault == "clock":
        damaged[0]["received_utc"] = "unknown"
    elif fault == "kind":
        damaged[0]["kind"] = "caller_claim"
    else:
        responses = [page(rows[:1], next_after="1", watermark="70"), page(rows[1:], watermark="71")]
    db, wire = journal(responses or [page(damaged)])
    with pytest.raises(StoreError) as error:
        db.get_objects(ids)
    assert error.value.status == 502
    assert all(call[1].endswith("read_objects_v1") for call in wire.calls)


def test_object_batch_refuses_oversized_page_before_decoding():
    row = object_row({"padding": "x" * (6 * 1024 * 1024)}, "source_snapshot", "1")
    db, _ = journal([page([row])])
    with pytest.raises(StoreError) as error:
        db.get_objects([row["object_id"]])
    assert error.value.status == 502

