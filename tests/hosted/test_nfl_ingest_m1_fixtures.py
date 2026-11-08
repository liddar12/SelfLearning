"""Hosted conformance for fixtures/nfl-ingest-m1 — no NFL checkout, no live writes."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal
from services.web.application import create_app
from services.web.security import MAX_LEARNING_JSON
from .fakes import FakeStore, TEST_KEY

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "nfl-ingest-m1"
INGEST = "test-nfl-ingest-credential-" + "a" * 32
SHORT = "too-short-token-not-32"
ENV = {
    "SELFLEARN_OPERATOR_TOKEN": TEST_KEY,
    "SELFLEARN_NFL_INGEST_TOKEN": INGEST,
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def client_for(path):
    return TestClient(create_app(
        store=FakeStore(), env=ENV,
        journal_factory=lambda: ReceiptJournal(path)))


def post(client, route, body, token=INGEST):
    return client.post(route, json=body, headers={"Authorization": "Bearer " + token})


def upload_all(client):
    sequence = load("objects_upload_sequence.json")
    acks = []
    for name in sequence["order"]:
        body = load(name)
        response = post(client, "/api/v1/nfl/objects", body)
        assert response.status_code == 200, response.text
        acks.append(response.json())
    finalize = load("finalize_request.json")
    response = post(client, "/api/v1/nfl/generations", finalize)
    return acks, response


def test_fixture_hashes_and_manifest_keys_match_engine_canon():
    contract = load("contract.json")
    assert contract["MANIFEST_KEYS"] == list(MANIFEST_KEYS)
    assert contract["limits"]["supabase_http_timeout_seconds_per_request"] == 12
    assert contract["limits"]["vercel_function_max_duration_seconds"] == 60
    assert len(contract["limits"]["m1_serial_http_operations"]) == 5
    assert "engine_store_timeout_seconds" not in contract["limits"]
    assert "client_timeout_should_be_at_most_seconds" not in contract["limits"]
    assert contract["auth"]["token_min_chars"] == 32
    assert contract["publication_status"]["required_on_generation_capture"] == "generated_unverified"
    assert contract["publication_status"]["learned_eligibility"]["invented_served_enum"] == "forbidden"
    for name in ("source_snapshot_leg_pool.json", "source_snapshot_schedule.json",
                 "source_snapshot_parlay_calibration.json"):
        obj = load(name)
        assert obj["object_id"] == fingerprint(obj["payload"])
    gen = load("generation_envelope.json")
    payload = gen["payload"]
    assert gen["object_id"] == payload["envelope_id"]
    assert gen["object_id"] == fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
    manifest = payload["generations"][0]
    assert manifest["generation_id"] == fingerprint({k: manifest[k] for k in MANIFEST_KEYS})
    assert manifest["publication_status"] == "generated_unverified"
    assert payload["receipts"][0]["publication_status"] == "generated_unverified"
    assert load("index.json")["ids"]["envelope_id"] == gen["object_id"]


def test_happy_path_ack_and_idempotent_replay(tmp_path):
    path = tmp_path / "m1.sqlite"
    with client_for(path) as client:
        acks, finalize = upload_all(client)
        assert finalize.status_code == 200, finalize.text
        value = finalize.json()
        assert value["durable"] is True
        assert value["object_id"] == load("finalize_request.json")["object_id"]
        assert value["rejections"] == []
        assert len(value["receipts"]) == 1
        assert value["receipts"][0]["status"] == "recorded"
        assert value["receipts"][0]["source_receipt_id"] == load("index.json")["ids"]["source_receipt_id"]
        assert all(row["status"] == "recorded" for row in acks)
        # Exact object duplicate ACK.
        again = post(client, "/api/v1/nfl/objects", load("source_snapshot_leg_pool.json"))
        assert again.status_code == 200
        assert again.json()["status"] == "duplicate"
        # Generation finalize replay returns duplicate receipt statuses.
        replay = post(client, "/api/v1/nfl/generations", load("finalize_request.json"))
        assert replay.status_code == 200
        assert {row["status"] for row in replay.json()["receipts"]} == {"duplicate"}
    with ReceiptJournal(path) as journal:
        receipts = journal.receipts()
        assert len(receipts) == 1
        assert receipts[0]["publication_status"] == "generated_unverified"
        assert journal.generation_ack(load("finalize_request.json")["object_id"])["durable"] is True


def test_token_minimum_and_scope(tmp_path):
    body = load("source_snapshot_parlay_calibration.json")
    path = tmp_path / "tok.sqlite"
    with client_for(path) as client:
        assert post(client, "/api/v1/nfl/objects", body, token=SHORT).status_code in (401, 503)
        assert post(client, "/api/v1/nfl/objects", body, token=TEST_KEY).status_code == 401
        assert post(client, "/api/v1/nfl/objects", body).status_code == 200
    # Misconfigured short ingest token fails closed (503), never ACKs.
    env = {**ENV, "SELFLEARN_NFL_INGEST_TOKEN": SHORT}
    with TestClient(create_app(store=FakeStore(), env=env,
                               journal_factory=lambda: ReceiptJournal(tmp_path / "short.sqlite"))) as client:
        assert post(client, "/api/v1/nfl/objects", body, token=SHORT).status_code == 503


def test_timeout_contract_is_per_request_not_total_capture():
    contract = load("contract.json")
    limits = contract["limits"]
    assert limits["supabase_http_timeout_seconds_per_request"] == 12
    assert limits["vercel_function_max_duration_seconds"] == 60
    guidance = limits["producer_timeout_retry_guidance"]
    assert "20s" in guidance["per_request_client_timeout_seconds"]
    assert "identical immutable" in guidance["retries"]
    assert "5 HTTP" in guidance["total_wall_clock"] or "N_ops" in guidance["total_wall_clock"]


@pytest.mark.parametrize("corruption", [
    "object_hash_mismatch",
    "envelope_hash_mismatch",
    "manifest_hash_mismatch",
    "wrong_source_join",
    "missing_schedule_join",
    "verified_publication_on_generation",
    "duplicate_receipt_membership",
])
def test_semantic_rejects_before_ack(tmp_path, corruption):
    path = tmp_path / f"{corruption}.sqlite"
    gen = load("generation_envelope.json")
    payload = copy.deepcopy(gen["payload"])
    snapshots = [
        load("source_snapshot_leg_pool.json"),
        load("source_snapshot_schedule.json"),
        load("source_snapshot_parlay_calibration.json"),
    ]
    if corruption == "object_hash_mismatch":
        snapshots[0] = {**snapshots[0], "object_id": "sha256:" + ("0" * 64)}
    elif corruption == "envelope_hash_mismatch":
        payload["week"] = 7
        # Keep declared envelope_id stale → object upload or finalize fails.
        gen = {**gen, "payload": payload}
    elif corruption == "manifest_hash_mismatch":
        payload["generations"][0]["joint_model_version"] = "sha256:" + ("1" * 64)
        payload["envelope_id"] = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        gen = {"version": 1, "kind": "generation", "object_id": payload["envelope_id"], "payload": payload}
    elif corruption == "wrong_source_join":
        payload["receipts"][0]["leg"]["line"] = 999.5
        payload["envelope_id"] = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        gen = {"version": 1, "kind": "generation", "object_id": payload["envelope_id"], "payload": payload}
    elif corruption == "missing_schedule_join":
        snapshots = [snapshots[0], snapshots[2]]  # drop schedule
        payload["envelope_id"] = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        gen = {"version": 1, "kind": "generation", "object_id": payload["envelope_id"], "payload": payload}
    elif corruption == "verified_publication_on_generation":
        payload["generations"][0]["publication_status"] = "verified_publication"
        payload["envelope_id"] = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        gen = {"version": 1, "kind": "generation", "object_id": payload["envelope_id"], "payload": payload}
    elif corruption == "duplicate_receipt_membership":
        twin = copy.deepcopy(payload["receipts"][0])
        payload["receipts"].append(twin)
        payload["generations"][0]["receipt_ids"] = [
            payload["receipts"][0]["receipt_id"], twin["receipt_id"],
        ]
        # regenerate generation_id for mutated offered? receipt_ids not in MANIFEST_KEYS
        payload["envelope_id"] = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        gen = {"version": 1, "kind": "generation", "object_id": payload["envelope_id"], "payload": payload}

    with client_for(path) as client:
        for snap in snapshots:
            response = post(client, "/api/v1/nfl/objects", snap)
            if corruption == "object_hash_mismatch" and snap is snapshots[0]:
                assert response.status_code == 422
                return
            assert response.status_code == 200, response.text
        uploaded = post(client, "/api/v1/nfl/objects", gen)
        if corruption == "envelope_hash_mismatch":
            assert uploaded.status_code == 422
            return
        assert uploaded.status_code == 200, uploaded.text
        finalize = post(client, "/api/v1/nfl/generations",
                        {"version": 1, "object_id": gen["object_id"]})
        assert finalize.status_code == 422, finalize.text
    with ReceiptJournal(path) as journal:
        assert journal.receipts() == []
        assert journal.generation_ack(gen["object_id"]) is None


def test_oversized_body_rejected(tmp_path):
    headers = {"Authorization": "Bearer " + INGEST, "Content-Type": "application/json"}
    with client_for(tmp_path / "size.sqlite") as client:
        body = b" " * (MAX_LEARNING_JSON + 1)
        assert client.post("/api/v1/nfl/objects", content=body, headers=headers).status_code == 413
