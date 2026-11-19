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
from selflearn_core.adapters.nfl_observed_resolution import DNP_BASIS, observation_record, resolve_observed
from selflearn_core.probability_receipts import fingerprint

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures" / "nfl-ingest-m1"
PUBLICATION_TIME = "2026-09-01T14:02:02Z"
FIRST_OUTCOME = "2026-09-02T04:00:00Z"


def observed_revision(receipt, source, *, hit, observed_utc=FIRST_OUTCOME, prior=None, legacy=False):
    """Produce a real bridge proposal for synthetic retained-source identities."""
    played = hit is not None
    origin = "nflverse.stats_player" if played else "nflverse.snap_counts"
    leg = {**source, "source_receipt_id": source["receipt_id"], "model_prob": source["served_prob"],
           "status": "STATUS_FINAL", "played": played, "authoritative": True,
           "result": "hit" if hit else "miss" if played else "void",
           "reason": None if played else "did_not_play",
           "actual": float(source["line"]) + (1 if hit else -1) if played else None,
           "participation_basis": "stat_row" if played else DNP_BASIS,
           "source": origin, "observed_utc": observed_utc,
           "observation_id": fingerprint([origin, observed_utc]),
           "source_row_sha256": fingerprint([source["player_id"], origin, observed_utc]),
           "evidence_quality": "observed_source_snapshot",
           "evidence_capture_id": fingerprint(["capture", observed_utc])}
    if not legacy:
        leg.update(identity_join_policy="stable_player_ids_exact_fixture_v2", identity_crosswalk={
            "source": "nflverse.rosters", "scope": "postgame_identity_only",
            "observation_id": fingerprint(["roster", observed_utc]), "observed_utc": observed_utc,
            "raw_sha256": fingerprint("roster fixture"), "player_id": source["player_id"],
            "gsis_id": "00-1234567", "pfr_id": "TestFi00"})
    game = {"season": source["season"], "week": source["week"], "game_id": source["game_id"],
            "status": "STATUS_FINAL", "kickoff_utc": receipt["event_start_utc"],
            "home": "SEA", "away": "LAR", "home_score": 24, "away_score": 10,
            "source": "espn.scoreboard", "observation_id": fingerprint(["scoreboard", observed_utc]),
            "observed_utc": observed_utc}
    result = resolve_observed([receipt], [source], [leg], {game["game_id"]: game},
                             observed_utc=observed_utc, existing_revisions=[prior] if prior else [],
                             existing_observations=[observation_record(prior["resolution"])] if prior else [])
    assert not result["rejections"], result["rejections"]
    assert len(result["proposals"]) == 1
    return result["proposals"][0]


def seed_publication_journal(journal, n_per=1, *, publish=True, identity_calibrator_markets=(), base_model_version=None):
    requests = [json.loads((FIXTURES / name).read_text()) for name in (
        "source_snapshot_leg_pool.json", "source_snapshot_schedule.json",
        "source_snapshot_parlay_calibration.json")]
    pool, schedule, calibration = [request["payload"] for request in requests]
    if base_model_version is not None:
        pool["base_model_version"] = base_model_version
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
            if base_model_version is not None:
                receipt["base_model_version"] = base_model_version
            if market in identity_calibrator_markets:
                receipt["calibrator"] = {"a": 0, "b": 1}
                receipt["calibrator_version"] = fingerprint(receipt["calibrator"])
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
    sources = {row["receipt_id"]: row["learning_receipt"] for row in rows}
    for receipt in batch["receipts"]:
        sid = receipt["source_receipt_id"]
        journal.append_resolution(**observed_revision(receipt, sources[sid], hit=outcomes[sid]))
    return {"doc":doc, "pool":pool, "receipts":batch["receipts"], "attestation":attestation}
