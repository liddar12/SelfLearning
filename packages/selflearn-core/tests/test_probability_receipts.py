"""Stdlib tests: independent scalar oracles and a second non-NFL domain."""
import copy
import math
import unittest

from selflearn_core.probability_receipts import (fingerprint, seal_receipt,
    validate_probability_receipt, validate_resolution, prediction_id)
from selflearn_core.scoring.binary_receipts import score_binary_receipts


def receipt(contract="poll-a", probability=.7, event="election-1", group="same-election"):
    return seal_receipt({"version": 1, "domain": "elections", "task": "election.binary",
        "contract_id": fingerprint(contract), "event_id": event, "correlation_group": group,
        "base_prob": .6, "served_prob": probability, "base_model_version": "poll-v1",
        "calibrator_version": "cal-v1", "forecast_created_utc": "2026-11-01T12:00:00Z",
        "first_seen_utc": "2026-11-01T12:00:01Z", "event_start_utc": "2026-11-03T12:00:00Z",
        "source_snapshot_id": fingerprint({"poll": 51}), "source_receipt_id": fingerprint([contract, probability]),
        "source_timestamps": {"poll": "2026-10-31T12:00:00Z"}, "features": {"poll_margin": 2.0},
        "publication_status": "generated_unverified"})


def outcome(r, hit=True):
    return {"receipt_id": r["receipt_id"], "contract_id": r["contract_id"], "event_id": r["event_id"],
            "status": "FINAL", "hit": hit, "resolved_utc": "2026-11-04T12:00:00Z", "source": "fixture certified result"}


def reseal(r, **updates):
    payload = {k: copy.deepcopy(v) for k, v in r.items() if k != "receipt_id"}
    payload.update(updates)
    return seal_receipt(payload)


class ProbabilityReceiptsTest(unittest.TestCase):
    def test_non_nfl_scores_match_independent_oracle_and_not_base(self):
        a, b = receipt("a", .8), receipt("b", .3, "election-2", "other-election")
        report = score_binary_receipts([a, b], [outcome(a), outcome(b, False)])
        self.assertEqual(report["n"], 2)
        self.assertAlmostEqual(report["log_loss"], (-math.log(.8) - math.log(.7)) / 2)
        self.assertAlmostEqual(report["brier"], (.2 ** 2 + .3 ** 2) / 2)
        self.assertAlmostEqual(report["mean_served_prob"], .55)
        self.assertNotEqual(report["mean_served_prob"], a["base_prob"])
        self.assertEqual(report["correlation_groups"], 2)

    def test_copies_and_related_contracts_are_not_independent_samples(self):
        a, b = receipt("a", .8), receipt("b", .3)
        report = score_binary_receipts([a, a, b], [outcome(a), outcome(b, False)])
        self.assertEqual((report["n"], report["unique_events"], report["correlation_groups"]), (2, 1, 1))
        self.assertEqual(report["largest_group"], 2)
        self.assertEqual(report["duplicate_or_later_copies"], 1)
        self.assertIsNone(report["independent_sample_size"])
        self.assertIsNone(report["ci_low"])
        self.assertIsNone(report["ci_high"])

    def test_final_bool_identity_and_resolution_time_are_required(self):
        r = receipt()
        for updates in [{"hit": 1}, {"hit": None}, {"status": "VOID"}, {"status": "PENDING"},
                        {"contract_id": fingerprint("other")}, {"event_id": "other"},
                        {"receipt_id": fingerprint("other")}, {"source": ""},
                        {"resolved_utc": "2026-10-31T12:00:00Z"}]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                validate_resolution(r, {**outcome(r), **updates})

    def test_late_missing_asof_missing_features_invalid_probabilities_are_rejected(self):
        r = receipt()
        for updates in [{"first_seen_utc": r["event_start_utc"]},
                        {"forecast_created_utc": "2026-11-01T13:00:00Z"},
                        {"forecast_created_utc": "2026-11-01T12:00:00"},
                        {"source_timestamps": {}}, {"source_timestamps": {"poll": None}},
                        {"source_timestamps": {"poll": "2026-11-02T12:00:00Z"}},
                        {"features": {}}, {"served_prob": True}, {"base_prob": -1},
                        {"served_prob": float("nan")}, {"served_prob": float("inf")}]:
            with self.subTest(updates=updates), self.assertRaises(ValueError):
                reseal(r, **updates)

    def test_mutation_and_resealing_existing_envelope_are_not_silent(self):
        r = receipt()
        changed = copy.deepcopy(r)
        changed["features"]["poll_margin"] = 999
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            validate_probability_receipt(changed)
        with self.assertRaises(ValueError):
            seal_receipt(r)

    def test_conflicting_outcomes_and_same_time_forecasts_quarantine_not_order(self):
        a, b = receipt("a", .8), receipt("a", .3)
        report = score_binary_receipts([a], [outcome(a), outcome(a, False)])
        self.assertEqual(report["n"], 0)
        self.assertIn("conflicting_outcomes", report["excluded"])
        one = score_binary_receipts([a, b], [outcome(a), outcome(b)])
        two = score_binary_receipts([b, a], [outcome(b), outcome(a)])
        self.assertEqual(one, two)
        self.assertEqual(one["n"], 0)
        self.assertIn("conflicting_first_forecast", one["excluded"])

    def test_later_vintage_does_not_replace_initial_probability(self):
        a = receipt("a", .8)
        later = reseal(receipt("a", .3), forecast_created_utc="2026-11-01T13:00:00Z",
                       first_seen_utc="2026-11-01T13:00:01Z")
        report = score_binary_receipts([later, a], [outcome(later), outcome(a)])
        self.assertEqual(report["n"], 1)
        self.assertAlmostEqual(report["mean_served_prob"], .8)

    def test_two_cycles_preserve_ids_and_original_receipt_probabilities(self):
        a = receipt("a", .8)
        cycle1 = score_binary_receipts([a], [outcome(a)])
        b = reseal(receipt("b", .4, "election-2", "other-election"), calibrator_version="cal-v2")
        cycle2 = score_binary_receipts([b, a, a], [outcome(a), outcome(b, False)])
        self.assertEqual(cycle1["rows"][0]["receipt_id"], a["receipt_id"])
        self.assertEqual(prediction_id(copy.deepcopy(a)), prediction_id(a))
        self.assertEqual(a["served_prob"], .8)
        self.assertEqual(cycle2["n"], 2)
        self.assertAlmostEqual(cycle2["brier"], (.2 ** 2 + .4 ** 2) / 2)

    def test_empty_denominators_are_null_and_scores_do_not_mutate_inputs(self):
        r = receipt()
        original = copy.deepcopy(r)
        report = score_binary_receipts([r], [])
        self.assertEqual(report["n"], 0)
        self.assertIsNone(report["log_loss"])
        self.assertIsNone(report["brier"])
        self.assertEqual(r, original)


if __name__ == "__main__":
    unittest.main()
