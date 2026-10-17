"""Actual Python adapter → PostgreSQL WASM RPC integration, without networking.

This suite is explicit (outside the dependency-free core suite). It requires an
already installed PGlite module and fails, rather than skips, if it is absent.
It does not establish native PostgreSQL concurrency or deployed durability.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import select
import subprocess

import pytest

from selflearn_core.probability_receipts import fingerprint, seal_receipt, instant
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from selflearn_core.store.supabase_store import StoreConflict, StoreError


ROOT = Path(__file__).resolve().parents[2]


class PostgresBridge:
    """Test-only transport; responses are executed by the actual migration."""

    def __init__(self):
        if not os.environ.get("SELFLEARN_PGLITE_MODULE"):
            raise RuntimeError("Set SELFLEARN_PGLITE_MODULE to an already installed reviewed module")
        self.process = subprocess.Popen(
            ["node", "tests/postgres/rpc_bridge.mjs"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self.calls, self.counter = [], 0
        try:
            self.ready = self._read(timeout=30)
            if (self.ready.get("ready") is not True or self.ready.get("engine") != "postgresql-wasm"
                    or "PostgreSQL" not in self.ready.get("version", "")):
                raise RuntimeError("Actual PostgreSQL bridge did not initialize")
        except Exception:
            self.close()
            raise

    def _read(self, *, timeout=20):
        stream = self.process.stdout
        if stream is None or not select.select([stream], [], [], timeout)[0]:
            raise RuntimeError("Actual PostgreSQL bridge timed out")
        line = stream.readline()
        if not line:
            raise RuntimeError("Actual PostgreSQL bridge stopped before returning a result")
        return json.loads(line)

    def request(self, method, path, *, data):
        self.counter += 1
        request = {"id": self.counter, "method": method, "path": path, "data": data}
        self.calls.append(copy.deepcopy(request))
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
        self.process.stdin.flush()
        result = self._read()
        if result.get("id") != self.counter:
            raise RuntimeError("Actual PostgreSQL bridge response identity mismatch")
        if result.get("ok") is not True:
            code = result.get("code")
            if code == "PT409":
                raise StoreConflict()
            raise StoreError({"PT422": 422, "42501": 403}.get(code, 503))
        return result["result"]

    def close(self):
        if self.process.stdin is not None and not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream is not None:
                stream.close()
        if self.process.returncode != 0:
            raise RuntimeError("Actual PostgreSQL bridge exited unsuccessfully")


@pytest.fixture(scope="module")
def bridge():
    transport = PostgresBridge()
    try:
        yield transport
    finally:
        transport.close()


def db(bridge, *, page_size=500):
    return SupabaseReceiptJournal("https://offline.supabase.invalid", "sb_secret_offline_fixture",
                                  transport=bridge, page_size=page_size)


def forecast(name):
    return seal_receipt({
        "version": 1, "domain": "elections", "task": "election.binary",
        "contract_id": fingerprint(["contract", name]), "event_id": "election-" + name,
        "correlation_group": "fixture-event-" + name,
        "base_prob": .6, "served_prob": .7, "base_model_version": "fixture-base-v1",
        "calibrator_version": "fixture-cal-v1",
        "forecast_created_utc": "2026-09-01T12:00:00Z", "first_seen_utc": "2026-09-01T12:01:00Z",
        "event_start_utc": "2026-09-02T00:15:00Z",
        "source_snapshot_id": fingerprint(["source", name]), "source_receipt_id": fingerprint(["receipt", name]),
        "source_timestamps": {"poll": "2026-09-01T11:00:00Z"},
        "features": {"poll_margin": 2.0, "truth": True, "one": 1},
        "publication_status": "generated_unverified",
    })


def result(r, hit=True):
    return {"receipt_id": r["receipt_id"], "contract_id": r["contract_id"], "event_id": r["event_id"],
            "status": "FINAL", "hit": hit, "resolved_utc": "2026-09-02T04:15:00Z",
            "source": "explicit synthetic official fixture"}


def generation(receipts):
    unsigned = {"version": 1, "receipts": [{"receipt_id": r["source_receipt_id"]} for r in receipts]}
    return {**unsigned, "envelope_id": fingerprint(unsigned)}


def test_actual_postgres_store_round_trip_replay_clocks_and_canonical_types(bridge):
    journal = db(bridge)
    status = journal.status()
    assert status["backend"] == "postgres" and status["schema_version"] == 1
    assert bridge.ready["engine"] == "postgresql-wasm"
    receipt = forecast("round-trip")
    assert journal.append_receipt(receipt) is True
    assert journal.append_receipt(copy.deepcopy(receipt)) is False
    assert journal.get_receipt(receipt["receipt_id"]) == receipt
    payloads = [{"x": True}, {"x": 1}, {"x": 1.0}, {"é": "🚀", "small": 1e-7, "zero": -0.0}]
    identities = []
    for payload in payloads:
        oid = fingerprint(payload)
        identities.append(oid)
        assert journal.append_object("source_snapshot", oid, payload) is True
        assert journal.append_object("source_snapshot", oid, copy.deepcopy(payload)) is False
        row = journal.get_object(oid)
        assert row["payload"] == payload
        assert instant(row["received_utc"]) > instant(receipt["first_seen_utc"])
    assert len(set(identities)) == len(payloads)
    changed = seal_receipt({**{k: v for k, v in receipt.items() if k != "receipt_id"},
                           "first_seen_utc": "2026-09-01T12:02:00Z"})
    with pytest.raises(JournalConflict):
        journal.append_receipt(changed)


def test_actual_generation_transaction_partial_partition_replay_and_rollback(bridge):
    journal = db(bridge)
    a, b = forecast("generation-a"), forecast("generation-b")
    doc = generation([a, b])
    assert journal.append_object("generation", doc["envelope_id"], doc) is True
    rejection = [{"source_receipt_id": b["source_receipt_id"], "reason": "unsupported_fixture"}]
    ack = journal.commit_generation(doc["envelope_id"], [a], rejection)
    assert ack["durable"] is True
    assert ack["receipts"][0]["status"] == "recorded"
    assert journal.generation_ack(doc["envelope_id"]) == ack
    repeated = journal.commit_generation(doc["envelope_id"], [a], rejection)
    assert repeated["receipts"][0]["status"] == "duplicate"
    assert journal.generation_ack(doc["envelope_id"])["receipts"][0]["status"] == "recorded"
    assert journal.get_receipt(b["receipt_id"]) is None
    with pytest.raises(JournalConflict):
        journal.commit_generation(doc["envelope_id"], [a, b], [])
    c, d = forecast("rollback-first-new"), forecast("rollback-existing-source")
    assert journal.append_receipt(d) is True
    conflicting = seal_receipt({**{k: v for k, v in d.items() if k != "receipt_id"},
                               "first_seen_utc": "2026-09-01T12:03:00Z"})
    failed = generation([c, conflicting])
    journal.append_object("generation", failed["envelope_id"], failed)
    with pytest.raises(JournalConflict):
        journal.commit_generation(failed["envelope_id"], [c, conflicting], [])
    assert journal.get_receipt(c["receipt_id"]) is None
    assert journal.generation_ack(failed["envelope_id"]) is None
    assert failed["envelope_id"] not in {g["object_id"] for g in journal.generations()}


def test_actual_final_void_revision_chain_and_as_of_snapshot(bridge):
    journal = db(bridge, page_size=2)
    receipt = forecast("revision-chain")
    journal.append_receipt(receipt)
    assert journal.append_resolution(result(receipt), revision_id="chain-official-v1") is True
    assert journal.append_resolution(result(receipt), revision_id="chain-official-v1") is False
    fence = journal.status()["watermark"]
    void = {**result(receipt), "status": "VOID", "hit": None, "authoritative": True,
            "final_evidence_status": "FINAL", "reason": "official fixture withdrawal"}
    assert journal.append_resolution(void, revision_id="chain-official-v2", supersedes="chain-official-v1",
                                     reason="official correction") is True
    history = journal.resolution_history(receipt["receipt_id"])
    assert [r["revision_id"] for r in history] == ["chain-official-v1", "chain-official-v2"]
    assert history[0]["resolution"]["hit"] is True and history[1]["resolution"]["hit"] is None
    assert all(instant(r["recorded_utc"]) >= instant(r["resolution"]["resolved_utc"]) for r in history)
    assert score_binary_receipts([receipt], journal.latest_resolutions())["n"] == 0
    with pytest.raises(JournalConflict):
        journal.append_resolution(result(receipt, False), revision_id="chain-fork", supersedes="chain-official-v1", reason="fork")
    assert journal.append_resolution(result(receipt), revision_id="chain-official-v1") is False
    earlier, watermark = journal._read("latest_resolutions", watermark=fence)
    old = [json.loads(row["payload_json"]) for row in earlier]
    assert watermark == fence
    assert [r["revision_id"] for r in old if r["resolution"]["receipt_id"] == receipt["receipt_id"]] == ["chain-official-v1"]
    snapshot = journal.read_snapshot()
    selected = [r for r in snapshot["revisions"] if r["resolution"]["receipt_id"] == receipt["receipt_id"]]
    assert len(selected) == 2
    assert all(int(call["data"]["p_limit"]) <= 2 for call in bridge.calls
               if call["path"].endswith("read_v1") and call["data"]["p_watermark"] == snapshot["watermark"])
    assert receipt["served_prob"] == .7


def test_actual_configured_factory_api_checks_schema_then_commits_object(bridge, monkeypatch):
    from fastapi.testclient import TestClient
    from services.web import application
    operator = "offline-operator-credential-" + "a" * 32
    ingest = "offline-nfl-ingest-credential-" + "b" * 32
    env = {"SELFLEARN_OPERATOR_TOKEN": operator, "SELFLEARN_NFL_INGEST_TOKEN": ingest,
           "SELFLEARN_JOURNAL_BACKEND": "supabase", "SUPABASE_URL": "https://offline.supabase.invalid",
           "SUPABASE_SECRET_KEY": "sb_secret_offline_fixture"}
    monkeypatch.setattr(application, "SupabaseReceiptJournal",
                        lambda origin, key: SupabaseReceiptJournal(origin, key, transport=bridge))
    class LegacyStatusOnly:
        def status(self):
            return {"schema_version": 1, "predictions": 0, "outcomes": 0, "models": 0}
    source = {"source": "actual SQL API fixture", "integer": 1, "float": 1.0, "truth": True}
    oid = fingerprint(source)
    value = {"version": 1, "kind": "source_snapshot", "object_id": oid, "payload": source}
    start = len(bridge.calls)
    with TestClient(application.create_app(store=LegacyStatusOnly(), env=env)) as client:
        unauthorized = client.post("/api/v1/nfl/objects", json=value,
                                   headers={"Authorization": "Bearer " + operator})
        assert unauthorized.status_code == 401 and len(bridge.calls) == start
        for expected in ("recorded", "duplicate"):
            response = client.post("/api/v1/nfl/objects", json=value,
                                   headers={"Authorization": "Bearer " + ingest})
            assert response.status_code == 200, response.text
            assert response.json() == {"version": 1, "object_id": oid, "durable": True, "status": expected}
        response = client.get("/api/status", headers={"Authorization": "Bearer " + operator})
        assert response.status_code == 200
        status = response.json()
        assert status["nfl_ingestion"]["schema_verified"] is True
        assert status["nfl_ingestion"]["production_connection_verified"] is False
        assert status["capabilities"]["automatic_promotion"] is False
        assert status["capabilities"]["nfl2026_connected"] is False
    assert db(bridge).get_object(oid)["payload"] == source
    methods = [call["path"].split("/")[-1] for call in bridge.calls[start:]]
    assert methods[:4] == ["selflearn_journal_read_v1", "selflearn_journal_append_object_v1"] * 2
    assert all(call["data"]["p_view"] == "status" for call in bridge.calls[start:start + 4]
               if call["path"].endswith("read_v1"))
