"""Domain-neutral owner-intent DTOs. Hashes are integrity checks, NOT signatures.

An intent records a decision, not adoption, statistical validation or serving
state. Referenced content is checked separately by the journal. No I/O occurs
here and no domain adapter is imported.
"""
from __future__ import annotations

import json
import re

from .probability_receipts import fingerprint, instant

MAX_COMMAND_BYTES = 128 * 1024
MAX_EVENTS = 256
MAX_PAGE_BYTES = 3 * 1024 * 1024
# Leave a fixed framing reserve for a complete history response (namespace,
# watermark, clocks, flags and hashes). Count and byte ceilings are independent.
MAX_HISTORY_BYTES = MAX_PAGE_BYTES - 8192
FLAGS = {"scope": "owner_intent_record_only", "content_hash_verified": True,
         "domain_pin_binding_verified": False, "current_serving_verified": False,
         "activation": False}
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEQ = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
CLOCK = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|[+-](?:0[0-9]|1[0-5]):[0-5][0-9])\Z")
JSON_ESCAPES = re.compile(r'["\\\x00-\x1f\x7f-\U0010ffff]')
PIN_KEYS = {"candidate_id", "artifact_id", "evaluation_report_id", "policy_id",
            "generation_context_id", "expected_previous_current_id", "evidence_object_ids"}
COMMAND_KEYS = {"version", "kind", "namespace", "action", "expected_head_event_id",
                "pins", "effective_from_utc", "expires_utc", "reason", "command_id"}
EVENT_KEYS = {"version", "kind", "authority_id", "actor_ref", "namespace", "sequence",
              "previous_event_id", "command", "recorded_utc", "effective_from_utc", "event_id"}
ACK_KEYS = {"version", "authority_id", "namespace", "command_id", "event_id", "sequence",
            "recorded_utc", "durable", *FLAGS}
PAGE_KEYS = {"version", "authority_id", "namespace", "rows", "next_after", "watermark",
             "head_event_id", "head_sequence", "observed_utc"}


def need(value, reason="invalid_owner_intent"):
    if not value:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _bound_json(value, *, limit):
    """Check canonical byte/work limits without constructing expanded JSON."""
    need(type(limit) is int and 0 < limit <= MAX_PAGE_BYTES, "owner_intent_byte_limit")
    count, used = 0, 0
    def charge(size):
        nonlocal used
        used += size
        need(used <= limit, "owner_intent_byte_limit")
    def visit(item, depth=0):
        nonlocal count
        count += 1
        need(depth <= 24 and count <= 50000, "owner_intent_work_limit")
        if type(item) is dict:
            need(len(item) <= 1024)
            charge(2 + max(0, len(item) - 1) + len(item))  # braces, commas, colons
            for key, child in item.items():
                need(type(key) is str and len(key) <= limit)
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif type(item) is list:
            need(len(item) <= 10000)
            charge(2 + max(0, len(item) - 1))
            for child in item:
                visit(child, depth + 1)
        elif type(item) is str:
            charge(2 + len(item))  # quoted ASCII lower bound, before encoding
            # Count ensure_ascii JSON expansion without constructing an escaped
            # string. Plain ASCII hashes use the regex's fast no-match path.
            for match in JSON_ESCAPES.finditer(item):
                char = match.group(); code = ord(char)
                need(code != 0 and not 0xD800 <= code <= 0xDFFF)
                charge(1 if char in '\\"\b\f\n\r\t' else 11 if code > 0xFFFF else 5)
        else:
            need(item is None or type(item) in (bool, int))
            if type(item) is int:
                need(abs(item) <= 9223372036854775807)
            charge(len(canonical(item)))
    visit(value)


def snapshot(value, *, limit=MAX_PAGE_BYTES):
    """Bound and detach strict JSON before hashing or invoking trusted callbacks."""
    _bound_json(value, limit=limit)
    encoded = canonical(value)
    need(len(encoded.encode("utf-8")) <= limit, "owner_intent_byte_limit")
    return json.loads(encoded)


def decode(text, *, limit=MAX_PAGE_BYTES):
    need(type(text) is str and len(text) <= limit, "invalid_owner_intent_json")
    need(len(text.encode("utf-8")) <= limit, "owner_intent_byte_limit")
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate_owner_intent_key")
            result[key] = value
        return result
    def reject(_):
        raise ValueError("nonfinite_owner_intent_json")
    try:
        result = json.loads(text, object_pairs_hook=pairs, parse_constant=reject)
        result = snapshot(result, limit=limit)
        need(canonical(result) == text, "noncanonical_owner_intent_json")
        return result
    except (RecursionError, OverflowError, UnicodeError):
        raise ValueError("invalid_owner_intent_json") from None


def digest(value):
    need(type(value) is str and HASH.fullmatch(value) is not None)
    return value


def namespace(value):
    need(type(value) is dict and set(value) == {"domain", "task", "producer_id"})
    need(all(type(v) is str and NAME.fullmatch(v) for v in value.values()))
    return dict(value)


def actor(value):
    need(type(value) is str and 1 <= len(value) <= 200 and value.isascii()
         and all(32 <= ord(c) <= 126 for c in value) and value.strip() == value)
    return value


def sequence(value):
    need(type(value) is str and SEQ.fullmatch(value) is not None)
    number = int(value)
    need(number <= 9223372036854775807)
    return number


def _clock(value):
    need(type(value) is str and len(value) <= 40 and CLOCK.fullmatch(value) is not None)
    result = instant(value)
    need(2000 <= result.year <= 2100)
    return result


def seal_command(payload, *, authority_id):
    need(type(payload) is dict and "command_id" not in payload)
    out = snapshot(payload, limit=MAX_COMMAND_BYTES)
    out["command_id"] = fingerprint({"version": 1, "kind": "selflearn.owner_intent_identity.v1",
                                     "authority_id": digest(authority_id), "command": out})
    return validate_command(out, authority_id=authority_id)


def validate_command(value, *, authority_id):
    value = snapshot(value, limit=MAX_COMMAND_BYTES)
    digest(authority_id)
    need(type(value) is dict and set(value) == COMMAND_KEYS)
    need(type(value["version"]) is int and value["version"] == 1
         and value["kind"] == "selflearn.owner_intent_command.v1")
    namespace(value["namespace"])
    need(value["action"] in ("approve", "revoke", "rollback"))
    if value["expected_head_event_id"] is not None:
        digest(value["expected_head_event_id"])
    pins = value["pins"]
    need(type(pins) is dict and set(pins) == PIN_KEYS)
    for key in PIN_KEYS - {"evidence_object_ids"}:
        digest(pins[key])
    refs = pins["evidence_object_ids"]
    need(type(refs) is list and 1 <= len(refs) <= 16)
    for ref in refs:
        digest(ref)
    need(refs == sorted(set(refs)), "duplicate_or_unsorted_owner_evidence")
    need(type(value["reason"]) is str and 1 <= len(value["reason"]) <= 1000
         and bool(value["reason"].strip()))
    if value["action"] == "revoke":
        need(value["effective_from_utc"] is None and value["expires_utc"] is None,
             "revocation_must_be_immediate_permanent")
    else:
        if value["effective_from_utc"] is not None:
            _clock(value["effective_from_utc"])
        end = _clock(value["expires_utc"])
        if value["effective_from_utc"] is not None:
            need(_clock(value["effective_from_utc"]) < end)
    digest(value["command_id"])
    unsigned = {k: v for k, v in value.items() if k != "command_id"}
    need(value["command_id"] == fingerprint({"version": 1, "kind": "selflearn.owner_intent_identity.v1",
         "authority_id": authority_id, "command": unsigned}), "owner_command_hash_mismatch")
    return value


def validate_event(value, *, authority_id, expected_namespace):
    value = snapshot(value, limit=MAX_COMMAND_BYTES + 4096)
    need(type(value) is dict and set(value) == EVENT_KEYS)
    need(type(value["version"]) is int and value["version"] == 1
         and value["kind"] == "selflearn.owner_intent_event.v1")
    need(value["authority_id"] == digest(authority_id)
         and canonical(namespace(value["namespace"])) == canonical(namespace(expected_namespace)), "owner_intent_scope_mismatch")
    actor(value["actor_ref"])
    need(type(value["sequence"]) is int and 1 <= value["sequence"] <= MAX_EVENTS)
    command = validate_command(value["command"], authority_id=authority_id)
    need(canonical(command["namespace"]) == canonical(value["namespace"])
         and command["expected_head_event_id"] == value["previous_event_id"])
    if value["previous_event_id"] is not None:
        digest(value["previous_event_id"])
    recorded, effective = _clock(value["recorded_utc"]), _clock(value["effective_from_utc"])
    need(recorded <= effective, "invalid_owner_event_clock")
    if command["effective_from_utc"] is None:
        need(effective == recorded)
    else:
        need(effective == _clock(command["effective_from_utc"]))
    if command["action"] != "revoke":
        need(effective < _clock(command["expires_utc"]), "expired_owner_command")
    need(value["event_id"] == fingerprint({k: v for k, v in value.items() if k != "event_id"}), "owner_event_hash_mismatch")
    return value


def validate_history(events, *, authority_id, expected_namespace):
    need(type(events) is list and len(events) <= MAX_EVENTS, "incomplete_owner_history")
    previous, clock, known, revoked = None, None, {}, set()
    for index, raw in enumerate(events, 1):
        event = validate_event(raw, authority_id=authority_id, expected_namespace=expected_namespace)
        need(event["sequence"] == index and event["previous_event_id"] == previous,
             "incomplete_owner_history")
        recorded = _clock(event["recorded_utc"])
        need(clock is None or clock <= recorded, "invalid_owner_event_clock")
        command = event["command"]; pins = command["pins"]; target = pins["artifact_id"]
        if command["action"] == "approve":
            known[target] = pins; revoked.discard(target)
        elif command["action"] == "revoke":
            revoked.add(target)
        else:
            need(target in known and target not in revoked and all(pins[k] == known[target][k]
                 for k in ("candidate_id", "artifact_id", "evaluation_report_id", "policy_id")), "rollback_intent_target_unavailable")
        previous, clock = event["event_id"], recorded
    return events


def make_event(command, history, *, authority_id, actor_ref, recorded_utc):
    command = validate_command(command, authority_id=authority_id)
    validate_history(history, authority_id=authority_id, expected_namespace=command["namespace"])
    need(len(history) < MAX_EVENTS, "owner_intent_capacity_exceeded")
    expected = history[-1]["event_id"] if history else None
    need(command["expected_head_event_id"] == expected, "owner_head_conflict")
    event = {"version": 1, "kind": "selflearn.owner_intent_event.v1", "authority_id": digest(authority_id),
        "actor_ref": actor(actor_ref), "namespace": command["namespace"], "sequence": len(history) + 1,
        "previous_event_id": expected, "command": command, "recorded_utc": recorded_utc,
        "effective_from_utc": command["effective_from_utc"] or recorded_utc}
    event["event_id"] = fingerprint(event)
    validate_history(history + [event], authority_id=authority_id, expected_namespace=command["namespace"])
    try:
        _bound_json(history + [event], limit=MAX_HISTORY_BYTES)
    except ValueError as exc:
        if str(exc) == "owner_intent_byte_limit":
            raise ValueError("owner_intent_capacity_exceeded") from None
        raise
    return event


def acknowledgement(event):
    return {"version": 1, "authority_id": event["authority_id"], "namespace": event["namespace"],
        "command_id": event["command"]["command_id"], "event_id": event["event_id"],
        "sequence": event["sequence"], "recorded_utc": event["recorded_utc"], "durable": True, **FLAGS}


def complete_snapshot(events, *, authority_id, expected_namespace, watermark, observed_utc):
    digest(authority_id)
    events = snapshot(events)
    validate_history(events, authority_id=authority_id, expected_namespace=expected_namespace)
    sequence(watermark); observed = _clock(observed_utc)
    need(all(_clock(e["recorded_utc"]) <= observed for e in events), "owner_snapshot_before_event")
    result = {"version": 1, "kind": "selflearn.owner_intent_snapshot.v1", "authority_id": authority_id,
        "namespace": namespace(expected_namespace), "events": events, "complete": True,
        "watermark": watermark, "head_event_id": events[-1]["event_id"] if events else None,
        "head_sequence": len(events), "observed_utc": observed_utc, **FLAGS}
    _bound_json(result, limit=MAX_PAGE_BYTES)
    result["snapshot_id"] = fingerprint(result)
    return snapshot(result)
