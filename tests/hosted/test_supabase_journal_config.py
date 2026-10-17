"""Explicit backend selection and schema readiness, without network or DDL."""
import base64
import copy
import json

import pytest
from fastapi.testclient import TestClient

from services.web import application
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict, ReceiptJournal
from selflearn_core.store.supabase_store import StoreConflict, StoreError
from .fakes import FakeStore, TEST_KEY
from .test_nfl_ingestion import INGEST_KEY

SECRET = "sb_secret_test_backend_not_a_real_credential"
ORIGIN = "https://offline.supabase.invalid"
ENV = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": INGEST_KEY,
       "SUPABASE_URL": ORIGIN, "SUPABASE_SECRET_KEY": SECRET}
READY = {"schema_version": 1, "backend": "postgres", "watermark": "0"}


def legacy_key(role="service_role"):
    payload = base64.urlsafe_b64encode(json.dumps({"role": role}).encode()).decode().rstrip("=")
    return "header." + payload + ".fixture-signature-not-authentication"


def post(client, token=INGEST_KEY):
    payload = {"source": "offline fixed fixture"}
    value = {"version": 1, "kind": "source_snapshot", "object_id": fingerprint(payload), "payload": payload}
    return client.post("/api/v1/nfl/objects", json=value,
                       headers={"Authorization": "Bearer " + token})


def status(client):
    return client.get("/api/status", headers={"Authorization": "Bearer " + TEST_KEY})


class ProbeJournal:
    """Factory test double only. Actual SQL is exercised separately."""
    readiness = READY
    failure = None
    write_failure = None
    calls = []

    def __init__(self, origin, key):
        self.calls.append(("init", origin, key))

    def __enter__(self):
        self.calls.append(("enter",))
        return self

    def __exit__(self, *args):
        self.calls.append(("exit",))

    def status(self):
        self.calls.append(("status",))
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.readiness)

    def append_object(self, kind, object_id, payload):
        self.calls.append(("write", kind, object_id))
        if self.write_failure:
            raise self.write_failure
        return True


@pytest.fixture
def probe(monkeypatch):
    class LocalProbe(ProbeJournal):
        calls = []
    monkeypatch.setattr(application, "SupabaseReceiptJournal", LocalProbe)
    return LocalProbe


@pytest.mark.parametrize("selection", [None, "", "disabled"])
def test_credentials_alone_never_enable_learning(probe, selection):
    env = {**ENV}
    if selection is not None:
        env["SELFLEARN_JOURNAL_BACKEND"] = selection
    with TestClient(application.create_app(store=FakeStore(), env=env)) as client:
        response = post(client)
        assert response.status_code == 503 and "durable" not in response.json()
        assert status(client).json()["nfl_ingestion"] == {
            "protocol_version": 1, "backend": "disabled", "journal_configured": False,
            "configuration_valid": True, "readiness": "disabled", "schema_verified": False,
            "production_connection_verified": False}
        assert client.get("/api/health").status_code == 200
    assert probe.calls == []


@pytest.mark.parametrize("selection", ["sqlite", "memory", "Supabase", " supabase ", "unexpected-secret"])
def test_unknown_backend_is_not_a_local_fallback(probe, selection):
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": selection})) as client:
        response = post(client)
        assert response.status_code == 503 and selection not in response.text
        info = status(client).json()["nfl_ingestion"]
        assert info["backend"] == "invalid" and info["configuration_valid"] is False
        assert info["schema_verified"] is False and info["readiness"] == "invalid_configuration"
    assert probe.calls == []


@pytest.mark.parametrize("overrides", [
    {"SUPABASE_URL": ""}, {"SUPABASE_URL": "http://insecure.invalid"},
    {"SUPABASE_URL": "https://user:secret@offline.supabase.invalid"},
    {"SUPABASE_URL": "https://offline.supabase.invalid?secret=not-for-logs"},
    {"SUPABASE_SECRET_KEY": ""}, {"SUPABASE_SECRET_KEY": "sb_publishable_browser_key"},
    {"SUPABASE_SECRET_KEY": "not-a-backend-key"}, {"SUPABASE_SECRET_KEY": "sb_secret_has whitespace"},
    {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": legacy_key("anon")},
    {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "header.invalid.signature"},
    {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": "header..signature"},
    {"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": legacy_key(["service_role"])},
    {"SUPABASE_SECRET_KEY": "bad-preferred-key", "SUPABASE_SERVICE_ROLE_KEY": legacy_key()},
])
def test_bad_configuration_fails_without_constructing_journal(probe, overrides, caplog):
    env = {**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase", **overrides}
    with TestClient(application.create_app(store=FakeStore(), env=env)) as client:
        response = post(client)
        assert response.status_code == 503
        info = status(client).json()["nfl_ingestion"]
        assert info["journal_configured"] is False and info["configuration_valid"] is False
        assert info["readiness"] == "invalid_configuration"
        for value in overrides.values():
            if value:
                assert value not in response.text and value not in caplog.text
    assert probe.calls == []


@pytest.mark.parametrize("key_config,selected", [
    ({"SUPABASE_SECRET_KEY": SECRET}, SECRET),
    ({"SUPABASE_SECRET_KEY": "", "SUPABASE_SERVICE_ROLE_KEY": legacy_key()}, legacy_key()),
])
def test_explicit_supabase_checks_schema_before_every_ack(probe, key_config, selected):
    env = {**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase", **key_config}
    app = application.create_app(store=FakeStore(), env=env)
    assert probe.calls == []
    with TestClient(app) as client:
        for _ in range(2):
            response = post(client)
            assert response.status_code == 200 and response.json()["durable"] is True
        assert [c[0] for c in probe.calls] == ["init", "enter", "status", "write", "exit"] * 2
        assert all(c[1:] == (ORIGIN, selected) for c in probe.calls if c[0] == "init")
        result = status(client).json()
        info = result["nfl_ingestion"]
        assert info["journal_configured"] is True and info["schema_verified"] is True
        assert info["readiness"] == "verified" and info["journal_schema_version"] == 1
        assert info["production_connection_verified"] is False
        assert result["capabilities"]["nfl2026_connected"] is False
        assert result["capabilities"]["automatic_promotion"] is False
        assert selected not in json.dumps(result)
        assert len([c for c in probe.calls if c[0] == "write"]) == 2


@pytest.mark.parametrize("readiness", [
    None, {}, {**READY, "schema_version": 2}, {**READY, "schema_version": True},
    {**READY, "backend": "sqlite"}, {**READY, "watermark": None},
    {**READY, "watermark": "-1"}, {**READY, "watermark": "01"},
])
def test_wrong_schema_never_acknowledges(probe, readiness):
    probe.readiness = readiness
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase"})) as client:
        for response in (post(client), status(client)):
            assert response.status_code == 503 and "durable" not in response.json()
    assert not any(c[0] == "write" for c in probe.calls)


@pytest.mark.parametrize("failure", [StoreError(404), StoreError(403), StoreError(401), StoreError(502),
                                      StoreConflict(), ValueError("sensitive SQL error"), OSError("sensitive path")])
def test_readiness_failure_is_sanitized_not_cached(probe, failure, caplog):
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase"})) as client:
        assert post(client).status_code == 200
        probe.failure = failure
        response = post(client)
        assert response.status_code == 503
        assert "sensitive" not in response.text and "sensitive" not in caplog.text
        assert SECRET not in response.text and SECRET not in caplog.text
        assert len([c for c in probe.calls if c[0] == "write"]) == 1
        assert len([c for c in probe.calls if c[0] == "status"]) == 2


@pytest.mark.parametrize("failure", [ValueError("sensitive constructor"), OSError("sensitive socket")])
def test_runtime_constructor_failure_is_not_caller_validation_or_fallback(monkeypatch, failure, caplog):
    def broken_constructor(*args):
        raise failure
    monkeypatch.setattr(application, "SupabaseReceiptJournal", broken_constructor)
    env = {**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase", "VERCEL": "1"}
    with TestClient(application.create_app(store=FakeStore(), env=env)) as client:
        response = post(client)
        assert response.status_code == 503
        assert "sensitive" not in response.text and "sensitive" not in caplog.text


def test_data_conflict_after_readiness_still_returns_409(probe):
    probe.write_failure = JournalConflict("sensitive immutable conflict")
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase"})) as client:
        response = post(client)
        assert response.status_code == 409 and "sensitive" not in response.text


def test_wrong_credential_scope_is_rejected_before_database_io(probe):
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase"})) as client:
        assert post(client, token=TEST_KEY).status_code == 401
        assert post(client, token="wrong-key").status_code == 401
        assert client.get("/api/status", headers={"Authorization": "Bearer " + INGEST_KEY}).status_code == 401
    assert probe.calls == []


def test_injected_local_factory_keeps_existing_behavior(tmp_path, probe):
    path = tmp_path / "explicit-local-test.sqlite"
    with TestClient(application.create_app(store=FakeStore(), env={**ENV, "SELFLEARN_JOURNAL_BACKEND": "unexpected"},
                                          journal_factory=lambda: ReceiptJournal(path))) as client:
        assert post(client).status_code == 200
        info = status(client).json()["nfl_ingestion"]
        assert info["backend"] == "injected" and info["readiness"] == "injected_unverified"
        assert info["schema_verified"] is False
    assert path.is_file() and probe.calls == []
