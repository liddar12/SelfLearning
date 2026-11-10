"""Offline RPC adapter checks with scripted transport, NOT SQL execution."""
import copy
from unittest.mock import patch

import pytest

from selflearn_core.adapters.nfl_publication import validate_publication_attestation
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_journal import _canonical
from selflearn_core.store.supabase_store import StoreError
from test_supabase_journal import Scripted, ORIGIN, KEY, page, NOW, receipt_row, generation_row
from selflearn_core.adapters.nfl_publication_journal import NFLSupabasePublicationJournal


def journal(responses, **kwargs):
    wire = Scripted(responses)
    return NFLSupabasePublicationJournal(ORIGIN, KEY, transport=wire, **kwargs), wire
from test_probability_receipts import receipt


def record(object_id=None):
    policy = {"version": 1, "kind": "nfl.pool_http_publication.v1",
              "origin": "https://nfl.example.com", "path": "/data/leg_pool.json"}
    value = {"version": 1, "kind": "nfl.pool_http_publication_attestation.v1",
        "object_id": object_id or fingerprint("generation"), "generation_id": fingerprint("manifest"),
        "source_pool_id": fingerprint("pool"), "policy": policy, "policy_id": fingerprint(policy),
        "recorded_utc": NOW, "observation": {"observed_started_utc": NOW, "observed_completed_utc": NOW,
            "http_status": 200, "body_sha256": fingerprint("raw-bytes-placeholder"), "body_bytes": 2},
        "receipts": [], "rejections": [], "status": "no_supported_membership",
        "scope": "public_pool_http_evidence_only", "durable": True}
    value["attestation_id"] = fingerprint(value)
    return validate_publication_attestation(value)


def row(value, seq="10"):
    return {"object_id": value["object_id"], "attestation_json": _canonical(value),
            "recorded_utc": value["recorded_utc"], "journal_seq": seq}


def test_existing_publication_returns_original_before_new_observation_validation():
    original = record()
    db, wire = journal([page([row(original)])])
    assert db.commit_publication_attestation(original["object_id"], None) == original
    assert len(wire.calls) == 1
    assert wire.calls[0][1].endswith("selflearn_journal_read_publication_v1")
    assert wire.calls[0][2]["data"]["p_limit"] == 1


def test_atomic_rpc_sends_exact_observation_text_without_client_db_clock():
    value = record()
    observation = {"body_utf8": ' { "snow": "雪" }\n', "observed_completed_utc": NOW}
    db, wire = journal([page(), {"version": 1, "attestation": value, "replayed": False}])
    with patch("selflearn_core.adapters.nfl_publication_journal.build_publication_attestation", return_value=value) as build:
        assert db.commit_publication_attestation(value["object_id"], observation) == value
        assert build.call_count == 2, "pure join checked before write and against actual DB time"
        assert build.call_args.args[-1] == value["recorded_utc"]
    assert wire.calls[-1][1].endswith("selflearn_journal_commit_publication_v1")
    assert wire.calls[-1][2]["data"] == {"p_object_id": value["object_id"],
                                          "p_observation_json": _canonical(observation)}


def test_concurrent_replay_returns_winners_original_without_comparing_new_evidence():
    original = record()
    db, _ = journal([page(), {"version": 1, "attestation": original, "replayed": True}])
    with patch("selflearn_core.adapters.nfl_publication_journal.build_publication_attestation", return_value=original) as build:
        assert db.commit_publication_attestation(original["object_id"], {"observed_completed_utc": NOW}) == original
        assert build.call_count == 1


@pytest.mark.parametrize("mutate", [
    lambda r: {**r, "recorded_utc": "client-clock"},
    lambda r: {**r, "object_id": fingerprint("wrong")},
    lambda r: {**r, "attestation_json": _canonical({"version": 1})},
    lambda r: {**r, "extra": "sensitive"},
])
def test_malformed_publication_rows_fail_closed(mutate):
    value = record()
    db, _ = journal([page([mutate(row(value))])])
    with pytest.raises(StoreError) as error:
        db.get_publication_attestation(value["object_id"])
    assert error.value.status == 502


def test_publication_fit_snapshot_uses_one_fence_for_all_four_views():
    generation = {"version": 1, "receipts": [], "generations": [{"generation_id": fingerprint("manifest"),
        "source_snapshots": {"leg_pool": fingerprint("pool")}}]}
    generation["envelope_id"] = fingerprint(generation)
    value = record(generation["envelope_id"])
    db, wire = journal([page(watermark="10"),
        page([generation_row(generation, [])], watermark="10"),
        page(watermark="10"), page([row(value)], watermark="10")])
    snapshot = db.read_publication_fit_snapshot()
    assert snapshot["publication_attestations"] == [value]
    assert snapshot["watermark"] == "10"
    assert [c[2]["data"]["p_watermark"] for c in wire.calls] == [None, "10", "10", "10"]
    assert all(c[1].endswith("selflearn_journal_read_publication_v1") for c in wire.calls)
    assert [c[2]["data"]["p_view"] for c in wire.calls] == [
        "receipts", "generations", "revisions", "publication_attestations"]


def test_snapshot_refuses_publication_without_its_generation_at_same_fence():
    value = record()
    db, _ = journal([page(), page(), page(), page([row(value)])])
    with pytest.raises(StoreError):
        db.read_publication_fit_snapshot()


def test_new_commit_refuses_invalid_pure_join_before_any_rpc_write():
    db, wire = journal([page()])
    with patch("selflearn_core.adapters.nfl_publication_journal.build_publication_attestation", side_effect=ValueError("bad_pool")):
        with pytest.raises(ValueError, match="bad_pool"):
            db.commit_publication_attestation(fingerprint("generation"), {"observed_completed_utc": NOW})
    assert len(wire.calls) == 1
