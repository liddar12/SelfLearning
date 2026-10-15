"""Shadow-only NFL base-logit calibration on immutable prospective receipts.

This is NOT generic confidence-Platt, and never authorizes production serving.
The caller supplies persisted receipts, original NFL source receipts and complete
database-recorded revision histories. Hashes validate integrity, not authenticity.
Pool-rung and actually offered populations cannot be silently interchanged.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import argparse
import copy
import json
import math
from pathlib import Path
import random
import re
import sys

from .nfl import adapt_prospective, validate_nfl_receipt, _prospective_prediction
from ..probability_receipts import fingerprint, finite_number, instant, validate_probability_receipt

KIND = "nfl.my_props.base_logit.v1"
TASK = "nfl.parlay_leg"
RECEIPT_POLICY = "base_receipt_v1"
MIN_LEGS = 100
MIN_HELD_OUT = 2
PROPOSED_MARGIN = .0015  # additional proposed return policy; NOT an existing MY rule
POSITIONS = {"qb_pass_yds": "QB", "rb_rush_yds": "RB", "wr_rec_yds": "WR"}
TRANSFORM = {"name": "sigmoid_intercept_plus_slope_logit", "input_stage": "immutable_base_prob",
             "input_clip": [.0001, .9999], "output_stage": "served_prob",
             "output_clamp": [.05, .95], "output_decimals": 4, "coefficient_decimals": 6}
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _sigmoid(x):
    if x >= 0:
        return 1 / (1 + math.exp(-x))
    e = math.exp(x)
    return e / (1 + e)


def _logit(p):
    p = min(max(p, .0001), .9999)
    return math.log(p / (1 - p))


def serve_base(base_probability, coefficients=None):
    """NFL-compatible transform: base in, one correction, clamp, round out."""
    if not finite_number(base_probability) or not 0 < base_probability < 1:
        raise ValueError("invalid_base_probability")
    p = float(base_probability)
    if coefficients is not None:
        if (not isinstance(coefficients, dict) or set(coefficients) != {"a", "b"}
                or not all(finite_number(coefficients.get(k)) for k in ("a", "b"))
                or coefficients["b"] <= 0):
            raise ValueError("invalid_nfl_coefficients")
        p = _sigmoid(coefficients["a"] + coefficients["b"] * _logit(p))
    return round(min(max(p, .05), .95), 4)


def validate_candidate(candidate, *, expected_base_version=None):
    """Validate a typed shadow artifact. Success is NOT approval to serve it."""
    if (not isinstance(candidate, dict) or type(candidate.get("version")) is not int
            or candidate["version"] != 1 or candidate.get("kind") != KIND
            or candidate.get("task") != TASK or candidate.get("state") != "shadow"
            or candidate.get("evidence_status") != "retrospective_reconstruction"
            or "created_utc" in candidate
            or candidate.get("automatic_promotion") is not False
            or candidate.get("receipt_policy") != RECEIPT_POLICY
            or candidate.get("transform") != TRANSFORM):
        raise ValueError("incompatible_nfl_candidate")
    base = candidate.get("base_model_version")
    if not isinstance(base, str) or not _HASH.fullmatch(base):
        raise ValueError("invalid_base_model_version")
    if expected_base_version is not None and base != expected_base_version:
        raise ValueError("candidate_base_version_mismatch")
    if not isinstance(candidate.get("coefficients"), dict):
        raise ValueError("missing_nfl_coefficients")
    serve_base(.5, candidate["coefficients"])
    if any(round(v, 6) != v for v in candidate["coefficients"].values()):
        raise ValueError("unrounded_coefficients")
    manifest = candidate.get("training")
    if (not isinstance(manifest, dict) or manifest.get("population") not in ("offered_my", "eligible_pool")
            or manifest.get("membership_policy") not in ("first_envelope_capture_v1", "committed_generations_v1")
            or not isinstance(manifest.get("rows"), list) or len(manifest["rows"]) < MIN_LEGS
            or type(manifest.get("n")) is not int or manifest["n"] != len(manifest["rows"])
            or manifest.get("dataset_id") != fingerprint(manifest["rows"])):
        raise ValueError("invalid_training_manifest")
    cutoff = instant(manifest.get("cutoff_utc"))
    if instant(candidate.get("fit_as_of_utc")) != cutoff:
        raise ValueError("candidate_fit_asof_mismatch")
    if (manifest.get("fit_rule") != "ridge_newton_logit_base_v1"
            or manifest.get("ridge") != .001
            or any(not isinstance(r, dict) for r in manifest["rows"])
            or any(not isinstance(r.get("contract_id"), str) for r in manifest["rows"])
            or len({r.get("contract_id") for r in manifest["rows"]}) != manifest["n"]):
        raise ValueError("invalid_training_policy_or_duplicates")
    for row in manifest["rows"]:
        created = instant(row.get("forecast_created_utc"))
        seen = instant(row.get("first_seen_utc"))
        start = instant(row.get("event_start_utc"))
        resolved = instant(row.get("resolved_utc"))
        recorded = instant(row.get("outcome_recorded_utc"))
        if (not all(isinstance(row.get(k), str) and _HASH.fullmatch(row[k])
                    for k in ("receipt_id", "source_receipt_id", "contract_id", "revision_hash"))
                or not isinstance(row.get("revision_id"), str) or not row["revision_id"]
                or not created <= seen < start <= resolved <= recorded < cutoff
                or type(row.get("season")) is not int or row["season"] < 2000
                or type(row.get("week")) is not int or not 1 <= row["week"] <= 18
                or row.get("position") not in POSITIONS.values()
                or not isinstance(row.get("event_id"), str)
                or not row["event_id"].startswith(f"nfl:{row['season']}:game:")
                or not row["event_id"].removeprefix(f"nfl:{row['season']}:game:")
                or row.get("correlation_group") != row["event_id"]
                or row.get("base_model_version") != base
                or not finite_number(row.get("base_prob")) or not 0 < row["base_prob"] < 1
                or not finite_number(row.get("served_prob")) or not 0 <= row["served_prob"] <= 1
                or type(row.get("hit")) is not bool):
            raise ValueError("invalid_training_row")
        if manifest["population"] == "offered_my" and manifest["membership_policy"] == "committed_generations_v1":
            offer = row.get("offering")
            if (not isinstance(offer, dict)
                    or not isinstance(offer.get("card_ids"), list) or not offer["card_ids"]
                    or not all(isinstance(card, str) and card.strip() for card in offer["card_ids"])
                    or len(set(offer["card_ids"])) != len(offer["card_ids"])
                    or not all(isinstance(offer.get(k), str) and _HASH.fullmatch(offer[k])
                               for k in ("object_id", "generation_id"))
                    or not created <= instant(offer.get("captured_utc")) < start
                    or not instant(offer["captured_utc"]) <= instant(offer.get("recorded_utc")) < cutoff):
                raise ValueError("invalid_offering_evidence")
    if candidate.get("candidate_id") != fingerprint({k: v for k, v in candidate.items() if k != "candidate_id"}):
        raise ValueError("candidate_hash_mismatch")


def _latest_as_of(receipt, revisions, cutoff):
    """Validate the full chain known BEFORE cutoff, never future corrections."""
    from ..outcome_revisions import validate_journal_resolution
    known, source_versions = {}, {}
    for row in revisions:
        recorded = instant(row.get("recorded_utc"))
        if recorded >= cutoff:
            continue
        rid = row.get("revision_id")
        if not isinstance(rid, str) or not rid or type(row.get("version")) is not int or row["version"] != 1:
            raise ValueError("invalid_revision_identity")
        validate_journal_resolution(receipt, row.get("resolution"))
        if instant(row["resolution"]["resolved_utc"]) > recorded:
            raise ValueError("resolution_after_database_observation")
        if ("evidence_observed_utc" in row["resolution"]
                and instant(row["resolution"]["evidence_observed_utc"]) > recorded):
            raise ValueError("evidence_after_database_observation")
        outcome = row["resolution"]
        if "source_revision_id" in outcome:
            if not isinstance(outcome["source_revision_id"], str) or not outcome["source_revision_id"].strip():
                raise ValueError("invalid_official_source_revision_id")
            source_key = (outcome["source"], outcome["source_revision_id"])
            semantic = fingerprint({k:v for k,v in outcome.items() if k != "evidence_observed_utc"})
            if source_key in source_versions and source_versions[source_key] != semantic:
                raise ValueError("conflicting_official_source_revision")
            source_versions[source_key] = semantic
        if rid in known and known[rid] != row:
            raise ValueError("revision_id_conflict")
        known[rid] = row
    if not known:
        return None
    children, roots = defaultdict(list), []
    for rid, row in known.items():
        parent = row.get("supersedes")
        if parent is None:
            roots.append(rid)
            continue
        if parent not in known:
            raise ValueError("missing_revision_predecessor")
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("correction_reason_required")
        previous = known[parent]
        if (instant(row["recorded_utc"]) < instant(previous["recorded_utc"])
                or instant(row["resolution"]["resolved_utc"]) < instant(previous["resolution"]["resolved_utc"])):
            raise ValueError("revision_time_regressed")
        for clock in ("source_updated_utc", "evidence_observed_utc"):
            if clock in previous["resolution"] and (clock not in row["resolution"]
                    or instant(row["resolution"][clock]) < instant(previous["resolution"][clock])):
                raise ValueError("revision_evidence_clock_regressed")
        children[parent].append(rid)
    if len(roots) != 1 or any(len(c) > 1 for c in children.values()):
        raise ValueError("revision_chain_fork")
    cursor, seen = roots[0], set()
    while cursor not in seen:
        seen.add(cursor)
        if not children[cursor]:
            if len(seen) != len(known):
                raise ValueError("disconnected_revision_chain")
            return known[cursor]
        cursor = children[cursor][0]
    raise ValueError("revision_chain_cycle")


def _offering_memberships(generation_records, receipts, cutoff, reasons):
    """Read committed-generation observations; never mutate the first envelope.

    The caller must use durable store commit records, not arbitrary upload ACKs.
    As elsewhere, hashes alone do not authenticate these observations. Adapter
    validation checks each source/manifest link; the hosted boundary separately
    verifies the exact stored source bodies before allowing a generation commit.
    """
    memberships = defaultdict(list)
    known, conflicts = {}, set()
    persisted = {}
    for receipt in receipts:
        try:
            validate_probability_receipt(receipt)
            persisted[receipt["source_receipt_id"]] = receipt["receipt_id"]
        except (ValueError, TypeError, KeyError, OverflowError):
            continue
    for row in generation_records:
        try:
            if not isinstance(row, dict):
                raise ValueError("invalid_generation_commit_record")
            recorded = instant(row.get("recorded_utc"))
            if recorded >= cutoff:
                continue
            doc = row.get("payload")
            if (not isinstance(doc, dict) or row.get("object_id") != doc.get("envelope_id")
                    or row["object_id"] != fingerprint({k:v for k,v in doc.items() if k != "envelope_id"})):
                raise ValueError("invalid_generation_commit_hash")
            oid = row["object_id"]
            if oid in known:
                if known[oid] != row:
                    conflicts.add(oid)
                continue
            known[oid] = row
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reasons[str(exc)] += 1
    for oid, row in sorted(known.items()):
        if oid in conflicts:
            reasons["conflicting_generation_commit"] += 1
            continue
        try:
            doc, recorded = row["payload"], instant(row["recorded_utc"])
            ack = row.get("ack")
            if (not isinstance(ack, dict) or type(ack.get("version")) is not int or ack["version"] != 1
                    or ack.get("object_id") != oid or ack.get("durable") is not True
                    or not isinstance(ack.get("receipts"), list) or not isinstance(ack.get("rejections"), list)):
                raise ValueError("invalid_generation_commit_ack")
            accepted_ids, rejected_ids = [], []
            expected = [entry["receipt_id"] for entry in doc["receipts"]]
            for accepted in ack["receipts"]:
                if (not isinstance(accepted, dict) or accepted.get("status") not in ("recorded", "duplicate")
                        or not isinstance(accepted.get("source_receipt_id"), str)
                        or persisted.get(accepted["source_receipt_id"]) != accepted.get("receipt_id")):
                    raise ValueError("generation_ack_receipt_mismatch")
                accepted_ids.append(accepted["source_receipt_id"])
            for rejected in ack["rejections"]:
                if (not isinstance(rejected, dict) or not isinstance(rejected.get("source_receipt_id"), str)
                        or not isinstance(rejected.get("reason"), str) or not rejected["reason"].strip()):
                    raise ValueError("invalid_generation_rejection")
                rejected_ids.append(rejected["source_receipt_id"])
            all_ids = accepted_ids + rejected_ids
            if (len(set(expected)) != len(expected) or len(set(all_ids)) != len(all_ids)
                    or set(all_ids) != set(expected)):
                raise ValueError("generation_ack_partition_mismatch")
            batch = adapt_prospective(journal_docs=[doc], existing_receipts=receipts)
            accepted = {r["source_receipt_id"] for r in batch["receipts"]} & set(accepted_ids)
            staged = defaultdict(list)
            for entry in doc["receipts"]:
                if not isinstance(entry, dict) or entry.get("receipt_id") not in accepted:
                    continue
                sid = entry["receipt_id"]
                matches = [g for g in doc["generations"] if isinstance(g, dict)
                           and g.get("generation_id") == entry["generation_id"]]
                if len(matches) != 1:
                    raise ValueError("ambiguous_offering_generation")
                gen = matches[0]
                captured = instant(gen["recorded_utc"])
                if captured > recorded:
                    raise ValueError("offering_after_database_observation")
                cards = sorted({c["card_id"] for c in gen["offered_cards"] if sid in c["receipt_ids"]})
                if cards:
                    staged[sid].append({"object_id":oid, "generation_id":gen["generation_id"],
                        "captured_utc":gen["recorded_utc"], "recorded_utc":row["recorded_utc"], "card_ids":cards})
            for sid, rows in staged.items():
                memberships[sid].extend(rows)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reasons[str(exc)] += 1
    for rows in memberships.values():
        rows.sort(key=lambda r:(instant(r["captured_utc"]), instant(r["recorded_utc"]), r["object_id"]))
    return memberships


def build_cohort(receipts, source_receipts, revisions, *, as_of_utc, population, generation_records=None):
    """Freeze first-eligible forecast BEFORE joining outcomes, one per contract.

    `revisions` are complete flattened ReceiptJournal.resolution_history() rows.
    Database observation time, not a backdated source result, controls as-of use.
    Later available outcomes never cause a later forecast to replace an unresolved
    initial one. All stages are analysis-only and explicitly internally captured.
    """
    if population not in ("offered_my", "eligible_pool"):
        raise ValueError("explicit_supported_population_required")
    receipts = list(receipts)
    cutoff = instant(as_of_utc)
    reasons = Counter()
    policy = "first_envelope_capture_v1" if generation_records is None else "committed_generations_v1"
    offerings = None if generation_records is None else _offering_memberships(generation_records, receipts, cutoff, reasons)
    sources, bad_sources = {}, set()
    for src in source_receipts:
        if not isinstance(src, dict) or not isinstance(src.get("receipt_id"), str):
            reasons["invalid_source_receipt"] += 1
            continue
        sid = src["receipt_id"]
        if sid in sources and sources[sid] != src:
            bad_sources.add(sid)
        sources[sid] = src
    indexed = defaultdict(list)
    for revision in revisions:
        if not isinstance(revision, dict) or not isinstance(revision.get("resolution"), dict):
            reasons["invalid_revision"] += 1
            continue
        rid = revision["resolution"].get("receipt_id")
        if not isinstance(rid, str):
            reasons["invalid_revision_receipt_id"] += 1
            continue
        indexed[rid].append(revision)
    candidates, receipt_payloads, unverified_lineage = defaultdict(list), {}, defaultdict(list)
    for receipt in receipts:
        lineage_identity = None
        try:
            validate_probability_receipt(receipt)
            if receipt["domain"] != "nfl" or receipt["task"] != TASK:
                raise ValueError("unsupported_task")
            if instant(receipt["first_seen_utc"]) >= cutoff:
                continue  # future captures do not alter a past fit/manifest
            rid = receipt["receipt_id"]
            if rid in receipt_payloads:
                if receipt_payloads[rid] != receipt:
                    raise ValueError("receipt_id_conflict")
                continue
            receipt_payloads[rid] = receipt
            sid = receipt["source_receipt_id"]
            provenance = receipt.get("capture_provenance") or {}
            if (not isinstance(provenance, dict) or provenance.get("kind") != "nfl_generation_journal"
                    or provenance.get("population") != "eligible_pool_prop_rungs_before_display_dedupe"
                    or not isinstance(provenance.get("offered_card_ids"), list)
                    or not all(isinstance(c, str) and c for c in provenance["offered_card_ids"])):
                raise ValueError("missing_declared_capture_population")
            if population == "offered_my" and not (offerings.get(sid) if offerings is not None else provenance["offered_card_ids"]):
                reasons["not_in_offered_population"] += 1
                continue
            # A missing original source must not silently make a later forecast
            # the first one. Quarantine if this lineage gap could be earliest.
            lineage_identity = (receipt["contract_id"], instant(receipt["forecast_created_utc"]))
            if sid not in sources or sid in bad_sources:
                raise ValueError("missing_or_conflicting_source_receipt")
            src = sources[sid]
            if type(src.get("week")) is not int or not 1 <= src["week"] <= 18:
                raise ValueError("invalid_nfl_week")
            leg = {**src, "model_prob": receipt["served_prob"], "learning_receipt": src}
            key = validate_nfl_receipt(src, leg, src.get("season"), src["week"])
            for name in ("contract_id", "base_prob", "served_prob", "base_model_version", "calibrator_version",
                         "forecast_created_utc", "source_snapshot_id", "source_timestamps", "features", "publication_status"):
                if receipt[name] != src[name]:
                    raise ValueError("source_envelope_parity_mismatch")
            _prospective_prediction(receipt, src, src["season"], src["week"])
            if (receipt["event_id"] != f"nfl:{key[0]}:game:{key[1]}"
                    or receipt["correlation_group"] != receipt["event_id"]):
                raise ValueError("event_or_group_mismatch")
            candidates[receipt["contract_id"]].append((receipt, src))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reasons[str(exc)] += 1
            if lineage_identity is not None:
                unverified_lineage[lineage_identity[0]].append(lineage_identity[1])
    rows, forecasts = [], []
    for contract_id, versions in sorted(candidates.items()):
        versions.sort(key=lambda x: (instant(x[0]["forecast_created_utc"]), instant(x[0]["first_seen_utc"]), x[0]["receipt_id"]))
        first = instant(versions[0][0]["forecast_created_utc"])
        if any(stamp <= first for stamp in unverified_lineage[contract_id]):
            reasons["earliest_source_lineage_unverified"] += 1
            continue
        earliest = [r for r, _ in versions if instant(r["forecast_created_utc"]) == first]
        if len({fingerprint([r[k] for k in ("base_prob", "served_prob", "base_model_version", "calibrator_version", "features")]) for r in earliest}) != 1:
            reasons["conflicting_first_forecast"] += 1
            continue
        receipt, src = versions[0]
        # Freeze the chronological population BEFORE joining outcomes. A still
        # unresolved early forecast must still constrain that week's fit cutoff.
        forecasts.append({"receipt_id": receipt["receipt_id"], "contract_id": contract_id,
            "season": src["season"], "week": src["week"], "event_id": receipt["event_id"],
            "forecast_created_utc": receipt["forecast_created_utc"],
            "first_seen_utc": receipt["first_seen_utc"], "event_start_utc": receipt["event_start_utc"]})
        try:
            dispositions = set()
            for vintage, _ in versions:
                known = _latest_as_of(vintage, indexed.get(vintage["receipt_id"], []), cutoff)
                if known is not None:
                    result = known["resolution"]
                    dispositions.add((result["status"], result["hit"]))
            if len(dispositions) > 1:
                raise ValueError("conflicting_contract_outcomes")
            revision = _latest_as_of(receipt, indexed.get(receipt["receipt_id"], []), cutoff)
            if revision is None:
                raise ValueError("unresolved_at_cutoff")
            outcome = revision["resolution"]
            if outcome.get("status") != "FINAL" or type(outcome.get("hit")) is not bool:
                raise ValueError("non_binary_latest_revision")
            rows.append({"receipt_id": receipt["receipt_id"], "source_receipt_id": src["receipt_id"],
                "contract_id": contract_id, "event_id": receipt["event_id"],
                "correlation_group": receipt["correlation_group"], "season": src["season"], "week": src["week"],
                "position": POSITIONS[src["market"]], "base_prob": receipt["base_prob"],
                "served_prob": receipt["served_prob"], "hit": outcome["hit"],
                "forecast_created_utc": receipt["forecast_created_utc"], "event_start_utc": receipt["event_start_utc"],
                "first_seen_utc": receipt["first_seen_utc"], "base_model_version": receipt["base_model_version"],
                "revision_id": revision["revision_id"], "revision_hash": fingerprint(revision),
                "outcome_recorded_utc": revision["recorded_utc"], "resolved_utc": outcome["resolved_utc"]})
            if population == "offered_my" and offerings is not None:
                rows[-1]["offering"] = copy.deepcopy(offerings[src["receipt_id"]][0])
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reasons[str(exc)] += 1
    return {"version": 1, "population": population, "as_of_utc": as_of_utc, "n": len(rows),
            "dataset_id": fingerprint(rows), "rows": rows, "excluded": dict(sorted(reasons.items())),
            "forecasts": forecasts, "forecast_dataset_id": fingerprint(forecasts),
            "membership_policy":policy,
            "offering_membership_coverage":"committed_generation_records_supplied" if offerings is not None else "first_capture_only_later_membership_unknown",
            "publication": "internally captured; public serving not verified"}


def _fit(rows):
    """Ridge Newton fit on logit(BASE); deterministic, never generic confidence."""
    pts = [(_logit(r["base_prob"]), int(r["hit"])) for r in rows]
    a, b, ridge = 0., 1., .001

    def objective(aa, bb):
        return sum(max(aa + bb*x, 0) - y*(aa + bb*x) + math.log1p(math.exp(-abs(aa + bb*x)))
                   for x, y in pts) + ridge/2*(aa*aa + (bb-1)**2)

    for _ in range(80):
        ga, gb, haa, hab, hbb = ridge*a, ridge*(b-1), ridge, 0., ridge
        for x, y in pts:
            p = _sigmoid(a+b*x)
            v = p*(1-p)
            ga += p-y; gb += (p-y)*x
            haa += v; hab += v*x; hbb += v*x*x
        det = haa*hbb-hab*hab
        if not math.isfinite(det) or det <= 1e-14:
            raise ValueError("singular_calibration_fit")
        da, db = (hbb*ga-hab*gb)/det, (haa*gb-hab*ga)/det
        before, scale = objective(a,b), 1.
        while scale > 1e-8 and objective(a-scale*da,b-scale*db) > before:
            scale /= 2
        if scale <= 1e-8:
            break
        a -= scale*da; b -= scale*db
        if max(abs(scale*da), abs(scale*db)) < 1e-9:
            break
    coefficients = {"a": round(a,6), "b": round(b,6)}
    serve_base(.5,coefficients)  # fail a nonpositive slope; never clamp it to pass
    return coefficients


def _artifact(rows, population, cutoff_utc, membership_policy):
    if len(rows) < MIN_LEGS:
        raise ValueError("insufficient_training_contracts")
    bases = {r["base_model_version"] for r in rows}
    if len(bases) != 1:
        raise ValueError("mixed_base_model_versions")
    frozen = copy.deepcopy(sorted(rows,key=lambda r:r["contract_id"]))
    artifact = {"version":1,"kind":KIND,"task":TASK,"state":"shadow","automatic_promotion":False,
        "evidence_status":"retrospective_reconstruction",
        "receipt_policy":RECEIPT_POLICY,"base_model_version":next(iter(bases)),
        "transform":copy.deepcopy(TRANSFORM),"coefficients":_fit(frozen),"fit_as_of_utc":cutoff_utc,
        "training":{"population":population,"membership_policy":membership_policy,"cutoff_utc":cutoff_utc,"n":len(rows),
                    "fit_rule":"ridge_newton_logit_base_v1","ridge":.001,
                    "dataset_id":fingerprint(frozen),"rows":frozen}}
    artifact["candidate_id"] = fingerprint(artifact)
    validate_candidate(artifact)
    return artifact


def fit_shadow_candidate(receipts, source_receipts, revisions, *, cutoff_utc, population, generation_records=None):
    cohort = build_cohort(receipts,source_receipts,revisions,as_of_utc=cutoff_utc,population=population,
                          generation_records=generation_records)
    return {"candidate":_artifact(cohort["rows"],population,cutoff_utc,cohort["membership_policy"]),"cohort":cohort}


def _loss(p,y):
    q=min(max(p,1e-6),1-1e-6)
    return -math.log(q if y else 1-q)


def _metrics(rows):
    n=len(rows)
    return {"n":n, "incumbent_log_loss":sum(r["incumbent_loss"] for r in rows)/n if n else None,
            "candidate_log_loss":sum(r["candidate_loss"] for r in rows)/n if n else None,
            "base_log_loss":sum(r["base_loss"] for r in rows)/n if n else None,
            "delta_log_loss":sum(r["delta_loss"] for r in rows)/n if n else None,
            "incumbent_brier":sum((r["served_prob"]-r["hit"])**2 for r in rows)/n if n else None,
            "candidate_brier":sum((r["candidate_prob"]-r["hit"])**2 for r in rows)/n if n else None}


def _cluster_interval(rows, replicates):
    if type(replicates) is not int or not 200 <= replicates <= 10000:
        raise ValueError("bootstrap_replicates_must_be_200_to_10000")
    groups=defaultdict(list)
    for row in rows: groups[row["correlation_group"]].append(row["delta_loss"])
    if len(groups)<2:
        return {"low":None,"high":None,"groups":len(groups),"reason":"insufficient_games"}
    groups=[groups[k] for k in sorted(groups)]
    rnd=random.Random(20261015)
    deltas=[]
    for _ in range(replicates):
        sample=[groups[rnd.randrange(len(groups))] for _ in groups]
        deltas.append(sum(sum(g) for g in sample)/sum(len(g) for g in sample))
    deltas.sort()
    return {"low":deltas[int(.025*(replicates-1))],"high":deltas[int(.975*(replicates-1))],
            "groups":len(groups),"replicates":replicates,"seed":20261015,
            "method":"paired percentile bootstrap by game; leg-weighted mean log-loss difference",
            "scope":"conditional on fitted fold coefficients; excludes training/refit uncertainty and shared week/regime shocks",
            "minimum_cluster_policy":"not operationally approved; two games is mathematical computability, not sufficient evidence"}


def walk_forward_report(receipts,source_receipts,revisions,*,as_of_utc,population,bootstrap_replicates=2000,generation_records=None):
    """Evaluate the refitting POLICY, not pretend its final coefficients were frozen.

    Each fold fits earlier outcomes known before its first held-out forecast.
    Its rounded transform is scored once against the actual as-served incumbent.
    The returned final artifact stays shadow even when numeric gates pass.
    """
    receipts,source_receipts,revisions=list(receipts),list(source_receipts),list(revisions)
    if generation_records is not None: generation_records=list(generation_records)
    cohort=build_cohort(receipts,source_receipts,revisions,as_of_utc=as_of_utc,population=population,generation_records=generation_records)
    rows=cohort["rows"]
    if len({r["base_model_version"] for r in rows})>1:
        raise ValueError("mixed_base_model_versions")
    folds,scored=[],[]
    for period in sorted({(r["season"],r["week"]) for r in rows})[1:]:
        held=[r for r in rows if (r["season"],r["week"])==period]
        captured = [r for r in cohort["forecasts"] if (r["season"],r["week"])==period]
        cutoff=min(captured,key=lambda r:instant(r["forecast_created_utc"]))["forecast_created_utc"]
        before=build_cohort(receipts,source_receipts,revisions,as_of_utc=cutoff,population=population,generation_records=generation_records)
        fit=[r for r in before["rows"] if (r["season"],r["week"])<period]
        overlap={r["correlation_group"] for r in fit}&{r["correlation_group"] for r in held}
        if overlap: raise ValueError("train_evaluation_game_overlap")
        fold={"season":period[0],"week":period[1],"training_cutoff_utc":cutoff,"fit_n":len(fit),"n":len(held)}
        if len(fit)<MIN_LEGS:
            folds.append({**fold,"status":"skipped","reason":"insufficient_training_contracts"});continue
        try: artifact=_artifact(fit,population,cutoff,before["membership_policy"])
        except ValueError as exc:
            folds.append({**fold,"status":"held","reason":str(exc)});continue
        fold_rows=[]
        for row in held:
            p=serve_base(row["base_prob"],artifact["coefficients"])
            item={**row,"candidate_id":artifact["candidate_id"],"candidate_prob":p,
                  "candidate_loss":_loss(p,row["hit"]),"incumbent_loss":_loss(row["served_prob"],row["hit"]),
                  "base_loss":_loss(serve_base(row["base_prob"]),row["hit"])}
            item["delta_loss"]=item["candidate_loss"]-item["incumbent_loss"]
            fold_rows.append(item)
        scored.extend(fold_rows)
        folds.append({**fold,"status":"scored","candidate_id":artifact["candidate_id"],
                      "coefficients":artifact["coefficients"],"training_dataset_id":artifact["training"]["dataset_id"],
                      "fit_weeks":sorted({(r["season"],r["week"]) for r in fit}),**_metrics(fold_rows)})
    metrics=_metrics(scored)
    per_position={p:_metrics([r for r in scored if r["position"]==p]) for p in sorted(set(POSITIONS.values()))}
    interval=_cluster_interval(scored,bootstrap_replicates)
    scored_folds=[f for f in folds if f["status"]=="scored"]
    failures=[]
    if len(rows)<MIN_LEGS:failures.append("fewer_than_100_canonical_contracts")
    if len(scored_folds)<MIN_HELD_OUT:failures.append("fewer_than_two_held_out_week_folds")
    if any(f["status"]=="held" for f in folds):failures.append("invalid_fold_fit")
    if not scored or metrics["candidate_log_loss"]>=metrics["base_log_loss"]:failures.append("no_pooled_base_improvement")
    if scored and metrics["candidate_log_loss"]>=metrics["incumbent_log_loss"]:failures.append("no_pooled_incumbent_improvement")
    if any(f["candidate_log_loss"]>min(f["base_log_loss"],f["incumbent_log_loss"]) for f in scored_folds):
        failures.append("held_out_week_regressed")
    proposed=list(failures)
    if generation_records is None:proposed.append("committed_offering_membership_unverified")
    if scored and metrics["delta_log_loss"]>=-PROPOSED_MARGIN:proposed.append("proposed_0.0015_margin_not_met")
    if interval["high"] is None or interval["high"]>=0:proposed.append("clustered_interval_not_below_zero")
    if any(v["n"]==0 or v["delta_log_loss"]>0 for v in per_position.values()):proposed.append("missing_or_worse_position")
    try: final_candidate=_artifact(rows,population,as_of_utc,cohort["membership_policy"])
    except ValueError: final_candidate=None
    if final_candidate is None:
        failures.append("no_valid_final_fit");proposed.append("no_valid_final_fit")
    report={"version":1,"kind":"nfl.my_props.walk_forward.v1","population":population,"as_of_utc":as_of_utc,
        "evaluation_kind":"refitting_policy_walk_forward_not_fixed_artifact_prospective_validation",
        "cohort_id":cohort["dataset_id"],"cohort_n":len(rows),"excluded":cohort["excluded"],
        "forecast_dataset_id":cohort["forecast_dataset_id"],"captured_forecast_n":len(cohort["forecasts"]),
        "membership_policy":cohort["membership_policy"],"offering_membership_coverage":cohort["offering_membership_coverage"],
        "folds":folds,"metrics":metrics,"per_position":per_position,"paired_game_interval":interval,
        "shared_shadow_numeric_gate":{"passed":not failures,"failures":failures,"min_legs":MIN_LEGS,
            "min_training_contracts_per_fold":MIN_LEGS,"min_held_out_week_folds":MIN_HELD_OUT,
            "status":"proposed_shared_fitter_and_stricter_numeric_policy_not_current_nfl_R100_parity"},
        "current_nfl_rules_reference":{"baseline":"32c5bfbe","source":"scripts/backtest_leg_pool.py:live_recalibration",
            "min_legs_total":100,"min_held_out_week_folds":2,"min_legs_per_fold":None,
            "pooled_base_improvement":"strict","incumbent_rule":"no held-out week worse; ties allowed",
            "fit_rule":"scripts.backtest_parlay.fit_logistic; differs from this proposed ridge fitter",
            "parity_executed":False},
        "proposed_shared_return_gate":{"passed":not proposed,"failures":proposed,"additional_margin":PROPOSED_MARGIN,
                                       "status":"proposed_policy_not_production_authorization"},
        "candidate":final_candidate,"rows":scored,"automatic_promotion":False,
        "public_serving_verified":False,"publication":cohort["publication"]}
    report["report_id"]=fingerprint(report)
    return report


def main(argv=None):
    """Read one explicit offline bundle, print a shadow report; never write state."""
    parser = argparse.ArgumentParser(description="Offline shadow NFL evaluation, not model activation")
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--as-of-utc", required=True)
    parser.add_argument("--population", choices=("offered_my", "eligible_pool"), required=True)
    parser.add_argument("--bootstrap-replicates", type=int, default=2000)
    args = parser.parse_args(argv)
    try:
        # A multiweek analysis bundle, not an HTTP request. Bound memory before
        # decoding and reject ambiguous JSON fields instead of taking the last.
        with args.bundle.open("rb") as stream:
            raw = stream.read(64 * 1024 * 1024 + 1)
        if len(raw) > 64 * 1024 * 1024:
            raise ValueError("bundle_too_large")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_json_key")
                result[key] = value
            return result
        bundle = json.loads(raw, object_pairs_hook=unique)
        fields = {"version", "receipts", "source_receipts", "revisions", "generation_records"}
        if (not isinstance(bundle, dict) or set(bundle) != fields or type(bundle.get("version")) is not int
                or bundle["version"] != 1 or any(not isinstance(bundle[k], list) for k in fields - {"version"})):
            raise ValueError("invalid_evaluation_bundle")
        bundle_id = fingerprint(bundle)  # also rejects any nonfinite JSON number
        report = walk_forward_report(bundle["receipts"], bundle["source_receipts"], bundle["revisions"],
            as_of_utc=args.as_of_utc, population=args.population, generation_records=bundle["generation_records"],
            bootstrap_replicates=args.bootstrap_replicates)
        print(json.dumps({"version":1,"input_bundle_id":bundle_id,"report":report},
                         sort_keys=True, allow_nan=False))
        return 0
    except (OSError, ValueError, TypeError, KeyError, OverflowError, RecursionError):
        # Never echo the supplied file, untrusted content or credential values.
        print(json.dumps({"ok":False,"reason":"invalid_or_unavailable_evaluation_bundle"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
