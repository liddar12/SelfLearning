"""Server-built sealed whole-group applications; no writes or activation.

Raw readers/journal ports are trusted server composition, never public subset
or normalized-label inputs. This prepares the v2 SQL contract independently of
the older offline capacity model. A seal is not an outcome commit/learning ACK.
"""
from __future__ import annotations

import copy
from datetime import timezone

from . import nfl_capture_application as capacity
from . import nfl_capture_evidence as capture
from . import nfl_capture_delivery_journal as delivery
from .nfl_capture_delivery_job import _pointer_reader
from .nfl_capture_job import _PinnedJournal
from .nfl_capture_labels import POLICY as BINDING_POLICY, bind_capture_labels
from .nfl_observed_job import _now
from .nfl_observed_resolution import resolve_observed
from ..probability_receipts import fingerprint, instant
from ..store.supabase_journal import _revision_envelope

EXECUTION_POLICY = "sealed_whole_groups_v2"
MAX_MEMBERS, MAX_GROUPS = 10000, 2000
MAX_REQUEST_BYTES = MAX_SEAL_BYTES = 3 * 1024 * 1024
MAX_RPC_BYTES, MAX_BATCH_ITEMS = 8 * 1024 * 1024, 500
MEMBERSHIP_KEYS = ("scope", "receipt_ids", "source_receipt_ids", "generation_object_ids")
FACT_KEYS = ("week", "actual", "played", "status", "reason", "participation_basis", "game")


def bounded(value, maximum):
    return capacity._encode(value, maximum)


def sequence(value):
    result = delivery._sequence(value)
    if result > 9223372036854775807:
        raise ValueError("invalid_capture_application_sequence")
    return result


def make_request(capture_request):
    original = delivery._request(copy.deepcopy(capture_request))
    identity = {"version": 2, "kind": "nfl.capture_application.v2",
                "capture_intent_id": original["delivery_id"], "execution_policy": EXECUTION_POLICY}
    value = {"version": 2, "kind": "nfl.capture_application_request.v2",
             "execution_policy": EXECUTION_POLICY, "capture_request": original,
             "application_id": fingerprint(identity)}
    bounded(value, MAX_REQUEST_BYTES)
    return value


def validate_request(value):
    if type(value) is not dict or set(value) != {"version", "kind", "execution_policy", "capture_request", "application_id"}:
        raise ValueError("invalid_capture_application_request")
    expected = make_request(value["capture_request"])
    if bounded(expected, MAX_REQUEST_BYTES) != bounded(value, MAX_REQUEST_BYTES):
        raise ValueError("invalid_capture_application_request")
    return expected


def step_id(application_id, ordinal):
    delivery._hash(application_id)
    if type(ordinal) is not int or not 0 <= ordinal < MAX_GROUPS:
        raise ValueError("invalid_capture_application_ordinal")
    return fingerprint({"version": 2, "kind": "nfl.capture_application_step.v2",
                        "application_id": application_id, "ordinal": ordinal})


def source_vector_id(envelope):
    """Content/observed-instant vector, not provider-authentication evidence."""
    _manifest, sources, _objects = capture._preflight(envelope, capture.DEFAULT_MAX_TOTAL_BYTES)
    entries, seen = [], set()
    for _oid, source in sources:
        key = (source["profile"], source["season"], source["week"])
        if key in seen:
            raise ValueError("duplicate_capture_application_source")
        seen.add(key)
        clock = instant(source["observed_utc"]).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        entries.append([*key, source["raw_sha256"], source["raw_bytes"], clock])
    entries.sort(key=lambda row: (row[0], row[1], -1 if row[2] is None else row[2]))
    return fingerprint(entries)


def membership_id(groups):
    """v2 global order is group-ID order, not v1 binder scope order."""
    return fingerprint({"binding_policy": BINDING_POLICY,
                        "groups": [group["membership_id"] for group in sorted(groups, key=lambda group: group["group_id"])]})


def common_fact(observation):
    measurement = observation["measurement"]
    return {**{key: copy.deepcopy(measurement[key]) for key in FACT_KEYS},
            "source_observations": copy.deepcopy(observation["source_observations"])}


def batch_wire(application_id, ordinal, groups, observations, proposals, watermark, *, maximum=MAX_RPC_BYTES):
    """Actual RPC field names and escaped outer JSON, not a placeholder API."""
    data = {"p_application_id": application_id, "p_step_id": step_id(application_id, ordinal),
            "p_group_results_json": bounded(groups, MAX_SEAL_BYTES),
            "p_batch_json": bounded({"version": 1, "observations": observations,
                                      "revisions": [_revision_envelope(**row) for row in proposals]}, MAX_RPC_BYTES),
            "p_watermark": watermark}
    # SupabaseStore's json.dumps default spacing is bounded conservatively by
    # the existing spaced encoder. IDs/actual values remain the real v2 wire.
    return data, len(capacity._encode(data, maximum, outer=True).encode("utf-8"))


def _snapshot(journal):
    snapshot = copy.deepcopy(journal.read_observed_snapshot())
    if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or any(type(snapshot.get(key)) is not list for key in
                   ("receipts", "generation_records", "observations", "observation_records", "revisions"))):
        raise ValueError("invalid_capture_application_snapshot")
    sequence(snapshot.get("watermark"))
    return snapshot


def bind_context(journal, request, raw_chunks, *, clock=_now, max_total_bytes=capture.DEFAULT_MAX_TOTAL_BYTES):
    """Re-read COMPLETE authority and reverify raw input; never accept a subset."""
    if (not callable(clock) or not callable(raw_chunks) or type(max_total_bytes) is not int
            or not 0 <= max_total_bytes <= capture.DEFAULT_MAX_TOTAL_BYTES
            or any(not callable(getattr(journal, key, None)) for key in ("read_observed_snapshot", "get_object"))):
        raise ValueError("invalid_capture_application_boundary")
    request = validate_request(request)
    original = request["capture_request"]
    processing = clock()
    instant(processing)
    snapshot = _snapshot(journal)
    staged = bind_capture_labels(_PinnedJournal(journal, snapshot), original["envelope"],
                                 _pointer_reader(original, raw_chunks), max_total_bytes=max_total_bytes)
    if staged["membership_watermark"] != snapshot["watermark"]:
        raise ValueError("capture_application_binding_fence_mismatch")
    groups = staged["groups"]
    if type(groups) is not list or len(groups) > MAX_GROUPS:
        raise ValueError("capture_application_group_work_limit")
    owners, seen_groups, seen_sources = {}, set(), set()
    for group in groups:
        capacity._group(group)
        if any(len(value) > 200 for value in group["scope"][1:]):
            raise ValueError("capture_application_scope_text_work_limit")
        if group["group_id"] in seen_groups or seen_sources.intersection(group["source_receipt_ids"]):
            raise ValueError("duplicate_capture_application_authority")
        seen_groups.add(group["group_id"])
        seen_sources.update(group["source_receipt_ids"])
        for rid in group["receipt_ids"]:
            if rid in owners:
                raise ValueError("duplicate_capture_application_member")
            owners[rid] = group["group_id"]
    if len(owners) > MAX_MEMBERS or staged["membership_id"] != fingerprint(
            {"binding_policy": BINDING_POLICY, "groups": [g["membership_id"] for g in groups]}):
        raise ValueError("invalid_capture_application_membership")
    resolved = resolve_observed(snapshot["receipts"], staged["source_receipts"], staged["observed_legs"],
        staged["game_observations"], observed_utc=processing, existing_revisions=snapshot["revisions"],
        existing_observations=snapshot["observations"])
    observations, proposals = {}, {}
    for key, rows, identity in (("observations", resolved["observation_proposals"], "observation_id"),
                                ("revisions", resolved["proposals"], "revision_id")):
        seen_ids, seen_receipts = set(), set()
        destination = observations if key == "observations" else proposals
        for row in rows:
            if type(row) is not dict:
                raise ValueError("unlocatable_or_duplicate_capture_application_proof")
            target = row if key == "observations" else row.get("resolution", {})
            if type(target) is not dict:
                raise ValueError("unlocatable_or_duplicate_capture_application_proof")
            rid = target.get("receipt_id")
            if (type(row) is not dict or rid not in owners or type(row.get(identity)) is not str
                    or not row[identity] or len(row[identity]) > 200 or row[identity] in seen_ids or rid in seen_receipts):
                raise ValueError("unlocatable_or_duplicate_capture_application_proof")
            if key == "observations":
                delivery._hash(row[identity])
            seen_ids.add(row[identity]); seen_receipts.add(rid)
            destination.setdefault(owners[rid], []).append(copy.deepcopy(row))
    return {"request": request, "snapshot": snapshot, "staged": staged,
            "observations": observations, "proposals": proposals}


def group_proofs(context, original):
    """One whole <=500 group, with no unassigned/partial normalized input."""
    staged = context["staged"]
    projection = {"version": 1, "kind": "nfl.capture_labels.v1", "binding_policy": BINDING_POLICY,
                  "season": staged["season"], "weeks": staged["weeks"], "groups": [original],
                  "membership_id": fingerprint({"binding_policy": BINDING_POLICY, "groups": [original["membership_id"]]})}
    gid = original["group_id"]
    binding, observations, proposals = delivery.make_capture_delivery_binding(
        projection, context["observations"].get(gid, []), context["proposals"].get(gid, []))
    return binding["groups"][0], observations, proposals


def prepare_seal(context, *, max_batch_members=500, max_batch_observations=500,
                 max_batch_revisions=500, max_batch_bytes=MAX_RPC_BYTES):
    """Trusted bound context only, for internal registration orchestration."""
    limits = {"members": capacity._cap(max_batch_members, MAX_BATCH_ITEMS),
              "observations": capacity._cap(max_batch_observations, MAX_BATCH_ITEMS),
              "revisions": capacity._cap(max_batch_revisions, MAX_BATCH_ITEMS),
              "rpc_bytes": capacity._cap(max_batch_bytes, MAX_RPC_BYTES)}
    request, staged = context["request"], context["staged"]
    application_id = request["application_id"]
    groups, ready = [], []
    for original in sorted(staged["groups"], key=lambda group: group["group_id"]):
        group = {key: copy.deepcopy(original[key]) for key in ("group_id", *MEMBERSHIP_KEYS, "membership_id")}
        reasons, fact = set(original["reason_codes"]), None
        group.update(status="withheld", reason_codes=[], batch_ordinal=None, fact=None, fact_id=None)
        if len(original["receipt_ids"]) > limits["members"]:
            reasons.add("complete_group_exceeds_member_limit")
        if not reasons:
            try:
                result, obs, rev = group_proofs(context, original)
                reasons.update(result["reason_codes"])
                if not reasons:
                    facts = {fingerprint(common_fact(row)): common_fact(row) for row in obs}
                    if len(facts) != 1:
                        reasons.add("conflicting_underlying_stat_observations")
                    elif len(obs) > limits["observations"] or len(rev) > limits["revisions"]:
                        reasons.add("complete_group_exceeds_proof_limit")
                    else:
                        fact = next(iter(facts.values()))
                        batch_wire(application_id, MAX_GROUPS - 1, [result], obs, rev,
                                   "9223372036854775807", maximum=limits["rpc_bytes"])
                        ready.append((group, result, obs, rev))
            except (capacity._WireLimit, ValueError) as exc:
                if not isinstance(exc, capacity._WireLimit) and str(exc) != "capture_delivery_exceeds_byte_limit":
                    raise
                reasons.add("complete_group_exceeds_byte_limit")
        group.update(status="withheld" if reasons else "planned", reason_codes=sorted(reasons),
                     fact=None if reasons else fact, fact_id=None if reasons else fingerprint(fact))
        groups.append(group)
    batches, current, obs, rev, results = [], [], [], [], []

    def flush():
        if not current:
            return
        ordinal = len(batches)
        for group in current:
            group["batch_ordinal"] = ordinal
        batches.append({"ordinal": ordinal, "step_id": step_id(application_id, ordinal),
                        "group_ids": [g["group_id"] for g in current],
                        "member_count": sum(len(g["receipt_ids"]) for g in current)})

    for group, result, new_obs, new_rev in ready:
        fits = (sum(len(g["receipt_ids"]) for g in current) + len(group["receipt_ids"]) <= limits["members"]
                and len(obs) + len(new_obs) <= limits["observations"] and len(rev) + len(new_rev) <= limits["revisions"])
        if fits:
            try:
                batch_wire(application_id, MAX_GROUPS - 1, results + [result], obs + new_obs, rev + new_rev,
                           "9223372036854775807", maximum=limits["rpc_bytes"])
            except capacity._WireLimit:
                fits = False
        if not fits:
            flush()
            current, obs, rev, results = [], [], [], []
        current.append(group); results.append(result); obs.extend(new_obs); rev.extend(new_rev)
    flush()
    seal = {"version": 2, "kind": "nfl.capture_application_seal.v2", "application_id": application_id,
            "request_id": fingerprint(request), "capture_intent_id": request["capture_request"]["delivery_id"],
            "capture_id": request["capture_request"]["capture_id"], "execution_policy": EXECUTION_POLICY,
            "binding_policy": BINDING_POLICY, "season": staged["season"], "weeks": staged["weeks"],
            "source_watermark": context["snapshot"]["watermark"], "membership_id": membership_id(groups),
            "source_vector_id": source_vector_id(request["capture_request"]["envelope"]), "limits": limits,
            "groups": groups, "batches": batches}
    seal["seal_id"] = fingerprint(seal)
    bounded(seal, MAX_SEAL_BYTES)
    # Registration RPC itself has the same escaped 8 MiB transport ceiling.
    capacity._encode({"p_request_json": bounded(request, MAX_REQUEST_BYTES),
                      "p_seal_json": bounded(seal, MAX_SEAL_BYTES),
                      "p_watermark": seal["source_watermark"]}, MAX_RPC_BYTES, outer=True)
    return seal
