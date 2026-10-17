"""Pure NFL FINAL-evidence bridge to exact persisted prospective receipts.

No store, network, fitting or source discovery. Evidence must come from a trusted
NFL resolver; a caller's `authoritative` flag is not source authentication.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy

from .nfl import canonical_key, validate_nfl_receipt
from ..outcome_revisions import validate_journal_resolution
from ..probability_receipts import fingerprint, finite_number, instant, validate_probability_receipt

FINAL_STATUSES = frozenset(("FINAL", "STATUS_FINAL", "STATUS_FINAL_OVERTIME"))
VOID_REASONS = frozenset(("did_not_play", "official_void", "push"))
BRIDGE_VERSION = "nfl.prospective_resolution_v1"


def _text(value, reason):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(reason)
    return value


def _proposal(row):
    return {k: copy.deepcopy(row.get(k)) for k in ("revision_id", "supersedes", "reason", "resolution")}


def _history(rows, receipt, observed):
    """Validate an entire chain without relying on caller array order."""
    nodes, source_revisions = {}, {}
    for row in rows:
        if "version" in row and (type(row["version"]) is not int or row["version"] != 1):
            raise ValueError("unsupported_existing_revision_version")
        rid = _text(row.get("revision_id"), "invalid_existing_revision_id")
        if len(rid) > 200:
            raise ValueError("invalid_existing_revision_id")
        proposal = _proposal(row)
        validate_journal_resolution(receipt, proposal["resolution"])
        if proposal["reason"] is not None:
            _text(proposal["reason"], "invalid_existing_revision_reason")
        if instant(proposal["resolution"]["resolved_utc"]) > observed:
            raise ValueError("existing_revision_after_observation")
        if ("evidence_observed_utc" in proposal["resolution"]
                and instant(proposal["resolution"]["evidence_observed_utc"]) > observed):
            raise ValueError("existing_revision_after_observation")
        value = proposal["resolution"]
        if "source_revision_id" in value:
            source_key = (value["source"], _text(value["source_revision_id"], "invalid_existing_source_revision_id"))
            payload = {k: v for k, v in value.items() if k != "evidence_observed_utc"}
            if source_key in source_revisions and source_revisions[source_key] != payload:
                raise ValueError("conflicting_existing_official_source_revision")
            source_revisions[source_key] = payload
        if rid in nodes and nodes[rid] != proposal:
            raise ValueError("conflicting_existing_revision_id")
        nodes[rid] = proposal
    if not nodes:
        return {}, None
    referenced = []
    for node in nodes.values():
        parent = node["supersedes"]
        if parent is not None:
            if not isinstance(parent, str) or parent not in nodes:
                raise ValueError("incomplete_existing_revision_chain")
            _text(node["reason"], "missing_existing_correction_reason")
            if instant(node["resolution"]["resolved_utc"]) < instant(nodes[parent]["resolution"]["resolved_utc"]):
                raise ValueError("existing_revision_time_regressed")
            child_value, parent_value = node["resolution"], nodes[parent]["resolution"]
            if ("source_updated_utc" in child_value and "source_updated_utc" in parent_value
                    and instant(child_value["source_updated_utc"]) < instant(parent_value["source_updated_utc"])):
                raise ValueError("existing_revision_source_time_regressed")
            if ("evidence_observed_utc" in child_value and "evidence_observed_utc" in parent_value
                    and instant(child_value["evidence_observed_utc"]) < instant(parent_value["evidence_observed_utc"])):
                raise ValueError("existing_revision_observation_time_regressed")
            referenced.append(parent)
    heads = set(nodes) - set(referenced)
    if len(heads) != 1 or len(referenced) != len(set(referenced)):
        raise ValueError("conflicting_existing_revision_heads")
    head = next(iter(heads))
    seen, current = set(), head
    while current is not None:
        if current in seen:
            raise ValueError("cyclic_existing_revision_chain")
        seen.add(current)
        current = nodes[current]["supersedes"]
    if len(seen) != len(nodes):
        raise ValueError("disconnected_existing_revision_chain")
    return nodes, nodes[head]


def _evidence(receipt, source, leg, game, observed):
    season, week = source.get("season"), source.get("week")
    if (type(week) is not int or week < 1 or type(leg.get("season")) is not int
            or type(leg.get("week")) is not int or leg.get("season") != season or leg.get("week") != week):
        raise ValueError("score_period_mismatch")
    normalized_leg = dict(leg)
    normalized_leg.setdefault("model_prob", source.get("served_prob"))
    key = validate_nfl_receipt(source, normalized_leg, season, week)
    if canonical_key(leg, season) != key:
        raise ValueError("score_contract_mismatch")
    if receipt["domain"] != "nfl" or receipt["task"] != "nfl.parlay_leg":
        raise ValueError("unsupported_receipt_task")
    event = f"nfl:{season}:game:{key[1]}"
    if receipt["event_id"] != event or receipt["contract_id"] != source["contract_id"]:
        raise ValueError("persisted_contract_or_event_mismatch")
    for name in ("base_prob", "served_prob", "base_model_version", "calibrator_version",
                 "forecast_created_utc", "source_snapshot_id", "source_timestamps", "features", "publication_status"):
        if receipt[name] != source.get(name):
            raise ValueError("persisted_source_" + name + "_mismatch")
    if receipt["source_receipt_id"] != source["receipt_id"]:
        raise ValueError("persisted_source_receipt_mismatch")
    if not isinstance(game, dict) or game.get("status") not in FINAL_STATUSES:
        raise ValueError("game_not_final")
    for name, expected in (("game_id", key[1]), ("season", season), ("week", week)):
        if name in game and (type(game[name]) is not type(expected) or game[name] != expected):
            raise ValueError("final_game_identity_mismatch")
    kickoff, final = instant(game.get("kickoff_utc")), instant(game.get("final_utc"))
    if kickoff != instant(receipt["event_start_utc"]) or final < kickoff:
        raise ValueError("final_game_timeline_mismatch")
    game_source = _text(game.get("source"), "missing_final_game_source")
    game_updated = instant(game.get("source_updated_utc"))
    if game_updated < final:
        raise ValueError("game_source_before_final")
    if leg.get("status") not in FINAL_STATUSES or leg.get("authoritative") is not True:
        raise ValueError("missing_authoritative_final_stats")
    stat_source = _text(leg.get("source"), "missing_stat_source")
    revision = _text(leg.get("source_revision_id"), "missing_source_revision_id")
    stat_updated = instant(leg.get("source_updated_utc"))
    if stat_updated < final:
        raise ValueError("stat_source_before_final")
    if max(final, stat_updated, game_updated) > observed:
        raise ValueError("source_after_observation")
    result = leg.get("result")
    if result == "void":
        reason = leg.get("reason")
        if reason not in VOID_REASONS:
            raise ValueError("unsupported_void_reason")
        if reason == "did_not_play" and leg.get("played") is not False:
            raise ValueError("dnp_requires_explicit_nonparticipation")
        if leg.get("actual") is not None:
            raise ValueError("void_actual_must_be_null")
        status, hit = "VOID", None
    else:
        if result not in ("hit", "miss"):
            raise ValueError("unresolved_or_nonbinary_grade")
        if leg.get("played") is not True:
            raise ValueError("missing_participation_or_dnp")
        if not finite_number(leg.get("actual")):
            raise ValueError("missing_actual_stat")
        # The NFL contract is over/gte, and negative yardage is a real stat.
        hit = leg["actual"] >= source["line"]
        if (result == "hit") != hit:
            raise ValueError("grade_actual_mismatch")
        status, reason = "FINAL", None
    resolved = max((game["final_utc"], leg["source_updated_utc"], game["source_updated_utc"]), key=instant)
    return {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
            "event_id": receipt["event_id"], "status": status, "hit": hit, "resolved_utc": resolved,
            "source": stat_source, "source_revision_id": revision,
            "source_updated_utc": leg["source_updated_utc"], "actual": leg.get("actual"),
            "reason": reason, "authoritative": True, "final_evidence_status": "FINAL",
            "game_evidence": {"status": game["status"], "kickoff_utc": game["kickoff_utc"],
                              "final_utc": game["final_utc"], "source": game_source,
                              "source_updated_utc": game["source_updated_utc"]}}


def resolve_prospective(receipts, source_receipts, scored_legs, game_evidence, *,
                        observed_utc, existing_revisions=()):
    """Return append_resolution-compatible proposals and explicit exclusions.

    Original source receipts and canonical scored legs are mandatory. No source
    discovery, display-card filtering or assumption that absent statistics are
    zero. Callers retain all receipts, including ones with no outcome proposal.
    A changed official source revision needs a correction_reason; a replay reuses
    its original immutable chain link. Conflicting duplicates quarantine every
    affected contract rather than selecting whichever appeared last.
    """
    observed = instant(observed_utc)
    rejected, persisted, sources, grades, histories = [], defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)

    def reject(reason, sid=None):
        rejected.append({"reason": str(reason), "source_receipt_id": sid if isinstance(sid, str) else None})

    for receipt in receipts:
        try:
            validate_probability_receipt(receipt)
            if receipt["domain"] == "nfl" and receipt["task"] == "nfl.parlay_leg":
                persisted[receipt["source_receipt_id"]].append(receipt)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(exc)
    for source in source_receipts:
        if not isinstance(source, dict) or not isinstance(source.get("receipt_id"), str):
            reject("invalid_source_receipt")
        else:
            sources[source["receipt_id"]].append(source)
    for row in existing_revisions:
        if not isinstance(row, dict) or not isinstance(row.get("resolution"), dict) or not isinstance(row["resolution"].get("receipt_id"), str):
            raise ValueError("malformed_existing_revision_history")
        histories[row["resolution"]["receipt_id"]].append(row)
    for leg in scored_legs:
        if not isinstance(leg, dict):
            reject("invalid_scored_leg")
            continue
        sid = leg.get("source_receipt_id", leg.get("receipt_id"))
        if not isinstance(sid, str):
            reject("missing_score_source_receipt_id")
        elif "receipt_id" in leg and leg["receipt_id"] != sid:
            reject("conflicting_score_receipt_ids", sid)
            grades[sid].append(None)
        else:
            grades[sid].append(leg)

    candidates, contract_states, quarantined_contracts = {}, defaultdict(set), set()
    for sid in sorted(grades):
        affected_contracts = set()
        try:
            known, original = persisted.get(sid, []), sources.get(sid, [])
            affected_contracts = {r["contract_id"] for r in known}
            if not known:
                raise ValueError("missing_persisted_receipt")
            if not original:
                raise ValueError("missing_original_source_receipt")
            if len({fingerprint(r) for r in known}) != 1 or len({fingerprint(r) for r in original}) != 1:
                raise ValueError("conflicting_source_or_persisted_receipts")
            receipt, source = known[0], original[0]
            evidence, reasons = {}, set()
            for leg in grades[sid]:
                if leg is None:
                    raise ValueError("conflicting_score_receipt_ids")
                item = _evidence(receipt, source, leg, game_evidence.get(source.get("game_id")), observed)
                validate_journal_resolution(receipt, item)
                evidence[fingerprint(item)] = item
                reasons.add(leg.get("correction_reason"))
            if len(evidence) != 1 or len(reasons) != 1:
                raise ValueError("conflicting_duplicate_outcomes")
            item = next(iter(evidence.values()))
            contract_states[receipt["contract_id"]].add(fingerprint({k: item[k] for k in ("status", "hit", "actual", "reason")}))
            candidates[sid] = (receipt, item, next(iter(reasons)))
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
            # A malformed/contradictory copy cannot make another model vintage
            # of this same contract look unambiguous by merely being discarded.
            quarantined_contracts.update(affected_contracts)
            reject(exc, sid)

    staged = []
    for sid, (receipt, evidence, correction_reason) in sorted(candidates.items()):
        try:
            if len(contract_states[receipt["contract_id"]]) != 1:
                raise ValueError("conflicting_contract_outcomes")
            if receipt["contract_id"] in quarantined_contracts:
                raise ValueError("quarantined_contract_evidence")
            nodes, head = _history(histories.get(receipt["receipt_id"], []), receipt, observed)
            revision_id = BRIDGE_VERSION + ":" + fingerprint(evidence)
            if revision_id in nodes:
                expected = {k: v for k, v in nodes[revision_id]["resolution"].items() if k != "evidence_observed_utc"}
                if expected != evidence:
                    raise ValueError("existing_revision_payload_conflict")
                staged.append((sid, nodes[revision_id], True))
                continue
            for old in nodes.values():
                value = old["resolution"]
                if (value.get("source"), value.get("source_revision_id")) == (evidence["source"], evidence["source_revision_id"]):
                    raise ValueError("conflicting_official_source_revision")
            if head is not None:
                _text(correction_reason, "correction_reason_required")
                if instant(evidence["resolved_utc"]) < instant(head["resolution"]["resolved_utc"]):
                    raise ValueError("correction_time_regressed")
                previous_update = head["resolution"].get("source_updated_utc")
                if previous_update is not None and instant(evidence["source_updated_utc"]) < instant(previous_update):
                    raise ValueError("correction_source_time_regressed")
            resolution = {**copy.deepcopy(evidence), "evidence_observed_utc": observed_utc}
            staged.append((sid, {"revision_id": revision_id, "supersedes": head["revision_id"] if head else None,
                                 "reason": correction_reason if head else None, "resolution": resolution}, False))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            quarantined_contracts.add(receipt["contract_id"])
            reject(exc, sid)
    # A later-sorted vintage may reveal a chain/revision conflict. Do not let
    # earlier staging order publish a partial, contradictory contract update.
    proposals, replayed = [], 0
    for sid, proposal, replay in staged:
        if proposal["resolution"]["contract_id"] in quarantined_contracts:
            reject("quarantined_contract_evidence", sid)
        else:
            proposals.append(proposal)
            replayed += int(replay)
    # A missing grade is an explicit unresolved exclusion, never a loss.
    for sid in sorted(set(persisted) - set(grades)):
        reject("no_final_grade", sid)
    rejected.sort(key=lambda row: (row["reason"], str(row["source_receipt_id"])))
    return {"version": 1, "adapter": BRIDGE_VERSION, "proposals": proposals, "rejections": rejected,
            "summary": {"persisted_source_receipts": len(persisted), "proposed_revisions": len(proposals),
                        "replayed_revisions": replayed, "excluded_records": len(rejected),
                        "exclusion_reasons": dict(sorted(Counter(r["reason"] for r in rejected).items())),
                        "writes": False}}
