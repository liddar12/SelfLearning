"""Malformed stat and snap identities cannot prove that a player was absent."""

import pytest

from test_nfl_capture_evidence import csv_audit
from test_nfl_capture_event_identity import EXACT_GAME, bind_and_resolve, snap_rows, stats_rows
from test_nfl_capture_labels import RECEIVER_GSIS_ID, RECEIVER_PFR_ID, player_ids, stat_row, unrelated_stats


@pytest.mark.parametrize("player_present", [True, False], ids=["positive-snaps", "absent-from-snaps"])
@pytest.mark.parametrize("raw_id", [b"bad-id", b"00-malformed"])
def test_target_looking_stat_with_malformed_gsis_blocks_fallback(raw_id, player_present):
    stats = stats_rows().replace(RECEIVER_GSIS_ID.encode(), raw_id)
    receipt, staged, resolved = bind_and_resolve(stats=stats, snaps=snap_rows(player_present=player_present))
    assert staged["observed_legs"] == resolved["proposals"] == []
    assert staged["groups"][0]["status"] == "withheld"
    assert staged["groups"][0]["receipt_ids"] == [receipt["receipt_id"]]
    assert staged["groups"][0]["reason_codes"] == ["unidentified_stat_row_prevents_zero_inference"]


@pytest.mark.parametrize("player_present,expected", [(True, ("FINAL", 0.0, False)), (False, ("VOID", None, None))])
def test_same_name_stat_with_distinct_valid_gsis_does_not_block_fallback(player_present, expected):
    distinct_gsis = player_ids(5)[1].encode()
    stats = stats_rows().replace(RECEIVER_GSIS_ID.encode(), distinct_gsis)
    _, staged, resolved = bind_and_resolve(stats=stats, snaps=snap_rows(player_present=player_present))
    assert staged["rejections"] == resolved["rejections"] == []
    assert len(resolved["proposals"]) == 1
    value = resolved["proposals"][0]["resolution"]
    assert (value["status"], value["actual"], value["hit"]) == expected


@pytest.mark.parametrize("player_present", [True, False], ids=["positive-snaps", "absent-from-snaps"])
def test_missing_identity_sentinel_is_not_a_distinct_player_name(player_present):
    stats = stats_rows().replace(RECEIVER_GSIS_ID.encode(), b"").replace(b"Fixture Receiver", b"NaN")
    _, staged, resolved = bind_and_resolve(stats=stats, snaps=snap_rows(player_present=player_present))
    assert staged["observed_legs"] == resolved["proposals"] == []
    assert staged["groups"][0]["reason_codes"] == ["unidentified_stat_row_prevents_zero_inference"]


def test_unverified_namesake_pfr_withholds_absence_without_overriding_bound_stats():
    snaps = snap_rows().replace(RECEIVER_PFR_ID.encode(), b"bad-id")
    stats = stats_rows()
    _, staged, resolved = bind_and_resolve(stats=unrelated_stats(stats), snaps=snaps)
    assert staged["observed_legs"] == resolved["proposals"] == []
    assert staged["groups"][0]["reason_codes"] == ["unjoined_participation_identity_requires_crosswalk"]

    _, staged, resolved = bind_and_resolve(stats=stats, snaps=snaps)
    assert staged["rejections"] == resolved["rejections"] == []
    assert [row["resolution"]["actual"] for row in resolved["proposals"]] == [42.0]


def test_roster_proves_same_name_snap_with_distinct_pfr_is_another_player():
    distinct_pfr = player_ids(5)[2].encode()
    snaps = snap_rows().replace(RECEIVER_PFR_ID.encode(), distinct_pfr)
    _, staged, resolved = bind_and_resolve(stats=unrelated_stats(stats_rows()), snaps=snaps)
    assert staged["rejections"] == resolved["rejections"] == []
    assert [row["resolution"]["status"] for row in resolved["proposals"]] == ["VOID"]


def anonymous_stat_row(*, game="2026_06_IND_HOU", team="IND", opponent="HOU", name=""):
    # Week 11 nflverse produced this shape with blank names, position and GSIS.
    return stat_row(name, "0", player_index=0, team=team, opponent=opponent, game=game).replace(
        b",WR,", b",,").replace(RECEIVER_GSIS_ID.encode(), b"")


@pytest.mark.parametrize("name", ["", "Fixture Receiver"], ids=["anonymous", "recognizable"])
@pytest.mark.parametrize("player_present,expected", [(True, ("FINAL", 0.0, False)), (False, ("VOID", None, None))])
def test_unidentified_stat_from_another_confirmed_fixture_does_not_block_inference(name, player_present, expected):
    stats = unrelated_stats(stats_rows()) + anonymous_stat_row(name=name)
    _, staged, resolved = bind_and_resolve(
        stats=stats, stats_audit=csv_audit(len(stats.splitlines()) - 1),
        snaps=snap_rows(player_present=player_present),
    )
    assert staged["rejections"] == resolved["rejections"] == []
    assert len(resolved["proposals"]) == 1
    value = resolved["proposals"][0]["resolution"]
    assert (value["status"], value["actual"], value["hit"]) == expected


@pytest.mark.parametrize("game,team,opponent", [
    (EXACT_GAME, "SEA", "LAR"),
    ("2026_06_IND_HOU", "IND", "MIN"),
    ("", "SEA", "LAR"),
], ids=["same-fixture", "conflicting-team", "unknown-fixture"])
def test_unidentified_stat_is_withheld_without_proof_of_another_fixture(game, team, opponent):
    stats = unrelated_stats(stats_rows()) + anonymous_stat_row(game=game, team=team, opponent=opponent)
    _, staged, resolved = bind_and_resolve(
        stats=stats, stats_audit=csv_audit(len(stats.splitlines()) - 1), snaps=snap_rows(),
    )
    assert staged["observed_legs"] == resolved["proposals"] == []
    assert staged["groups"][0]["reason_codes"] == ["unidentified_stat_row_prevents_zero_inference"]
