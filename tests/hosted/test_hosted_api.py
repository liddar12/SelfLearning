import base64
import time
import pytest
from fastapi.testclient import TestClient
from services.web.application import create_app
from selflearn_core.store.supabase_store import StoreError
from .fakes import FakeStore, TEST_KEY

PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=')


@pytest.fixture
def client():
    app = create_app(store=FakeStore(), env={"SELFLEARN_OPERATOR_TOKEN": TEST_KEY})
    with TestClient(app) as client:
        client.headers["Authorization"] = "Bearer " + TEST_KEY
        yield client


def prediction(**overrides):
    return {"id": "test-1", "ts": int(time.time()) - 7200, "model_version": "test-v1",
            "task": "test.binary", "features": {"weather": "unknown"}, "prediction": {"probability": .6},
            "horizon_s": 3600, **overrides}


def test_public_shell_and_private_boundary(client):
    assert client.get("/").status_code == 200
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200
    assert client.get("/api/health").json()["mode"] == "L0"
    for path in ("/.env.local", "/.git/config", "/app.py", "/docs", "/openapi.json"):
        assert client.get(path).status_code == 404
    for path in ("/api/status", "/api/predictions", "/api/uploads", "/mcp", "/mcp/"):
        response = client.get(path, headers={"Authorization": "Bearer wrong"})
        assert response.status_code == 401
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/status", headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.get("/api/status", headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/status").headers["x-content-type-options"] == "nosniff"


def test_empty_store_and_honest_capabilities(client):
    data = client.get("/api/status").json()
    assert data["store"]["predictions"] == 0
    assert data["capabilities"]["automatic_promotion"] is False
    assert data["capabilities"]["nfl2026_connected"] is False
    assert client.get("/api/predictions?limit=101").status_code == 422


def test_prediction_replay_conflict_and_server_provenance(client):
    data = prediction()
    assert client.post("/api/predictions", json=data).status_code == 200
    assert client.post("/api/predictions", json=data).status_code == 200
    assert client.post("/api/predictions", json={**data, "prediction": 999}).status_code == 409
    rows = client.get("/api/predictions").json()["records"]
    assert len(rows) == 1
    assert rows[0]["meta"]["_selflearn"]["provenance"] == "unverified_import"


@pytest.mark.parametrize("override", [
    {"ts": True}, {"ts": int(time.time()) + 86400}, {"horizon_s": 0},
    {"horizon_s": True}, {"horizon_s": 99999999999}, {"id": ""}, {"id": "a" * 201},
    {"confidence": True}, {"confidence": 1.1}, {"meta": []}, {"cohort": []},
    {"received_at": "2026-01-01"}, {"meta": {"_selflearn": {"provenance": "verified"}}},
    {"task": "nfl.game"}, {"features": {"a": "\x00"}},
])
def test_prediction_validation(client, override):
    assert client.post("/api/predictions", json=prediction(**override)).status_code == 422


def test_nonfinite_malformed_oversized_and_deep_json(client):
    headers = {"Content-Type": "application/json"}
    assert client.post("/api/predictions", content='{"value": NaN}', headers=headers).status_code == 422
    assert client.post("/api/predictions", content='{broken', headers=headers).status_code == 422
    assert client.post("/api/predictions", content='{}').status_code == 415
    assert client.post("/api/predictions", content='x' * 262145, headers=headers).status_code == 413
    assert client.post("/api/predictions", content='[' * 40 + '1' + ']' * 40, headers=headers).status_code == 422


def test_outcome_order_and_immutability(client):
    data = prediction()
    outcome = {"prediction_id": data["id"], "resolved_ts": data["ts"] + 3600, "realized": {"hit": True}}
    assert client.post("/api/outcomes", json=outcome).status_code == 404
    client.post("/api/predictions", json=data)
    assert client.post("/api/outcomes", json={**outcome, "resolved_ts": data["ts"]}).status_code == 422
    assert client.post("/api/outcomes", json={**outcome, "resolved_ts": int(time.time()) + 999}).status_code == 422
    assert client.post("/api/outcomes", json=outcome).status_code == 200
    assert client.post("/api/outcomes", json=outcome).status_code == 200
    assert client.post("/api/outcomes", json={**outcome, "realized": {"hit": False}}).status_code == 409


def test_private_upload_roundtrip_and_replay(client):
    response = client.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
    assert response.status_code == 200
    assert response.json()["analysis_performed"] is False
    asset = response.json()["id"]
    assert client.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).json()["id"] == asset
    assert len(client.get("/api/uploads").json()["assets"]) == 1
    download = client.get("/api/uploads/" + asset)
    assert download.content == PNG
    assert download.headers["content-type"] == "application/octet-stream"
    assert download.headers["content-disposition"].startswith("attachment;")
    assert client.get("/api/uploads/" + asset, headers={"Authorization": ""}).status_code == 401
    assert client.get("/api/uploads/bad.png").status_code == 404


@pytest.mark.parametrize("mime,content,status", [
    ("image/png", b"<script>alert(1)</script>", 415), ("image/heic", b"abcd", 415),
    ("text/html", b"<html>bad</html>", 415), ("image/png", b"x" * 3145729, 413),
])
def test_reject_unsafe_uploads(client, mime, content, status):
    assert client.post("/api/uploads", content=content, headers={"Content-Type": mime}).status_code == status


def test_missing_configuration_fails_closed():
    with TestClient(create_app(env={})) as client:
        assert client.get("/api/status").status_code == 503
        assert client.get("/api/health").status_code == 200
    with TestClient(create_app(env={"SELFLEARN_OPERATOR_TOKEN": TEST_KEY})) as client:
        response = client.get("/api/status", headers={"Authorization": "Bearer " + TEST_KEY})
        assert response.status_code == 503


def test_upstream_errors_are_not_reported_as_success_or_leaked():
    class BrokenStore(FakeStore):
        def status(self):
            raise StoreError(401)
    with TestClient(create_app(store=BrokenStore(), env={"SELFLEARN_OPERATOR_TOKEN": TEST_KEY})) as client:
        response = client.get("/api/status", headers={"Authorization": "Bearer " + TEST_KEY})
        assert response.status_code == 503
        assert TEST_KEY not in response.text


def test_real_mcp_protocol_and_read_only_tool_surface(client):
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
    initialized = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"}}})
    assert initialized.status_code == 200
    assert "result" in initialized.json()
    listing = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listing.status_code == 200
    tools = listing.json()["result"]["tools"]
    assert {t["name"] for t in tools} == {"get_status", "list_tasks", "list_predictions", "get_nfl_forecast_trace"}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)
    called = client.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "get_status", "arguments": {}}})
    assert called.status_code == 200
    assert not called.json()["result"].get("isError")
    bad_host = client.post("/mcp", headers={**headers, "Host": "attacker.example"}, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert bad_host.status_code == 421
