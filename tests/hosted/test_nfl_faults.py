"""Semantic rejection and storage-fault tests for authenticated NFL intake."""
import copy

import pytest
from fastapi.testclient import TestClient

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal, JournalConflict
from services.web.application import create_app
from services.web.nfl_ingestion import MANIFEST_KEYS, validate_generation
from .fakes import FakeStore
from .nfl_fixture import inputs
from .test_nfl_ingestion import ENV, client_for, post, upload


def seal(doc):
    generation = doc["generations"][0]
    if all(key in generation for key in MANIFEST_KEYS):
        generation["generation_id"] = fingerprint({key: generation[key] for key in MANIFEST_KEYS})
        for row in doc["receipts"]:
            if isinstance(row, dict):
                row["generation_id"] = generation["generation_id"]
    doc["envelope_id"] = fingerprint({key: value for key, value in doc.items() if key != "envelope_id"})


def replace_source(doc, snapshots, name, payload):
    digest = fingerprint(payload)
    snapshots[digest] = payload
    doc["generations"][0]["source_snapshots"][name] = digest


@pytest.mark.parametrize("fault", [
    "unsupported_document", "missing_manifest", "period", "policy", "publication", "future",
    "source_names", "source_id", "source_shape", "source_period", "conflicting_pool",
    "conflicting_game", "invalid_row", "base_version", "missing_game", "receipt_generation", "offered",
])
def test_hash_valid_but_semantically_invalid_generations_cannot_commit(tmp_path, monkeypatch, fault):
    journal, _, _, pool, schedule, _, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, [], recorded)
    generation = doc["generations"][0]
    if fault == "missing_manifest":
        del generation["selection_policy_version"]
    elif fault == "period":
        doc["week"] = generation["week"] = 0
    elif fault == "policy":
        generation["selection_policy_version"] = "not-a-content-version"
    elif fault == "publication":
        generation["publication_status"] = "published"
    elif fault == "future":
        generation["recorded_utc"] = "2099-01-01T00:00:00Z"
    elif fault == "source_names":
        del generation["source_snapshots"]["parlay_calibration"]
    elif fault == "source_id":
        generation["source_snapshots"]["schedule"] = "not-a-content-id"
    elif fault == "source_shape":
        replace_source(doc, snapshots, "parlay_calibration", [])
    elif fault == "source_period":
        pool["week"] = 5
        replace_source(doc, snapshots, "leg_pool", pool)
    elif fault == "conflicting_pool":
        duplicate = copy.deepcopy(pool["players"][0])
        duplicate["rungs"][0]["line"] += 1
        pool["players"].append(duplicate)
        replace_source(doc, snapshots, "leg_pool", pool)
    elif fault == "conflicting_game":
        schedule["games"].append({**schedule["games"][0], "kickoff_utc": "2026-09-04T00:15:00Z"})
        replace_source(doc, snapshots, "schedule", schedule)
    elif fault == "invalid_row":
        doc["receipts"][0] = "not-a-receipt"
    elif fault == "base_version":
        pool["base_model_version"] = "different-base-version"
        replace_source(doc, snapshots, "leg_pool", pool)
    elif fault == "missing_game":
        schedule["games"] = []
        replace_source(doc, snapshots, "schedule", schedule)
    elif fault == "offered":
        generation["offered_cards"] = [{"card_id": "unbacked", "receipt_ids": ["sha256:" + "0" * 64]}]
    seal(doc)
    if fault == "receipt_generation":
        doc["receipts"][0]["generation_id"] = "sha256:" + "0" * 64
        doc["envelope_id"] = fingerprint({key: value for key, value in doc.items() if key != "envelope_id"})
    if fault == "unsupported_document":
        doc["version"] = 2
        doc["envelope_id"] = fingerprint({key: value for key, value in doc.items() if key != "envelope_id"})
    path = tmp_path / "fault.sqlite"
    with client_for(path) as client:
        response = upload(client, doc, snapshots)
        assert response.status_code == 422, (fault, response.text)
        assert "durable" not in response.json()
    with ReceiptJournal(path) as db:
        assert db.receipts() == []
        assert db.generation_ack(doc["envelope_id"]) is None


def test_unclaimed_bad_pool_rows_do_not_discard_valid_receipts(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, _, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, [], recorded)
    pool["players"] += [None, {"rungs": None}]
    pool["players"][0]["rungs"] += [None, {}, {"learning_receipt": {"receipt_id": None}}]
    schedule["games"].append(None)
    replace_source(doc, snapshots, "leg_pool", pool)
    replace_source(doc, snapshots, "schedule", schedule)
    seal(doc)
    with client_for(tmp_path / "unclaimed.sqlite") as client:
        response = upload(client, doc, snapshots)
        assert response.status_code == 200, response.text
        assert len(response.json()["receipts"]) == len(doc["receipts"])
        assert response.json()["rejections"] == []


def test_missing_object_and_malformed_finalize_requests_have_no_ack(tmp_path):
    with client_for(tmp_path / "missing.sqlite") as client:
        assert post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": "sha256:" + "0" * 64}).status_code == 422
        for value in ({}, {"version": True, "object_id": "sha256:" + "0" * 64},
                      {"version": 1, "object_id": "bad"}):
            response = post(client, "/api/v1/nfl/generations", value)
            assert response.status_code == 422
            assert "durable" not in response.json()


@pytest.mark.parametrize("failure,status", [(OSError("sensitive-path"), 503), (JournalConflict("sensitive-id"), 409)])
def test_factory_failures_are_sanitized_not_saved(failure, status):
    def unavailable():
        raise failure
    with TestClient(create_app(store=FakeStore(), env=ENV, journal_factory=unavailable)) as client:
        response = post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": "sha256:" + "0" * 64})
        assert response.status_code == status
        assert "sensitive" not in response.text
        assert "durable" not in response.json()


def test_corrupt_store_bodies_are_rechecked_not_trusted(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, _, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, [], recorded)
    path = tmp_path / "corrupt-store.sqlite"
    with ReceiptJournal(path) as db:
        for object_id, payload in snapshots.items():
            db.append_object("source_snapshot", object_id, payload)
        db.append_object("generation", doc["envelope_id"], doc)
        assert validate_generation(db, doc["envelope_id"]) == doc
        original = db.get_object
        for target in (doc["envelope_id"], doc["generations"][0]["source_snapshots"]["leg_pool"]):
            def corrupt(object_id):
                result = original(object_id)
                if object_id == target:
                    result["payload"]["uncommitted_change"] = True
                return result
            monkeypatch.setattr(db, "get_object", corrupt)
            with pytest.raises(ValueError, match="hash_mismatch"):
                validate_generation(db, doc["envelope_id"])
        monkeypatch.setattr(db, "get_object", original)
        assert db.generation_ack(doc["envelope_id"]) is None


def test_unidentifiable_adapter_rejection_cannot_fake_complete_partition(tmp_path, monkeypatch):
    journal, _, _, pool, schedule, _, recorded = inputs(monkeypatch)
    doc, snapshots = journal.capture(pool, schedule, None, [], recorded)
    from services.web import nfl_ingestion
    monkeypatch.setattr(nfl_ingestion, "adapt_prospective", lambda **kwargs: {
        "receipts": [], "rejections": [{"source_receipt_id": None, "reason": "missing_source"}]})
    path = tmp_path / "partition.sqlite"
    with client_for(path) as client:
        response = upload(client, doc, snapshots)
        assert response.status_code == 422
    with ReceiptJournal(path) as db:
        assert db.receipts() == []
        assert db.generation_ack(doc["envelope_id"]) is None
