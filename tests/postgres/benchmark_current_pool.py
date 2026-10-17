"""Read-only current NFL producer → real PostgreSQL journal volume check.

No model refit, FINAL outcome, source-file mutation, HTTP listener, installation
or cloud access occurs. The in-memory PostgreSQL bridge disappears on exit.
This is a local latency observation, not a hosted Supabase performance claim.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_calibration import build_cohort
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from test_adapter_integration import PostgresBridge


class TimedTransport:
    def __init__(self, bridge):
        self.bridge = bridge
        self.samples = []

    def request(self, method, path, *, data):
        start = time.monotonic()
        response = None
        try:
            response = self.bridge.request(method, path, data=data)
            return response
        finally:
            self.samples.append({
                "rpc": path.rsplit("/", 1)[-1],
                "view": data.get("p_view"),
                "seconds": round(time.monotonic() - start, 6),
                "request_bytes": len(json.dumps(data, allow_nan=False).encode()),
                "response_bytes": (len(json.dumps(response, allow_nan=False).encode())
                                   if response is not None else None),
            })


def main():
    checkout = Path(os.environ.get("NFL2026_CHECKOUT", "")).resolve()
    if not (checkout / "scripts/learning_journal.py").is_file():
        raise RuntimeError("NFL2026_CHECKOUT must identify the real isolated NFL producer checkout")
    sys.path.insert(0, str(checkout))
    from scripts import build_leg_pool, build_my_cards as cards, learning_journal, learning_receipts

    data = checkout / "data"
    paths = {data / name for name in (
        "leg_pool.json", "leg_pool_backtest.json", "player_weekly.json", "player_projections.json",
        "game_predictions.json", "parlays.json", "schedule_full.json", "parlay_backtest.json")}
    paths.update(Path(getattr(build_leg_pool, name)) for name in ("ATD_WEEK", "ATD_BACKTEST"))
    before = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths if path.is_file()}
    frozen = json.loads((data / "leg_pool.json").read_text())["generated_utc"]
    created = learning_receipts.instant(frozen)
    assert created is not None and created <= datetime.now(timezone.utc)
    captured = (created + timedelta(seconds=1)).isoformat()
    inputs = build_leg_pool.load_inputs()
    inputs["generated_utc"] = frozen
    pool = build_leg_pool.build(inputs)
    schedule = json.loads(Path(cards.SCHEDULE_PATH).read_text())
    calibration = json.loads(Path(cards.CALIBRATION_PATH).read_text())
    offered, _ = cards.offered_cards(pool, schedule["games"], cards._correlation_table(calibration), frozen)
    kickoffs = cards.kickoff_index(schedule["games"])
    records = [cards.card_record(dial, seed, rank, card, kickoffs) for dial, seed, rank, card in offered]
    document, objects = learning_journal.capture(pool, schedule, calibration, records, captured)
    adapted = adapt_prospective(journal_docs=[document])
    assert document["receipts"] and not adapted["rejections"], adapted["summary"]
    assert len(adapted["receipts"]) == len(document["receipts"])
    bridge = PostgresBridge()
    transport = TimedTransport(bridge)
    journal = SupabaseReceiptJournal("https://offline.supabase.invalid", "sb_secret_offline_fixture",
                                    transport=transport)
    try:
        for object_id, source in objects.items():
            assert journal.append_object("source_snapshot", object_id, source) is True
        assert journal.append_object("generation", document["envelope_id"], document) is True
        ack = journal.commit_generation(document["envelope_id"], adapted["receipts"], adapted["rejections"])
        assert ack["durable"] is True and len(ack["receipts"]) == len(document["receipts"])
        assert all(row["status"] == "recorded" for row in ack["receipts"])
        assert journal.generation_ack(document["envelope_id"]) == ack
        repeated = journal.commit_generation(document["envelope_id"], adapted["receipts"], adapted["rejections"])
        assert all(row["status"] == "duplicate" for row in repeated["receipts"])
        snapshot = journal.read_snapshot()
        assert len(snapshot["receipts"]) == len(document["receipts"])
        assert len(snapshot["generation_records"]) == 1 and snapshot["revisions"] == []
        cohort = build_cohort(snapshot["receipts"], [row["learning_receipt"] for row in document["receipts"]],
            snapshot["revisions"], as_of_utc=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            population="offered_my", generation_records=snapshot["generation_records"])
        offered_sources = {source for card in document["generations"][0]["offered_cards"]
                           for source in card["receipt_ids"]}
        expected_contracts = {row["learning_receipt"]["contract_id"] for row in document["receipts"]
                              if row["receipt_id"] in offered_sources}
        assert expected_contracts and {row["contract_id"] for row in cohort["forecasts"]} == expected_contracts
        assert cohort["n"] == 0 and cohort["membership_policy"] == "committed_generations_v1"
        after = {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in before}
        assert before == after, "Read-only benchmark changed NFL source files"
        slow = [sample for sample in transport.samples if sample["seconds"] >= 12]
        oversized = [sample for sample in transport.samples
                     if sample["response_bytes"] is not None and sample["response_bytes"] > 8 * 1024 * 1024]
        report = {
            "version": 1,
            "ok": not slow and not oversized,
            "engine": bridge.ready,
            "scope": "local single-connection PostgreSQL WASM; not hosted/native concurrency proof",
            "frozen_generation_utc": frozen,
            "source_objects": len(objects),
            "captured_receipts": len(document["receipts"]),
            "offered_contracts": len(expected_contracts),
            "generation_id": document["envelope_id"],
            "evaluated_outcomes": cohort["n"],
            "source_files_unchanged": True,
            "live_http_timeout_seconds": 12,
            "rpc_samples": transport.samples,
            "rpc_samples_over_live_timeout": slow,
            "rpc_samples_over_live_response_cap": oversized,
        }
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if report["ok"] else 1
    finally:
        bridge.close()


if __name__ == "__main__":
    raise SystemExit(main())
