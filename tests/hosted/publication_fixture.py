"""Synthetic future-event M1 graph, sealed from the committed conformance fixture.

This is not historical publication evidence and does not modify shipped inputs.
No NFL checkout, network, database-clock override or skipped acceptance needed.
"""
import json
from pathlib import Path

from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.probability_receipts import fingerprint

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures/nfl-ingest-m1"


def graph(kickoff="2099-09-02T00:15:00Z"):
    def read(name):
        return json.loads((FIXTURE / name).read_text())
    sources = {name: read("source_snapshot_" + name + ".json")["payload"]
               for name in ("leg_pool", "schedule", "parlay_calibration")}
    for game in sources["schedule"]["games"]:
        game["kickoff_utc"] = kickoff
    doc = read("generation_envelope.json")["payload"]
    generation = doc["generations"][0]
    generation["source_snapshots"] = {name: fingerprint(value) for name, value in sources.items()}
    generation["generation_id"] = fingerprint({k: generation[k] for k in MANIFEST_KEYS})
    for row in doc["receipts"]:
        row["generation_id"] = generation["generation_id"]
        row["kickoff_utc"] = kickoff
    doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    return doc, sources
