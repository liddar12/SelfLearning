"""Actual NFL sender -> in-process HTTP -> actual PostgreSQL-WASM quotas/results.

Private Supabase Storage is an explicit in-memory fake. No listening server,
network/cloud, hosted credentials, native concurrency or production acceptance
is established. Missing reviewed NFL/PGlite dependencies FAIL, never skip.
"""
import base64
import copy
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

from selflearn_core.evidence_chunks import make_chunk
from selflearn_core.probability_receipts import fingerprint
from test_capture_outcomes_atomic import raw_producer, admit
from test_capture_intake_api import (
    intake_context, CAPTURE_KEY, GENERATION_KEY, ENV, DEST, CHUNKS, DELIVERIES,
)


@pytest.fixture(scope="module")
def nfl_sender():
    selected = os.environ.get("NFL2026_CHECKOUT")
    if not selected:
        raise RuntimeError("Set NFL2026_CHECKOUT to the reviewed NFL sender checkout")
    checkout = Path(selected).resolve()
    source = checkout / "scripts/export_outcome_capture.py"
    if not source.is_file():
        raise RuntimeError("The reviewed actual NFL outcome sender is required")
    sys.path.insert(0, str(checkout))
    try:
        sender = importlib.import_module("scripts.export_outcome_capture")
    finally:
        sys.path.pop(0)
    if Path(sender.__file__).resolve() != source:
        raise RuntimeError("The imported sender is not the configured NFL checkout")
    assert sender.ORIGIN == "https://self-learning-beige.vercel.app"
    assert sender.PRODUCER == "nfl2026"
    assert fingerprint({"base_url": sender.ORIGIN}) == DEST
    return sender


def saved_plan(sender, captured, directory):
    """Persist ORIGINAL raw observations through the real producer, not labels."""
    bundle = {"manifest": copy.deepcopy(captured["manifest"]),
              "observations": copy.deepcopy(captured["observations"]),
              "raw_objects": {key: base64.b64decode(value, validate=True)
                              for key, value in captured["raw"].items()}}
    expected = copy.deepcopy(bundle)
    path = sender.capture.save_capture(bundle, directory)
    assert bundle == expected
    assert sender.capture.load_capture(path) == expected
    plan = sender.plan(path, sender.ORIGIN)
    assert plan["request"]["envelope"]["manifest"] == expected["manifest"]
    assert plan["request"]["envelope"]["observations"] == expected["observations"]
    assert plan["request"]["producer_id"] == "nfl2026"
    assert plan["request"]["destination_id"] == DEST
    return plan


class InProcessSenderTransport:
    """Real sender request bytes cross ASGI, not a fake receiver/ACK function."""
    def __init__(self, context, *, lose_delivery_reply=False, forge_stage_ack=False):
        self.client = context["client"]
        self.calls = []
        self.responses = []
        self.lose_delivery_reply = lose_delivery_reply
        self.forge_stage_ack = forge_stage_ack
        self.lost = False

    def __call__(self, method, route, body):
        assert method in ("GET", "POST")
        assert route in (CHUNKS, DELIVERIES) or route.startswith(DELIVERIES + "/sha256%3A")
        assert body is None or type(body) is bytes and len(body) <= 3 * 1024 * 1024
        assert CAPTURE_KEY not in route
        assert body is None or CAPTURE_KEY.encode() not in body
        self.calls.append((method, route, body))
        response = self.client.request(method, route, content=body, headers={
            "Authorization": "Bearer " + CAPTURE_KEY, "Content-Type": "application/json"})
        self.responses.append((response.status_code, bytes(response.content)))
        assert response.headers["cache-control"] == "no-store"
        if self.lose_delivery_reply and not self.lost and method == "POST" and route == DELIVERIES:
            assert response.status_code == 200, response.text
            self.lost = True
            raise TimeoutError("SYNTHETIC_PRIVATE_LOST_REPLY_AFTER_ACTUAL_COMMIT")
        raw = response.content
        if self.forge_stage_ack and method == "POST" and route == CHUNKS and response.status_code == 200:
            # Actual quota reservation and fake private write already occurred;
            # the sender must still refuse this tampered staging response.
            value = response.json()
            value["durable_ack"] = True
            raw = json.dumps(value).encode()
        return response.status_code, bytes(raw)


def send(sender, plan, directory, transport):
    return sender.send(plan, directory, CAPTURE_KEY, transport=transport,
                       attempts=2, sleep=lambda _: None)


def delivery_commits(context):
    return sum(call["path"] == "/rest/v1/rpc/selflearn_journal_commit_capture_delivery_v1"
               for call in context["bridge"].calls)


def test_actual_sender_prepare_stage_quota_and_atomic_original_ack(raw_producer, nfl_sender, tmp_path):
    plan = saved_plan(nfl_sender, raw_producer["captures"][0], tmp_path / "authority")
    original_files = {path: path.read_bytes() for path in (tmp_path / "authority").rglob("*") if path.is_file()}
    with intake_context(raw_producer) as context:
        journal = context["other"]
        original_receipts = copy.deepcopy(journal.receipts())
        events = len(context["events"])
        prepared = nfl_sender.prepare(plan, tmp_path / "outbox")
        assert prepared["network_used"] is False and prepared["learning_ack"] is False
        assert len(context["events"]) == events
        assert journal.get_capture_delivery(plan["request"]["delivery_id"]) is None
        transport = InProcessSenderTransport(context)
        reply = send(nfl_sender, plan, tmp_path / "outbox", transport)
        assert reply["status"] == "original_result"
        assert reply["durable_result_ack"] is True and reply["learning_ack"] is True
        assert reply["complete_success"] is True and reply["current_membership_reverified"] is False
        original = reply["result"]
        assert original["status"] == "accepted"
        assert (original["added_observations"], original["added_revisions"]) == (4, 4)
        assert len(original["groups"]) == 1 and len(original["groups"][0]["receipt_ids"]) == 4
        assert journal.get_capture_delivery(plan["request"]["delivery_id"]) == original
        persisted = list((tmp_path / "outbox").rglob("results/original.json"))
        assert len(persisted) == 1 and json.loads(persisted[0].read_bytes()) == original
        snapshot = journal.read_observed_snapshot()
        assert len(snapshot["observations"]) == len(snapshot["revisions"]) == 4
        assert journal.receipts() == original_receipts
        assert delivery_commits(context) == 1
        assert context["events"][0] == "quota"
        assert context["events"].index("quota") < context["events"].index("storage-POST")
        usage = context["bridge"].staging_summary(context["namespace_id"])
        assert usage["reserved_chunks"] == len(context["storage"].objects)
        assert usage["reserved_total_bytes"] == sum(map(len, context["storage"].objects.values()))
        assert transport.calls[0][0] == "GET"
        assert transport.calls[-1][1] == DELIVERIES
        stages = [json.loads(path.read_bytes()) for path in (tmp_path / "outbox").rglob("staging/*.json")]
        assert stages and all(not ack["learning_accepted"] and not ack["durable_ack"] for ack in stages)
        assert all("bucket" not in ack and "key" not in ack for ack in stages)
        assert all(path.read_bytes() == original_files[path] for path in original_files)
        assert all(CAPTURE_KEY not in path.read_text() for path in (tmp_path / "outbox").rglob("*.json"))


def test_actual_sender_lost_reply_new_vintage_correction_then_old_retry_recovers_original(raw_producer, nfl_sender, tmp_path):
    plan = saved_plan(nfl_sender, raw_producer["captures"][0], tmp_path / "authority")
    corrected_plan = saved_plan(nfl_sender, raw_producer["captures"][2], tmp_path / "authority")
    with intake_context(raw_producer) as context:
        journal = context["other"]
        original_receipts = copy.deepcopy(journal.receipts())
        lost_transport = InProcessSenderTransport(context, lose_delivery_reply=True)
        first = send(nfl_sender, plan, tmp_path / "outbox", lost_transport)
        assert lost_transport.lost and first["complete_success"] is True
        original = copy.deepcopy(first["result"])
        assert delivery_commits(context) == 1
        assert sum(method == "POST" and route == DELIVERIES for method, route, body in lost_transport.calls) == 1
        assert lost_transport.calls[-1][0] == "GET", "Lost POST reply recovers the actual committed ledger, not a synthetic ACK"
        persisted = next((tmp_path / "outbox").rglob("results/original.json"))
        frozen_ack_bytes = persisted.read_bytes()
        complete = raw_producer["complete"]
        admit(journal, complete["source_objects"], [row["request"]["payload"] for row in complete["generations"]])
        assert len(journal.receipts()) == 6
        assert all(row in journal.receipts() for row in original_receipts)
        changed = send(nfl_sender, corrected_plan, tmp_path / "outbox", InProcessSenderTransport(context))
        assert changed["complete_success"] is True
        assert len(changed["result"]["groups"][0]["receipt_ids"]) == 6
        assert (changed["result"]["added_observations"], changed["result"]["added_revisions"]) == (6, 6)
        before = copy.deepcopy(journal.read_observed_snapshot())
        assert all(row["hit"] is False for row in journal.latest_resolutions())
        commits, storage_calls = delivery_commits(context), len(context["storage"].calls)
        usage = context["bridge"].staging_summary(context["namespace_id"])
        retry_transport = InProcessSenderTransport(context)
        replay = send(nfl_sender, plan, tmp_path / "outbox", retry_transport)
        assert replay["result"] == original
        assert len(replay["result"]["groups"][0]["receipt_ids"]) == 4
        assert replay["current_membership_reverified"] is False
        assert len(retry_transport.calls) == 1 and retry_transport.calls[0][0] == "GET"
        assert delivery_commits(context) == commits
        assert len(context["storage"].calls) == storage_calls
        assert context["bridge"].staging_summary(context["namespace_id"]) == usage
        assert journal.read_observed_snapshot() == before, "Old delivery retry must not revert corrections or manufacture new observations"
        assert persisted.read_bytes() == frozen_ack_bytes
        pinned = journal._load_observed_snapshot(original["watermark"])
        assert len(pinned["receipts"]) == len(pinned["observations"]) == len(pinned["revisions"]) == 4
        assert all(row["resolution"]["hit"] is True for row in pinned["revisions"])


def test_actual_sender_quota_denial_precedes_storage_and_leaves_pending_without_learning(raw_producer, nfl_sender, tmp_path):
    plan = saved_plan(nfl_sender, raw_producer["captures"][0], tmp_path / "authority")
    with intake_context(raw_producer, maximum=1) as context:
        reply = send(nfl_sender, plan, tmp_path / "outbox", InProcessSenderTransport(context))
        assert reply["status"] == "pending" and reply["learning_ack"] is False
        assert context["storage"].calls == [] and context["storage"].objects == {}
        assert delivery_commits(context) == 0
        assert context["other"].get_capture_delivery(plan["request"]["delivery_id"]) is None
        snapshot = context["other"].read_observed_snapshot()
        assert snapshot["observations"] == snapshot["revisions"] == []
        usage = context["bridge"].staging_summary(context["namespace_id"])
        assert usage["reserved_chunks"] == usage["reserved_total_bytes"] == 0
        assert list((tmp_path / "outbox").rglob("outbox/delivery.json"))
        assert not list((tmp_path / "outbox").rglob("results/*.json"))


def test_actual_staging_but_malformed_ack_cannot_reach_outcome_commit(raw_producer, nfl_sender, tmp_path):
    plan = saved_plan(nfl_sender, raw_producer["captures"][0], tmp_path / "authority")
    with intake_context(raw_producer) as context:
        transport = InProcessSenderTransport(context, forge_stage_ack=True)
        reply = send(nfl_sender, plan, tmp_path / "outbox", transport)
        assert reply["status"] == "pending" and reply["learning_ack"] is False
        assert reply["reason"] == "invalid_staging_ack"
        assert context["storage"].objects, "Failed acknowledgement does not claim rollback of staged bytes"
        usage = context["bridge"].staging_summary(context["namespace_id"])
        assert usage["reserved_chunks"] == 1 and usage["reserved_total_bytes"] > 0
        assert delivery_commits(context) == 0
        assert not any(method == "POST" and route == DELIVERIES for method, route, body in transport.calls)
        snapshot = context["other"].read_observed_snapshot()
        assert snapshot["observations"] == snapshot["revisions"] == []
        assert context["other"].get_capture_delivery(plan["request"]["delivery_id"]) is None


def test_actual_owner_generation_or_wrong_keys_cannot_stage_or_inspect_capture_results(raw_producer, nfl_sender, tmp_path):
    plan = saved_plan(nfl_sender, raw_producer["captures"][0], tmp_path / "authority")
    with intake_context(raw_producer) as context:
        dto = make_chunk(b"private fixture bytes")
        before = len(context["bridge"].calls)
        for key in (ENV["SELFLEARN_OPERATOR_TOKEN"], GENERATION_KEY, "wrong-credential", ""):
            headers = {"Authorization": "Bearer " + key}
            for route, body in ((CHUNKS, dto), (DELIVERIES, plan["request"])):
                response = context["client"].post(route, json=body, headers=headers)
                assert response.status_code == 401
                assert response.headers["cache-control"] == "no-store"
            response = context["client"].get(DELIVERIES + "/" + plan["request"]["delivery_id"], headers=headers)
            assert response.status_code == 401
        assert len(context["bridge"].calls) == before, "Wrong scope is refused before quota/database/storage"
        assert context["storage"].calls == [] and context["events"] == []
        assert context["client"].get("/api/status", headers={"Authorization": "Bearer " + CAPTURE_KEY}).status_code == 401
