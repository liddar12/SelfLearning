"""Generic journal validation, including authoritative non-binary corrections.

Binary scoring remains stricter: VOID is durable settlement evidence, never a
binary loss. Domain adapters are responsible for authentic source semantics.
"""
from __future__ import annotations

from .probability_receipts import instant, validate_probability_receipt, validate_resolution


def validate_journal_resolution(receipt, resolution):
    """Validate a FINAL binary result or an explicit authoritative FINAL void."""
    if not isinstance(resolution, dict) or resolution.get("status") != "VOID":
        validate_resolution(receipt, resolution)
    else:
        validate_probability_receipt(receipt)
        for key in ("receipt_id", "contract_id", "event_id"):
            if resolution.get(key) != receipt[key]:
                raise ValueError("outcome_" + key + "_mismatch")
        if "hit" not in resolution or resolution["hit"] is not None:
            raise ValueError("void_must_have_null_hit")
        if resolution.get("authoritative") is not True or resolution.get("final_evidence_status") != "FINAL":
            raise ValueError("void_requires_authoritative_final_evidence")
        for key in ("source", "reason"):
            if not isinstance(resolution.get(key), str) or not resolution[key].strip():
                raise ValueError("missing_void_" + key)
        if instant(resolution.get("resolved_utc")) < instant(receipt["event_start_utc"]):
            raise ValueError("resolution_before_event")
    # Older binary records need not invent clocks they never captured. Where a
    # producer supplies normalized source/observation clocks, they must agree.
    resolved = instant(resolution["resolved_utc"])
    if "source_updated_utc" in resolution and instant(resolution["source_updated_utc"]) > resolved:
        raise ValueError("source_update_after_resolution")
    if "evidence_observed_utc" in resolution and instant(resolution["evidence_observed_utc"]) < resolved:
        raise ValueError("resolution_after_evidence_observation")
