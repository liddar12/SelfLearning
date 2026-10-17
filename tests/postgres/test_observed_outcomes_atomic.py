"""Real frozen NFL producer → indexed observations and atomic PostgreSQL port.

Local synthetic evidence only. PostgreSQL WASM is not native concurrency or
hosted PostgREST. No fixture-owned observation-ID index supplies these reads.
"""
import copy

import pytest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_job import resolve_and_commit_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.adapters.nfl_observed_journal import NFLObservedJournal
from test_adapter_integration import PostgresBridge
from test_observed_outcomes import DELIVERED, producer  # Actual NFL fixture, not copied output.


@pytest.fixture
def journal(monkeypatch, producer):
    monkeypatch.setenv("SELFLEARN_OBSERVED_JOURNAL_TEST", "1")
    bridge = PostgresBridge()
    try:
        store = NFLObservedJournal("https://offline.supabase.invalid", "sb_secret_offline_fixture",
                                       transport=bridge, page_size=2)
        batch = adapt_prospective(journal_docs=producer["docs"])
        assert not batch["rejections"] and len(batch["receipts"]) == 4
        for obj in producer["plan"]["source_objects"]:
            store.append_object(obj["kind"], obj["object_id"], obj["payload"])
        for doc in producer["docs"]:
            store.append_object("generation", doc["envelope_id"], doc)
            ids = {row["receipt_id"] for row in doc["receipts"]}
            store.commit_generation(doc["envelope_id"],
                [r for r in batch["receipts"] if r["source_receipt_id"] in ids], [])
        yield store
    finally:
        bridge.close()


def deliver(journal, document):
    assert document["rejections"] == []
    return resolve_and_commit_observed(journal, document["source_receipts"], document["observed_legs"],
                                      document["game_observations"], clock=lambda: DELIVERED)


def test_actual_producer_atomic_cycles_enumerate_both_streams_and_pin_history(journal, producer):
    receipts = copy.deepcopy(journal.receipts())
    initial, unchanged, correction, restored, void = producer["observations"]
    first = deliver(journal, initial)
    assert first["durable"] is True
    assert first["commit"]["added_observations"] == first["commit"]["added_revisions"] == 4
    original = journal.read_observed_snapshot()
    assert len(original["observations"]) == len(original["observation_records"]) == 4
    pinned = original["watermark"]
    stable = deliver(journal, unchanged)
    assert stable["commit"]["added_observations"] == 4
    assert stable["commit"]["added_revisions"] == 0
    current = journal.read_observed_snapshot()
    assert current["revisions"] == original["revisions"], "confirmation cannot restamp outcome time"
    assert len(current["observations"]) == 8
    changed = deliver(journal, correction)
    assert changed["commit"]["added_observations"] == changed["commit"]["added_revisions"] == 4
    assert all(row["hit"] is False for row in journal.latest_resolutions())
    retried = deliver(journal, initial)
    assert retried["commit"]["added_observations"] == retried["commit"]["added_revisions"] == 0
    assert all(row["hit"] is False for row in journal.latest_resolutions())
    assert deliver(journal, restored)["commit"]["added_revisions"] == 4
    assert all(row["hit"] is True for row in journal.latest_resolutions())
    assert deliver(journal, void)["commit"]["added_revisions"] == 4
    latest = journal.latest_resolutions()
    assert all(row["status"] == "VOID" and row["hit"] is None for row in latest)
    assert score_binary_receipts(receipts, latest)["n"] == 0
    final = journal.read_observed_snapshot()
    assert final["receipts"] == receipts
    assert len(final["observations"]) == len(final["observation_records"]) == 20
    assert len(final["revisions"]) == 16
    assert all(record["payload"]["observation_id"] == record["observation_id"]
               and record["recorded_utc"] for record in final["observation_records"])
    historical = journal._load_observed_snapshot(pinned)
    assert historical["observations"] == original["observations"]
    assert historical["revisions"] == original["revisions"]


def test_actual_cas_conflict_rereads_complete_index_before_retry(journal, producer):
    original_commit, calls = journal.commit_observed, []

    def concurrent_writer(observations, proposals, *, watermark):
        calls.append(watermark)
        if len(calls) == 1:
            body = {"synthetic": "concurrent unrelated immutable source"}
            journal.append_object("source_snapshot", fingerprint(body), body)
        return original_commit(observations, proposals, watermark=watermark)

    journal.commit_observed = concurrent_writer
    result = deliver(journal, producer["observations"][0])
    assert result["durable"] is True and result["attempts"] == 2
    assert len(calls) == 2 and int(calls[1]) > int(calls[0])
    snap = journal.read_observed_snapshot()
    assert len(snap["observations"]) == len(snap["revisions"]) == 4
