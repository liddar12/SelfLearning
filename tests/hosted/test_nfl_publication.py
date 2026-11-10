"""Actual scoped API -> verifier -> SQLite, with explicit offline HTTP bytes."""
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from services.web.nfl_publication_verifier import (
    MAX_BODY_BYTES, PublicPoolVerifier, PublicationUnavailable, select_verifier)
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal as ReceiptJournal
from .fakes import FakeStore, TEST_KEY
from .publication_fixture import graph

ROUTE = "/api/v1/nfl/publication-attestations"
INGEST, OUTCOME, CORRECTIONS, PUBLICATION = ("publication-test-" + c * 40 for c in "iocp")
ORIGIN = "https://publication-fixture.example.com"
ENV = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": INGEST,
       "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": OUTCOME,
       "SELFLEARN_NFL_CORRECTIONS_TOKEN": CORRECTIONS,
       "SELFLEARN_NFL_PUBLICATION_TOKEN": PUBLICATION,
       "SELFLEARN_PUBLICATION_MODE": "record", "SELFLEARN_NFL_PUBLICATION_ORIGIN": ORIGIN}


def post(client, route, body, token=PUBLICATION):
    return client.post(route, json=body, headers={"Authorization": "Bearer " + token})


def client_for(path, transport, env=None):
    return TestClient(create_app(store=FakeStore(), env=ENV if env is None else env,
        journal_factory=lambda: ReceiptJournal(path), publication_transport=transport))


def upload(client, doc, sources):
    for payload in sources.values():
        result = post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "source_snapshot",
            "object_id": fingerprint(payload), "payload": payload}, INGEST)
        assert result.status_code == 200, result.text
    assert post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "generation",
        "object_id": doc["envelope_id"], "payload": doc}, INGEST).status_code == 200
    result = post(client, "/api/v1/nfl/generations", {"version": 1,
        "object_id": doc["envelope_id"]}, INGEST)
    assert result.status_code == 200, result.text
    assert len(result.json()["receipts"]) == 1 and result.json()["rejections"] == []


def transport_for(pool, calls):
    raw = json.dumps(pool, indent=2).encode("utf-8")
    def fetch(url):
        calls.append(url)
        return 200, {"Content-Type": "application/json; charset=utf-8"}, raw, url
    return fetch, raw


def test_actual_publication_persists_recovers_original_and_preserves_forecasts(tmp_path):
    path = tmp_path / "journal.sqlite"
    doc, sources = graph()
    calls = []
    fetch, raw = transport_for(sources["leg_pool"], calls)
    with client_for(path, fetch) as client:
        upload(client, doc, sources)
        with ReceiptJournal(path) as journal:
            before = journal.receipts(), journal.generations()
        result = post(client, ROUTE, {"version": 1, "object_id": doc["envelope_id"]})
        assert result.status_code == 200, result.text
        original = result.json()
        assert original["durable"] is True and original["status"] == "accepted"
        assert len(original["receipts"]) == 1 and original["rejections"] == []
        assert original["observation"]["body_sha256"] == "sha256:" + hashlib.sha256(raw).hexdigest()
        assert "body_utf8" not in result.text
        assert result.headers["cache-control"] == "no-store"
        rid = original["receipts"][0]["receipt_id"]
        trace = client.get("/api/v1/nfl/forecast-trace/" + rid,
                           headers={"Authorization": "Bearer " + TEST_KEY})
        assert trace.status_code == 200, trace.text
        detail = trace.json()
        assert detail["public_pool_availability"] == "attested"
        assert detail["publication_evidence"][0]["attestation_id"] == original["attestation_id"]
        assert detail["forecast"]["public_availability"] == "unverified", "pool availability is not display evidence"
        assert detail["forecast"]["model_adoption"] == "unverified"
    assert calls == [ORIGIN + "/data/leg_pool.json"]

    def must_not_fetch(url):
        raise AssertionError("retry must recover the original proof without HTTP")

    # New process/connection, including recovery after a caller lost the ACK.
    with client_for(path, must_not_fetch) as client:
        retry = post(client, ROUTE, {"version": 1, "object_id": doc["envelope_id"]})
        assert retry.status_code == 200, retry.text
        assert retry.json() == original
    with ReceiptJournal(path) as journal:
        assert (journal.receipts(), journal.generations()) == before
        assert journal.receipts()[0]["publication_status"] == "generated_unverified"
        assert journal.resolution_history(journal.receipts()[0]["receipt_id"]) == []
        assert journal.get_publication_attestation(doc["envelope_id"]) == original


def test_scope_configuration_and_client_claims_fail_before_http(tmp_path):
    calls = []
    fetch, _ = transport_for({}, calls)
    request = {"version": 1, "object_id": fingerprint("unfinalized")}
    with client_for(tmp_path / "scope.sqlite", fetch) as client:
        for token in (TEST_KEY, INGEST, OUTCOME, CORRECTIONS, ""):
            assert post(client, ROUTE, request, token).status_code == 401
        assert post(client, "/api/v1/nfl/objects", request).status_code == 401
        for extra in ({"url": ORIGIN}, {"observed_completed_utc": "2026-01-01T00:00Z"},
                      {"body_utf8": "{}"}, {"receipt_ids": []}):
            assert post(client, ROUTE, {**request, **extra}).status_code == 422
        assert post(client, ROUTE, request).status_code == 422
    assert calls == []
    for changes in ({"SELFLEARN_PUBLICATION_MODE": "off"}, {"SELFLEARN_NFL_PUBLICATION_ORIGIN": ""},
                    {"SELFLEARN_NFL_PUBLICATION_TOKEN": INGEST}):
        with client_for(tmp_path / "disabled.sqlite", fetch, {**ENV, **changes}) as client:
            assert post(client, ROUTE, request).status_code == 503
    assert calls == []


@pytest.mark.parametrize("raw", [b'{"players":[]}', b'{"a":1,"a":2}', b'{"x":NaN}', b'[]'])
def test_wrong_or_malformed_public_pool_cannot_produce_ack(tmp_path, raw):
    doc, sources = graph()
    with client_for(tmp_path / "bad.sqlite", lambda url: (200, {"content-type": "application/json"}, raw, url)) as client:
        upload(client, doc, sources)
        result = post(client, ROUTE, {"version": 1, "object_id": doc["envelope_id"]})
        assert result.status_code == 422, result.text
    with ReceiptJournal(tmp_path / "bad.sqlite") as journal:
        assert journal.get_publication_attestation(doc["envelope_id"]) is None


@pytest.mark.parametrize("origin", [None, "", "http://example.com", "https://u:p@example.com",
    "https://example.com/", "https://example.com:443", "https://example.com?q=1", "https://127.0.0.1",
    "https://localhost", "https://[::1]", "https://example.com#fragment"])
def test_origin_must_be_explicit_and_canonical(origin):
    assert select_verifier({"SELFLEARN_PUBLICATION_MODE": "record", "SELFLEARN_NFL_PUBLICATION_ORIGIN": origin}) is None


@pytest.mark.parametrize("status,headers,raw,redirect", [
    (302, {"content-type": "application/json"}, b'{}', False),
    (200, {"content-type": "application/json"}, b'{}', True),
    (200, {"content-type": "text/html"}, b'{}', False),
    (200, {"content-type": "application/json", "content-encoding": "gzip"}, b'{}', False),
    (200, {"content-type": "application/json"}, b'\xff', False),
    (200, {"content-type": "application/json"}, b'x' * (MAX_BODY_BYTES + 1), False),
])
def test_verifier_rejects_redirects_encoding_sizes_and_non_json(status, headers, raw, redirect):
    verifier = PublicPoolVerifier(ORIGIN, transport=lambda url: (status, headers, raw,
                                  "https://other.example.com/data/leg_pool.json" if redirect else url))
    with pytest.raises(PublicationUnavailable):
        verifier.observe()


def test_observation_clocks_belong_to_server_and_budget_is_enforced():
    clocks = iter(["2026-11-09T01:00:00Z", "2026-11-09T01:00:01Z"])
    verifier = PublicPoolVerifier(ORIGIN, clock=lambda: next(clocks),
        transport=lambda url: (200, {"content-type": "application/json"}, b'{}', url))
    result = verifier.observe()
    assert result["observed_started_utc"].endswith("00:00Z")
    assert result["observed_completed_utc"].endswith("00:01Z")
    ticks = iter([0, 21])
    verifier = PublicPoolVerifier(ORIGIN, monotonic=lambda: next(ticks),
        transport=lambda url: (200, {"content-type": "application/json"}, b'{}', url))
    with pytest.raises(PublicationUnavailable, match="timeout"):
        verifier.observe()
