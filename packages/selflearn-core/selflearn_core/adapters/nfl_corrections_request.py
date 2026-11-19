"""Strict request validation for NFL corrections."""
from __future__ import annotations

from ..probability_receipts import instant
from .nfl_corrections_policy import (
    BASELINE_GENERATION_KEYS,
    CorrectionsError,
    LEG_KEYS,
    LINEAGE_VERSION,
    MAX_LEG_IDS_PER_PARLAY,
    MAX_LEGS,
    MAX_PARLAYS,
    PARLAY_KEYS,
    REQUEST_KEYS,
    SCHEMA_VERSION,
    _prob,
    _text,
    is_digest,
)


def validate_request(payload):
    if not isinstance(payload, dict):
        raise CorrectionsError("invalid_request_shape")
    version = payload.get("schema_version")
    if type(version) is not int or version not in (SCHEMA_VERSION, LINEAGE_VERSION):
        raise CorrectionsError("unsupported_schema_version")
    lineage_request = version == LINEAGE_VERSION
    if lineage_request:
        required = REQUEST_KEYS - {"baseline_model_version"}
        if not required <= set(payload) <= REQUEST_KEYS | {"calibrator_version", "baseline_generation"}:
            raise CorrectionsError("invalid_request_shape")
    elif set(payload) != REQUEST_KEYS:
        raise CorrectionsError("invalid_request_shape")
    request_id = _text(payload["request_id"], name="request_id")
    if type(payload["season"]) is not int or not 2000 <= payload["season"] <= 2100:
        raise CorrectionsError("invalid_season")
    if type(payload["week"]) is not int or not 1 <= payload["week"] <= 22:
        raise CorrectionsError("invalid_week")
    if not lineage_request:
        _text(payload["baseline_model_version"], name="baseline_model_version")
    instant(payload["generated_at"])
    legs, parlays = payload["legs"], payload["parlays"]
    if type(legs) is not list or type(parlays) is not list:
        raise CorrectionsError("invalid_collections")
    if not 1 <= len(legs) <= MAX_LEGS or len(parlays) > MAX_PARLAYS:
        raise CorrectionsError("payload_size_limit")
    normalized_legs, counts = [], {}
    for row in legs:
        allowed = LEG_KEYS | {"contract_id", "source_receipt_id"} if lineage_request else LEG_KEYS
        if not isinstance(row, dict) or not LEG_KEYS <= set(row) <= allowed:
            raise CorrectionsError("invalid_leg_shape")
        leg_id = _text(row["leg_id"], name="leg_id")
        counts[leg_id] = counts.get(leg_id, 0) + 1
        normalized_legs.append({
            "leg_id": leg_id,
            "game_id": _text(row["game_id"], name="game_id"),
            "market": _text(row["market"], name="market", maximum=100),
            "selection": _text(row["selection"], name="selection", maximum=100),
            "baseline_prob": _prob(row["baseline_prob"]),
        })
        if lineage_request:
            for key in ("contract_id", "source_receipt_id"):
                normalized_legs[-1][key] = row.get(key) if is_digest(row.get(key)) else None
    dupes = {leg_id for leg_id, count in counts.items() if count > 1}
    for row in normalized_legs:
        row["duplicate"] = row["leg_id"] in dupes
    normalized_parlays, seen_parlays = [], set()
    for row in parlays:
        if not isinstance(row, dict) or set(row) != PARLAY_KEYS:
            raise CorrectionsError("invalid_parlay_shape")
        parlay_id = _text(row["parlay_id"], name="parlay_id")
        if parlay_id in seen_parlays:
            raise CorrectionsError("duplicate_parlay_id")
        seen_parlays.add(parlay_id)
        leg_ids = row["leg_ids"]
        if (type(leg_ids) is not list or not 1 <= len(leg_ids) <= MAX_LEG_IDS_PER_PARLAY
                or any(not isinstance(item, str) or not 1 <= len(item) <= 200 for item in leg_ids)
                or len(set(leg_ids)) != len(leg_ids)):
            raise CorrectionsError("invalid_parlay_leg_ids")
        normalized_parlays.append({
            "parlay_id": parlay_id,
            "leg_ids": list(leg_ids),
            "baseline_prob": _prob(row["baseline_prob"]),
        })
    result = {
        "schema_version": version,
        "request_id": request_id,
        "season": payload["season"],
        "week": payload["week"],
        "baseline_model_version": payload.get("baseline_model_version"),
        "generated_at": payload["generated_at"],
        "legs": normalized_legs,
        "parlays": normalized_parlays,
    }
    if lineage_request:
        baseline = payload.get("baseline_generation")
        result["calibrator_version"] = payload.get("calibrator_version")
        result["baseline_generation"] = (baseline if type(baseline) is dict
                                         and set(baseline) == BASELINE_GENERATION_KEYS else None)
    return result
