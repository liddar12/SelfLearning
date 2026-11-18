"""Shared admission of exact-fixture NFL labels for trusted fitting and shadow QA.

Call only after validating the complete as-of revision chain. Captured source
hashes establish integrity, not public publication or external authentication.
This module never upgrades an old revision from a later observation.
"""
import re

from ..probability_receipts import finite_number, instant

_CONTENT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def label_exclusion_reason(receipt, source, history, head, cutoff):
    """Qualify a stored revision, without upgrading it from later observations."""
    from .nfl_observed_resolution import BRIDGE_VERSION, EVENT_BOUND_JOIN, _validate_observed_history

    resolution = head["resolution"]
    trace = resolution.get("producer_trace")
    if not isinstance(trace, dict) or trace.get("identity_join_policy") != EVENT_BOUND_JOIN:
        return "legacy_outcome"
    if (resolution.get("evidence_basis") != BRIDGE_VERSION
            or resolution.get("authoritative") is not True
            or resolution.get("final_evidence_status") != "FINAL"
            or trace.get("evidence_quality") != "observed_source_snapshot"
            or any(not isinstance(trace.get(key), str) or not _CONTENT_ID.fullmatch(trace[key])
                   for key in ("evidence_capture_id", "source_row_sha256"))):
        return "unqualified_outcome"
    known = {row["revision_id"]: row for row in history if instant(row["recorded_utc"]) < cutoff}
    parent_id = head.get("supersedes")
    if parent_id is not None:
        prior = known[parent_id]["resolution"]
        prior_trace = prior.get("producer_trace")
        prior_qualified = isinstance(prior_trace, dict) and prior_trace.get("identity_join_policy") == EVENT_BOUND_JOIN
        if not prior_qualified and all(resolution.get(k) == prior.get(k) for k in ("actual", "status", "hit")):
            return "unchanged_outcome_revision"
    try:
        _validate_observed_history(known, receipt)
        game = resolution["measurement"]["game"]
        if (game["season"], game["week"], game["game_id"], game["state"]) != (
                source["season"], source["week"], source["game_id"], "FINAL"):
            return "unqualified_outcome"
        if (instant(game["kickoff_utc"]) != instant(receipt["event_start_utc"])
                or instant(resolution["source_observations"]["espn.scoreboard"]["observed_utc"])
                < instant(receipt["event_start_utc"])):
            return "unqualified_outcome"
        if resolution["status"] == "FINAL" and (not finite_number(resolution.get("actual"))
                or resolution["hit"] is not (resolution["actual"] >= source["line"])):
            return "unqualified_outcome"
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return "unqualified_outcome"
    return "void_outcome" if resolution["status"] == "VOID" else None
