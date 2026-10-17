"""Observed-after-FINAL semantics, exact vintages and correction/retry safety."""
import copy
import unittest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import (
    BRIDGE_VERSION, DNP_BASIS, GAME_SOURCE, SNAP_SOURCE, STAT_SOURCE, resolve_observed)
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from selflearn_core.store.receipt_journal import ReceiptJournal
from test_nfl_adapter import fixture


PROCESSING = "2026-09-02T10:00:00Z"


def evidence(*, probability=.8, line=59.5):
    doc, _, _ = fixture(probability=probability)
    source = doc["cards"][0]["legs"][0]["learning_receipt"]
    source.update(forecast_created_utc="2026-09-01T17:00:00Z",
                  source_timestamps={"weekly": "2026-09-01T16:00:00Z"}, line=line)
    source["features"].update(line=line, mu=line + 40)
    key = [2026, "g1", "espn-123", "wr_rec_yds", format(line, ".12g"), "home", "over", "gte"]
    source["contract_id"] = fingerprint(key)
    source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
    card = doc["cards"][0]
    card.update(first_seen_utc="2026-09-01T17:01:00Z", earliest_kickoff_utc="2026-09-02T00:15:00Z")
    original_leg = card["legs"][0]
    original_leg["line"] = line
    scheduled = {"g1": {"status": "STATUS_SCHEDULED", "kickoff_utc": card["earliest_kickoff_utc"], "source": "fixture"}}
    batch = adapt_prospective(card_docs=[doc], game_evidence=scheduled, observed_utc=card["first_seen_utc"])
    assert len(batch["receipts"]) == 1, batch
    receipt = batch["receipts"][0]
    leg = {k: copy.deepcopy(v) for k, v in original_leg.items() if k != "learning_receipt"}
    leg.update(week=6, source_receipt_id=source["receipt_id"], result="hit", actual=80,
               played=True, status="FINAL", authoritative=True, source=STAT_SOURCE,
               observed_utc="2026-09-02T04:20:00Z", observation_id=fingerprint("stats-A"),
               participation_basis="stat_row", team="BUF", opp="MIA")
    games = {"g1": {"season": 2026, "week": 6, "game_id": "g1", "status": "STATUS_FINAL",
        "kickoff_utc": card["earliest_kickoff_utc"], "home": "BUF", "away": "MIA",
        "home_score": 24, "away_score": 17, "source": GAME_SOURCE,
        "observed_utc": "2026-09-02T04:15:00Z", "observation_id": fingerprint("game-final-A")}}
    return receipt, source, leg, games


def resolve(r, s, legs, games, history=(), observations=()):
    return resolve_observed([r], [s], legs, games, observed_utc=PROCESSING,
                            existing_revisions=history, existing_observations=observations)


class Delivery:
    """Explicitly retain both immutable streams; no history-to-proof shortcut."""
    def __init__(self, r, s, games):
        self.r, self.s, self.games = r, s, games
        self.history, self.observations = {}, {}

    def send(self, leg, *, games=None):
        result = resolve(self.r, self.s, [leg], games or self.games,
                         list(self.history.values()), list(self.observations.values()))
        for row in result["proposals"]:
            self.history[row["revision_id"]] = row
        for row in result["observation_proposals"]:
            self.observations[row["observation_id"]] = row
        return result


def corrected(leg, actual, label, time):
    return {**leg, "actual": actual, "result": "hit" if actual >= leg["line"] else "miss",
            "observation_id": fingerprint(label), "observed_utc": time}


def reseal_observation(row):
    """Adversarial fixture rehashing must not bypass semantic validation."""
    row["measurement_id"] = fingerprint(row["measurement"])
    row["evidence_observation_id"] = fingerprint({"measurement_id": row["measurement_id"],
                                                  "components": row["source_observations"]})
    row["observation_id"] = fingerprint({k: v for k, v in row.items() if k != "observation_id"})
    return row


class ObservedResolutionTests(unittest.TestCase):
    def test_unknown_provider_clocks_stay_unknown_and_observation_clock_is_not_processing_time(self):
        r, s, leg, games = evidence()
        frozen = copy.deepcopy([r, s, leg, games])
        batch = resolve(r, s, [leg], games)
        self.assertEqual(batch["rejections"], [])
        value = batch["proposals"][0]["resolution"]
        self.assertEqual(value["resolved_utc"], leg["observed_utc"])
        self.assertEqual(value["evidence_observed_utc"], leg["observed_utc"])
        self.assertIsNone(value["game_evidence"]["final_utc"])
        self.assertIsNone(value["game_evidence"]["source_updated_utc"])
        self.assertNotIn("source_revision_id", value)
        self.assertNotIn("source_updated_utc", value)
        self.assertFalse(value["timing_limitations"]["provider_final_revision_known"])
        self.assertEqual([r, s, leg, games], frozen)

    def test_all_final_statuses_and_irrelevant_score_capture_changes_reuse_current_measurement(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        initial = state.send(leg)["proposals"][0]
        for status in ("FINAL", "STATUS_FINAL", "STATUS_FINAL_OVERTIME"):
            changed_games = copy.deepcopy(games)
            changed_games["g1"].update(status=status, home_score=31, observation_id=fingerprint(status),
                                       observed_utc="2026-09-02T05:00:00Z")
            later = corrected(leg, 80.0, "unrelated-season-csv-change", "2026-09-02T05:01:00Z")
            later["evidence_capture_id"] = fingerprint("new unrelated data")
            result = state.send(later, games=changed_games)
            self.assertEqual(result["proposals"], [initial])
            self.assertEqual(result["summary"]["replayed_revisions"], 1)

    def test_newer_actual_correction_old_bundle_retry_and_A_B_A_are_distinct(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        with ReceiptJournal(":memory:") as db:
            db.append_receipt(r)
            first = state.send(leg)["proposals"][0]
            db.append_resolution(**first)
            changed = corrected(leg, 30, "stats-B", "2026-09-02T05:00:00Z")
            second = state.send(changed)["proposals"][0]
            self.assertEqual(second["supersedes"], first["revision_id"])
            db.append_resolution(**second)
            old_retry = state.send(leg)
            self.assertEqual(old_retry["proposals"], [first])
            self.assertFalse(db.append_resolution(**old_retry["proposals"][0]))
            self.assertFalse(db.latest_resolutions()[0]["hit"])
            again = corrected(leg, 80, "stats-A-new-observation", "2026-09-02T06:00:00Z")
            third = state.send(again)["proposals"][0]
            self.assertNotEqual(third["revision_id"], first["revision_id"])
            self.assertEqual(third["supersedes"], second["revision_id"])
            self.assertEqual(third["resolution"]["measurement_id"], first["resolution"]["measurement_id"])
            db.append_resolution(**third)
            self.assertEqual(len(db.resolution_history(r["receipt_id"])), 3)
            self.assertTrue(db.latest_resolutions()[0]["hit"])
            self.assertAlmostEqual(score_binary_receipts([r], db.latest_resolutions())["mean_served_prob"], r["served_prob"])
            self.assertEqual(db.receipts(), [r])

    def test_contradictory_older_observation_does_not_revert_current_head(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)["proposals"][0]
        newer = corrected(leg, 30, "stats-B", "2026-09-02T06:00:00Z")
        second = state.send(newer)["proposals"][0]
        stale = corrected(leg, 80, "unknown-stale-A", "2026-09-02T05:00:00Z")
        result = state.send(stale)
        self.assertEqual(result["proposals"], [])
        self.assertIn("stale_conflicting_source_observation", result["summary"]["exclusion_reasons"])

    def test_newer_unrelated_component_cannot_mask_stale_primary_stat(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)["proposals"][0]
        changed = corrected(leg, 30, "stats-B", "2026-09-02T06:00:00Z")
        second = state.send(changed)["proposals"][0]
        stale = corrected(leg, 80, "stale-stats", "2026-09-02T05:00:00Z")
        stale.update(participation_source=SNAP_SOURCE, participation_observed_utc="2026-09-02T07:00:00Z",
                     participation_observation_id=fingerprint("new irrelevant snaps"))
        result = state.send(stale)
        self.assertEqual(result["proposals"], [])
        self.assertIn("stale_conflicting_source_observation", result["summary"]["exclusion_reasons"])

    def test_same_raw_primary_observation_cannot_create_a_different_stat(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)["proposals"][0]
        changed = corrected(leg, 30, "unused", "2026-09-02T06:00:00Z")
        changed["observation_id"] = leg["observation_id"]
        result = state.send(changed)
        self.assertEqual(result["proposals"], [])
        self.assertIn("conflicting_existing_primary_observation_measurement", result["summary"]["exclusion_reasons"])

    def test_unchanged_newer_capture_durably_fences_a_late_older_correction(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)
        unchanged = state.send(corrected(leg, 80, "same-measurement-06", "2026-09-02T06:00:00Z"))
        self.assertEqual(unchanged["proposals"], first["proposals"])
        self.assertEqual(unchanged["proposals"][0]["resolution"]["resolved_utc"], leg["observed_utc"])
        self.assertEqual(len(state.observations), 2)
        self.assertNotEqual(first["observation_proposals"], unchanged["observation_proposals"])
        stale = state.send(corrected(leg, 30, "late-05", "2026-09-02T05:00:00Z"))
        self.assertEqual(stale["proposals"], [])
        self.assertEqual(stale["observation_proposals"], [])
        self.assertIn("stale_conflicting_source_observation", stale["summary"]["exclusion_reasons"])
        self.assertEqual(len(state.history), 1)

    def test_exact_unchanged_old_capture_without_own_revision_cannot_revert_new_head(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)
        same = corrected(leg, 80, "same-06", "2026-09-02T06:00:00Z")
        state.send(same)
        second = state.send(corrected(leg, 30, "changed-07", "2026-09-02T07:00:00Z"))
        retry = state.send(same)
        self.assertEqual(retry["proposals"], [])
        self.assertEqual(retry["summary"]["historical_observation_retries"], 1)
        self.assertEqual(len(retry["observation_proposals"]), 1)
        self.assertEqual(state.send(leg)["proposals"], first["proposals"])
        self.assertEqual(len(state.history), 2)
        self.assertEqual(second["proposals"][0]["supersedes"], first["proposals"][0]["revision_id"])

    def test_existing_v2_revision_without_durable_observation_proof_fails_closed(self):
        r, s, leg, games = evidence()
        first = resolve(r, s, [leg], games)
        missing = resolve(r, s, [leg], games, first["proposals"])
        self.assertEqual(missing["proposals"], [])
        self.assertEqual(missing["observation_proposals"], [])
        self.assertIn("missing_existing_observation_proof", missing["summary"]["exclusion_reasons"])
        with self.assertRaisesRegex(ValueError, "malformed_existing_observation_history"):
            resolve(r, s, [leg], games, observations=[None])

    def test_equal_primary_clock_contradictory_persisted_observations_quarantine_newer_delivery(self):
        r, s, leg, games = evidence()
        first = resolve(r, s, [leg], games)
        conflicting_leg = corrected(leg, 30, "other-primary-same-clock", leg["observed_utc"])
        contradictory = resolve(r, s, [conflicting_leg], games)
        self.assertEqual(contradictory["rejections"], [])
        observations = first["observation_proposals"] + contradictory["observation_proposals"]
        for records in (observations, list(reversed(observations))):
            result = resolve(r, s, [corrected(leg, 10, "newer-C", "2026-09-02T06:00:00Z")],
                             games, first["proposals"], records)
            self.assertEqual(result["proposals"], [])
            self.assertEqual(result["observation_proposals"], [])
            self.assertIn("conflicting_equal_clock_primary_measurements", result["summary"]["exclusion_reasons"])

    def test_equal_primary_clock_same_measurement_confirmation_is_valid(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)
        confirmation = state.send(corrected(leg, 80, "distinct-identical-primary", leg["observed_utc"]))
        self.assertEqual(confirmation["rejections"], [])
        self.assertEqual(confirmation["proposals"], first["proposals"])
        self.assertEqual(len(state.observations), 2)
        newer = state.send(corrected(leg, 30, "proper-newer-B", "2026-09-02T06:00:00Z"))
        self.assertEqual(newer["rejections"], [])
        self.assertEqual(newer["proposals"][0]["supersedes"], first["proposals"][0]["revision_id"])

    def test_unchanged_measurement_cannot_mutate_a_reused_capture_clock(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        state.send(leg)
        result = state.send({**leg, "observed_utc": "2026-09-02T06:00:00Z"})
        self.assertEqual(result["proposals"], [])
        self.assertIn("existing_source_observation_clock_mutated", result["summary"]["exclusion_reasons"])

    def test_rehashed_observation_records_require_valid_receipt_game_grade_and_clock_semantics(self):
        r, s, leg, games = evidence()
        first = resolve(r, s, [leg], games)
        original = first["observation_proposals"][0]
        mutations = (
            (lambda v: v.update(version=True), "invalid_existing_observation_record"),
            (lambda v: v.update(contract_id="wrong"), "existing_observation_receipt_mismatch"),
            (lambda v: v["measurement"].update(canonical_contract=[]), "invalid_existing_observation_measurement"),
            (lambda v: v["measurement"].update(week=True), "existing_observation_game_identity_mismatch"),
            (lambda v: v["measurement"]["game"].update(home="wrong"), "existing_observation_game_identity_mismatch"),
            (lambda v: v["measurement"].update(actual=True), "invalid_existing_observation_grade"),
            (lambda v: v["measurement"].update(status="LIVE"), "invalid_existing_observation_status"),
            (lambda v: v["measurement"].update(status="VOID", actual=None, hit=None, played=False,
                                             reason="official_void"), "invalid_existing_observation_void"),
            (lambda v: v["measurement"].update(actual=0, hit=False,
                participation_basis="r106_positive_offensive_snaps_no_stat_row"), "missing_existing_participation_observation"),
            (lambda v: v["source_observations"].pop(STAT_SOURCE), "missing_existing_required_observation"),
            (lambda v: v["source_observations"].update({STAT_SOURCE: []}), "missing_existing_required_observation"),
            (lambda v: v["source_observations"].update({STAT_SOURCE: "not-an-object"}), "missing_existing_required_observation"),
            (lambda v: v["source_observations"].update({"unknown": {}}), "invalid_existing_observation_component"),
            (lambda v: v["source_observations"][STAT_SOURCE].update(source="wrong"), "invalid_existing_observation_component"),
            (lambda v: v["source_observations"][STAT_SOURCE].update(observed_utc="2026-09-02T04:14:00Z"),
             "existing_observation_before_final_evidence"),
            (lambda v: v.update(observed_utc="2026-09-02T04:30:00Z"), "existing_observation_clock_mismatch"),
        )
        for mutate, reason in mutations:
            row = copy.deepcopy(original)
            mutate(row)
            reseal_observation(row)
            result = resolve(r, s, [leg], games, observations=[row])
            self.assertEqual(result["proposals"], [], reason)
            self.assertIn(reason, result["summary"]["exclusion_reasons"], reason)
        row = copy.deepcopy(original)
        row["evidence_observation_id"] = fingerprint("wrong")
        row["observation_id"] = fingerprint({k: v for k, v in row.items() if k != "observation_id"})
        self.assertIn("invalid_existing_observation_hash",
                      resolve(r, s, [leg], games, observations=[row])["summary"]["exclusion_reasons"])

    def test_malformed_primary_observation_quarantines_every_rung_in_underlying_scope(self):
        a, sa, la, games = evidence(line=59.5)
        b, sb, lb, _ = evidence(line=79.5)
        first = resolve(a, sa, [la], games)
        invalid = copy.deepcopy(first["observation_proposals"][0])
        invalid["source_observations"][STAT_SOURCE] = []
        reseal_observation(invalid)
        result = resolve_observed([a, b], [sa, sb], [la, lb], games,
            observed_utc=PROCESSING, existing_observations=[invalid])
        self.assertEqual(result["proposals"], [])
        self.assertEqual(result["observation_proposals"], [])
        self.assertIn("missing_existing_required_observation", result["summary"]["exclusion_reasons"])

    def test_DNP_requires_explicit_inference_basis_and_never_becomes_binary_loss(self):
        r, s, leg, games = evidence()
        dnp = {**leg, "result": "void", "actual": None, "played": False, "reason": "did_not_play",
               "source": SNAP_SOURCE, "participation_basis": DNP_BASIS}
        batch = resolve(r, s, [dnp], games)
        self.assertEqual(batch["rejections"], [])
        value = batch["proposals"][0]["resolution"]
        self.assertEqual(value["status"], "VOID")
        self.assertEqual(value["participation_basis"], DNP_BASIS)
        self.assertEqual(score_binary_receipts([r], [value])["n"], 0)
        for change in ({"participation_basis": None}, {"played": True}, {"source": STAT_SOURCE},
                       {"reason": "official_void"}, {"reason": "push"}, {"actual": 0}):
            self.assertEqual(resolve(r, s, [{**dnp, **change}], games)["proposals"], [], change)

    def test_binary_DNP_binary_preserves_prior_source_watermarks(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)["proposals"][0]
        dnp = {**leg, "result": "void", "actual": None, "played": False, "reason": "did_not_play",
               "source": SNAP_SOURCE, "participation_basis": DNP_BASIS,
               "observation_id": fingerprint("snaps correction"), "observed_utc": "2026-09-02T06:00:00Z"}
        second = state.send(dnp)["proposals"][0]
        self.assertIn(STAT_SOURCE, second["resolution"]["observation_watermarks"])
        final = corrected(leg, 0, "new stats after DNP", "2026-09-02T07:00:00Z")
        third = state.send(final)["proposals"][0]
        self.assertIn(SNAP_SOURCE, third["resolution"]["observation_watermarks"])
        self.assertFalse(third["resolution"]["hit"])

    def test_pending_missing_nonfinite_bool_and_unproved_participation_are_not_losses(self):
        r, s, leg, games = evidence()
        for change in ({"actual": None}, {"actual": True}, {"actual": float("nan")}, {"actual": float("inf")},
                       {"played": None}, {"played": False}, {"result": "pending"}, {"result": "miss"},
                       {"source": "espn.boxscore"}, {"authoritative": False}, {"status": "LIVE"}):
            result = resolve(r, s, [{**leg, **change}], games)
            self.assertEqual(result["proposals"], [], change)
            self.assertTrue(result["rejections"], change)
        self.assertEqual(resolve(r, s, [], games)["summary"]["exclusion_reasons"], {"no_observed_grade": 1})

    def test_required_snap_observation_advances_conservative_resolution_clock(self):
        r, s, leg, games = evidence()
        leg.update(actual=0, result="miss", participation_basis="r106_positive_offensive_snaps_no_stat_row",
                   participation_source=SNAP_SOURCE, participation_observed_utc="2026-09-02T04:30:00Z",
                   participation_observation_id=fingerprint("positive offensive snaps"))
        value = resolve(r, s, [leg], games)["proposals"][0]["resolution"]
        self.assertEqual(value["resolved_utc"], leg["participation_observed_utc"])
        for change in ({"participation_observation_id": None}, {"actual": 10}, {"participation_basis": "unproved"}):
            self.assertEqual(resolve(r, s, [{**leg, **change}], games)["proposals"], [], change)
        unsupported = {key: value for key, value in leg.items() if not key.startswith("participation_")}
        unsupported["participation_basis"] = "r106_positive_offensive_snaps_no_stat_row"
        self.assertEqual(resolve(r, s, [unsupported], games)["proposals"], [])

    def test_final_observation_order_unknown_clocks_and_frozen_identity_are_strict(self):
        r, s, leg, games = evidence()
        bad_legs = ({"observed_utc": "2026-09-02T04:14:00Z"}, {"observed_utc": "2026-09-03T00:00:00Z"},
                    {"observation_id": None}, {"team": "NYJ"}, {"opp": "NYJ"}, {"week": 5},
                    {"source_updated_utc": leg["observed_utc"]}, {"source_revision_id": "claimed-official"},
                    {"participation_source": SNAP_SOURCE},
                    {"participation_source": SNAP_SOURCE, "participation_observation_id": fingerprint("snaps"),
                     "participation_observed_utc": "2026-09-02T04:14:00Z"})
        for change in bad_legs:
            self.assertEqual(resolve(r, s, [{**leg, **change}], games)["proposals"], [], change)
        for change in ({"status": "LIVE"}, {"home_score": 24.0}, {"home_score": None}, {"week": 5},
                       {"kickoff_utc": "2026-09-02T01:00:00Z"}, {"observation_id": None},
                       {"final_utc": "2026-09-02T04:00:00Z"}, {"source": "unverified"},
                       {"observed_utc": "2026-09-01T20:00:00Z"}):
            altered = {"g1": {**games["g1"], **change}}
            self.assertEqual(resolve(r, s, [leg], altered)["proposals"], [], change)

    def test_all_exact_persisted_vintages_are_resolved_even_owner_excluded(self):
        a, sa, la, games = evidence(probability=.8)
        b, sb, lb, _ = evidence(probability=.3)
        lb.update(excluded=True, eligibility="out", owner_hidden=True)
        batch = resolve_observed([a, b], [sa, sb], [lb, la], games, observed_utc=PROCESSING)
        self.assertEqual(batch["rejections"], [])
        self.assertEqual({p["resolution"]["receipt_id"] for p in batch["proposals"]}, {a["receipt_id"], b["receipt_id"]})
        self.assertNotEqual(a["served_prob"], b["served_prob"])

    def test_conflicting_underlying_actual_across_rungs_quarantines_every_contract(self):
        a, sa, la, games = evidence(line=59.5)
        b, sb, lb, _ = evidence(line=79.5)
        lb.update(actual=30, result="miss")
        for order in ([la, lb], [lb, la]):
            result = resolve_observed([a, b], [sa, sb], order, games, observed_utc=PROCESSING)
            self.assertEqual(result["proposals"], [])
            self.assertIn("conflicting_underlying_stat_observations", result["summary"]["exclusion_reasons"])
        for malformed in ({"actual": None}, {"played": False}, {"team": "wrong team"}):
            result = resolve_observed([a, b], [sa, sb], [la, {**lb, **malformed}], games, observed_utc=PROCESSING)
            self.assertEqual(result["proposals"], [], malformed)

    def test_duplicate_rows_are_idempotent_but_contradictory_copies_are_quarantined(self):
        r, s, leg, games = evidence()
        one = resolve(r, s, [leg], games)
        self.assertEqual(resolve(r, s, [leg, copy.deepcopy(leg)], games), one)
        other = {**leg, "actual": 30, "result": "miss"}
        self.assertEqual(resolve(r, s, [leg, other], games)["proposals"], [])

    def test_mixed_clock_history_mutated_nested_outcome_or_missing_parent_fail_closed(self):
        r, s, leg, games = evidence()
        first = resolve(r, s, [leg], games)["proposals"][0]
        current = corrected(leg, 30, "new stats", "2026-09-02T06:00:00Z")
        legacy = copy.deepcopy(first)
        legacy["resolution"].pop("evidence_basis")
        legacy["resolution"]["source_updated_utc"] = legacy["resolution"]["resolved_utc"]
        self.assertIn("incompatible_existing_clock_policy", resolve(r, s, [current], games, [legacy])["summary"]["exclusion_reasons"])
        changed = copy.deepcopy(first)
        changed["resolution"]["hit"] = False
        self.assertEqual(resolve(r, s, [current], games, [changed])["proposals"], [])
        changed = copy.deepcopy(first)
        changed["supersedes"] = "missing"
        self.assertEqual(resolve(r, s, [current], games, [changed])["proposals"], [])
        with self.assertRaisesRegex(ValueError, "malformed_existing_revision_history"):
            resolve(r, s, [current], games, [None])

    def test_trace_is_preserved_without_becoming_a_measurement_feature(self):
        r, s, leg, games = evidence()
        leg.update(evidence_capture_id=fingerprint("capture"), pool_snapshot_id=fingerprint("pool"),
                   generation_ids=[fingerprint("generation")], identity_join_policy="exact_frozen_pool_v1")
        value = resolve(r, s, [leg], games)["proposals"][0]["resolution"]
        self.assertEqual(value["producer_trace"]["identity_join_policy"], "exact_frozen_pool_v1")
        self.assertNotIn("producer_trace", value["measurement"])
        self.assertEqual(value["evidence_basis"], BRIDGE_VERSION)

    def test_existing_watermarks_cannot_drop_or_rewind_a_prior_component(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        first = state.send(leg)["proposals"][0]
        dnp = {**leg, "result": "void", "actual": None, "played": False, "reason": "did_not_play",
               "source": SNAP_SOURCE, "participation_basis": DNP_BASIS,
               "observed_utc": "2026-09-02T06:00:00Z", "observation_id": fingerprint("new snaps")}
        second = state.send(dnp)["proposals"][0]
        current = corrected(leg, 30, "next stats", "2026-09-02T07:00:00Z")
        for action in ("drop", "rewind"):
            mutated = copy.deepcopy(second)
            watermarks = mutated["resolution"]["observation_watermarks"]
            if action == "drop":
                watermarks.pop(STAT_SOURCE)
            else:
                watermarks[STAT_SOURCE]["observed_utc"] = "2026-09-02T04:16:00Z"
            batch = resolve(r, s, [current], games, [first, mutated])
            self.assertEqual(batch["proposals"], [], action)
            self.assertIn("existing_source_observation_watermark_union_mismatch", batch["summary"]["exclusion_reasons"])

    def test_corrupt_persisted_evidence_metadata_is_not_replayed(self):
        r, s, leg, games = evidence()
        first = resolve(r, s, [leg], games)["proposals"][0]
        current = corrected(leg, 30, "next stats", "2026-09-02T07:00:00Z")
        mutations = (
            (lambda v: v.update(source_revision_id="not-an-official-revision"), "mixed_existing_clock_policy"),
            (lambda v: v.update(measurement_id=fingerprint("wrong")), "existing_measurement_hash_mismatch"),
            (lambda v: v.update(source_observations={}), "missing_existing_observation_components"),
            (lambda v: v.update(evidence_observation_id=fingerprint("wrong")), "existing_observation_hash_mismatch"),
            (lambda v: v["observation_watermarks"].update({"untrusted": {}}), "invalid_existing_observation_watermark"),
            (lambda v: v["observation_watermarks"][STAT_SOURCE].update(source="untrusted"), "invalid_existing_observation_watermark"),
            (lambda v: v["observation_watermarks"].pop(STAT_SOURCE), "missing_existing_observation_watermark"),
            (lambda v: v.update(source="untrusted"), "missing_existing_required_observation"),
            (lambda v: v.update(resolved_utc="2026-09-02T04:25:00Z", evidence_observed_utc="2026-09-02T04:25:00Z"),
             "existing_observation_clock_mismatch"),
        )
        for mutate, reason in mutations:
            changed = copy.deepcopy(first)
            mutate(changed["resolution"])
            result = resolve(r, s, [current], games, [changed])
            self.assertEqual(result["proposals"], [], reason)
            self.assertIn(reason, result["summary"]["exclusion_reasons"])
        changed = copy.deepcopy(first)
        changed["revision_id"] = "wrong-observation-revision-id"
        result = resolve(r, s, [current], games, [changed])
        self.assertIn("existing_observation_revision_id_mismatch", result["summary"]["exclusion_reasons"])

    def test_malformed_wrappers_missing_originals_and_duplicate_identity_fail_honestly(self):
        r, s, leg, games = evidence()
        for receipts, sources, legs, expected in (
            ([{**r, "served_prob": True}], [s], [leg], "invalid_served_prob"),
            ([r], [None], [leg], "invalid_source_receipt"),
            ([r], [s], [None], "invalid_observed_leg"),
            ([], [s], [leg], "missing_persisted_or_original_source_receipt"),
            ([r], [], [leg], "missing_persisted_or_original_source_receipt"),
            ([r], [s, {**s, "extra": "conflicting"}], [leg], "conflicting_source_or_persisted_receipts"),
            ([r], [s], [{**leg, "receipt_id": "wrong"}], "conflicting_score_receipt_ids"),
        ):
            batch = resolve_observed(receipts, sources, legs, games, observed_utc=PROCESSING)
            self.assertEqual(batch["proposals"], [], expected)
            self.assertIn(expected, batch["summary"]["exclusion_reasons"])
        payload = {k: copy.deepcopy(v) for k, v in r.items() if k != "receipt_id"}
        payload["features"]["extra"] = 1
        source = copy.deepcopy(s)
        source["features"]["extra"] = True
        source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
        payload["source_receipt_id"] = source["receipt_id"]
        altered = seal_receipt(payload)
        result = resolve(altered, source, [{**leg, "source_receipt_id": source["receipt_id"]}], games)
        self.assertIn("persisted_source_features_mismatch", result["summary"]["exclusion_reasons"])

    def test_late_sorted_invalid_vintage_quarantines_an_already_staged_valid_copy(self):
        a, sa, la, games = evidence(probability=.8)
        b, sb, lb, _ = evidence(probability=.3)
        bad_r, bad_s, bad_l = max(((a, sa, la), (b, sb, lb)), key=lambda item: item[0]["source_receipt_id"])
        legacy = resolve(bad_r, bad_s, [bad_l], games)["proposals"][0]
        legacy["resolution"].pop("evidence_basis")
        result = resolve_observed([a, b], [sa, sb], [la, lb], games,
                                  observed_utc=PROCESSING, existing_revisions=[legacy])
        self.assertEqual(result["proposals"], [])
        self.assertIn("quarantined_contract_evidence", result["summary"]["exclusion_reasons"])
