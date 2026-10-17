"""Empty journal → BOTH real senders → ASGI → actual SQL/quota, local only.

Original synthetic NFL forecast/capture files are copied byte-for-byte from the
real producer fixture. No receipt/object is pre-admitted: every source and
forecast enters through the actual forecast sender/API. PostgreSQL is WASM;
private Storage and the unused generic dashboard store are explicit fakes.
No hosted/network/native concurrency, provider-authentication, fitting, model
adoption or working-learning-loop proof is asserted by these tests.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from test_capture_application_api import ApplicationAPITransport
from test_capture_application_sender_failures import bounded_producer, SmallBatchReceiver, DESTINATION, BASE
from hosted.fakes import FakeStore
from hosted.test_capture_application_api import receiver_for, ENV, OWNER, FORECAST, OUTCOME, CHUNKS


@pytest.fixture(scope="module")
def runner():
    selected = os.environ.get("NFL2026_CHECKOUT")
    if not selected:
        raise RuntimeError("Set NFL2026_CHECKOUT to the reviewed NFL intake runner checkout")
    root = Path(selected).resolve()
    sys.path.insert(0, str(root))
    try:
        module = importlib.import_module("scripts.run_learning_intake")
    finally:
        sys.path.pop(0)
    if Path(module.__file__).resolve() != root / "scripts/run_learning_intake.py":
        raise RuntimeError("The intake runner is not the reviewed real NFL checkout")
    return module


def copied_plan(runner, produced, directory):
    """Isolated exact originals permit a real source-unavailable restart test."""
    source = Path(produced["journal"]["snapshot_dir"])
    raw = Path(produced["sender"]["capture_plan"]["capture_path"])
    authority = directory / "original-authority"
    snapshots = shutil.copytree(source, authority / "forecasts")
    copied_raw = shutil.copytree(raw.parent.parent, authority / "capture") / "captures" / raw.name
    assert copied_raw.read_bytes() == raw.read_bytes()
    for original in source.rglob("*.json"):
        assert (snapshots / original.relative_to(source)).read_bytes() == original.read_bytes()
    value = runner.plan(snapshots, [copied_raw])
    assert value["forecast_plan"]["generations"] == produced["journal"]["generations"]
    assert value["capture_plans"][0]["request"] == produced["sender"]["request"]
    return value, authority


@contextmanager
def empty_pipeline():
    bridge = ApplicationAPITransport()
    try:
        journal = NFLCaptureApplicationJournal("https://offline.supabase.invalid",
            "sb_secret_full_intake_fixture_only", transport=bridge, page_size=100)
        # This MUST precede any forecast/API action. No admit/initial/preseed.
        snapshot = journal.read_observed_snapshot()
        assert snapshot["receipts"] == snapshot["generation_records"] == snapshot["revisions"] == snapshot["observations"] == []
        base, storage, _unused_memory_quota = receiver_for(journal, destination=DESTINATION)
        namespace = staging_namespace(destination_id=DESTINATION, producer_id="nfl2026")
        provisioned = bridge.request("TEST_ADMIN", "/test/provision-capture-staging", data={
            "namespace_id": namespace, "max_total_bytes": 64 * 1024 * 1024,
            "max_chunks": 65536, "enabled": True})
        assert provisioned == {"version": 1, "fixture_provisioned": True}
        receiver = SmallBatchReceiver(journal, base.evidence,
            CaptureStagingQuota(bridge.request, namespace_id=namespace),
            destination_id=DESTINATION, producer_id="nfl2026")

        @contextmanager
        def capture_factory():
            yield receiver

        @contextmanager
        def journal_factory():
            yield journal

        # Explicit test configuration/injection; no environment discovery or
        # hosted capability is selected by the existence of these fake keys.
        with TestClient(create_app(store=FakeStore(), env=ENV,
                journal_factory=journal_factory, capture_factory=capture_factory)) as client:
            yield {"bridge": bridge, "journal": journal, "storage": storage,
                   "namespace": namespace, "client": client}
    finally:
        bridge.close()


class PipelineWire:
    """Actual ASGI response bytes, including intentionally lost/tampered replies."""
    def __init__(self, context):
        self.context, self.calls, self.lost = context, [], []
        self.incomplete_forecast = False
        self.lose_forecast, self.lose_middle = False, False
        self.first_outcome_forecasts = None
        self.final_revision_count_when_reply = None

    def _request(self, kind, method, path, body):
        assert method in ("GET", "POST")
        assert body is None or type(body) is bytes and len(body) <= 3 * 1024 * 1024
        key = FORECAST if kind == "forecast" else OUTCOME
        assert key not in path and (body is None or key.encode() not in body)
        if kind == "outcome" and self.first_outcome_forecasts is None:
            snapshot = self.context["journal"].read_observed_snapshot()
            assert len(snapshot["receipts"]) == 12
            assert len(snapshot["generation_records"]) == 2
            assert snapshot["observations"] == snapshot["revisions"] == []
            self.first_outcome_forecasts = copy.deepcopy(snapshot["receipts"])
        self.calls.append((kind, method, path))
        response = self.context["client"].request(method, path, content=body,
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        assert response.headers["cache-control"] == "no-store"
        status, raw = response.status_code, bytes(response.content)
        if kind == "forecast" and path.endswith("/generations") and status == 200:
            if self.lose_forecast:
                self.lose_forecast = False
                self.lost.append((kind, path, json.loads(body)["object_id"]))
                raise TimeoutError("PRIVATE_LOST_FORECAST_REPLY_AFTER_ACTUAL_SQL")
            if self.incomplete_forecast:
                self.incomplete_forecast = False
                value = response.json()
                assert value["receipts"]
                value["receipts"].pop()  # Negative-only response fault, never a fabricated success.
                raw = json.dumps(value, allow_nan=False).encode()
        if kind == "outcome" and method == "POST" and "/steps/" in path and status == 200:
            result = response.json()["result"]
            if result["ordinal"] == 1 and self.lose_middle:
                self.lose_middle = False
                self.lost.append((kind, path, result["batch_result_id"]))
                raise TimeoutError("PRIVATE_LOST_MIDDLE_REPLY_AFTER_ACTUAL_SQL")
            if result["ordinal"] == 2:
                self.final_revision_count_when_reply = len(self.context["journal"].read_observed_snapshot()["revisions"])
        return status, raw

    def forecast(self, path, body):
        assert path in ("/api/v1/nfl/objects", "/api/v1/nfl/generations")
        return self._request("forecast", "POST", path, body)

    def outcome(self, method, path, body):
        assert path == CHUNKS or path == BASE or path.startswith(BASE + "/sha256%3A")
        return self._request("outcome", method, path, body)


def execute(runner, plan, directory, wire, **kwargs):
    return runner.send(plan, directory, FORECAST, OUTCOME,
        forecast_transport=wire.forecast, outcome_transport=wire.outcome,
        attempts=2, sleep=lambda _: None, **kwargs)


def assert_truth(result):
    for field in ("evaluation_run", "model_adoption", "working_learning_loop",
                  "current_membership_reverified", "current_outcome_coverage_verified",
                  "namespace_authenticated", "source_completeness_verified"):
        assert result[field] is False


def outcome_writes(bridge):
    suffixes = ("register_capture_application_v2", "commit_capture_application_batch_v2",
                "stop_capture_application_v2", "reserve_capture_stage_v1")
    return [row for row in bridge.calls if row["path"].endswith(suffixes)]


def test_empty_SQL_both_auth_scopes_real_forecasts_then_twelve_raw_resolutions_before_completion(
        bounded_producer, runner, tmp_path):
    produced, _ = bounded_producer
    value, _ = copied_plan(runner, produced, tmp_path)
    with empty_pipeline() as context:
        client = context["client"]
        source = value["forecast_plan"]["source_objects"][0]
        for wrong in (OWNER, OUTCOME):
            denied = client.post("/api/v1/nfl/objects", json=source,
                headers={"Authorization": "Bearer " + wrong})
            assert denied.status_code == 401
        denied = client.post(CHUNKS, json=make_chunk(b"must-not-stage"),
            headers={"Authorization": "Bearer " + FORECAST})
        assert denied.status_code == 401 and context["storage"].calls == []
        assert context["journal"].receipts() == []
        wire = PipelineWire(context)
        result = execute(runner, value, tmp_path / "separate-outbox", wire, max_steps=3)
        assert result["intake_complete"] is True and result["status"] == "intake_complete", result
        assert result["forecast_complete"] is True
        assert wire.final_revision_count_when_reply == 12
        durable = context["journal"].read_observed_snapshot()
        assert durable["receipts"] == wire.first_outcome_forecasts
        assert len(durable["receipts"]) == len(durable["observations"]) == len(durable["revisions"]) == 12
        assert all(row["resolution"]["hit"] is True for row in durable["revisions"])
        original = result["capture_results"][0]["result"]
        assert original["counts"]["accepted_members"] == 12 and original["counts"]["committed_batches"] == 3
        assert [c[0] for c in wire.calls].index("outcome") > max(i for i, c in enumerate(wire.calls) if c[0] == "forecast")
        quota = context["bridge"].request("TEST_ADMIN", "/test/capture-staging-summary",
            data={"namespace_id": context["namespace"]})
        assert quota["reserved_chunks"] == len(context["storage"].objects) == 3
        assert int(quota["reserved_total_bytes"]) == sum(map(len, context["storage"].objects.values()))
        assert_truth(result)


def test_incomplete_actual_forecast_ACK_holds_all_outcome_SQL_and_storage_then_recovers_original(
        bounded_producer, runner, tmp_path):
    produced, _ = bounded_producer
    value, _ = copied_plan(runner, produced, tmp_path)
    with empty_pipeline() as context:
        wire = PipelineWire(context); wire.incomplete_forecast = True
        output = tmp_path / "separate-outbox"
        refused = execute(runner, value, output, wire, max_steps=3)
        assert refused["status"] == "pending_forecasts" and refused["forecast_complete"] is False
        assert context["storage"].calls == [] and outcome_writes(context["bridge"]) == []
        assert not any(row[0] == "outcome" for row in wire.calls)
        snapshot = context["journal"].read_observed_snapshot()
        assert len(snapshot["receipts"]) == 12 and snapshot["observations"] == snapshot["revisions"] == []
        recovered = runner.resume(output, value["run_id"], FORECAST, OUTCOME,
            forecast_transport=wire.forecast, outcome_transport=wire.outcome,
            attempts=2, max_steps=3, sleep=lambda _: None)
        assert recovered["intake_complete"] is True, recovered
        durable = context["journal"].read_observed_snapshot()
        assert durable["receipts"] == snapshot["receipts"] and len(durable["revisions"]) == 12
        assert_truth(refused); assert_truth(recovered)


def test_lost_real_forecast_and_middle_batch_replies_resume_without_duplicates_or_available_client_sources(
        bounded_producer, runner, tmp_path):
    produced, _ = bounded_producer
    value, authority = copied_plan(runner, produced, tmp_path)
    with empty_pipeline() as context:
        wire = PipelineWire(context); wire.lose_forecast = True
        output = tmp_path / "separate-outbox"
        first = execute(runner, value, output, wire, max_steps=1)
        assert first["status"] == "pending_outcomes" and first["intake_complete"] is False
        assert len(context["journal"].read_observed_snapshot()["revisions"]) == 4
        wire.lose_middle = True
        complete = runner.resume(output, value["run_id"], FORECAST, OUTCOME,
            forecast_transport=wire.forecast, outcome_transport=wire.outcome,
            attempts=2, max_steps=2, sleep=lambda _: None)
        assert complete["intake_complete"] is True, complete
        assert [row[0] for row in wire.lost] == ["forecast", "outcome"]
        steps = [path for kind, method, path in wire.calls if method == "POST" and "/steps/" in path]
        assert len(steps) == len(set(steps)) == 3
        durable = copy.deepcopy(context["journal"].read_observed_snapshot())
        assert durable["receipts"] == wire.first_outcome_forecasts
        assert len(durable["receipts"]) == len(durable["observations"]) == len(durable["revisions"]) == 12
        # Physically unavailable client authorities; immutable server originals
        # still recover without raw revalidation, restaging or forecast replay.
        authority.rename(tmp_path / "unavailable-original-authority")
        before, storage_before = len(wire.calls), len(context["storage"].calls)
        writes_before = len(outcome_writes(context["bridge"]))
        context["storage"].bucket["public"] = True
        with patch.object(runner.forecast, "plan", side_effect=AssertionError("No original snapshot reload")), \
             patch.object(runner.outcome.O, "_validated", side_effect=AssertionError("No client raw reload")):
            original = runner.resume(output, value["run_id"], FORECAST, OUTCOME,
                forecast_transport=wire.forecast, outcome_transport=wire.outcome,
                attempts=2, max_steps=1, sleep=lambda _: None)
        assert original["intake_complete"] is True and original["steps_issued"] == 0
        assert original["capture_results"] == complete["capture_results"]
        assert all(kind == "outcome" and method == "GET" for kind, method, path in wire.calls[before:])
        assert len(context["storage"].calls) == storage_before
        assert len(outcome_writes(context["bridge"])) == writes_before
        assert context["journal"].read_observed_snapshot() == durable
        files = "".join(path.read_text() for path in output.rglob("*.json"))
        assert FORECAST not in files and OUTCOME not in files and "PRIVATE_LOST" not in files
        assert_truth(first); assert_truth(complete); assert_truth(original)
