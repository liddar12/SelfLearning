"""Read-only NFL2026 MY-prop adapter for frozen v1 learning receipts.

No database, network, fitting, deployment or mutation. Legacy forecasts are
quarantined rather than reconstructed from current models. Game/Week parlays,
ATD, game winners and player-season estimates are not covered by this adapter.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
import json
import math
from pathlib import Path
import re

from ..contracts_sports import validate_sports_record, validate_sports_outcome
from ..probability_receipts import (fingerprint, finite_number, instant,
                                    prediction_id, seal_receipt, validate_resolution,
                                    validate_probability_receipt)
from ..scoring.binary_receipts import score_binary_receipts

PROP_MARKETS = frozenset(("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"))


def canonical_key(leg, season):
    """Exact cross-repo contract; display labels never identify a player."""
    if not isinstance(leg, dict) or type(season) is not int:
        raise ValueError("missing_canonical_identity")
    player = leg.get("player_id") or leg.get("gsis_id")
    if leg.get("player_id") and leg.get("gsis_id") and leg["player_id"] != leg["gsis_id"]:
        raise ValueError("conflicting_player_identity")
    if (not isinstance(leg.get("game_id"), str) or not leg["game_id"]
            or not isinstance(player, str) or not player
            or leg.get("market") not in PROP_MARKETS or not finite_number(leg.get("line"))
            or leg["line"] <= 0 or leg.get("side") not in ("home", "away")
            or leg.get("direction") != "over" or leg.get("comparison_operator") != "gte"):
        raise ValueError("missing_canonical_identity")
    if "season" in leg and leg["season"] != season:
        raise ValueError("event_period_mismatch")
    return (season, leg["game_id"], player, leg["market"], format(float(leg["line"]), ".12g"),
            leg["side"], leg["direction"], leg["comparison_operator"])


def _sigmoid(value):
    if value >= 0:
        return 1 / (1 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1 + exp_value)


def validate_nfl_receipt(receipt, leg, season, week):
    """Independent source-contract verifier; does not import an NFL checkout."""
    if not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != 1:
        raise ValueError("missing_or_unsupported_receipt")
    key = canonical_key(leg, season)
    if canonical_key(receipt, season) != key or receipt.get("season") != season or receipt.get("week") != week:
        raise ValueError("receipt_identity_mismatch")
    if receipt.get("contract_id") != fingerprint(list(key)):
        raise ValueError("contract_hash_mismatch")
    if receipt.get("receipt_id") != fingerprint({k: v for k, v in receipt.items() if k != "receipt_id"}):
        raise ValueError("source_receipt_hash_mismatch")
    if receipt.get("publication_status") != "generated_unverified":
        raise ValueError("unsupported_publication_claim")
    base = receipt.get("base_prob")
    if not finite_number(base) or not 0 < base < 1:
        raise ValueError("invalid_base_probability")
    correction = receipt.get("calibrator")
    if receipt.get("calibrator_version") != fingerprint(correction):
        raise ValueError("calibrator_hash_mismatch")
    p = base
    if correction is not None:
        if (not isinstance(correction, dict) or not finite_number(correction.get("a"))
                or not finite_number(correction.get("b")) or correction["b"] <= 0):
            raise ValueError("invalid_calibrator")
        q = min(max(base, 1e-4), 1 - 1e-4)
        p = _sigmoid(correction["a"] + correction["b"] * math.log(q / (1 - q)))
    expected = round(min(max(p, .05), .95), 4)
    if (not finite_number(receipt.get("served_prob")) or receipt["served_prob"] != expected
            or leg.get("model_prob") != expected):
        raise ValueError("serving_parity_mismatch")
    features = receipt.get("features")
    if not isinstance(features, dict):
        raise ValueError("missing_frozen_features")
    if (features.get("market") != key[3] or features.get("line") != leg["line"]
            or not all(finite_number(features.get(k)) for k in ("mu", "sd", "z", "p_team"))
            or features["sd"] <= 0 or not 0 <= features["p_team"] <= 1
            or not isinstance(features.get("correlation_tags"), list)
            or not all(isinstance(t, str) and t for t in features["correlation_tags"])):
        raise ValueError("invalid_frozen_features")
    coef = features.get("calibration")
    if not isinstance(coef, dict) or not all(finite_number(coef.get(k)) for k in ("a", "b", "c")):
        raise ValueError("missing_frozen_calibration")
    z = (features["mu"] - features["line"]) / features["sd"]
    reconstructed = _sigmoid(coef["a"] + coef["b"] * features["z"] + coef["c"] * (features["p_team"] - .5))
    if not math.isclose(z, features["z"], rel_tol=1e-12, abs_tol=1e-12) or reconstructed != base:
        raise ValueError("base_feature_parity_mismatch")
    return key


def _candidate(doc, card, leg, scored_leg, game_evidence, scores_doc):
    season, week = doc.get("season"), doc.get("week")
    source = leg.get("learning_receipt")
    key = validate_nfl_receipt(source, leg, season, week)
    if canonical_key(scored_leg, season) != key:
        raise ValueError("score_identity_mismatch")
    if scored_leg.get("receipt_id") != source["receipt_id"]:
        raise ValueError("score_receipt_mismatch")
    if scored_leg.get("result") not in ("hit", "miss"):
        raise ValueError("non_binary_outcome")
    seen, first_kick = instant(card.get("first_seen_utc")), instant(card.get("earliest_kickoff_utc"))
    created = instant(source.get("forecast_created_utc"))
    if card.get("locked") is not True or not created <= seen < first_kick:
        raise ValueError("not_internal_pregame_receipt")
    game = game_evidence.get(key[1])
    if not isinstance(game, dict) or game.get("status") != "FINAL":
        raise ValueError("missing_final_game_evidence")
    kickoff, final = instant(game.get("kickoff_utc")), instant(game.get("final_utc"))
    if first_kick > kickoff or final < kickoff:
        raise ValueError("invalid_game_timeline")
    if instant(scores_doc.get("generated_utc")) < final:
        raise ValueError("score_before_final")
    if not isinstance(game.get("source"), str) or not game["source"].strip():
        raise ValueError("missing_final_source")
    envelope = seal_receipt({
        "version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
        "contract_id": source["contract_id"], "event_id": f"nfl:{season}:game:{key[1]}",
        "correlation_group": f"nfl:{season}:game:{key[1]}",
        "base_prob": source["base_prob"], "served_prob": source["served_prob"],
        "base_model_version": source["base_model_version"], "calibrator_version": source["calibrator_version"],
        "forecast_created_utc": source["forecast_created_utc"], "first_seen_utc": card["first_seen_utc"],
        "event_start_utc": game["kickoff_utc"], "source_snapshot_id": source["source_snapshot_id"],
        "source_timestamps": copy.deepcopy(source["source_timestamps"]),
        "features": copy.deepcopy(source["features"]), "source_receipt_id": source["receipt_id"],
        "publication_status": source["publication_status"],
    })
    resolution = {"receipt_id": envelope["receipt_id"], "contract_id": envelope["contract_id"],
                  "event_id": envelope["event_id"], "status": "FINAL",
                  "hit": scored_leg["result"] == "hit", "resolved_utc": game["final_utc"],
                  "source": game["source"]}
    validate_resolution(envelope, resolution)
    pid = prediction_id(envelope)
    prediction = {"id": pid, "ts": int(created.timestamp()),
                  "model_version": fingerprint([source["base_model_version"], source["calibrator_version"]]),
                  "task": "nfl.parlay_leg", "features": copy.deepcopy(source["features"]),
                  "prediction": {"model_prob": source["served_prob"]},
                  "horizon_s": max(1, math.ceil((kickoff - created).total_seconds())),
                  "confidence": source["served_prob"], "cohort": f"{season}:wk{week}:{key[3]}",
                  "meta": {"kickoff_ts": int(kickoff.timestamp()), "horizon_kind": "until_event_start",
                           "contract_id": source["contract_id"], "source_receipt_id": source["receipt_id"],
                           "receipt_id": envelope["receipt_id"], "game_id": key[1], "gsis_id": key[2],
                           "correlation_group": envelope["correlation_group"],
                           "publication_status": source["publication_status"],
                           "input_context": copy.deepcopy(source.get("input_context")),
                           "base_prob": source["base_prob"], "calibrator_version": source["calibrator_version"]}}
    validate_sports_record(prediction)
    realized = {"hit": resolution["hit"]}
    validate_sports_outcome("nfl.parlay_leg", realized)
    outcome = {"prediction_id": pid, "resolved_ts": int(final.timestamp()), "realized": realized,
               "meta": {"status": "FINAL", "source": game["source"], "contract_id": source["contract_id"]}}
    return {"receipt": envelope, "resolution": resolution, "prediction": prediction, "outcome": outcome}


def adapt_my_cards(card_docs, scores_doc, game_evidence):
    """Pure batch ingest; returns log-ready records and explicit quarantines.

    game_evidence is keyed by exact game_id with status='FINAL', kickoff_utc,
    final_utc and source. A scored hit alone is not proof the game was final.
    Repeated card copies do not enlarge the sample or change prediction IDs.
    """
    offered, conflicts, rejected = {}, set(), []

    def reject(reason, card_id=None, receipt_id=None):
        rejected.append({"reason": reason, "card_id": card_id, "source_receipt_id": receipt_id})

    for doc in card_docs or []:
        if not isinstance(doc, dict):
            reject("invalid_card_document")
            continue
        for card in doc.get("cards") or []:
            if not isinstance(card, dict) or not isinstance(card.get("card_id"), str):
                reject("missing_card_identity")
                continue
            key = (doc.get("season"), doc.get("week"), card["card_id"])
            if key in offered and offered[key][1] != card:
                conflicts.add(key)
            offered[key] = (doc, card)
    candidates = defaultdict(list)
    for scored in (scores_doc or {}).get("cards") or []:
        if not isinstance(scored, dict):
            reject("invalid_score_card")
            continue
        card_id = scored.get("card_id")
        key = (scored.get("season", scores_doc.get("season")), scored.get("week"), card_id)
        if key not in offered or key in conflicts:
            reject("missing_or_conflicting_source_card", card_id)
            continue
        doc, card = offered[key]
        for scored_leg in scored.get("legs") or []:
            if not isinstance(scored_leg, dict):
                reject("invalid_score_leg", card_id)
                continue
            if scored_leg.get("market") not in PROP_MARKETS:
                reject("unsupported_market", card_id)
                continue
            try:
                identity = canonical_key(scored_leg, key[0])
                matches = []
                for leg in card.get("legs") or []:
                    try:
                        if canonical_key(leg, key[0]) == identity:
                            matches.append(leg)
                    except ValueError:
                        pass
                if len(matches) != 1:
                    raise ValueError("ambiguous_or_missing_leg_join")
                item = _candidate(doc, card, matches[0], scored_leg, game_evidence, scores_doc)
                candidates[item["receipt"]["contract_id"]].append(item)
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                reject(str(exc), card_id, scored_leg.get("receipt_id"))
    selected, duplicates = [], 0
    for contract_id in sorted(candidates):
        versions = candidates[contract_id]
        if len({v["resolution"]["hit"] for v in versions}) != 1:
            reject("conflicting_event_outcomes", receipt_id=contract_id)
            continue
        versions.sort(key=lambda v: (instant(v["receipt"]["forecast_created_utc"]),
                                    instant(v["receipt"]["first_seen_utc"]), v["receipt"]["source_receipt_id"]))
        first = instant(versions[0]["receipt"]["forecast_created_utc"])
        earliest = [v["receipt"] for v in versions if instant(v["receipt"]["forecast_created_utc"]) == first]
        if len({(r["base_prob"], r["served_prob"], r["base_model_version"], r["calibrator_version"],
                 fingerprint(r["features"])) for r in earliest}) != 1:
            reject("conflicting_first_forecast", receipt_id=contract_id)
            continue
        selected.append(versions[0])
        duplicates += len(versions) - 1
    rejected.sort(key=lambda r: (r["reason"], str(r["card_id"]), str(r["source_receipt_id"])))
    return {"version": 1, "adapter": "nfl.my_props.receipts_v1",
            "predictions": [v["prediction"] for v in selected], "outcomes": [v["outcome"] for v in selected],
            "receipts": [v["receipt"] for v in selected], "resolutions": [v["resolution"] for v in selected],
            "rejections": rejected,
            "summary": {"eligible_unique_contracts": len(selected), "duplicate_or_later_copies": duplicates,
                        "quarantined_records": len(rejected),
                        "quarantine_reasons": dict(sorted(Counter(r["reason"] for r in rejected).items())),
                        "publication": "internal pre-event receipts; public availability unverified",
                        "writes": False}}


def _prospective_prediction(receipt, source, season, week):
    created, kickoff = instant(receipt["forecast_created_utc"]), instant(receipt["event_start_utc"])
    prediction = {"id": prediction_id(receipt), "ts": int(created.timestamp()),
        "model_version": fingerprint([receipt["base_model_version"], receipt["calibrator_version"]]),
        "task": receipt["task"], "features": copy.deepcopy(receipt["features"]),
        "prediction": {"model_prob": receipt["served_prob"]}, "confidence": receipt["served_prob"],
        "horizon_s": max(1, math.ceil((kickoff - created).total_seconds())),
        "cohort": f"{season}:wk{week}:{source['market']}",
        "meta": {"kickoff_ts": int(kickoff.timestamp()), "horizon_kind": "until_event_start",
                 "contract_id": receipt["contract_id"], "receipt_id": receipt["receipt_id"],
                 "source_receipt_id": receipt["source_receipt_id"], "game_id": source["game_id"],
                 "gsis_id": source["player_id"], "correlation_group": receipt["correlation_group"],
                 "publication_status": receipt["publication_status"],
                 "base_prob": receipt["base_prob"], "calibrator_version": receipt["calibrator_version"],
                 "input_context": copy.deepcopy(source.get("input_context"))}}
    validate_sports_record(prediction)
    return prediction


def adapt_prospective(card_docs=(), pool_docs=(), game_evidence=None, *, observed_utc=None,
                      journal_docs=(), existing_receipts=()):
    """Pure, unresolved capture of frozen MY prop receipts, never final scoring.

    Raw card/pool inputs need an explicit capture time and scheduled game evidence
    keyed by game_id with kickoff_utc and source. Immutable NFL journal documents
    instead supply their original recorded_utc and generation-linked kickoff.
    Their internal evidence is NOT a claim of public availability. Existing sealed
    receipts preserve the first capture on retries; changed evidence quarantines.
    Every distinct source receipt is retained, including later model vintages.
    """
    rejected, candidates, previous = [], defaultdict(list), {}
    games = game_evidence if isinstance(game_evidence, dict) else {}

    def reject(reason, source_id=None):
        rejected.append({"reason": str(reason), "source_receipt_id": source_id if isinstance(source_id, str) else None})

    for receipt in existing_receipts:
        try:
            validate_probability_receipt(receipt)
            if receipt["domain"] != "nfl" or receipt["task"] != "nfl.parlay_leg":
                continue
            sid = receipt["source_receipt_id"]
            if sid in previous and previous[sid] != receipt:
                raise ValueError("conflicting_existing_receipts")
            previous[sid] = copy.deepcopy(receipt)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            # Invalid existing state is not a license to mint a replacement.
            raise ValueError("invalid_existing_receipts: " + str(exc)) from exc

    def add(doc, leg, seen, kickoff, provenance):
        sid = None
        try:
            if not isinstance(leg, dict):
                raise ValueError("invalid_leg")
            source = leg.get("learning_receipt")
            sid = source.get("receipt_id") if isinstance(source, dict) else None
            season, week = doc.get("season"), doc.get("week")
            if type(week) is not int or not 1 <= week <= 30:
                raise ValueError("invalid_week")
            key = validate_nfl_receipt(source, leg, season, week)
            context = source.get("input_context") or {}
            if not isinstance(context, dict):
                raise ValueError("invalid_eligibility_context")
            eligibility, this_week = context.get("eligibility") or {}, context.get("this_week") or {}
            if not isinstance(eligibility, dict) or not isinstance(this_week, dict):
                raise ValueError("invalid_eligibility_context")
            if eligibility.get("eligible") is False or this_week.get("playable") is False:
                raise ValueError("source_not_eligible")
            if eligibility.get("week", week) != week or this_week.get("wk", week) != week:
                raise ValueError("wrong_week_eligibility_context")
            payload = {"version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
                "contract_id": source["contract_id"], "event_id": f"nfl:{season}:game:{key[1]}",
                "correlation_group": f"nfl:{season}:game:{key[1]}",
                "base_prob": source["base_prob"], "served_prob": source["served_prob"],
                "base_model_version": source["base_model_version"], "calibrator_version": source["calibrator_version"],
                "forecast_created_utc": source["forecast_created_utc"], "first_seen_utc": seen,
                "event_start_utc": kickoff, "source_snapshot_id": source["source_snapshot_id"],
                "source_timestamps": copy.deepcopy(source["source_timestamps"]),
                "features": copy.deepcopy(source["features"]), "source_receipt_id": sid,
                "publication_status": source["publication_status"], "capture_provenance": provenance}
            envelope = seal_receipt(payload)
            prediction = _prospective_prediction(envelope, source, season, week)
            candidates[sid].append((envelope, prediction))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(exc, sid)

    def raw_add(doc, leg):
        sid = (leg.get("learning_receipt") or {}).get("receipt_id") if isinstance(leg, dict) and isinstance(leg.get("learning_receipt"), dict) else None
        try:
            if not isinstance(leg, dict) or not isinstance(leg.get("game_id"), str):
                raise ValueError("missing_canonical_identity")
            game = games.get(leg["game_id"])
            if not isinstance(game, dict) or not isinstance(game.get("source"), str) or not game["source"].strip():
                raise ValueError("missing_schedule_evidence")
            known = previous.get(sid)
            seen = known["first_seen_utc"] if known else observed_utc
            if not known and game.get("status") not in ("SCHEDULED", "STATUS_SCHEDULED"):
                raise ValueError("game_not_scheduled")
            for key in ("season", "week"):
                if key in game and game[key] != doc.get(key):
                    raise ValueError("schedule_period_mismatch")
            instant(observed_utc)  # The caller must supply an actual observation, even on retry.
            if instant(seen) > instant(observed_utc):
                raise ValueError("observation_time_regressed")
            add(doc, leg, seen, game.get("kickoff_utc"), {"kind": "raw_capture", "source": game["source"]})
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(exc, sid)

    for doc in card_docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("cards"), list):
            reject("invalid_card_document")
            continue
        for card in doc["cards"]:
            if not isinstance(card, dict) or not isinstance(card.get("legs"), list):
                reject("invalid_card")
                continue
            for leg in card["legs"]:
                raw_add(doc, leg)

    for doc in pool_docs:
        if not isinstance(doc, dict) or not isinstance(doc.get("players"), list):
            reject("invalid_pool_document")
            continue
        for player in doc["players"]:
            if not isinstance(player, dict) or not isinstance(player.get("rungs"), list):
                reject("invalid_pool_player")
                continue
            for rung in player["rungs"]:
                if not isinstance(rung, dict):
                    reject("invalid_pool_rung")
                    continue
                # NFL pool v1's fixed yardage contract omits these redundant
                # fields. Explicit conflicting fields are never overwritten.
                leg = {"direction": "over", "comparison_operator": "gte", **player, **rung}
                source = rung.get("learning_receipt")
                if isinstance(source, dict):
                    try:
                        if (instant(source.get("forecast_created_utc")) != instant(doc.get("generated_utc"))
                                or source.get("base_model_version") != doc.get("base_model_version")):
                            raise ValueError("receipt_pool_generation_mismatch")
                    except (ValueError, TypeError) as exc:
                        reject(exc, source.get("receipt_id"))
                        continue
                raw_add(doc, leg)

    for doc in journal_docs:
        if (not isinstance(doc, dict) or type(doc.get("version")) is not int or doc.get("version") != 1
                or not isinstance(doc.get("receipts"), list) or not isinstance(doc.get("generations"), list)):
            reject("invalid_journal_document")
            continue
        try:
            if doc.get("envelope_id") != fingerprint({k: v for k, v in doc.items() if k != "envelope_id"}):
                raise ValueError("journal_envelope_hash_mismatch")
        except (ValueError, TypeError) as exc:
            reject(exc)
            continue
        for row in doc["receipts"]:
            sid = row.get("receipt_id") if isinstance(row, dict) else None
            try:
                if not isinstance(row, dict) or not isinstance(sid, str):
                    raise ValueError("invalid_journal_row")
                source = row.get("learning_receipt")
                if not isinstance(source, dict) or source.get("receipt_id") != sid:
                    raise ValueError("journal_receipt_id_mismatch")
                matches = [g for g in doc["generations"] if isinstance(g, dict)
                           and isinstance(row.get("generation_id"), str) and g.get("generation_id") == row["generation_id"]]
                if len(matches) != 1:
                    raise ValueError("missing_or_ambiguous_generation")
                generation = matches[0]
                manifest_keys = ("version", "season", "week", "pool_generated_utc", "source_snapshots",
                                 "offered_cards", "selection_policy_version", "joint_model_version")
                manifest = {k: generation[k] for k in manifest_keys}
                if (generation["generation_id"] != fingerprint(manifest)
                        or type(generation["version"]) is not int or generation["version"] != 1
                        or generation["season"] != doc.get("season") or generation["week"] != doc.get("week")):
                    raise ValueError("generation_hash_or_period_mismatch")
                if (row.get("publication_status") != "generated_unverified"
                        or generation.get("publication_status") != "generated_unverified"):
                    raise ValueError("unsupported_publication_claim")
                if not isinstance(generation.get("receipt_ids"), list) or sid not in generation["receipt_ids"]:
                    raise ValueError("receipt_not_in_generation")
                if row.get("recorded_utc") != generation.get("recorded_utc"):
                    raise ValueError("generation_observation_mismatch")
                if instant(generation.get("pool_generated_utc")) > instant(row.get("recorded_utc")):
                    raise ValueError("generation_after_observation")
                if instant(source.get("forecast_created_utc")) != instant(generation.get("pool_generated_utc")):
                    raise ValueError("receipt_pool_generation_mismatch")
                if generation.get("population") != "eligible_pool_prop_rungs_before_display_dedupe":
                    raise ValueError("unsupported_capture_population")
                snapshots = generation.get("source_snapshots")
                if (not isinstance(snapshots, dict) or any(not isinstance(snapshots.get(k), str)
                        or re.fullmatch(r"sha256:[0-9a-f]{64}", snapshots[k]) is None
                        for k in ("leg_pool", "schedule", "parlay_calibration"))):
                    raise ValueError("missing_schedule_snapshot")
                leg = row.get("leg")
                if not isinstance(leg, dict):
                    raise ValueError("invalid_leg")
                if "learning_receipt" in leg and leg["learning_receipt"] != source:
                    raise ValueError("journal_leg_receipt_mismatch")
                offered = generation.get("offered_cards")
                if (not isinstance(offered, list) or any(not isinstance(c, dict)
                        or not isinstance(c.get("card_id"), str) or not isinstance(c.get("receipt_ids"), list)
                        or any(not isinstance(r, str) for r in c["receipt_ids"]) for c in offered)):
                    raise ValueError("invalid_offered_card_references")
                add(doc, {"direction": "over", "comparison_operator": "gte", **leg, "learning_receipt": source},
                    row.get("recorded_utc"), row.get("kickoff_utc"),
                    {"kind": "nfl_generation_journal", "generation_id": row["generation_id"],
                     "schedule_snapshot_id": snapshots["schedule"],
                     "population": generation.get("population"),
                     "offered_card_ids": sorted({c["card_id"] for c in offered if sid in c["receipt_ids"]})})
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                reject(exc, sid)

    def immutable_forecast(receipt):
        return {k: v for k, v in receipt.items() if k not in ("receipt_id", "first_seen_utc", "capture_provenance")}

    selected, duplicates = [], 0
    for sid, versions in sorted(candidates.items()):
        if len({fingerprint(immutable_forecast(r)) for r, _ in versions}) != 1:
            reject("conflicting_capture_evidence", sid)
            continue
        versions.sort(key=lambda item: (instant(item[0]["first_seen_utc"]), item[0]["receipt_id"]))
        receipt, prediction = versions[0]
        if sid in previous:
            known = previous[sid]
            if immutable_forecast(known) != immutable_forecast(receipt):
                reject("existing_receipt_conflict", sid)
                continue
            receipt = known
            prediction["id"] = prediction_id(known)
            prediction["meta"]["receipt_id"] = known["receipt_id"]
        selected.append((receipt, prediction))
        duplicates += len(versions) - 1
    rejected.sort(key=lambda r: (r["reason"], str(r["source_receipt_id"])))
    return {"version": 1, "adapter": "nfl.my_props.prospective_v1",
            "receipts": [r for r, _ in selected], "predictions": [p for _, p in selected],
            "outcomes": [], "resolutions": [], "rejections": rejected,
            "summary": {"eligible_receipts": len(selected), "duplicate_copies": duplicates,
                        "quarantined_records": len(rejected),
                        "quarantine_reasons": dict(sorted(Counter(r["reason"] for r in rejected).items())),
                        "publication": "internal observations only; public availability unverified", "writes": False}}


def main():
    parser = argparse.ArgumentParser(description="Read-only NFL receipt validation and score parity; prints JSON, never writes a store.")
    parser.add_argument("--cards", type=Path, action="append", required=True)
    parser.add_argument("--scores", type=Path, required=True)
    parser.add_argument("--games", type=Path, required=True, help="game_id -> explicit FINAL evidence")
    args = parser.parse_args()
    def read(path):
        return json.loads(path.read_text(encoding="utf-8"))
    batch = adapt_my_cards([read(path) for path in args.cards], read(args.scores), read(args.games))
    batch["score"] = score_binary_receipts(batch["receipts"], batch["resolutions"])
    print(json.dumps(batch, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
