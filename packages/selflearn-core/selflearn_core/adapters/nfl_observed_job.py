"""Bounded internal resolve → fenced commit, never a public ingestion route.

Call only after trusted capture/identity verification and complete underlying
stat-group assembly. This helper does not authenticate uploads, fetch sources,
chunk captures, schedule jobs, approve candidates or activate a backend. Those
boundaries remain separate. Durable history is supplied by the journal, never
by a caller's selected observation IDs.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import re

from .nfl_observed_resolution import resolve_observed
from ..probability_receipts import fingerprint, instant
from ..store.receipt_journal import JournalConflict

_SEQUENCE = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")


class ObservedCommitError(RuntimeError):
    """Sanitized unavailable/malformed persistence boundary; no durable ACK."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _sequence(value):
    if not isinstance(value, str) or not _SEQUENCE.fullmatch(value):
        raise ObservedCommitError("invalid_observed_commit_watermark")
    return int(value)


def resolve_and_commit_observed(journal, source_receipts, observed_legs, game_observations,
                                *, clock=_now, max_attempts=3):
    """Re-read and re-resolve on CAS conflicts; never retry stale proposals.

    A lost transport response is not treated as failure-before-commit: it
    propagates without a success claim, and the producer may replay the same
    immutable input later. Only an explicit JournalConflict retries here.
    Current trusted processing time is read anew per attempt, while source
    observation clocks remain unchanged.
    """
    if type(max_attempts) is not int or not 1 <= max_attempts <= 5:
        raise ValueError("observed_commit_attempts_must_be_1_to_5")
    if (not isinstance(source_receipts, list) or not isinstance(observed_legs, list)
            or not isinstance(game_observations, dict) or not callable(clock)):
        raise ValueError("invalid_observed_job_input")
    # Fail on non-JSON/mutable exotic containers before any journal operation.
    evidence = copy.deepcopy({"sources": source_receipts, "legs": observed_legs,
                              "games": game_observations})
    fingerprint(evidence)
    for attempt in range(1, max_attempts + 1):
        processing = clock()
        instant(processing)
        snapshot = journal.read_observed_snapshot()
        if (not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int
                or snapshot["version"] != 1
                or any(not isinstance(snapshot.get(key), list) for key in (
                    "receipts", "generation_records", "revisions", "observations", "observation_records"))):
            raise ObservedCommitError("invalid_observed_snapshot")
        watermark = snapshot.get("watermark")
        _sequence(watermark)
        result = resolve_observed(snapshot["receipts"], evidence["sources"], evidence["legs"],
            evidence["games"], observed_utc=processing, existing_revisions=snapshot["revisions"],
            existing_observations=snapshot["observations"])
        if not result["observation_proposals"] and not result["proposals"]:
            return {"version": 1, "durable": False, "status": "no_accepted_evidence",
                    "attempts": attempt, "result": result, "commit": None}
        # A complete CAS snapshot makes the exact number of new immutable IDs
        # knowable. A merely plausible lower count could ACK only one stream.
        known_observations = {row["observation_id"] for row in snapshot["observations"]}
        known_revisions = {row["revision_id"] for row in snapshot["revisions"]}
        expected_observations = len({row["observation_id"] for row in result["observation_proposals"]}
                                    - known_observations)
        expected_revisions = len({row["revision_id"] for row in result["proposals"]} - known_revisions)
        try:
            ack = journal.commit_observed(result["observation_proposals"], result["proposals"],
                                          watermark=watermark)
        except JournalConflict:
            if attempt == max_attempts:
                raise JournalConflict("observed_commit_conflict_retry_exhausted") from None
            continue
        if (not isinstance(ack, dict)
                or set(ack) != {"version", "added_observations", "added_revisions", "watermark"}
                or type(ack.get("version")) is not int or ack["version"] != 1
                or any(type(ack.get(key)) is not int or ack[key] < 0 for key in (
                    "added_observations", "added_revisions"))
                or ack["added_observations"] != expected_observations
                or ack["added_revisions"] != expected_revisions):
            raise ObservedCommitError("invalid_observed_commit_response")
        high = _sequence(ack["watermark"])
        if (high < _sequence(watermark)
                or bool(expected_observations + expected_revisions) != (high != _sequence(watermark))):
            raise ObservedCommitError("invalid_observed_commit_response")
        return {"version": 1, "durable": True, "status": "committed",
                "attempts": attempt, "result": result, "commit": ack}
    raise AssertionError("bounded_attempts_exhausted")
