"""Opt-in immutable delivery results; no HTTP/auth/quota/backend activation.

Trusted server configuration supplies destination/producer identity. A stored
result describes its ORIGINAL cohort, not today's head or provider authenticity.
No retries, uploads, filesystem fallback or resource opens occur in this module.
"""
from __future__ import annotations

import copy
import json
import re

from . import nfl_capture_evidence as capture
from .nfl_capture_labels import POLICY
from .nfl_observed_journal import NFLObservedJournal
from .. import evidence_chunks
from ..probability_receipts import fingerprint, instant
from ..store.receipt_journal import JournalConflict
from ..store.supabase_journal import _canonical, _revision_envelope
from ..store.supabase_store import StoreError

MAX_REQUEST_BYTES = 3 * 1024 * 1024
MAX_RPC_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 500
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SUBJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}\Z")
_SEQ = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
_GROUP_KEYS = {"group_id", "scope", "receipt_ids", "source_receipt_ids", "generation_object_ids",
               "membership_id", "status", "reason_codes", "observation_ids", "revision_ids"}
_RESULT_KEYS = {"version", "kind", "delivery_id", "request_id", "capture_id", "membership_id", "status", "groups",
                "added_observations", "added_revisions", "input_watermark", "watermark", "delivery_seq", "recorded_utc",
                "durable", "scope", "source_authentication"}


def _hash(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        raise ValueError("invalid_capture_delivery_digest")
    return value


def _sequence(value, *, positive=False):
    if type(value) is not str or not _SEQ.fullmatch(value) or (positive and value == "0"):
        raise ValueError("invalid_capture_delivery_sequence")
    return int(value)


def _bounded(value, maximum):
    # Domain snapshots already enforce bounded containers/strings before the
    # serialized-wire check. No untrusted exotic object equality is accepted.
    encoded = _canonical(value)
    if len(encoded.encode("utf-8")) > maximum:
        raise ValueError("capture_delivery_exceeds_byte_limit")
    return encoded


def make_capture_delivery_request(envelope, raw_pointers, *, destination_id, producer_id):
    """Freeze the presend intent; metadata integrity is NOT raw verification."""
    _hash(destination_id)
    if type(producer_id) is not str or not _SUBJECT.fullmatch(producer_id):
        raise ValueError("invalid_capture_delivery_producer")
    frozen = capture._snapshot(envelope)
    manifest, _, _ = capture._preflight(frozen, capture.DEFAULT_MAX_TOTAL_BYTES)
    if type(raw_pointers) is not list or len(raw_pointers) > 20:
        raise ValueError("invalid_capture_delivery_pointer_set")
    pointers = copy.deepcopy(raw_pointers)
    expected, seen, total = [], set(), 0
    for value in pointers:
        pointer = evidence_chunks._pointer(value, evidence_chunks.DEFAULT_MAX_OBJECT_BYTES)
        if pointer.object_id in seen:
            raise ValueError("duplicate_capture_delivery_object")
        seen.add(pointer.object_id)
        total += pointer.raw_bytes
        expected.append({"raw_sha256": pointer.object_id, "raw_bytes": pointer.raw_bytes})
    if total > capture.DEFAULT_MAX_TOTAL_BYTES:
        raise ValueError("capture_delivery_pointer_budget")
    pointers.sort(key=lambda value: value["object_id"])
    expected.sort(key=lambda value: value["raw_sha256"])
    if _canonical(expected) != _canonical(frozen["raw_objects"]):
        raise ValueError("capture_delivery_pointer_envelope_mismatch")
    identity = {"version": 1, "kind": "nfl.capture_delivery.v1", "destination_id": destination_id,
                "producer_id": producer_id, "capture_id": manifest["capture_id"], "binding_policy": POLICY}
    request = {**identity, "kind": "nfl.capture_delivery_request.v1", "envelope": frozen,
               "raw_pointers": pointers, "delivery_id": fingerprint(identity)}
    _bounded(request, MAX_REQUEST_BYTES)
    return request


def _request(value):
    if type(value) is not dict or set(value) != {"version", "kind", "destination_id", "producer_id", "capture_id",
                                                "binding_policy", "envelope", "raw_pointers", "delivery_id"}:
        raise ValueError("invalid_capture_delivery_request")
    rebuilt = make_capture_delivery_request(value["envelope"], value["raw_pointers"],
                    destination_id=value["destination_id"], producer_id=value["producer_id"])
    if _canonical(rebuilt) != _canonical(value):
        raise ValueError("capture_delivery_request_mismatch")
    return rebuilt


def _ids(value, *, hashes=True, empty=True):
    if type(value) is not list or len(value) > MAX_MEMBERS or (not empty and not value):
        raise ValueError("invalid_capture_delivery_ids")
    if any(type(item) is not str or not item or len(item) > 200 for item in value):
        raise ValueError("invalid_capture_delivery_ids")
    if hashes:
        for item in value:
            _hash(item)
    if value != sorted(set(value)):
        raise ValueError("noncanonical_capture_delivery_ids")
    return value


def _group(value):
    if type(value) is not dict or set(value) != _GROUP_KEYS:
        raise ValueError("invalid_capture_delivery_group")
    scope = value["scope"]
    if (type(scope) is not list or len(scope) != 5 or type(scope[0]) is not int
            or any(type(item) is not str or not item or len(item) > 200 for item in scope[1:])
            or scope[3] not in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds") or scope[4] not in ("home", "away")
            or value["group_id"] != fingerprint(scope)):
        raise ValueError("invalid_capture_delivery_group_scope")
    for key in ("receipt_ids", "source_receipt_ids", "generation_object_ids"):
        _ids(value[key], empty=False)
    if len(value["receipt_ids"]) != len(value["source_receipt_ids"]):
        raise ValueError("invalid_capture_delivery_member_mapping")
    membership = {"binding_policy": POLICY, **{key: value[key] for key in
                    ("scope", "receipt_ids", "source_receipt_ids", "generation_object_ids")}}
    if value["membership_id"] != fingerprint(membership):
        raise ValueError("capture_delivery_membership_hash_mismatch")
    reasons = value["reason_codes"]
    if (type(reasons) is not list or len(reasons) > 100
            or any(type(reason) is not str or not _REASON.fullmatch(reason) for reason in reasons)
            or reasons != sorted(set(reasons)) or type(value["status"]) is not str
            or value["status"] not in ("accepted", "withheld")
            or (value["status"] == "accepted") != (not reasons)):
        raise ValueError("invalid_capture_delivery_group_status")
    _ids(value["observation_ids"])
    _ids(value["revision_ids"], hashes=False)
    if value["status"] == "withheld" and (value["observation_ids"] or value["revision_ids"]):
        raise ValueError("withheld_capture_group_has_writes")
    if value["status"] == "accepted" and len(value["observation_ids"]) != len(value["receipt_ids"]):
        raise ValueError("incomplete_capture_group_proofs")
    return value


def make_capture_delivery_binding(staged, observations, proposals):
    """Return (binding, filtered observations, filtered proposals).

    Missing proof for ONE member withholds that whole underlying group. Extra,
    duplicate or unlocatable proof identity is a population error, not silently
    filtered evidence. Call after the frozen kernel has quarantined its inputs.
    """
    if (type(staged) is not dict or staged.get("kind") != "nfl.capture_labels.v1"
            or staged.get("binding_policy") != POLICY or type(staged.get("groups")) is not list
            or type(observations) is not list or type(proposals) is not list):
        raise ValueError("invalid_capture_delivery_binding_input")
    data = copy.deepcopy({"staged": staged, "observations": observations, "proposals": proposals})
    _bounded(data, MAX_RPC_BYTES)
    staged, observations, proposals = data["staged"], data["observations"], data["proposals"]
    groups, owners, known_groups = [], {}, set()
    for original in staged["groups"]:
        if type(original) is not dict or original.get("group_id") in known_groups:
            raise ValueError("duplicate_capture_delivery_group")
        known_groups.add(original.get("group_id"))
        group = {key: original[key] for key in _GROUP_KEYS - {"observation_ids", "revision_ids"}}
        group.update(status="accepted" if original["status"] == "staged" else "withheld", observation_ids=[], revision_ids=[])
        if original["status"] not in ("staged", "withheld"):
            raise ValueError("invalid_staged_capture_group_status")
        for rid in group["receipt_ids"]:
            if rid in owners:
                raise ValueError("duplicate_capture_delivery_member")
            owners[rid] = group
        groups.append(group)
    if len(owners) > MAX_MEMBERS:
        raise ValueError("complete_capture_exceeds_member_limit")
    obs_by_group, rev_by_group, seen_obs, seen_rev, seen_receipts = {}, {}, set(), set(), set()
    for row in observations:
        if type(row) is not dict or row.get("receipt_id") not in owners:
            raise ValueError("unlocatable_capture_delivery_proof")
        oid, rid = _hash(row.get("observation_id")), row["receipt_id"]
        if oid in seen_obs or rid in seen_receipts:
            raise ValueError("duplicate_capture_delivery_proof")
        seen_obs.add(oid); seen_receipts.add(rid)
        obs_by_group.setdefault(owners[rid]["group_id"], []).append(row)
    for row in proposals:
        if type(row) is not dict or type(row.get("resolution")) is not dict or row["resolution"].get("receipt_id") not in owners:
            raise ValueError("unlocatable_capture_delivery_revision")
        revision_id = row.get("revision_id")
        if type(revision_id) is not str or not revision_id or revision_id in seen_rev:
            raise ValueError("duplicate_or_invalid_capture_delivery_revision")
        seen_rev.add(revision_id)
        rid = row["resolution"]["receipt_id"]
        rev_by_group.setdefault(owners[rid]["group_id"], []).append(row)
    kept_obs, kept_rev = [], []
    for group in groups:
        obs, rev = obs_by_group.get(group["group_id"], []), rev_by_group.get(group["group_id"], [])
        if group["status"] == "accepted" and {row["receipt_id"] for row in obs} != set(group["receipt_ids"]):
            group.update(status="withheld", reason_codes=sorted(set(group["reason_codes"] + ["kernel_group_incomplete"])))
        if group["status"] == "accepted":
            group["observation_ids"] = sorted(row["observation_id"] for row in obs)
            group["revision_ids"] = sorted(row["revision_id"] for row in rev)
            kept_obs.extend(obs); kept_rev.extend(rev)
        _group(group)
    binding = {"version": 1, "kind": "nfl.capture_delivery_binding.v1", "season": staged["season"], "weeks": staged["weeks"],
               "membership_id": fingerprint({"binding_policy": POLICY, "groups": [g["membership_id"] for g in groups]}), "groups": groups}
    if binding["membership_id"] != staged["membership_id"]:
        raise ValueError("capture_delivery_staged_membership_mismatch")
    return binding, kept_obs, kept_rev


def _result(value, *, request=None, delivery_id=None):
    if type(value) is not dict or set(value) != _RESULT_KEYS:
        raise ValueError("invalid_capture_delivery_result")
    if (type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "nfl.capture_delivery_result.v1"
            or value["durable"] is not True or value["scope"] != "observations_and_group_results"
            or value["source_authentication"] != "not_performed"):
        raise ValueError("invalid_capture_delivery_result")
    for key in ("delivery_id", "request_id", "capture_id", "membership_id"):
        _hash(value[key])
    if delivery_id is not None and value["delivery_id"] != delivery_id:
        raise ValueError("capture_delivery_result_identity_mismatch")
    if request is not None and any(value[key] != expected for key, expected in
            (("delivery_id", request["delivery_id"]), ("capture_id", request["capture_id"]), ("request_id", fingerprint(request)))):
        raise ValueError("capture_delivery_result_request_mismatch")
    low, high = _sequence(value["input_watermark"]), _sequence(value["watermark"])
    _sequence(value["delivery_seq"], positive=True)
    instant(value["recorded_utc"])
    if request is not None and instant(value["recorded_utc"]) < instant(request["envelope"]["manifest"]["completed_utc"]):
        raise ValueError("capture_delivery_result_clock_mismatch")
    groups = value["groups"]
    if type(groups) is not list or len(groups) > MAX_MEMBERS:
        raise ValueError("invalid_capture_delivery_result_groups")
    seen_groups, seen_receipts, seen_observations, seen_revisions = set(), set(), set(), set()
    for group in groups:
        _group(group)
        for items, known in (([group["group_id"]], seen_groups), (group["receipt_ids"], seen_receipts),
                             (group["observation_ids"], seen_observations), (group["revision_ids"], seen_revisions)):
            if known.intersection(items):
                raise ValueError("duplicate_capture_delivery_result_identity")
            known.update(items)
    if len(seen_receipts) > MAX_MEMBERS or value["membership_id"] != fingerprint({"binding_policy": POLICY, "groups": [g["membership_id"] for g in groups]}):
        raise ValueError("capture_delivery_result_membership_mismatch")
    expected_status = ("no_supported_membership" if not groups else "accepted" if all(g["status"] == "accepted" for g in groups)
                       else "partial" if any(g["status"] == "accepted" for g in groups) else "withheld")
    if value["status"] != expected_status:
        raise ValueError("capture_delivery_result_status_mismatch")
    for key, ids in (("added_observations", seen_observations), ("added_revisions", seen_revisions)):
        if type(value[key]) is not int or not 0 <= value[key] <= len(ids):
            raise ValueError("capture_delivery_result_count_mismatch")
    if high < low or bool(value["added_observations"] + value["added_revisions"]) != (high != low):
        raise ValueError("capture_delivery_result_watermark_mismatch")
    _bounded(value, MAX_REQUEST_BYTES)
    return value


class NFLCaptureDeliveryJournal(NFLObservedJournal):
    """Explicit local/pilot capability; never selected by existing API config."""

    def get_capture_delivery(self, delivery_id):
        _hash(delivery_id)
        groups, summary, after, total = [], None, None, None
        for _ in range(MAX_MEMBERS + 1):
            page = self._observed_rpc("selflearn_journal_read_capture_delivery_v1",
                {"p_delivery_id": delivery_id, "p_after": after, "p_limit": min(self.page_size, 100)})
            try:
                if (set(page) != {"version", "result", "groups", "next_after", "total_groups"}
                        or type(page["groups"]) is not list or type(page["total_groups"]) is not int
                        or not 0 <= page["total_groups"] <= MAX_MEMBERS):
                    raise ValueError("invalid_capture_delivery_page")
                if page["result"] is None:
                    if after is not None or page["groups"] or page["total_groups"] or page["next_after"] is not None:
                        raise ValueError("invalid_missing_capture_delivery_page")
                    return None
                if type(page["result"]) is not dict or set(page["result"]) != _RESULT_KEYS - {"groups"}:
                    raise ValueError("invalid_capture_delivery_page_summary")
                if summary is not None and (_canonical(summary) != _canonical(page["result"]) or total != page["total_groups"]):
                    raise ValueError("capture_delivery_page_changed")
                summary, total = page["result"], page["total_groups"]
                if len(page["groups"]) > min(self.page_size, 100):
                    raise ValueError("capture_delivery_page_limit")
                groups.extend(page["groups"])
                if len(groups) > total:
                    raise ValueError("capture_delivery_page_overflow")
                next_after = page["next_after"]
                if next_after is None:
                    if len(groups) != total:
                        raise ValueError("capture_delivery_page_incomplete")
                    return _result({**summary, "groups": groups}, delivery_id=delivery_id)
                if not page["groups"] or _sequence(next_after) != len(groups) or len(groups) >= total:
                    raise ValueError("capture_delivery_cursor_mismatch")
                after = next_after
            except (ValueError, KeyError, TypeError, OverflowError):
                raise StoreError(502) from None
        raise StoreError(502)

    def commit_capture_delivery(self, request, binding, observations, proposals, *, watermark):
        request = _request(request)
        _sequence(watermark)
        if type(binding) is not dict or type(observations) is not list or type(proposals) is not list:
            raise ValueError("invalid_capture_delivery_commit_input")
        if len(observations) > MAX_MEMBERS or len(proposals) > MAX_MEMBERS:
            raise ValueError("complete_capture_exceeds_item_limit")
        revisions = [_revision_envelope(**row) for row in proposals]
        batch = {"version": 1, "observations": observations, "revisions": revisions}
        data = {"p_request_json": _bounded(request, MAX_REQUEST_BYTES), "p_binding_json": _bounded(binding, MAX_REQUEST_BYTES),
                "p_batch_json": _bounded(batch, MAX_RPC_BYTES), "p_watermark": watermark}
        _bounded(data, MAX_RPC_BYTES)
        snapshot = self._observed_snapshot
        if snapshot is not None and snapshot.get("watermark") != watermark:
            snapshot = None
        # No current membership/count comparison here: another writer may have
        # already committed THIS immutable delivery. Its original ACK wins even
        # when today's rederived membership, head or expected counts differ.
        try:
            result = self._observed_rpc("selflearn_journal_commit_capture_delivery_v1", data)
        finally:
            self._observed_snapshot = None
        try:
            result = _result(result, request=request)
            matches_attempt = result["input_watermark"] == watermark
            if result["input_watermark"] == watermark:
                if (_canonical(result["groups"]) != _canonical(binding.get("groups"))
                        or result["membership_id"] != binding.get("membership_id")):
                    matches_attempt = False
                else:
                    if snapshot is None:
                        snapshot = self._load_observed_snapshot(watermark)
                        self._observed_snapshot = None
                    prior_obs = {row["observation_id"] for row in snapshot["observations"]}
                    prior_rev = {row["revision_id"] for row in snapshot["revisions"]}
                    matches_attempt = (result["added_observations"] == len({row["observation_id"] for row in observations} - prior_obs)
                        and result["added_revisions"] == len({row["revision_id"] for row in revisions} - prior_rev))
            if not matches_attempt:
                # A prior withheld/no-write result can share the same evidence
                # fence. Recover only by independently reading the immutable
                # stored result, never by relaxing fresh-batch count checks.
                stored = self.get_capture_delivery(request["delivery_id"])
                if stored is None or _canonical(stored) != _canonical(result):
                    raise ValueError("unverified_original_capture_delivery_result")
            return result
        except (ValueError, KeyError, TypeError, OverflowError):
            raise StoreError(502) from None
