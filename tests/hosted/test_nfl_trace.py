"""Read-only pinned trace/API/MCP; not hosted or browser acceptance."""
import copy
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from services.web.application import create_app
from services.web import nfl_trace as trace
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.probability_receipts import fingerprint
from .fakes import FakeStore, TEST_KEY
from .nfl_fixture import inputs

INGEST = "fixture-nfl-ingest-" + "a" * 32


@pytest.fixture
def snapshot(monkeypatch):
    journal, _, _, pool, schedule, cards, recorded = inputs(monkeypatch)
    doc, _ = journal.capture(pool, schedule, None, cards, recorded)
    adapted = adapt_prospective(journal_docs=[doc])
    assert adapted["receipts"] and not adapted["rejections"]
    rows = adapted["receipts"]
    ack = {"version": 1, "object_id": doc["envelope_id"], "durable": True, "rejections": [],
           "receipts": [{"receipt_id": r["receipt_id"], "source_receipt_id": r["source_receipt_id"], "status": "recorded"} for r in rows]}
    return {"version": 1, "watermark": "100", "receipts": rows,
            "generation_records": [{"object_id": doc["envelope_id"], "payload": doc, "ack": ack,
                                     "recorded_utc": "2026-09-01T15:00:00Z"}], "revisions": []}


def node(receipt, *, rid="revision1", parent=None, hit=True, recorded="2026-09-03T12:00:00Z", void=False):
    resolution = {k: receipt[k] for k in ("receipt_id", "contract_id", "event_id")}
    resolution.update(status="VOID" if void else "FINAL", hit=None if void else hit,
                      resolved_utc="2026-09-03T10:00:00Z", source="synthetic outcome fixture")
    if void:
        resolution.update(authoritative=True, final_evidence_status="FINAL", reason="did_not_play")
    return {"version": 1, "revision_id": rid, "supersedes": parent, "reason": None,
            "resolution": resolution, "recorded_utc": recorded}


def test_pending_catalog_and_exact_trace_preserve_unknown_authority(snapshot):
    before = fingerprint(snapshot)
    result = trace.catalog(snapshot)
    assert result["total_forecasts"] == len(snapshot["receipts"])
    assert result["read_only"] is True and result["production_connection_verified"] is False
    assert all(r["outcome_state"] == "pending" and r["outcome_hit"] is None for r in result["records"])
    receipt = snapshot["receipts"][0]
    detail = trace.forecast_trace(snapshot, receipt["receipt_id"])
    assert detail["forecast"]["source_receipt_id"] == receipt["source_receipt_id"]
    assert detail["generations"][0]["envelope_id"] == snapshot["generation_records"][0]["object_id"]
    assert detail["source_freshness"] == detail["forecast"]["public_availability"] == "unverified"
    assert detail["forecast"]["model_adoption"] == "unverified"
    assert detail["outcome_revisions"] == [] and detail["warnings"]
    assert detail["observed_proof_history_available"] is False
    assert fingerprint(snapshot) == before
    assert trace.forecast_trace(snapshot, "sha256:" + "0" * 64) is None
    assert trace.catalog(snapshot, query=receipt["event_id"], limit=1)["returned"] == 1
    assert trace.catalog(snapshot, query="does-not-exist")["matched_forecasts"] == 0


def test_corrected_void_outcomes_preserve_full_chain_and_null_not_zero(snapshot):
    receipt = snapshot["receipts"][0]
    snapshot["revisions"] = [node(receipt), node(receipt, rid="revision2", parent="revision1", hit=False,
                                                recorded="2026-09-04T12:00:00Z")]
    detail = trace.forecast_trace(snapshot, receipt["receipt_id"])
    assert detail["forecast"]["outcome_state"] == "graded" and detail["forecast"]["outcome_hit"] is False
    assert [r["revision_id"] for r in detail["outcome_revisions"]] == ["revision1", "revision2"]
    snapshot["revisions"].append(node(receipt, rid="revision3", parent="revision2", void=True,
                                      recorded="2026-09-05T12:00:00Z"))
    detail = trace.forecast_trace(snapshot, receipt["receipt_id"])
    assert detail["forecast"]["outcome_state"] == "void" and detail["forecast"]["outcome_hit"] is None
    assert detail["outcome_revisions"][-1]["actual"] is None
    snapshot["observations"] = []
    assert trace.forecast_trace(snapshot, receipt["receipt_id"])["observed_proof_history_available"] is True


@pytest.mark.parametrize("kind", ["fork", "orphan", "cycle", "duplicate", "wrong_contract", "late_resolution", "regressing_clock", "bool_version", "malformed"])
def test_invalid_crosslinked_revision_history_fails_closed(snapshot, kind):
    receipt = snapshot["receipts"][0]
    first, second = node(receipt), node(receipt, rid="revision2", parent="revision1")
    if kind == "fork": second["supersedes"] = None
    elif kind == "orphan": second["supersedes"] = "missing"
    elif kind == "cycle": first["supersedes"] = "revision2"
    elif kind == "duplicate": second["revision_id"] = first["revision_id"]
    elif kind == "wrong_contract": second["resolution"]["contract_id"] = "sha256:" + "0" * 64
    elif kind == "late_resolution": second["recorded_utc"] = "2026-09-02T10:00:00Z"
    elif kind == "regressing_clock": second["recorded_utc"] = "2026-09-03T11:00:00Z"
    elif kind == "bool_version": second["version"] = True
    elif kind == "malformed": second = {"resolution": None}
    snapshot["revisions"] = [first, second]
    with pytest.raises(trace.TraceError):
        trace.forecast_trace(snapshot, receipt["receipt_id"])


@pytest.mark.parametrize("kind", ["snapshot", "duplicate", "bad_receipt", "bad_ack", "ack_object", "envelope", "missing_original", "source_hash"])
def test_invalid_snapshot_generation_never_invents_trace(snapshot, kind):
    receipt = snapshot["receipts"][0]
    record = snapshot["generation_records"][0]
    if kind == "snapshot": snapshot["version"] = True
    elif kind == "duplicate": snapshot["receipts"].append(receipt)
    elif kind == "bad_receipt": receipt["served_prob"] = .999
    elif kind == "bad_ack": record["ack"]["durable"] = False
    elif kind == "ack_object": record["ack"]["object_id"] = "sha256:" + "0" * 64
    elif kind == "envelope": record["payload"]["week"] = 18
    elif kind in ("missing_original", "source_hash"):
        doc = record["payload"]
        if kind == "missing_original": doc["receipts"] = []
        else: doc["receipts"][0]["learning_receipt"]["base_prob"] = .999
        oid = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
        doc["envelope_id"] = record["object_id"] = record["ack"]["object_id"] = oid
    with pytest.raises(trace.TraceError):
        trace.forecast_trace(snapshot, receipt["receipt_id"])


def test_limits_empty_and_read_protocol_selection(snapshot):
    for kwargs in ({"query": "a" * 129}, {"limit": True}, {"limit": 0}, {"limit": 51}):
        with pytest.raises(trace.TraceError): trace.catalog(snapshot, **kwargs)
    for rid in (None, "../other", "sha256:" + "A" * 64):
        with pytest.raises(trace.TraceError): trace.forecast_trace(snapshot, rid)
    with pytest.raises(trace.TraceError): trace.read_snapshot(object())
    class Read:
        def read_snapshot(self): return snapshot
    assert trace.read_snapshot(Read()) is snapshot
    class Observed(Read):
        def read_observed_snapshot(self): return {"observed": True}
    assert trace.read_snapshot(Observed()) == {"observed": True}
    empty = {**snapshot, "receipts": [], "generation_records": []}
    assert trace.catalog(empty)["records"] == []


def reseal_record(record, *, manifest=False):
    doc = record["payload"]
    if manifest:
        gen = doc["generations"][0]
        gen["generation_id"] = fingerprint({k: gen[k] for k in trace.MANIFEST_FIELDS})
        for original in doc["receipts"]:
            original["generation_id"] = gen["generation_id"]
    oid = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    doc["envelope_id"] = record["object_id"] = record["ack"]["object_id"] = oid


@pytest.mark.parametrize("kind", [
    "stale_selection", "stale_joint", "stale_source", "manifest_version", "document_version",
    "period", "source_names", "source_digest", "source_type", "missing_partition", "duplicate_partition",
    "foreign_partition", "accepted_source", "accepted_status", "ack_version", "receipt_membership",
    "source_generation", "source_duplicate", "offered_type", "offered_foreign", "source_clock", "database_clock",
])
def test_resealed_envelope_cannot_forge_manifest_or_ack_lineage(snapshot, kind):
    receipt = snapshot["receipts"][0]
    record = snapshot["generation_records"][0]
    doc, ack = record["payload"], record["ack"]
    gen = doc["generations"][0]
    rehash_manifest = True
    if kind == "stale_selection":
        gen["selection_policy_version"] = fingerprint("different policy")
        rehash_manifest = False
    elif kind == "stale_joint":
        gen["joint_model_version"] = fingerprint("different joint model")
        rehash_manifest = False
    elif kind == "stale_source":
        gen["source_snapshots"]["leg_pool"] = fingerprint("different pool")
        rehash_manifest = False
    elif kind == "manifest_version": gen["version"] = True
    elif kind == "document_version": doc["version"] = True
    elif kind == "period": gen["week"] = doc["week"] + 1
    elif kind == "source_names": gen["source_snapshots"]["foreign"] = fingerprint("foreign")
    elif kind == "source_digest": gen["source_snapshots"]["schedule"] = "unsealed"
    elif kind == "source_type": gen["source_snapshots"] = None
    elif kind == "missing_partition": ack["receipts"].pop()
    elif kind == "duplicate_partition": ack["receipts"].append(copy.deepcopy(ack["receipts"][0]))
    elif kind == "foreign_partition": ack["rejections"].append({"source_receipt_id": fingerprint("foreign"), "reason": "invalid"})
    elif kind == "accepted_source": ack["receipts"][0]["source_receipt_id"] = fingerprint("foreign")
    elif kind == "accepted_status": ack["receipts"][0]["status"] = "approved"
    elif kind == "ack_version": ack["version"] = True
    elif kind == "receipt_membership": gen["receipt_ids"] = []
    elif kind == "source_generation":
        doc["receipts"][-1]["generation_id"] = fingerprint("foreign generation")
        rehash_manifest = False
    elif kind == "source_duplicate": doc["receipts"].append(copy.deepcopy(doc["receipts"][0]))
    elif kind == "offered_type": gen["offered_cards"] = None
    elif kind == "offered_foreign": gen["offered_cards"] = [{"card_id": "foreign", "receipt_ids": [fingerprint("foreign")]}]
    elif kind == "source_clock": gen["pool_generated_utc"] = "2200-01-01T00:00:00Z"
    elif kind == "database_clock": record["recorded_utc"] = "1900-01-01T00:00:00Z"
    reseal_record(record, manifest=rehash_manifest)
    with pytest.raises(trace.TraceError):
        trace.forecast_trace(snapshot, receipt["receipt_id"])


def test_complete_ack_partition_can_include_rejected_neighbors(snapshot):
    receipt = snapshot["receipts"][0]
    record = snapshot["generation_records"][0]
    assert len(record["ack"]["receipts"]) > 1
    rejected = record["ack"]["receipts"].pop()
    record["ack"]["rejections"].append({"source_receipt_id": rejected["source_receipt_id"], "reason": "row_quarantined"})
    snapshot["receipts"] = [r for r in snapshot["receipts"] if r["receipt_id"] != rejected["receipt_id"]]
    # An accepted neighbor remains readable; we do not turn a rejection into a valid forecast.
    assert trace.forecast_trace(snapshot, receipt["receipt_id"])["generations"]


def test_owner_only_http_trace_mcp_and_disabled_failure_states(snapshot):
    calls = []
    @contextmanager
    def factory():
        class Read:
            def read_snapshot(self):
                calls.append("read")
                return copy.deepcopy(snapshot)
        yield Read()
    env = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": INGEST}
    receipt_id = snapshot["receipts"][0]["receipt_id"]
    route = "/api/v1/nfl/forecast-trace/" + receipt_id
    with TestClient(create_app(store=FakeStore(), env=env, journal_factory=factory)) as api:
        for token in ("", INGEST):
            assert api.get(route, headers={"Authorization": "Bearer " + token}).status_code == 401
        api.headers["Authorization"] = "Bearer " + TEST_KEY
        assert api.get(route).json()["forecast"]["receipt_id"] == receipt_id
        assert api.get(route).headers["cache-control"] == "no-store"
        assert api.get("/api/v1/nfl/forecasts?limit=1").json()["returned"] == 1
        assert api.get("/api/v1/nfl/forecasts?limit=51").status_code == 422
        assert api.get("/api/v1/nfl/forecasts?query=" + "x" * 129).status_code == 422
        assert api.get("/api/v1/nfl/forecast-trace/invalid").status_code == 422
        assert api.get("/api/v1/nfl/forecast-trace/sha256:" + "0" * 64).status_code == 404
        mcp_headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-11-25"}
        result = api.post("/mcp", headers=mcp_headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "get_nfl_forecast_trace", "arguments": {"receipt_id": receipt_id}}})
        assert result.status_code == 200 and not result.json()["result"].get("isError")
    with TestClient(create_app(store=FakeStore(), env=env)) as api:
        assert api.get(route, headers={"Authorization": "Bearer " + TEST_KEY}).status_code == 503
    @contextmanager
    def broken():
        raise RuntimeError("SECRET_schema_diagnostics")
        yield
    with TestClient(create_app(store=FakeStore(), env=env, journal_factory=broken)) as api:
        response = api.get(route, headers={"Authorization": "Bearer " + TEST_KEY})
        assert response.status_code == 503 and "SECRET" not in response.text
    assert calls and set(calls) == {"read"}

