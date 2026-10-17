"""Read-only, whole-group CAPACITY planning, not an executable application.

The complete trusted journal and raw bytes are independently bound ONCE before
partitioning. No receipt subset, caller-normalized label or parsed-index flag is
an input. Existing delivery/SQL bounds remain unchanged. The prospective wire
model names NO implemented RPC; execution needs a separately authorized sealed
manifest/batch-result protocol, fresh CAS and group-closure validation.

No resources are opened here. Supplied journal/read callbacks may do caller-owned
I/O and propagate failures. A partial preview is never a whole-capture ACK,
unbiased evaluation cohort, provider authentication or model adoption.
"""
from __future__ import annotations

import copy
import json
import re

from . import nfl_capture_evidence as capture
from .nfl_capture_delivery_job import _identity, _pointer_reader, _request
from .nfl_capture_delivery_journal import make_capture_delivery_binding
from .nfl_capture_job import _PinnedJournal
from .nfl_capture_labels import POLICY, bind_capture_labels
from .nfl_observed_job import _now, _sequence
from .nfl_observed_resolution import resolve_observed
from ..probability_receipts import fingerprint, instant
from ..store.supabase_journal import _revision_envelope

PLANNER_POLICY = "whole_underlying_groups_capacity_v1"
CAPACITY_MODEL = "prospective_application_rpc_v1_not_implemented"
MAX_MEMBERS = 10000
MAX_GROUPS = 2000
MAX_MANIFEST_BYTES = 3 * 1024 * 1024
MAX_BATCH_ITEMS = 500
MAX_BATCH_BYTES = 8 * 1024 * 1024
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
_MEMBERSHIP_KEYS = ("scope", "receipt_ids", "source_receipt_ids", "generation_object_ids")
_GROUP_KEYS = {"group_id", "membership_id", "status", "reason_codes", *_MEMBERSHIP_KEYS}


class CapturePlanError(ValueError):
    """No plan is returned after unlocatable authority/work-limit failure."""


class _WireLimit(CapturePlanError):
    pass


def _cap(value, maximum):
    if type(value) is not int or not 1 <= value <= maximum:
        raise CapturePlanError("invalid_capture_plan_limit")
    return value


def _encode(value, maximum, *, outer=False):
    # Bound incremental encoding instead of allocating an unbounded serialized
    # body merely to discover that a single whole group does not fit.
    encoder = json.JSONEncoder(sort_keys=True, ensure_ascii=True, allow_nan=False,
                              separators=(", ", " : ") if outer else (",", ":"))
    pieces, total = [], 0
    for piece in encoder.iterencode(value):
        total += len(piece.encode("utf-8"))
        if total > maximum:
            raise _WireLimit("capture_plan_encoded_work_limit")
        pieces.append(piece)
    return "".join(pieces)


def _ids(value, maximum=MAX_MEMBERS):
    if (type(value) is not list or not 1 <= len(value) <= maximum
            or any(type(item) is not str or not _HASH.fullmatch(item) for item in value)
            or value != sorted(set(value))):
        raise CapturePlanError("invalid_capture_plan_membership_ids")


def _group(value):
    if type(value) is not dict or set(value) != _GROUP_KEYS:
        raise CapturePlanError("invalid_capture_plan_group")
    scope = value["scope"]
    if (type(scope) is not list or len(scope) != 5 or type(scope[0]) is not int
            or any(type(item) is not str or not item for item in scope[1:])
            or scope[3] not in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds") or scope[4] not in ("home", "away")
            or value["group_id"] != fingerprint(scope)):
        raise CapturePlanError("invalid_capture_plan_group_scope")
    for key in ("receipt_ids", "source_receipt_ids", "generation_object_ids"):
        _ids(value[key])
    if len(value["receipt_ids"]) != len(value["source_receipt_ids"]):
        raise CapturePlanError("capture_plan_member_mapping_mismatch")
    if value["membership_id"] != fingerprint({"binding_policy": POLICY, **{key: value[key] for key in _MEMBERSHIP_KEYS}}):
        raise CapturePlanError("capture_plan_membership_hash_mismatch")
    reasons = value["reason_codes"]
    if (value["status"] not in ("staged", "withheld") or type(value["status"]) is not str
            or type(reasons) is not list or len(reasons) > 100 or reasons != sorted(set(reasons))
            or any(type(reason) is not str or not _REASON.fullmatch(reason) for reason in reasons)
            or (value["status"] == "staged") != (not reasons)):
        raise CapturePlanError("invalid_capture_plan_group_status")


def _payload(groups, observations, proposals):
    return {"version": 1, "group_ids": [g["group_id"] for g in groups],
            "observations": observations, "revisions": [_revision_envelope(**row) for row in proposals]}


def _wire(groups, observations, proposals, limit):
    payload = _payload(groups, observations, proposals)
    encoded = _encode(payload, limit)
    # Placeholder values are conservative capacity inputs, NOT a callable RPC.
    # SQL json_build_object's spaced outer representation is at least as large
    # as the current Python transport's default JSON spacing. Fixed SHA length,
    # maximum ordinal and 19-digit fence prevent optimistic metadata estimates.
    wire = {"p_application_id": "sha256:" + "0" * 64, "p_batch_ordinal": MAX_GROUPS,
            "p_batch_json": encoded, "p_watermark": "9223372036854775807"}
    outer = _encode(wire, limit, outer=True)
    return payload, len(outer.encode("utf-8"))


def _preview(status, *, manifest=None, candidates=None, original_result=None):
    return {"version": 1, "kind": "nfl.capture_application_preview.v1", "status": status,
            "manifest": manifest, "candidates": candidates or [], "original_result": original_result,
            "scope": "offline_planning_only", "durable_ack": False, "execution_ready": False,
            "source_authentication": "not_performed"}


def plan_capture_application(journal, request, raw_chunks, *, clock=_now, max_batch_members=500,
                             max_batch_observations=500, max_batch_revisions=500,
                             max_batch_bytes=MAX_BATCH_BYTES, max_total_bytes=capture.DEFAULT_MAX_TOTAL_BYTES):
    """One full source/cohort bind, deterministic complete-group packing.

    The returned input fence is an AS-OF snapshot, not a lock or registration.
    A future executor must never send these candidates to the current whole-
    capture RPC. Membership changes require explicit immutable application
    handling, not silent reinterpretation of an original delivery/result.
    """
    limits = {"members": _cap(max_batch_members, MAX_BATCH_ITEMS),
              "observations": _cap(max_batch_observations, MAX_BATCH_ITEMS),
              "revisions": _cap(max_batch_revisions, MAX_BATCH_ITEMS),
              "rpc_bytes": _cap(max_batch_bytes, MAX_BATCH_BYTES)}
    if (not callable(clock) or not callable(raw_chunks) or type(max_total_bytes) is not int
            or not 0 <= max_total_bytes <= capture.DEFAULT_MAX_TOTAL_BYTES
            or any(not callable(getattr(journal, key, None)) for key in
                   ("get_capture_delivery", "read_observed_snapshot", "get_object"))):
        raise CapturePlanError("invalid_capture_plan_boundary")
    frozen = _request(request)
    old = journal.get_capture_delivery(frozen["delivery_id"])
    if old is not None:
        return _preview("original_delivery_exists", original_result=copy.deepcopy(_identity(old, frozen)))
    processing = clock()
    instant(processing)
    snapshot = copy.deepcopy(journal.read_observed_snapshot())
    if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or any(type(snapshot.get(key)) is not list for key in
                   ("receipts", "generation_records", "observations", "observation_records", "revisions"))):
        raise CapturePlanError("invalid_capture_plan_snapshot")
    _sequence(snapshot.get("watermark"))
    staged = bind_capture_labels(_PinnedJournal(journal, snapshot), frozen["envelope"], _pointer_reader(frozen, raw_chunks),
                                 max_total_bytes=max_total_bytes)
    if staged["membership_watermark"] != snapshot["watermark"]:
        raise CapturePlanError("capture_plan_binding_fence_mismatch")
    originals = staged["groups"]
    if type(originals) is not list or len(originals) > MAX_GROUPS:
        raise CapturePlanError("capture_plan_group_work_limit")
    owners, source_ids, group_ids = {}, set(), set()
    for group in originals:
        _group(group)
        if group["group_id"] in group_ids or source_ids.intersection(group["source_receipt_ids"]):
            raise CapturePlanError("duplicate_capture_plan_group_or_source")
        group_ids.add(group["group_id"]); source_ids.update(group["source_receipt_ids"])
        for rid in group["receipt_ids"]:
            if rid in owners:
                raise CapturePlanError("duplicate_capture_plan_member")
            owners[rid] = group["group_id"]
    if len(owners) > MAX_MEMBERS:
        raise CapturePlanError("capture_plan_member_work_limit")
    if staged["membership_id"] != fingerprint({"binding_policy": POLICY, "groups": [g["membership_id"] for g in originals]}):
        raise CapturePlanError("capture_plan_population_hash_mismatch")
    resolved = resolve_observed(snapshot["receipts"], staged["source_receipts"], staged["observed_legs"],
        staged["game_observations"], observed_utc=processing, existing_revisions=snapshot["revisions"],
        existing_observations=snapshot["observations"])
    obs_by_group, rev_by_group = {}, {}
    seen_ids, seen_receipts = set(), set()
    for row in resolved["observation_proposals"]:
        if (type(row) is not dict or row.get("receipt_id") not in owners or type(row.get("observation_id")) is not str
                or not _HASH.fullmatch(row["observation_id"]) or row["observation_id"] in seen_ids or row["receipt_id"] in seen_receipts):
            raise CapturePlanError("unlocatable_or_duplicate_capture_plan_proof")
        seen_ids.add(row["observation_id"]); seen_receipts.add(row["receipt_id"])
        obs_by_group.setdefault(owners[row["receipt_id"]], []).append(row)
    seen_ids, seen_receipts = set(), set()
    for row in resolved["proposals"]:
        if (type(row) is not dict or type(row.get("resolution")) is not dict or row["resolution"].get("receipt_id") not in owners
                or type(row.get("revision_id")) is not str or not row["revision_id"] or len(row["revision_id"]) > 200
                or row["revision_id"] in seen_ids or row["resolution"]["receipt_id"] in seen_receipts):
            raise CapturePlanError("unlocatable_or_duplicate_capture_plan_revision")
        seen_ids.add(row["revision_id"]); seen_receipts.add(row["resolution"]["receipt_id"])
        rev_by_group.setdefault(owners[row["resolution"]["receipt_id"]], []).append(row)
    manifest_groups, ready = [], []
    for original in sorted(originals, key=lambda group: group["group_id"]):
        gid = original["group_id"]
        obs = sorted(obs_by_group.get(gid, []), key=lambda row: row["receipt_id"])
        rev = sorted(rev_by_group.get(gid, []), key=lambda row: row["resolution"]["receipt_id"])
        reasons = set(original["reason_codes"])
        if len(original["receipt_ids"]) > limits["members"]:
            reasons.add("complete_group_exceeds_member_limit")
        elif len(obs) > limits["observations"] or len(rev) > limits["revisions"]:
            reasons.add("complete_group_exceeds_proof_limit")
        group = {**copy.deepcopy(original), "status": "withheld"}
        if not reasons:
            projection = {"version": 1, "kind": "nfl.capture_labels.v1", "binding_policy": POLICY,
                "season": staged["season"], "weeks": staged["weeks"], "groups": [original],
                "membership_id": fingerprint({"binding_policy": POLICY, "groups": [original["membership_id"]]})}
            try:
                bound, obs, rev = make_capture_delivery_binding(projection, obs, rev)
                group = bound["groups"][0]
                if group["status"] == "accepted":
                    _wire([group], obs, rev, limits["rpc_bytes"])
                    group["status"] = "planned"
                    ready.append((group, obs, rev))
                else:
                    reasons.update(group["reason_codes"])
            except _WireLimit:
                reasons.add("complete_group_exceeds_byte_limit")
            except ValueError as exc:
                if str(exc) != "capture_delivery_exceeds_byte_limit":
                    raise
                reasons.add("complete_group_exceeds_byte_limit")
        group.update(status="withheld" if reasons else "planned", reason_codes=sorted(reasons))
        if reasons:
            group.update(observation_ids=[], revision_ids=[])
        manifest_groups.append(group)
    batches, candidates, current, current_obs, current_rev = [], [], [], [], []

    def flush():
        if not current:
            return
        payload, wire_bytes = _wire(current, current_obs, current_rev, limits["rpc_bytes"])
        payload_id = fingerprint(payload)
        batch_id = fingerprint({"version": 1, "kind": "nfl.capture_application_batch_plan.v1", "request_id": fingerprint(frozen),
            "source_watermark": snapshot["watermark"], "planner_policy": PLANNER_POLICY,
            "group_memberships": [g["membership_id"] for g in current], "payload_id": payload_id})
        batches.append({"ordinal": len(batches), "batch_id": batch_id, "group_ids": [g["group_id"] for g in current],
            "member_count": sum(len(g["receipt_ids"]) for g in current), "observation_count": len(current_obs),
            "revision_count": len(current_rev), "rpc_bytes": wire_bytes, "payload_id": payload_id})
        candidates.append({"batch_id": batch_id, "payload": payload, "capacity_model": CAPACITY_MODEL})

    for group, obs, rev in ready:
        joined = current + [group]
        fits = (sum(len(g["receipt_ids"]) for g in joined) <= limits["members"]
                and len(current_obs) + len(obs) <= limits["observations"] and len(current_rev) + len(rev) <= limits["revisions"])
        if fits:
            try:
                _wire(joined, current_obs + obs, current_rev + rev, limits["rpc_bytes"])
            except _WireLimit:
                fits = False
        if not fits:
            flush()
            current, current_obs, current_rev = [], [], []
        current.append(group); current_obs.extend(obs); current_rev.extend(rev)
    flush()
    held = [g["group_id"] for g in manifest_groups if g["status"] == "withheld"]
    status = "no_supported_membership" if not manifest_groups else "withheld" if not batches else "partial" if held else "planned"
    manifest = {"version": 1, "kind": "nfl.capture_application_plan.v1", "delivery_id": frozen["delivery_id"],
        "request_id": fingerprint(frozen), "capture_id": frozen["capture_id"], "binding_policy": POLICY,
        "planner_policy": PLANNER_POLICY, "capacity_model": CAPACITY_MODEL, "source_watermark": snapshot["watermark"],
        "membership_id": staged["membership_id"], "planned_utc": processing, "limits": limits,
        "groups": manifest_groups, "batches": batches, "held_group_ids": held,
        "counts": {"members": len(owners), "groups": len(manifest_groups), "batches": len(batches),
                   "planned_members": sum(len(g["receipt_ids"]) for g in manifest_groups if g["status"] == "planned"),
                   "withheld_members": sum(len(g["receipt_ids"]) for g in manifest_groups if g["status"] == "withheld")},
        "scope": "offline_planning_only", "durable_ack": False, "execution_ready": False,
        "source_authentication": "not_performed"}
    manifest["plan_id"] = fingerprint(manifest)
    _encode(manifest, MAX_MANIFEST_BYTES)
    return _preview(status, manifest=manifest, candidates=candidates)
