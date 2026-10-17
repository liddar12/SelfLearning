"""Versioned, domain-neutral binary forecast receipts. No fitting, storage or I/O.

Hashes detect accidental mutation; they are not signatures or proof of public
publication. A receipt proves only the fields its producer actually supplied.
Adapters must validate their source contract before sealing this envelope.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import math
import re

VERSION = 1
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_TEXT = ("domain", "task", "event_id", "correlation_group", "base_model_version",
         "calibrator_version")
_DIGESTS = ("contract_id", "source_receipt_id", "source_snapshot_id", "receipt_id")


def fingerprint(value):
    """Canonical JSON digest; reject NaN/infinity rather than invent JSON."""
    return "sha256:" + hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False).encode("utf-8")).hexdigest()


def instant(value):
    if not isinstance(value, str):
        raise ValueError("invalid_timestamp")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid_timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone_required")
    return result.astimezone(dt.timezone.utc)


def finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def seal_receipt(payload):
    """Copy, hash and validate. Existing IDs may not be silently overwritten."""
    if not isinstance(payload, dict) or "receipt_id" in payload:
        raise ValueError("unsealed_payload_required")
    out = copy.deepcopy(payload)
    out["receipt_id"] = fingerprint(out)
    validate_probability_receipt(out)
    return out


def validate_probability_receipt(receipt):
    """Strict pre-event, finite probabilities, source-as-of and content integrity.

    All source timestamps must be known. Null is not evidence of pre-event
    availability. This is deliberately stricter than some historical adapters.
    """
    if not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != VERSION:
        raise ValueError("unsupported_receipt_version")
    for key in _TEXT:
        if not isinstance(receipt.get(key), str) or not receipt[key].strip():
            raise ValueError("missing_" + key)
    for key in _DIGESTS:
        if not isinstance(receipt.get(key), str) or not _HASH.fullmatch(receipt[key]):
            raise ValueError("invalid_" + key)
    for key in ("base_prob", "served_prob"):
        if not finite_number(receipt.get(key)) or not 0 <= receipt[key] <= 1:
            raise ValueError("invalid_" + key)
    if receipt.get("publication_status") not in ("generated_unverified", "verified_publication"):
        raise ValueError("invalid_publication_status")
    if not isinstance(receipt.get("features"), dict) or not receipt["features"]:
        raise ValueError("missing_features")
    created = instant(receipt.get("forecast_created_utc"))
    seen = instant(receipt.get("first_seen_utc"))
    start = instant(receipt.get("event_start_utc"))
    if not created <= seen < start:
        raise ValueError("not_preevent_receipt")
    timestamps = receipt.get("source_timestamps")
    if not isinstance(timestamps, dict) or not timestamps:
        raise ValueError("missing_source_timestamps")
    for name, stamp in timestamps.items():
        if not isinstance(name, str) or not name or instant(stamp) > created:
            raise ValueError("source_after_prediction")
    try:
        digest = fingerprint({k: v for k, v in receipt.items() if k != "receipt_id"})
    except (ValueError, TypeError) as exc:
        raise ValueError("non_json_receipt") from exc
    if receipt["receipt_id"] != digest:
        raise ValueError("receipt_hash_mismatch")


def validate_resolution(receipt, outcome):
    """Only explicit FINAL binary outcomes on this exact original contract."""
    validate_probability_receipt(receipt)
    if not isinstance(outcome, dict):
        raise ValueError("missing_outcome")
    for key in ("receipt_id", "contract_id", "event_id"):
        if outcome.get(key) != receipt[key]:
            raise ValueError("outcome_" + key + "_mismatch")
    if outcome.get("status") != "FINAL" or type(outcome.get("hit")) is not bool:
        raise ValueError("not_final_binary_outcome")
    if instant(outcome.get("resolved_utc")) < instant(receipt["event_start_utc"]):
        raise ValueError("resolution_before_event")
    if not isinstance(outcome.get("source"), str) or not outcome["source"].strip():
        raise ValueError("missing_outcome_source")


def prediction_id(receipt):
    """Stable across ingestion batches and copies of the same source receipt."""
    validate_probability_receipt(receipt)
    return receipt["task"] + ":" + receipt["source_receipt_id"]
