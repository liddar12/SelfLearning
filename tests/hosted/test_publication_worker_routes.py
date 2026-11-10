"""Actual scoped enqueue/run/read routes with one persistent SQLite authority."""
from contextlib import contextmanager
import json

import pytest
from fastapi.testclient import TestClient

from services.web import application
from services.web.nfl_publication_verifier import select_verifier
from services.web.publication_worker_routes import select_worker_factory
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from .fakes import FakeStore, TEST_KEY
from .publication_fixture import graph
from .test_nfl_publication import ENV as PUBLIC_ENV, PUBLICATION, INGEST, OUTCOME, CORRECTIONS, upload, post, transport_for

WORKER = "offline-publication-worker-" + "w" * 40
AUTHORITY = fingerprint("offline-publication-worker-authority")
ENV = {**PUBLIC_ENV, "SELFLEARN_PUBLICATION_WORKER_MODE": "record",
       "SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID": AUTHORITY,
       "SELFLEARN_NFL_PUBLICATION_WORKER_TOKEN": WORKER}
JOBS = "/api/v1/nfl/publication-jobs"
RUN = "/api/v1/nfl/publication-worker"


def pair_for(path, *, other_path=None, authority=AUTHORITY, calls=None):
    @contextmanager
    def pair():
        if calls is not None:
            calls.append("pair")
        with LearningJobJournal(path, authority_id=authority) as jobs, NFLPublicationJournal(other_path or path) as journal:
            yield jobs, journal
    return pair


def client_for(path, fetch, *, env=None, pair=None):
    return TestClient(application.create_app(store=FakeStore(), env=ENV if env is None else env,
        journal_factory=lambda: NFLPublicationJournal(path), publication_transport=fetch,
        publication_job_factory=pair or pair_for(path)))


def test_actual_routes_restart_worker_and_keep_intake_and_publication_immutable(tmp_path):
    path = tmp_path / "worker.sqlite"
    doc, sources = graph()
    calls = []
    fetch, _ = transport_for(sources["leg_pool"], calls)
    with client_for(path, fetch) as client:
        upload(client, doc, sources)
        with NFLPublicationJournal(path) as journal:
            original = journal.receipts(), journal.generations()
        response = post(client, JOBS, {"version": 1, "object_id": doc["envelope_id"]})
        assert response.status_code == 200, response.text
        job_id = response.json()["definition"]["job_id"]
        repeated = post(client, JOBS, {"version": 1, "object_id": doc["envelope_id"]})
        assert repeated.status_code == 200 and repeated.json()["definition"] == response.json()["definition"]
        assert calls == [], "enqueue must not observe the public pool"

    # Fresh HTTP app and fresh database connections, no chat/process-local queue.
    with client_for(path, fetch) as client:
        worked = post(client, RUN, {"version": 1}, WORKER)
        assert worked.status_code == 200, worked.text
        observed = client.get(JOBS + "/" + job_id, headers={"Authorization": "Bearer " + PUBLICATION})
        assert observed.status_code == 200, observed.text
        assert observed.json()["effective_status"] == "succeeded"
        assert observed.json()["activation"] is False
        assert observed.headers["cache-control"] == "no-store"
        rerun = client.get(RUN, headers={"Authorization": "Bearer " + WORKER})
        assert rerun.status_code == 200, rerun.text
        assert len(calls) == 1
        status = client.get("/api/status", headers={"Authorization": "Bearer " + TEST_KEY}).json()
        assert status["nfl_publication_worker"]["configured"] is True
        assert status["nfl_publication_worker"]["scheduler_verified"] is False
    with NFLPublicationJournal(path) as journal:
        assert (journal.receipts(), journal.generations()) == original
        proof = journal.get_publication_attestation(doc["envelope_id"])
        assert proof["status"] == "accepted"
        assert journal.resolution_history(proof["receipts"][0]["receipt_id"]) == []


def test_worker_records_public_bytes_above_old_bound_and_retains_exact_body_size(tmp_path):
    # Actual Nov-9 NFL pretty pool was 3,216,964 bytes; its canonical ingestion
    # request was only 2,045,960. Whitespace changes byte size, not pool identity.
    path = tmp_path / "large-public-pool.sqlite"
    doc, sources = graph()
    raw = json.dumps(sources["leg_pool"]).encode()
    raw += b" " * (3216964 - len(raw))
    calls = []
    def fetch(url):
        calls.append(url)
        return 200, {"Content-Type": "application/json"}, raw, url
    with client_for(path, fetch) as client:
        upload(client, doc, sources)
        queued = post(client, JOBS, {"version": 1, "object_id": doc["envelope_id"]})
        assert queued.status_code == 200, queued.text
        ran = post(client, RUN, {"version": 1}, WORKER)
        assert ran.status_code == 200 and ran.json()["recording_complete"] is True, ran.text
    with NFLPublicationJournal(path) as journal:
        proof = journal.get_publication_attestation(doc["envelope_id"])
        assert proof["observation"]["body_bytes"] == len(raw)
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"SELFLEARN_PUBLICATION_WORKER_MODE": "off"},
    {"SELFLEARN_PUBLICATION_WORKER_MODE": ""},
    {"SELFLEARN_PUBLICATION_WORKER_MODE": True},
    {"SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID": "bad"},
    {"SELFLEARN_PUBLICATION_MODE": "off"},
    {"SELFLEARN_NFL_PUBLICATION_ORIGIN": ""},
])
def test_disabled_or_invalid_worker_never_opens_ports_or_observes(tmp_path, changes):
    opened, fetched = [], []
    path = tmp_path / "disabled.sqlite"
    with client_for(path, lambda *_: fetched.append("fetch"), env={**ENV, **changes},
                    pair=pair_for(path, calls=opened)) as client:
        assert post(client, JOBS, {"version": 1, "object_id": fingerprint("absent")}).status_code == 503
        assert post(client, RUN, {"version": 1}, WORKER).status_code == 503
    assert opened == fetched == []
    assert not path.exists()


def test_scopes_are_distinct_and_clients_cannot_supply_worker_controls(tmp_path):
    path = tmp_path / "scopes.sqlite"
    opened, fetched = [], []
    with client_for(path, lambda *_: fetched.append("fetch"), pair=pair_for(path, calls=opened)) as client:
        for token in (TEST_KEY, INGEST, OUTCOME, CORRECTIONS, PUBLICATION, ""):
            assert post(client, RUN, {"version": 1}, token).status_code == 401
        for token in (TEST_KEY, INGEST, OUTCOME, CORRECTIONS, WORKER, ""):
            assert post(client, JOBS, {"version": 1, "object_id": fingerprint("absent")}, token).status_code == 401
        for extra in ({"job_id": fingerprint("selected")}, {"worker_id": "mine"}, {"limit": 100},
                      {"code_id": fingerprint("new-code")}, {"lease_seconds": 1000}):
            assert post(client, RUN, {"version": 1, **extra}, WORKER).status_code == 422
        assert post(client, RUN, {"version": True}, WORKER).status_code == 422
        assert client.get(RUN + "?job_id=x", headers={"Authorization": "Bearer " + WORKER}).status_code == 422
        for extra in ({"not_after_utc": "2099-01-01T00:00:00Z"}, {"origin": "https://wrong.example.com"},
                      {"authority_id": fingerprint("other")}):
            assert post(client, JOBS, {"version": 1, "object_id": fingerprint("absent"), **extra}).status_code == 422
    assert opened == fetched == []


@pytest.mark.parametrize("collision", [TEST_KEY, INGEST, OUTCOME, CORRECTIONS, PUBLICATION])
def test_worker_credential_collision_fails_closed_before_ports(tmp_path, collision):
    opened = []
    path = tmp_path / "collision.sqlite"
    with client_for(path, lambda *_: pytest.fail("HTTP must not run"),
                    env={**ENV, "SELFLEARN_NFL_PUBLICATION_WORKER_TOKEN": collision},
                    pair=pair_for(path, calls=opened)) as client:
        assert post(client, RUN, {"version": 1}, collision).status_code == 503
    assert opened == []


def test_split_database_or_authority_injected_pair_is_refused_without_job_mutation(tmp_path):
    for name, options in [("database", {"other_path": tmp_path / "other.sqlite"}),
                          ("authority", {"authority": fingerprint("wrong authority")})]:
        path = tmp_path / (name + ".sqlite")
        with client_for(path, lambda *_: pytest.fail("HTTP must not run"), pair=pair_for(path, **options)) as client:
            assert post(client, JOBS, {"version": 1, "object_id": fingerprint("absent")}).status_code == 503
        with LearningJobJournal(path, authority_id=options.get("authority", AUTHORITY)) as jobs:
            assert jobs._db.execute("SELECT count(*) FROM learning_jobs_v1").fetchone()[0] == 0


def test_configuration_is_inert_and_production_ports_share_one_transport(monkeypatch):
    calls = []
    shared = object()
    class Journal:
        def __init__(self, url, key):
            calls.append(("journal", url, key)); self._request = shared
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def status(self): return {"backend": "postgres", "schema_version": 1, "watermark": "0"}
    class Jobs:
        def __init__(self, url, key, *, authority_id, transport):
            calls.append(("jobs", url, key, authority_id, transport)); self.authority_id = authority_id
        def __enter__(self): return self
        def __exit__(self, *_): pass
    monkeypatch.setattr(application, "NFLSupabasePublicationJournal", Journal)
    monkeypatch.setattr(application, "SupabaseLearningJobJournal", Jobs)
    env = {**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase", "SUPABASE_URL": "https://paired.supabase.invalid",
           "SUPABASE_SECRET_KEY": "sb_secret_offline_configuration_only"}
    journal_factory, _ = application._journal_selection(env, None)
    factory, status = select_worker_factory(env, None, journal_factory, select_verifier(env))
    assert calls == [] and status["readiness"] == "configured_unverified"
    with factory() as (jobs, journal):
        assert jobs.authority_id == AUTHORITY
    assert calls[0][1:3] == calls[1][1:3]
    assert calls[1][-1] is shared


def test_unknown_job_namespace_is_not_exposed_and_raw_store_errors_are_redacted(tmp_path):
    class Broken:
        authority_id = AUTHORITY
        def read_job(self, _job): raise RuntimeError("db password provider diagnostic sensitive")
    @contextmanager
    def pair(): yield Broken(), object()
    with client_for(tmp_path / "redacted.sqlite", lambda *_: None, pair=pair) as client:
        response = client.get(JOBS + "/" + fingerprint("absent"), headers={"Authorization": "Bearer " + PUBLICATION})
        assert response.status_code == 503
        assert "password" not in response.text and "provider diagnostic" not in response.text
    class Other(Broken):
        def read_job(self, _job):
            return {"definition": {"namespace": {"domain": "other", "task": "private", "producer_id": "other"},
                                   "operation": "private.operation"}, "private_payload": "must not leak"}
    @contextmanager
    def other_pair(): yield Other(), object()
    with client_for(tmp_path / "other-job.sqlite", lambda *_: None, pair=other_pair) as client:
        response = client.get(JOBS + "/" + fingerprint("other"), headers={"Authorization": "Bearer " + PUBLICATION})
        assert response.status_code == 404 and "must not leak" not in response.text
