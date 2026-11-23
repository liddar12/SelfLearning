"""Private scoped ownership avoids redundant graphs; trust checks still execute."""
import copy
from unittest.mock import patch

import pytest

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.adapters.nfl_capture_job import _PinnedJournal
from selflearn_core.adapters.nfl_capture_labels import _OwnedGameSnapshot, _ScopedPinnedJournal, _frozen_entries
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_journal import _canonical
from test_nfl_capture_labels import journal_fixture
from test_supabase_journal import Scripted, page

SCOPE = V.CaptureGameScope(2026, 6, "g6")


def port():
    fixture = journal_fixture()
    receipts = [{"receipt_id": row["receipt_id"], "payload_json": _canonical(row),
                 "received_utc": "2026-10-20T12:00:00Z", "journal_seq": str(i + 1)}
                for i, row in enumerate(fixture.snapshot["receipts"])]
    generations = [{"object_id": row["object_id"], "payload_json": _canonical(row["payload"]),
                    "ack_json": _canonical(row["ack"]), "recorded_utc": row["recorded_utc"],
                    "journal_seq": str(i + len(receipts) + 1)}
                   for i, row in enumerate(fixture.snapshot["generation_records"])]
    wire = Scripted([page(receipts), page(generations), page(), page()])
    journal = NFLCaptureApplicationJournal("https://offline.supabase.invalid", "sb_secret_owned_fixture", transport=wire)
    return journal, fixture


def test_actual_scoped_loader_transfers_graph_without_full_copy_and_isolates_commit_history():
    journal, fixture = port()
    original_copy = copy.deepcopy
    full_copies = []
    def tracked(value, *args, **kwargs):
        if type(value) is dict and "generation_records" in value:
            full_copies.append(value)
        return original_copy(value, *args, **kwargs)
    with patch.object(copy, "deepcopy", side_effect=tracked):
        snapshot = V._snapshot(journal, SCOPE.document())
    assert not full_copies
    assert len(snapshot["generation_records"]) == 2
    assert "generation_records" not in journal._observed_snapshot
    assert journal._observed_snapshot["receipts"] == snapshot["receipts"]
    snapshot["receipts"][0]["features"]["changed"] = True
    snapshot["observations"].append({"untrusted": True})
    assert "changed" not in journal._observed_snapshot["receipts"][0]["features"]
    assert journal._observed_snapshot["observations"] == []


def test_generic_scoped_reader_and_all_public_pinned_reads_keep_defensive_copies():
    fixture = journal_fixture()
    fixture.snapshot["game_scope"] = SCOPE.document()
    fixture.read_observed_game_snapshot = lambda scope: fixture.snapshot
    snapshot = V._snapshot(fixture, SCOPE.document())
    assert snapshot == fixture.snapshot and snapshot is not fixture.snapshot
    snapshot["generation_records"][0]["payload"]["season"] = 2000
    assert fixture.snapshot["generation_records"][0]["payload"]["season"] == 2026
    for pinned in (_PinnedJournal(fixture, fixture.snapshot),
                   _ScopedPinnedJournal(fixture, _OwnedGameSnapshot(fixture.snapshot, SCOPE))):
        public = pinned.read_observed_snapshot()
        public["generation_records"].clear()
        assert len(fixture.snapshot["generation_records"]) == 2


def test_owned_scoped_binder_borrows_exact_graph_without_mutating_or_skipping_hash_checks():
    fixture = journal_fixture()
    snapshot = {**fixture.read_observed_snapshot(), "game_scope": SCOPE.document()}
    pinned = _ScopedPinnedJournal(fixture, _OwnedGameSnapshot(snapshot, SCOPE))
    before = fingerprint(snapshot)
    with patch.object(pinned, "read_observed_snapshot", side_effect=AssertionError("copying public read")):
        watermark, entries = _frozen_entries(pinned, 2026, {6}, SCOPE)
    assert watermark == snapshot["watermark"]
    assert len(entries) == 4
    assert fingerprint(snapshot) == before
    assert all(entry["source"] is next(row["learning_receipt"]
               for generation in snapshot["generation_records"] for row in generation["payload"]["receipts"]
               if row["receipt_id"] == sid) for sid, entry in entries.items())
    snapshot["generation_records"][0]["payload"]["season"] = 2025
    with pytest.raises(ValueError, match="generation_envelope_hash_mismatch"):
        _frozen_entries(pinned, 2026, {6}, SCOPE)


def test_owned_snapshot_tokens_and_scope_are_checked_and_ack_partition_is_revalidated():
    fixture = journal_fixture()
    snapshot = {**fixture.read_observed_snapshot(), "game_scope": SCOPE.document()}
    fixture._read_owned_observed_game_snapshot = lambda scope: snapshot
    with pytest.raises(ValueError, match="invalid_owned_capture_game_snapshot"):
        V._snapshot(fixture, SCOPE.document())
    fixture._read_owned_observed_game_snapshot = lambda scope: _OwnedGameSnapshot(snapshot, V.CaptureGameScope(2026, 6, "other"))
    with pytest.raises(ValueError, match="invalid_owned_capture_game_snapshot"):
        V._snapshot(fixture, SCOPE.document())
    pinned = _ScopedPinnedJournal(fixture, _OwnedGameSnapshot(snapshot, SCOPE))
    with pytest.raises(ValueError, match="capture_game_snapshot_scope_mismatch"):
        _frozen_entries(pinned, 2026, {6}, V.CaptureGameScope(2026, 6, "other"))
    snapshot["generation_records"][0]["ack"]["receipts"].pop()
    with pytest.raises(ValueError, match="generation_ack_partition_mismatch"):
        _frozen_entries(pinned, 2026, {6}, SCOPE)
