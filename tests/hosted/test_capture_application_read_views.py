"""Complete v2 HTTP-view/memoization contracts over EXPLICIT fake RPC pages.

Real preparation, kernel IDs and strict journal/report validation run. Fixture
ACKs/Storage are memory-only: these tests do not establish SQL durability or
hosted performance. Actual PostgreSQL execution has its separate suite.
"""
import copy
from pathlib import Path
import sys

import pytest

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_application_job import CaptureApplicationStopped
from selflearn_core.adapters.nfl_capture_application_journal import CaptureApplicationBudgetExceeded
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError
from services.web.nfl_capture_applications import CaptureApplicationReceiver
from .test_capture_application_api import (BoundaryJournal, receiver_for, api_client, headers, BASE)

CORE = Path(__file__).resolve().parents[2] / "packages/selflearn-core/tests"
sys.path.insert(0, str(CORE))
try:
    from test_nfl_capture_application_port_faults import Pages, port
finally:
    sys.path.pop(0)


def fixture(complete=True):
    pages = Pages(complete=complete)
    captured = pages.ctx["request"]["capture_request"]
    receiver, storage, quota = receiver_for(port(pages),
        destination=captured["destination_id"], producer=captured["producer_id"])
    return pages, receiver, storage, quota


@pytest.mark.parametrize("complete", [False, True])
def test_every_http_view_preserves_original_partition_and_pinned_metadata_without_storage(complete):
    pages, receiver, storage, quota = fixture(complete)
    path = BASE + "/" + pages.seal["application_id"]
    with api_client(receiver) as client:
        for view, expected in (("registration", 1), ("groups", 2), ("batches", 2),
                               ("status", 1), ("terminal", int(complete))):
            reply = client.get(path + "?view=" + view, headers=headers())
            assert reply.status_code == 200, reply.text
            value = reply.json()
            assert value["total_records"] == len(value["records"]) == expected
            assert value["ledger_watermark"] == pages.fence
            assert value["registration_id"] == pages.ack["registration_id"]
            assert value["seal_id"] == pages.seal["seal_id"]
            assert value["next_after"] is None
            if view == "registration":
                assert value["records"][0]["registration"] == pages.ack
            elif view == "terminal" and complete:
                assert value["records"] == [pages.terminal]
        for slot in pages.batches:
            response = client.get(path + "/steps/" + slot["assignment"]["step_id"], headers=headers())
            assert response.status_code == 200
            assert response.json()["records"] == ([slot["result"]] if complete else [])
        assert client.get(path + "/steps/" + fingerprint("unassigned-step"), headers=headers()).status_code == 404
    assert storage.calls == quota.calls == []
    assert all(path.endswith("selflearn_journal_read_capture_application_v2") for _, path, _ in pages.calls)


def test_paged_views_have_exact_cursors_no_overlap_and_reject_unknown_or_end_cursors():
    pages, receiver, storage, _ = fixture()
    path = BASE + "/" + pages.seal["application_id"]
    with api_client(receiver) as client:
        for view in ("groups", "batches"):
            first = client.get(path + "?view=" + view + "&limit=1", headers=headers()).json()
            assert first["total_records"] == 2 and len(first["records"]) == 1 and first["next_after"] is not None
            second = client.get(path + "?view=" + view + "&limit=1&after=" + first["next_after"], headers=headers()).json()
            assert second["total_records"] == 2 and len(second["records"]) == 1 and second["next_after"] is None
            assert first["records"] != second["records"]
            expected = pages.groups if view == "groups" else pages.batches
            assert first["records"] + second["records"] == expected
            end = expected[-1]["sealed_group"]["group_id"] if view == "groups" else "1"
            for cursor in (end, fingerprint("unknown") if view == "groups" else "-1", "SECRET"):
                refused = client.get(path + "?view=" + view + "&after=" + cursor, headers=headers())
                assert refused.status_code == 422 and "SECRET" not in refused.text
        for query in ("view=bad", "view=batch", "view=status&after=0"):
            assert client.get(path + "?" + query, headers=headers()).status_code == 422
    assert storage.calls == []


def test_response_capacity_shrinks_page_but_never_returns_an_unrepresentable_single_record(monkeypatch):
    # Explicit capacity FAULT INJECTION, not proof of real3MiB payload size.
    pages, _, _, _ = fixture()
    journal = BoundaryJournal(); journal.known = copy.deepcopy(pages.application())
    captured = journal.known["request"]["capture_request"]
    receiver, _, _ = receiver_for(journal, destination=captured["destination_id"], producer=captured["producer_id"])
    real = V.bounded
    def one_record(value, maximum):
        if value.get("kind") == "nfl.capture_application_page.v2" and len(value["records"]) > 1:
            raise V.capacity._WireLimit("synthetic-page-bound")
        return real(value, maximum)
    monkeypatch.setattr(V, "bounded", one_record)
    result = receiver.application(pages.seal["application_id"], view="groups")
    assert len(result["records"]) == 1 and result["total_records"] == 2
    assert result["next_after"] == pages.groups[0]["sealed_group"]["group_id"]
    def no_record(value, maximum):
        raise V.capacity._WireLimit("synthetic-single-record-bound")
    monkeypatch.setattr(V, "bounded", no_record)
    with pytest.raises(StoreError) as refused:
        receiver.application(pages.seal["application_id"], view="status")
    assert refused.value.status == 503


@pytest.mark.parametrize("failure,status", [(CaptureApplicationStopped({"secret": "SECRET"}), 409),
    (JournalConflict("SECRET"), 409), (CaptureApplicationBudgetExceeded("SECRET"), 503)])
def test_stopped_conflict_and_expired_budget_are_static_not_success_or_private_payload(failure, status):
    journal = BoundaryJournal(); journal.failure = failure
    receiver, storage, _ = receiver_for(journal)
    with api_client(receiver) as client:
        result = client.get(BASE + "/" + fingerprint("any"), headers=headers())
        assert result.status_code == status and "SECRET" not in result.text
        assert "complete_success\":true" not in result.text
    assert storage.calls == []


def test_step_raw_pointer_lookup_is_lazy_memoized_and_rechecks_configured_scope(monkeypatch):
    pages, _, _, _ = fixture()
    journal = BoundaryJournal(); journal.known = copy.deepcopy(pages.application())
    captured = journal.known["request"]["capture_request"]
    receiver, storage, _ = receiver_for(journal, destination=captured["destination_id"], producer=captured["producer_id"])
    reads = []
    def object_chunks(pointer):
        reads.append(copy.deepcopy(pointer))
        return iter([b"explicit raw-read fixture, not a kernel/DB acceptance"])
    monkeypatch.setattr(receiver.evidence, "object_chunks", object_chunks)
    def step(port, aid, sid, raw, *, expected_scope):
        assert port is journal and expected_scope == (captured["destination_id"], captured["producer_id"])
        pointer = captured["raw_pointers"][0]
        assert list(raw(pointer["object_id"])) == list(raw(pointer["object_id"]))
        return copy.deepcopy(pages.batches[0]["result"])
    monkeypatch.setattr("services.web.nfl_capture_applications.commit_capture_application_step", step)
    path = BASE + "/" + pages.seal["application_id"] + "/steps/" + pages.batches[0]["assignment"]["step_id"]
    with api_client(receiver) as client:
        result = client.post(path, json={}, headers=headers())
        assert result.status_code == 200 and result.json()["scope"] == "batch_only"
        assert result.json()["result"] == pages.batches[0]["result"]
        assert len(reads) == 2 and len(journal.calls) == 1
        for known in (None, {"request": {"capture_request": {"destination_id": captured["destination_id"], "producer_id": "foreign"}}}):
            journal.known = known
            assert client.post(path, json={}, headers=headers()).status_code == 404
    assert storage.calls == []


def test_receiver_constructor_rejects_missing_v2_consumer_even_if_v1_boundary_exists():
    journal = BoundaryJournal(); journal.stop_capture_application = None
    receiver, _, _ = receiver_for(BoundaryJournal())
    with pytest.raises(ValueError, match="invalid_capture_application_receiver_boundary"):
        CaptureApplicationReceiver(journal, receiver.evidence, receiver.quota,
            destination_id=receiver.destination_id, producer_id=receiver.producer_id)
