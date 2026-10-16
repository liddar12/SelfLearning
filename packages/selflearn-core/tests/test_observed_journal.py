"""Opt-in observed journal wire/CAS tests, with no network or local fallback."""
import copy

import pytest

from selflearn_core.adapters.nfl_observed_resolution import observation_record
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.adapters.nfl_observed_journal import NFLObservedJournal
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import _canonical
from selflearn_core.store.supabase_store import StoreConflict, StoreError
from test_nfl_observed_resolution import Delivery, corrected, evidence, resolve, reseal_observation
from test_supabase_journal import Scripted, page


ORIGIN = "https://offline.supabase.invalid"
KEY = "sb_secret_observed_offline_fixture"
DB_CLOCK = "2026-09-03T12:00:00Z"


def fixture():
    r, s, leg, games = evidence()
    batch = resolve(r, s, [leg], games)
    return r, batch["observation_proposals"][0], batch["proposals"][0]


def receipt_row(r):
    return {"receipt_id": r["receipt_id"], "payload_json": _canonical(r),
            "received_utc": DB_CLOCK, "journal_seq": "1"}


def revision_row(proposal, seq="2"):
    return {"revision_id": proposal["revision_id"], "payload_json": _canonical({"version": 1, **proposal}),
            "recorded_utc": DB_CLOCK, "journal_seq": seq}


def observation_row(record, seq="3"):
    return {"observation_id": record["observation_id"], "receipt_id": record["receipt_id"],
            "payload_json": _canonical(record), "recorded_utc": DB_CLOCK, "journal_seq": seq}


def responses(r, record=None, proposal=None, *, watermark="10"):
    return [page([receipt_row(r)], watermark=watermark), page(watermark=watermark),
            page([revision_row(proposal)] if proposal else [], watermark=watermark),
            page([observation_row(record)] if record else [], watermark=watermark)]


def journal(results, **kwargs):
    wire = Scripted(results)
    return NFLObservedJournal(ORIGIN, KEY, transport=wire, **kwargs), wire


def ack(observations=1, revisions=1, watermark="12"):
    return {"version": 1, "added_observations": observations, "added_revisions": revisions, "watermark": watermark}


def state_responses(state):
    result = responses(state.r)
    result[2]["rows"] = [revision_row(proposal, str(index + 2))
                          for index, proposal in enumerate(state.history.values())]
    result[3]["rows"] = [observation_row(record, str(index + len(state.history) + 2))
                          for index, record in enumerate(state.observations.values())]
    return result


def test_complete_snapshot_uses_only_new_global_fence_and_separates_database_clock():
    r, record, proposal = fixture()
    db, wire = journal(responses(r, record, proposal))
    snapshot = db.read_observed_snapshot()
    assert set(snapshot) == {"version", "watermark", "receipts", "generation_records", "revisions",
                             "observations", "observation_records"}
    assert snapshot["observations"] == [record]
    assert snapshot["observation_records"] == [{"observation_id": record["observation_id"],
        "payload": record, "recorded_utc": DB_CLOCK}]
    assert snapshot["observations"][0]["observed_utc"] != DB_CLOCK
    assert all(c[1] == "/rest/v1/rpc/selflearn_journal_read_observed_v1" for c in wire.calls)
    assert [c[2]["data"]["p_watermark"] for c in wire.calls] == [None, "10", "10", "10"]
    snapshot["observations"][0]["measurement"]["actual"] = 999
    assert db._observed_snapshot["observations"] == [record], "external mutation must not poison commit validation"


def test_read_snapshot_and_status_remain_compatible_without_upgrading_existing_api_readiness():
    r, _, _ = fixture()
    db, wire = journal(responses(r)[:3] + [page([{"schema_version": 1, "backend": "postgres", "journal_seq": "10"}], watermark="10")])
    assert set(db.read_snapshot()) == {"version", "watermark", "receipts", "generation_records", "revisions"}
    assert db.status() == {"schema_version": 1, "backend": "postgres", "watermark": "10"}
    assert len(wire.calls) == 4


def test_atomic_commit_encodes_canonical_batch_and_never_invents_database_timestamp():
    r, record, proposal = fixture()
    db, wire = journal(responses(r) + [ack()])
    assert db.read_observed_snapshot()["watermark"] == "10"
    result = db.commit_observed([record], [proposal], watermark="10")
    assert result == ack()
    request = wire.calls[-1]
    assert request[1] == "/rest/v1/rpc/selflearn_journal_commit_observed_v1"
    assert request[2]["data"] == {"p_batch_json": _canonical({"version": 1, "observations": [record],
        "revisions": [{"version": 1, **proposal}]}), "p_watermark": "10"}
    assert "recorded_utc" not in request[2]["data"]["p_batch_json"]
    assert db._observed_snapshot is None


def test_direct_commit_loads_exact_requested_fence_instead_of_reading_latest():
    r, record, proposal = fixture()
    db, wire = journal(responses(r) + [ack()])
    db.commit_observed([record], [proposal], watermark="10")
    assert [c[2]["data"]["p_watermark"] for c in wire.calls[:-1]] == ["10"] * 4


def test_stale_global_fence_is_one_sanitized_conflict_with_no_hidden_retry():
    r, record, proposal = fixture()
    db, wire = journal(responses(r) + [StoreConflict()])
    db.read_observed_snapshot()
    with pytest.raises(JournalConflict, match="reread_required"):
        db.commit_observed([record], [proposal], watermark="10")
    assert len(wire.calls) == 5 and db._observed_snapshot is None


@pytest.mark.parametrize("failure", [RuntimeError("secret source body"), StoreError(403), StoreError(503)])
def test_rpc_failure_is_redacted_and_never_falls_back(failure):
    db, wire = journal([failure])
    with pytest.raises(StoreError) as error:
        db.read_observed_snapshot()
    assert "secret" not in str(error.value) and len(wire.calls) == 1


@pytest.mark.parametrize("result", [None, {"version": True}, {"version": 2},
    ack(True, 1), ack(2, 1), ack(1, -1), ack(1, 1, "9"), ack(0, 0, "12"), ack(1, 1, "10"),
    ack(0, 0, "10"), ack(0, 1, "12"), ack(1, 0, "12"),
    {**ack(), "extra": "not-allowed"}])
def test_commit_rejects_malformed_ack_types_counts_and_watermarks(result):
    r, record, proposal = fixture()
    db, wire = journal(responses(r) + [result])
    with pytest.raises(StoreError):
        db.commit_observed([record], [proposal], watermark="10")
    assert len(wire.calls) == 5


def test_exact_replay_ack_has_no_additions_or_watermark_change():
    r, record, proposal = fixture()
    db, _ = journal(responses(r, record, proposal) + [ack(0, 0, "10")])
    assert db.commit_observed([record], [proposal], watermark="10") == ack(0, 0, "10")


def test_atomic_batch_rejects_missing_proofs_unknown_links_duplicates_and_wrong_resolution_policy():
    r, record, proposal = fixture()
    db, wire = journal(responses(r))
    db.read_observed_snapshot()
    with pytest.raises(ValueError, match="missing_existing_observation_proof"):
        db.commit_observed([], [proposal], watermark="10")
    with pytest.raises(ValueError, match="without_corresponding_outcome"):
        db.commit_observed([record], [], watermark="10")
    with pytest.raises(ValueError, match="duplicate"):
        db.commit_observed([record, record], [proposal], watermark="10")
    with pytest.raises(ValueError, match="v2_resolution"):
        db.commit_observed([record], [{**proposal, "resolution": {}}], watermark="10")
    bad = copy.deepcopy(record)
    bad["receipt_id"] = fingerprint("not-recorded")
    reseal_observation(bad)
    with pytest.raises(ValueError, match="unknown_receipt"):
        db.commit_observed([bad], [proposal], watermark="10")
    with pytest.raises(ValueError, match="atomic_commit"):
        db.append_resolution(**proposal)
    assert len(wire.calls) == 4


def test_batch_count_and_outer_http_body_are_bounded_before_any_io():
    r, record, proposal = fixture()
    db, wire = journal([])
    for obs, props in (([record] * 501, []), ([], [proposal] * 501), ((), []), ([], ())):
        with pytest.raises(ValueError, match="array_bound"):
            db.commit_observed(obs, props, watermark="10")
    oversized = copy.deepcopy(proposal)
    # Escaped JSON text inside the RPC body, not just inner canonical bytes.
    oversized["resolution"]["trace"] = "\\" * (3 * 1024 * 1024)
    with pytest.raises(ValueError, match="byte_bound"):
        db.commit_observed([record], [oversized], watermark="10")
    assert wire.calls == []


def test_more_than_one_thousand_observations_are_enumerated_at_one_fence():
    r, original, proposal = fixture()
    records = [original]
    for number in range(1002):
        record = copy.deepcopy(original)
        record["source_observations"]["nflverse.stats_player"]["observation_id"] = fingerprint(["capture", number])
        records.append(reseal_observation(record))
    rows = [observation_row(record, str(index + 3)) for index, record in enumerate(records)]
    result = responses(r, proposal=proposal, watermark="2000")[:3]
    result += [page(rows[:500], next_after=rows[499]["journal_seq"], watermark="2000"),
               page(rows[500:1000], next_after=rows[999]["journal_seq"], watermark="2000"),
               page(rows[1000:], watermark="2000")]
    db, wire = journal(result)
    snapshot = db.read_observed_snapshot()
    assert len(snapshot["observations"]) == 1003
    assert [c[2]["data"]["p_after"] for c in wire.calls[3:]] == [None, "502", "1002"]
    assert all(c[2]["data"]["p_watermark"] == "2000" for c in wire.calls[1:])


def test_short_byte_bounded_page_with_cursor_remains_complete():
    r, record, proposal = fixture()
    result = responses(r, proposal=proposal)[:3]
    result += [page([observation_row(record)], next_after="3", watermark="10"), page(watermark="10")]
    db, _ = journal(result)
    assert db.read_observed_snapshot()["observations"] == [record]


@pytest.mark.parametrize("mutate", [
    lambda row: row.update(recorded_utc="2026-09-02T04:19:00Z"),
    lambda row: row.update(observation_id=fingerprint("wrong")),
    lambda row: row.update(receipt_id=fingerprint("wrong")),
    lambda row: row.update(payload_json='{ "not":"canonical"}'),
    lambda row: row.update(extra="forbidden"),
])
def test_snapshot_rejects_corrupt_identity_canonical_text_and_database_clock(mutate):
    r, record, proposal = fixture()
    result = responses(r, record, proposal)
    mutate(result[-1]["rows"][0])
    db, _ = journal(result)
    with pytest.raises(StoreError) as error:
        db.read_observed_snapshot()
    assert error.value.status == 502


def test_existing_v2_without_indexed_proof_or_with_orphan_measurement_fails_closed():
    r, record, proposal = fixture()
    db, _ = journal(responses(r, proposal=proposal))
    with pytest.raises(StoreError):
        db.read_observed_snapshot()
    db, _ = journal(responses(r, record=record))
    with pytest.raises(StoreError):
        db.read_observed_snapshot()


def test_legacy_correction_cannot_downgrade_an_observed_receipt_history():
    r, record, proposal = fixture()
    legacy = {"revision_id": "legacy-unindexed-correction", "supersedes": proposal["revision_id"],
              "reason": "legacy correction", "resolution": {
                  "receipt_id": r["receipt_id"], "contract_id": r["contract_id"], "event_id": r["event_id"],
                  "status": "FINAL", "hit": False, "actual": 30,
                  "resolved_utc": "2026-09-02T07:00:00Z", "source": "legacy.fixture"}}
    result = responses(r, record, proposal)
    result[2]["rows"].append(revision_row(legacy, seq="4"))
    db, _ = journal(result)
    with pytest.raises(StoreError) as error:
        db.read_observed_snapshot()
    assert error.value.status == 502


def test_new_historical_measurement_observation_requires_actual_current_head_correction():
    r, s, leg, games = evidence()
    state = Delivery(r, s, games)
    first = state.send(leg)
    second = state.send(corrected(leg, 30, "new-B", "2026-09-02T06:00:00Z"))
    later_a = resolve(r, s, [corrected(leg, 80, "new-A", "2026-09-02T07:00:00Z")], games)
    result = responses(r, proposal=first["proposals"][0])
    result[2]["rows"].append(revision_row(second["proposals"][0], seq="3"))
    result[3]["rows"] = [observation_row(record, str(index + 4))
                          for index, record in enumerate(state.observations.values())]
    db, wire = journal(result + [ack(0, 0, "10")])
    db.read_observed_snapshot()
    with pytest.raises(ValueError, match="new_observation_requires_current_outcome"):
        db.commit_observed(later_a["observation_proposals"], [], watermark="10")
    assert len(wire.calls) == 4
    # Already indexed old A is still a valid exact historical no-op replay.
    assert db.commit_observed(first["observation_proposals"], first["proposals"], watermark="10") == ack(0, 0, "10")


def test_newer_correction_is_valid_but_fence_from_unchanged_capture_blocks_older_proposal():
    r, s, leg, games = evidence()
    state = Delivery(r, s, games)
    first = state.send(leg)
    initial_rows = state_responses(state)
    newer = state.send(corrected(leg, 30, "valid-B06", "2026-09-02T06:00:00Z"))
    db, _ = journal(initial_rows + [ack()])
    assert db.commit_observed(newer["observation_proposals"], newer["proposals"], watermark="10") == ack()
    state = Delivery(r, s, games)
    state.send(leg)
    state.send(corrected(leg, 80, "same-A06", "2026-09-02T06:00:00Z"))
    # A caller computing against an incomplete observation subset could propose
    # this; the port independently validates against its complete DB snapshot.
    stale = resolve(r, s, [corrected(leg, 30, "stale-B05", "2026-09-02T05:00:00Z")], games,
                    first["proposals"], first["observation_proposals"])
    db, wire = journal(state_responses(state))
    with pytest.raises(ValueError, match="stale_conflicting_source_observation"):
        db.commit_observed(stale["observation_proposals"], stale["proposals"], watermark="10")
    assert len(wire.calls) == 4


def test_unchanged_measurement_cannot_create_a_fake_new_outcome_revision():
    r, s, leg, games = evidence()
    state = Delivery(r, s, games)
    first = state.send(leg)["proposals"][0]
    later = resolve(r, s, [corrected(leg, 80, "same-A06", "2026-09-02T06:00:00Z")], games)
    fake = later["proposals"][0]
    fake.update(supersedes=first["revision_id"], reason="fabricated-no-change")
    fake["revision_id"] = "nfl.observed_resolution_v2:" + fingerprint({"receipt_id": r["receipt_id"],
        "observation_id": fake["resolution"]["evidence_observation_id"], "supersedes": first["revision_id"]})
    db, wire = journal(state_responses(state))
    with pytest.raises(ValueError, match="unchanged_measurement_requires_original_revision"):
        db.commit_observed(later["observation_proposals"], [fake], watermark="10")
    assert len(wire.calls) == 4


def test_rehashed_game_identity_missing_resolution_and_malformed_inputs_fail_locally():
    r, record, proposal = fixture()
    db, wire = journal(responses(r))
    db.read_observed_snapshot()
    bad = copy.deepcopy(record)
    bad["measurement"]["game"]["home"] = bad["measurement"]["game"]["away"]
    reseal_observation(bad)
    with pytest.raises(ValueError, match="game_identity"):
        db.commit_observed([bad], [proposal], watermark="10")
    with pytest.raises(ValueError, match="invalid_observed_record"):
        db.commit_observed([None], [], watermark="10")
    with pytest.raises(ValueError, match="revision_proposal"):
        db.commit_observed([], [None], watermark="10")
    unknown = copy.deepcopy(proposal)
    unknown["resolution"]["receipt_id"] = fingerprint("unrecorded")
    with pytest.raises(ValueError, match="outcome_unknown_receipt"):
        db.commit_observed([record], [unknown], watermark="10")
    assert len(wire.calls) == 4


def test_existing_non_observed_append_methods_still_delegate_to_original_write_rpc():
    r, _, _ = fixture()
    db, wire = journal([{"version": 1, "added": True}])
    assert db.append_receipt(r) is True
    assert wire.calls[0][1] == "/rest/v1/rpc/selflearn_journal_append_receipt_v1"


@pytest.mark.parametrize("line", [True, 59.5, "private-input-not-a-number", "59.500"])
def test_port_requires_the_frozen_canonical_numeric_string_line_without_normalizing_it(line):
    r, record, proposal = fixture()
    invalid = copy.deepcopy(record)
    invalid["measurement"]["canonical_contract"][4] = line
    reseal_observation(invalid)
    db, wire = journal(responses(r))
    with pytest.raises(ValueError) as error:
        db.commit_observed([invalid], [proposal], watermark="10")
    assert "private-input" not in str(error.value)
    assert len(wire.calls) == 4
