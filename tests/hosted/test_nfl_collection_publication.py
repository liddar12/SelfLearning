"""Actual scoped collection and publication routes on durable local SQLite."""
import hashlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from selflearn_core import learning_jobs as dto
from selflearn_core.adapters import nfl_collection_publication as publication
from selflearn_core.adapters.nfl_collection_handoff import SQLiteCollectionHandoff
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from services.web import application, nfl_publication_jobs as worker
from .fakes import FakeStore
from .publication_fixture import graph
from .test_nfl_collection_handoff import BASE, COLLECTION, ENV as HANDOFF_ENV, call, endpoint, intent_for, record, upload
from .test_nfl_publication import transport_for
from .test_publication_worker_routes import AUTHORITY, ENV as WORKER_ENV, RUN, WORKER, pair_for

CODE = fingerprint("original collection worker release")
ENV = {**WORKER_ENV, **HANDOFF_ENV}


def client_for(path, fetch, *, env=ENV, pair=None):
    return TestClient(application.create_app(store=FakeStore(), env=env,
        journal_factory=lambda: NFLPublicationJournal(path), collection_handoff_store=SQLiteCollectionHandoff,
        publication_transport=fetch, publication_job_factory=pair or pair_for(path,
            authority=env["SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID"])))


def prepare(client, doc, sources, *, publisher=None, finalize=True):
    intent = intent_for(doc)
    uri = endpoint(intent)
    opened = call(client, "POST", BASE, intent)
    upload(client, doc, sources)
    request = {"version": 1, "intent_id": opened["intent"]["intent_id"]}
    if finalize:
        call(client, "POST", uri + "/recover", request)
    call(client, "POST", uri + "/publisher", record(opened, **(publisher or {})))
    return uri + "/publication", request


def test_lost_stage_and_enqueue_replies_recover_same_definition_across_restarts(tmp_path):
    doc, sources = graph()
    calls = []
    fetch, raw = transport_for(sources["leg_pool"], calls)
    db = tmp_path / "original.sqlite"
    real_prepare = publication.SQLiteCollectionPublication.prepare
    real_register = publication.SQLiteCollectionPublication.register

    def lose_preparation(store, *args):
        real_prepare(store, *args)
        raise OSError("fixture lost prepared response")

    def lose_registration(store, *args):
        real_register(store, *args)
        raise OSError("fixture lost register response")

    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, fetch) as first:
        uri, request = prepare(first, doc, sources)
        ready = call(first, "GET", uri)
        assert ready["state"] == "awaiting_publication" and ready["prepared"] is None
        with NFLPublicationJournal(db) as journal:
            assert journal._db.execute("SELECT 1 FROM sqlite_master WHERE name='nfl_collection_publication_v1'").fetchone() is None
        with patch.object(publication.SQLiteCollectionPublication, "prepare", lose_preparation):
            call(first, "POST", uri, request, expected=503)
    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, fetch) as second:
        staged = call(second, "GET", uri)
        assert staged["state"] == "prepared" and staged["job"] is None
        assert staged["prepared"]["definition"]["code_id"] == CODE
        with patch.object(publication.SQLiteCollectionPublication, "register", lose_registration):
            call(second, "POST", uri, request, expected=503)
    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, fetch) as third:
        found = call(third, "GET", uri)
        assert found["state"] == "registered" and found["prepared"] == staged["prepared"]
        assert found["job_registered"] and not found["publication_verified"]
        assert len(call(third, "GET", BASE + "/pending")["pending"]) == 1
        assert calls == []
        result = call(third, "POST", RUN, {"version": 1}, token=WORKER)
        assert result["recording_complete"]
        completed = call(third, "GET", uri)
        assert completed["state"] == "job_terminal"
        assert completed["recording_complete"] and completed["publication_verified"]
        assert completed["training_approved"] is False
        assert completed["job"]["attestation"]["observation"]["body_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert call(third, "GET", BASE + "/pending")["pending"] == []

    changed = {**ENV, "SELFLEARN_NFL_PUBLICATION_ORIGIN": "https://another-policy.example.com",
               "SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID": fingerprint("new configured authority")}
    with patch.object(worker, "code_identity", side_effect=AssertionError("original read must ignore current code")), \
            patch.object(SQLiteCollectionHandoff, "_now", return_value=doc["receipts"][0]["kickoff_utc"]), \
            client_for(db, lambda _: pytest.fail("original read must never refetch"), env=changed) as restarted:
        for method in ("GET", "POST"):
            recovered = call(restarted, method, uri, request if method == "POST" else None)
            assert recovered["prepared"] == staged["prepared"]
            assert recovered["job"]["result"] == completed["job"]["result"]
            assert recovered["job"]["attestation"] == completed["job"]["attestation"]
    with NFLPublicationJournal(db) as journal:
        assert journal._db.execute("SELECT count(*) FROM nfl_collection_publication_v1").fetchone()[0] == 1
        assert journal._db.execute("SELECT count(*) FROM learning_jobs_v1").fetchone()[0] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("change", ["code", "policy", "authority"])
def test_prepared_original_with_changed_declaration_stays_held(tmp_path, change):
    doc, sources = graph()
    db = tmp_path / "changed.sqlite"
    fetch = lambda _: pytest.fail("enqueue must never observe")
    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, fetch) as first:
        uri, request = prepare(first, doc, sources)
        with patch.object(worker, "enqueue_pinned", side_effect=OSError("fixture interruption")):
            call(first, "POST", uri, request, expected=503)
        original = call(first, "GET", uri)["prepared"]
    changed = dict(ENV)
    if change == "policy":
        changed["SELFLEARN_NFL_PUBLICATION_ORIGIN"] = "https://another-policy.example.com"
    if change == "authority":
        changed["SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID"] = fingerprint("new authority")
    with patch.object(worker, "code_identity", return_value=fingerprint("new code") if change == "code" else CODE), \
            client_for(db, fetch, env=changed) as restarted:
        for method in ("GET", "POST"):
            held = call(restarted, method, uri, request if method == "POST" else None)
            assert held["state"] == "held_declaration_changed" and held["prepared"] == original
            assert not held["job_registered"]
    with NFLPublicationJournal(db) as journal:
        assert journal._db.execute("SELECT count(*) FROM learning_jobs_v1").fetchone()[0] == 0


@pytest.mark.parametrize("why", ["noop", "mismatch", "unchanged", "unfinalized"])
def test_holds_invalid_publisher_or_missing_intake_without_preparing(tmp_path, why):
    doc, sources = graph()
    db = tmp_path / "gate.sqlite"
    updates = {"noop": {"published_sha": None, "published_pool_id": None},
               "mismatch": {"published_pool_id": fingerprint("other pool")},
               "unchanged": {"previous_pool_id": fingerprint(sources["leg_pool"])},
               "unfinalized": {}}[why]
    with client_for(db, lambda _: pytest.fail("invalid attempt must never observe")) as client:
        uri, request = prepare(client, doc, sources, publisher=updates, finalize=why != "unfinalized")
        result = call(client, "POST", uri, request)
        assert result["state"] == ("ready_to_finalize" if why == "unfinalized" else "held_publisher_" + why)
        assert result["prepared"] is None and not result["job_registered"]
        call(client, "POST", uri, {**request, "code_id": CODE}, expected=422)
        call(client, "POST", uri, {**request, "intent_id": fingerprint("different attempt")}, expected=409)
        call(client, "GET", uri, token=WORKER, expected=401)


def test_cutoff_crosses_while_registering_and_rolls_back_only_new_job(tmp_path):
    doc, sources = graph()
    db = tmp_path / "cutoff.sqlite"
    kickoff = doc["receipts"][0]["kickoff_utc"]
    real_clock, real_reference = LearningJobJournal._now, LearningJobJournal._reference
    crossed = []

    def advancing_reference(jobs, *args):
        real_reference(jobs, *args)
        crossed.append(True)

    def clock(jobs):
        return kickoff if crossed else real_clock(jobs)

    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, lambda _: pytest.fail("no public fetch")) as client:
        uri, request = prepare(client, doc, sources)
        with patch.object(LearningJobJournal, "_reference", advancing_reference), patch.object(LearningJobJournal, "_now", clock):
            held = call(client, "POST", uri, request)
        assert crossed and held["state"] == "held_late" and not held["job_registered"]
        assert held["prepared"] is not None
        with patch.object(SQLiteCollectionHandoff, "_now", return_value=kickoff):
            assert call(client, "GET", uri)["state"] == "held_late"
            again = call(client, "POST", uri, request)
            assert again["state"] == "held_late" and again["prepared"] == held["prepared"]
    with NFLPublicationJournal(db) as journal:
        assert journal._db.execute("SELECT count(*) FROM nfl_collection_publication_v1").fetchone()[0] == 1
        assert journal._db.execute("SELECT count(*) FROM learning_jobs_v1").fetchone()[0] == 0


def test_actual_failed_terminal_job_stays_unverified_and_leaves_pending(tmp_path):
    doc, sources = graph()
    db = tmp_path / "failed.sqlite"
    with patch.object(worker, "code_identity", return_value=CODE), client_for(db, lambda _: pytest.fail("no public fetch")) as client:
        uri, request = prepare(client, doc, sources)
        registered = call(client, "POST", uri, request)
        job_id = registered["prepared"]["definition"]["job_id"]
        with LearningJobJournal(db, authority_id=AUTHORITY) as jobs:
            claim = {"version": 1, "kind": "selflearn.job_command.v1", "job_id": job_id,
                     "expected_head_event_id": None, "action": "claim", "worker_id": "fixture-terminal",
                     "lease_id": None, "object_id": None, "reason_code": None}
            first = jobs.append_job_command(dto.seal_command(claim, authority_id=AUTHORITY))
            fail = {**claim, "expected_head_event_id": first["event_id"], "action": "fail",
                    "lease_id": first["state"]["lease_id"], "reason_code": "invalid_input"}
            jobs.append_job_command(dto.seal_command(fail, authority_id=AUTHORITY))
        terminal = call(client, "GET", uri)
        assert terminal["state"] == "job_terminal"
        assert terminal["job"]["snapshot"]["effective_status"] == "failed"
        assert terminal["job_registered"] and not terminal["recording_complete"] and not terminal["publication_verified"]
        assert call(client, "GET", BASE + "/pending")["pending"] == []


def test_disabled_and_split_pairstore_never_prepares(tmp_path):
    doc, sources = graph()
    db = tmp_path / "primary.sqlite"
    fetch = lambda _: pytest.fail("no public fetch")
    with client_for(db, fetch, env={**ENV, "SELFLEARN_PUBLICATION_WORKER_MODE": "off"}) as client:
        uri, request = prepare(client, doc, sources)
        call(client, "POST", uri, request, expected=503)
    with client_for(db, fetch, pair=pair_for(tmp_path / "wrong.sqlite")) as client:
        call(client, "POST", uri, request, expected=503)
    with NFLPublicationJournal(db) as journal:
        assert journal._db.execute("SELECT 1 FROM sqlite_master WHERE name='nfl_collection_publication_v1'").fetchone() is None
