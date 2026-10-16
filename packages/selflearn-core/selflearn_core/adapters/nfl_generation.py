"""Pure frozen-source generation joins shared by file and HTTP intake.

The caller supplies a trusted object getter and observation clock. No resource
is opened here. Producer policy hashes identify content, not source authority,
executed code, calibrated joint probabilities or public publication.
"""
import re

from ..probability_receipts import fingerprint, instant

HASH=re.compile(r"sha256:[0-9a-f]{64}\Z")
SOURCE_NAMES={"leg_pool","schedule","parlay_calibration"}
MANIFEST_KEYS=("version","season","week","pool_generated_utc","source_snapshots","offered_cards",
               "selection_policy_version","joint_model_version")
LEG_KEYS={"game_id","gsis_id","market","line","side","direction","comparison_operator","model_prob","selection","position"}


def validate_generation(journal,object_id,*,now_utc):
    """Join every claimed receipt to the actual pool and scheduled game."""
    obj=journal.get_object(object_id)
    if obj is None or obj["kind"]!="generation":raise ValueError("generation_object_missing")
    doc=obj["payload"]
    if (type(doc) is not dict or type(doc.get("version")) is not int or doc["version"]!=1
        or type(doc.get("generations")) is not list or len(doc["generations"])!=1
        or type(doc.get("receipts")) is not list):raise ValueError("invalid_generation_document")
    if doc.get("envelope_id")!=object_id or fingerprint({k:v for k,v in doc.items()if k!="envelope_id"})!=object_id:
        raise ValueError("generation_object_hash_mismatch")
    gen=doc["generations"][0]
    if type(gen) is not dict or any(k not in gen for k in MANIFEST_KEYS):raise ValueError("invalid_generation_manifest")
    if gen.get("generation_id")!=fingerprint({k:gen[k]for k in MANIFEST_KEYS}):raise ValueError("generation_manifest_hash_mismatch")
    if (type(gen["version"]) is not int or gen["version"]!=1 or type(doc.get("season")) is not int
        or type(doc.get("week")) is not int or not 1<=doc["week"]<=18
        or type(gen["season"]) is not int or type(gen["week"]) is not int
        or gen["season"]!=doc["season"]or gen["week"]!=doc["week"]):raise ValueError("generation_period_mismatch")
    if any(type(gen[k]) is not str or not HASH.fullmatch(gen[k])for k in("selection_policy_version","joint_model_version")):
        raise ValueError("missing_policy_version")
    if gen.get("publication_status")!="generated_unverified"or gen.get("population")!="eligible_pool_prop_rungs_before_display_dedupe":
        raise ValueError("unsupported_generation_claim")
    recorded=instant(gen.get("recorded_utc"))
    if recorded>instant(now_utc):raise ValueError("future_generation_observation")
    if type(gen["source_snapshots"]) is not dict or set(gen["source_snapshots"])!=SOURCE_NAMES:raise ValueError("invalid_source_manifest")
    payloads={}
    for name,identity in gen["source_snapshots"].items():
        if type(identity) is not str or not HASH.fullmatch(identity):raise ValueError("invalid_source_object_id")
        value=journal.get_object(identity)
        if value is None or value["kind"]!="source_snapshot":raise ValueError("source_object_missing")
        if fingerprint(value["payload"])!=identity:raise ValueError("source_object_hash_mismatch")
        payloads[name]=value["payload"]
    pool,schedule,calibration=(payloads[k]for k in("leg_pool","schedule","parlay_calibration"))
    if (type(pool) is not dict or type(pool.get("players")) is not list or type(schedule) is not dict
        or type(schedule.get("games")) is not list or calibration is not None and type(calibration) is not dict):
        raise ValueError("invalid_source_payload")
    if (pool.get("season")!=doc["season"]or pool.get("week")!=doc["week"]
        or schedule.get("season",doc["season"])!=doc["season"]or pool.get("generated_utc")!=gen["pool_generated_utc"]
        or instant(pool["generated_utc"])>recorded):raise ValueError("source_generation_mismatch")
    candidates={}
    for player in pool["players"]:
        if type(player) is not dict or type(player.get("rungs")) is not list:continue
        for rung in player["rungs"]:
            if type(rung) is not dict or type(rung.get("learning_receipt")) is not dict:continue
            sid=rung["learning_receipt"].get("receipt_id")
            if type(sid) is not str:continue
            value={"leg":{k:v for k,v in{**player,**rung}.items()if k in LEG_KEYS},"learning_receipt":rung["learning_receipt"]}
            if sid in candidates and fingerprint(candidates[sid])!=fingerprint(value):raise ValueError("conflicting_source_pool_receipt")
            candidates[sid]=value
    games,conflicts={},set()
    for game in schedule["games"]:
        if type(game) is not dict:continue
        gid=str(game.get("game_id"))
        if gid in games and fingerprint(games[gid])!=fingerprint(game):conflicts.add(gid)
        games[gid]=game
    ids=[]
    for row in doc["receipts"]:
        if type(row) is not dict or type(row.get("receipt_id")) is not str:raise ValueError("invalid_generation_receipt")
        sid=row["receipt_id"];ids.append(sid);source=candidates.get(sid)
        if source is None or fingerprint(row.get("leg"))!=fingerprint(source["leg"])or fingerprint(row.get("learning_receipt"))!=fingerprint(source["learning_receipt"]):
            raise ValueError("generation_pool_receipt_mismatch")
        if source["learning_receipt"].get("base_model_version")!=pool.get("base_model_version"):raise ValueError("receipt_pool_base_mismatch")
        gid=str(row["leg"].get("game_id"));game=games.get(gid)
        if gid in conflicts or game is None:raise ValueError("missing_or_conflicting_schedule_game")
        if (game.get("week")!=doc["week"]or game.get("status")!="STATUS_SCHEDULED"or game.get("kickoff_utc")!=row.get("kickoff_utc")
            or row.get("recorded_utc")!=gen["recorded_utc"]or recorded>=instant(game["kickoff_utc"])):
            raise ValueError("generation_schedule_mismatch")
        if row.get("generation_id")!=gen["generation_id"]:raise ValueError("receipt_generation_mismatch")
    if len(set(ids))!=len(ids)or gen.get("receipt_ids")!=sorted(ids):raise ValueError("generation_receipt_membership_mismatch")
    offered=gen.get("offered_cards")
    if (type(offered) is not list or any(type(card) is not dict or type(card.get("card_id")) is not str
        or type(card.get("receipt_ids")) is not list or any(type(sid) is not str or sid not in candidates for sid in card["receipt_ids"])
        for card in offered)):raise ValueError("offered_receipt_not_in_pool")
    return doc
