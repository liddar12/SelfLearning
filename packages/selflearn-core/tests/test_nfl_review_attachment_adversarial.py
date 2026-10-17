"""Independent local raw-producer diagnostic checks, not SQL/hosted authority.

Only the shared explicit raw fixture generator is reused; evaluator and
attachment both execute. No author's test bodies or fake successful diagnostic
are used. Claims of supplied namespace/watermark remain unauthenticated.
"""
import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from selflearn_core.probability_receipts import fingerprint
from tools import nfl_review_attachment as attachment, nfl_review_parity as evaluator
from test_nfl_review_parity import actual_fixture


CHECKOUT = os.environ.get("NFL2026_CHECKOUT")
AVAILABLE = bool(CHECKOUT and (Path(CHECKOUT) / "scripts/pinned_cohort_oracle.py").is_file())


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def base_review(diagnostic):
    return {"season": 2026, "generated_utc": diagnostic["as_of_utc"], "review_through_week": 6,
        "weeks": {"1": {"summary": {"picks": {"right": 2, "wrong": 1}}},
                  "6": {"parlays": [{"result": "pending", "sim_net": 31.0}],
                        "as_offered": {"wins": 14, "cards": 63}}},
        "learning": {"legacy_only": True, "n": 0}, "refit": {"adopt": False},
        "settlement": {"reduced_wins": 3}, "opaque": "\u2603\\\"[]"}


@unittest.skipUnless(AVAILABLE, "Explicit NFL2026_CHECKOUT required for real local producer/oracle QA")
class IndependentReviewAttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = actual_fixture(CHECKOUT)
        cls.diagnostics = [evaluator.evaluate(value, expected_input_id=fingerprint(value),
            nfl_checkout=CHECKOUT, now_utc=stamp()) for value in cls.inputs]

    def attach(self, diagnostic=None, base=None):
        diagnostic = copy.deepcopy(self.diagnostics[1]) if diagnostic is None else diagnostic
        base = base_review(diagnostic) if base is None else base
        return attachment.attach_engine_parity(base, diagnostic,
            expected_input_id=diagnostic["input_id"], now_utc=stamp())

    def test_real_unchanged_confirmations_attach_to_one_detached_field_without_regrading_or_aliasing(self):
        original_input = copy.deepcopy(self.inputs[1]); diagnostic = copy.deepcopy(self.diagnostics[1])
        base = base_review(diagnostic); original_base, original_diagnostic = encoded(base), encoded(diagnostic)
        with patch("builtins.open", side_effect=AssertionError("attachment must not open resources")), \
             patch("socket.create_connection", side_effect=AssertionError("attachment must not connect")):
            result = self.attach(diagnostic, base)
        self.assertTrue(result["engine_parity"]["diagnostic"]["parity"])
        self.assertEqual(result["engine_parity"]["diagnostic"]["coverage"]["supplied_observations"], 8)
        self.assertEqual(result["engine_parity"]["review_input_id"], fingerprint(base))
        block = result.pop("engine_parity")
        self.assertEqual(encoded(result), original_base)
        self.assertEqual(encoded(base), original_base); self.assertEqual(encoded(diagnostic), original_diagnostic)
        self.assertEqual(self.inputs[1], original_input)
        self.assertEqual(block["attachment_id"], fingerprint({k: v for k, v in block.items() if k != "attachment_id"}))
        block["diagnostic"]["coverage"]["graded_contracts"] = 999
        result["weeks"]["6"]["parlays"].clear()
        self.assertEqual(encoded(base), original_base); self.assertEqual(encoded(diagnostic), original_diagnostic)
        self.assertIs(type(base["weeks"]["6"]["parlays"][0]["sim_net"]), float)

    def test_actual_voids_and_missing_database_fence_stay_unmeasured_or_unsupported(self):
        void = self.attach(copy.deepcopy(self.diagnostics[4]))["engine_parity"]
        self.assertFalse(void["diagnostic"]["parity"])
        self.assertEqual(void["diagnostic"]["coverage"]["graded_contracts"], 0)
        self.assertIsNone(void["diagnostic"]["nfl_scalars"]["brier"])
        self.assertEqual(void["diagnostic"]["coverage"]["nfl_excluded"]["non_binary_latest_revision"], 2)
        value = copy.deepcopy(self.inputs[1]); value["analysis"]["snapshot"]["watermark"] = None
        diagnostic = evaluator.evaluate(value, expected_input_id=fingerprint(value), nfl_checkout=CHECKOUT, now_utc=stamp())
        block = self.attach(diagnostic)["engine_parity"]
        self.assertEqual(block["diagnostic"]["status"], "unsupported_local_content_pin")
        self.assertFalse(block["diagnostic"]["parity"])
        for key in ("live_learning", "namespace_authenticated", "completeness_authenticated", "model_adoption",
                    "accuracy_improvement_claimed", "live_review_replaced"):
            self.assertIs(block[key], False)

    def test_exact_cutoff_review_scope_and_original_hash_are_not_inferred_from_matching_numbers(self):
        diagnostic = copy.deepcopy(self.diagnostics[1]); base = base_review(diagnostic)
        changes = [lambda r: r.update(season=2025), lambda r: r.update(review_through_week=5),
            lambda r: r.update(generated_utc=(datetime.fromisoformat(r["generated_utc"].replace("Z", "+00:00"))
                + timedelta(microseconds=1)).isoformat()), lambda r: r.update(weeks={"1": {}}),
            lambda r: r.update(weeks={6: {}})]
        for change in changes:
            altered = copy.deepcopy(base); change(altered)
            with self.subTest(change=changes.index(change)), self.assertRaisesRegex(ValueError, "^invalid_engine_parity_attachment$"):
                self.attach(diagnostic, altered)
        result = self.attach(diagnostic, base)
        with self.assertRaisesRegex(ValueError, "^invalid_engine_parity_attachment$"):
            self.attach(diagnostic, result)
        # A different legacy review can carry the same independent diagnostic,
        # but receives a different exact ORIGINAL review hash. No grade rewrite.
        other = copy.deepcopy(base); other["weeks"]["6"]["as_offered"]["wins"] = 99
        changed = self.attach(diagnostic, other)
        self.assertNotEqual(result["engine_parity"]["review_input_id"], changed["engine_parity"]["review_input_id"])
        self.assertEqual(changed["weeks"]["6"]["as_offered"]["wins"], 99)

    def test_resealed_authority_upgrade_and_changed_input_pin_are_rejected_with_static_reason(self):
        for key in ("namespace_authenticated", "completeness_authenticated", "model_adoption", "live_review_replaced"):
            diagnostic = copy.deepcopy(self.diagnostics[1]); diagnostic[key] = True
            diagnostic["diagnostic_id"] = fingerprint({k: v for k, v in diagnostic.items() if k != "diagnostic_id"})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "^invalid_engine_parity_attachment$"):
                self.attach(diagnostic)
        diagnostic = copy.deepcopy(self.diagnostics[1])
        with self.assertRaisesRegex(ValueError, "^invalid_engine_parity_attachment$"):
            attachment.attach_engine_parity(base_review(diagnostic), diagnostic,
                expected_input_id=fingerprint("different raw snapshot"), now_utc=stamp())

    def test_real_canonical_byte_boundary_and_disabled_path_preserve_types_without_io(self):
        diagnostic = copy.deepcopy(self.diagnostics[1]); base = base_review(diagnostic)
        for constant, value in (("MAX_REVIEW_BYTES", base), ("MAX_DIAGNOSTIC_BYTES", diagnostic)):
            with patch.object(attachment, constant, len(encoded(value))):
                self.assertIn("engine_parity", self.attach(diagnostic, base))
            with patch.object(attachment, constant, len(encoded(value)) - 1), \
                 self.assertRaisesRegex(ValueError, "^invalid_engine_parity_attachment$"):
                self.attach(diagnostic, base)
        opaque = {"generated_utc": "legacy unknown", "n": 1.0, "flag": True, "unknown": None}
        result = attachment.attach_engine_parity(opaque)
        self.assertEqual(encoded(result), encoded(opaque)); self.assertIsNot(result, opaque)
        self.assertIs(type(result["n"]), float); self.assertIs(type(result["flag"]), bool)


if __name__ == "__main__":
    unittest.main()
