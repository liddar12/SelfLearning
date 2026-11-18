"""Offline fit for NFL v1 corrections artifacts. No serving side effects.

Caller-supplied grade lists remain integrity-hashed only — not authenticated
provenance. Digests ≠ signatures. Runtime ``applied=true`` requires
``source=receipt_journal_publication_as_of_v2`` from the publication/as-of binder.
Original capture receipts are never rewritten to claim public availability.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

from ..probability_receipts import fingerprint, finite_number, instant, validate_probability_receipt
from .nfl_corrections_policy import (
    CorrectionsError,
    DEFAULT_MIN_GRADED_PER_MARKET,
    DEFAULT_MIN_GRADED_TOTAL,
    DEFAULT_PRIOR_STRENGTH,
    ELIGIBILITY_RULES,
    HARD_CLAMP,
    HARD_LEG_CAP_PP,
    HARD_PARLAY_CAP_PP,
    HARD_PARLAY_CAP_REL,
    KIND,
    METHOD,
    MODEL_VERSION,
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
    _cap_pp,
    _prob,
    _text,
    is_void_status,
    segment_key,
)

_CONTENT_ID = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _journal_label_reason(receipt, source, history, head, cutoff):
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


def _eligible_record(row, *, trained_through):
    if not isinstance(row, dict):
        return None
    try:
        market = _text(row.get("market"), name="market", maximum=100)
        selection = _text(row.get("selection"), name="selection", maximum=100)
        baseline = _prob(row.get("baseline_prob"))
        served_at = instant(row.get("served_at"))
        kickoff_at = instant(row.get("kickoff_at"))
        graded_at = instant(row.get("graded_at"))
        cutoff = instant(trained_through)
        outcome = row.get("outcome")
        status = row.get("status")
    except (CorrectionsError, ValueError, TypeError):
        return None
    if is_void_status(status):
        return None
    if type(outcome) is not bool:
        return None
    if not (served_at < kickoff_at and graded_at <= cutoff):
        return None
    return {
        "market": market,
        "selection": selection,
        "segment": segment_key(market, selection),
        "baseline_prob": baseline,
        "outcome": outcome,
        "served_at": row["served_at"],
        "kickoff_at": row["kickoff_at"],
        "graded_at": row["graded_at"],
    }


def require_clock_order(*, trained_through, fitted_at, expires_at):
    trained = instant(trained_through)
    fitted = instant(fitted_at)
    expires = instant(expires_at)
    if not (trained <= fitted <= expires):
        raise CorrectionsError("invalid_artifact_clock_order")


def fit_corrections_artifact(graded_records, *, trained_through, expires_at,
                             fitted_at=None, source=UNTRUSTED_ARTIFACT_SOURCE,
                             prior_strength=DEFAULT_PRIOR_STRENGTH,
                             min_graded_total=DEFAULT_MIN_GRADED_TOTAL,
                             min_graded_per_market=DEFAULT_MIN_GRADED_PER_MARKET,
                             leg_cap_pp=HARD_LEG_CAP_PP,
                             allow_trusted_source=False):
    """Build a content-addressed corrections artifact from graded records.

    Storage without a migration: persist body (sans nested artifact_id) as an
    existing journal ``source_snapshot`` and pin with
    ``SELFLEARN_CORRECTIONS_ARTIFACT_ID``. Digests detect mutation only.
    Default ``source`` is untrusted and cannot yield runtime ``applied=true``.
    Trusted source labels are refused here unless ``allow_trusted_source``
    (binder-only); caller-list / CLI paths must not claim binder origin.
    """
    if not isinstance(graded_records, list):
        raise CorrectionsError("invalid_graded_records")
    if source == TRUSTED_ARTIFACT_SOURCE and allow_trusted_source is not True:
        raise CorrectionsError("trusted_source_requires_journal_binder")
    if not finite_number(prior_strength) or prior_strength <= 0:
        raise CorrectionsError("invalid_prior_strength")
    if type(min_graded_total) is not int or min_graded_total < 1:
        raise CorrectionsError("invalid_min_graded_total")
    if type(min_graded_per_market) is not int or min_graded_per_market < 1:
        raise CorrectionsError("invalid_min_graded_per_market")
    leg_cap_pp = _cap_pp(leg_cap_pp, hard=HARD_LEG_CAP_PP)
    fitted = fitted_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    require_clock_order(trained_through=trained_through, fitted_at=fitted, expires_at=expires_at)
    eligible = []
    for row in graded_records:
        item = _eligible_record(row, trained_through=trained_through)
        if item is not None:
            eligible.append(item)
    by_segment = defaultdict(list)
    for row in eligible:
        by_segment[row["segment"]].append(row["outcome"] - row["baseline_prob"])
    segments = {}
    for key, residuals in sorted(by_segment.items()):
        n = len(residuals)
        raw = sum(residuals) / n
        delta = raw * (n / (n + float(prior_strength)))
        if abs(delta) > leg_cap_pp:
            delta = math.copysign(leg_cap_pp, delta)
        if not math.isfinite(delta):
            delta = 0.0
        segments[key] = {
            "n_eligible": n,
            "raw_bias": round(raw, 8),
            "delta": round(delta, 8),
            "eligible_for_application": n >= min_graded_per_market,
        }
    fit_inputs = {
        "trained_through": trained_through,
        "eligibility_rules": list(ELIGIBILITY_RULES),
        "eligible": [{k: r[k] for k in ("market", "selection", "baseline_prob", "outcome",
                                        "served_at", "kickoff_at", "graded_at")}
                     for r in eligible],
        "method": METHOD,
        "prior_strength": float(prior_strength),
        "min_graded_total": min_graded_total,
        "min_graded_per_market": min_graded_per_market,
        "leg_cap_pp": leg_cap_pp,
    }
    body = {
        "version": 1,
        "kind": KIND,
        "model_version": MODEL_VERSION,
        "method": METHOD,
        "trained_through": trained_through,
        "fitted_at": fitted,
        "expires_at": expires_at,
        "n_graded": len(eligible),
        "source": _text(source, name="source", maximum=120),
        "prior_strength": float(prior_strength),
        "min_graded_total": min_graded_total,
        "min_graded_per_market": min_graded_per_market,
        "leg_cap_pp": leg_cap_pp,
        "parlay_cap_pp": HARD_PARLAY_CAP_PP,
        "parlay_cap_rel": HARD_PARLAY_CAP_REL,
        "clamp": list(HARD_CLAMP),
        "eligibility_rules": list(ELIGIBILITY_RULES),
        "fit_inputs_hash": fingerprint(fit_inputs),
        "segments": segments,
        "sufficient_total": len(eligible) >= min_graded_total,
    }
    return {**body, "artifact_id": fingerprint(body)}


def fit_corrections_from_journal(journal, *, trained_through, expires_at, fitted_at=None,
                                 prior_strength=DEFAULT_PRIOR_STRENGTH,
                                 min_graded_total=DEFAULT_MIN_GRADED_TOTAL,
                                 min_graded_per_market=DEFAULT_MIN_GRADED_PER_MARKET,
                                 leg_cap_pp=HARD_LEG_CAP_PP):
    """Join independently attested publication and outcomes at one journal fence.

    A publication enum alone never establishes eligibility. Direction comes
    from the original frozen NFL source receipt, not invented feature fields.
    Generation, publication and revision knowledge must precede the cutoff.
    The sealed local artifact's fit_audit accounts for the frozen cohort.
    """
    from .nfl_calibration import _latest_as_of
    from .nfl_publication import validate_publication_attestation

    cutoff = instant(trained_through)
    reader = getattr(journal, "read_publication_fit_snapshot", None)
    if not callable(reader):
        raise CorrectionsError("publication_snapshot_required")
    snapshot = reader()
    if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or type(snapshot.get("watermark")) is not str or not snapshot["watermark"].isdigit()
            or any(type(snapshot.get(k)) is not list for k in
                   ("receipts", "generation_records", "revisions", "publication_attestations"))):
        raise CorrectionsError("invalid_publication_snapshot")
    # Never mix separate live readers with this pinned snapshot.
    receipts = {}
    for receipt in snapshot["receipts"]:
        try:
            validate_probability_receipt(receipt)
            if receipt.get("domain") != "nfl" or receipt.get("task") != "nfl.parlay_leg":
                continue
            rid = receipt["receipt_id"]
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
        if rid in receipts:
            raise CorrectionsError("duplicate_snapshot_receipt")
        receipts[rid] = receipt
    generations = {}
    for record in snapshot["generation_records"]:
        try:
            if instant(record["recorded_utc"]) >= cutoff:
                continue
            doc, ack, oid = record["payload"], record["ack"], record["object_id"]
            if (doc["envelope_id"] != oid
                    or fingerprint({k:v for k,v in doc.items() if k != "envelope_id"}) != oid
                    or len(doc["generations"]) != 1 or ack["object_id"] != oid or ack["durable"] is not True):
                continue
            gen = doc["generations"][0]
            from .nfl_generation import MANIFEST_KEYS
            if gen["generation_id"] != fingerprint({k:gen[k] for k in MANIFEST_KEYS}):
                continue
            if instant(gen["recorded_utc"]) >= cutoff:
                continue
            accepted = {r["receipt_id"]:r["source_receipt_id"] for r in ack["receipts"]}
            sources = {r["receipt_id"]:r for r in doc["receipts"]}
            ack_sources = list(accepted.values()) + [r["source_receipt_id"] for r in ack["rejections"]]
            if (len(accepted) != len(ack["receipts"]) or len(sources) != len(doc["receipts"])
                    or len(set(ack_sources)) != len(ack_sources) or set(ack_sources) != set(sources)
                    or gen["receipt_ids"] != sorted(sources)):
                continue
            generations[oid] = (gen, accepted, sources)
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    candidates = defaultdict(list)
    for proof in snapshot["publication_attestations"]:
        try:
            validate_publication_attestation(proof)
            if instant(proof["recorded_utc"]) >= cutoff:
                continue
            gen, accepted, sources = generations[proof["object_id"]]
            if (proof["generation_id"] != gen["generation_id"]
                    or proof["source_pool_id"] != gen["source_snapshots"]["leg_pool"]):
                continue
            partition = {row["receipt_id"]:row["source_receipt_id"]
                         for row in proof["receipts"] + proof["rejections"]}
            if partition != accepted:
                continue
            completed = proof["observation"]["observed_completed_utc"]
            if (instant(completed) >= cutoff or instant(gen["recorded_utc"])
                    > instant(proof["observation"]["observed_started_utc"])):
                continue
            for publication in proof["receipts"]:
                rid, sid = publication["receipt_id"], publication["source_receipt_id"]
                receipt = receipts.get(rid)
                source_row = sources.get(sid)
                if receipt is None or source_row is None or accepted.get(rid) != sid:
                    continue
                source = source_row["learning_receipt"]
                from .nfl import validate_nfl_receipt
                # NFL pool v1 omits these redundant fixed yardage fields.
                # Normalize only the validation view; explicit conflicts/nulls
                # win, and the original frozen leg/hash must remain untouched.
                leg = {"direction": "over", "comparison_operator": "gte", **source_row["leg"]}
                validate_nfl_receipt(source, leg, gen["season"], gen["week"])
                if (source["receipt_id"] != sid or receipt["source_receipt_id"] != sid
                        or receipt["contract_id"] != source["contract_id"]
                        or receipt["event_id"] != f'nfl:{gen["season"]}:game:{source["game_id"]}'
                        or source_row["generation_id"] != gen["generation_id"]
                        or source_row["recorded_utc"] != gen["recorded_utc"]
                        or instant(source["forecast_created_utc"]) != instant(gen["pool_generated_utc"])
                        or instant(source["forecast_created_utc"]) > instant(gen["recorded_utc"])
                        or source_row["kickoff_utc"] != receipt["event_start_utc"]
                        or any(fingerprint(receipt.get(k)) != fingerprint(source.get(k)) for k in
                               ("base_prob", "served_prob", "base_model_version", "calibrator_version",
                                "forecast_created_utc", "source_snapshot_id", "source_timestamps", "features"))
                        or publication["market"] != source["market"]
                        or publication["direction"] != source["direction"]
                        or publication["served_prob"] != receipt["served_prob"]
                        or publication["event_start_utc"] != receipt["event_start_utc"]):
                    continue
                if not (instant(source["forecast_created_utc"]) <= instant(completed)
                        <= instant(proof["recorded_utc"]) < instant(receipt["event_start_utc"])):
                    continue
                candidates[rid].append((proof, source, completed))
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    # Freeze the contract/event population BEFORE looking at outcomes. Refresh
    # vintages are repeated forecasts of the same contract, not extra samples;
    # a later resolved vintage cannot replace an earlier unresolved original.
    contracts = defaultdict(list)
    for rid, publications in sorted(candidates.items()):
        receipt = receipts[rid]
        proof, source, completed = min(publications, key=lambda p:
            (instant(p[0]["recorded_utc"]), p[0]["attestation_id"]))
        contracts[(receipt["contract_id"], receipt["event_id"])].append((rid, proof, source, completed))
    selected = []
    for _, vintages in sorted(contracts.items()):
        selected.append(min(vintages, key=lambda v:
            (instant(receipts[v[0]]["forecast_created_utc"]),
             instant(receipts[v[0]]["first_seen_utc"]), v[0])))
    cohort = [{"contract_id":receipts[rid]["contract_id"], "event_id":receipts[rid]["event_id"],
        "receipt_id":rid, "source_receipt_id":receipts[rid]["source_receipt_id"],
        "generation_object_id":proof["object_id"], "generation_id":proof["generation_id"],
        "source_pool_id":proof["source_pool_id"], "attestation_id":proof["attestation_id"],
        "policy_id":proof["policy_id"]} for rid,proof,_,_ in selected]
    histories = defaultdict(list)
    for revision in snapshot["revisions"]:
        try:
            histories[revision["resolution"]["receipt_id"]].append(revision)
        except (TypeError, KeyError):
            continue
    excluded = []
    graded, provenance = [], []
    for rid, proof, source, completed in sorted(selected, key=lambda v:v[0]):
        receipt = receipts[rid]
        try:
            head = _latest_as_of(receipt, histories[rid], cutoff)
        except (ValueError, TypeError, KeyError, OverflowError):
            excluded.append({"receipt_id": rid, "reason": "invalid_outcome_history"})
            continue
        reason = (_journal_label_reason(receipt, source, histories[rid], head, cutoff)
                  if head is not None else "unresolved_outcome")
        baseline = receipt["served_prob"]
        if reason is None and not 0 < baseline < 1:
            reason = "baseline_probability_boundary"
        if reason is not None:
            excluded.append({"receipt_id": rid, "revision_id": head["revision_id"] if head else None,
                             "reason": reason})
            continue
        resolution = head["resolution"]
        graded.append({"market": source["market"], "selection": source["direction"],
            "baseline_prob": float(baseline),
            "served_at": completed,
            "kickoff_at": receipt["event_start_utc"],
            "graded_at": resolution["resolved_utc"],
            "outcome": resolution["hit"]})
        provenance.append({"receipt_id": rid, "source_receipt_id": receipt["source_receipt_id"],
            "generation_object_id": proof["object_id"], "generation_id": proof["generation_id"],
            "source_pool_id": proof["source_pool_id"], "revision_id": head["revision_id"],
            "resolution_hash": fingerprint(resolution),
            "attestation_id": proof["attestation_id"], "policy_id": proof["policy_id"]})
    artifact = fit_corrections_artifact(
        graded,
        trained_through=trained_through,
        expires_at=expires_at,
        fitted_at=fitted_at,
        source=TRUSTED_ARTIFACT_SOURCE,
        prior_strength=prior_strength,
        min_graded_total=min_graded_total,
        min_graded_per_market=min_graded_per_market,
        leg_cap_pp=leg_cap_pp,
        allow_trusted_source=True,
    )
    if artifact["n_graded"] != len(graded):
        raise CorrectionsError("journal_fit_denominator_mismatch")
    body = {k:v for k,v in artifact.items() if k != "artifact_id"}
    audit = {"version": 1, "label_policy": "stable_player_ids_exact_fixture_v2",
             "forecast_cohort_count": len(cohort), "graded_count": len(graded),
             "excluded_count": len(excluded),
             "exclusion_reasons": dict(sorted(Counter(row["reason"] for row in excluded).items()))}
    body["fit_audit"] = audit
    body["fit_inputs_hash"] = fingerprint({"version": 3, "source": TRUSTED_ARTIFACT_SOURCE,
        "cohort_policy":"first_independently_attested_contract_event_v1", "forecast_cohort":cohort,
        "graded_inputs_hash": artifact["fit_inputs_hash"], "publication_provenance": provenance,
        "fit_audit": audit, "label_exclusions": excluded})
    return {**body, "artifact_id": fingerprint(body)}


def authorize_corrections_artifact(journal, artifact):
    """Recompute binder fit from live journal; require exact artifact_id match.

    A caller-controlled ``source`` string or content digest alone never
    authorizes apply. Returns the sealed artifact only when the journal as-of
    recomputation matches. Raises ``CorrectionsError`` on mismatch/shape fault.
    """
    if not isinstance(artifact, dict):
        raise CorrectionsError("invalid_artifact")
    if artifact.get("source") != TRUSTED_ARTIFACT_SOURCE:
        raise CorrectionsError("trusted_source_required_for_authorization")
    recomputed = fit_corrections_from_journal(
        journal,
        trained_through=artifact["trained_through"],
        expires_at=artifact["expires_at"],
        fitted_at=artifact["fitted_at"],
        prior_strength=artifact["prior_strength"],
        min_graded_total=artifact["min_graded_total"],
        min_graded_per_market=artifact["min_graded_per_market"],
        leg_cap_pp=artifact["leg_cap_pp"],
    )
    if recomputed != artifact:
        raise CorrectionsError("trusted_artifact_journal_mismatch")
    return artifact
