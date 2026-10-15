"""Use the real NFL producer and selector, never fabricated durable ACKs."""
import math
import os
from pathlib import Path

import pytest


def producer(monkeypatch):
    checkout = os.environ.get("NFL2026_CHECKOUT")
    if not checkout or not (Path(checkout) / "scripts/learning_journal.py").is_file():
        pytest.skip("Set NFL2026_CHECKOUT to run the real cross-repository producer")
    monkeypatch.syspath_prepend(checkout)
    from scripts import learning_journal, learning_receipts, build_my_cards, export_learning
    return learning_journal, learning_receipts, build_my_cards, export_learning


def inputs(monkeypatch):
    journal, lr, cards, exporter = producer(monkeypatch)
    created = "2026-09-01T14:00:00Z"
    recorded = "2026-09-01T14:01:00Z"
    kickoff = "2026-09-02T00:15:00Z"
    pool = cards._fixture_pool(created)
    pool.update(week=6, base_model_version="fixture-base")
    for player in pool["players"]:
        for rung in player["rungs"]:
            z = (player["mu"] - rung["line"]) / 30
            coefficients = {"a": math.log(rung["model_prob"] / (1-rung["model_prob"])), "b": 0, "c": 0}
            features = {"market": player["market"], "line": rung["line"], "mu": player["mu"],
                        "sd": 30, "z": z, "p_team": player["p_team"], "calibration": coefficients,
                        "correlation_tags": ["game:" + player["game_id"]]}
            context = {"season": 2026, "week": 6, "base_model_version": "fixture-base",
                       "forecast_created_utc": created, "source_snapshot_id": lr.fingerprint({"created": created}),
                       "source_timestamps": {"fixture": created}, "generation_sha": "fixture"}
            base = lr.base_probability(coefficients, z, player["p_team"])
            receipt = lr.make_receipt({**player, **rung}, base, None, context, features=features)
            rung.update(model_prob=receipt["served_prob"], learning_receipt=receipt)
    schedule = {"season": 2026, "games": [{"game_id": gid, "week": 6, "status": "STATUS_SCHEDULED",
                 "kickoff_utc": kickoff} for gid in ("G1", "G2")]}
    offered, _ = cards.offered_cards(pool, schedule["games"], None, created)
    records = [cards.card_record(d, s, r, c, cards.kickoff_index(schedule["games"]))
               for d, s, r, c in offered]
    return journal, lr, exporter, pool, schedule, records, recorded
