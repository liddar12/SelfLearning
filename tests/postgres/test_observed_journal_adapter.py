"""Actual opt-in Python port → additive PostgreSQL RPCs, no network/cloud.

Sequential WASM transactions prove contract behavior, not native concurrent
locking or hosted PostgREST/deployment acceptance.
"""
import copy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from selflearn_core.adapters.nfl_observed_resolution import DNP_BASIS, SNAP_SOURCE, resolve_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.adapters.nfl_observed_journal import NFLObservedJournal
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import _canonical
from selflearn_core.store.supabase_store import StoreError
from test_adapter_integration import PostgresBridge


# Reuse the independent synthetic frozen-receipt fixture, not production data.
TESTS = Path(__file__).resolve().parents[2] / "packages/selflearn-core/tests"
sys.path.insert(0, str(TESTS))
try:
    spec = importlib.util.spec_from_file_location("observed_port_fixture", TESTS / "test_nfl_observed_resolution.py")
    fixture_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture_module)
finally:
    sys.path.pop(0)


@pytest.fixture
def observed_bridge(monkeypatch):
    monkeypatch.setenv("SELFLEARN_OBSERVED_JOURNAL_TEST", "1")
    bridge = PostgresBridge()
    try:
        yield bridge
    finally:
        bridge.close()


def port(bridge, **kwargs):
    return NFLObservedJournal("https://offline.supabase.invalid", "sb_secret_offline_observed_fixture",
                                   transport=bridge, **kwargs)


def compute(store, source, leg, games):
    snapshot = store.read_observed_snapshot()
    batch = resolve_observed(snapshot["receipts"], [source], [leg], games,
        observed_utc=datetime.now(timezone.utc).isoformat(), existing_revisions=snapshot["revisions"],
        existing_observations=snapshot["observations"])
    return snapshot, batch


def commit(store, snapshot, batch):
    assert batch["rejections"] == [], batch
    return store.commit_observed(batch["observation_proposals"], batch["proposals"], watermark=snapshot["watermark"])


def test_actual_indexed_observations_CAS_no_change_fence_and_historical_replay(observed_bridge):
    store, other = port(observed_bridge, page_size=1), port(observed_bridge)
    r, source, leg, games = fixture_module.evidence()
    assert store.append_receipt(r)
    first_snapshot, first = compute(store, source, leg, games)
    first_ack = commit(store, first_snapshot, first)
    assert (first_ack["added_observations"], first_ack["added_revisions"]) == (1, 1)
    a_snapshot, a = compute(store, source, fixture_module.corrected(leg, 80, "A06", "2026-09-02T06:00:00Z"), games)
    b_snapshot, b = compute(other, source, fixture_module.corrected(leg, 30, "B05", "2026-09-02T05:00:00Z"), games)
    assert a_snapshot["watermark"] == b_snapshot["watermark"]
    a_ack = commit(store, a_snapshot, a)
    assert (a_ack["added_observations"], a_ack["added_revisions"]) == (1, 0)
    before = len(observed_bridge.calls)
    with pytest.raises(JournalConflict):
        commit(other, b_snapshot, b)
    assert len(observed_bridge.calls) == before + 1, "CAS conflict must not hide a recompute/retry"
    _, stale = compute(other, source, fixture_module.corrected(leg, 30, "B05", "2026-09-02T05:00:00Z"), games)
    assert stale["proposals"] == [] and stale["observation_proposals"] == []
    newer_snapshot, newer = compute(store, source, fixture_module.corrected(leg, 30, "B07", "2026-09-02T07:00:00Z"), games)
    assert commit(store, newer_snapshot, newer)["added_revisions"] == 1
    historical_snapshot, historical = compute(store, source, leg, games)
    assert historical["proposals"] == first["proposals"]
    replay_ack = commit(store, historical_snapshot, historical)
    assert (replay_ack["added_observations"], replay_ack["added_revisions"]) == (0, 0)
    final = store.read_observed_snapshot()
    assert len(final["observations"]) == 3 and len(final["revisions"]) == 2
    assert final["revisions"][-1]["resolution"]["hit"] is False
    assert final["receipts"] == [r]
    assert all(datetime.fromisoformat(row["recorded_utc"].replace("Z", "+00:00")) >=
               datetime.fromisoformat(row["payload"]["observed_utc"].replace("Z", "+00:00"))
               for row in final["observation_records"])
    assert store.read_snapshot()["watermark"] == final["watermark"]
    assert all(c["path"].endswith("selflearn_journal_read_observed_v1")
               for c in observed_bridge.calls if c["data"].get("p_view"))


def test_actual_database_failure_leaves_neither_observation_nor_revision(observed_bridge):
    r, source, leg, games = fixture_module.evidence()

    class RejectRevisionTransport:
        def request(self, method, path, *, data):
            if path.endswith("selflearn_journal_commit_observed_v1"):
                data = copy.deepcopy(data)
                batch = json.loads(data["p_batch_json"])
                batch["revisions"][0]["resolution"].pop("measurement_id")
                data["p_batch_json"] = _canonical(batch)
            return observed_bridge.request(method, path, data=data)

    store = port(RejectRevisionTransport())
    store.append_receipt(r)
    snapshot, batch = compute(store, source, leg, games)
    with pytest.raises(StoreError) as error:
        commit(store, snapshot, batch)
    assert error.value.status in (422, 503)
    assert store._observed_snapshot is None
    after = port(observed_bridge).read_observed_snapshot()
    assert after["watermark"] == snapshot["watermark"]
    assert after["observations"] == [] and after["revisions"] == []


def test_actual_kernel_carried_outcome_component_is_not_the_complete_observation_fence(observed_bridge):
    store = port(observed_bridge, page_size=1)
    r, source, leg, games = fixture_module.evidence()
    store.append_receipt(r)
    dnp = {**leg, "result": "void", "actual": None, "played": False, "reason": "did_not_play",
           "source": SNAP_SOURCE, "participation_basis": DNP_BASIS,
           "observed_utc": "2026-09-02T04:20:00Z", "observation_id": fingerprint("snap-DNP04")}
    initial_snapshot, initial = compute(store, source, dnp, games)
    assert commit(store, initial_snapshot, initial)["added_revisions"] == 1
    confirmed_dnp = {**dnp, "observed_utc": "2026-09-02T06:00:00Z", "observation_id": fingerprint("snap-DNP06")}
    confirmation_snapshot, confirmation = compute(store, source, confirmed_dnp, games)
    assert confirmation["proposals"] == initial["proposals"]
    confirmation_ack = commit(store, confirmation_snapshot, confirmation)
    assert (confirmation_ack["added_observations"], confirmation_ack["added_revisions"]) == (1, 0)

    # A measured stat row needs no current snap component. Its immutable outcome
    # carries the previous OUTCOME component (04), while the independent index
    # must retain the newer no-change observation (06) as the actual fence.
    final_snapshot, final = compute(store, source,
        fixture_module.corrected(leg, 80, "stats-FINAL07", "2026-09-02T07:00:00Z"), games)
    resolution = final["proposals"][0]["resolution"]
    assert SNAP_SOURCE not in resolution["source_observations"]
    assert resolution["observation_watermarks"][SNAP_SOURCE]["observed_utc"] == dnp["observed_utc"]
    assert commit(store, final_snapshot, final)["added_revisions"] == 1
    snapshot = store.read_observed_snapshot()
    assert len(snapshot["observations"]) == 3 and len(snapshot["revisions"]) == 2
    assert max(record["source_observations"][SNAP_SOURCE]["observed_utc"]
               for record in snapshot["observations"] if SNAP_SOURCE in record["source_observations"]) == confirmed_dnp["observed_utc"]

    stale = {**dnp, "observed_utc": "2026-09-02T05:00:00Z", "observation_id": fingerprint("snap-stale-DNP05")}
    _, rejected = compute(store, source, stale, games)
    assert rejected["proposals"] == [] and rejected["observation_proposals"] == []
    assert "stale_conflicting_source_observation" in rejected["summary"]["exclusion_reasons"]
    assert store.read_observed_snapshot() == snapshot
