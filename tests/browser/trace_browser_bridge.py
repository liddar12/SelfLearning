"""Disposable non-network ASGI/SQL forecast-trace browser fixture.

Original NFL forecasts/raw outcomes and PostgreSQL-WASM are real code paths.
Generic dashboard records and private chunk Storage are explicit memory fakes.
No production credentials, cloud services, source data writes or model fitting.
"""
from contextlib import contextmanager
import base64
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "packages/selflearn-core"),
               str(ROOT / "tests"), str(ROOT / "tests/postgres")]

from fastapi.testclient import TestClient
from services.web.application import create_app
from test_adapter_integration import PostgresBridge
from test_capture_outcomes_atomic import raw_producer, port, admit, deliver
from hosted.fakes import FakeStore

OWNER = "local-browser-fixture-owner-" + "a" * 32


def main():
    # pytest's already-reviewed fixture invokes the actual local NFL producer
    # in a disposable directory, returning original forecasts and raw bytes.
    fixture = raw_producer.__wrapped__()
    # The existing bridge selects its additive observed-journal migration only
    # for this explicit test mode. Never infer cloud migration readiness.
    os.environ["SELFLEARN_OBSERVED_JOURNAL_TEST"] = "1"
    bridge = PostgresBridge()
    try:
        journal = port(bridge)
        plan = fixture["initial"]
        admit(journal, plan["source_objects"], [r["request"]["payload"] for r in plan["generations"]])
        for index in (0, 2, 4):
            deliver(journal, fixture["captures"][index])
        original = journal.read_observed_snapshot()
        assert len(original["receipts"]) == 4 and len(original["revisions"]) == 12
        @contextmanager
        def journal_factory():
            yield journal
        app = create_app(store=FakeStore(), env={"SELFLEARN_OPERATOR_TOKEN": OWNER},
                         journal_factory=journal_factory)
        # No listener or external request: Playwright's route handler forwards
        # each request through this fixed stdin/stdout protocol to TestClient.
        with TestClient(app) as client:
            print(json.dumps({"fixture_ready": True, "url": "http://selflearn-browser.invalid",
                "receipts": len(original["receipts"]), "revisions": len(original["revisions"]),
                "scope": "actual_NFL_trace_PostgreSQL_WASM_fake_generic_store_and_chunk_Storage"}), flush=True)
            while True:
                line = sys.stdin.readline(4 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line.encode()) > 4 * 1024 * 1024:
                    raise ValueError("Oversized local browser bridge request")
                request = json.loads(line)
                assert set(request) == {"id", "method", "path", "headers"}
                assert request["method"] == "GET"
                path = request["path"]
                assert path in ("/", "/style.css", "/app.js", "/nfl-trace.js", "/favicon.ico",
                    "/api/status", "/api/predictions", "/api/uploads") or path.startswith((
                        "/api/v1/nfl/forecasts?", "/api/v1/nfl/forecast-trace/"))
                response = client.get(path, headers=request["headers"])
                print(json.dumps({"id": request["id"], "status": response.status_code,
                    "headers": dict(response.headers), "body": base64.b64encode(response.content).decode()}), flush=True)
        assert journal.read_observed_snapshot() == original, "Browser reads must not mutate forecast or evidence streams"
        print(json.dumps({"fixture_closed": True, "read_only_unchanged": True}), flush=True)
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
