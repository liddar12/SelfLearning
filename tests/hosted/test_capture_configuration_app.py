"""Real app composition with a FAKE HTTP transport, not hosted acceptance."""
import copy

import pytest
from fastapi.testclient import TestClient

from services.web import capture_configuration as config
from services.web.application import create_app
from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import StoreError, SupabaseStore
from .fakes import FakeStore, TEST_KEY


CAPTURE_KEY = "fixture-capture-composition-key-" + "c" * 32
GENERATION_KEY = "fixture-generation-composition-key-" + "g" * 32
ENV = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": GENERATION_KEY,
       "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": CAPTURE_KEY,
       "SELFLEARN_CAPTURE_BACKEND": "supabase", "SUPABASE_URL": "https://offline.supabase.invalid",
       "SUPABASE_SECRET_KEY": "sb_secret_fixture_never_a_real_key",
       "SELFLEARN_CAPTURE_EVIDENCE_BUCKET": "fixture-capture-private"}
STATUS = "/rest/v1/rpc/selflearn_journal_read_observed_v1"
DELIVERY = "/rest/v1/rpc/selflearn_journal_read_capture_delivery_v1"
QUOTA = "/rest/v1/rpc/selflearn_reserve_capture_stage_v1"
CHUNKS = "/api/v1/nfl/capture-chunks"


@pytest.fixture
def transport(monkeypatch):
    class FakeTransport:
        def __init__(self):
            self.calls, self.objects, self.reservations = [], {}, {}
            self.failure = None

        def request(self, store, method, path, **kwargs):
            self.calls.append((method, path, copy.deepcopy(kwargs)))
            if path == self.failure:
                raise StoreError(503)
            if path == STATUS:
                return {"version": 1, "rows": [{"schema_version": 1, "backend": "postgres", "journal_seq": "0"}],
                        "next_after": None, "watermark": "0"}
            if path == DELIVERY:
                return {"version": 1, "result": None, "groups": [], "next_after": None, "total_groups": 0}
            if path == QUOTA:
                body = kwargs["data"]
                # The real adapter supplies a strictly validated envelope.
                assert body["p_namespace_id"] == config._NAMESPACE
                cid, size = body["p_chunk_id"], body["p_raw_bytes"]
                identity = {"version": 1, "namespace_id": config._NAMESPACE, "chunk_id": cid, "raw_bytes": size}
                self.reservations.setdefault(cid, {**identity, "kind": "selflearn.capture_stage_reservation.v1",
                    "reservation_id": fingerprint(identity), "reserved_total_bytes": size, "reserved_chunks": 1,
                    "recorded_utc": "2026-10-16T00:00:00Z", "scope": "staging_budget_only",
                    "quota_reserved": True, "learning_accepted": False, "durable_ack": False})
                return copy.deepcopy(self.reservations[cid])
            if path == "/storage/v1/bucket/fixture-capture-private":
                return {"id": "fixture-capture-private", "public": False}
            prefix = "/storage/v1/object/fixture-capture-private/"
            authenticated = "/storage/v1/object/authenticated/fixture-capture-private/"
            if method == "POST" and path.startswith(prefix):
                assert kwargs.get("raw") is not None
                self.objects[path.removeprefix(prefix)] = kwargs["raw"]
                return {"not_an_integrity_ack": True}
            if method == "GET" and path.startswith(authenticated):
                return self.objects[path.removeprefix(authenticated)]
            raise AssertionError("Unexpected fake transport request")

    fake = FakeTransport()
    monkeypatch.setattr(SupabaseStore, "request", lambda store, method, path, **kwargs:
                        fake.request(store, method, path, **kwargs))
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *args, **kwargs:
                        pytest.fail("Network access is forbidden in composition tests"))
    return fake


def headers(token=CAPTURE_KEY):
    return {"Authorization": "Bearer " + token}


def test_default_off_with_all_tokens_and_credentials_does_not_probe(transport):
    env = {key: value for key, value in ENV.items() if key != "SELFLEARN_CAPTURE_BACKEND"}
    with TestClient(create_app(store=FakeStore(), env=env)) as client:
        status = client.get("/api/status", headers=headers(TEST_KEY))
        assert status.status_code == 200
        assert status.json()["nfl_outcome_intake"]["readiness"] == "disabled"
        response = client.post(CHUNKS, json=make_chunk(b"not enabled"), headers=headers())
        assert response.status_code == 503 and "disabled" in response.text
    assert transport.calls == []


def test_explicit_selection_composes_real_ports_quota_before_private_readback(transport):
    app = create_app(store=FakeStore(), env=ENV)
    assert transport.calls == []  # Selecting the new backend performs no HTTP.
    with TestClient(app) as client:
        status = client.get("/api/status", headers=headers(TEST_KEY)).json()
        selected = status["nfl_outcome_intake"]
        assert selected["readiness"] == "configured_unverified"
        assert selected["production_connection_verified"] is False
        assert selected["working_learning_loop"] is False
        assert selected["model_adoption"] == "unverified"
        assert status["nfl_ingestion"]["backend"] == "disabled"
        assert transport.calls == []  # Overview does not open capture resources.
        dto = make_chunk(b"verified fake transport bytes")
        response = client.post(CHUNKS, json=dto, headers=headers())
        assert response.status_code == 200, response.text
        assert response.headers["Cache-Control"] == "no-store"
        result = response.json()
        assert result["learning_accepted"] is result["durable_ack"] is False
        assert result["scope"] == "uploaded_only"
        paths = [call[1] for call in transport.calls]
        assert paths[:3] == [STATUS, DELIVERY, QUOTA]
        assert len(transport.objects) == len(transport.reservations) == 1
        assert next(iter(transport.objects)).startswith(config._STORAGE_NAMESPACE + "/")
        assert paths[-1].startswith("/storage/v1/object/authenticated/")
        missing = client.get("/api/v1/nfl/capture-deliveries/" + fingerprint("not recorded"), headers=headers())
        assert missing.status_code == 404
        # A successful stage never marks the static status as live learning.
        assert client.get("/api/status", headers=headers(TEST_KEY)).json()["nfl_outcome_intake"] == selected


@pytest.mark.parametrize("path", [STATUS, DELIVERY, QUOTA])
def test_missing_read_rpc_or_quota_fails_without_private_write(transport, path):
    transport.failure = path
    with TestClient(create_app(store=FakeStore(), env=ENV)) as client:
        response = client.post(CHUNKS, json=make_chunk(b"held"), headers=headers())
        assert response.status_code == 503
        assert "offline.supabase" not in response.text and "sb_secret" not in response.text
    assert transport.objects == {} and not any("/storage/" in call[1] for call in transport.calls)


@pytest.mark.parametrize("selector", ["Supabase", False, "SECRET-selector"])
def test_bad_selector_private_status_without_fallback(transport, selector):
    with TestClient(create_app(store=FakeStore(), env={**ENV, "SELFLEARN_CAPTURE_BACKEND": selector})) as client:
        status = client.get("/api/status", headers=headers(TEST_KEY))
        assert status.status_code == 200
        assert status.json()["nfl_outcome_intake"]["readiness"] == "invalid_configuration"
        response = client.post(CHUNKS, json=make_chunk(b"held"), headers=headers())
        assert response.status_code == 503 and "SECRET-selector" not in response.text
    assert transport.calls == []


def test_authentication_precedes_backend_probe_and_scopes_are_separate(transport):
    with TestClient(create_app(store=FakeStore(), env=ENV)) as client:
        for token in (TEST_KEY, GENERATION_KEY, "wrong"):
            assert client.post(CHUNKS, json=make_chunk(b"held"), headers=headers(token)).status_code == 401
        assert client.get("/api/status", headers=headers()).status_code == 401
    assert transport.calls == []
