"""Capture labels require evidence from the original receipt's fixture."""

import unittest

from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from test_nfl_capture_evidence import csv_audit, encoded, event, score_audit
from test_nfl_capture_labels import (
    BINDING_ROSTER, BINDING_SNAPS, BINDING_STATS, FIXTURE_NFLVERSE_GAME, RECEIVER_APP_ID,
    RECEIVER_GSIS_ID, bind, binding_capture, journal_fixture, player_ids, roster_row,
    snap_row, stat_row, unrelated_stats,
)


EXACT_GAME = FIXTURE_NFLVERSE_GAME
OTHER_GAME = "2026_06_MIN_SEA"
SCOPE = [2026, "g6", RECEIVER_APP_ID, "wr_rec_yds", "home"]
OBSERVED_UTC = "2026-10-21T00:00:00Z"


def stats_rows(game=EXACT_GAME, opponent="LAR", value="42"):
    header, _, *others = BINDING_STATS.splitlines(keepends=True)
    return header + stat_row("Fixture Receiver", value, player_index=0, game=game, opponent=opponent) + b"".join(others)


def snap_rows(game=EXACT_GAME, opponent="LAR", *, player_present=True):
    header, _, away = BINDING_SNAPS.splitlines(keepends=True)
    name, index = ("Fixture Receiver", 0) if player_present else ("Another Player", 5)
    return header + snap_row(name, "40", player_index=index, game=game, opponent=opponent) + away


def bind_and_resolve(**capture_kwargs):
    journal = journal_fixture(vintages=1, lines=(39.5,))
    envelope, objects = binding_capture(**capture_kwargs)
    staged = bind(journal, envelope, objects)
    original = journal.snapshot["receipts"][0]
    resolved = resolve_observed(
        journal.snapshot["receipts"], staged["source_receipts"],
        staged["observed_legs"], staged["game_observations"], observed_utc=OBSERVED_UTC,
    )
    return original, staged, resolved


class CaptureEventIdentityTests(unittest.TestCase):
    def assert_membership(self, original, staged):
        self.assertEqual(len(staged["groups"]), 1)
        group = staged["groups"][0]
        self.assertEqual(group["scope"], SCOPE)
        self.assertEqual(group["receipt_ids"], [original["receipt_id"]])
        self.assertEqual(group["source_receipt_ids"], [original["source_receipt_id"]])
        self.assertEqual([row["receipt_id"] for row in staged["source_receipts"]],
                         [original["source_receipt_id"]])
        return group

    def assert_withheld(self, **capture_kwargs):
        original, staged, resolved = bind_and_resolve(**capture_kwargs)
        group = self.assert_membership(original, staged)
        outcomes = [row["resolution"] for row in resolved["proposals"]]
        labels = [(row["status"], row["actual"], row["hit"], row["authoritative"]) for row in outcomes]
        self.assertEqual(labels, [], "Unbound evidence became an outcome proposal")
        self.assertEqual(staged["observed_legs"], [])
        self.assertEqual(staged["status"], "withheld")
        self.assertEqual(group["status"], "withheld")
        self.assertTrue(group["reason_codes"])
        self.assertEqual({row["source_receipt_id"] for row in staged["rejections"]},
                         {original["source_receipt_id"]})

    def test_matching_fixture_stat_creates_authoritative_receipt_hit(self):
        original, staged, resolved = bind_and_resolve(stats=stats_rows(), snaps=snap_rows())
        group = self.assert_membership(original, staged)
        self.assertEqual(group["status"], "staged")
        self.assertEqual(staged["rejections"], [])
        self.assertEqual(len(staged["observed_legs"]), 1)
        leg = staged["observed_legs"][0]
        self.assertEqual((leg["game_id"], leg["result"], leg["actual"], leg["participation_basis"]),
                         ("g6", "hit", 42.0, "stat_row"))
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual(len(resolved["proposals"]), 1)
        outcome = resolved["proposals"][0]["resolution"]
        self.assertEqual((outcome["receipt_id"], outcome["status"], outcome["actual"]),
                         (original["receipt_id"], "FINAL", 42.0))
        self.assertIs(outcome["authoritative"], True)
        self.assertIs(outcome["hit"], True)
        self.assertEqual(outcome["measurement"]["game"]["game_id"], "g6")

    def test_wrong_fixture_stat_cannot_create_250_yard_hit(self):
        self.assert_withheld(stats=stats_rows(OTHER_GAME, "MIN", "250"), snaps=snap_rows())

    def test_matching_fixture_snap_sheet_can_settle_without_a_stat_row(self):
        stats = unrelated_stats(stats_rows())
        cases = ((True, "FINAL", 0.0, False), (False, "VOID", None, None))
        for player_present, status, actual, hit in cases:
            with self.subTest(player_present=player_present):
                original, staged, resolved = bind_and_resolve(
                    stats=stats, snaps=snap_rows(player_present=player_present))
                group = self.assert_membership(original, staged)
                self.assertEqual(group["status"], "staged")
                self.assertEqual(resolved["rejections"], [])
                self.assertEqual(len(resolved["proposals"]), 1)
                outcome = resolved["proposals"][0]["resolution"]
                self.assertEqual((outcome["status"], outcome["actual"], outcome["hit"]),
                                 (status, actual, hit))
                self.assertIs(outcome["authoritative"], True)

    def test_wrong_fixture_snaps_cannot_create_zero_yard_miss_or_void(self):
        stats = unrelated_stats(stats_rows())
        for player_present in (True, False):
            with self.subTest(player_present=player_present):
                self.assert_withheld(stats=stats, snaps=snap_rows(
                    OTHER_GAME, "MIN", player_present=player_present))

    def test_stat_event_fields_must_agree_with_fixture_and_own_row(self):
        cases = {
            "missing_game": ("", "LAR"),
            "missing_opponent": (EXACT_GAME, ""),
            "conflicting_opponent": (EXACT_GAME, "MIN"),
            "reversed_venue": ("2026_06_SEA_LAR", "LAR"),
            "conflicting_week": ("2026_07_LAR_SEA", "LAR"),
            "conflicting_season": ("2025_06_LAR_SEA", "LAR"),
            "missing_season": ("_06_LAR_SEA", "LAR"),
        }
        for name, (game, opponent) in cases.items():
            with self.subTest(name=name):
                self.assert_withheld(stats=stats_rows(game, opponent), snaps=snap_rows())

    def test_snap_sheet_event_fields_must_agree_before_proving_absence(self):
        stats = unrelated_stats(stats_rows())
        cases = {
            "missing_game": ("", "LAR"),
            "missing_opponent": (EXACT_GAME, ""),
            "conflicting_opponent": (EXACT_GAME, "MIN"),
            "conflicting_season": ("2025_06_LAR_SEA", "LAR"),
        }
        for name, (game, opponent) in cases.items():
            with self.subTest(name=name):
                self.assert_withheld(stats=stats, snaps=snap_rows(
                    game, opponent, player_present=False))

    def test_conflicting_rows_for_one_player_do_not_create_a_label(self):
        wrong = stats_rows(OTHER_GAME, "MIN", "250").splitlines()[1]
        unidentified = stat_row("Fixture Receiver", "250", player_index=0).rstrip(b"\n").replace(
            RECEIVER_GSIS_ID.encode(), b"")
        for name, conflicting in (("other_fixture", wrong), ("missing_provider_id", unidentified)):
            with self.subTest(name=name):
                self.assert_withheld(stats=stats_rows() + conflicting + b"\n",
                                     stats_audit=csv_audit(4), snaps=snap_rows())

    def test_same_name_with_different_raw_id_cannot_satisfy_receipt(self):
        stats = stats_rows(value="250").replace(RECEIVER_GSIS_ID.encode(), player_ids(1)[1].encode())
        header, _, away = snap_rows().splitlines(keepends=True)
        self.assert_withheld(stats=stats, snaps=header + away, snaps_audit=csv_audit(1))

    def test_separate_namesake_id_does_not_override_the_bound_stat(self):
        namesake = stat_row("Fixture Receiver", "250", player_index=1, team="LAR", opponent="SEA")
        original, staged, resolved = bind_and_resolve(
            stats=stats_rows() + namesake, stats_audit=csv_audit(4), snaps=snap_rows())
        self.assertEqual(self.assert_membership(original, staged)["status"], "staged")
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual([row["resolution"]["actual"] for row in resolved["proposals"]], [42.0])

    def test_conflicting_or_missing_roster_crosswalk_withholds(self):
        _, other_gsis, other_pfr = player_ids(7)
        conflict = BINDING_ROSTER + roster_row(0, gsis_id=other_gsis, pfr_id=other_pfr)
        for roster in (None, conflict):
            with self.subTest(roster_present=roster is not None):
                self.assert_withheld(stats=stats_rows(), snaps=snap_rows(), roster=roster)

    def test_stats_and_snaps_require_a_final_for_the_receipt_game(self):
        cases = (
            (event(status="STATUS_IN_PROGRESS"), score_audit(final_games=0, nonfinal_games=1)),
            (event(gid="different-game"), score_audit()),
        )
        for score_event, audit in cases:
            with self.subTest(game=score_event["id"], status=score_event["status"]["type"]["name"]):
                self.assert_withheld(
                    stats=stats_rows(), snaps=snap_rows(),
                    score_raw={6: encoded({"events": [score_event]})}, score_audits={6: audit},
                )
