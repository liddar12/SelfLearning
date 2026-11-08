"""Strict request validation for NFL corrections v1."""
from __future__ import annotations

from ..probability_receipts import instant
from .nfl_corrections_policy import (
    CorrectionsError,
    LEG_KEYS,
    MAX_LEG_IDS_PER_PARLAY,
    MAX_LEGS,
    MAX_PARLAYS,
    PARLAY_KEYS,
    REQUEST_KEYS,
    SCHEMA_VERSION,
    _prob,
    _text,
)


def validate_request(payload):
    if not isinstance(payload, dict) or set(payload) != REQUEST_KEYS:
        raise CorrectionsError("invalid_request_shape")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
        raise CorrectionsError("unsupported_schema_version")
    request_id = _text(payload["request_id"], name="request_id")
    if type(payload["season"]) is not int or not 2000 <= payload["season"] <= 2100:
        raise CorrectionsError("invalid_season")
    if type(payload["week"]) is not int or not 1 <= payload["week"] <= 22:
        raise CorrectionsError("invalid_week")
    _text(payload["baseline_model_version"], name="baseline_model_version")
    instant(payload["generated_at"])
    legs, parlays = payload["legs"], payload["parlays"]
    if type(legs) is not list or type(parlays) is not list:
        raise CorrectionsError("invalid_collections")
    if not 1 <= len(legs) <= MAX_LEGS or len(parlays) > MAX_PARLAYS:
        raise CorrectionsError("payload_size_limit")
    normalized_legs, counts = [], {}
    for row in legs:
        if not isinstance(row, dict) or set(row) != LEG_KEYS:
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
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id,
        "season": payload["season"],
        "week": payload["week"],
        "baseline_model_version": payload["baseline_model_version"],
        "generated_at": payload["generated_at"],
        "legs": normalized_legs,
        "parlays": normalized_parlays,
    }
