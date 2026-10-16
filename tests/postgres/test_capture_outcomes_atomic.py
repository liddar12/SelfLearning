"""Actual NFL raw bytes → independent binder/job → PostgreSQL-WASM journal.

Chunk persistence is an explicitly FAKE private in-memory test adapter. Journal
DDL/RPC execution is actual PostgreSQL-WASM, not native concurrency, hosted
PostgREST, provider authentication or a durable delivery/group ACK. Explicit
missing local dependencies fail rather than becoming skipped acceptance.
"""
from __future__ import annotations

import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_capture_job import verify_and_commit_capture
from selflearn_core.adapters.nfl_observed_journal import NFLObservedJournal
from selflearn_core.evidence_chunks import chunk_stream, encode_chunk_request, verify_chunk
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from test_adapter_integration import PostgresBridge

DELIVERED = "2026-10-10T10:00:00Z"


class FakePrivateChunks:
    """Contract fixture only; NOT Supabase Storage or durable chunk evidence."""
    def __init__(self, raw_objects):
        self.chunks, self.pointers, self.calls = {}, {}, []
        for raw_id, raw in raw_objects.items():
            def stage(dto):
                # Exercise the approved public-sized canonical chunk DTO, but
                # deliberately don't pretend this dictionary is durable.
                assert len(encode_chunk_request(dto)) <= 3 * 1024 * 1024
                known = self.chunks.setdefault(dto["chunk_id"], copy.deepcopy(dto))
                assert known == dto
            pointer = chunk_stream(io.BytesIO(raw), stage)
            assert pointer["object_id"] == raw_id
            self.pointers[raw_id] = pointer

    def read(self, raw_id):
        self.calls.append(raw_id)
        pointer = copy.deepcopy(self.pointers[raw_id])
        for ref in pointer["chunks"]:
            dto = copy.deepcopy(self.chunks[ref["chunk_id"]])
            raw = verify_chunk(dto)
            assert dto["chunk_id"] == ref["chunk_id"] and len(raw) == ref["raw_bytes"]
            yield raw


@pytest.fixture(scope="module")
def raw_producer():
    checkout = os.environ.get("NFL2026_CHECKOUT")
    if not checkout or not (Path(checkout) / "tests/fixtures/week6_outcomes.py").is_file():
        raise RuntimeError("Set NFL2026_CHECKOUT to the reviewed local real NFL fixture checkout")
    # No producer-normalized outcome is returned or accepted. This calls only
    # the actual original forecast producer and raw capture fixture helpers.
    script = r'''
import base64, copy, importlib.util, json, tempfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory)
 f.generation(root)
 f.generation(root,'2026-09-01T13:00:00Z')
 initial=copy.deepcopy(f.plan(root))
 extra,_,_=f.generation(root,'2026-09-01T14:00:00Z')
 complete=f.plan(root)
 captures=[
  f.bundle(value='80',observed='2026-09-03T12:00:00Z'),
  f.bundle(value='80',observed='2026-09-04T12:00:00Z'),
  f.bundle(value='30',observed='2026-09-05T12:00:00Z'),
  f.bundle(value='80',observed='2026-09-06T12:00:00Z'),
  f.bundle(stats_rows=[f.stats_row('5',player_display_name='Other Receiver')],
    snap_rows=[f.snap_row(player='Other Receiver')],observed='2026-09-07T12:00:00Z')]
 out=[]
 for bundle in captures:
  out.append({'manifest':bundle['manifest'],'observations':bundle['observations'],
    'raw':{key:base64.b64encode(raw).decode() for key,raw in bundle['raw_objects'].items()}})
 print(json.dumps({'initial':initial,'complete':complete,'extra':extra,'captures':out}))
'''
    run = subprocess.run([sys.executable, "-c", script], cwd=checkout, capture_output=True, text=True, timeout=30)
    if run.returncode:
        raise RuntimeError("Actual local NFL raw capture/forecast fixture did not complete")
    return json.loads(run.stdout)


def port(bridge):
    return NFLObservedJournal("https://offline.supabase.invalid", "sb_secret_offline_raw_capture_fixture",
                              transport=bridge, page_size=2)


def admit(journal, source_objects, documents):
    existing = journal.receipts()
    batch = adapt_prospective(journal_docs=documents, existing_receipts=existing)
    assert batch["rejections"] == []
    for obj in source_objects:
        journal.append_object(obj["kind"], obj["object_id"], obj["payload"])
    for doc in documents:
        journal.append_object("generation", doc["envelope_id"], doc)
        source_ids = {row["receipt_id"] for row in doc["receipts"]}
        result = journal.commit_generation(doc["envelope_id"],
            [row for row in batch["receipts"] if row["source_receipt_id"] in source_ids], [])
        assert result["durable"] is True


@pytest.fixture
def journals(monkeypatch, raw_producer):
    monkeypatch.setenv("SELFLEARN_OBSERVED_JOURNAL_TEST", "1")
    bridge = PostgresBridge()
    try:
        journal, other = port(bridge), port(bridge)
        plan = raw_producer["initial"]
        documents = [item["request"]["payload"] for item in plan["generations"]]
        admit(journal, plan["source_objects"], documents)
        assert len(journal.receipts()) == 4
        yield journal, other, bridge
    finally:
        bridge.close()


def stage(capture):
    objects = {key: base64.b64decode(value, validate=True) for key, value in capture["raw"].items()}
    envelope = {"version": 1, "kind": "nfl.capture_envelope.v1", "scope": "uploaded_only",
                "manifest": copy.deepcopy(capture["manifest"]), "observations": copy.deepcopy(capture["observations"]),
                "raw_objects": [{"raw_sha256": key, "raw_bytes": len(raw)} for key, raw in sorted(objects.items())]}
    return envelope, FakePrivateChunks(objects)


def deliver(journal, capture):
    envelope, storage = stage(capture)
    return verify_and_commit_capture(journal, envelope, storage.read, clock=lambda: DELIVERED), storage


def test_actual_raw_capture_cycles_commit_both_streams_and_preserve_original_forecasts(journals, raw_producer):
    journal, _, _ = journals
    receipts = copy.deepcopy(journal.receipts())
    initial, unchanged, correction, restored, void = raw_producer["captures"]
    first, storage = deliver(journal, initial)
    assert first["status"] == "observations_persisted"
    assert first["durable_ack"] is False and first["source_authentication"] == "not_performed"
    assert first["persistence"]["commit"]["added_observations"] == first["persistence"]["commit"]["added_revisions"] == 4
    assert len(first["staged"]["groups"]) == 1 and len(first["staged"]["groups"][0]["receipt_ids"]) == 4
    assert len(storage.calls) == 5, "scoreboard once plus stats/snaps independently rehashed"
    original = journal.read_observed_snapshot()
    pinned = original["watermark"]
    stable, _ = deliver(journal, unchanged)
    assert stable["persistence"]["commit"]["added_observations"] == 4
    assert stable["persistence"]["commit"]["added_revisions"] == 0
    assert journal.read_observed_snapshot()["revisions"] == original["revisions"], "confirmation cannot restamp outcome history"
    changed, _ = deliver(journal, correction)
    assert changed["persistence"]["commit"]["added_observations"] == changed["persistence"]["commit"]["added_revisions"] == 4
    assert all(row["hit"] is False for row in journal.latest_resolutions())
    retried, _ = deliver(journal, initial)
    assert retried["persistence"]["commit"]["added_observations"] == retried["persistence"]["commit"]["added_revisions"] == 0
    assert all(row["hit"] is False for row in journal.latest_resolutions()), "old raw capture retry must not revert corrected head"
    restored_result, _ = deliver(journal, restored)
    assert restored_result["persistence"]["commit"]["added_revisions"] == 4
    assert all(row["hit"] is True for row in journal.latest_resolutions())
    void_result, _ = deliver(journal, void)
    assert void_result["persistence"]["commit"]["added_revisions"] == 4
    assert all(row["status"] == "VOID" and row["hit"] is None for row in journal.latest_resolutions())
    assert score_binary_receipts(receipts, journal.latest_resolutions())["n"] == 0
    final = journal.read_observed_snapshot()
    assert final["receipts"] == receipts
    assert len(final["observations"]) == len(final["observation_records"]) == 20
    assert len(final["revisions"]) == 16
    historical = journal._load_observed_snapshot(pinned)
    assert historical["receipts"] == original["receipts"]
    assert historical["observations"] == original["observations"]
    assert historical["revisions"] == original["revisions"]


def test_true_global_cas_conflict_rebinds_new_accepted_vintage_before_any_ack(journals, raw_producer):
    journal, competing, _ = journals
    envelope, storage = stage(raw_producer["captures"][0])
    before = journal.read_observed_snapshot()
    original_commit, attempts = journal.commit_observed, []

    def race(observations, proposals, *, watermark):
        attempts.append({"watermark": watermark, "observation_count": len(observations),
                         "receipt_ids": sorted(row["resolution"]["receipt_id"] for row in proposals)})
        if len(attempts) == 1:
            # Separate adapter, same actual PostgreSQL authority. This changes
            # real accepted membership, not a fabricated JournalConflict.
            admit(competing, raw_producer["complete"]["source_objects"], [raw_producer["extra"]])
        return original_commit(observations, proposals, watermark=watermark)

    journal.commit_observed = race
    result = verify_and_commit_capture(journal, envelope, storage.read, clock=lambda: DELIVERED)
    assert result["attempts"] == 2 and result["status"] == "observations_persisted"
    assert result["durable_ack"] is False
    assert [attempt["observation_count"] for attempt in attempts] == [4, 6]
    assert int(attempts[1]["watermark"]) > int(attempts[0]["watermark"])
    assert result["staged"]["membership_watermark"] == attempts[1]["watermark"]
    assert len(result["staged"]["groups"][0]["receipt_ids"]) == 6
    assert result["persistence"]["commit"]["added_observations"] == result["persistence"]["commit"]["added_revisions"] == 6
    assert len(storage.calls) == 10, "CAS retry must perform fresh raw integrity AND raw-label rereads"
    current = journal.read_observed_snapshot()
    assert len(current["receipts"]) == len(current["observations"]) == len(current["revisions"]) == 6
    assert sorted(row["receipt_id"] for row in current["receipts"]) == attempts[1]["receipt_ids"]
    pinned = journal._load_observed_snapshot(before["watermark"])
    assert len(pinned["receipts"]) == 4 and pinned["observations"] == [] and pinned["revisions"] == []


def test_tampered_staged_chunk_never_reaches_an_observation_commit(journals, raw_producer):
    journal, _, bridge = journals
    envelope, storage = stage(raw_producer["captures"][0])
    key = next(iter(storage.chunks))
    storage.chunks[key]["data"] = "x" + storage.chunks[key]["data"][1:]
    before = journal.read_observed_snapshot()
    calls = len(bridge.calls)
    with pytest.raises(ValueError):
        verify_and_commit_capture(journal, envelope, storage.read, clock=lambda: DELIVERED)
    assert not any(call["path"].endswith("selflearn_journal_commit_observed_v1") for call in bridge.calls[calls:])
    after = journal.read_observed_snapshot()
    assert after["watermark"] == before["watermark"]
    assert after["observations"] == after["revisions"] == []
