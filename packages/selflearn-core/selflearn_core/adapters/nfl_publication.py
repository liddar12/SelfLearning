"""Pure publication-evidence joins. HTTP observation is a trusted server input.

This module opens no resources and authenticates no remote origin. Only the
configured server verifier may construct observations; hashes are integrity,
not provider signatures or proof that a browser displayed a forecast.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
from urllib.parse import urlsplit

from ..probability_receipts import fingerprint, instant, finite_number
from .nfl import adapt_prospective
from .nfl_generation import validate_generation

MAX_BODY_BYTES = 3 * 1024 * 1024
MAX_JSON_DEPTH = 24
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
MARKETS = {"qb_pass_yds", "rb_rush_yds", "wr_rec_yds"}
POLICY_KIND = "nfl.pool_http_publication.v1"
KIND = "nfl.pool_http_publication_attestation.v1"
ROW_KEYS = {"receipt_id", "source_receipt_id", "market", "direction", "served_prob", "event_start_utc"}
RECORD_KEYS = {"version", "kind", "object_id", "generation_id", "source_pool_id", "policy", "policy_id",
               "recorded_utc", "observation", "receipts", "rejections", "status", "scope", "durable", "attestation_id"}


def _need(value, reason):
    if not value:
        raise ValueError(reason)


def _keys(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "invalid_publication_fields")


def _hash(value):
    _need(type(value) is str and HASH.fullmatch(value) is not None, "invalid_publication_digest")
    return value


def validate_policy(policy):
    """Validate an explicitly configured exact DNS HTTPS origin; no default."""
    _keys(policy, {"version", "kind", "origin", "path"})
    _need(type(policy["version"]) is int and policy["version"] == 1
          and policy["kind"] == POLICY_KIND and policy["path"] == "/data/leg_pool.json",
          "invalid_publication_policy")
    origin = policy["origin"]
    _need(type(origin) is str and 1 <= len(origin) <= 2048 and origin.isascii(), "invalid_publication_origin")
    try:
        parts = urlsplit(origin)
        host = parts.hostname
        valid = (parts.scheme == "https" and host and parts.netloc == host
                 and not parts.path and not parts.query and not parts.fragment
                 and parts.username is None and parts.password is None and parts.port is None
                 and origin == "https://" + host and len(host) <= 253
                 and host != "localhost" and not host.endswith(".localhost")
                 and "." in host and re.fullmatch(r"[a-z]{2,63}", host.rsplit(".", 1)[-1])
                 and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                                         for label in host.split(".")))
        if valid:
            try:
                ipaddress.ip_address(host)
            except ValueError:
                pass
            else:
                valid = False
    except (ValueError, TypeError):
        valid = False
    _need(valid, "invalid_publication_origin")
    return policy


validate_publication_policy = validate_policy


def _json_value(value, depth=0):
    _need(depth <= MAX_JSON_DEPTH, "publication_json_too_deep")
    if type(value) is float:
        _need(math.isfinite(value), "publication_nonfinite_json")
    elif type(value) is str:
        _need("\x00" not in value, "publication_null_character")
    elif type(value) is dict:
        for key, child in value.items():
            _json_value(key, depth + 1)
            _json_value(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _json_value(child, depth + 1)


def parse_publication_body(body_utf8):
    _need(type(body_utf8) is str, "invalid_publication_body")
    try:
        raw = body_utf8.encode("utf-8")
        _need(0 < len(raw) <= MAX_BODY_BYTES, "invalid_publication_body_size")

        def pairs(rows):
            value = {}
            for key, child in rows:
                _need(key not in value, "publication_duplicate_json_key")
                value[key] = child
            return value
        value = json.loads(body_utf8, object_pairs_hook=pairs)
        _json_value(value)
    except (UnicodeError, RecursionError, OverflowError):
        raise ValueError("invalid_publication_body") from None
    return value, raw


def validate_observation(observation, recorded_utc):
    _keys(observation, {"version", "policy", "observed_started_utc", "observed_completed_utc", "http_status", "body_utf8"})
    _need(type(observation["version"]) is int and observation["version"] == 1
          and type(observation["http_status"]) is int and observation["http_status"] == 200,
          "invalid_publication_http_observation")
    validate_policy(observation["policy"])
    _need(instant(observation["observed_started_utc"]) <= instant(observation["observed_completed_utc"])
          <= instant(recorded_utc), "invalid_publication_clock_order")
    return parse_publication_body(observation["body_utf8"])


def validate_publication_attestation(record):
    """Validate a compact stored record's shape/hash; authority needs DB joins."""
    _keys(record, RECORD_KEYS)
    _need(type(record["version"]) is int and record["version"] == 1 and record["kind"] == KIND
          and record["scope"] == "public_pool_http_evidence_only" and record["durable"] is True,
          "invalid_publication_attestation")
    for key in ("object_id", "generation_id", "source_pool_id", "policy_id", "attestation_id"):
        _hash(record[key])
    validate_policy(record["policy"])
    _need(record["policy_id"] == fingerprint(record["policy"]), "publication_policy_hash_mismatch")
    obs = record["observation"]
    _keys(obs, {"observed_started_utc", "observed_completed_utc", "http_status", "body_sha256", "body_bytes"})
    _hash(obs["body_sha256"])
    _need(type(obs["http_status"]) is int and obs["http_status"] == 200
          and type(obs["body_bytes"]) is int and 0 < obs["body_bytes"] <= MAX_BODY_BYTES,
          "invalid_publication_observation_metadata")
    _need(instant(obs["observed_started_utc"]) <= instant(obs["observed_completed_utc"])
          <= instant(record["recorded_utc"]), "invalid_publication_clock_order")
    _need(type(record["receipts"]) is list and type(record["rejections"]) is list,
          "invalid_publication_partition")
    source_ids, receipt_ids = set(), set()
    for rows, accepted in ((record["receipts"], True), (record["rejections"], False)):
        order = []
        for row in rows:
            _keys(row, ROW_KEYS if accepted else {"receipt_id", "source_receipt_id", "reason"})
            rid, sid = _hash(row["receipt_id"]), _hash(row["source_receipt_id"])
            _need(rid not in receipt_ids and sid not in source_ids, "duplicate_publication_member")
            receipt_ids.add(rid); source_ids.add(sid); order.append(sid)
            if accepted:
                _need(row["market"] in MARKETS and row["direction"] == "over"
                      and finite_number(row["served_prob"]) and 0 < row["served_prob"] < 1,
                      "invalid_publication_receipt")
                _need(instant(obs["observed_completed_utc"]) < instant(row["event_start_utc"])
                      and instant(record["recorded_utc"]) < instant(row["event_start_utc"]),
                      "publication_not_preevent")
            else:
                _need(row["reason"] == "publication_not_preevent", "invalid_publication_rejection")
        _need(order == sorted(order), "noncanonical_publication_members")
    status = ("accepted" if record["receipts"] and not record["rejections"] else "partial" if record["receipts"]
              else "withheld" if record["rejections"] else "no_supported_membership")
    _need(record["status"] == status, "invalid_publication_status")
    _need(record["attestation_id"] == fingerprint({k: v for k, v in record.items() if k != "attestation_id"}),
          "publication_attestation_hash_mismatch")
    return record


def build_publication_attestation(journal, object_id, observation, recorded_utc):
    """Join real observed pool bytes to finalized original receipt identities."""
    _hash(object_id)
    body, raw = validate_observation(observation, recorded_utc)
    doc = validate_generation(journal, object_id, now_utc=recorded_utc)
    generation = doc["generations"][0]
    _need(instant(generation["recorded_utc"]) <= instant(observation["observed_started_utc"]),
          "publication_before_generation")
    source_pool_id = generation["source_snapshots"]["leg_pool"]
    _need(fingerprint(body) == source_pool_id, "publication_pool_hash_mismatch")
    ack = journal.generation_ack(object_id)
    _keys(ack, {"version", "object_id", "durable", "receipts", "rejections"})
    _need(type(ack["version"]) is int and ack["version"] == 1 and ack["object_id"] == object_id
          and ack["durable"] is True and type(ack["receipts"]) is list and type(ack["rejections"]) is list,
          "publication_generation_not_finalized")
    accepted = {}
    for row in ack["receipts"]:
        _keys(row, {"receipt_id", "source_receipt_id", "status"})
        _need(row["status"] in ("recorded", "duplicate"), "invalid_publication_generation_ack")
        sid, rid = _hash(row["source_receipt_id"]), _hash(row["receipt_id"])
        _need(sid not in accepted and rid not in accepted.values(), "invalid_publication_generation_ack")
        accepted[sid] = rid
    rejected = []
    for row in ack["rejections"]:
        _keys(row, {"source_receipt_id", "reason"})
        rejected.append(_hash(row["source_receipt_id"]))
    expected = {row["receipt_id"] for row in doc["receipts"]}
    _need(len(set(list(accepted) + rejected)) == len(accepted) + len(rejected)
          and set(accepted) | set(rejected) == expected, "invalid_publication_generation_partition")
    reproduced = adapt_prospective(journal_docs=[doc], existing_receipts=journal.receipts())
    mapping = {row["source_receipt_id"]: row["receipt_id"] for row in reproduced["receipts"]}
    _need(mapping == accepted, "publication_generic_receipt_mismatch")
    original = {row["receipt_id"]: row for row in doc["receipts"]}
    generic = {row["receipt_id"]: row for row in journal.receipts()}
    receipts, rejections = [], []
    for sid, rid in sorted(accepted.items()):
        _need(rid in generic and generic[rid]["source_receipt_id"] == sid, "publication_receipt_missing")
        source = original[sid]["learning_receipt"]
        identity = {"receipt_id": rid, "source_receipt_id": sid}
        kickoff = generic[rid]["event_start_utc"]
        if (instant(observation["observed_completed_utc"]) >= instant(kickoff)
                or instant(recorded_utc) >= instant(kickoff)):
            rejections.append({**identity, "reason": "publication_not_preevent"})
        else:
            receipts.append({**identity, "market": source["market"], "direction": source["direction"],
                             "served_prob": generic[rid]["served_prob"], "event_start_utc": kickoff})
    record = {"version": 1, "kind": KIND, "object_id": object_id, "generation_id": generation["generation_id"],
        "source_pool_id": source_pool_id, "policy": dict(observation["policy"]), "policy_id": fingerprint(observation["policy"]),
        "recorded_utc": recorded_utc, "observation": {k: observation[k] for k in
            ("observed_started_utc", "observed_completed_utc", "http_status")},
        "receipts": receipts, "rejections": rejections,
        "status": "accepted" if receipts and not rejections else "partial" if receipts else "withheld" if rejections else "no_supported_membership",
        "scope": "public_pool_http_evidence_only", "durable": True}
    record["observation"].update(body_sha256="sha256:" + hashlib.sha256(raw).hexdigest(), body_bytes=len(raw))
    record["attestation_id"] = fingerprint(record)
    return validate_publication_attestation(record)
