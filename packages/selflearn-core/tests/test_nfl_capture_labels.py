"""Independent frozen journal fixtures and explicit real NFL binding parity."""
import base64
import copy
import csv
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest import mock

from selflearn_core.adapters import nfl_capture_labels as B
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
from selflearn_core.probability_receipts import fingerprint

import test_nfl_capture_evidence as E


def player_ids(index):
    return f"espn-{9_000_001 + index}", f"00-{90_001 + index:07d}", f"TestRx{index + 1:02d}"


RECEIVER_APP_ID, RECEIVER_GSIS_ID, RECEIVER_PFR_ID = player_ids(0)
OTHER_APP_ID, _, _ = player_ids(4)
FIXTURE_NFLVERSE_GAME = "2026_06_LAR_SEA"


def stat_row(name, yards, *, player_index, team="SEA", opponent="LAR", game=FIXTURE_NFLVERSE_GAME):
    _, gsis_id, _ = player_ids(player_index)
    return f"2026,6,REG,WR,{team},{name},,,{yards},{game},{opponent},{gsis_id}\n".encode()


def snap_row(name, snaps, *, player_index, team="SEA", opponent="LAR", game=FIXTURE_NFLVERSE_GAME):
    _, _, pfr_id = player_ids(player_index)
    return f"2026,6,REG,{team},{name},{snaps},{game},{opponent},{pfr_id}\n".encode()


def roster_row(index, *, gsis_id=None, pfr_id=None):
    app_id, source_gsis_id, source_pfr_id = player_ids(index)
    return f"2026,6,{app_id.removeprefix('espn-')},{gsis_id or source_gsis_id},{pfr_id or source_pfr_id}\n".encode()


BINDING_STATS = (
    E.STATS.splitlines()[0] + b",game_id,opponent_team,player_id\n"
    + stat_row("Fixture Receiver", "NA", player_index=0)
    + stat_row("Negative Receiver", "-7", player_index=2)
    + b"2026,6,REG,QB,SEA,Fixture Quarterback,0,,,2026_06_LAR_SEA,LAR,00-0090004\n"
)
BINDING_SNAPS = (
    E.SNAPS.splitlines()[0] + b",game_id,opponent,pfr_player_id\n"
    + snap_row("Fixture Receiver", "40", player_index=0)
    + snap_row("Other Receiver", "52", player_index=1, team="LAR", opponent="SEA")
)
BINDING_ROSTER = b"season,week,espn_id,gsis_id,pfr_id\n" + b"".join(roster_row(i) for i in range(8))


def unrelated_stats(stats=BINDING_STATS, *, name=b"Different Receiver"):
    return stats.replace(b"Fixture Receiver", name).replace(
        RECEIVER_GSIS_ID.encode(), player_ids(5)[1].encode())


def unrelated_snaps(snaps=BINDING_SNAPS, *, name=b"Different Receiver"):
    return snaps.replace(b"Fixture Receiver", name).replace(
        RECEIVER_PFR_ID.encode(), player_ids(5)[2].encode())


def binding_capture(*, stats=BINDING_STATS, snaps=BINDING_SNAPS, roster=BINDING_ROSTER,
                    roster_audit=None, **kwargs):
    if roster is not None and roster_audit is None:
        roster_audit = E.csv_audit(len(roster.splitlines()) - 1)
    return E.fixture(stats=stats, snaps=snaps, roster=roster, roster_audit=roster_audit, **kwargs)


class Journal:
    """Only the trusted complete journal protocol, no selected-ID read API."""
    def __init__(self, snapshot, objects):
        self.snapshot, self.objects = snapshot, objects
        self.reads, self.gets = 0, []

    def read_observed_snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.snapshot)

    def get_object(self, oid):
        self.gets.append(oid)
        return copy.deepcopy(self.objects.get(oid))


def journal_fixture(*, vintages=2, lines=(39.5, 59.5), player="Fixture Receiver",
                    player_id=RECEIVER_APP_ID, watermark="100"):
    documents, objects = [], {}
    for vintage in range(vintages):
        created, recorded = f"2026-10-17T{12 + vintage:02d}:00:00Z", f"2026-10-17T{12 + vintage:02d}:01:00Z"
        base = {"player": player, "position": "WR", "team": "SEA", "gsis_id": player_id,
                "game_id": "g6", "market": "wr_rec_yds", "side": "home", "direction": "over", "comparison_operator": "gte"}
        rungs, rows = [], []
        for line in lines:
            key = [2026, "g6", player_id, "wr_rec_yds", format(line, ".12g"), "home", "over", "gte"]
            source = {"version": 1, "contract_id": fingerprint(key), "season": 2026, "week": 6, "game_id": "g6",
                "player_id": player_id, "market": "wr_rec_yds", "line": line, "side": "home", "direction": "over", "comparison_operator": "gte",
                "base_prob": .5, "served_prob": .5, "base_model_version": "fixture-base", "calibrator_version": fingerprint(None), "calibrator": None,
                "forecast_created_utc": created, "source_snapshot_id": fingerprint([created]), "source_timestamps": {"fixture": created},
                "generation_sha": "synthetic-local", "publication_status": "generated_unverified",
                "features": {"market": "wr_rec_yds", "line": line, "mu": 80, "sd": 30, "z": (80 - line) / 30, "p_team": .6,
                             "calibration": {"a": 0, "b": 0, "c": 0}, "correlation_tags": ["game:g6"]}}
            source["receipt_id"] = fingerprint(source)
            rung = {"line": line, "model_prob": .5, "selection": f"Fixture {line}+ yards", "learning_receipt": source}
            rungs.append(rung)
            leg = {k: value for k, value in {**base, **rung}.items() if k in B._LEG_KEYS}
            rows.append({"receipt_id": source["receipt_id"], "leg": leg, "learning_receipt": source,
                         "kickoff_utc": "2026-10-18T20:25:00Z", "recorded_utc": recorded, "publication_status": "generated_unverified"})
        payloads = {"leg_pool": {"season": 2026, "week": 6, "generated_utc": created, "base_model_version": "fixture-base",
                                 "players": [{**base, "rungs": rungs}]},
                    "schedule": {"season": 2026, "games": [{"game_id": "g6", "week": 6, "home": "SEA", "away": "LAR",
                                  "status": "STATUS_SCHEDULED", "kickoff_utc": "2026-10-18T20:25:00Z"}]}, "parlay_calibration": None}
        refs = {}
        for name, payload in payloads.items():
            oid = fingerprint(payload)
            refs[name] = oid
            objects[oid] = {"object_id": oid, "kind": "source_snapshot", "payload": payload, "received_utc": "2026-10-20T11:00:00Z"}
        generation = {"version": 1, "season": 2026, "week": 6, "pool_generated_utc": created, "source_snapshots": refs,
                      "offered_cards": [], "selection_policy_version": fingerprint("selector"), "joint_model_version": fingerprint("joint")}
        generation["generation_id"] = fingerprint(generation)
        generation.update(recorded_utc=recorded, publication_status="generated_unverified",
                          population="eligible_pool_prop_rungs_before_display_dedupe", receipt_ids=sorted(row["receipt_id"] for row in rows))
        for row in rows: row["generation_id"] = generation["generation_id"]
        doc = {"version": 1, "season": 2026, "week": 6, "generations": [generation], "receipts": rows}
        doc["envelope_id"] = fingerprint(doc)
        documents.append(doc)
    adapted = adapt_prospective(journal_docs=documents)
    if adapted["rejections"]: raise AssertionError(adapted["rejections"])
    by_source = {receipt["source_receipt_id"]: receipt for receipt in adapted["receipts"]}
    records = []
    for doc in documents:
        oid = doc["envelope_id"]
        ack = {"version": 1, "object_id": oid, "durable": True, "receipts": [
            {"source_receipt_id": row["receipt_id"], "receipt_id": by_source[row["receipt_id"]]["receipt_id"], "status": "recorded"}
            for row in doc["receipts"]], "rejections": []}
        records.append({"object_id": oid, "payload": doc, "ack": ack, "recorded_utc": "2026-10-20T12:00:00Z"})
    snapshot = {"version": 1, "watermark": watermark, "receipts": adapted["receipts"], "generation_records": records,
                "revisions": [], "observations": [], "observation_records": []}
    return Journal(snapshot, objects)


def good_capture(**kwargs):
    return binding_capture(stats=BINDING_STATS.replace(b",,,NA", b",,,80"), **kwargs)


def bind(journal, envelope, objects):
    return B.bind_capture_labels(journal, envelope, lambda key: E.stream(objects[key]))


def raw_rows(raw):
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline="")))


def change_frozen_player(journal, record_index, **changes):
    """Reseal a different valid frozen context; original receipt IDs stay exact."""
    record = journal.snapshot["generation_records"][record_index]
    doc, generation = record["payload"], record["payload"]["generations"][0]
    old = generation["source_snapshots"]["leg_pool"]
    wrapper = copy.deepcopy(journal.objects[old])
    wrapper["payload"]["players"][0].update(changes)
    new = fingerprint(wrapper["payload"])
    wrapper["object_id"] = new
    journal.objects[new] = wrapper
    generation["source_snapshots"]["leg_pool"] = new
    generation["generation_id"] = fingerprint({key: generation[key] for key in B._MANIFEST_KEYS})
    for row in doc["receipts"]: row["generation_id"] = generation["generation_id"]
    doc["envelope_id"] = fingerprint({key: value for key, value in doc.items() if key != "envelope_id"})
    record["object_id"] = record["ack"]["object_id"] = doc["envelope_id"]


class CaptureLabelBindingTests(unittest.TestCase):
    def test_all_unoffered_rungs_and_original_vintages_bind_exact_raw_provenance(self):
        journal, (envelope, objects) = journal_fixture(), good_capture()
        originals = copy.deepcopy(journal.snapshot["receipts"])
        result = bind(journal, envelope, objects)
        self.assertEqual(journal.reads, 1)
        self.assertEqual(len(result["observed_legs"]), 4)
        self.assertEqual(len(result["source_receipts"]), 4)
        self.assertEqual(len(result["groups"]), 1)
        group = result["groups"][0]
        self.assertEqual(group["scope"], [2026, "g6", RECEIVER_APP_ID, "wr_rec_yds", "home"])
        self.assertEqual(group["receipt_ids"], sorted(row["receipt_id"] for row in originals))
        self.assertEqual(group["status"], "staged")
        self.assertEqual(result["membership_watermark"], "100")
        self.assertEqual(result["status"], "staged")
        self.assertFalse(result["durable_ack"])
        self.assertEqual(journal.snapshot["receipts"], originals)
        stat = next(o for o in envelope["observations"].values() if o["profile"] == E.C.STATS_PROFILE)
        roster_id, roster = next((oid, o) for oid, o in envelope["observations"].items()
                                 if o["profile"] == E.C.ROSTERS_PROFILE)
        expected_hash = fingerprint(raw_rows(objects[stat["raw_sha256"]])[0])
        for leg in result["observed_legs"]:
            self.assertEqual((leg["actual"], leg["played"], leg["result"]), (80, True, "hit"))
            self.assertEqual(leg["source_row_sha256"], expected_hash)
            self.assertEqual(leg["identity_join_policy"], "stable_player_ids_exact_fixture_v2")
            self.assertEqual(leg["identity_crosswalk"], {
                "source": "nflverse.rosters", "observation_id": roster_id,
                "observed_utc": roster["observed_utc"], "raw_sha256": roster["raw_sha256"],
                "player_id": RECEIVER_APP_ID, "gsis_id": RECEIVER_GSIS_ID, "pfr_id": RECEIVER_PFR_ID,
                "scope": "postgame_identity_only",
            })
        resolved = resolve_observed(originals, result["source_receipts"], result["observed_legs"], result["game_observations"],
                                     observed_utc="2026-10-21T00:00:00Z")
        self.assertEqual(resolved["rejections"], [])
        self.assertEqual(len(resolved["proposals"]), 4)
        for proposal in resolved["proposals"]:
            outcome = proposal["resolution"]
            self.assertEqual(outcome["producer_trace"]["identity_crosswalk"],
                             result["observed_legs"][0]["identity_crosswalk"])
            self.assertEqual(outcome["source_observations"]["nflverse.rosters"]["observation_id"], roster_id)

    def test_legacy_three_source_capture_is_valid_and_cannot_label(self):
        envelope, objects = E.fixture(stats=E.STATS.replace(b",,,NA", b",,,80"))
        self.assertEqual(len(E.verify(envelope, objects)["source_audits"]), 3)
        journal = journal_fixture()
        staged = bind(journal, envelope, objects)
        self.assertEqual(staged["status"], "withheld")
        self.assertEqual(staged["observed_legs"], [])
        self.assertEqual(staged["groups"][0]["receipt_ids"],
                         sorted(row["receipt_id"] for row in journal.snapshot["receipts"]))

    def test_measured_zero_negative_thresholds_and_missing_values_never_repair_to_zero(self):
        for value in ("0", "-7.5", "39.5", "59.5", "80", "NA", "", "NaN", "Infinity", "bad"):
            envelope, objects = binding_capture(stats=BINDING_STATS.replace(b",,,NA", (",,," + value).encode()))
            result = bind(journal_fixture(), envelope, objects)
            with self.subTest(value=value):
                if value in ("NA", "", "NaN", "Infinity", "bad"):
                    self.assertEqual(result["observed_legs"], [])
                    self.assertEqual(result["groups"][0]["reason_codes"], ["missing_stat_value"])
                else:
                    self.assertEqual([leg["actual"] for leg in result["observed_legs"]], [float(value)] * 4)

    def test_r106_positive_snaps_and_ordered_team_absence_digest_match_independent_oracle(self):
        stats = unrelated_stats()
        for absent in (False, True):
            snaps = unrelated_snaps() if absent else BINDING_SNAPS
            envelope, objects = binding_capture(stats=stats, snaps=snaps)
            result = bind(journal_fixture(), envelope, objects)
            snap_rows = raw_rows(snaps)
            expected = fingerprint([row for row in snap_rows if row["team"] == "SEA"]) if absent else fingerprint(snap_rows[0])
            self.assertEqual(len(result["observed_legs"]), 4)
            for leg in result["observed_legs"]:
                self.assertEqual(leg["source_row_sha256"], expected)
                self.assertEqual(leg["actual"], None if absent else 0)
                self.assertEqual(leg["result"], "void" if absent else "miss")
                self.assertEqual(leg["participation_basis"], "r106_team_snap_absence_inference" if absent else "r106_positive_offensive_snaps_no_stat_row")

    def test_ordered_absence_sheet_digest_is_not_a_sorted_or_row_hash_set(self):
        stats = unrelated_stats()
        original = unrelated_snaps(name=b"First Other")
        lines = original.splitlines(keepends=True)
        second = snap_row("Second Other", "60", player_index=6)
        for rows in ((lines[1], second, lines[2]), (second, lines[1], lines[2])):
            snaps = lines[0] + b"".join(rows)
            envelope, objects = binding_capture(stats=stats, snaps=snaps, snaps_audit=E.csv_audit(3))
            result = bind(journal_fixture(), envelope, objects)
            expected = fingerprint([row for row in raw_rows(snaps) if row["team"] == "SEA"])
            self.assertEqual({leg["source_row_sha256"] for leg in result["observed_legs"]}, {expected})

    def test_known_display_name_aliases_and_opposite_team_namesakes_preserve_exact_match(self):
        journal = journal_fixture(player="Hollywood Brown")
        stats = BINDING_STATS.replace(b"Fixture Receiver", b"Marquise Brown").replace(b",,,NA", b",,,80")
        stats += stat_row("Marquise Brown", "250", player_index=1, team="LAR", opponent="SEA")
        envelope, objects = binding_capture(stats=stats, stats_audit=E.csv_audit(4))
        result = bind(journal, envelope, objects)
        self.assertEqual([leg["actual"] for leg in result["observed_legs"]], [80] * 4)
        self.assertEqual({leg["player"] for leg in result["observed_legs"]}, {"Hollywood Brown"})

    def test_unknown_participation_period_never_disappears_before_helper_absence_inference(self):
        journal = journal_fixture()
        envelope, objects = good_capture()
        _, entries = B._frozen_entries(journal, 2026, {6})
        sources = {obs["profile"]: {"observation_id": oid, **obs} for oid, obs in envelope["observations"].items()
                   if obs["profile"] in (E.C.STATS_PROFILE, E.C.SNAPS_PROFILE, E.C.ROSTERS_PROFILE)}
        stats = raw_rows(unrelated_stats())
        bad_row = snap_row("Fixture Receiver", "30", player_index=0).replace(b",6,", b",NA,", 1)
        for target_positive in (False, True):
            snaps = raw_rows((BINDING_SNAPS if target_positive else unrelated_snaps()) + bad_row)
            with mock.patch.object(B, "_read_rows", side_effect=[iter(raw_rows(BINDING_ROSTER)), iter(stats), iter(snaps)]):
                facts, published, sheets, global_errors = B._raw_facts(entries, sources, None)
            game = E.C.verify_capture_envelope(envelope, lambda key: E.stream(objects[key]))["scoreboard_rows"][0]
            with self.assertRaisesRegex(ValueError, "source_fixture_conflict"):
                B._measurement(next(iter(entries.values())), facts, published, sheets, global_errors, game, sources, envelope["manifest"]["capture_id"])
        # Full public entry additionally rejects this unverified source period
        # in its first raw-integrity pass; the helper test is defense in depth.
        envelope, objects = binding_capture(snaps=BINDING_SNAPS + bad_row, snaps_audit=E.csv_audit(3))
        with self.assertRaises(ValueError): bind(journal_fixture(), envelope, objects)

    def test_ambiguous_snap_sheets_and_aliases_do_not_create_dnp(self):
        stats = unrelated_stats()
        variants = {"zero": BINDING_SNAPS.replace(b",40", b",0"), "na": BINDING_SNAPS.replace(b",40", b",NA"),
            "fraction": BINDING_SNAPS.replace(b",40", b",1.5"), "unknown_team": BINDING_SNAPS.replace(b"REG,SEA", b"REG,"),
            "unknown_reg": BINDING_SNAPS.replace(b"REG,SEA", b",SEA"),
            "alias": BINDING_SNAPS.replace(b"Fixture Receiver", b"F. Receiver").replace(RECEIVER_PFR_ID.encode(), b""),
            "unlocatable": BINDING_SNAPS + b"2026,6,REG,,,50,2026_06_LAR_SEA,LAR,\n",
            "duplicate": BINDING_SNAPS + BINDING_SNAPS.splitlines(keepends=True)[1]}
        for name, snaps in variants.items():
            count = 3 if name in ("unlocatable", "duplicate") else 2
            envelope, objects = binding_capture(stats=stats, snaps=snaps, snaps_audit=E.csv_audit(count, 1 if name == "duplicate" else 0))
            result = bind(journal_fixture(), envelope, objects)
            with self.subTest(name=name):
                self.assertEqual(result["observed_legs"], [])
                self.assertEqual(result["groups"][0]["status"], "withheld")

    def test_unjoined_stat_period_team_position_and_unknown_names_prevent_false_zero(self):
        for mode in ("alias", "team", "position", "reg", "unnamed", "post"):
            stats = BINDING_STATS
            if mode == "alias": stats = stats.replace(b"Fixture Receiver", b"F. Receiver").replace(RECEIVER_GSIS_ID.encode(), b"")
            elif mode == "team": stats = stats.replace(b"WR,SEA,Fixture", b"WR,,Fixture")
            elif mode == "position": stats = stats.replace(b"WR,SEA,Fixture", b",SEA,Fixture")
            elif mode == "reg": stats = stats.replace(b"REG,WR,SEA,Fixture", b",WR,SEA,Fixture")
            elif mode == "post": stats = stats.replace(b"REG,WR,SEA,Fixture", b"POST,WR,SEA,Fixture")
            else: stats = stats.replace(b"WR,SEA,Fixture Receiver", b"TE,,")
            envelope, objects = binding_capture(stats=stats)
            result = bind(journal_fixture(), envelope, objects)
            with self.subTest(mode=mode): self.assertEqual(result["observed_legs"], [])

    def test_measured_neighbors_survive_bad_participation_and_full_group_withholding(self):
        journal = journal_fixture()
        other = journal_fixture(player="Other Receiver", player_id=OTHER_APP_ID, lines=(39.5,))
        journal.snapshot["receipts"] += other.snapshot["receipts"]
        journal.snapshot["generation_records"] += other.snapshot["generation_records"]
        journal.objects.update(other.objects)
        stats = BINDING_STATS.replace(b",,,NA", b",,,80") + stat_row("Other Receiver", "NA", player_index=4)
        snaps = BINDING_SNAPS.replace(b",40", b",NA")
        envelope, objects = binding_capture(stats=stats, stats_audit=E.csv_audit(4), snaps=snaps)
        result = bind(journal, envelope, objects)
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(len(result["observed_legs"]), 4)
        self.assertEqual({leg["player_id"] for leg in result["observed_legs"]}, {RECEIVER_APP_ID})
        self.assertEqual(sum(group["status"] == "withheld" for group in result["groups"]), 1)

    def test_conflicting_frozen_names_across_vintages_cannot_split_one_underlying_group(self):
        for other in (b"20", b"80"):
            journal = journal_fixture()
            change_frozen_player(journal, 1, player="Other Receiver")
            stats = BINDING_STATS.replace(b",,,NA", b",,,80") + stat_row("Other Receiver", other.decode(), player_index=4)
            envelope, objects = binding_capture(stats=stats, stats_audit=E.csv_audit(4))
            result = bind(journal, envelope, objects)
            self.assertEqual(result["observed_legs"], [])
            self.assertEqual(result["groups"][0]["reason_codes"], ["conflicting_frozen_group_identity"])
            self.assertEqual(len(result["groups"][0]["source_receipt_ids"]), 4)

    def test_pinned_known_nickname_across_vintages_is_not_false_identity_conflict(self):
        journal = journal_fixture(player="Hollywood Brown")
        change_frozen_player(journal, 1, player="Marquise Brown")
        stats = BINDING_STATS.replace(b"Fixture Receiver", b"Marquise Brown").replace(b",,,NA", b",,,80")
        envelope, objects = binding_capture(stats=stats)
        result = bind(journal, envelope, objects)
        self.assertEqual(len(result["observed_legs"]), 4)
        self.assertEqual(result["groups"][0]["status"], "staged")

    def test_identifiable_bad_frozen_member_withholds_all_original_group_members(self):
        envelope, objects = good_capture()
        journal = journal_fixture()
        change_frozen_player(journal, 1, player="")
        result = bind(journal, envelope, objects)
        self.assertEqual(result["observed_legs"], [])
        self.assertEqual(result["groups"][0]["reason_codes"], ["invalid_frozen_player_identity"])
        self.assertEqual(len(result["source_receipts"]), 4)

    def test_durably_rejected_generation_rows_are_not_misrepresented_as_accepted_members(self):
        envelope, objects = good_capture()
        journal = journal_fixture()
        ack = journal.snapshot["generation_records"][0]["ack"]
        rejected = ack["receipts"].pop()
        ack["rejections"].append({"source_receipt_id": rejected["source_receipt_id"], "reason": "source_not_eligible"})
        journal.snapshot["receipts"] = [row for row in journal.snapshot["receipts"] if row["receipt_id"] != rejected["receipt_id"]]
        result = bind(journal, envelope, objects)
        self.assertEqual(len(result["observed_legs"]), 3)
        self.assertEqual(len(result["groups"][0]["receipt_ids"]), 3)

    def test_membership_ids_are_stable_for_unrelated_watermarks_but_expand_with_vintages(self):
        envelope, objects = good_capture()
        a = bind(journal_fixture(watermark="100"), envelope, objects)
        b = bind(journal_fixture(watermark="101"), envelope, objects)
        c = bind(journal_fixture(vintages=3), envelope, objects)
        self.assertEqual(a["membership_id"], b["membership_id"])
        self.assertNotEqual(a["membership_watermark"], b["membership_watermark"])
        self.assertNotEqual(a["membership_id"], c["membership_id"])
        self.assertEqual(len(c["observed_legs"]), 6)

    def test_late_raw_reread_mutation_and_source_exceptions_never_return_evidence(self):
        journal, (envelope, objects) = journal_fixture(), good_capture()
        stat_id = next(o["raw_sha256"] for o in envelope["observations"].values() if o["profile"] == E.C.STATS_PROFILE)
        calls = {}
        def source(key):
            calls[key] = calls.get(key, 0) + 1
            raw = objects[key]
            if key == stat_id and calls[key] == 2: raw = raw.replace(b",,,80", b",,,30")
            return E.stream(raw)
        with self.assertRaises(ValueError): B.bind_capture_labels(journal, envelope, source)
        def broken(_): raise OSError("trusted source failure")
        with self.assertRaises(OSError): B.bind_capture_labels(journal, envelope, broken)

    def test_all_authority_and_metadata_are_frozen_before_any_raw_callback(self):
        journal, (envelope, objects) = journal_fixture(), good_capture()
        wanted = envelope["manifest"]["capture_id"]
        def source(key):
            journal.snapshot["receipts"].clear()
            journal.snapshot["generation_records"].clear()
            journal.objects.clear()
            envelope["manifest"]["sources"].clear()
            return E.stream(objects[key])
        result = B.bind_capture_labels(journal, envelope, source)
        self.assertEqual(result["capture_id"], wanted)
        self.assertEqual(len(result["observed_legs"]), 4)

    def test_missing_ack_member_and_unlocatable_orphan_fail_population_closed(self):
        envelope, objects = good_capture()
        for mode in ("ack", "orphan", "generation_hash", "snapshot"):
            journal = journal_fixture()
            if mode == "ack": journal.snapshot["generation_records"][0]["ack"]["receipts"].pop()
            elif mode == "orphan": journal.snapshot["generation_records"].pop()
            elif mode == "generation_hash": journal.snapshot["generation_records"][0]["payload"]["season"] = 2025
            else: journal.snapshot["watermark"] = True
            with self.subTest(mode=mode), self.assertRaises(ValueError): bind(journal, envelope, objects)

    def test_missing_changed_or_late_imported_source_withholds_whole_identifiable_group(self):
        envelope, objects = good_capture()
        for mode in ("missing", "hash", "late", "kind"):
            journal = journal_fixture()
            key = journal.snapshot["generation_records"][0]["payload"]["generations"][0]["source_snapshots"]["leg_pool"]
            if mode == "missing": journal.objects.pop(key)
            elif mode == "hash": journal.objects[key]["payload"]["week"] = 5
            elif mode == "late": journal.objects[key]["received_utc"] = "2026-10-20T13:00:00Z"
            else: journal.objects[key]["kind"] = "generation"
            result = bind(journal, envelope, objects)
            with self.subTest(mode=mode):
                self.assertEqual(result["observed_legs"], [])
                self.assertEqual(len(result["groups"][0]["receipt_ids"]), 4)

    def test_empty_membership_is_explicit_not_success_and_invalid_limits_never_read_journal(self):
        envelope, objects = good_capture()
        journal = Journal({"version": 1, "watermark": "0", "receipts": [], "generation_records": []}, {})
        result = bind(journal, envelope, objects)
        self.assertEqual(result["status"], "no_supported_membership")
        self.assertEqual(result["observed_legs"], [])
        for limit in (True, -1, B.capture.DEFAULT_MAX_TOTAL_BYTES + 1):
            with self.assertRaises(ValueError): B.bind_capture_labels(None, envelope, None, max_total_bytes=limit)

    def test_provenance_work_limit_and_missing_whole_week_fail_closed(self):
        envelope, objects = good_capture()
        with mock.patch.object(B, "MAX_PROVENANCE_BYTES", 1), self.assertRaisesRegex(ValueError, "provenance_work"):
            bind(journal_fixture(), envelope, objects)
        envelope, objects = binding_capture(stats=BINDING_STATS.replace(b"REG,", b"POST,"))
        result = bind(journal_fixture(), envelope, objects)
        self.assertEqual(result["observed_legs"], [])
        self.assertEqual(result["groups"][0]["reason_codes"], ["stats_week_not_published"])


CHECKOUT = os.environ.get("NFL2026_CHECKOUT")


@unittest.skipUnless(CHECKOUT, "Set NFL2026_CHECKOUT for original producer outcome parity")
class ActualProducerBindingTests(unittest.TestCase):
    def test_original_four_receipts_and_raw_outcomes_match_field_for_field(self):
        self.assertTrue((Path(CHECKOUT) / "tests/fixtures/week6_outcomes.py").is_file())
        script = r'''
import base64, importlib.util, json, tempfile
from pathlib import Path
from scripts import build_learning_outcomes as producer
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory);f.generation(root);f.generation(root,'2026-09-01T13:00:00Z');plan=f.plan(root)
 captures=[f.bundle(),f.bundle(value='0'),f.bundle(value='-5'),f.bundle(value='NA'),f.bundle(status='STATUS_IN_PROGRESS'),
  f.bundle(stats_rows=[f.stats_row('20',player_display_name='Other Receiver')]),
  f.bundle(stats_rows=[f.stats_row('20',player_display_name='Other Receiver')],snap_rows=[f.snap_row(player='Other Receiver')])]
 output=[]
 for capture in captures:
  output.append({'manifest':capture['manifest'],'observations':capture['observations'],
    'raw':{key:base64.b64encode(raw).decode() for key,raw in capture['raw_objects'].items()},'expected':producer.build(plan,capture)})
 print(json.dumps({'documents':[item['request']['payload'] for item in plan['generations']],
                  'sources':plan['source_objects'],'cases':output}))
'''
        run = subprocess.run([sys.executable, "-c", script], cwd=CHECKOUT, text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, "Actual local NFL producer fixture failed")
        payload = json.loads(run.stdout)
        adapted = adapt_prospective(journal_docs=payload["documents"])
        self.assertEqual(adapted["rejections"], [])
        by_source = {row["source_receipt_id"]: row for row in adapted["receipts"]}
        records = [{"object_id": doc["envelope_id"], "payload": doc, "recorded_utc": "2026-10-20T12:00:00Z", "ack": {
            "version": 1, "object_id": doc["envelope_id"], "durable": True, "rejections": [], "receipts": [
                {"source_receipt_id": row["receipt_id"], "receipt_id": by_source[row["receipt_id"]]["receipt_id"], "status": "recorded"}
                for row in doc["receipts"]]}} for doc in payload["documents"]]
        objects = {item["object_id"]: {**item, "received_utc": "2026-10-20T11:00:00Z"} for item in payload["sources"]}
        journal = Journal({"version": 1, "watermark": "100", "receipts": adapted["receipts"], "generation_records": records}, objects)
        for case in payload["cases"]:
            raw = {key: base64.b64decode(value) for key, value in case["raw"].items()}
            envelope = {"version": 1, "kind": "nfl.capture_envelope.v1", "scope": "uploaded_only", "manifest": case["manifest"],
                        "observations": case["observations"], "raw_objects": [{"raw_sha256": key, "raw_bytes": len(value)} for key, value in raw.items()]}
            actual = bind(journal, envelope, raw)
            expected = case["expected"]
            self.assertEqual(actual["source_receipts"], sorted(expected["source_receipts"], key=lambda row: row["receipt_id"]))
            self.assertEqual(actual["observed_legs"], sorted(expected["observed_legs"], key=lambda row: row["source_receipt_id"]))
            self.assertEqual(actual["game_observations"], expected["game_observations"])
            self.assertEqual(actual["rejections"], expected["rejections"])
            self.assertEqual(len(actual["groups"][0]["receipt_ids"]), 4)


if __name__ == "__main__":
    unittest.main()
