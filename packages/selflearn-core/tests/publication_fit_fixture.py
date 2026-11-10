"""Real M1 wire-derived publication fixtures; historical clocks are test seams.

No network, environment credentials, source-data writes or receipt upgrades.
The production receipt's feature shape deliberately omits selection/direction.
"""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from selflearn_core.adapters.nfl import adapt_prospective, canonical_key
from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.probability_receipts import fingerprint

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "nfl-ingest-m1"
PUBLICATION_TIME = "2026-09-01T14:02:02Z"


def seed_publication_journal(journal, n_per=1, *, publish=True):
    requests = [json.loads((FIXTURES / name).read_text()) for name in (
        "source_snapshot_leg_pool.json", "source_snapshot_schedule.json",
        "source_snapshot_parlay_calibration.json")]
    pool, schedule, calibration = [request["payload"] for request in requests]
    doc = json.loads((FIXTURES / "generation_envelope.json").read_text())["payload"]
    template_player = pool["players"][0]
    template_row = doc["receipts"][0]
    players, rows, outcomes = [], [], {}
    for market, position in (("qb_pass_yds", "QB"), ("rb_rush_yds", "RB"), ("wr_rec_yds", "WR")):
        for index in range(n_per):
            player = copy.deepcopy(template_player)
            player.update(gsis_id=f"fixture-{market}-{index}", market=market, position=position)
            rung = player["rungs"][0]
            player["rungs"] = [rung]
            receipt = rung["learning_receipt"]
            receipt.update(player_id=player["gsis_id"], market=market)
            receipt["features"]["market"] = market
            receipt["features"].pop("selection", None)
            receipt["features"].pop("direction", None)
            receipt["contract_id"] = fingerprint(list(canonical_key(receipt, 2026)))
            receipt["receipt_id"] = fingerprint({k:v for k,v in receipt.items() if k != "receipt_id"})
            leg = {**template_row["leg"], "gsis_id":player["gsis_id"], "market":market}
            if "position" in leg:
                leg["position"] = position
            row = {**copy.deepcopy(template_row), "receipt_id":receipt["receipt_id"],
                   "learning_receipt":copy.deepcopy(receipt), "leg":leg}
            players.append(player);rows.append(row)
            outcomes[receipt["receipt_id"]] = (index % 10) < 4
    pool["players"] = players
    gen = doc["generations"][0]
    gen["offered_cards"] = []
    gen["source_snapshots"]["leg_pool"] = fingerprint(pool)
    gen["receipt_ids"] = sorted(row["receipt_id"] for row in rows)
    gen["generation_id"] = fingerprint({k:gen[k] for k in MANIFEST_KEYS})
    for row in rows:
        row["generation_id"] = gen["generation_id"]
    doc["receipts"] = sorted(rows, key=lambda row:row["receipt_id"])
    doc["envelope_id"] = fingerprint({k:v for k,v in doc.items() if k != "envelope_id"})
    for value in (pool, schedule, calibration):
        journal.append_object("source_snapshot", fingerprint(value), value)
    journal.append_object("generation", doc["envelope_id"], doc)
    batch = adapt_prospective(journal_docs=[doc])
    assert not batch["rejections"], batch["rejections"]
    journal.commit_generation(doc["envelope_id"], batch["receipts"], [])
    attestation = None
    if publish:
        observation = {"version":1, "policy":{"version":1, "kind":"nfl.pool_http_publication.v1",
            "origin":"https://publication-fixture.example", "path":"/data/leg_pool.json"},
            "observed_started_utc":"2026-09-01T14:02:00Z",
            "observed_completed_utc":"2026-09-01T14:02:01Z",
            "http_status":200, "body_utf8":json.dumps(pool)}
        # This is the explicitly trusted historical test clock seam, never a
        # caller-supplied clock on the production publication API.
        with patch.object(journal, "_publication_recorded_utc", return_value=PUBLICATION_TIME):
            attestation = journal.commit_publication_attestation(doc["envelope_id"], observation)
    for receipt in batch["receipts"]:
        journal.append_resolution({"receipt_id":receipt["receipt_id"],
            "contract_id":receipt["contract_id"], "event_id":receipt["event_id"],
            "status":"FINAL", "hit":outcomes[receipt["source_receipt_id"]],
            "resolved_utc":"2026-09-02T04:00:00Z", "source":"synthetic official fixture"},
            revision_id="revision-" + receipt["receipt_id"])
    return {"doc":doc, "pool":pool, "receipts":batch["receipts"], "attestation":attestation}
