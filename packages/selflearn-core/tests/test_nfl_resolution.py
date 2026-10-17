"""FINAL evidence joins the original persisted forecast, never a resealed one."""
import copy
import unittest

from selflearn_core.adapters.nfl import adapt_my_cards, adapt_prospective
from selflearn_core.adapters.nfl_resolution import resolve_prospective
from selflearn_core.outcome_revisions import validate_journal_resolution
from selflearn_core.probability_receipts import fingerprint, seal_receipt, validate_resolution
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.store.receipt_journal import ReceiptJournal
from test_nfl_adapter import fixture

OBSERVED = "2026-10-18T22:00:00Z"


def settlement_fixture(**kwargs):
    doc, old_scores, games = fixture(**kwargs)
    gid = next(iter(games))
    pregame = copy.deepcopy(games)
    pregame[gid]["status"] = "STATUS_SCHEDULED"
    pending = adapt_prospective(card_docs=[doc], game_evidence=pregame,
                                observed_utc="2026-10-18T17:01:00Z")
    assert len(pending["receipts"]) == 1, pending
    receipt = pending["receipts"][0]
    source = doc["cards"][0]["legs"][0]["learning_receipt"]
    leg = {k: copy.deepcopy(v) for k, v in doc["cards"][0]["legs"][0].items() if k != "learning_receipt"}
    leg.update(week=6, source_receipt_id=source["receipt_id"], result="hit", actual=80,
               played=True, status="FINAL", authoritative=True, source="official stats fixture",
               source_revision_id="stats-r1", source_updated_utc="2026-10-18T21:15:00Z")
    games[gid]["source_updated_utc"] = "2026-10-18T21:12:00Z"
    return receipt, source, leg, games, doc, old_scores


def resolve(r, s, legs, games, *, history=(), observed=OBSERVED):
    return resolve_prospective([r], [s], legs, games, observed_utc=observed, existing_revisions=history)


class NFLResolutionTest(unittest.TestCase):
    def test_final_and_overtime_resolve_the_exact_existing_envelope(self):
        for status in ("FINAL", "STATUS_FINAL", "STATUS_FINAL_OVERTIME"):
            with self.subTest(status=status):
                r, s, leg, games, doc, old_scores = settlement_fixture(correction={"a": -.7, "b": .7})
                frozen = copy.deepcopy([r, s, leg, games])
                old_envelope = adapt_my_cards([doc], old_scores, games)["receipts"][0]
                self.assertNotEqual(old_envelope["receipt_id"], r["receipt_id"])
                games["g1"]["status"] = status
                leg["status"] = status
                batch = resolve(r, s, [leg], games)
                self.assertEqual(batch["rejections"], [])
                proposal = batch["proposals"][0]
                self.assertEqual(proposal["resolution"]["receipt_id"], r["receipt_id"])
                with ReceiptJournal(":memory:") as journal:
                    journal.append_receipt(r)
                    self.assertTrue(journal.append_resolution(**proposal))
                    report = score_binary_receipts(journal.receipts(), journal.latest_resolutions())
                    self.assertEqual(report["n"], 1)
                    self.assertAlmostEqual(report["mean_served_prob"], r["served_prob"])
                self.assertEqual([r, s], frozen[:2])

    def test_replay_and_correction_preserve_receipt_and_old_revision(self):
        r, s, leg, games, *_ = settlement_fixture()
        frozen = copy.deepcopy(r)
        with ReceiptJournal(":memory:") as journal:
            journal.append_receipt(r)
            initial = resolve(r, s, [leg], games)["proposals"][0]
            journal.append_resolution(**initial)
            history = journal.resolution_history(r["receipt_id"])
            replay = resolve(r, s, [leg, copy.deepcopy(leg)], games, history=history,
                             observed="2026-10-18T23:00:00Z")["proposals"][0]
            self.assertEqual(replay, initial)
            self.assertFalse(journal.append_resolution(**replay))
            correction = {**leg, "result": "miss", "actual": 30, "source_revision_id": "stats-r2",
                          "source_updated_utc": "2026-10-18T22:15:00Z", "correction_reason": "official stat correction"}
            second = resolve(r, s, [correction], games, history=history,
                             observed="2026-10-18T23:00:00Z")["proposals"][0]
            self.assertEqual(second["supersedes"], initial["revision_id"])
            journal.append_resolution(**second)
            current_history = journal.resolution_history(r["receipt_id"])
            self.assertTrue(current_history[0]["resolution"]["hit"])
            self.assertFalse(current_history[1]["resolution"]["hit"])
            old_replay = resolve(r, s, [leg], games, history=list(reversed(current_history)),
                                 observed="2026-10-18T23:30:00Z")
            self.assertEqual(old_replay["proposals"], [initial])
            self.assertFalse(journal.append_resolution(**old_replay["proposals"][0]))
            self.assertEqual(journal.receipts(), [frozen])

    def test_authoritative_void_revision_invalidates_prior_hit_without_erasing_it(self):
        r, s, leg, games, *_ = settlement_fixture()
        with ReceiptJournal(":memory:") as journal:
            journal.append_receipt(r)
            first = resolve(r, s, [leg], games)["proposals"][0]
            journal.append_resolution(**first)
            void = {**leg, "result": "void", "actual": None, "played": False, "reason": "did_not_play",
                    "source_revision_id": "stats-r2", "source_updated_utc": "2026-10-18T22:15:00Z",
                    "correction_reason": "official participation correction"}
            result = resolve(r, s, [void], games, history=journal.resolution_history(r["receipt_id"]),
                             observed="2026-10-18T23:00:00Z")
            self.assertEqual(result["rejections"], [])
            revision = result["proposals"][0]
            journal.append_resolution(**revision)
            latest = journal.latest_resolutions()
            self.assertEqual(latest[0]["status"], "VOID")
            self.assertIsNone(latest[0]["hit"])
            self.assertEqual(score_binary_receipts(journal.receipts(), latest)["n"], 0)
            self.assertTrue(journal.resolution_history(r["receipt_id"])[0]["resolution"]["hit"])
            with self.assertRaises(ValueError):
                validate_resolution(r, latest[0])  # The binary scorer contract stays unchanged.

    def test_pending_missing_dnp_unknown_and_fake_void_are_not_false_losses(self):
        cases = ({"result": "pending"}, {"actual": None}, {"played": False},
                 {"played": None}, {"status": "LIVE"}, {"authoritative": False},
                 {"result": "void", "actual": None, "reason": "missing_stats"},
                 {"result": "void", "actual": None, "reason": "did_not_play", "played": True})
        for update in cases:
            r, s, leg, games, *_ = settlement_fixture()
            result = resolve(r, s, [{**leg, **update}], games)
            self.assertEqual(result["proposals"], [], update)
            self.assertTrue(result["rejections"], update)
        r, s, _, games, *_ = settlement_fixture()
        self.assertEqual(resolve(r, s, [], games)["summary"]["exclusion_reasons"], {"no_final_grade": 1})

    def test_source_times_and_explicit_final_game_are_mandatory(self):
        for kind in ("future_stat", "future_game", "before_final_stat", "missing_final", "live_game", "wrong_kickoff"):
            r, s, leg, games, *_ = settlement_fixture()
            if kind == "future_stat": leg["source_updated_utc"] = "2026-10-19T00:00:00Z"
            elif kind == "future_game": games["g1"]["source_updated_utc"] = "2026-10-19T00:00:00Z"
            elif kind == "before_final_stat": leg["source_updated_utc"] = "2026-10-18T20:00:00Z"
            elif kind == "missing_final": del games["g1"]["final_utc"]
            elif kind == "live_game": games["g1"]["status"] = "STATUS_IN_PROGRESS"
            else: games["g1"]["kickoff_utc"] = "2026-10-18T19:00:00Z"
            with self.subTest(kind=kind):
                result = resolve(r, s, [leg], games)
                self.assertEqual(result["proposals"], [])
                self.assertTrue(result["rejections"])

    def test_exact_stat_grade_identity_and_original_frozen_values_are_checked(self):
        for update in ({"gsis_id": "other"}, {"line": 40}, {"week": 5}, {"actual": 30},
                       {"direction": "under"}, {"comparison_operator": "gt"}, {"model_prob": .01},
                       {"actual": True}, {"receipt_id": "different"}):
            r, s, leg, games, *_ = settlement_fixture()
            self.assertEqual(resolve(r, s, [{**leg, **update}], games)["proposals"], [], update)
        r, s, leg, games, *_ = settlement_fixture()
        changed = {k: v for k, v in r.items() if k != "receipt_id"}
        changed["base_prob"] = .1
        bad = seal_receipt(changed)
        result = resolve(bad, s, [leg], games)
        self.assertIn("persisted_source_base_prob_mismatch", result["summary"]["exclusion_reasons"])

    def test_conflicting_duplicate_grades_and_sources_are_order_independent(self):
        r, s, leg, games, *_ = settlement_fixture()
        other = {**leg, "actual": 30, "result": "miss", "source_revision_id": "stats-r2"}
        a = resolve(r, s, [leg, other], games)
        b = resolve(r, s, [other, leg], games)
        self.assertEqual(a, b)
        self.assertEqual(a["proposals"], [])
        bad_source = {**s, "served_prob": .01}
        conflict = resolve_prospective([r], [s, bad_source], [leg], games, observed_utc=OBSERVED)
        self.assertEqual(conflict["proposals"], [])

    def test_conflicting_contract_grades_across_vintages_quarantine_both(self):
        a, sa, la, ga, *_ = settlement_fixture()
        b, sb, lb, gb, *_ = settlement_fixture(probability=.3)
        lb.update(actual=20, result="miss")
        result = resolve_prospective([a, b], [sa, sb], [la, lb], {**ga, **gb}, observed_utc=OBSERVED)
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["summary"]["exclusion_reasons"], {"conflicting_contract_outcomes": 2})

    def test_conflicted_or_invalid_copy_quarantines_other_vintage_of_same_contract(self):
        a, sa, la, games, *_ = settlement_fixture()
        b, sb, lb, _, *_ = settlement_fixture(probability=.3)
        conflict = {**la, "actual": 20, "result": "miss"}
        for bad in ([la, conflict, lb], [{**la, "played": None}, lb],
                    [{**la, "source_updated_utc": "2026-10-19T00:00:00Z"}, lb]):
            with self.subTest(bad=bad):
                result = resolve_prospective([a, b], [sa, sb], bad, games, observed_utc=OBSERVED)
                reversed_result = resolve_prospective([b, a], [sb, sa], list(reversed(bad)), games,
                                                      observed_utc=OBSERVED)
                self.assertEqual(result, reversed_result)
                self.assertEqual(result["proposals"], [])
                self.assertIn("quarantined_contract_evidence", result["summary"]["exclusion_reasons"])

    def test_source_clock_cannot_regress_behind_a_fresher_game_clock(self):
        r, s, leg, games, *_ = settlement_fixture()
        original = resolve(r, s, [leg], games)["proposals"][0]
        games["g1"]["source_updated_utc"] = "2026-10-18T22:00:00Z"
        stale = {**leg, "result": "miss", "actual": 30, "source_revision_id": "stats-r2",
                 "source_updated_utc": "2026-10-18T21:14:00Z", "correction_reason": "official correction"}
        result = resolve(r, s, [stale], games, history=[original], observed="2026-10-18T23:00:00Z")
        self.assertEqual(result["proposals"], [])
        self.assertIn("correction_source_time_regressed", result["summary"]["exclusion_reasons"])

    def test_existing_history_must_be_temporally_available_and_well_formed(self):
        r, s, leg, games, *_ = settlement_fixture()
        initial = resolve(r, s, [leg], games)["proposals"][0]
        cases = (({"version": True}, "unsupported_existing_revision_version"),
                 ({"revision_id": "x" * 201}, "invalid_existing_revision_id"),
                 ({"reason": 123}, "invalid_existing_revision_reason"))
        for update, expected in cases:
            result = resolve(r, s, [leg], games, history=[{**initial, **update}])
            self.assertEqual(result["proposals"], [])
            self.assertIn(expected, result["summary"]["exclusion_reasons"])
        future = copy.deepcopy(initial)
        future["resolution"]["evidence_observed_utc"] = "2026-10-19T01:00:00Z"
        result = resolve(r, s, [leg], games, history=[future])
        self.assertEqual(result["proposals"], [])
        self.assertIn("existing_revision_after_observation", result["summary"]["exclusion_reasons"])

    def test_invalid_revision_of_one_vintage_withholds_whole_contract_batch(self):
        a, sa, la, games, *_ = settlement_fixture()
        b, sb, lb, _, *_ = settlement_fixture(probability=.3)
        original = resolve(a, sa, [la], games)["proposals"][0]
        # This changes a previously immutable official source revision. A
        # second model vintage must not sneak through as an unrelated first hit.
        changed = {**la, "actual": 81, "correction_reason": "revised without new official revision"}
        lb["actual"] = 81
        result = resolve_prospective([a, b], [sa, sb], [changed, lb], games,
                                     observed_utc=OBSERVED, existing_revisions=[original])
        self.assertEqual(result["proposals"], [])
        self.assertIn("conflicting_official_source_revision", result["summary"]["exclusion_reasons"])
        self.assertIn("quarantined_contract_evidence", result["summary"]["exclusion_reasons"])

    def test_existing_chain_cannot_reuse_a_changed_official_source_revision(self):
        r, s, leg, games, *_ = settlement_fixture()
        original = resolve(r, s, [leg], games)["proposals"][0]
        bad = copy.deepcopy(original)
        bad.update(revision_id="invalid-changed-official-revision", supersedes=original["revision_id"],
                   reason="bad existing correction")
        bad["resolution"]["actual"] = 81
        result = resolve(r, s, [leg], games, history=[original, bad])
        self.assertEqual(result["proposals"], [])
        self.assertIn("conflicting_existing_official_source_revision", result["summary"]["exclusion_reasons"])

    def test_actual_zero_or_negative_yards_are_real_misses_only_with_participation(self):
        for actual in (0, -3):
            r, s, leg, games, *_ = settlement_fixture()
            leg.update(actual=actual, result="miss")
            result = resolve(r, s, [leg], games)
            self.assertEqual(result["rejections"], [])
            self.assertIs(result["proposals"][0]["resolution"]["hit"], False)

    def test_same_source_revision_cannot_change_payload_and_corrections_need_reason(self):
        r, s, leg, games, *_ = settlement_fixture()
        initial = resolve(r, s, [leg], games)["proposals"][0]
        for changes, reason in (({"actual": 30, "result": "miss", "correction_reason": "changed"},
                                 "conflicting_official_source_revision"),
                                ({"source_revision_id": "stats-r2"}, "correction_reason_required")):
            result = resolve(r, s, [{**leg, **changes}], games, history=[initial])
            self.assertEqual(result["proposals"], [])
            self.assertIn(reason, result["summary"]["exclusion_reasons"])

    def test_two_cycles_keep_original_forecast_and_all_unresolved_candidates(self):
        a, sa, la, ga, *_ = settlement_fixture()
        b, sb, lb, gb, *_ = settlement_fixture(player="espn-456", game="g2", probability=.3,
                                               correction={"a": .1, "b": .9})
        old = copy.deepcopy([a, b])
        with ReceiptJournal(":memory:") as journal:
            journal.append_receipt(a)
            first = resolve(a, sa, [la], ga)["proposals"][0]
            journal.append_resolution(**first)
            journal.append_receipt(b)
            partial = resolve_prospective(journal.receipts(), [sa, sb], [la], {**ga, **gb},
                        observed_utc=OBSERVED, existing_revisions=journal.resolution_history(a["receipt_id"]))
            self.assertEqual(partial["summary"]["exclusion_reasons"], {"no_final_grade": 1})
            second = resolve_prospective(journal.receipts(), [sa, sb], [la, lb], {**ga, **gb},
                        observed_utc=OBSERVED, existing_revisions=journal.resolution_history(a["receipt_id"]))
            self.assertEqual(second["rejections"], [])
            for proposal in second["proposals"]:
                journal.append_resolution(**proposal)
            self.assertEqual(score_binary_receipts(journal.receipts(), journal.latest_resolutions())["n"], 2)
            self.assertEqual({r["receipt_id"]: r for r in journal.receipts()}, {r["receipt_id"]: r for r in old})

    def test_generic_void_validator_refuses_missing_final_authority_hit_and_linkage(self):
        r, *_ = settlement_fixture()
        void = {"receipt_id": r["receipt_id"], "contract_id": r["contract_id"], "event_id": r["event_id"],
                "status": "VOID", "hit": None, "source": "official settlement", "resolved_utc": OBSERVED,
                "reason": "official_void", "authoritative": True, "final_evidence_status": "FINAL"}
        validate_journal_resolution(r, void)
        for update in ({"hit": False}, {"authoritative": False}, {"final_evidence_status": "PENDING"},
                       {"source": ""}, {"reason": ""}, {"event_id": "other"}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                validate_journal_resolution(r, {**void, **update})

    def test_optional_normalized_source_and_observation_clocks_are_not_ignored(self):
        r, s, leg, games, *_ = settlement_fixture()
        binary = resolve(r, s, [leg], games)["proposals"][0]["resolution"]
        void = {**binary, "status": "VOID", "hit": None, "reason": "official_void", "actual": None}
        for value in (binary, void):
            with self.subTest(status=value["status"]):
                validate_journal_resolution(r, value)
                for update in ({"source_updated_utc": "2026-10-19T01:00:00Z"},
                               {"evidence_observed_utc": "2026-10-18T20:00:00Z"},
                               {"source_updated_utc": None}):
                    with self.subTest(update=update), self.assertRaises(ValueError):
                        validate_journal_resolution(r, {**value, **update})

    def test_malformed_intake_records_are_counted_and_unlinked_scores_are_not_admitted(self):
        r, s, leg, games, *_ = settlement_fixture()
        batch = resolve_prospective([None, r], [None, s], [None, {}, leg], games, observed_utc=OBSERVED)
        self.assertEqual(len(batch["proposals"]), 1)
        for reason in ("unsupported_receipt_version", "invalid_source_receipt",
                       "invalid_scored_leg", "missing_score_source_receipt_id"):
            self.assertIn(reason, batch["summary"]["exclusion_reasons"])
        for receipts, sources, expected in (([], [s], "missing_persisted_receipt"),
                                             ([r], [], "missing_original_source_receipt")):
            result = resolve_prospective(receipts, sources, [leg], games, observed_utc=OBSERVED)
            self.assertEqual(result["proposals"], [])
            self.assertIn(expected, result["summary"]["exclusion_reasons"])
        with self.assertRaisesRegex(ValueError, "malformed_existing_revision_history"):
            resolve(r, s, [leg], games, history=[{"resolution": None}])

    def test_invalid_game_identity_source_clock_and_nonnull_void_stat_are_rejected(self):
        for kind, expected in (("identity", "final_game_identity_mismatch"),
                               ("game_source_early", "game_source_before_final"),
                               ("nonnull_void", "void_actual_must_be_null"),
                               ("event", "persisted_contract_or_event_mismatch")):
            r, s, leg, games, *_ = settlement_fixture()
            if kind == "identity":
                games["g1"]["week"] = 5
            elif kind == "game_source_early":
                games["g1"]["source_updated_utc"] = "2026-10-18T21:09:00Z"
            elif kind == "nonnull_void":
                leg.update(result="void", reason="official_void")
            else:
                payload = {k: v for k, v in r.items() if k != "receipt_id"}
                payload["event_id"] = "nfl:2026:game:different"
                r = seal_receipt(payload)
            with self.subTest(kind=kind):
                result = resolve(r, s, [leg], games)
                self.assertEqual(result["proposals"], [])
                self.assertIn(expected, result["summary"]["exclusion_reasons"])

    def test_missing_predecessors_forks_and_disconnected_history_are_rejected(self):
        r, s, leg, games, *_ = settlement_fixture()
        first = resolve(r, s, [leg], games)["proposals"][0]

        def child(rid, parent, source_revision):
            value = copy.deepcopy(first)
            value.update(revision_id=rid, supersedes=parent, reason="synthetic official correction")
            value["resolution"]["source_revision_id"] = source_revision
            return value

        missing = child("missing-child", "unknown-parent", "stats-r2")
        left = child("left-child", first["revision_id"], "stats-r2")
        right = child("right-child", first["revision_id"], "stats-r3")
        cycle_a = child("cycle-a", "cycle-b", "stats-r2")
        cycle_b = child("cycle-b", "cycle-a", "stats-r3")
        for history, expected in (([first, missing], "incomplete_existing_revision_chain"),
                                  ([first, left, right], "conflicting_existing_revision_heads"),
                                  ([first, cycle_a, cycle_b], "disconnected_existing_revision_chain")):
            with self.subTest(expected=expected):
                result = resolve(r, s, [leg], games, history=history)
                self.assertEqual(result["proposals"], [])
                self.assertIn(expected, result["summary"]["exclusion_reasons"])

    def test_existing_correction_clocks_cannot_regress(self):
        r, s, leg, games, *_ = settlement_fixture()
        first = resolve(r, s, [leg], games)["proposals"][0]
        for kind, expected in (("resolved", "existing_revision_time_regressed"),
                               ("source", "existing_revision_source_time_regressed"),
                               ("observed", "existing_revision_observation_time_regressed")):
            child = copy.deepcopy(first)
            child.update(revision_id="synthetic-correction", supersedes=first["revision_id"],
                         reason="synthetic official correction")
            child["resolution"]["source_revision_id"] = "stats-r2"
            child["resolution"]["resolved_utc"] = "2026-10-18T21:20:00Z"
            if kind == "resolved":
                child["resolution"].update(resolved_utc="2026-10-18T21:14:00Z",
                                           source_updated_utc="2026-10-18T21:14:00Z")
            elif kind == "source":
                child["resolution"]["source_updated_utc"] = "2026-10-18T21:14:00Z"
            else:
                child["resolution"]["evidence_observed_utc"] = "2026-10-18T21:30:00Z"
            with self.subTest(kind=kind):
                result = resolve(r, s, [leg], games, history=[first, child])
                self.assertEqual(result["proposals"], [])
                self.assertIn(expected, result["summary"]["exclusion_reasons"])

    def test_reused_existing_revision_id_cannot_hide_changed_reason_or_payload(self):
        r, s, leg, games, *_ = settlement_fixture()
        first = resolve(r, s, [leg], games)["proposals"][0]
        changed_reason = {**copy.deepcopy(first), "reason": "changed immutable reason"}
        result = resolve(r, s, [leg], games, history=[first, changed_reason])
        self.assertIn("conflicting_existing_revision_id", result["summary"]["exclusion_reasons"])
        changed_payload = copy.deepcopy(first)
        changed_payload["resolution"]["actual"] = 81
        result = resolve(r, s, [leg], games, history=[changed_payload])
        self.assertEqual(result["proposals"], [])
        self.assertIn("existing_revision_payload_conflict", result["summary"]["exclusion_reasons"])

    def test_correction_cannot_use_earlier_resolution_even_with_valid_stat_time(self):
        r, s, leg, games, *_ = settlement_fixture()
        games["g1"]["source_updated_utc"] = "2026-10-18T22:15:00Z"
        original = resolve(r, s, [leg], games, observed="2026-10-18T23:00:00Z")["proposals"][0]
        games["g1"]["source_updated_utc"] = "2026-10-18T21:16:00Z"
        changed = {**leg, "source_revision_id": "stats-r2", "source_updated_utc": "2026-10-18T21:16:00Z",
                   "correction_reason": "synthetic correction"}
        result = resolve(r, s, [changed], games, history=[original], observed="2026-10-18T23:00:00Z")
        self.assertEqual(result["proposals"], [])
        self.assertIn("correction_time_regressed", result["summary"]["exclusion_reasons"])


if __name__ == "__main__":
    unittest.main()
