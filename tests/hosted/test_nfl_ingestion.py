"""Real producer -> transport -> authenticated ASGI -> durable SQLite boundary.

SQLite is explicitly injected for local acceptance tests. This is not a
production Supabase migration or a claim the hosted deployment is connected.
"""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from services.web.security import MAX_LEARNING_JSON
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal
from .fakes import FakeStore, TEST_KEY
from .nfl_fixture import inputs

INGEST_KEY = "test-nfl-ingest-credential-" + "a" * 32
ENV = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": INGEST_KEY}


def client_for(path, **kwargs):
    return TestClient(create_app(store=FakeStore(), env=ENV,
        journal_factory=lambda: ReceiptJournal(path), **kwargs))


def post(client, path, data, token=INGEST_KEY):
    return client.post(path, json=data, headers={"Authorization": "Bearer " + token})


def upload(client, doc, snapshots):
    for object_id, payload in snapshots.items():
        response = post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "source_snapshot",
                        "object_id": object_id, "payload": payload})
        assert response.status_code == 200, response.text
    response = post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "generation",
                    "object_id": doc["envelope_id"], "payload": doc})
    assert response.status_code == 200, response.text
    return post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})


def test_actual_producer_exporter_api_persistence_and_restart(tmp_path, monkeypatch):
    journal, _, exporter, pool, schedule, cards, recorded = inputs(monkeypatch)
    snapshots = tmp_path / "source-snapshots"
    doc, added = journal.record(pool, schedule, None, cards, recorded, str(snapshots))
    assert added and doc["receipts"]
    plan = exporter.plan(snapshots)
    path = tmp_path / "durable.sqlite"
    requests = []
    with client_for(path) as client:
        def transport(route, body):
            requests.append(route)
            response = client.post(route, content=body,
                headers={"Authorization": "Bearer " + INGEST_KEY, "Content-Type": "application/json"})
            return response.status_code, response.content
        result = exporter.send(plan, "https://local-acceptance.invalid", tmp_path / "delivery", INGEST_KEY,
                               transport=transport, sleep=lambda _: None)
    assert result["ok"] is True, result
    assert result["receipts_acked"] == len(doc["receipts"])
    assert requests.count("/api/v1/nfl/objects") == 4
    assert requests.count("/api/v1/nfl/generations") == 1
    with ReceiptJournal(path) as db:
        before = db.receipts()
        assert len(before) == len(doc["receipts"])
        assert {r["source_receipt_id"] for r in before} == {r["receipt_id"] for r in doc["receipts"]}
        assert db.generation_ack(doc["envelope_id"])["durable"] is True
        assert all(r["publication_status"] == "generated_unverified" for r in before)
    # A different app/process connection and an empty exporter ACK cache must
    # recover the same persisted identities, not invent new first-seen times.
    with client_for(path) as client:
        def replay_transport(route, body):
            response = client.post(route, content=body,
                headers={"Authorization": "Bearer " + INGEST_KEY, "Content-Type": "application/json"})
            return response.status_code, response.content
        replay = exporter.send(plan, "https://local-acceptance.invalid", tmp_path / "fresh-delivery", INGEST_KEY,
                               transport=replay_transport, sleep=lambda _: None)
        assert replay["ok"] is True
        response = post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})
        assert {r["status"] for r in response.json()["receipts"]} == {"duplicate"}
    with ReceiptJournal(path) as db:
        assert db.receipts() == before


def test_credentials_are_scoped_and_namespace_is_server_owned(tmp_path):
    value = {"version": 1, "kind": "source_snapshot", "object_id": fingerprint(None), "payload": None}
    path = tmp_path / "scope.sqlite"
    with client_for(path) as client:
        for token in (TEST_KEY, "", "legacy-nfl-token"):
            assert post(client, "/api/v1/nfl/objects", value, token).status_code == 401
        for route in ("/api/status", "/api/predictions", "/mcp"):
            assert client.get(route, headers={"Authorization": "Bearer " + INGEST_KEY}).status_code == 401
        assert post(client, "/api/v1/nfl/objects", {**value, "namespace": "other-owner"}).status_code == 422
        assert post(client, "/api/v1/nfl/objects", value).status_code == 200
        assert post(client, "/api/v1/nfl/objects", value).json()["status"] == "duplicate"
    # Equal operator/pipeline secrets are a configuration error, never broad scope.
    with TestClient(create_app(store=FakeStore(), env={**ENV, "SELFLEARN_NFL_INGEST_TOKEN": TEST_KEY})) as client:
        assert post(client, "/api/v1/nfl/objects", value, TEST_KEY).status_code == 503


def test_missing_or_failed_durable_journal_never_acknowledges(tmp_path):
    value = {"version": 1, "kind": "source_snapshot", "object_id": fingerprint(None), "payload": None}
    # Vercel production must not choose local SQLite even when generic storage is configured.
    with TestClient(create_app(store=FakeStore(), env={**ENV, "VERCEL": "1"})) as client:
        assert post(client, "/api/v1/nfl/objects", value).status_code == 503
    class BrokenJournal(ReceiptJournal):
        def append_object(self, *args):
            raise sqlite3.OperationalError("sensitive filesystem secret")
    with TestClient(create_app(store=FakeStore(), env=ENV,
            journal_factory=lambda: BrokenJournal(tmp_path / "broken.sqlite"))) as client:
        response = post(client, "/api/v1/nfl/objects", value)
        assert response.status_code == 503
        assert "sensitive" not in response.text


def test_bounded_chunked_json_and_content_type(tmp_path):
    headers = {"Authorization": "Bearer " + INGEST_KEY, "Content-Type": "application/json"}
    with client_for(tmp_path / "sizes.sqlite") as client:
        body = b" " * (MAX_LEARNING_JSON + 1)
        assert client.post("/api/v1/nfl/objects", content=body, headers=headers).status_code == 413
        def chunks():
            yield b"x" * (MAX_LEARNING_JSON // 2)
            yield b"x" * (MAX_LEARNING_JSON // 2 + 1)
        assert client.post("/api/v1/nfl/objects", content=chunks(), headers=headers).status_code == 413
        # A valid object larger than the legacy 256 KiB JSON limit is accepted.
        payload = "x" * 300_000
        value = {"version": 1, "kind": "source_snapshot", "object_id": fingerprint(payload), "payload": payload}
        assert post(client, "/api/v1/nfl/objects", value).status_code == 200
        assert client.post("/api/v1/nfl/objects", content=b"{}", headers={"Authorization": headers["Authorization"]}).status_code == 415
        for raw in (b'{"value":NaN}', b"[" * 30 + b"1" + b"]" * 30, b"{broken",
                    b'{"version":1,"version":2}'):
            assert client.post("/api/v1/nfl/objects", content=raw, headers=headers).status_code == 422


@pytest.mark.parametrize("corruption", ["digest", "pool", "kickoff", "period", "source_missing", "manifest", "receipt_ids"])
def test_semantic_cross_checks_precede_receipt_ack(tmp_path, monkeypatch, corruption):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    generation = doc["generations"][0]
    if corruption == "digest":
        doc["week"] = 5
    elif corruption == "pool":
        doc["receipts"][0]["leg"]["line"] += 1
    elif corruption == "kickoff":
        doc["receipts"][0]["kickoff_utc"] = "2026-09-03T00:15:00Z"
    elif corruption == "period":
        generation["week"] = 5
    elif corruption == "source_missing":
        snapshots.pop(generation["source_snapshots"]["schedule"])
    elif corruption == "manifest":
        generation["joint_model_version"] = "sha256:" + "0" * 64
    elif corruption == "receipt_ids":
        generation["receipt_ids"] = []
    if corruption != "digest":
        doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    path = tmp_path / "reject.sqlite"
    with client_for(path) as client:
        if corruption == "digest":
            response = post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "generation",
                            "object_id": doc["envelope_id"], "payload": doc})
        else:
            response = upload(client, doc, snapshots)
        assert response.status_code == 422
    with ReceiptJournal(path) as db:
        assert db.receipts() == []
        assert db.generation_ack(doc["envelope_id"]) is None


def test_generation_commit_failure_rolls_back_all_receipts(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    path = tmp_path / "atomic.sqlite"
    class FailSecond(ReceiptJournal):
        count = 0
        def _append_receipt(self, receipt):
            self.count += 1
            if self.count == 2:
                raise sqlite3.OperationalError("disk full")
            return super()._append_receipt(receipt)
    with TestClient(create_app(store=FakeStore(), env=ENV, journal_factory=lambda: FailSecond(path))) as client:
        response = upload(client, doc, snapshots)
        assert response.status_code == 503
    with ReceiptJournal(path) as db:
        assert db.receipts() == []
        assert db.generation_ack(doc["envelope_id"]) is None
    with client_for(path) as client:
        assert upload(client, doc, snapshots).status_code == 200


def test_database_immutable_objects_and_commit_records(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    path = tmp_path / "immutable.sqlite"
    with client_for(path) as client:
        assert upload(client, doc, snapshots).status_code == 200
    with ReceiptJournal(path) as db:
        with pytest.raises(sqlite3.IntegrityError):
            db._db.execute("DELETE FROM receipt_journal_objects")
        with pytest.raises(sqlite3.IntegrityError):
            db._db.execute("UPDATE receipt_journal_generation_commits SET payload='{}'")
        assert db.get_object(doc["envelope_id"])["received_utc"]
        assert len(db.receipts()) == len(doc["receipts"])


def test_bad_receipt_quarantines_only_that_row_and_ack_is_durable(tmp_path, monkeypatch):
    journal, lr, _, pool, schedule, _, recorded = inputs(monkeypatch)
    source = pool["players"][0]["rungs"][0]["learning_receipt"]
    source["features"]["market_prob"] = .99  # Forbidden predictor, even when frozen/hash-valid.
    source["receipt_id"] = lr.fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
    doc, snapshots = journal.capture(pool, schedule, None, [], recorded)
    assert len(doc["receipts"]) > 1
    path = tmp_path / "quarantine.sqlite"
    with client_for(path) as client:
        response = upload(client, doc, snapshots)
        assert response.status_code == 200, response.text
        ack = response.json()
        assert ack["durable"] is True
        assert ack["rejections"] == [{"source_receipt_id": source["receipt_id"], "reason": "invalid_receipt_contract"}]
        assert len(ack["receipts"]) == len(doc["receipts"]) - 1
        replay = post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})
        assert replay.json()["rejections"] == ack["rejections"]
    with ReceiptJournal(path) as db:
        assert db.generation_ack(doc["envelope_id"])["rejections"] == ack["rejections"]
        assert len(db.receipts()) == len(doc["receipts"]) - 1


def test_generation_membership_requires_commit_not_just_object_upload(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    path = tmp_path / "membership.sqlite"
    with ReceiptJournal(path) as db:
        for object_id, payload in snapshots.items():
            db.append_object("source_snapshot", object_id, payload)
        db.append_object("generation", doc["envelope_id"], doc)
        assert db.generations() == []
    with client_for(path) as client:
        response = post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})
        assert response.status_code == 200
    with ReceiptJournal(path) as db:
        generations = db.generations()
        assert len(generations) == 1
        assert generations[0]["object_id"] == doc["envelope_id"]
        assert generations[0]["payload"] == doc
        assert generations[0]["ack"] == db.generation_ack(doc["envelope_id"])
        assert {r["source_receipt_id"] for r in generations[0]["ack"]["receipts"]} == {
            r["receipt_id"] for r in doc["receipts"]}
        assert generations[0]["recorded_utc"] != recorded
        before = generations
    with client_for(path) as client:
        assert post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]}).status_code == 200
    with ReceiptJournal(path) as db:
        assert db.generations() == before


def test_committed_retry_does_not_rerun_changed_adapter(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    path = tmp_path / "adapter-version.sqlite"
    with client_for(path) as client:
        first = upload(client, doc, snapshots)
        assert first.status_code == 200
    from services.web import nfl_ingestion
    def changed_adapter(**kwargs):
        raise AssertionError("A retry must recover the original committed result")
    monkeypatch.setattr(nfl_ingestion, "adapt_prospective", changed_adapter)
    with client_for(path) as client:
        replay = post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})
        assert replay.status_code == 200
        assert {r["status"] for r in replay.json()["receipts"]} == {"duplicate"}
        assert [{k: v for k, v in row.items() if k != "status"} for row in replay.json()["receipts"]] == [
            {k: v for k, v in row.items() if k != "status"} for row in first.json()["receipts"]]


def test_concurrent_finalization_has_one_durable_result(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, cards, recorded)
    path = tmp_path / "concurrent.sqlite"
    with ReceiptJournal(path) as db:
        for object_id, payload in snapshots.items():
            db.append_object("source_snapshot", object_id, payload)
        db.append_object("generation", doc["envelope_id"], doc)
    from services.web import nfl_ingestion
    original = nfl_ingestion.adapt_prospective
    rendezvous = Barrier(2)
    def simultaneous_adapter(**kwargs):
        result = original(**kwargs)
        rendezvous.wait(timeout=10)
        return result
    monkeypatch.setattr(nfl_ingestion, "adapt_prospective", simultaneous_adapter)
    def finalize(client):
        return post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]})
    with client_for(path) as first, client_for(path) as second, ThreadPoolExecutor(max_workers=2) as workers:
        futures = [workers.submit(finalize, client) for client in (first, second)]
        responses = [future.result(timeout=15) for future in futures]
    assert [response.status_code for response in responses] == [200, 200]
    assert sorted({row["status"] for row in response.json()["receipts"]}.pop()
                  for response in responses) == ["duplicate", "recorded"]
    with ReceiptJournal(path) as db:
        assert len(db.receipts()) == len(doc["receipts"])
        assert len(db.generations()) == 1
