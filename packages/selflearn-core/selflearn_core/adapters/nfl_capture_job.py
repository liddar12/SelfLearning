"""Internal verified-raw capture → fenced observation/revision persistence.

No HTTP route, producer authentication, upload authorization, scheduler, durable
delivery/group-result ledger or model activation. A trusted server-owned journal
and raw reader are required. This composes the independent binder with the
existing atomic persistence kernel; it does not accept caller-normalized labels.
Every explicit CAS conflict reruns the complete raw/frozen membership binding.
"""
from __future__ import annotations

import copy
import json

from . import nfl_capture_evidence as capture
from .nfl_capture_labels import bind_capture_labels
from .nfl_observed_job import ObservedCommitError, _now, resolve_and_commit_observed
from ..probability_receipts import instant
from ..store.receipt_journal import JournalConflict

MAX_BATCH_ITEMS = 500
MAX_BATCH_BYTES = 8 * 1024 * 1024


class CaptureCommitError(RuntimeError):
    """Bounded internal failure; never a durable delivery acknowledgement."""


class _PinnedJournal:
    def __init__(self, authority, snapshot):
        self._authority, self._snapshot = authority, snapshot

    def read_observed_snapshot(self):
        return copy.deepcopy(self._snapshot)

    def get_object(self, object_id):
        return self._authority.get_object(object_id)

    def commit_observed(self, observations, proposals, *, watermark):
        if watermark != self._snapshot.get("watermark"):
            raise CaptureCommitError("capture_commit_watermark_mismatch")
        if len(observations) > MAX_BATCH_ITEMS or len(proposals) > MAX_BATCH_ITEMS:
            raise CaptureCommitError("complete_capture_batch_exceeds_item_limit")
        # Conservatively bound this entire capture instead of silently splitting
        # a player's rungs/vintages. The actual journal independently checks its
        # serialized RPC envelope and SQL limits as well.
        encoded = json.dumps({"observations": observations, "proposals": proposals},
                             sort_keys=True, ensure_ascii=True, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_BATCH_BYTES:
            raise CaptureCommitError("complete_capture_batch_exceeds_byte_limit")
        return self._authority.commit_observed(observations, proposals, watermark=watermark)


def verify_and_commit_capture(journal, capture_envelope, raw_chunks, *, clock=_now,
                              max_attempts=3, max_total_bytes=capture.DEFAULT_MAX_TOTAL_BYTES):
    """One complete immutable capture per attempt; no stale-membership retry.

    Only explicit JournalConflict retries. A lost response, malformed ACK or raw
    failure propagates without success. Later stable-ID replay can recover
    persisted observations, but an original delivery/group ACK cannot be claimed
    until its separate immutable result ledger is implemented.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("capture_commit_attempts_must_be_1_to_5")
    if (not callable(clock) or not callable(raw_chunks)
            or type(max_total_bytes) is not int or not 0 <= max_total_bytes <= capture.DEFAULT_MAX_TOTAL_BYTES
            or any(not callable(getattr(journal, key, None)) for key in
                   ("read_observed_snapshot", "get_object", "commit_observed"))):
        raise ValueError("invalid_capture_commit_boundary")
    frozen_envelope = capture._snapshot(capture_envelope)
    for attempt in range(1, max_attempts + 1):
        processing = clock()
        instant(processing)
        snapshot = copy.deepcopy(journal.read_observed_snapshot())
        pinned = _PinnedJournal(journal, snapshot)
        staged = bind_capture_labels(pinned, frozen_envelope, raw_chunks, max_total_bytes=max_total_bytes)
        if staged["membership_watermark"] != snapshot.get("watermark"):
            raise CaptureCommitError("capture_binding_watermark_mismatch")
        if any(len(group["receipt_ids"]) > MAX_BATCH_ITEMS for group in staged["groups"]):
            raise CaptureCommitError("complete_underlying_group_exceeds_item_limit")
        try:
            persisted = resolve_and_commit_observed(pinned, staged["source_receipts"], staged["observed_legs"],
                staged["game_observations"], clock=lambda: processing, max_attempts=1)
        except JournalConflict:
            if attempt == max_attempts:
                raise JournalConflict("capture_commit_conflict_retry_exhausted") from None
            continue
        except ObservedCommitError:
            raise CaptureCommitError("capture_observation_persistence_unverified") from None
        return {"version": 1, "kind": "nfl.capture_commit.v1", "attempts": attempt,
                "status": "observations_persisted" if persisted["durable"] else "no_accepted_evidence",
                "staged": staged, "persistence": persisted,
                "scope": "internal_observation_persistence_only",
                "source_authentication": "not_performed", "durable_ack": False}
    raise AssertionError("bounded_attempts_exhausted")
