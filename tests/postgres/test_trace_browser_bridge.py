"""Actual stdio → ASGI → PostgreSQL-WASM trace, with NO browser/network.

This verifies the browser fixture's explicit EOF/read-only completion protocol.
It does not establish browser layout/interaction, native concurrency, hosted
Storage, production connectivity or source authenticity. Missing explicit local
fixture dependencies fail instead of silently skipping acceptance.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[2]
BRIDGE = ROOT / "tests/browser/trace_browser_bridge.py"
OWNER = "local-browser-fixture-owner-" + "a" * 32
MAX_FRAME = 8 * 1024 * 1024


class Bridge:
    """One explicitly owned local process group; no sockets or browser launch."""
    def __init__(self):
        required = ("SELFLEARN_PGLITE_MODULE", "NFL2026_CHECKOUT")
        if any(not os.environ.get(key) for key in required):
            raise RuntimeError("Explicit local PGlite and NFL fixture paths are required")
        env = {key: os.environ[key] for key in required}
        env.update(PATH=os.environ.get("PATH", "/usr/bin:/bin"), PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
        self.process = subprocess.Popen([sys.executable, str(BRIDGE)], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True)
        self.buffer, self.request_id = bytearray(), 0
        os.set_blocking(self.process.stdout.fileno(), False)

    def read(self, timeout=15):
        deadline = time.monotonic() + timeout
        while True:
            boundary = self.buffer.find(b"\n")
            if boundary >= 0:
                line = bytes(self.buffer[:boundary])
                del self.buffer[:boundary + 1]
                result = json.loads(line)
                assert type(result) is dict, "Invalid local bridge response"
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.process.stdout], [], [], remaining)[0]:
                raise RuntimeError("Local trace bridge response deadline exceeded")
            chunk = os.read(self.process.stdout.fileno(), min(65536, MAX_FRAME - len(self.buffer) + 1))
            if not chunk:
                raise RuntimeError("Local trace bridge ended before its explicit completion response")
            self.buffer.extend(chunk)
            if len(self.buffer) > MAX_FRAME:
                raise RuntimeError("Local trace bridge response exceeds its byte budget")

    def get(self, path, *, authorized=True):
        self.request_id += 1
        request = {"id": self.request_id, "method": "GET", "path": path,
                   "headers": {"Authorization": "Bearer " + OWNER} if authorized else {}}
        encoded = json.dumps(request, allow_nan=False).encode() + b"\n"
        assert len(encoded) < 4096
        self.process.stdin.write(encoded)
        self.process.stdin.flush()
        reply = self.read()
        assert set(reply) == {"id", "status", "headers", "body"}
        assert type(reply["id"]) is int and reply["id"] == self.request_id
        assert type(reply["status"]) is int and type(reply["headers"]) is dict
        raw = base64.b64decode(reply["body"], validate=True)
        assert OWNER.encode() not in raw, "The local fixture key must not appear in response content"
        assert reply["headers"]["cache-control"] == "no-store"
        return reply["status"], json.loads(raw)

    def finish(self):
        self.process.stdin.close()
        result = self.read()
        assert result == {"fixture_closed": True, "read_only_unchanged": True}
        assert result["fixture_closed"] is True and result["read_only_unchanged"] is True
        assert self.process.wait(timeout=15) == 0, "Local trace fixture did not exit successfully"
        assert not self.buffer, "Unexpected data after the fixture completion response"

    def close(self):
        if not self.process.stdin.closed:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            # Only this freshly created process group and its fixture children
            # are eligible for cleanup. No external/native server is touched.
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        self.process.stdout.close()


def test_browser_fixture_stdio_reads_finish_with_actual_sql_read_only_ack():
    bridge = Bridge()
    try:
        ready = bridge.read(timeout=30)
        assert ready == {"fixture_ready": True, "url": "http://selflearn-browser.invalid",
            "receipts": 4, "revisions": 12,
            "scope": "actual_NFL_trace_PostgreSQL_WASM_fake_generic_store_and_chunk_Storage"}
        assert ready["fixture_ready"] is True
        assert bridge.get("/api/status", authorized=False)[0] == 401
        status_code, status = bridge.get("/api/status")
        assert status_code == 200 and status["capabilities"]["automatic_promotion"] is False
        assert status["capabilities"]["nfl2026_connected"] is False
        status_code, listing = bridge.get("/api/v1/nfl/forecasts?limit=4")
        assert status_code == 200 and listing["total_forecasts"] == listing["returned"] == 4
        assert listing["read_only"] is True and listing["production_connection_verified"] is False
        ids = [row["receipt_id"] for row in listing["records"]]
        assert len(set(ids)) == 4
        for receipt_id in ids:
            status_code, detail = bridge.get("/api/v1/nfl/forecast-trace/" + receipt_id)
            assert status_code == 200 and detail["kind"] == "nfl.forecast_trace.v1"
            assert detail["forecast"]["receipt_id"] == receipt_id
            assert detail["forecast"]["outcome_state"] == "void" and detail["forecast"]["outcome_hit"] is None
            assert [row["status"] for row in detail["outcome_revisions"]] == ["FINAL", "FINAL", "VOID"]
            assert [row["hit"] for row in detail["outcome_revisions"]] == [True, False, None]
            assert detail["outcome_revisions"][-1]["actual"] is None
            assert detail["read_only"] is True and detail["observed_proof_history_available"] is True
            assert detail["production_connection_verified"] is False
            assert detail["source_freshness"] == detail["forecast"]["model_adoption"] == "unverified"
        # Success requires EOF to run the source fixture's FINAL snapshot
        # equality assertion and then its explicit ACK; killing it is not proof.
        bridge.finish()
    finally:
        bridge.close()
