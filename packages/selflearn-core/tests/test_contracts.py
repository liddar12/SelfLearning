"""Tests for the terminal <-> scorer data contract (selflearn_core.contracts)."""

import json

import pytest

from selflearn_core import contracts as C


def test_breadth_stamp_derives_levels():
    b = C.breadth_stamp(direction="rising", last=0.2861, dma50=0.2839, dma200=0.2852)
    assert b == {
        "direction": "rising",
        "vs50dma": "above",
        "vs200dma": "above",
        "ratio": 0.2861,
    }


def test_breadth_stamp_below_levels_and_explicit_ratio():
    b = C.breadth_stamp(direction="falling", last=0.281, dma50=0.284, dma200=0.285, ratio=0.28104)
    assert b["vs50dma"] == "below"
    assert b["vs200dma"] == "below"
    assert b["ratio"] == 0.28104


def test_breadth_stamp_rejects_bad_direction():
    with pytest.raises(ValueError):
        C.breadth_stamp(direction="sideways", last=1.0, dma50=1.0, dma200=1.0)


def test_from_payload_happy_path():
    payload = {"direction": "rising", "last": 0.2861, "dma50": 0.2839, "dma200": 0.2852}
    assert C.breadth_stamp_from_payload(payload) == {
        "direction": "rising",
        "vs50dma": "above",
        "vs200dma": "above",
        "ratio": 0.2861,
    }


@pytest.mark.parametrize(
    "payload",
    [
        {"direction": "rising", "last": None, "dma50": 0.28, "dma200": 0.28},  # breadth unavailable
        {"direction": "rising", "dma50": 0.28, "dma200": 0.28},  # missing last
        {"last": 0.28, "dma50": 0.28, "dma200": 0.28},  # missing direction
        {},  # empty
    ],
)
def test_from_payload_returns_none_when_unavailable(payload):
    assert C.breadth_stamp_from_payload(payload) is None


def _valid_record():
    return {
        "id": "abc-123",
        "ts": 1754491862,
        "model_version": "scanner@2026-08-06",
        "task": "scanner",
        "features": {"breadth": C.breadth_stamp(direction="rising", last=0.286, dma50=0.284, dma200=0.285)},
        "prediction": {"side": "call", "symbol": "NVDA"},
        "horizon_s": 86400,
        "confidence": 0.6,
        "cohort": "NVDA",
    }


def test_valid_record_passes():
    C.validate_prediction_record(_valid_record())


def test_record_without_breadth_is_valid():
    rec = _valid_record()
    rec["features"] = {}  # breadth is optional (unavailable -> omitted)
    C.validate_prediction_record(rec)


@pytest.mark.parametrize("field", ["id", "ts", "model_version", "task", "features", "prediction", "horizon_s"])
def test_missing_required_field_fails(field):
    rec = _valid_record()
    del rec[field]
    with pytest.raises(ValueError):
        C.validate_prediction_record(rec)


def test_bad_confidence_fails():
    rec = _valid_record()
    rec["confidence"] = 1.5
    with pytest.raises(ValueError):
        C.validate_prediction_record(rec)


def test_bad_breadth_block_fails():
    rec = _valid_record()
    rec["features"]["breadth"]["vs50dma"] = "sideways"
    with pytest.raises(ValueError):
        C.validate_prediction_record(rec)


def test_python_constants_match_schema_enums():
    """The Python vocabulary must stay in sync with the JSON Schema."""
    schema = C.load_schema()
    props = schema["$defs"]["breadth"]["properties"]
    assert set(props["direction"]["enum"]) == set(C.DIRECTIONS)
    assert set(props["vs50dma"]["enum"]) == set(C.VS_LEVELS)
    assert set(props["vs200dma"]["enum"]) == set(C.VS_LEVELS)


def test_schema_file_is_valid_json_and_targets_prediction_record():
    schema = C.load_schema()
    assert schema["title"] == "PredictionRecord"
    assert set(schema["required"]) == set(
        ["id", "ts", "model_version", "task", "features", "prediction", "horizon_s"]
    )
    # sanity: it round-trips
    json.dumps(schema)
