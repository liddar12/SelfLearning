"""Pure attachment checks; mock diagnostic metadata is NOT oracle/SQL proof."""
import copy
from datetime import datetime, timezone
import json
import os
import unittest
from unittest.mock import patch

from tools import nfl_review_attachment as A
from selflearn_core.probability_receipts import fingerprint


def review():
    # Real build_review shape: weeks are string-keyed blocks, not a list.
    return {"season": 2026, "generated_utc": "2026-10-01T12:00:00Z", "review_through_week": 6,
        "weeks": {"1": {"summary": {"picks": {"right": 1, "wrong": 2}}},
            "6": {"parlays": [{"result": "pending", "money": {"kind": "potential", "net": 31.0}}],
                "summary": {"learning": {"graded_locks": 3}}, "as_offered": {"hits": 2, "total": 6}}},
        "learning": {"graded_locks_total": 3, "consumed_all": None}, "refit": {"a": 0.0},
        "settlement": {"reduced_cards": 3}, "sources": {"fixture": "unverified"},
        "players_season": {}, "notes": ["<script>opaque legacy text, not HTML authority</script>"]}


def reseal(value):
    value["diagnostic_id"] = fingerprint({k: v for k, v in value.items() if k != "diagnostic_id"})
    return value


def diagnostic():
    scalar = {"n": 2, "brier": .16, "log_loss": .51, "mean_served_prob": .6}
    return reseal({"version": 1, "kind": "nfl.review_parity_diagnostic.v1", "status": "supported_pinned_cohort_parity",
        "parity": True, "input_id": fingerprint("explicit-fake-input-not-an-evaluated-snapshot"),
        "full_snapshot_id": fingerprint("fake-snapshot"), "projection_id": fingerprint("fake-projection"),
        "comparator_input_id": fingerprint("fake-comparator-input"), "comparator_report_id": fingerprint("fake-report"),
        "namespace": dict(A.NAMESPACE), "namespace_authenticated": False, "completeness_authenticated": False,
        "pin_basis": "supplied_observed_snapshot_with_claimed_watermark", "snapshot_watermark": "100",
        "as_of_utc": "2026-10-01T12:00:00Z", "snapshot_exported_utc": "2026-10-01T12:01:00Z",
        "population": "eligible_pool", "scope": {"season": 2026, "weeks": [6], "population": "eligible_pool",
            "market_family": "MY_player_yardage_only"},
        "coverage": {"supplied_receipts": 4, "supplied_revisions": 4, "supplied_generation_records": 2,
            "supplied_observations": 8, "first_eligible_forecasts": 2, "graded_contracts": 2,
            "graded_fraction": 1.0, "all_first_eligible_contracts_graded": True,
            "nfl_excluded": {}, "engine_excluded": {}, "raw_producer_excluded": {},
            "capture_ids": [fingerprint("fake-capture")]},
        "nfl_set_id": fingerprint("fake-sets"), "engine_set_id": fingerprint("fake-sets"),
        "nfl_scalars": scalar, "engine_scalars": dict(scalar),
        "numeric_tolerance": {"absolute": 1e-12, "relative": 1e-12}, "reason_codes": [],
        "source_code_sha256": {"fixture_not_real_code.py": fingerprint("fake-code")},
        "source_authentication": "not_performed", "live_review_replaced": False, "model_adoption": False,
        "limitations": ["Explicit fake metadata tests; no comparator, SQL or authenticated execution evidence."]})


def attach(base=None, value=None, **kwargs):
    base, value = review() if base is None else base, diagnostic() if value is None else value
    return A.attach_engine_parity(base, value, expected_input_id=value["input_id"],
        now_utc="2026-10-02T00:00:00Z", **kwargs)


class ReviewAttachmentTests(unittest.TestCase):
    def test_disabled_is_a_detached_no_op_and_partial_configuration_is_not_opt_in(self):
        base = review(); base["generated_utc"] = "legacy-unparsed-clock"
        result = A.attach_engine_parity(base)
        self.assertEqual(result, base); self.assertIsNot(result, base)
        result["weeks"]["6"]["parlays"][0]["result"] = "changed"
        self.assertEqual(base["weeks"]["6"]["parlays"][0]["result"], "pending")
        choices = {"diagnostic": diagnostic(), "expected_input_id": diagnostic()["input_id"],
                   "now_utc": "2026-10-02T00:00:00Z"}
        for keys in (("diagnostic",), ("expected_input_id",), ("now_utc",),
                     ("diagnostic", "expected_input_id"), ("diagnostic", "now_utc"),
                     ("expected_input_id", "now_utc")):
            with self.subTest(keys=keys), self.assertRaises(ValueError):
                A.attach_engine_parity(review(), **{key: choices[key] for key in keys})

    def test_attachment_changes_only_one_top_level_field_and_is_content_pinned_without_auth_upgrades(self):
        base, value = review(), diagnostic()
        saved_base, saved_value = copy.deepcopy(base), copy.deepcopy(value)
        result = attach(base, value); block = result.pop("engine_parity")
        self.assertEqual(json.dumps(result, sort_keys=True), json.dumps(saved_base, sort_keys=True))
        self.assertEqual(base, saved_base); self.assertEqual(value, saved_value)
        self.assertEqual(block["review_input_id"], fingerprint(base))
        self.assertEqual(block["diagnostic_id"], value["diagnostic_id"])
        self.assertEqual(block["expected_input_id"], value["input_id"])
        self.assertEqual(block["attachment_id"], fingerprint({k: v for k, v in block.items() if k != "attachment_id"}))
        for key in ("live_learning", "namespace_authenticated", "completeness_authenticated", "model_adoption",
                    "live_review_replaced", "accuracy_improvement_claimed"):
            self.assertIs(block[key], False)
        self.assertIs(block["display_only"], True); self.assertIs(block["legacy_review_unchanged"], True)
        block["diagnostic"]["coverage"]["graded_contracts"] = 99
        result["weeks"]["6"]["parlays"].clear()
        self.assertEqual(base, saved_base); self.assertEqual(value, saved_value)
        self.assertEqual(attach(base, value), attach(base, value))

    def test_wrong_input_pin_tampered_diagnostic_and_replacing_an_attachment_are_refused(self):
        value = diagnostic()
        with self.assertRaises(ValueError):
            A.attach_engine_parity(review(), value, expected_input_id=fingerprint("wrong"), now_utc="2026-10-02T00:00:00Z")
        value["coverage"]["supplied_observations"] += 1
        with self.assertRaises(ValueError): attach(value=value)
        base = attach()
        with self.assertRaises(ValueError): attach(base=base)

    def test_clock_binding_compares_utc_instants_and_rejects_future_export_or_stale_review(self):
        base = review(); base["generated_utc"] = "2026-10-01T07:00:00-05:00"
        self.assertEqual(attach(base=base)["engine_parity"]["review_as_of_utc"], base["generated_utc"])
        changes = [lambda b, d: b.update(generated_utc="2026-10-01T12:00:01Z"),
            lambda b, d: d.update(as_of_utc="2026-10-01T11:59:59Z"),
            lambda b, d: d.update(snapshot_exported_utc="2026-10-01T11:59:59Z"),
            lambda b, d: d.update(snapshot_exported_utc="2099-01-01T00:00:00Z"),
            lambda b, d: d.update(as_of_utc="2026-10-01T12:00:00"),
            lambda b, d: b.update(generated_utc=True), lambda b, d: d.update(snapshot_exported_utc=None)]
        for change in changes:
            base, value = review(), diagnostic(); change(base, value); reseal(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError): attach(base, value)

    def test_season_weeks_population_and_market_scope_are_explicit_not_inferred_from_hashes(self):
        for through in (None, 22):
            base = review(); base["review_through_week"] = through
            self.assertEqual(attach(base=base)["review_through_week"], through)
        changes = [lambda b, d: b.update(season=2025), lambda b, d: b.update(season=True),
            lambda b, d: b.update(season=2026.0), lambda b, d: b.update(review_through_week=5),
            lambda b, d: b.update(review_through_week=6.0), lambda b, d: b.update(weeks=[]),
            lambda b, d: b.update(review_through_week=23), lambda b, d: b.pop("review_through_week"),
            lambda b, d: b["weeks"].update({"6": None}), lambda b, d: d.update(scope=None),
            lambda b, d: d["scope"].update(season="2026"), lambda b, d: d["scope"].update(weeks=[]),
            lambda b, d: d["scope"].update(weeks=[6, 6]), lambda b, d: d["scope"].update(weeks=[7]),
            lambda b, d: d["scope"].update(weeks=[True]), lambda b, d: d["scope"].update(weeks=[6.0]),
            lambda b, d: d["scope"].update(weeks=[6, 1]), lambda b, d: d["scope"].update(market_family="all_parlays"),
            lambda b, d: d.update(population="offered_my"), lambda b, d: d["scope"].update(population="offered_my"),
            lambda b, d: d["scope"].update(extra="SECRET")]
        for change in changes:
            base, value = review(), diagnostic(); change(base, value); reseal(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError): attach(base, value)

    def test_false_flags_strict_schema_and_approved_namespace_claim_are_mandatory(self):
        for flag in ("namespace_authenticated", "completeness_authenticated", "model_adoption", "live_review_replaced"):
            for wrong in (True, 0, None, "false"):
                value = diagnostic(); value[flag] = wrong; reseal(value)
                with self.subTest(flag=flag, wrong=wrong), self.assertRaises(ValueError): attach(value=value)
        changes = [lambda d: d.update(version=True), lambda d: d.update(version=1.0),
            lambda d: d.update(kind="other"), lambda d: d.update(status="live_learning"),
            lambda d: d.update(parity=1), lambda d: d.update(extra="SECRET"),
            lambda d: d["namespace"].update(producer_id="foreign"),
            lambda d: d["namespace"].update(destination_id=fingerprint("foreign")),
            lambda d: d.update(source_authentication="verified"), lambda d: d.pop("projection_id"),
            lambda d: d.update(comparator_report_id=None)]
        for change in changes:
            value = diagnostic(); change(value); reseal(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError) as caught: attach(value=value)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_partial_mismatch_and_missing_watermark_diagnostics_never_become_full_success(self):
        value = diagnostic(); value.update(status="partial_pinned_cohort_parity")
        value["coverage"].update(first_eligible_forecasts=4, graded_fraction=.5, all_first_eligible_contracts_graded=False)
        partial = attach(value=reseal(value))["engine_parity"]
        self.assertTrue(partial["diagnostic"]["parity"])
        self.assertFalse(partial["diagnostic"]["coverage"]["all_first_eligible_contracts_graded"])
        value = diagnostic(); value.update(parity=False, status="mismatch_or_unavailable", nfl_set_id=None, engine_set_id=None,
            reason_codes=["independent_nfl_scalar_oracle_differs"])
        value["coverage"]["all_first_eligible_contracts_graded"] = False
        self.assertFalse(attach(value=reseal(value))["engine_parity"]["diagnostic"]["parity"])
        value["engine_scalars"] = {"n": 0, "brier": None, "log_loss": None, "mean_served_prob": None}
        self.assertEqual(attach(value=reseal(value))["engine_parity"]["diagnostic"]["engine_scalars"]["n"], 0)
        value = diagnostic(); value.update(parity=False, status="unsupported_local_content_pin", snapshot_watermark=None,
            pin_basis="local_content_pin_only", reason_codes=["missing_supplied_database_watermark"])
        value["coverage"]["all_first_eligible_contracts_graded"] = False
        self.assertEqual(attach(value=reseal(value))["engine_parity"]["diagnostic"]["status"], "unsupported_local_content_pin")

    def test_coverage_types_scalar_counts_hashes_and_supported_status_cannot_contradict_each_other(self):
        changes = [lambda d: d["coverage"].update(graded_contracts=True),
            lambda d: d["coverage"].update(supplied_receipts=4.0),
            lambda d: d["coverage"].update(graded_contracts=5),
            lambda d: d["coverage"].update(graded_fraction=True),
            lambda d: d["coverage"].update(graded_fraction=1),
            lambda d: d["coverage"].update(all_first_eligible_contracts_graded=1),
            lambda d: d["coverage"]["nfl_excluded"].update(unknown=True),
            lambda d: d["coverage"].update(capture_ids=[fingerprint("c"), fingerprint("c")]),
            lambda d: d["coverage"].update(capture_ids=["SECRET"]),
            lambda d: d.update(snapshot_watermark="00"), lambda d: d.update(snapshot_watermark=100),
            lambda d: d.update(snapshot_watermark="9223372036854775808"),
            lambda d: d.update(pin_basis="authenticated"), lambda d: d.update(nfl_set_id=None),
            lambda d: d.update(engine_set_id=fingerprint("different")),
            lambda d: d["engine_scalars"].update(n=1), lambda d: d["engine_scalars"].update(brier=.99),
            lambda d: d["engine_scalars"].update(log_loss=-1), lambda d: d["nfl_scalars"].update(mean_served_prob=True),
            lambda d: d["numeric_tolerance"].update(absolute=1e-3),
            lambda d: d.update(reason_codes=["a", "a"]), lambda d: d.update(limitations=[]),
            lambda d: d.update(source_code_sha256={"SECRET": "wrong"}),
            lambda d: d.update(source_code_sha256={}), lambda d: d.update(parity=False),
            lambda d: d.update(status="partial_pinned_cohort_parity"),
            lambda d: d.update(status="unsupported_local_content_pin")]
        for change in changes:
            value = diagnostic(); change(value); reseal(value)
            with self.subTest(change=changes.index(change)), self.assertRaises(ValueError) as caught: attach(value=value)
            self.assertNotIn("SECRET", str(caught.exception))

    def test_unknown_outcomes_zero_denominator_and_null_scores_remain_unknown(self):
        value = diagnostic(); value.update(parity=False, status="mismatch_or_unavailable", nfl_set_id=None, engine_set_id=None,
            reason_codes=["no_supported_graded_cohort"])
        value["coverage"].update(graded_contracts=0, graded_fraction=0.0, all_first_eligible_contracts_graded=False)
        value["nfl_scalars"] = value["engine_scalars"] = {"n": 0, "brier": None, "log_loss": None, "mean_served_prob": None}
        self.assertIsNone(attach(value=reseal(value))["engine_parity"]["diagnostic"]["nfl_scalars"]["brier"])
        value["coverage"].update(first_eligible_forecasts=0, graded_fraction=None)
        self.assertIsNone(attach(value=reseal(value))["engine_parity"]["diagnostic"]["coverage"]["graded_fraction"])
        value["nfl_scalars"]["brier"] = 0.0
        with self.assertRaises(ValueError): attach(value=reseal(value))

    def test_plain_json_resource_limits_and_nonfinite_or_subclass_values_fail_closed(self):
        class Integer(int): pass
        class Text(str): pass
        class Mapping(dict): pass
        class Items(list): pass
        for bad in (float("nan"), float("inf"), Integer(1), Text("text"), Mapping(a=1), Items([1]), (1,), object(), 10 ** 400):
            base = review(); base["extra"] = bad
            with self.subTest(bad=type(bad).__name__), self.assertRaises(ValueError): attach(base=base)
        base = review(); base["weeks"][6] = {}
        with self.assertRaises(ValueError): attach(base=base)
        for constant in ("MAX_REVIEW_BYTES", "MAX_DIAGNOSTIC_BYTES", "MAX_NODES", "MAX_STRING_CHARS"):
            with patch.object(A, constant, 1), self.subTest(constant=constant), self.assertRaises(ValueError): attach()
        base = review(); branch = base
        for _ in range(A.MAX_DEPTH + 1):
            branch["deep"] = {}; branch = branch["deep"]
        with self.assertRaises(ValueError): attach(base=base)
        base = review(); base["cycle"] = base
        with self.assertRaises(ValueError): attach(base=base)

    def test_helper_opens_no_io_resources_and_preserves_numeric_identity(self):
        with patch("builtins.open", side_effect=AssertionError("No source open")), \
             patch("socket.create_connection", side_effect=AssertionError("No network")):
            one = attach()
        base = review(); base["refit"]["a"] = 0
        two = attach(base=base)
        self.assertNotEqual(one["engine_parity"]["review_input_id"], two["engine_parity"]["review_input_id"])
        self.assertEqual(one["refit"]["a"], two["refit"]["a"])
        self.assertIs(type(one["refit"]["a"]), float); self.assertIs(type(two["refit"]["a"]), int)


CHECKOUT = os.environ.get("NFL2026_CHECKOUT")


@unittest.skipUnless(CHECKOUT, "Explicit NFL2026_CHECKOUT required for actual raw-producer diagnostic attachment")
class ActualProducerAttachmentTests(unittest.TestCase):
    def test_actual_evaluator_full_confirmation_stream_attaches_without_rewriting_review(self):
        # Uses the actual pinned comparator/raw fixture, not the mocked DTOs above.
        from test_nfl_review_parity import actual_fixture
        from tools import nfl_review_parity as evaluator
        cutoff = datetime.now(timezone.utc).isoformat()
        value = actual_fixture(CHECKOUT)[1]
        diagnostic_value = evaluator.evaluate(value, expected_input_id=fingerprint(value),
            nfl_checkout=CHECKOUT, now_utc=datetime.now(timezone.utc).isoformat())
        self.assertEqual(diagnostic_value["namespace"], A.NAMESPACE)
        self.assertEqual(diagnostic_value["coverage"]["supplied_observations"], 8)
        base = review(); base["generated_utc"] = diagnostic_value["as_of_utc"]
        attached = A.attach_engine_parity(base, diagnostic_value, expected_input_id=fingerprint(value),
            now_utc=datetime.now(timezone.utc).isoformat())
        self.assertTrue(attached["engine_parity"]["diagnostic"]["parity"])
        self.assertEqual(attached["engine_parity"]["diagnostic"]["coverage"]["graded_contracts"], 2)
        self.assertEqual({k: v for k, v in attached.items() if k != "engine_parity"}, base)
        self.assertLess(cutoff, diagnostic_value["snapshot_exported_utc"])


if __name__ == "__main__":
    unittest.main()
