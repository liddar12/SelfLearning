"""Opt-in, reproducible current-input volume acceptance across both repositories.

This rebuilds only the existing deterministic producer/selector in memory. No
model is fit, no FINAL outcomes are consumed, and all generated artifacts live
under pytest's temporary directory. A frozen producer timestamp avoids a test
changing its forecast population merely because wall-clock kickoff has passed.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import pytest

from selflearn_core.adapters.nfl_calibration import build_cohort
from selflearn_core.store.receipt_journal import ReceiptJournal
from .nfl_fixture import producer
from .test_nfl_ingestion import INGEST_KEY, client_for


def test_current_frozen_nfl_inputs_export_and_persist_at_real_volume(tmp_path, monkeypatch):
    journal, lr, cards, exporter = producer(monkeypatch)
    from scripts import build_leg_pool
    checkout = Path(os.environ["NFL2026_CHECKOUT"]).resolve()
    data_dir = checkout / "data"
    # These are the producer's actual input files, not reduced hand-built rows.
    source_paths = {data_dir / name for name in (
        "leg_pool.json", "leg_pool_backtest.json", "player_weekly.json", "player_projections.json",
        "game_predictions.json", "parlays.json", "schedule_full.json", "parlay_backtest.json")}
    source_paths.update(Path(getattr(build_leg_pool, name)) for name in ("ATD_WEEK", "ATD_BACKTEST"))
    before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in source_paths if path.is_file()}
    frozen_pool = json.loads((data_dir / "leg_pool.json").read_text())
    frozen_time = frozen_pool.get("generated_utc")
    created = lr.instant(frozen_time)
    assert created is not None, "Current pool must identify its frozen generation time"
    captured = (created + timedelta(seconds=1)).isoformat()
    # Production source dates must be actual historical observations, not a
    # test-defined clock that would make future snapshots appear available now.
    assert created <= datetime.now(timezone.utc), "Current frozen pool is from the future; ingestion is not verifiable"
    inputs = build_leg_pool.load_inputs()
    inputs["generated_utc"] = frozen_time
    pool = build_leg_pool.build(inputs)
    schedule = json.loads(Path(cards.SCHEDULE_PATH).read_text())
    calibration = json.loads(Path(cards.CALIBRATION_PATH).read_text())
    offered, _ = cards.offered_cards(pool, schedule["games"], cards._correlation_table(calibration), frozen_time)
    kickoffs = cards.kickoff_index(schedule["games"])
    records = [cards.card_record(dial, seed, rank, card, kickoffs) for dial, seed, rank, card in offered]
    assert records, "Current frozen inputs provide no offered pre-kickoff MY population to verify"
    snapshots = tmp_path / "source-snapshots"
    doc, added = journal.record(pool, schedule, calibration, records, captured, str(snapshots))
    assert added and doc["receipts"], "Current frozen inputs provide no eligible forecast receipts"
    try:
        plan = exporter.plan(snapshots)
    except exporter.ExportError as error:
        # Fail explicitly if real volume is unsupported. Never silently trim
        # candidates, replace real inputs with a tiny fixture, or claim a pass.
        pytest.fail("Current frozen-input export is unsupported: " + str(error))
    requests = [row for row in plan["source_objects"]]
    requests += [entry["request"] for entry in plan["generations"]]
    assert all(len(exporter.encoded(request)) <= exporter.MAX_BODY_BYTES for request in requests)
    path = tmp_path / "durable.sqlite"
    transmitted = []
    with client_for(path) as client:
        def transport(route, body):
            assert len(body) <= exporter.MAX_BODY_BYTES
            transmitted.append(route)
            response = client.post(route, content=body, headers={
                "Authorization": "Bearer " + INGEST_KEY, "Content-Type": "application/json"})
            return response.status_code, response.content
        result = exporter.send(plan, "https://local-current-pool-acceptance.invalid", tmp_path / "delivery",
                               INGEST_KEY, transport=transport, sleep=lambda _: None)
    assert result["ok"] is True, result
    assert result["receipts_rejected"] == 0
    assert result["receipts_acked"] == len(doc["receipts"])
    assert transmitted.count("/api/v1/nfl/generations") == len(plan["generations"])
    with ReceiptJournal(path) as db:
        receipts, generations = db.receipts(), db.generations()
        assert len(receipts) == len(doc["receipts"])
        assert len(generations) == 1
        assert generations[0]["ack"]["rejections"] == []
        assert {row["source_receipt_id"] for row in generations[0]["ack"]["receipts"]} == {
            row["receipt_id"] for row in doc["receipts"]}
        cohort = build_cohort(receipts, [row["learning_receipt"] for row in doc["receipts"]], [],
            as_of_utc=(datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat(),
            population="offered_my", generation_records=generations)
    offered_sources = {sid for card in doc["generations"][0]["offered_cards"] for sid in card["receipt_ids"]}
    offered_contracts = {row["learning_receipt"]["contract_id"] for row in doc["receipts"]
                         if row["receipt_id"] in offered_sources}
    assert offered_contracts
    assert {row["contract_id"] for row in cohort["forecasts"]} == offered_contracts
    assert cohort["membership_policy"] == "committed_generations_v1"
    assert cohort["n"] == 0 and cohort["rows"] == []
    assert cohort["excluded"]["unresolved_at_cutoff"] == len(offered_contracts)
    assert {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before} == before
