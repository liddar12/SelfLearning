"""Independent NFL receipt producer fixtures, no dependency on NFL2026 checkout."""
import copy
import hashlib
import json
import math
import unittest

from selflearn_core.adapters.nfl import adapt_my_cards
from selflearn_core.scoring.binary_receipts import score_binary_receipts


def digest(value):
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def fixture(player="espn-123", game="g1", card_id="card-a", probability=.8, hit=True, correction=None):
    a = math.log(probability / (1 - probability))
    base = 1 / (1 + math.exp(-a)) if a >= 0 else math.exp(a) / (1 + math.exp(a))
    p = base
    if correction:
        z = correction["a"] + correction["b"] * math.log(base / (1 - base))
        p = 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))
    served = round(min(.95, max(.05, p)), 4)
    key = [2026, game, player, "wr_rec_yds", "59.5", "home", "over", "gte"]
    r = {"version": 1, "contract_id": digest(key), "season": 2026, "week": 6,
         "game_id": game, "player_id": player, "market": "wr_rec_yds", "line": 59.5,
         "side": "home", "direction": "over", "comparison_operator": "gte",
         "base_prob": base, "served_prob": served, "base_model_version": digest("base-v1"),
         "calibrator_version": digest(correction), "calibrator": correction,
         "forecast_created_utc": "2026-10-18T17:00:00Z", "source_snapshot_id": digest("frozen-source"),
         "source_timestamps": {"weekly": "2026-10-18T16:00:00Z"}, "generation_sha": "test-sha",
         "publication_status": "generated_unverified",
         "features": {"market": "wr_rec_yds", "line": 59.5, "mu": 99.5, "sd": 20.0,
                      "z": 2.0, "p_team": .6, "calibration": {"a": a, "b": 0.0, "c": 0.0},
                      "correlation_tags": ["game:" + game, "side:home", "market:wr_rec_yds"]},
         "input_context": {"availability": "active"}}
    r["receipt_id"] = digest(r)
    leg = {"season": 2026, "game_id": game, "gsis_id": player, "market": "wr_rec_yds", "line": 59.5,
           "side": "home", "direction": "over", "comparison_operator": "gte", "selection": "J. Same 60+ REC",
           "model_prob": served, "learning_receipt": r}
    doc = {"season": 2026, "week": 6, "cards": [{"card_id": card_id, "locked": True,
           "first_seen_utc": "2026-10-18T17:01:00Z", "earliest_kickoff_utc": "2026-10-18T18:00:00Z", "legs": [leg]}]}
    scored = {k: v for k, v in leg.items() if k not in ("learning_receipt", "model_prob")}
    scored.update({"receipt_id": r["receipt_id"], "result": "hit" if hit else "miss", "actual": 80 if hit else 30})
    scores = {"season": 2026, "generated_utc": "2026-10-18T22:00:00Z",
              "cards": [{"card_id": card_id, "week": 6, "legs": [scored]}]}
    games = {game: {"status": "FINAL", "kickoff_utc": "2026-10-18T18:00:00Z",
                    "final_utc": "2026-10-18T21:10:00Z", "source": "fixture final score feed"}}
    return doc, scores, games


def refresh_receipt(doc, scores):
    r = doc["cards"][0]["legs"][0]["learning_receipt"]
    r["receipt_id"] = digest({k: v for k, v in r.items() if k != "receipt_id"})
    scores["cards"][0]["legs"][0]["receipt_id"] = r["receipt_id"]


class NFLAdapterTest(unittest.TestCase):
    def test_frozen_receipt_maps_to_sports_contract_and_hand_scoring_oracle(self):
        a, sa, ga = fixture(probability=.8)
        b, sb, gb = fixture("espn-456", "g2", "card-b", .3, False)
        scores = {**sa, "cards": sa["cards"] + sb["cards"]}
        original = copy.deepcopy([a, b, scores, ga, gb])
        batch = adapt_my_cards([a, b], scores, {**ga, **gb})
        self.assertEqual(batch["summary"]["eligible_unique_contracts"], 2)
        self.assertEqual(batch["rejections"], [])
        self.assertEqual(batch["predictions"][0]["task"], "nfl.parlay_leg")
        self.assertTrue(all(p["meta"]["publication_status"] == "generated_unverified" for p in batch["predictions"]))
        report = score_binary_receipts(batch["receipts"], batch["resolutions"])
        self.assertAlmostEqual(report["log_loss"], (-math.log(.8) - math.log(.7)) / 2)
        self.assertAlmostEqual(report["brier"], (.2 ** 2 + .3 ** 2) / 2)
        self.assertEqual([a, b, scores, ga, gb], original)

    def test_corrected_served_probability_is_scored_without_applying_correction_again(self):
        doc, scores, games = fixture(probability=.8, correction={"a": -.7, "b": .7})
        batch = adapt_my_cards([doc], scores, games)
        p = doc["cards"][0]["legs"][0]["model_prob"]
        self.assertNotEqual(p, .8)
        report = score_binary_receipts(batch["receipts"], batch["resolutions"])
        self.assertAlmostEqual(report["log_loss"], -math.log(p))
        self.assertAlmostEqual(report["brier"], (1 - p) ** 2)

    def test_late_missing_or_void_evidence_is_quarantined_not_a_loss(self):
        for case in ("late", "unlocked", "void", "pending", "live", "missing_final", "score_early", "legacy", "features", "source_asof"):
            with self.subTest(case=case):
                doc, scores, games = fixture()
                card, scored = doc["cards"][0], scores["cards"][0]["legs"][0]
                if case == "late": card["first_seen_utc"] = card["earliest_kickoff_utc"]
                elif case == "unlocked": card["locked"] = False
                elif case in ("void", "pending"): scored["result"] = case
                elif case == "live": games["g1"]["status"] = "LIVE"
                elif case == "missing_final": games = {}
                elif case == "score_early": scores["generated_utc"] = "2026-10-18T20:00:00Z"
                elif case == "legacy": del card["legs"][0]["learning_receipt"]
                elif case == "features":
                    del card["legs"][0]["learning_receipt"]["features"]
                    refresh_receipt(doc, scores)
                elif case == "source_asof":
                    card["legs"][0]["learning_receipt"]["source_timestamps"] = {"weekly": None}
                    refresh_receipt(doc, scores)
                batch = adapt_my_cards([doc], scores, games)
                self.assertEqual(batch["predictions"], [])
                self.assertEqual(batch["outcomes"], [])
                self.assertTrue(batch["rejections"])

    def test_same_abbreviated_player_name_never_cross_joins(self):
        doc, scores, games = fixture()
        scores["cards"][0]["legs"][0]["gsis_id"] = "espn-OTHER"
        batch = adapt_my_cards([doc], scores, games)
        self.assertEqual(batch["summary"]["eligible_unique_contracts"], 0)
        self.assertEqual(batch["rejections"][0]["reason"], "ambiguous_or_missing_leg_join")

    def test_partial_legacy_score_identity_and_receipt_substitution_are_rejected(self):
        for key in ("game_id", "gsis_id", "line", "side", "direction", "comparison_operator", "receipt_id"):
            with self.subTest(key=key):
                doc, scores, games = fixture()
                del scores["cards"][0]["legs"][0][key]
                batch = adapt_my_cards([doc], scores, games)
                self.assertEqual(batch["predictions"], [])

    def test_tamper_or_rehash_inconsistent_features_does_not_pass(self):
        for case in ("tamper", "rehash", "served", "wrong_period", "market_feature", "publication"):
            with self.subTest(case=case):
                doc, scores, games = fixture()
                leg = doc["cards"][0]["legs"][0]
                r = leg["learning_receipt"]
                if case in ("tamper", "rehash"): r["features"]["mu"] = 300
                elif case == "served": r["served_prob"] = .9
                elif case == "wrong_period": r["week"] = 5
                elif case == "market_feature": r["features"]["market_prob"] = .8
                elif case == "publication": r["publication_status"] = "verified_publication"
                if case != "tamper": refresh_receipt(doc, scores)
                batch = adapt_my_cards([doc], scores, games)
                self.assertEqual(batch["predictions"], [])

    def test_duplicate_copies_and_repeated_ingestion_are_deterministic(self):
        a, sa, games = fixture()
        b, sb, _ = fixture(card_id="card-b")
        scores = {**sa, "cards": sa["cards"] + sb["cards"]}
        batch = adapt_my_cards([a, b], scores, games)
        reverse = adapt_my_cards([b, a], {**scores, "cards": list(reversed(scores["cards"]))}, games)
        self.assertEqual(batch, reverse)
        self.assertEqual(batch, adapt_my_cards([a, b], scores, games))
        self.assertEqual(batch["summary"]["eligible_unique_contracts"], 1)
        self.assertEqual(batch["summary"]["duplicate_or_later_copies"], 1)
        self.assertEqual(batch["predictions"][0]["id"], batch["outcomes"][0]["prediction_id"])

    def test_conflicting_outcomes_or_first_forecasts_quarantine_entire_contract(self):
        for outcome_conflict in (True, False):
            a, sa, games = fixture()
            b, sb, _ = fixture(card_id="card-b", probability=.8 if outcome_conflict else .3,
                              hit=not outcome_conflict)
            scores = {**sa, "cards": sa["cards"] + sb["cards"]}
            batch = adapt_my_cards([a, b], scores, games)
            self.assertEqual(batch["predictions"], [])
            self.assertIn("conflicting_", batch["rejections"][0]["reason"])

    def test_two_cycles_keep_original_probability_and_add_distinct_event(self):
        a, sa, ga = fixture()
        first = adapt_my_cards([a], sa, ga)
        b, sb, gb = fixture("espn-456", "g2", "card-b", .35, False, {"a": .1, "b": .9})
        second = adapt_my_cards([a, b], {**sa, "cards": sa["cards"] + sb["cards"]}, {**ga, **gb})
        by_id = {p["id"]: p for p in second["predictions"]}
        self.assertEqual(by_id[first["predictions"][0]["id"]], first["predictions"][0])
        self.assertEqual(second["summary"]["eligible_unique_contracts"], 2)
        self.assertEqual(len({p["model_version"] for p in second["predictions"]}), 2)

    def test_different_player_same_game_explicitly_remains_correlated(self):
        a, sa, games = fixture()
        b, sb, _ = fixture("espn-456", "g1", "card-b", .3, False)
        batch = adapt_my_cards([a, b], {**sa, "cards": sa["cards"] + sb["cards"]}, games)
        report = score_binary_receipts(batch["receipts"], batch["resolutions"])
        self.assertEqual((report["n"], report["unique_events"], report["largest_group"]), (2, 1, 2))
        self.assertIsNone(report["ci_low"])


if __name__ == "__main__":
    unittest.main()
