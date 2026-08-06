"""The data contract between the liddar-terminal app (writer) and the
selflearn-core scorer (reader).

The terminal stamps every prediction, at creation time, with the market breadth
regime so the scorer can split accuracy by regime. The canonical wire format is
``schemas/prediction_record.schema.json``; this module provides the builders and
a dependency-free validator so neither side needs a JSON-Schema library.

Everything here is pure: no I/O beyond loading the packaged schema, no third-party
deps (keeps the Gate 1 package installable with an empty dependency set).
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any, Optional

# --- breadth regime vocabulary (kept in sync with the JSON Schema; a test asserts
# the two agree, so changing one without the other fails CI). ---
DIRECTIONS = ("rising", "falling", "flat")
VS_LEVELS = ("above", "below")

_REQUIRED_TOP = ("id", "ts", "model_version", "task", "features", "prediction", "horizon_s")
_BREADTH_KEY = "breadth"


def load_schema() -> dict[str, Any]:
    """Return the parsed prediction-record JSON Schema shipped with the package."""
    text = resources.files(__package__).joinpath(
        "schemas/prediction_record.schema.json"
    ).read_text(encoding="utf-8")
    return json.loads(text)


def breadth_stamp(
    *, direction: str, last: float, dma50: float, dma200: float, ratio: Optional[float] = None
) -> dict[str, Any]:
    """Build the ``features.breadth`` block from /api/breadth-style values.

    ``vs50dma`` / ``vs200dma`` are derived from ``last`` against the moving
    averages; ``ratio`` defaults to ``last`` when not given explicitly.
    """
    if direction not in DIRECTIONS:
        raise ValueError(f"direction must be one of {DIRECTIONS}, got {direction!r}")
    for name, v in (("last", last), ("dma50", dma50), ("dma200", dma200)):
        if v is None:
            raise ValueError(f"{name} is required to build a breadth stamp")
    block = {
        "direction": direction,
        "vs50dma": "above" if last >= dma50 else "below",
        "vs200dma": "above" if last >= dma200 else "below",
        "ratio": round(float(ratio if ratio is not None else last), 5),
    }
    return block


def breadth_stamp_from_payload(payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Build the breadth block straight from an /api/breadth JSON payload.

    Returns ``None`` when the payload lacks what's needed (breadth unavailable) —
    the caller then omits ``features.breadth`` rather than fabricating it, matching
    the terminal's fail-visible contract.
    """
    direction = payload.get("direction")
    last = payload.get("last")
    dma50 = payload.get("dma50")
    dma200 = payload.get("dma200")
    if direction not in DIRECTIONS or last is None or dma50 is None or dma200 is None:
        return None
    return breadth_stamp(direction=direction, last=last, dma50=dma50, dma200=dma200)


def validate_breadth(block: Any) -> None:
    """Raise ``ValueError`` unless ``block`` is a well-formed breadth stamp."""
    if not isinstance(block, dict):
        raise ValueError("breadth must be an object")
    missing = {"direction", "vs50dma", "vs200dma", "ratio"} - block.keys()
    if missing:
        raise ValueError(f"breadth missing keys: {sorted(missing)}")
    extra = block.keys() - {"direction", "vs50dma", "vs200dma", "ratio"}
    if extra:
        raise ValueError(f"breadth has unexpected keys: {sorted(extra)}")
    if block["direction"] not in DIRECTIONS:
        raise ValueError(f"breadth.direction must be one of {DIRECTIONS}")
    if block["vs50dma"] not in VS_LEVELS:
        raise ValueError(f"breadth.vs50dma must be one of {VS_LEVELS}")
    if block["vs200dma"] not in VS_LEVELS:
        raise ValueError(f"breadth.vs200dma must be one of {VS_LEVELS}")
    if not isinstance(block["ratio"], (int, float)) or isinstance(block["ratio"], bool):
        raise ValueError("breadth.ratio must be a number")


def validate_prediction_record(rec: Any) -> None:
    """Raise ``ValueError`` unless ``rec`` satisfies the prediction-record contract.

    Dependency-free check of the subset of the JSON Schema we rely on: required
    top-level fields, their basic types, and (when present) the breadth block.
    """
    if not isinstance(rec, dict):
        raise ValueError("record must be an object")
    missing = [k for k in _REQUIRED_TOP if k not in rec]
    if missing:
        raise ValueError(f"record missing required fields: {missing}")
    if not isinstance(rec["id"], str):
        raise ValueError("id must be a string")
    if not isinstance(rec["ts"], int) or isinstance(rec["ts"], bool):
        raise ValueError("ts must be an integer (unix seconds)")
    if not isinstance(rec["model_version"], str):
        raise ValueError("model_version must be a string")
    if not isinstance(rec["task"], str):
        raise ValueError("task must be a string")
    if not isinstance(rec["features"], dict):
        raise ValueError("features must be an object")
    if not isinstance(rec["horizon_s"], int) or isinstance(rec["horizon_s"], bool):
        raise ValueError("horizon_s must be an integer")
    if rec["horizon_s"] < 1:
        raise ValueError("horizon_s must be >= 1")
    conf = rec.get("confidence")
    if conf is not None and (not isinstance(conf, (int, float)) or not 0 <= conf <= 1):
        raise ValueError("confidence must be null or a number in [0, 1]")
    if _BREADTH_KEY in rec["features"]:
        validate_breadth(rec["features"][_BREADTH_KEY])
