"""Real configured v2 factory → ASGI → PostgreSQL missing-application read."""
from fastapi.testclient import TestClient

from services.web.application import create_app
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import SupabaseStore
from test_capture_application_adapter import ApplicationBridge


def test_configured_v2_missing_application_uses_only_its_real_sql_read(monkeypatch):
    bridge = ApplicationBridge()
    key = "fixture-preflight-outcome-" + "o" * 32
    env = {"SELFLEARN_CAPTURE_BACKEND": "supabase", "SELFLEARN_CAPTURE_PROTOCOL": "v2",
           "SUPABASE_URL": "https://offline.supabase.invalid", "SUPABASE_SECRET_KEY": "sb_secret_preflight_fixture",
           "SELFLEARN_CAPTURE_EVIDENCE_BUCKET": "fixture-private-capture",
           "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": key}
    monkeypatch.setattr(SupabaseStore, "request",
                        lambda _store, method, path, **kwargs: bridge.request(method, path, **kwargs))
    try:
        application_id = fingerprint("unregistered-preflight-test")
        with TestClient(create_app(store=object(), env=env)) as client:
            for _ in range(2):
                response = client.get("/api/v2/nfl/capture-applications/" + application_id,
                                      headers={"Authorization": "Bearer " + key})
                assert response.status_code == 404
                assert response.headers["Cache-Control"] == "no-store"
        assert len(bridge.calls) == 2
        assert all(call["path"] == "/rest/v1/rpc/selflearn_journal_read_capture_application_v2"
                   and call["data"]["p_application_id"] == application_id for call in bridge.calls)
    finally:
        bridge.close()
