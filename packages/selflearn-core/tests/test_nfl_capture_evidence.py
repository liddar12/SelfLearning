"""Standalone independent capture oracles; real producer parity is explicit opt-in."""
import base64
import copy
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tracemalloc
import unittest
from unittest import mock

from selflearn_core.adapters import nfl_capture_evidence as C

STATS = (b'\xef\xbb\xbfseason,week,season_type,position,team,player_display_name,passing_yards,rushing_yards,receiving_yards\r\n'
         b'2026,6,REG,WR,SEA,Fixture Receiver,,,NA\r\n'
         b'2026,6,REG,WR,SEA,Negative Receiver,,,-7\r\n'
         b'2026,6,REG,QB,SEA,Fixture Quarterback,0,,\r\n')
SNAPS = (b'season,week,game_type,team,player,offense_snaps\n'
         b'2026,6,REG,SEA,Fixture Receiver,40\n2026,6,REG,LAR,Other Receiver,52\n')
MEASUREMENT = "raw values retained; missing/negative cells are not coerced or graded"
CLOCK_POLICY = "producer observations only; upstream issue and exact game-finish times unknown"


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def fingerprint(value):
    return digest(encoded(value))


def event(gid="g6", status="STATUS_FINAL", home="24", away="17"):
    return {"id": gid, "date": "2026-10-18T20:25:00Z", "status": {"type": {"name": status}},
            "competitions": [{"competitors": [{"homeAway": "home", "team": {"abbreviation": "SEA"}, "score": home},
                                              {"homeAway": "away", "team": {"abbreviation": "LAR"}, "score": away}]}]}


def score_audit(**updates):
    return {"events": 1, "normalized_games": 1, "final_games": 1, "nonfinal_games": 0,
            "malformed_events": 0, "duplicate_events": 0, "conflicting_game_ids": 0,
            "invalid_final_scores": 0, "quarantined_game_ids": [], **updates}


def csv_audit(rows, duplicate_rows=0, outside=0):
    return {"rows": rows, "duplicate_rows": duplicate_rows, "outside_regular_week_rows": outside,
            "measurement_policy": MEASUREMENT}


def fixture(*, weeks=(6,), stats=STATS, snaps=SNAPS, stats_audit=None, snaps_audit=None,
            score_raw=None, score_audits=None, global_audit=None):
    manifest = {"version": 1, "kind": "nfl.outcome_capture.v1", "season": 2026, "weeks": list(weeks),
                "started_utc": "2026-10-19T02:00:00Z", "completed_utc": "2026-10-19T02:00:59Z",
                "sources": [], "clock_policy": CLOCK_POLICY,
                "publication_status": "source_capture_not_public_forecast_evidence",
                "audit": global_audit or {"cross_week_conflicting_game_ids": [], "quarantined_game_ids": [],
                                           "normalized_games": len(weeks)}}
    observations, objects = {}, {}
    specs = [("espn_scoreboard_v1", week) for week in weeks] + [("nflverse_stats_player_v1", None), ("nflverse_snap_counts_v1", None)]
    for index, (profile, week) in enumerate(specs):
        if week is not None:
            raw = (score_raw or {}).get(week, encoded({"season": {"year": 2026}, "week": {"number": week}, "events": [event("g" + str(week))]}))
            audit = copy.deepcopy((score_audits or {}).get(week, score_audit()))
            url = f"https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&dates=2026&week={week}"
        elif profile == "nflverse_stats_player_v1":
            raw, audit = stats, stats_audit or csv_audit(3)
            url = "https://github.com/nflverse/nflverse-data/releases/download/stats_player/stats_player_week_2026.csv"
        else:
            raw, audit = snaps, snaps_audit or csv_audit(2)
            url = "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_2026.csv"
        rid = digest(raw)
        objects[rid] = raw
        observation = {"version": 1, "kind": "nfl.outcome_source_observation.v1", "evidence_class": "observed_source_snapshot",
            "profile": profile, "season": 2026, "week": week, "source_url": url, "raw_sha256": rid, "raw_bytes": len(raw),
            "fetch_started_utc": f"2026-10-19T02:00:{index * 2 + 1:02d}Z",
            "observed_utc": f"2026-10-19T02:00:{index * 2 + 2:02d}Z", "upstream_updated_utc": None,
            "http": {"status": 200, "last_modified": "Fri, 01 Jan 2021 00:00:00 GMT"}, "audit": copy.deepcopy(audit)}
        oid = fingerprint(observation)
        observations[oid] = observation
        manifest["sources"].append({"profile": profile, "season": 2026, "week": week, "observation_id": oid})
    manifest["capture_id"] = fingerprint(manifest)
    envelope = {"version": 1, "kind": "nfl.capture_envelope.v1", "scope": "uploaded_only", "manifest": manifest,
                "observations": observations, "raw_objects": [{"raw_sha256": key, "raw_bytes": len(raw)} for key, raw in sorted(objects.items())]}
    return envelope, objects


def reseal(envelope):
    observations = {}
    for ref in envelope["manifest"]["sources"]:
        observation = envelope["observations"][ref["observation_id"]]
        oid = fingerprint(observation)
        observations[oid] = observation
        ref["observation_id"] = oid
    envelope["observations"] = observations
    manifest = envelope["manifest"]
    manifest["capture_id"] = fingerprint({key: value for key, value in manifest.items() if key != "capture_id"})


def stream(raw):
    return (raw[index:index + C.MAX_CHUNK_BYTES] for index in range(0, len(raw), C.MAX_CHUNK_BYTES))


def verify(envelope, objects, **kwargs):
    return C.verify_capture_envelope(envelope, lambda key: stream(objects[key]), **kwargs)


class NFLCaptureEvidenceTests(unittest.TestCase):
    def test_independent_golden_full_report_is_integrity_not_label_or_auth(self):
        envelope, objects = fixture()
        before = copy.deepcopy(envelope)
        result = verify(envelope, objects)
        self.assertEqual(envelope, before)
        self.assertEqual(result["capture_id"], before["manifest"]["capture_id"])
        self.assertEqual(result["verified_raw_bytes"], sum(map(len, objects.values())))
        self.assertEqual(result["distinct_unique_raw_bytes"], result["verified_raw_bytes"])
        self.assertEqual(result["capture_audit"], {"cross_week_conflicting_game_ids": [], "quarantined_game_ids": [], "normalized_games": 1})
        game = result["scoreboard_rows"][0]
        self.assertEqual((game["game_id"], game["home"], game["away"], game["home_score"], game["away_score"]), ("g6", "SEA", "LAR", 24, 17))
        self.assertIsNone(game["final_utc"])
        self.assertIsNone(game["upstream_updated_utc"])
        self.assertEqual(result["source_authentication"], "not_performed")
        self.assertFalse(result["labels_accepted"])
        self.assertFalse(result["durable_ack"])
        self.assertEqual(result["scope"], "capture_integrity_only")
        for field in ("hit", "actual", "proposals", "resolutions", "played"):
            self.assertNotIn(field, result)
            self.assertNotIn(field, game)

    def test_raw_na_zero_negative_fractional_and_unknown_period_are_not_semantic_labels(self):
        stats = STATS.replace(b"REG,WR", b",WR")
        snaps = SNAPS.replace(b"REG,SEA", b",SEA").replace(b",40", b",1.5").replace(b",52", b",NA")
        envelope, objects = fixture(stats=stats, snaps=snaps)
        result = verify(envelope, objects)
        self.assertEqual(result["source_audits"][1]["audit"], csv_audit(3))
        self.assertEqual(result["source_audits"][2]["audit"], csv_audit(2))
        self.assertFalse(result["labels_accepted"])

    def test_full_season_post_rows_duplicates_and_embedded_csv_newline_are_preserved(self):
        extra = b'2026,19,POST,WR,SEA,"Postseason\nReceiver",,,80\r\n'
        first = STATS.splitlines(keepends=True)[1]
        envelope, objects = fixture(stats=STATS + first + extra, stats_audit=csv_audit(5, 1, 1))
        result = verify(envelope, objects)
        self.assertEqual(result["source_audits"][1]["audit"], csv_audit(5, 1, 1))

    def test_live_and_scheduled_never_expose_scores_and_ot_is_final(self):
        events = [event("live", "STATUS_IN_PROGRESS"), event("scheduled", "STATUS_SCHEDULED"), event("ot", "STATUS_FINAL_OVERTIME", "27", "24")]
        raw = encoded({"events": events})
        envelope, objects = fixture(score_raw={6: raw}, score_audits={6: score_audit(events=3, normalized_games=3, final_games=1, nonfinal_games=2)},
                                   global_audit={"cross_week_conflicting_game_ids": [], "quarantined_game_ids": [], "normalized_games": 3})
        result = verify(envelope, objects)
        for row in result["scoreboard_rows"]:
            if not row["final"]:
                self.assertNotIn("home_score", row)
                self.assertNotIn("away_score", row)
        self.assertEqual(next(row for row in result["scoreboard_rows"] if row["game_id"] == "ot")["home_score"], 27)

    def test_invalid_scores_and_nested_metadata_quarantine_only_affected_games(self):
        events = [event("good")]
        for gid, score in (("fraction", 1.5), ("bool", True), ("negative", -1), ("nan", "NaN")):
            events.append(event(gid, home=score))
        for gid, change in (("completed", "completed"), ("period", "week"), ("reg", "reg"), ("competition", "competition"), ("team", "team")):
            item = event(gid)
            if change == "completed": item["status"]["type"]["completed"] = False
            elif change == "week": item["week"] = {"number": 5}
            elif change == "reg": item["season"] = {"year": 2026, "type": 3}
            elif change == "competition": item["competitions"][0]["status"] = {"type": {"name": "STATUS_IN_PROGRESS"}}
            else: item["competitions"][0]["competitors"][0]["team"]["abbreviation"] = "UNKNOWN"
            events.append(item)
        ids = sorted(item["id"] for item in events[1:])
        envelope, objects = fixture(score_raw={6: encoded({"events": events})},
            score_audits={6: score_audit(events=10, malformed_events=9, invalid_final_scores=4, quarantined_game_ids=ids)},
            global_audit={"cross_week_conflicting_game_ids": [], "quarantined_game_ids": ids, "normalized_games": 1})
        self.assertEqual([row["game_id"] for row in verify(envelope, objects)["scoreboard_rows"]], ["good"])

    def test_exact_and_conflicting_event_duplicates_and_global_crossweek_quarantine(self):
        same = event("same")
        events = [event("conflict"), event("conflict", home="30"), same, copy.deepcopy(same)]
        audit = score_audit(events=4, duplicate_events=2, conflicting_game_ids=1, quarantined_game_ids=["conflict"])
        envelope, objects = fixture(score_raw={6: encoded({"events": events})}, score_audits={6: audit},
            global_audit={"cross_week_conflicting_game_ids": [], "quarantined_game_ids": ["conflict"], "normalized_games": 1})
        self.assertEqual([row["game_id"] for row in verify(envelope, objects)["scoreboard_rows"]], ["same"])
        shared = encoded({"events": [event("shared")]})
        envelope, objects = fixture(weeks=(5, 6), score_raw={5: shared, 6: shared},
            global_audit={"cross_week_conflicting_game_ids": ["shared"], "quarantined_game_ids": ["shared"], "normalized_games": 0})
        calls = []
        result = C.verify_capture_envelope(envelope, lambda key: (calls.append(key), stream(objects[key]))[1])
        self.assertEqual(result["scoreboard_rows"], [])
        self.assertEqual(calls.count(digest(shared)), 2)
        self.assertEqual(result["verified_raw_bytes"], result["distinct_unique_raw_bytes"] + len(shared))

    def test_same_raw_id_for_two_csv_profiles_is_verified_and_charged_twice(self):
        raw = (b"season,week,position,team,player_display_name,passing_yards,rushing_yards,receiving_yards,player,offense_snaps\n"
               b"2026,6,WR,SEA,Name,,,NA,Name,NA\n")
        envelope, objects = fixture(stats=raw, snaps=raw, stats_audit=csv_audit(1), snaps_audit=csv_audit(1))
        result = verify(envelope, objects)
        self.assertEqual(result["verified_raw_bytes"] - result["distinct_unique_raw_bytes"], len(raw))

    def test_resealed_metadata_types_profiles_urls_clocks_and_membership_fail(self):
        cases = ("version", "season", "week", "profile", "url", "upstream", "naive", "regressed", "source_order", "extra_raw", "missing_raw", "extra_observation", "http", "audit")
        for case in cases:
            envelope, objects = fixture()
            obs = next(iter(envelope["observations"].values()))
            if case == "version": obs["version"] = True
            elif case == "season": obs["season"] = 2026.0
            elif case == "week": envelope["manifest"]["sources"][0]["week"] = True
            elif case == "profile": obs["profile"] = "other_profile"
            elif case == "url": obs["source_url"] = "http://127.0.0.1/private"
            elif case == "upstream": obs["upstream_updated_utc"] = obs["observed_utc"]
            elif case == "naive": obs["observed_utc"] = "2026-10-19T02:00:02"
            elif case == "regressed": obs["observed_utc"] = "2026-10-19T01:00:02Z"
            elif case == "source_order": envelope["manifest"]["sources"].reverse()
            elif case == "extra_raw": envelope["raw_objects"].append({"raw_sha256": digest(b"x"), "raw_bytes": 1})
            elif case == "missing_raw": envelope["raw_objects"].pop()
            elif case == "extra_observation": envelope["observations"][digest(b"x")] = copy.deepcopy(obs)
            elif case == "http": obs["http"]["unknown"] = "value"
            else: obs["audit"]["events"] = True
            if case not in ("season", "extra_observation"): reseal(envelope)
            with self.subTest(case=case), self.assertRaises(ValueError):
                C.verify_capture_envelope(envelope, lambda _: self.fail("invalid metadata must fail before raw callback"))

    def test_hash_and_audit_tampering_fail_even_when_structural_hashes_are_resealed(self):
        for case in ("raw", "observation_hash", "manifest_hash", "source_audit", "global_audit"):
            envelope, objects = fixture()
            observation = next(iter(envelope["observations"].values()))
            if case == "raw": objects[observation["raw_sha256"]] += b" "
            elif case == "observation_hash": observation["http"]["etag"] = "changed"
            elif case == "manifest_hash": envelope["manifest"]["completed_utc"] = "2026-10-19T02:01:00Z"
            elif case == "source_audit": observation["audit"]["normalized_games"] = 0; reseal(envelope)
            else: envelope["manifest"]["audit"]["normalized_games"] = 0; reseal(envelope)
            with self.subTest(case=case), self.assertRaises(ValueError):
                verify(envelope, objects)

    def test_malformed_csv_and_json_sources_do_not_produce_reports(self):
        bad_csv = [b"\xff", b"season,season,week\n2026,2026,6\n", b"season,week,position\n2026,6,WR\n",
                   STATS + b"2026,6\n", STATS.replace(b"Fixture", b"Fix\0ture"), STATS.replace(b"2026,6", b"2025,6"), STATS.splitlines(keepends=True)[0]]
        for raw in bad_csv:
            envelope, objects = fixture(stats=raw)
            with self.subTest(raw=raw[:25]), self.assertRaises(ValueError): verify(envelope, objects)
        bad_json = [b"no-json", b"{}", b'{"events":[],"events":[{}]}', b'{"events":[NaN]}', b'{"events":[1e999]}',
                    encoded({"events": []}), encoded({"season": {"year": 2025}, "events": [event()]}),
                    encoded({"season": {"year": 2026, "type": 3}, "events": [event()]}),
                    b'{"events":' + b'[' * 30 + b'0' + b']' * 30 + b'}']
        for raw in bad_json:
            envelope, objects = fixture(score_raw={6: raw})
            with self.subTest(raw=raw[:25]), self.assertRaises(ValueError): verify(envelope, objects)

    def test_raw_framing_truncation_extra_bytes_and_callback_exceptions_fail(self):
        envelope, objects = fixture()
        key = envelope["manifest"]["sources"][0]["observation_id"]
        raw = objects[envelope["observations"][key]["raw_sha256"]]
        for parts in ([raw[:-1]], [raw, b"x"], [b""], [bytearray(raw)], [raw[:1], raw[1:]]):
            with self.subTest(lengths=[len(p) for p in parts]), self.assertRaises(ValueError):
                C.verify_capture_envelope(envelope, lambda _: iter(parts))
        def failing(_): raise OSError("trusted fixture source diagnostic")
        with self.assertRaises(OSError): C.verify_capture_envelope(envelope, failing)

    def test_metadata_snapshots_survive_all_callback_mutations(self):
        envelope, objects = fixture()
        wanted = envelope["manifest"]["capture_id"]
        def source(key):
            envelope["observations"].clear()
            envelope["manifest"]["sources"].clear()
            envelope["raw_objects"].clear()
            return stream(objects[key])
        self.assertEqual(C.verify_capture_envelope(envelope, source)["capture_id"], wanted)

    def test_huge_metadata_rejected_before_encoding_or_callbacks(self):
        envelope, objects = fixture()
        envelope["manifest"]["started_utc"] = "x" * (C.MAX_METADATA_DOCUMENT + 1)
        with mock.patch.object(C, "_encoded", side_effect=AssertionError("must reject before encoding")):
            with self.assertRaises(ValueError): C.verify_capture_envelope(envelope, lambda _: self.fail("must not read"))
        envelope, objects = fixture()
        envelope["manifest"]["season"] = 10 ** 1000
        with self.assertRaises(ValueError): verify(envelope, objects)

    def test_limits_are_lower_only_and_source_reference_budget_is_not_deduped(self):
        envelope, objects = fixture()
        total = sum(len(raw) for raw in objects.values())
        self.assertEqual(verify(envelope, objects, max_total_bytes=total)["verified_raw_bytes"], total)
        for limit in (True, -1, 1.5, C.DEFAULT_MAX_TOTAL_BYTES + 1, total - 1):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                C.verify_capture_envelope(envelope, lambda _: self.fail("must reject before read"), max_total_bytes=limit)
        envelope, objects = fixture(weeks=tuple(range(1, 10)))
        ids = {}
        for ref in envelope["manifest"]["sources"]:
            observation = envelope["observations"][ref["observation_id"]]
            size = C.SCOREBOARD_LIMIT if ref["week"] else C.CSV_LIMIT
            rid = digest(("score" if ref["week"] else ref["profile"]).encode())
            observation.update(raw_sha256=rid, raw_bytes=size)
            ids[rid] = size
        envelope["raw_objects"] = [{"raw_sha256": key, "raw_bytes": size} for key, size in ids.items()]
        reseal(envelope)
        self.assertLess(sum(ids.values()), C.DEFAULT_MAX_TOTAL_BYTES)
        with self.assertRaisesRegex(ValueError, "total_size"):
            C.verify_capture_envelope(envelope, lambda _: self.fail("per-reference declared budget must reject"))

    def test_utf8_and_quoted_crlf_fields_can_cross_canonical_chunk_boundary(self):
        header = b"season,week,position,team,player_display_name,passing_yards,rushing_yards,receiving_yards\r\n"
        prefix = header
        rows = 0
        while len(prefix) + 80000 < C.MAX_CHUNK_BYTES:
            prefix += f"2026,6,WR,SEA,Name{rows},,,0".encode() + b"x" * 50000 + b"\r\n"
            rows += 1
        start = b'2026,6,WR,SEA,"'
        padding = C.MAX_CHUNK_BYTES - 1 - len(prefix) - len(start)
        raw = prefix + start + b"a" * padding + "é\r\nTail".encode() + b'",,,NA\r\n'
        self.assertEqual(raw[C.MAX_CHUNK_BYTES - 1:C.MAX_CHUNK_BYTES + 1], "é".encode())
        envelope, objects = fixture(stats=raw, stats_audit=csv_audit(rows + 1))
        self.assertEqual(verify(envelope, objects)["source_audits"][1]["audit"]["rows"], rows + 1)

    def test_oversized_unused_header_cannot_multiply_retained_row_memory(self):
        header = b"season,week,position,team,player_display_name,passing_yards,rushing_yards,receiving_yards," + b"x" * 50000 + b"\n"
        raw = header + b"".join(f"2026,6,WR,SEA,Name{i},,,NA,x\n".encode() for i in range(1000))
        envelope, objects = fixture(stats=raw, stats_audit=csv_audit(1000))
        tracemalloc.start()
        try:
            with self.assertRaisesRegex(ValueError, "header_exceeds"):
                verify(envelope, objects)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 4 * 1024 * 1024, "helper must not retain repeated 50k header per row")

    def test_csv_parser_work_limits_fail_closed_without_changing_duplicate_audit(self):
        envelope, objects = fixture(stats=STATS + STATS.splitlines(keepends=True)[1], stats_audit=csv_audit(4, 1))
        self.assertEqual(verify(envelope, objects)["source_audits"][1]["audit"], csv_audit(4, 1))
        limits = (("MAX_CSV_COLUMNS", 3), ("MAX_CSV_HEADER_CHARS", 5), ("MAX_CSV_LINE_BYTES", 10),
                  ("MAX_CSV_RECORD_BYTES", 10), ("MAX_CSV_FIELD_CHARS", 5), ("MAX_CSV_ROWS", 1))
        for name, value in limits:
            with self.subTest(limit=name), mock.patch.object(C, name, value), self.assertRaises(ValueError):
                verify(envelope, objects)


CHECKOUT = os.environ.get("NFL2026_CHECKOUT")


@unittest.skipUnless(CHECKOUT, "Set NFL2026_CHECKOUT for actual producer capture parity")
class RealNFLCaptureParityTests(unittest.TestCase):
    def test_actual_producer_fixture_captures_match_independent_engine_verifier(self):
        checkout = Path(CHECKOUT)
        self.assertTrue((checkout / "tests/fixtures/week6_outcomes.py").is_file(), "Explicit NFL checkout is not usable")
        script = r'''
import base64, importlib.util, json
from pathlib import Path
from scripts import capture_outcome_evidence as capture
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
cases=[f.bundle(),f.bundle(status='STATUS_IN_PROGRESS'),f.bundle(status='STATUS_FINAL_OVERTIME'),
       f.bundle(value='NA'),f.bundle(value='-5'),f.bundle(snap_rows=[f.snap_row(offense_snaps='1.5')]),
       f.bundle(stats_rows=[f.stats_row(),f.stats_row(),f.stats_row(week='19',season_type='POST')])]
out=[]
for bundle in cases:
 capture.verify_capture(bundle)
 out.append({'manifest':bundle['manifest'],'observations':bundle['observations'],
             'raw':{key:base64.b64encode(raw).decode() for key,raw in bundle['raw_objects'].items()},
             'games':capture.scoreboard_rows(bundle)})
print(json.dumps(out))
'''
        run = subprocess.run([sys.executable, "-c", script], cwd=checkout, text=True, capture_output=True, timeout=30)
        self.assertEqual(run.returncode, 0, "Actual local NFL fixture failed")
        for case in json.loads(run.stdout):
            objects = {key: base64.b64decode(raw) for key, raw in case["raw"].items()}
            envelope = {"version": 1, "kind": "nfl.capture_envelope.v1", "scope": "uploaded_only",
                "manifest": case["manifest"], "observations": case["observations"],
                "raw_objects": [{"raw_sha256": key, "raw_bytes": len(raw)} for key, raw in sorted(objects.items())]}
            report = verify(envelope, objects)
            self.assertEqual(report["capture_audit"], case["manifest"]["audit"])
            self.assertEqual(report["scoreboard_rows"], case["games"])
            self.assertEqual([row["audit"] for row in report["source_audits"]],
                             [case["observations"][ref["observation_id"]]["audit"] for ref in case["manifest"]["sources"]])
            self.assertFalse(report["labels_accepted"])


if __name__ == "__main__":
    unittest.main()
