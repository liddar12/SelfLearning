"""Internal orchestration for immutable original delivery-result recovery.

Trusted journal/private raw readers only; no route, sender, credential lookup,
upload quota, source authentication, scheduler or model adoption. The result
ledger—not a current re-evaluation—owns the answer to an already committed
delivery. A fresh capture is needed to cover members admitted afterward.
"""
from __future__ import annotations

import copy
import hashlib
import json

from . import nfl_capture_evidence as capture
from .nfl_capture_job import CaptureCommitError, MAX_BATCH_BYTES, MAX_BATCH_ITEMS, _PinnedJournal
from .nfl_capture_labels import bind_capture_labels
from .nfl_observed_job import _now, _sequence
from .nfl_observed_resolution import resolve_observed
from ..probability_receipts import fingerprint, instant
from .. import evidence_chunks
from ..store.receipt_journal import JournalConflict


def _request(value):
    # The import is local to this optional integration, never backend selection.
    from .nfl_capture_delivery_journal import make_capture_delivery_request
    frozen = capture._snapshot(value)
    try:
        expected = make_capture_delivery_request(frozen["envelope"], frozen["raw_pointers"],
            destination_id=frozen["destination_id"], producer_id=frozen["producer_id"])
    except (TypeError, KeyError, AttributeError):
        raise ValueError("invalid_capture_delivery_request") from None
    if fingerprint(expected) != fingerprint(frozen):
        raise ValueError("invalid_capture_delivery_request")
    return expected


def _identity(result, request):
    if (type(result) is not dict or result.get("delivery_id") != request["delivery_id"]
            or result.get("request_id") != fingerprint(request)
            or result.get("capture_id") != request["capture_id"] or result.get("durable") is not True):
        raise CaptureCommitError("capture_delivery_result_identity_mismatch")
    # Detailed durable result structure/partition validation belongs to the
    # injected delivery journal; this boundary independently pins request ID.
    return result


def _pointer_reader(request, raw_chunks):
    pointers = {row["object_id"]: evidence_chunks._pointer(row, evidence_chunks.DEFAULT_MAX_OBJECT_BYTES)
                for row in request["raw_pointers"]}

    def read(object_id):
        if object_id not in pointers:
            raise ValueError("capture_delivery_raw_pointer_missing")
        source = iter(raw_chunks(object_id))
        for chunk_id, size in pointers[object_id].chunks:
            try:
                raw = next(source)
            except StopIteration:
                raise ValueError("capture_delivery_raw_chunk_missing") from None
            if (type(raw) is not bytes or len(raw) != size
                    or "sha256:" + hashlib.sha256(raw).hexdigest() != chunk_id):
                raise ValueError("capture_delivery_chunk_reference_mismatch")
            yield raw
        try:
            next(source)
        except StopIteration:
            return
        raise ValueError("capture_delivery_extra_raw_chunks")

    return read


def commit_capture_delivery(journal, request, raw_chunks, *, clock=_now, max_attempts=3,
                            max_total_bytes=capture.DEFAULT_MAX_TOTAL_BYTES):
    """Recover original result first, otherwise bind/resolve/commit at one fence.

    Only explicit CAS conflicts retry, always with new full membership and raw
    integrity/label passes. A lost reply propagates without a success claim;
    replay with the identical pre-send request recovers the original ledger
    result even if later forecasts or corrections have arrived.
    """
    from .nfl_capture_delivery_journal import make_capture_delivery_binding
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("capture_delivery_attempts_must_be_1_to_5")
    if (not callable(clock) or not callable(raw_chunks)
            or type(max_total_bytes) is not int or not 0 <= max_total_bytes <= capture.DEFAULT_MAX_TOTAL_BYTES
            or any(not callable(getattr(journal, key, None)) for key in
                   ("read_observed_snapshot", "get_object", "get_capture_delivery", "commit_capture_delivery"))):
        raise ValueError("invalid_capture_delivery_boundary")
    frozen = _request(request)
    verified_chunks = _pointer_reader(frozen, raw_chunks)
    for attempt in range(1, max_attempts + 1):
        old = journal.get_capture_delivery(frozen["delivery_id"])
        if old is not None:
            return {"version": 1, "status": "recovered_original", "attempts": attempt,
                    "result": _identity(old, frozen), "scope": "internal_delivery_results_only"}
        processing = clock()
        instant(processing)
        snapshot = copy.deepcopy(journal.read_observed_snapshot())
        if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
                or any(type(snapshot.get(key)) is not list for key in
                       ("receipts", "generation_records", "revisions", "observations", "observation_records"))):
            raise CaptureCommitError("invalid_capture_delivery_snapshot")
        _sequence(snapshot.get("watermark"))
        staged = bind_capture_labels(_PinnedJournal(journal, snapshot), frozen["envelope"], verified_chunks,
                                     max_total_bytes=max_total_bytes)
        if staged["membership_watermark"] != snapshot["watermark"]:
            raise CaptureCommitError("capture_delivery_binding_watermark_mismatch")
        resolved = resolve_observed(snapshot["receipts"], staged["source_receipts"], staged["observed_legs"],
            staged["game_observations"], observed_utc=processing, existing_revisions=snapshot["revisions"],
            existing_observations=snapshot["observations"])
        binding, observations, proposals = make_capture_delivery_binding(
            staged, resolved["observation_proposals"], resolved["proposals"])
        if len(observations) > MAX_BATCH_ITEMS or len(proposals) > MAX_BATCH_ITEMS:
            raise CaptureCommitError("complete_capture_delivery_exceeds_item_limit")
        wire = json.dumps({"request": frozen, "binding": binding,
                           "observations": observations, "proposals": proposals},
                          sort_keys=True, ensure_ascii=True, allow_nan=False)
        if len(wire.encode("utf-8")) > MAX_BATCH_BYTES:
            raise CaptureCommitError("complete_capture_delivery_exceeds_byte_limit")
        try:
            result = journal.commit_capture_delivery(frozen, binding, observations, proposals,
                                                      watermark=snapshot["watermark"])
        except JournalConflict:
            if attempt == max_attempts:
                raise JournalConflict("capture_delivery_conflict_retry_exhausted") from None
            continue
        return {"version": 1, "status": "recorded_or_recovered", "attempts": attempt,
                "result": _identity(result, frozen), "scope": "internal_delivery_results_only"}
    raise AssertionError("bounded_attempts_exhausted")
