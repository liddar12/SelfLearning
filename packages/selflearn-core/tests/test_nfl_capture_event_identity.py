"""Capture labels require evidence from the original receipt's fixture."""

import unittest

from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from test_nfl_capture_evidence import SNAPS, STATS, csv_audit, encoded, event, fixture, score_audit
from test_nfl_capture_labels import bind, journal_fixture


EXACT_GAME = b"2026_06_LAR_SEA"
OTHER_GAME = b"2026_06_MIN_SEA"
SCOPE = [2026, "g6", "espn-fixture-1", "wr_rec_yds", "home"]
OBSERVED_UTC = "2026-10-21T00:00:00Z"


def stats_rows(game=EXACT_GAME, opponent=b"LAR", value=b"42"):
    header, first, *others = STATS.replace(b",,,NA", b",,," + value).splitlines()
    return b"\n".join([
        header + b",game_id,opponent_team",
        first + b"," + game + b"," + opponent,
        *(row + b"," + EXACT_GAME + b",LAR" for row in others),
    ]) + b"\n"


def snap_rows(game=EXACT_GAME, opponent=b"LAR", *, player_present=True):
    header, home, away = SNAPS.splitlines()
    if not player_present:
        home = home.replace(b"Fixture Receiver", b"Another Player")
    return b"\n".join([
        header + b",game_id,opponent",
        home + b"," + game + b"," + opponent,
        away + b"," + EXACT_GAME + b",SEA",
    ]) + b"\n"


def bind_and_resolve(**capture_kwargs):
    journal = journal_fixture(vintages=1, lines=(39.5,))
    envelope, objects = fixture(**capture_kwargs)
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
        self.assert_withheld(stats=stats_rows(OTHER_GAME, b"MIN", b"250"), snaps=snap_rows())

    def test_matching_fixture_snap_sheet_can_settle_without_a_stat_row(self):
        stats = stats_rows().replace(b"Fixture Receiver", b"Different Receiver")
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
        stats = stats_rows().replace(b"Fixture Receiver", b"Different Receiver")
        for player_present in (True, False):
            with self.subTest(player_present=player_present):
                self.assert_withheld(stats=stats, snaps=snap_rows(
                    OTHER_GAME, b"MIN", player_present=player_present))

    def test_stat_event_fields_must_agree_with_fixture_and_own_row(self):
        cases = {
            "missing_game": (b"", b"LAR"),
            "missing_opponent": (EXACT_GAME, b""),
            "conflicting_opponent": (EXACT_GAME, b"MIN"),
            "reversed_venue": (b"2026_06_SEA_LAR", b"LAR"),
            "conflicting_week": (b"2026_07_LAR_SEA", b"LAR"),
            "conflicting_season": (b"2025_06_LAR_SEA", b"LAR"),
            "missing_season": (b"_06_LAR_SEA", b"LAR"),
        }
        for name, (game, opponent) in cases.items():
            with self.subTest(name=name):
                self.assert_withheld(stats=stats_rows(game, opponent), snaps=snap_rows())

    def test_snap_sheet_event_fields_must_agree_before_proving_absence(self):
        stats = stats_rows().replace(b"Fixture Receiver", b"Different Receiver")
        cases = {
            "missing_game": (b"", b"LAR"),
            "missing_opponent": (EXACT_GAME, b""),
            "conflicting_opponent": (EXACT_GAME, b"MIN"),
            "conflicting_season": (b"2025_06_LAR_SEA", b"LAR"),
        }
        for name, (game, opponent) in cases.items():
            with self.subTest(name=name):
                self.assert_withheld(stats=stats, snaps=snap_rows(
                    game, opponent, player_present=False))

    def test_conflicting_rows_for_one_player_do_not_create_a_label(self):
        wrong = stats_rows(OTHER_GAME, b"MIN", b"250").splitlines()[1]
        stats = stats_rows() + wrong + b"\n"
        self.assert_withheld(stats=stats, stats_audit=csv_audit(4), snaps=snap_rows())

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
