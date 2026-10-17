"""Domain-neutral, bounded job protocol. No scheduler, IO, fitting or activation.

Hashes bind content, not callers. A durable acknowledgement records an original
transition; it does not renew an expired lease or establish current ownership.
Backends own transition clocks. Side effects outside this journal are NOT made
exactly-once by a lease; workers must keep computation pure/idempotent and commit
its immutable result only through the fenced transition.
"""
from __future__ import annotations

import datetime as dt
import json
import re

from .probability_receipts import fingerprint, instant

MAX_EVENTS, MAX_DOCUMENT_BYTES, MAX_HISTORY_BYTES = 128, 16384, 262144
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
CLOCK = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9](?:\.[0-9]{1,6})?(?:Z|[+-](?:0[0-9]|1[0-5]):[0-5][0-9])\Z")
SPEC_KEYS = {"version", "kind", "namespace", "operation", "input_object_id", "policy_object_id", "code_id",
             "not_before_utc", "not_after_utc", "lease_seconds", "max_runtime_seconds", "max_attempts", "max_steps", "job_id", "definition_id"}
IDENTITY_KEYS = frozenset(("namespace", "operation", "input_object_id", "policy_object_id", "code_id"))
COMMAND_KEYS = {"version", "kind", "job_id", "expected_head_event_id", "action", "worker_id", "lease_id",
                "object_id", "reason_code", "command_id"}
STATE_KEYS = {"status", "attempt", "steps", "worker_id", "lease_id", "lease_until_utc", "started_utc",
              "runtime_deadline_utc", "checkpoint_object_id", "result_object_id", "reason_code"}
REASONS = frozenset(("paused", "worker_error", "cancelled", "invalid_input", "evaluation_failed"))


class JobConflict(ValueError):
    """Static transition refusal; never provider diagnostics or uploaded prose."""


def need(value, reason="invalid_learning_job"):
    if not value:
        raise ValueError(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def snapshot(value, *, limit=MAX_DOCUMENT_BYTES):
    """Bound plain ASCII protocol metadata BEFORE serialization, then detach it.

    Arbitrary evidence/metrics are stored as referenced source objects, never in
    job metadata. Restricting this protocol to ASCII avoids Unicode expansion,
    free-text/secret diagnostics and SQL/Python canonicalization disagreements.
    """
    need(type(limit) is int and 0 < limit <= MAX_HISTORY_BYTES)
    stack, nodes, used = [(value, 0)], 0, 0
    while stack:
        item, depth = stack.pop();nodes += 1
        need(nodes <= 10000 and depth <= 12, "learning_job_work_limit")
        if type(item) is dict:
            need(len(item) <= 128 and all(type(k) is str for k in item))
            used += 2 + max(0, len(item) - 1) + len(item)
            stack.extend((v, depth + 1) for pair in item.items() for v in pair)
        elif type(item) is list:
            need(len(item) <= MAX_EVENTS)
            used += 2 + max(0, len(item) - 1);stack.extend((v, depth + 1) for v in item)
        elif type(item) is str:
            need(len(item) <= limit and item.isascii() and all(32 <= ord(c) <= 126 for c in item))
            used += 2 + len(item) + item.count('"') + item.count('\\')
        else:
            need(item is None or type(item) in (int, bool))
            if type(item) is int:need(abs(item) <= 9223372036854775807)
            used += len(canonical(item))
        need(used <= limit, "learning_job_byte_limit")
    return json.loads(canonical(value))


def digest(value):
    need(type(value) is str and HASH.fullmatch(value))
    return value


def name(value):
    need(type(value) is str and NAME.fullmatch(value))
    return value


def namespace(value):
    need(type(value) is dict and set(value) == {"domain", "task", "producer_id"})
    for v in value.values():name(v)
    return dict(value)


def clock(value):
    need(type(value) is str and len(value) <= 40 and CLOCK.fullmatch(value))
    parsed = instant(value);need(2000 <= parsed.year <= 2100)
    return parsed


def stamp(value):
    return value.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def code_identity():
    """Conservative local package-source identity, NOT execution authentication.

    Includes every local selflearn-core Python file, not a handpicked import
    list. Interpreter labels are included, but interpreter/stdlib binaries,
    dependencies outside this package and hosted execution are not attested.
    Calling the function is explicit IO; importing this module remains inert.
    """
    import hashlib
    import os
    from pathlib import Path
    import platform
    import stat
    import sys
    root = Path(__file__).resolve().parent
    sources, total, directories = [], 0, 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        directories += 1;need(directories <= 1024, "learning_job_code_limit")
        need(all(not (Path(directory) / p).is_symlink() for p in dirs), "learning_job_code_symlink")
        for filename in sorted(files):
            if not filename.endswith(".py"):continue
            path = Path(directory) / filename
            need(not path.is_symlink(), "learning_job_code_symlink")
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
            with os.fdopen(fd, "rb") as handle:
                need(stat.S_ISREG(os.fstat(handle.fileno()).st_mode), "learning_job_code_not_regular")
                raw = handle.read(8 * 1024 * 1024 + 1)
            total += len(raw)
            need(len(raw) <= 8 * 1024 * 1024 and total <= 64 * 1024 * 1024 and len(sources) < 1024, "learning_job_code_limit")
            sources.append({"path": path.relative_to(root).as_posix(), "sha256": hashlib.sha256(raw).hexdigest()})
    return fingerprint({"profile": "selflearn.package_python_source.v1", "sources": sorted(sources,key=lambda r:r["path"]),
        "python_version": sys.version, "implementation": platform.python_implementation(), "machine": platform.machine(), "system": platform.system()})


def seal_spec(value, *, authority_id):
    value = snapshot(value);need(type(value) is dict and "job_id" not in value and "definition_id" not in value and IDENTITY_KEYS <= set(value))
    value["job_id"] = fingerprint({"version": 1, "kind": "selflearn.job_identity.v1",
        "authority_id": digest(authority_id), "scope": {k:value[k] for k in IDENTITY_KEYS}})
    value["definition_id"] = fingerprint({"version": 1, "kind": "selflearn.job_definition_identity.v1",
        "authority_id": authority_id, "definition": value})
    return validate_spec(value, authority_id=authority_id)


def validate_spec(value, *, authority_id):
    value = snapshot(value);digest(authority_id)
    need(type(value) is dict and set(value) == SPEC_KEYS)
    need(type(value["version"]) is int and value["version"] == 1 and value["kind"] == "selflearn.job_definition.v1")
    namespace(value["namespace"]);name(value["operation"])
    for key in ("input_object_id", "policy_object_id", "code_id", "job_id", "definition_id"):digest(value[key])
    need(clock(value["not_before_utc"]) < clock(value["not_after_utc"]))
    for key, maximum in (("lease_seconds", 300), ("max_runtime_seconds", 3600), ("max_attempts", 8), ("max_steps", 32)):
        need(type(value[key]) is int and 1 <= value[key] <= maximum)
    need(value["job_id"] == fingerprint({"version": 1, "kind": "selflearn.job_identity.v1",
        "authority_id": authority_id, "scope": {k:value[k] for k in IDENTITY_KEYS}}), "learning_job_identity_mismatch")
    need(value["definition_id"] == fingerprint({"version": 1, "kind": "selflearn.job_definition_identity.v1",
        "authority_id": authority_id, "definition": {k:v for k,v in value.items() if k != "definition_id"}}), "learning_job_definition_hash_mismatch")
    return value


def seal_command(value, *, authority_id):
    value = snapshot(value);need(type(value) is dict and "command_id" not in value)
    value["command_id"] = fingerprint({"version": 1, "kind": "selflearn.job_command_identity.v1",
        "authority_id": digest(authority_id), "command": value})
    return validate_command(value, authority_id=authority_id)


def validate_command(value, *, authority_id):
    value = snapshot(value);digest(authority_id)
    need(type(value) is dict and set(value) == COMMAND_KEYS)
    need(type(value["version"]) is int and value["version"] == 1 and value["kind"] == "selflearn.job_command.v1")
    digest(value["job_id"]);digest(value["command_id"]);name(value["worker_id"])
    if value["expected_head_event_id"] is not None:digest(value["expected_head_event_id"])
    action = value["action"];need(type(action) is str and action in ("claim", "checkpoint", "complete", "release", "fail"))
    if action == "claim":need(value["lease_id"] is None)
    else:digest(value["lease_id"])
    if action in ("checkpoint", "complete"):digest(value["object_id"])
    else:need(value["object_id"] is None)
    if action == "release":need(value["reason_code"] in ("paused", "worker_error", "cancelled"))
    elif action == "fail":need(value["reason_code"] in ("invalid_input", "evaluation_failed"))
    else:need(value["reason_code"] is None)
    unsigned = {k: v for k, v in value.items() if k != "command_id"}
    need(value["command_id"] == fingerprint({"version": 1, "kind": "selflearn.job_command_identity.v1",
        "authority_id": authority_id, "command": unsigned}), "learning_job_command_hash_mismatch")
    return value


def initial_state():
    return {"status": "queued", "attempt": 0, "steps": 0, "worker_id": None, "lease_id": None,
        "lease_until_utc": None, "started_utc": None, "runtime_deadline_utc": None,
        "checkpoint_object_id": None, "result_object_id": None, "reason_code": None}


def advance(spec, state, command, *, recorded_utc):
    """Pure transition. The store supplies its own clock and checks object refs."""
    now = clock(recorded_utc);out = snapshot(state);action = command["action"]
    if state["status"] in ("succeeded", "failed"):raise JobConflict("learning_job_terminal")
    if now < clock(spec["not_before_utc"]):raise JobConflict("learning_job_not_due")
    if now >= clock(spec["not_after_utc"]):raise JobConflict("learning_job_expired")
    if state["runtime_deadline_utc"] and now >= clock(state["runtime_deadline_utc"]):raise JobConflict("learning_job_runtime_limit")
    if action == "claim":
        if state["status"] == "running" and now < clock(state["lease_until_utc"]):raise JobConflict("learning_job_busy")
        if state["attempt"] >= spec["max_attempts"]:raise JobConflict("learning_job_attempt_limit")
        started = state["started_utc"] or recorded_utc
        deadline = state["runtime_deadline_utc"] or stamp(min(clock(spec["not_after_utc"]), clock(started) + dt.timedelta(seconds=spec["max_runtime_seconds"])))
        out.update(status="running", attempt=state["attempt"] + 1, worker_id=command["worker_id"], lease_id=command["command_id"],
            started_utc=started, runtime_deadline_utc=deadline, reason_code=None)
    else:
        if (state["status"] != "running" or command["lease_id"] != state["lease_id"]
                or command["worker_id"] != state["worker_id"] or now >= clock(state["lease_until_utc"])):
            raise JobConflict("learning_job_lease_conflict")
        if action in ("checkpoint", "complete"):
            # Reserve one step for completion; an all-checkpoint job must not
            # consume the final slot and become impossible to finish.
            if state["steps"] >= spec["max_steps"] - (1 if action == "checkpoint" else 0):raise JobConflict("learning_job_step_limit")
            out["steps"] += 1
            if action == "checkpoint":
                if command["object_id"] == state["checkpoint_object_id"]:raise JobConflict("learning_job_checkpoint_not_advanced")
                out["checkpoint_object_id"] = command["object_id"]
            else:out.update(status="succeeded", result_object_id=command["object_id"])
        elif action == "release":out.update(status="queued", reason_code=command["reason_code"])
        else:out.update(status="failed", reason_code=command["reason_code"])
    out["lease_until_utc"] = stamp(min(now + dt.timedelta(seconds=spec["lease_seconds"]), clock(out["runtime_deadline_utc"]))) if out["status"] == "running" else None
    if out["status"] != "running":out.update(worker_id=None, lease_id=None)
    return out


def make_event(spec, history, command, *, authority_id, registered_utc, recorded_utc):
    spec = validate_spec(spec, authority_id=authority_id);command = validate_command(command, authority_id=authority_id)
    need(command["job_id"] == spec["job_id"] and len(history) < MAX_EVENTS)
    previous = history[-1] if history else None
    if command["expected_head_event_id"] != (previous["event_id"] if previous else None):raise JobConflict("learning_job_head_conflict")
    need(clock(recorded_utc) >= clock(previous["recorded_utc"] if previous else registered_utc), "learning_job_clock_regressed")
    state = advance(spec, previous["state"] if previous else initial_state(), command, recorded_utc=recorded_utc)
    event = {"version": 1, "kind": "selflearn.job_event.v1", "authority_id": authority_id, "job_id": spec["job_id"], "definition_id": spec["definition_id"],
        "sequence": len(history) + 1, "previous_event_id": command["expected_head_event_id"], "command": command,
        "recorded_utc": recorded_utc, "state": state}
    event["event_id"] = fingerprint(event)
    snapshot({"definition": spec, "events": [*history, event]}, limit=MAX_HISTORY_BYTES)
    return event


def acknowledgement(event):
    return {"version": 1, "authority_id": event["authority_id"], "job_id": event["job_id"],
        "command_id": event["command"]["command_id"], "event_id": event["event_id"], "sequence": event["sequence"],
        "recorded_utc": event["recorded_utc"], "state": snapshot(event["state"]), "durable": True,
        "current_lease_authority": False, "activation": False}


def complete_snapshot(spec, history, *, authority_id, registered_utc, observed_utc):
    spec = validate_spec(spec, authority_id=authority_id);clock(registered_utc);observed = clock(observed_utc)
    history = snapshot(history, limit=MAX_HISTORY_BYTES);need(type(history) is list and len(history) <= MAX_EVENTS)
    checked, commands = [], set()
    for event in history:
        need(type(event) is dict and type(event.get("command")) is dict)
        need(event["command"].get("command_id") not in commands, "learning_job_duplicate_command")
        expected = make_event(spec, checked, event["command"], authority_id=authority_id,
            registered_utc=registered_utc, recorded_utc=event["recorded_utc"])
        need(canonical(expected) == canonical(event), "learning_job_event_mismatch")
        commands.add(event["command"]["command_id"]);checked.append(event)
    need(observed >= clock(checked[-1]["recorded_utc"] if checked else registered_utc), "learning_job_clock_regressed")
    state = checked[-1]["state"] if checked else initial_state();effective = state["status"]
    if effective not in ("succeeded", "failed"):
        if observed >= clock(spec["not_after_utc"]):effective = "expired"
        elif state["runtime_deadline_utc"] and observed >= clock(state["runtime_deadline_utc"]):effective = "runtime_limit"
        elif effective == "queued" and observed < clock(spec["not_before_utc"]):effective = "not_due"
        elif effective == "running" and observed >= clock(state["lease_until_utc"]):effective = "attempt_limit" if state["attempt"] >= spec["max_attempts"] else "expired_lease"
        elif effective == "queued" and state["attempt"] >= spec["max_attempts"]:effective = "attempt_limit"
    return snapshot({"version": 1, "authority_id": authority_id, "definition": spec, "registered_utc": registered_utc,
        "observed_utc": observed_utc, "events": checked, "head_event_id": checked[-1]["event_id"] if checked else None,
        "state": state, "effective_status": effective, "activation": False, "exactly_once_compute": False}, limit=MAX_HISTORY_BYTES)
