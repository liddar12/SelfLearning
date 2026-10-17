"""One resumable registration/step at a time; no scheduler or model activation.

Every new step re-reads complete authority and verifies raw bytes against the
sealed request. Only SQL owns atomic writes/results. Exact original recovery
precedes clocks/raw access, and unrelated CAS conflicts recompute everything.
"""
from __future__ import annotations

from collections import defaultdict
import math
import time

from . import nfl_capture_application_v2 as V
from . import nfl_capture_evidence as capture
from .nfl_capture_application_journal import CaptureApplicationBudgetExceeded
from .nfl_capture_job import _PinnedJournal
from .nfl_capture_labels import _frozen_entries
from .nfl_observed_job import _now
from ..probability_receipts import fingerprint
from ..store.receipt_journal import JournalConflict


class CaptureApplicationStopped(ValueError):
    """A durable incomplete terminal exists; recover its report, never reapply."""
    def __init__(self, result):
        self.result = result
        super().__init__("capture_application_stopped_recover_original_terminal")


class CaptureApplicationScopeMismatch(ValueError):
    """A known digest does not authorize a different configured producer."""


def _budget(seconds, monotonic):
    if (type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 < seconds <= 45
            or not callable(monotonic)):
        raise ValueError("invalid_capture_application_run_budget")
    end = monotonic() + seconds
    def check():
        if monotonic() >= end:
            raise CaptureApplicationBudgetExceeded("capture_application_pending_operation_budget")
    return check


def _attempts(value):
    if type(value) is not int or not 1 <= value <= 5:
        raise ValueError("capture_application_attempts_must_be_1_to_5")


def _warm(journal, period, check):
    check()
    if not callable(getattr(journal, "prepare_capture_membership", None)):
        raise ValueError("capture_application_membership_cache_port_unavailable")
    # This is only a performance hint. All full raw/member checks and the
    # authoritative SQL CAS still run, even when a cache certificate exists.
    journal.prepare_capture_membership(period["season"], period["weeks"])
    check()


def _membership(journal, snapshot, seal):
    watermark, entries = _frozen_entries(_PinnedJournal(journal, snapshot), seal["season"], set(seal["weeks"]))
    if watermark != snapshot["watermark"]:
        raise ValueError("capture_application_membership_fence_changed")
    grouped = defaultdict(list)
    for sid, entry in entries.items():
        grouped[entry["scope"]].append(sid)
    groups = []
    for scope, sids in grouped.items():
        membership = {"binding_policy": V.BINDING_POLICY, "scope": list(scope),
            "receipt_ids": sorted(entries[sid]["stored"]["receipt_id"] for sid in sids),
            "source_receipt_ids": sorted(sids),
            "generation_object_ids": sorted({oid for sid in sids for oid in entries[sid]["generation_object_ids"]})}
        groups.append({"group_id": fingerprint(list(scope)), "membership_id": fingerprint(membership)})
    return V.membership_id(groups)


def register_capture_application(journal, request, raw_chunks, *, clock=_now, max_attempts=3,
                                 max_run_seconds=45, monotonic=time.monotonic, **seal_limits):
    _attempts(max_attempts)
    check = _budget(max_run_seconds, monotonic)
    frozen = V.validate_request(request)
    for attempt in range(max_attempts):
        check()
        old = journal.get_capture_application(frozen["application_id"])
        if old is not None:
            if fingerprint(old["request"]) != fingerprint(frozen):
                raise JournalConflict("capture_application_request_conflict")
            return old["registration"]
        check()
        if journal.get_capture_delivery(frozen["capture_request"]["delivery_id"]) is not None:
            raise JournalConflict("capture_application_legacy_original_exists")
        context = V.bind_context(journal, frozen, raw_chunks, clock=clock)
        seal = V.prepare_seal(context, **seal_limits)
        _warm(journal, seal, check)
        check()
        try:
            return journal.register_capture_application(frozen, seal, watermark=seal["source_watermark"])
        except JournalConflict:
            check()
            old = journal.get_capture_application(frozen["application_id"])
            if old is not None:
                if fingerprint(old["request"]) != fingerprint(frozen):
                    raise JournalConflict("capture_application_request_conflict") from None
                return old["registration"]
            # The generic transport maps HTTP409 to a conflict, not an
            # authenticated SQL-reason code. Never blindly retry an immutable
            # ownership/vector conflict: require an actually changed evidence
            # fence before a fresh full bind may be attempted.
            check()
            if V._snapshot(journal)["watermark"] == seal["source_watermark"]:
                raise JournalConflict("capture_application_conflict_without_changed_fence") from None
            if attempt + 1 == max_attempts:
                raise JournalConflict("capture_application_registration_retry_exhausted") from None
    raise AssertionError("bounded_attempts_exhausted")


def _stop(journal, application_id, reason, watermark, check):
    check()
    result = journal.stop_capture_application(application_id, reason, watermark=watermark)
    raise CaptureApplicationStopped(result)


def commit_capture_application_step(journal, application_id, step_id, raw_chunks, *, clock=_now,
                                    max_attempts=3, max_run_seconds=45, monotonic=time.monotonic, expected_scope=None):
    V.delivery._hash(application_id); V.delivery._hash(step_id)
    _attempts(max_attempts)
    check = _budget(max_run_seconds, monotonic)
    if expected_scope is not None:
        from ..store.capture_staging import staging_namespace
        if type(expected_scope) is not tuple or len(expected_scope) != 2:
            raise ValueError("invalid_capture_application_expected_scope")
        staging_namespace(destination_id=expected_scope[0], producer_id=expected_scope[1])
    for attempt in range(max_attempts):
        check()
        application = journal.get_capture_application(application_id)
        if application is None:
            raise ValueError("capture_application_not_registered")
        captured = application["request"]["capture_request"]
        if expected_scope is not None and (captured["destination_id"], captured["producer_id"]) != expected_scope:
            raise CaptureApplicationScopeMismatch("capture_application_not_found_in_configured_scope")
        batch = next((row for row in application["batches"] if row["assignment"]["step_id"] == step_id), None)
        if batch is None:
            raise ValueError("capture_application_step_not_in_seal")
        if batch["result"] is not None:
            return batch["result"]
        if application["terminal"] is not None:
            raise CaptureApplicationStopped(application["terminal"])
        if application["status"]["next_step_id"] != step_id:
            raise JournalConflict("capture_application_step_out_of_order")
        seal, frozen = application["seal"], application["request"]
        snapshot = None
        try:
            _warm(journal, seal, check)
            check()
            snapshot = V._snapshot(journal)
            if _membership(journal, snapshot, seal) != seal["membership_id"]:
                check()
                # SQL detects complete current membership BEFORE proof-array
                # validation and atomically records a not-applied step plus
                # the incomplete terminal. No raw/Storage access is needed.
                return journal.commit_capture_application_batch(application_id, step_id, [], [], [],
                                                                 watermark=snapshot["watermark"])
            check()
            try:
                context = V.bind_context(journal, frozen, raw_chunks, clock=clock)
            except capture.CaptureIntegrityError:
                _stop(journal, application_id, "source_integrity_failed", snapshot["watermark"], check)
            except ValueError as exc:
                if str(exc) not in {"capture_delivery_raw_chunk_missing", "capture_delivery_extra_raw_chunks",
                                    "capture_delivery_chunk_reference_mismatch"}:
                    raise
                _stop(journal, application_id, "source_integrity_failed", snapshot["watermark"], check)
            snapshot = context["snapshot"]
            if V.membership_id(context["staged"]["groups"]) != seal["membership_id"]:
                check()
                return journal.commit_capture_application_batch(application_id, step_id, [], [], [],
                                                                 watermark=snapshot["watermark"])
            originals = {group["group_id"]: group for group in context["staged"]["groups"]}
            sealed = {group["group_id"]: group for group in seal["groups"]}
            results, observations, proposals = [], [], []
            for gid in batch["assignment"]["group_ids"]:
                result, obs, rev = V.group_proofs(context, originals[gid])
                if result["status"] == "accepted" and any(fingerprint(V.common_fact(row)) != sealed[gid]["fact_id"] for row in obs):
                    _stop(journal, application_id, "source_integrity_failed", snapshot["watermark"], check)
                results.append(result); observations.extend(obs); proposals.extend(rev)
            try:
                if len(observations) > seal["limits"]["observations"] or len(proposals) > seal["limits"]["revisions"]:
                    raise V.capacity._WireLimit("capture_application_runtime_proof_limit")
                V.batch_wire(application_id, batch["assignment"]["ordinal"], results, observations, proposals,
                             snapshot["watermark"], maximum=seal["limits"]["rpc_bytes"])
            except V.capacity._WireLimit:
                _stop(journal, application_id, "runtime_capacity_exceeded", snapshot["watermark"], check)
            check()
            return journal.commit_capture_application_batch(application_id, step_id, results, observations, proposals,
                                                             watermark=snapshot["watermark"])
        except JournalConflict:
            check()
            recovered = journal.get_capture_application(application_id)
            if recovered is not None:
                original = next((row["result"] for row in recovered["batches"] if row["assignment"]["step_id"] == step_id), None)
                if original is not None:
                    return original
                if recovered["terminal"] is not None:
                    raise CaptureApplicationStopped(recovered["terminal"]) from None
            check()
            if snapshot is None or V._snapshot(journal)["watermark"] == snapshot["watermark"]:
                raise JournalConflict("capture_application_conflict_without_changed_fence") from None
            if attempt + 1 == max_attempts:
                raise JournalConflict("capture_application_step_retry_exhausted") from None
    raise AssertionError("bounded_attempts_exhausted")
