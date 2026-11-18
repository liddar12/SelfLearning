"""The roster component is retained as evidence without upgrading old grades."""

import copy
import hashlib
import io
import unittest

from selflearn_core import evidence_chunks
from selflearn_core.adapters import nfl_capture_application_contracts as contracts
from selflearn_core.adapters import nfl_capture_delivery_journal as delivery
from selflearn_core.adapters.nfl_observed_resolution import EVENT_BOUND_JOIN, ROSTER_SOURCE
from selflearn_core.probability_receipts import fingerprint
from test_nfl_capture_evidence import ROSTER, fixture
from test_nfl_observed_resolution import Delivery, corrected, evidence, resolve


def event_bound(leg):
    return {**leg, "identity_join_policy": EVENT_BOUND_JOIN,
            "identity_crosswalk": {"source": ROSTER_SOURCE, "observation_id": fingerprint("roster"),
                                   "observed_utc": "2026-09-02T05:00:00Z", "raw_sha256": fingerprint("roster CSV"),
                                   "player_id": "espn-123", "gsis_id": "00-0080001", "pfr_id": "FictRe00",
                                   "scope": "postgame_identity_only"}}


class NFLRosterTransportTests(unittest.TestCase):
    def test_full_season_fourth_profile_survives_delivery_request_roundtrip(self):
        envelope, objects = fixture(weeks=tuple(range(1, 19)), roster=ROSTER)
        pointers = [evidence_chunks.chunk_stream(io.BytesIO(raw), lambda _: None)
                    for raw in objects.values()]
        self.assertEqual(len(pointers), 21)
        request = delivery.make_capture_delivery_request(
            envelope, pointers, destination_id=fingerprint("destination"), producer_id="nfl2026",
        )
        self.assertEqual(delivery._request(request), request)
        roster_id = envelope["manifest"]["sources"][-1]["observation_id"]
        self.assertEqual(request["envelope"]["observations"][roster_id]["raw_sha256"],
                         "sha256:" + hashlib.sha256(ROSTER).hexdigest())
        with self.assertRaisesRegex(ValueError, "pointer_envelope_mismatch"):
            delivery.make_capture_delivery_request(
                envelope, pointers[:-1], destination_id=fingerprint("destination"), producer_id="nfl2026",
            )

    def test_revision_and_observation_retain_roster_provenance_and_clock(self):
        r, s, leg, games = evidence()
        strict = event_bound(leg)
        state = Delivery(r, s, games)
        initial = state.send(strict)
        self.assertEqual(initial["rejections"], [])
        revision = initial["proposals"][0]
        observed = initial["observation_proposals"][0]
        value = revision["resolution"]
        self.assertEqual(value["resolved_utc"], strict["identity_crosswalk"]["observed_utc"])
        self.assertEqual(observed["observed_utc"], value["resolved_utc"])
        self.assertEqual(observed["source_observations"], value["observation_watermarks"])
        component = observed["source_observations"][ROSTER_SOURCE]
        self.assertEqual(component["observation_id"], strict["identity_crosswalk"]["observation_id"])
        self.assertEqual(value["producer_trace"]["identity_crosswalk"], strict["identity_crosswalk"])
        self.assertEqual(state.send(strict)["proposals"], [revision])

    def test_new_roster_observation_does_not_requalify_an_unchanged_legacy_revision(self):
        r, s, leg, games = evidence()
        state = Delivery(r, s, games)
        legacy = state.send(leg)["proposals"][0]
        with_roster = state.send(event_bound(leg))
        self.assertEqual(with_roster["proposals"], [legacy])
        self.assertNotIn(ROSTER_SOURCE, legacy["resolution"]["source_observations"])
        self.assertIn(ROSTER_SOURCE, with_roster["observation_proposals"][0]["source_observations"])
        later = event_bound(corrected(leg, 42, "later stat", "2026-09-02T06:00:00Z"))
        later["identity_crosswalk"].update(observation_id=fingerprint("later roster"),
                                           observed_utc="2026-09-02T06:30:00Z")
        changed = state.send(later)
        self.assertEqual(changed["rejections"], [])
        self.assertEqual(changed["proposals"][0]["supersedes"], legacy["revision_id"])

    def test_event_bound_join_requires_receipt_identity_and_postgame_crosswalk(self):
        r, s, leg, games = evidence()
        strict = event_bound(leg)
        for key, value, error in (
                ("player_id", "espn-999", "invalid_observed_identity_crosswalk"),
                ("raw_sha256", "not a digest", "invalid_observed_identity_crosswalk"),
                ("scope", "pregame", "invalid_observed_identity_crosswalk"),
                ("observed_utc", "2026-09-02T04:14:00Z", "identity_crosswalk_observed_before_final_evidence"),
                ("observed_utc", "2026-09-02T11:00:00Z", "source_after_observation")):
            candidate = copy.deepcopy(strict)
            candidate["identity_crosswalk"][key] = value
            result = resolve(r, s, [candidate], games)
            with self.subTest(key=key, value=value):
                self.assertEqual(result["proposals"], [])
                self.assertEqual(result["rejections"][0]["reason"], error)
        absent = {key: value for key, value in strict.items() if key != "identity_crosswalk"}
        self.assertEqual(resolve(r, s, [absent], games)["proposals"], [])

    def test_roster_bearing_seal_rejects_unqualified_fact(self):
        envelope, _ = fixture(roster=ROSTER)
        profiles = {"espn_scoreboard_v1": "espn.scoreboard", "nflverse_stats_player_v1": "nflverse.stats_player",
                    "nflverse_rosters_v1": ROSTER_SOURCE}
        sources = {}
        for ref in envelope["manifest"]["sources"]:
            name = profiles.get(ref["profile"])
            if name:
                oid = ref["observation_id"]
                sources[name] = {"source": name, "observation_id": oid,
                                 "observed_utc": envelope["observations"][oid]["observed_utc"]}
        fact = {"week": 6, "actual": 80, "played": True, "status": "FINAL", "reason": None,
                "participation_basis": "stat_row", "source_observations": sources,
                "game": {"season": 2026, "week": 6, "game_id": "g6", "kickoff_utc": "2026-10-18T20:25:00Z",
                         "home": "SEA", "away": "LAR", "state": "FINAL"}}
        scope, seal = [2026, "g6", "espn-101", "wr_rec_yds", "home"], {"weeks": [6]}
        request = {"capture_request": {"envelope": envelope}}
        contracts._fact(fact, scope, seal, request)
        del sources[ROSTER_SOURCE]
        with self.assertRaisesRegex(ValueError, "crosswalk_membership_mismatch"):
            contracts._fact(fact, scope, seal, request)


if __name__ == "__main__":
    unittest.main()
