"""Authenticated NFL generation intake. No fitting, publication claim or promotion.

The API has one server-owned journal. There is no client-selectable tenant,
database, path or namespace. A production journal implementation is deliberately
not selected here; only explicitly injected durable implementations can ACK.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.probability_receipts import fingerprint, instant
from selflearn_core.store.receipt_journal import JournalConflict

LOG = logging.getLogger(__name__)
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
SOURCE_NAMES = {"leg_pool", "schedule", "parlay_calibration"}
MANIFEST_KEYS = ("version", "season", "week", "pool_generated_utc", "source_snapshots",
                 "offered_cards", "selection_policy_version", "joint_model_version")
LEG_KEYS = {"game_id", "gsis_id", "market", "line", "side", "direction",
            "comparison_operator", "model_prob", "selection", "position"}


def request_fields(value, fields):
    if (not isinstance(value, dict) or set(value) != set(fields)
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("object_id"), str) or not HASH.fullmatch(value["object_id"])):
        raise ValueError("invalid_ingestion_request")
    return value


def validate_generation(journal, object_id):
    """Join the exact stored source bodies, not just plausible digest strings.

    The joint/selection versions remain producer-declared hashes. We validate
    the exact correlation input's hash, not an independently recovered selector
    source or a claim that joint probabilities are calibrated.
    """
    obj = journal.get_object(object_id)
    if obj is None or obj["kind"] != "generation":
        raise ValueError("generation_object_missing")
    doc = obj["payload"]
    if (not isinstance(doc, dict) or type(doc.get("version")) is not int or doc["version"] != 1
            or not isinstance(doc.get("generations"), list) or len(doc["generations"]) != 1
            or not isinstance(doc.get("receipts"), list)):
        raise ValueError("invalid_generation_document")
    if fingerprint({k: v for k, v in doc.items() if k != "envelope_id"}) != object_id:
        raise ValueError("generation_object_hash_mismatch")
    gen = doc["generations"][0]
    if not isinstance(gen, dict) or any(k not in gen for k in MANIFEST_KEYS):
        raise ValueError("invalid_generation_manifest")
    if gen.get("generation_id") != fingerprint({k: gen[k] for k in MANIFEST_KEYS}):
        raise ValueError("generation_manifest_hash_mismatch")
    if (type(gen["version"]) is not int or gen["version"] != 1
            or type(doc.get("season")) is not int or type(doc.get("week")) is not int
            or not 1 <= doc["week"] <= 18
            or gen["season"] != doc["season"] or gen["week"] != doc["week"]):
        raise ValueError("generation_period_mismatch")
    if any(not isinstance(gen[k], str) or not HASH.fullmatch(gen[k])
           for k in ("selection_policy_version", "joint_model_version")):
        raise ValueError("missing_policy_version")
    if (gen.get("publication_status") != "generated_unverified"
            or gen.get("population") != "eligible_pool_prop_rungs_before_display_dedupe"):
        raise ValueError("unsupported_generation_claim")
    recorded = instant(gen.get("recorded_utc"))
    if recorded > datetime.now(timezone.utc):
        raise ValueError("future_generation_observation")
    sources = gen["source_snapshots"]
    if not isinstance(sources, dict) or set(sources) != SOURCE_NAMES:
        raise ValueError("invalid_source_manifest")
    payloads = {}
    for name, source_id in sources.items():
        if not isinstance(source_id, str) or not HASH.fullmatch(source_id):
            raise ValueError("invalid_source_object_id")
        source = journal.get_object(source_id)
        if source is None or source["kind"] != "source_snapshot":
            raise ValueError("source_object_missing")
        if fingerprint(source["payload"]) != source_id:
            raise ValueError("source_object_hash_mismatch")
        payloads[name] = source["payload"]
    pool, schedule, calibration = (payloads[k] for k in ("leg_pool", "schedule", "parlay_calibration"))
    if (not isinstance(pool, dict) or not isinstance(pool.get("players"), list)
            or not isinstance(schedule, dict) or not isinstance(schedule.get("games"), list)
            or (calibration is not None and not isinstance(calibration, dict))):
        raise ValueError("invalid_source_payload")
    if (pool.get("season") != doc["season"] or pool.get("week") != doc["week"]
            or schedule.get("season", doc["season"]) != doc["season"]
            or pool.get("generated_utc") != gen["pool_generated_utc"]
            or instant(pool["generated_utc"]) > recorded):
        raise ValueError("source_generation_mismatch")
    pool_rows = {}
    for player in pool["players"]:
        if not isinstance(player, dict) or not isinstance(player.get("rungs"), list):
            continue  # A malformed, unclaimed candidate may be producer-quarantined.
        for rung in player["rungs"]:
            if not isinstance(rung, dict) or not isinstance(rung.get("learning_receipt"), dict):
                continue
            sid = rung["learning_receipt"].get("receipt_id")
            if not isinstance(sid, str):
                continue
            leg = {k: v for k, v in {**player, **rung}.items() if k in LEG_KEYS}
            entry = {"leg": leg, "learning_receipt": rung["learning_receipt"]}
            if sid in pool_rows and pool_rows[sid] != entry:
                raise ValueError("conflicting_source_pool_receipt")
            pool_rows[sid] = entry
    games, conflicts = {}, set()
    for game in schedule["games"]:
        if not isinstance(game, dict):
            continue
        gid = str(game.get("game_id"))
        if gid in games and games[gid] != game:
            conflicts.add(gid)
        games[gid] = game
    ids = []
    for row in doc["receipts"]:
        if not isinstance(row, dict) or not isinstance(row.get("receipt_id"), str):
            raise ValueError("invalid_generation_receipt")
        sid = row["receipt_id"]
        ids.append(sid)
        source = pool_rows.get(sid)
        if source is None or row.get("leg") != source["leg"] or row.get("learning_receipt") != source["learning_receipt"]:
            raise ValueError("generation_pool_receipt_mismatch")
        if source["learning_receipt"].get("base_model_version") != pool.get("base_model_version"):
            raise ValueError("receipt_pool_base_mismatch")
        gid = str(row["leg"].get("game_id"))
        game = games.get(gid)
        if gid in conflicts or game is None:
            raise ValueError("missing_or_conflicting_schedule_game")
        if (game.get("week") != doc["week"] or game.get("status") != "STATUS_SCHEDULED"
                or game.get("kickoff_utc") != row.get("kickoff_utc")
                or row.get("recorded_utc") != gen["recorded_utc"]
                or recorded >= instant(game["kickoff_utc"])):
            raise ValueError("generation_schedule_mismatch")
        if row.get("generation_id") != gen["generation_id"]:
            raise ValueError("receipt_generation_mismatch")
    if len(set(ids)) != len(ids) or gen.get("receipt_ids") != sorted(ids):
        raise ValueError("generation_receipt_membership_mismatch")
    offered = gen.get("offered_cards")
    if (not isinstance(offered, list) or any(not isinstance(card, dict)
            or not isinstance(card.get("card_id"), str) or not isinstance(card.get("receipt_ids"), list)
            or any(not isinstance(sid, str) or sid not in pool_rows for sid in card["receipt_ids"])
            for card in offered)):
        raise ValueError("offered_receipt_not_in_pool")
    return doc


def finalize_generation(journal, object_id):
    committed = journal.generation_ack(object_id)
    if committed is not None:
        # A delivery retry is not a new evaluation under today's adapter. The
        # immutable transaction result remains authoritative after code changes.
        return {**committed, "receipts": [{**row, "status": "duplicate"}
                                         for row in committed["receipts"]]}
    doc = validate_generation(journal, object_id)
    batch = adapt_prospective(journal_docs=[doc], existing_receipts=journal.receipts())
    rejected = []
    for row in batch["rejections"]:
        if not isinstance(row.get("source_receipt_id"), str):
            raise ValueError("invalid_generation_rejection")
        reason = row.get("reason")
        # A downstream contract validator may produce a descriptive exception,
        # including user-provided field names. Persist only bounded machine
        # codes, and quarantine that row rather than aborting valid neighbors.
        if not isinstance(reason, str) or not REASON.fullmatch(reason):
            reason = "invalid_receipt_contract"
        rejected.append({"source_receipt_id": row["source_receipt_id"], "reason": reason})
    return journal.commit_generation(object_id, batch["receipts"], rejected)


def register_nfl_routes(app, journal_factory, body_reader):
    def run(operation):
        if journal_factory is None:
            raise HTTPException(503, "Durable NFL journal is not configured; nothing was acknowledged")
        try:
            with journal_factory() as journal:
                return operation(journal)
        except JournalConflict:
            raise HTTPException(409, "Immutable NFL data conflict") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid NFL generation or source evidence") from None
        except (sqlite3.Error, OSError):
            LOG.warning("NFL journal operation failed; no durable acknowledgement returned")
            raise HTTPException(503, "NFL journal unavailable; retry with the same immutable IDs") from None

    @app.post("/api/v1/nfl/objects")
    async def put_object(request: Request):
        try:
            data = request_fields(await body_reader(request), ("version", "kind", "object_id", "payload"))
        except ValueError:
            raise HTTPException(422, "Invalid NFL object request") from None
        def write(journal):
            added = journal.append_object(data["kind"], data["object_id"], data["payload"])
            return {"version": 1, "object_id": data["object_id"], "durable": True,
                    "status": "recorded" if added else "duplicate"}
        return await run_in_threadpool(run, write)

    @app.post("/api/v1/nfl/generations")
    async def finalize(request: Request):
        try:
            data = request_fields(await body_reader(request), ("version", "object_id"))
        except ValueError:
            raise HTTPException(422, "Invalid NFL generation request") from None
        return await run_in_threadpool(run, lambda journal: finalize_generation(journal, data["object_id"]))
