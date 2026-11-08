#!/usr/bin/env python3
"""Regenerate fixtures/nfl-ingest-m1 machine-readable capture contract fixtures.

No network. Digests detect mutation only — not authenticity or public serving.
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "selflearn-core"))

from selflearn_core.adapters.nfl_generation import LEG_KEYS, MANIFEST_KEYS, SOURCE_NAMES
from selflearn_core.probability_receipts import fingerprint

OUT = ROOT / "fixtures" / "nfl-ingest-m1"


def build():
    created = "2026-09-01T14:00:00Z"
    recorded = "2026-09-01T14:01:00Z"
    kickoff = "2026-09-02T00:15:00Z"
    probability = 0.62
    a = math.log(probability / (1 - probability))
    base = 1 / (1 + math.exp(-a))
    served = round(base, 4)
    player = "00-0034857"
    game = "G1"
    key = [2026, game, player, "qb_pass_yds", "249.5", "home", "over", "gte"]
    source = {
        "version": 1,
        "contract_id": fingerprint(key),
        "season": 2026,
        "week": 6,
        "game_id": game,
        "player_id": player,
        "market": "qb_pass_yds",
        "line": 249.5,
        "side": "home",
        "direction": "over",
        "comparison_operator": "gte",
        "base_prob": base,
        "served_prob": served,
        "base_model_version": fingerprint("fixture-base-v1"),
        "calibrator_version": fingerprint(None),
        "calibrator": None,
        "forecast_created_utc": created,
        "source_snapshot_id": fingerprint({"created": created}),
        "source_timestamps": {"fixture": created},
        "generation_sha": "fixture-m1",
        "publication_status": "generated_unverified",
        "features": {
            "market": "qb_pass_yds",
            "line": 249.5,
            "mu": 260.0,
            "sd": 45.0,
            "z": (260 - 249.5) / 45,
            "p_team": 0.55,
            "selection": "over",
            "direction": "over",
            "calibration": {"a": a, "b": 0.0, "c": 0.0},
            "correlation_tags": ["game:" + game],
        },
        "input_context": {
            "availability": "active",
            "this_week": {"wk": 6},
            "eligibility": {"eligible": True},
        },
    }
    source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
    pool = {
        "season": 2026,
        "week": 6,
        "generated_utc": created,
        "base_model_version": source["base_model_version"],
        "players": [{
            "game_id": game,
            "gsis_id": player,
            "market": "qb_pass_yds",
            "side": "home",
            "position": "QB",
            "rungs": [{
                "line": 249.5,
                "model_prob": served,
                "selection": "over",
                "direction": "over",
                "comparison_operator": "gte",
                "learning_receipt": source,
            }],
        }],
    }
    schedule = {
        "season": 2026,
        "games": [{
            "game_id": game,
            "week": 6,
            "status": "STATUS_SCHEDULED",
            "kickoff_utc": kickoff,
        }],
    }
    calibration = None
    pool_id = fingerprint(pool)
    sched_id = fingerprint(schedule)
    cal_id = fingerprint(calibration)
    offered = [{"card_id": "card-fixture-1", "receipt_ids": [source["receipt_id"]]}]
    manifest = {
        "version": 1,
        "season": 2026,
        "week": 6,
        "pool_generated_utc": created,
        "source_snapshots": {
            "leg_pool": pool_id,
            "schedule": sched_id,
            "parlay_calibration": cal_id,
        },
        "offered_cards": offered,
        "selection_policy_version": fingerprint("selection-policy-fixture-v1"),
        "joint_model_version": fingerprint("joint-model-fixture-v1"),
    }
    gid = fingerprint({k: manifest[k] for k in MANIFEST_KEYS})
    leg_for_receipt = {
        k: v for k, v in {**pool["players"][0], **pool["players"][0]["rungs"][0]}.items()
        if k in LEG_KEYS
    }
    row = {
        "receipt_id": source["receipt_id"],
        "learning_receipt": copy.deepcopy(source),
        "leg": leg_for_receipt,
        "kickoff_utc": kickoff,
        "recorded_utc": recorded,
        "generation_id": gid,
        "publication_status": "generated_unverified",
    }
    generation = {
        **manifest,
        "generation_id": gid,
        "recorded_utc": recorded,
        "receipt_ids": sorted([source["receipt_id"]]),
        "publication_status": "generated_unverified",
        "population": "eligible_pool_prop_rungs_before_display_dedupe",
    }
    doc = {
        "version": 1,
        "season": 2026,
        "week": 6,
        "receipts": [row],
        "generations": [generation],
    }
    doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    ids = {
        "leg_pool_object_id": pool_id,
        "schedule_object_id": sched_id,
        "parlay_calibration_object_id": cal_id,
        "envelope_id": doc["envelope_id"],
        "generation_id": gid,
        "source_receipt_id": source["receipt_id"],
    }
    contract = {
        "version": 1,
        "kind": "nfl.ingest_contract.m1.v1",
        "engine_routes": {
            "objects": "POST /api/v1/nfl/objects",
            "generations": "POST /api/v1/nfl/generations",
        },
        "base_url_production": "https://self-learning-beige.vercel.app",
        "auth": {
            "header": "Authorization: Bearer <SELFLEARN_NFL_INGEST_TOKEN>",
            "token_min_chars": 32,
            "placeholder_ban": ["", "[SENSITIVE]"],
            "must_differ_from": [
                "SELFLEARN_OPERATOR_TOKEN",
                "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN",
                "SELFLEARN_NFL_CORRECTIONS_TOKEN",
            ],
            "note": ("Engine-side scoped ingest token already issued; NFL Actions "
                     "must wire the matching secret before producer --send."),
        },
        "limits": {
            "max_body_bytes": 3145728,
            "max_json_depth": 24,
            "supabase_http_timeout_seconds_per_request": 12,
            "vercel_function_max_duration_seconds": 60,
            "timeout_clarification": (
                "Engine urllib open(..., timeout=12) applies per Supabase store "
                "HTTP request inside a handler — not a whole-capture or "
                "multi-request producer budget. Vercel maxDuration is 60s per "
                "function invocation. Do not reinterpret 12s as total capture "
                "time, and do not demand exporter client 20→12 without measured "
                "per-request latency evidence."
            ),
            "m1_serial_http_operations": [
                "POST /api/v1/nfl/objects source_snapshot leg_pool",
                "POST /api/v1/nfl/objects source_snapshot schedule",
                "POST /api/v1/nfl/objects source_snapshot parlay_calibration",
                "POST /api/v1/nfl/objects generation envelope",
                "POST /api/v1/nfl/generations finalize",
            ],
            "producer_timeout_retry_guidance": {
                "per_request_client_timeout_seconds": (
                    "Must tolerate engine per-request Supabase work up to 12s "
                    "plus RTT. A 20s producer client timeout is not contract-"
                    "forbidden pending measurement; lowering below 12 risks "
                    "cutting off legitimate store waits."
                ),
                "retries": (
                    "Retry only with identical immutable object_id/envelope_id. "
                    "503/transport → retry; 422 → fix payload (do not retry); "
                    "409 → conflict, do not overwrite; 401/403 → stop. Object "
                    "ACK status=duplicate and finalize receipt status=duplicate "
                    "are successful idempotent replays."
                ),
                "total_wall_clock": (
                    "M1 serial path is 5 HTTP calls. Bound total wall clock as "
                    "N_ops × per_request_client_timeout × (1 + retry_budget), "
                    "independent of the 12s-per-Supabase-request constant. "
                    "Vercel 60s caps a single server invocation, not the "
                    "producer's multi-request job."
                ),
            },
        },
        "object_request_keys": ["version", "kind", "object_id", "payload"],
        "generation_request_keys": ["version", "object_id"],
        "kinds": ["source_snapshot", "generation"],
        "MANIFEST_KEYS": list(MANIFEST_KEYS),
        "SOURCE_NAMES": sorted(SOURCE_NAMES),
        "LEG_KEYS": sorted(LEG_KEYS),
        "publication_status": {
            "allowed_on_receipt": ["generated_unverified", "verified_publication"],
            "required_on_generation_capture": "generated_unverified",
            "population_required": "eligible_pool_prop_rungs_before_display_dedupe",
            "learned_eligibility": {
                "generated_unverified": "capture_ok_fit_ineligible",
                "verified_publication": (
                    "stronger_publication_claim_not_automatic_fit; "
                    "still needs journal revision/as-of binder"
                ),
                "invented_served_enum": "forbidden",
            },
        },
        "ack_shapes": {
            "object": {
                "version": 1,
                "object_id": "<sha256:...>",
                "durable": True,
                "status": "recorded|duplicate",
            },
            "generation": {
                "version": 1,
                "object_id": "<sha256:...>",
                "durable": True,
                "receipts": [{
                    "source_receipt_id": "...",
                    "receipt_id": "...",
                    "status": "recorded|duplicate",
                }],
                "rejections": [{"source_receipt_id": "...", "reason": "machine_code"}],
            },
        },
        "hash_rules": {
            "fingerprint": (
                "sha256: + sha256(json.dumps(value, sort_keys=True, "
                "separators=(',',':'), ensure_ascii=True, allow_nan=False))"
            ),
            "source_snapshot_object_id": "fingerprint(payload)",
            "generation_object_id": (
                "fingerprint(payload without envelope_id); "
                "payload.envelope_id == object_id"
            ),
            "generation_id": "fingerprint({k: manifest[k] for k in MANIFEST_KEYS})",
        },
        "fixture_ids": ids,
    }
    verified = copy.deepcopy(source)
    verified["publication_status"] = "verified_publication"
    verified["receipt_id"] = fingerprint({k: v for k, v in verified.items() if k != "receipt_id"})
    OUT.mkdir(parents=True, exist_ok=True)

    def dump(name, value):
        (OUT / name).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    dump("contract.json", contract)
    dump("index.json", {
        "version": 1,
        "kind": "nfl.ingest_fixture_index.m1.v1",
        "files": {
            "contract": "contract.json",
            "source_snapshot_leg_pool": "source_snapshot_leg_pool.json",
            "source_snapshot_schedule": "source_snapshot_schedule.json",
            "source_snapshot_parlay_calibration": "source_snapshot_parlay_calibration.json",
            "generation_envelope": "generation_envelope.json",
            "objects_upload_sequence": "objects_upload_sequence.json",
            "finalize_request": "finalize_request.json",
            "expected_object_ack_recorded": "expected_object_ack_recorded.json",
            "expected_finalize_ack": "expected_finalize_ack_shape.json",
            "example_verified_publication_receipt": "example_learning_receipt_verified_publication.json",
        },
        "ids": ids,
    })
    dump("source_snapshot_leg_pool.json", {
        "version": 1, "kind": "source_snapshot", "object_id": pool_id, "payload": pool,
    })
    dump("source_snapshot_schedule.json", {
        "version": 1, "kind": "source_snapshot", "object_id": sched_id, "payload": schedule,
    })
    dump("source_snapshot_parlay_calibration.json", {
        "version": 1, "kind": "source_snapshot", "object_id": cal_id, "payload": calibration,
    })
    dump("generation_envelope.json", {
        "version": 1, "kind": "generation", "object_id": doc["envelope_id"], "payload": doc,
    })
    dump("objects_upload_sequence.json", {
        "version": 1,
        "order": [
            "source_snapshot_leg_pool.json",
            "source_snapshot_schedule.json",
            "source_snapshot_parlay_calibration.json",
            "generation_envelope.json",
        ],
        "then": "finalize_request.json",
    })
    dump("finalize_request.json", {"version": 1, "object_id": doc["envelope_id"]})
    dump("expected_object_ack_recorded.json", {
        "version": 1, "object_id": pool_id, "durable": True, "status": "recorded",
    })
    dump("expected_finalize_ack_shape.json", {
        "version": 1,
        "object_id": doc["envelope_id"],
        "durable": True,
        "receipts": [{
            "source_receipt_id": source["receipt_id"],
            "receipt_id": "<engine_sealed_receipt_id>",
            "status": "recorded",
        }],
        "rejections": [],
        "note": (
            "receipt_id is engine-sealed generic envelope id; "
            "source_receipt_id is NFL learning_receipt.receipt_id"
        ),
    })
    dump("example_learning_receipt_verified_publication.json", {
        "note": (
            "Illustrates allowed receipt publication_status enum value. "
            "Generation capture still requires generated_unverified; "
            "this file is NOT a drop-in generation fixture."
        ),
        "learning_receipt": verified,
    })
    return ids


if __name__ == "__main__":
    print(json.dumps(build(), sort_keys=True, indent=2))
