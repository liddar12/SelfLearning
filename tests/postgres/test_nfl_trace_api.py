"""Actual raw-capture journal -> owner trace API, using PostgreSQL-WASM.

Private chunks remain fake; this does not prove native/hosted storage, browser
layout or live source provenance. No source data, cloud or migrations changed.
"""
from contextlib import contextmanager

from fastapi.testclient import TestClient

from services.web.application import create_app
from test_capture_outcomes_atomic import journals, raw_producer, deliver


def test_real_pinned_trace_http_preserves_forecasts_and_exposes_corrections(journals, raw_producer):
    journal, _, _ = journals
    initial, _, correction, _, void = raw_producer["captures"]
    first, _ = deliver(journal, initial)
    receipt_id = first["staged"]["groups"][0]["receipt_ids"][0]
    original_receipts = journal.receipts()
    @contextmanager
    def factory():
        yield journal
    token = "fixture-owner-trace-" + "a" * 32
    app = create_app(store=object(), env={"SELFLEARN_OPERATOR_TOKEN": token}, journal_factory=factory)
    with TestClient(app) as api:
        api.headers["Authorization"] = "Bearer " + token
        listing = api.get("/api/v1/nfl/forecasts")
        assert listing.status_code == 200
        assert listing.json()["total_forecasts"] == 4
        route = "/api/v1/nfl/forecast-trace/" + receipt_id
        original = api.get(route)
        assert original.status_code == 200, original.text
        before = original.json()
        assert before["forecast"]["outcome_state"] == "graded"
        assert before["forecast"]["outcome_hit"] is True
        assert len(before["outcome_revisions"]) == 1
        assert before["observed_proof_history_available"] is True
        assert before["source_freshness"] == "unverified"
        assert before["production_connection_verified"] is False
        assert before["generations"] and before["generations"][0]["public_availability"] == "unverified"
        deliver(journal, correction)
        corrected = api.get(route).json()
        assert corrected["forecast"]["outcome_hit"] is False
        assert len(corrected["outcome_revisions"]) == 2
        assert corrected["outcome_revisions"][0] == before["outcome_revisions"][0]
        assert int(corrected["watermark"]) > int(before["watermark"])
        deliver(journal, void)
        voided = api.get(route).json()
        assert voided["forecast"]["outcome_state"] == "void"
        assert voided["forecast"]["outcome_hit"] is None
        assert voided["outcome_revisions"][-1]["actual"] is None
        assert api.get(route).headers["cache-control"] == "no-store"
    assert journal.receipts() == original_receipts

