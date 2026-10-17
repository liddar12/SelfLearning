"""Actual NFL raw → ASGI v2 → real SQL journal AND staging quota.

Uses in-process HTTP and PostgreSQL-WASM; private Storage remains FAKE. This
does not prove hosted network/PostgREST/native concurrency, authenticated
providers or model adoption. TEST_ADMIN provisions only disposable local rows.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from selflearn_core.store.supabase_store import StoreError, StoreConflict
from test_capture_application_adapter import ApplicationBridge, journal, initial, REGISTER, COMMIT
from test_capture_outcomes_atomic import raw_producer, stage, admit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from hosted.test_capture_application_api import receiver_for, api_client, headers, BASE, CHUNKS, DESTINATION
finally:
    sys.path.pop(0)


class ApplicationAPITransport(ApplicationBridge):
    """Exact fixed local bridge protocol, including quota HTTP-status mapping."""
    def request(self, method, path, *, data):
        self.counter += 1
        request = {"id": self.counter, "method": method, "path": path, "data": data}
        self.calls.append(copy.deepcopy(request))
        self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
        self.process.stdin.flush()
        response = self._read()
        if response.get("id") != self.counter:
            raise RuntimeError("Actual local API bridge response identity mismatch")
        if response.get("ok") is not True:
            if response.get("code") == "PT409":
                raise StoreConflict()
            raise StoreError({"PT422": 422, "PT429": 429, "PT503": 503,
                              "42501": 403}.get(response.get("code"), 503))
        return response["result"]


@pytest.fixture
def application_bridge():
    bridge = ApplicationAPITransport()
    try:
        yield bridge
    finally:
        bridge.close()


def actual_receiver(journal, bridge, *, maximum=64 * 1024 * 1024):
    receiver, storage, _fake_quota_unused = receiver_for(journal)
    namespace = staging_namespace(destination_id=DESTINATION, producer_id="nfl2026")
    provisioned = bridge.request("TEST_ADMIN", "/test/provision-capture-staging", data={
        "namespace_id": namespace, "max_total_bytes": maximum, "max_chunks": 65536, "enabled": True})
    assert provisioned == {"version": 1, "fixture_provisioned": True}
    events = []
    def quota_request(method, path, *, data):
        events.append("quota")
        return bridge.request(method, path, data=data)
    storage.callback = lambda method, path: events.append("storage-" + method)
    receiver.quota = CaptureStagingQuota(quota_request, namespace_id=namespace)
    return receiver, storage, events


def usage(bridge):
    return bridge.request("TEST_ADMIN", "/test/capture-staging-summary", data={
        "namespace_id": staging_namespace(destination_id=DESTINATION, producer_id="nfl2026")})


def raw_request(captured):
    envelope, chunks = stage(captured)
    original = make_capture_delivery_request(envelope, list(chunks.pointers.values()),
        destination_id=DESTINATION, producer_id="nfl2026")
    return V.make_request(original), chunks


def stage_http(client, chunks):
    for dto in chunks.chunks.values():
        response = client.post(CHUNKS, json=dto, headers=headers())
        assert response.status_code == 200, response.text
        assert response.json()["scope"] == "uploaded_only"
        assert response.json()["learning_accepted"] is response.json()["durable_ack"] is False


def expand(journal, raw_producer):
    plan = raw_producer["complete"]
    admit(journal, plan["source_objects"], [row["request"]["payload"] for row in plan["generations"]])


def registration(client, request):
    response = client.post(BASE, json=request, headers=headers())
    assert response.status_code == 200, response.text
    value = response.json()
    assert set(value) == {"version", "registration", "scope", "learning_accepted"}
    assert value["version"] == 2 and value["scope"] == "registration_only"
    assert value["learning_accepted"] is False
    return value["registration"]


def test_real_raw_stage_register_step_terminal_originals_and_pinned_pages(journal, application_bridge, raw_producer):
    initial(journal, raw_producer)
    forecast = copy.deepcopy(journal.receipts())
    request, chunks = raw_request(raw_producer["captures"][0])
    receiver, storage, events = actual_receiver(journal, application_bridge)
    path = BASE + "/" + request["application_id"]
    with api_client(receiver) as client:
        stage_http(client, chunks)
        ack = registration(client, request)
        assert (ack["member_count"], ack["group_count"], ack["batch_count"]) == (4, 1, 1)
        active = client.get(path, headers=headers()).json()
        fence = active["ledger_watermark"]
        assert active["records"][0]["status"] == "active"
        step = path + "/steps/" + ack["next_step_id"]
        assert client.get(step, headers=headers()).json()["records"] == []
        response = client.post(step, json={}, headers=headers())
        assert response.status_code == 200, response.text
        applied = response.json()
        assert set(applied) == {"version", "result", "scope"} and applied["scope"] == "batch_only"
        assert (applied["result"]["added_observations"], applied["result"]["added_revisions"]) == (4, 4)
        terminal = client.get(path + "?view=terminal", headers=headers()).json()["records"][0]
        assert terminal["status"] == "accepted" and terminal["counts"]["accepted_members"] == 4
        assert terminal["processing_complete"] is terminal["complete_success"] is True
        historical = client.get(path + "?view=groups&limit=1&ledger_watermark=" + fence, headers=headers()).json()
        assert historical["records"][0]["execution_state"] == "pending"
        assert client.get(step, headers=headers()).json()["records"] == [applied["result"]]
        before = len(storage.calls)
        storage.bucket["public"] = True  # Old originals must not read Storage.
        assert registration(client, request) == ack
        assert client.post(step, json={}, headers=headers()).json() == applied
        assert len(storage.calls) == before
    assert journal.receipts() == forecast
    consumed = usage(application_bridge)
    assert consumed["reserved_chunks"] == len(chunks.chunks)
    assert int(consumed["reserved_total_bytes"]) == sum(len(raw) for raw in storage.objects.values())
    assert events[0] == "quota" and events.index("quota") < events.index("storage-POST")


@pytest.mark.parametrize("route", [REGISTER, COMMIT])
def test_lost_sql_reply_recovers_original_before_storage_after_membership_expansion(journal, application_bridge, raw_producer, route):
    initial(journal, raw_producer)
    request, chunks = raw_request(raw_producer["captures"][0])
    receiver, storage, _ = actual_receiver(journal, application_bridge)
    actual, lost = journal._request, []
    def lose_once(method, path, **kwargs):
        result = actual(method, path, **kwargs)
        if path == route and not lost:
            lost.append(copy.deepcopy(result))
            raise TimeoutError("SECRET_LOST_COMMITTED_REPLY")
        return result
    path = BASE + "/" + request["application_id"]
    with api_client(receiver) as client:
        stage_http(client, chunks)
        if route == REGISTER:
            journal._request = lose_once
            result = client.post(BASE, json=request, headers=headers())
            target = BASE
        else:
            ack = registration(client, request)
            target = path + "/steps/" + ack["next_step_id"]
            journal._request = lose_once
            result = client.post(target, json={}, headers=headers())
        assert result.status_code == 503 and "SECRET" not in result.text
        journal._request = actual
        expand(journal, raw_producer)
        assert len(journal.receipts()) == 6
        before = len(storage.calls); storage.bucket["public"] = True
        retry = client.post(target, json=request if route == REGISTER else {}, headers=headers())
        assert retry.status_code == 200, retry.text
        key = "registration" if route == REGISTER else "result"
        assert retry.json()[key] == lost[0]
        assert len(storage.calls) == before
        members = (retry.json()[key]["member_count"] if route == REGISTER
                   else sum(len(g["receipt_ids"]) for g in retry.json()[key]["groups"]))
        assert members == 4


def test_membership_change_terminalizes_without_private_storage_and_other_scope_is404(journal, application_bridge, raw_producer):
    initial(journal, raw_producer)
    request, chunks = raw_request(raw_producer["captures"][0])
    receiver, storage, _ = actual_receiver(journal, application_bridge)
    path = BASE + "/" + request["application_id"]
    with api_client(receiver) as client:
        stage_http(client, chunks); ack = registration(client, request)
        expand(journal, raw_producer)
        before = len(storage.calls); storage.bucket["public"] = True
        step = path + "/steps/" + ack["next_step_id"]
        response = client.post(step, json={}, headers=headers())
        assert response.status_code == 200, response.text
        value = response.json()["result"]
        assert value["status"] == "not_applied" and value["added_observations"] == value["added_revisions"] == 0
        assert len(storage.calls) == before
        terminal = client.get(path + "?view=terminal", headers=headers()).json()["records"][0]
        assert terminal["status"] == "incomplete_membership_changed"
        assert terminal["processing_complete"] is False and terminal["counts"]["not_applied_members"] == 4
        foreign, _, _ = receiver_for(journal, producer="foreign")
        with api_client(foreign) as other:
            for target in (path, step):
                assert other.get(target, headers=headers()).status_code == 404
            assert other.post(step, json={}, headers=headers()).status_code == 404
    assert journal.read_observed_snapshot()["observations"] == []


def test_actual_pinned_page_faults_proof_injection_and_corrupt_private_bytes_never_fake_success(journal, application_bridge, raw_producer):
    initial(journal, raw_producer)
    request, chunks = raw_request(raw_producer["captures"][0])
    receiver, storage, _ = actual_receiver(journal, application_bridge)
    path = BASE + "/" + request["application_id"]
    with api_client(receiver) as client:
        stage_http(client, chunks); ack = registration(client, request)
        for query in ("view=groups&after=" + fingerprint("unknown"), "ledger_watermark=999999999999999999",
                      "ledger_watermark=00", "view=groups&after=bad", "view=other"):
            assert client.get(path + "?" + query, headers=headers()).status_code in (422, 503)
        step = path + "/steps/" + ack["next_step_id"]
        for value in ({"groups": []}, {"normalized_labels": []}, {"receipt_ids": []}, {"batch": {}}):
            assert client.post(step, json=value, headers=headers()).status_code == 422
        storage.objects[next(iter(storage.objects))] = b"corrupted private bytes"
        failed = client.post(step, json={}, headers=headers())
        assert failed.status_code == 503 and "corrupted" not in failed.text
        assert client.get(path + "?view=terminal", headers=headers()).json()["records"] == []
    snapshot = journal.read_observed_snapshot()
    assert snapshot["observations"] == snapshot["revisions"] == []


def test_real_sql_exhausted_quota_rejects_before_storage_or_learning(journal, application_bridge):
    receiver, storage, _ = actual_receiver(journal, application_bridge, maximum=1)
    with api_client(receiver) as client:
        response = client.post(CHUNKS, json=make_chunk(b"over budget"), headers=headers())
        assert response.status_code == 429, response.text
        assert "durable\":true" not in response.text and storage.calls == []
    consumed = usage(application_bridge)
    assert consumed["reserved_chunks"] == int(consumed["reserved_total_bytes"]) == 0
    assert journal.read_observed_snapshot()["observations"] == []


def test_real_sql_lost_reservation_reply_remains_charged_before_storage_exact_retry(journal, application_bridge):
    receiver, storage, _ = actual_receiver(journal, application_bridge)
    original, lost = receiver.quota._request, []
    def lose_once(method, path, *, data):
        result = original(method, path, data=data)
        if not lost:
            lost.append(copy.deepcopy(result))
            raise TimeoutError("SECRET_LOST_SQL_RESERVATION_REPLY")
        return result
    receiver.quota._request = lose_once
    dto = make_chunk(b"charged reservation before any private write")
    with api_client(receiver) as client:
        first = client.post(CHUNKS, json=dto, headers=headers())
        assert first.status_code == 503 and "SECRET" not in first.text
        assert storage.calls == []
        reserved = usage(application_bridge)
        assert reserved["reserved_chunks"] == 1 and int(reserved["reserved_total_bytes"]) == dto["raw_bytes"]
        for _ in range(2):
            retry = client.post(CHUNKS, json=dto, headers=headers())
            assert retry.status_code == 200, retry.text
            assert retry.json()["durable_ack"] is retry.json()["learning_accepted"] is False
    assert len(storage.objects) == 1 and usage(application_bridge) == reserved
