"""Fixture conflicts must withhold the affected leg without rewriting its receipt."""

import copy
import unittest

from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from test_nfl_capture_evidence import csv_audit
from test_nfl_capture_event_identity import OBSERVED_UTC, bind_and_resolve, snap_rows, stats_rows
from test_nfl_capture_labels import bind, binding_capture, journal_fixture, player_ids, snap_row, stat_row, unrelated_stats


class CaptureEventConflictTests(unittest.TestCase):
    def assert_withheld(self, **capture_kwargs):
        original, staged, resolved = bind_and_resolve(**capture_kwargs)
        self.assertEqual(len(resolved["proposals"]), 0)
        self.assertEqual(staged["observed_legs"], [])
        self.assertEqual(staged["status"], "withheld")
        self.assertEqual(len(staged["groups"]), 1)
        group = staged["groups"][0]
        self.assertEqual(group["status"], "withheld")
        self.assertEqual(group["receipt_ids"], [original["receipt_id"]])
        self.assertEqual(group["source_receipt_ids"], [original["source_receipt_id"]])
        self.assertTrue(group["reason_codes"])

    def test_duplicate_target_pfr_rows_withhold_even_with_a_bound_stat(self):
        for second_count, duplicate_rows in (("40", 1), ("41", 0)):
            with self.subTest(second_count=second_count):
                self.assert_withheld(
                    stats=stats_rows(),
                    snaps=snap_rows() + snap_row("Fixture Receiver", second_count, player_index=0),
                    snaps_audit=csv_audit(3, duplicate_rows),
                )

    def test_bound_stat_and_target_snaps_on_opposite_sides_withhold(self):
        away_target = snap_row("Fixture Receiver", "40", player_index=0, team="LAR", opponent="SEA")
        self.assert_withheld(stats=stats_rows(), snaps=snap_rows(player_present=False) + away_target,
                             snaps_audit=csv_audit(3))

    def test_target_can_switch_sides_in_original_fixture_without_rewriting_receipt(self):
        header, _, *other_stats = stats_rows().splitlines(keepends=True)
        stats = header + stat_row("Fixture Receiver", "42", player_index=0, team="LAR", opponent="SEA")
        stats += b"".join(other_stats)
        snaps = snap_rows(player_present=False) + snap_row(
            "Fixture Receiver", "40", player_index=0, team="LAR", opponent="SEA")
        journal = journal_fixture(vintages=1, lines=(39.5,))
        before = copy.deepcopy((journal.snapshot, journal.objects))
        capture, objects = binding_capture(stats=stats, snaps=snaps, snaps_audit=csv_audit(3))
        staged = bind(journal, capture, objects)
        resolved = resolve_observed(
            journal.snapshot["receipts"], staged["source_receipts"], staged["observed_legs"],
            staged["game_observations"], observed_utc=OBSERVED_UTC,
        )
        self.assertEqual((journal.snapshot, journal.objects), before)
        self.assertEqual(staged["rejections"], [])
        self.assertEqual(len(staged["observed_legs"]), 1)
        leg = staged["observed_legs"][0]
        self.assertEqual((leg["game_id"], leg["team"], leg["side"], leg["actual"]),
                         ("g6", "SEA", "home", 42.0))
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual(len(resolved["proposals"]), 1)
        result = resolved["proposals"][0]["resolution"]
        self.assertEqual(result["receipt_id"], journal.snapshot["receipts"][0]["receipt_id"])
        self.assertIs(result["hit"], True)
        self.assertEqual(result["measurement"]["game"]["game_id"], "g6")

    def test_unrelated_bad_snap_row_does_not_poison_bound_positive_stat(self):
        stats, snaps = stats_rows(), snap_rows().replace(b"Other Receiver,52", b"Other Receiver,NA")
        _, staged, resolved = bind_and_resolve(stats=stats, snaps=snaps)
        self.assertEqual(staged["rejections"], [])
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual([row["resolution"]["actual"] for row in resolved["proposals"]], [42.0])

    def test_other_fixture_stat_release_cannot_prove_zero_in_receipt_fixture(self):
        stats = unrelated_stats(stats_rows()).replace(b"2026_06_LAR_SEA,LAR", b"2026_06_MIN_SEA,MIN")
        self.assert_withheld(stats=stats, snaps=snap_rows())

    def test_other_team_or_invalid_stat_rows_cannot_prove_publication_for_target_team(self):
        header = stats_rows().splitlines(keepends=True)[0]
        away = [
            stat_row("Away One", "10", player_index=1, team="LAR", opponent="SEA"),
            stat_row("Away Two", "20", player_index=2, team="LAR", opponent="SEA"),
        ]
        cases = {
            "away_only": stat_row("Away Three", "30", player_index=3, team="LAR", opponent="SEA"),
            "missing_measurement": stat_row("Home Other", "NA", player_index=3),
            "unsupported_position": stat_row("Home Other", "30", player_index=3).replace(b",WR,", b",K,"),
            "invalid_provider_id": stat_row("Home Other", "30", player_index=3).replace(
                player_ids(3)[1].encode(), b"not-a-gsis-id"),
        }
        for name, last in cases.items():
            with self.subTest(name=name):
                self.assert_withheld(stats=header + b"".join(away) + last, snaps=snap_rows())

    def test_other_player_stats_from_target_team_allow_coherent_snap_fallback(self):
        stats = unrelated_stats(stats_rows())
        for player_present, expected in ((True, ("FINAL", 0.0, False)), (False, ("VOID", None, None))):
            with self.subTest(player_present=player_present):
                _, staged, resolved = bind_and_resolve(
                    stats=stats, snaps=snap_rows(player_present=player_present))
                self.assertEqual(staged["rejections"], [])
                self.assertEqual(resolved["rejections"], [])
                self.assertEqual(len(resolved["proposals"]), 1)
                result = resolved["proposals"][0]["resolution"]
                self.assertEqual((result["status"], result["actual"], result["hit"]), expected)

    def test_target_row_conflicting_week_fields_do_not_disappear_before_fallback(self):
        stats = stats_rows().replace(b"2026,6,REG,WR,SEA,Fixture Receiver,",
                                     b"2026,7,REG,WR,SEA,Fixture Receiver,")
        target = snap_row("Fixture Receiver", "40", player_index=0).replace(b"2026,6,", b"2026,7,", 1)
        for padded in (False, True):
            source_key = b", 2026_06_LAR_SEA," if padded else b",2026_06_LAR_SEA,"
            with self.subTest(source="stat", padded=padded):
                self.assert_withheld(stats=stats.replace(b",2026_06_LAR_SEA,", source_key), snaps=snap_rows())
            with self.subTest(source="snap", padded=padded):
                self.assert_withheld(stats=unrelated_stats(stats_rows()),
                                     snaps=snap_rows(player_present=False) + target.replace(
                                         b",2026_06_LAR_SEA,", source_key),
                                     snaps_audit=csv_audit(3))

    def test_target_snap_row_cannot_claim_postseason_in_regular_fixture(self):
        target = snap_row("Fixture Receiver", "40", player_index=0).replace(b",REG,", b",POST,")
        self.assert_withheld(stats=unrelated_stats(stats_rows()),
                             snaps=snap_rows(player_present=False) + target, snaps_audit=csv_audit(3))

    def test_coherent_prior_week_target_data_does_not_poison_this_fixture(self):
        previous_game = "2026_05_MIN_SEA"
        previous_stat = stat_row("Fixture Receiver", "250", player_index=0, opponent="MIN",
                                 game=previous_game).replace(b"2026,6,", b"2026,5,", 1)
        previous_snap = snap_row("Fixture Receiver", "40", player_index=0, opponent="MIN",
                                 game=previous_game).replace(b"2026,6,", b"2026,5,", 1)
        _, staged, resolved = bind_and_resolve(
            stats=unrelated_stats(stats_rows()) + previous_stat, stats_audit=csv_audit(4),
            snaps=snap_rows() + previous_snap, snaps_audit=csv_audit(3),
        )
        self.assertEqual(staged["rejections"], [])
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual([row["resolution"]["actual"] for row in resolved["proposals"]], [0.0])
