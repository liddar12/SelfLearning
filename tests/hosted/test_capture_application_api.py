"""In-process ASGI boundary tests; fixtures are NOT durable SQL evidence."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from services.web.nfl_capture_applications import CaptureApplicationReceiver
from services.web.security import MAX_LEARNING_JSON
from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import staging_namespace
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from selflearn_core.store.supabase_store import StoreError

CORE_TESTS = Path(__file__).resolve().parents[2] / "packages/selflearn-core/tests"
sys.path.insert(0, str(CORE_TESTS))
try:
    from test_supabase_evidence import FakeStorage
    from test_nfl_capture_delivery_job import request as fixture_request
    from test_nfl_capture_labels import good_capture
finally:
    sys.path.pop(0)

OWNER = "application-api-owner-fixture-" + "o" * 32
FORECAST = "application-api-forecast-fixture-" + "f" * 32
OUTCOME = "application-api-outcome-fixture-" + "c" * 32
ENV = {"SELFLEARN_OPERATOR_TOKEN": OWNER, "SELFLEARN_NFL_INGEST_TOKEN": FORECAST,
       "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": OUTCOME}
DESTINATION = fingerprint("local-v2-application-fixture")
PRODUCER = "nfl2026"
BASE = "/api/v2/nfl/capture-applications"
CHUNKS = "/api/v1/nfl/capture-chunks"


class FakeQuota:
    """Explicit memory fake; no durable reservation or quota acceptance claim."""
    def __init__(self, destination=DESTINATION, producer=PRODUCER):
        self.namespace = staging_namespace(destination_id=destination, producer_id=producer)
        self.rows, self.calls = {}, []

    def reserve(self, chunk_id, raw_bytes):
        self.calls.append((chunk_id, raw_bytes))
        if chunk_id not in self.rows:
            identity = {"version": 1, "namespace_id": self.namespace, "chunk_id": chunk_id, "raw_bytes": raw_bytes}
            self.rows[chunk_id] = {**identity, "kind": "selflearn.capture_stage_reservation.v1",
                "reservation_id": fingerprint(identity),
                "reserved_total_bytes": raw_bytes + sum(row["raw_bytes"] for row in self.rows.values()),
                "reserved_chunks": len(self.rows) + 1, "recorded_utc": "2026-10-16T00:00:00Z",
                "scope": "staging_budget_only", "quota_reserved": True,
                "learning_accepted": False, "durable_ack": False}
        return copy.deepcopy(self.rows[chunk_id])


class BoundaryJournal:
    """Only a fake interface for auth/parse/error checks, never outcome proof."""
    def __init__(self):
        self.calls, self.known = [], None
        self.failure = None

    def get_capture_application(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.failure:
            raise self.failure
        return copy.deepcopy(self.known)

    def get_capture_delivery(self, *args):
        raise AssertionError("No v1 read expected")

    def read_observed_snapshot(self):
        raise AssertionError("No full raw bind expected")

    def get_object(self, *args):
        raise AssertionError("No source read expected")

    def commit_capture_delivery(self, *args, **kwargs):
        raise AssertionError("No v1 write expected")

    def register_capture_application(self, *args, **kwargs):
        raise AssertionError("No v2 write expected")

    def commit_capture_application_batch(self, *args, **kwargs):
        raise AssertionError("No batch write expected")

    def stop_capture_application(self, *args, **kwargs):
        raise AssertionError("No terminal write expected")


def receiver_for(journal, *, destination=DESTINATION, producer=PRODUCER):
    transport = FakeStorage()
    evidence = SupabaseEvidenceStore(transport, bucket="private-evidence", namespace="application-api-fixture")
    quota = FakeQuota(destination, producer)
    receiver = CaptureApplicationReceiver(journal, evidence, quota,
        destination_id=destination, producer_id=producer)
    return receiver, transport, quota


def api_client(receiver=None, *, env=None, factory=None):
    if receiver is not None and factory is None:
        @contextmanager
        def factory():
            yield receiver
    # No other store-backed app route is exercised; no Supabase client is made.
    return TestClient(create_app(store=object(), env=ENV if env is None else env, capture_factory=factory))


def headers(token=OUTCOME):
    return {"Authorization": "Bearer " + token}


def intent(*, destination=DESTINATION, producer=PRODUCER):
    envelope, objects = good_capture()
    original = fixture_request(envelope, objects)
    original = make_capture_delivery_request(envelope, original["raw_pointers"],
        destination_id=destination, producer_id=producer)
    return V.make_request(original)


def test_four_v2_routes_require_only_distinct_outcome_key_and_secure_headers():
    journal = BoundaryJournal(); receiver, storage, quota = receiver_for(journal)
    aid, sid = fingerprint("application"), fingerprint("step")
    routes = [("POST", BASE), ("GET", BASE + "/" + aid),
              ("POST", BASE + "/" + aid + "/steps/" + sid),
              ("GET", BASE + "/" + aid + "/steps/" + sid)]
    with api_client(receiver) as client:
        for method, path in routes:
            for key in (OWNER, FORECAST, "wrong", ""):
                result = client.request(method, path, json={}, headers=headers(key))
                assert result.status_code == 401
                assert result.headers["Cache-Control"] == "no-store"
                assert result.headers["X-Content-Type-Options"] == "nosniff"
        assert client.get(BASE + "/" + aid, headers=headers()).status_code == 404
    assert len(journal.calls) == 1 and storage.calls == [] and quota.calls == []


def test_token_alone_default_disabled_or_invalid_factory_never_opens_backend():
    with api_client(env={**ENV, "VERCEL": "1"}) as client:
        response = client.post(BASE, json={}, headers=headers())
        assert response.status_code == 503 and "acknowledged" in response.text
    @contextmanager
    def invalid():
        yield object()
    with api_client(factory=invalid) as client:
        assert client.post(BASE, json={}, headers=headers()).status_code == 503
    for invalid_key in (None, "", OWNER, FORECAST):
        with api_client(env={**ENV, "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": invalid_key}) as client:
            assert client.post(BASE, json={}, headers=headers()).status_code == 503


def test_parse_limits_content_type_and_injected_normalized_fields_fail_without_backend_reads():
    journal = BoundaryJournal(); receiver, storage, quota = receiver_for(journal)
    path = BASE + "/" + fingerprint("application") + "/steps/" + fingerprint("step")
    with api_client(receiver) as client:
        for target in (BASE, path):
            assert client.post(target, content=b"{}", headers=headers()).status_code == 415
            for data in (b'{broken', b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e400}'):
                response = client.post(target, content=data, headers={**headers(), "Content-Type": "application/json"})
                assert response.status_code == 422
            assert client.post(target, content=b" " * (MAX_LEARNING_JSON + 1),
                headers={**headers(), "Content-Type": "application/json"}).status_code == 413
        for value in ([], None, {"groups": []}, {"observations": []}, {"revision_ids": []}, {"receipt_ids": []}):
            assert client.post(path, content=json.dumps(value),
                headers={**headers(), "Content-Type": "application/json"}).status_code == 422
        value = intent(); value["normalized_labels"] = []
        assert client.post(BASE, json=value, headers=headers()).status_code == 422
        assert client.post(BASE, json={}, headers={**headers(), "Origin": "https://evil.invalid"}).status_code == 403
    assert journal.calls == [] and storage.calls == [] and quota.calls == []


def test_query_allowlist_unique_keys_cursor_types_and_scope_deny_before_writes():
    journal = BoundaryJournal(); receiver, storage, quota = receiver_for(journal)
    path = BASE + "/" + fingerprint("application")
    with api_client(receiver) as client:
        for query in ("url=https://evil.invalid", "view=status&view=groups", "limit=0", "limit=101",
                      "limit=01", "limit=true", "after=a&after=b", "ledger_watermark=1&ledger_watermark=2"):
            assert client.get(path + "?" + query, headers=headers()).status_code == 422
        assert journal.calls == []
        for field, value in (("destination", fingerprint("foreign")), ("producer", "foreign")):
            wrong = intent(**{field: value})
            assert client.post(BASE, json=wrong, headers=headers()).status_code == 404
        assert journal.calls == []
        journal.known = {"request": intent(producer="foreign")}
        assert client.get(path, headers=headers()).status_code == 404
        response = client.post(path + "/steps/" + fingerprint("step"), json={}, headers=headers())
        assert response.status_code == 404 and "request" not in response.text
    assert storage.calls == [] and quota.calls == []


@pytest.mark.parametrize("failure,expected", [(RuntimeError("SECRET_PROVIDER"), 503),
    (StoreError(502), 503), (StoreError(429), 429)])
def test_backend_failures_are_static_no_secret_or_false_durable_success(failure, expected):
    journal = BoundaryJournal(); journal.failure = failure
    receiver, storage, _ = receiver_for(journal)
    with api_client(receiver) as client:
        result = client.get(BASE + "/" + fingerprint("application"), headers=headers())
        assert result.status_code == expected and "SECRET" not in result.text
        assert "durable\":true" not in result.text
    assert storage.calls == []
