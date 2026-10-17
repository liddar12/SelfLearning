"""Opt-in native observed-journal CAS/transaction gate, standard library only.

Requires a separate fresh disposable PostgreSQL17 CI service. No cloud hosts,
servers, installation, production credentials or existing schemas are adopted.
WASM tests cannot substitute for the separate connections used by this suite.
"""
from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import shutil
import sys
import time
import unittest
from unittest.mock import patch
import uuid

import test_native_concurrency as native


ROOT = Path(__file__).resolve().parents[2]
ADDITIVE = ROOT / "supabase/migrations/20261015233134_observed_journal_v1.sql"
BASIS = "nfl.observed_resolution_v2"
GAME, STAT = "espn.scoreboard", "nflverse.stats_player"
RPCS = {"read": "selflearn_journal_read_observed_v1", "commit": "selflearn_journal_commit_observed_v1"}


def statement(operation, parameters):
    return "SELECT public." + RPCS[operation] + "(" + ",".join(native.literal(p) for p in parameters) + ");"


def forecast(label):
    # Generate a real source receipt through the unchanged independent fixture
    # and NFL adapter. No abbreviated SQL-only receipt can hide a wire mismatch.
    previous_path = sys.path[:]
    sys.path[:0] = [str(ROOT / "packages/selflearn-core"), str(ROOT / "packages/selflearn-core/tests")]
    try:
        from test_nfl_adapter import fixture
        from selflearn_core.adapters.nfl import adapt_prospective
        doc, _, _ = fixture(player=label)
        card = doc["cards"][0]
        source = card["legs"][0]["learning_receipt"]
        source.update(forecast_created_utc="2026-09-01T12:00:00Z",
                      source_timestamps={"weekly": "2026-09-01T11:00:00Z"})
        source["receipt_id"] = native.digest({k: v for k, v in source.items() if k != "receipt_id"})
        card.update(first_seen_utc="2026-09-01T12:01:00Z", earliest_kickoff_utc="2026-09-02T00:15:00Z")
        scheduled = {"g1": {"status": "STATUS_SCHEDULED", "kickoff_utc": card["earliest_kickoff_utc"],
                            "source": "explicit synthetic native pregame fixture"}}
        adapted = adapt_prospective(card_docs=[doc], game_evidence=scheduled, observed_utc=card["first_seen_utc"])
        if adapted["rejections"] or len(adapted["receipts"]) != 1:
            raise AssertionError("Native observed fixture failed the actual NFL receipt adapter")
        return adapted["receipts"][0], {"source": source, "leg": card["legs"][0]}
    finally:
        sys.path[:] = previous_path


def observed(receipt, key, tag, *, actual=80.0, clock="2026-09-02T04:00:00Z", parent=None):
    previous_path = sys.path[:]
    sys.path.insert(0, str(ROOT / "packages/selflearn-core"))
    try:
        from selflearn_core.adapters.nfl_observed_resolution import resolve_observed
        leg = copy.deepcopy(key["leg"])
        leg.pop("learning_receipt", None)
        leg.update(week=6, source_receipt_id=key["source"]["receipt_id"], actual=actual,
            result="hit" if actual >= leg["line"] else "miss", played=True, status="FINAL", authoritative=True,
            source=STAT, observed_utc=clock, observation_id=native.digest(["stats", tag, clock]),
            participation_basis="stat_row", team="BUF", opp="MIA")
        games = {"g1": {"season": 2026, "week": 6, "game_id": "g1", "status": "STATUS_FINAL",
            "kickoff_utc": receipt["event_start_utc"], "home": "BUF", "away": "MIA", "home_score": 24, "away_score": 17,
            "source": GAME, "observed_utc": "2026-09-02T03:50:00Z", "observation_id": native.digest("native-final")}}
        result = resolve_observed([receipt], [key["source"]], [leg], games, observed_utc="2026-09-03T00:00:00Z")
        if result["rejections"] or len(result["observation_proposals"]) != 1 or len(result["proposals"]) != 1:
            raise AssertionError("Native observed fixture failed the unchanged observed-outcome kernel")
        proposal = result["proposals"][0]
        if parent is not None:
            raise ValueError("Native fixture only creates independently resolved initial proposals")
        return result["observation_proposals"][0], {"version": 1, **proposal}
    finally:
        sys.path[:] = previous_path


def batch(observations, revisions):
    return native.canonical({"version": 1, "observations": observations, "revisions": revisions})


class ObservedNativeGuardTests(unittest.TestCase):
    def test_required_gate_missing_configuration_is_not_a_green_skip(self):
        with patch.dict(os.environ, {"SELFLEARN_REQUIRE_NATIVE_PG": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "explicit test database"):
                NativeObservedConcurrencyTests.setUpClass()

    def test_optional_unconfigured_gate_reports_no_native_evidence(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(unittest.SkipTest, "no concurrency proof"):
                NativeObservedConcurrencyTests.setUpClass()

    def test_fixture_is_generated_by_real_source_adapter_and_observed_kernel(self):
        row, context = forecast("stdlib-offline-native-fixture")
        proof, proposal = observed(row, context, "fixture")
        self.assertEqual(proof["measurement"]["canonical_contract"][4], "59.5")
        self.assertEqual(proof["receipt_id"], row["receipt_id"])
        self.assertEqual(proof["measurement"], proposal["resolution"]["measurement"])
        self.assertEqual(proof["observation_id"], native.digest({k: v for k, v in proof.items() if k != "observation_id"}))
        self.assertEqual(proposal["version"], 1)

    def test_cloud_configuration_is_rejected_before_client_or_database_access(self):
        with patch.dict(os.environ, {"SELFLEARN_NATIVE_PG_DSN":
                "postgresql://postgres:test_only@cloud.supabase.co/selflearn_journal_test"}, clear=True), \
                patch("shutil.which", side_effect=AssertionError("must reject host before client lookup")):
            with self.assertRaises(ValueError):
                NativeObservedConcurrencyTests.setUpClass()

    def test_missing_client_is_honest_local_skip_but_fails_required_CI(self):
        env = {"SELFLEARN_NATIVE_PG_DSN":
               "postgresql://postgres:test_only@127.0.0.1/selflearn_journal_test"}
        with patch.dict(os.environ, env, clear=True), patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(unittest.SkipTest, "no concurrency proof"):
                NativeObservedConcurrencyTests.setUpClass()
        with patch.dict(os.environ, {**env, "SELFLEARN_REQUIRE_NATIVE_PG": "1"}, clear=True), \
                patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "psql client"):
                NativeObservedConcurrencyTests.setUpClass()


class NativeObservedConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        uri = os.environ.get("SELFLEARN_NATIVE_PG_DSN")
        if not uri:
            if os.environ.get("SELFLEARN_REQUIRE_NATIVE_PG") == "1":
                raise RuntimeError("Required observed native gate has no explicit test database configured")
            raise unittest.SkipTest("Native PostgreSQL not configured; no concurrency proof was produced")
        env = native.guarded_environment(uri)
        executable = shutil.which("psql")
        if not executable:
            if os.environ.get("SELFLEARN_REQUIRE_NATIVE_PG") == "1":
                raise RuntimeError("Required observed native gate psql client is unavailable")
            raise unittest.SkipTest("Native psql client unavailable; no concurrency proof was produced")
        cls.pg = native.NativePsql(executable, env)
        native.validate_preflight(cls.pg.one("SELECT json_build_object('database',current_database(),"
            "'version',current_setting('server_version_num')::integer,"
            "'schema_exists',to_regnamespace('selflearn_private') IS NOT NULL);"))
        cls.pg.run("""
          DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
          END $$;
        """)
        safe_roles = cls.pg.one("SELECT json_build_object("
            "'service',(SELECT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='service_role'),"
            "'anon',(SELECT NOT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='anon'),"
            "'authenticated',(SELECT NOT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='authenticated'));")
        if safe_roles != {"service": True, "anon": True, "authenticated": True}:
            raise RuntimeError("Observed native fixture roles are unsafe; existing roles were not changed")
        for migration in (native.MIGRATION, ADDITIVE):
            sql = migration.read_text(encoding="utf-8")
            if not sql.strip():
                raise RuntimeError("Observed native gate refuses an empty migration")
            cls.pg.run(sql)

    def rpc(self, operation, parameters, *, expected=None):
        result = self.pg.run("SET ROLE service_role;" + statement(operation, parameters), expected=expected)
        return result.values[0] if result.returncode == 0 else result

    def read(self, view, key=None, watermark=None):
        return self.rpc("read", [view, key, None, 500, watermark])

    def fence(self):
        return self.read("status")["watermark"]

    def receipt(self, suffix):
        row, key = forecast(suffix + "-" + uuid.uuid4().hex)
        self.pg.rpc("append_receipt", [native.canonical(row)])
        return row, key

    def compete(self, first_sql, second_sql, inspect=None):
        first, second = self.pg.session(), self.pg.session()
        try:
            first.send("BEGIN; SET LOCAL ROLE service_role;" + first_sql)
            blocker = first.barrier("observed_write_uncommitted")
            second.send("SET ROLE service_role;")
            waiter = second.barrier("observed_competitor_connected")
            second.send(second_sql)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                blocked = self.pg.one("SELECT json_build_object('blocked'," + str(blocker)
                    + "=ANY(pg_blocking_pids(" + str(waiter) + "))); ")
                if blocked["blocked"]:
                    break
                time.sleep(.02)
            else:
                self.fail("Observed native competitor did not enter the shared transaction-lock wait")
            if inspect:
                inspect()
            first.send("COMMIT;")
            winner, loser = first.finish(), second.finish()
            self.assertEqual(winner.returncode, 0, "First observed native transaction failed")
            return winner, loser
        finally:
            second.abort()
            first.abort()

    def test_10_same_expected_global_fence_has_one_commit_winner_and_no_partial_loser(self):
        row, key = self.receipt("cas")
        a, ar = observed(row, key, "A")
        b, br = observed(row, key, "B", actual=20.0)
        before = self.fence()
        winner, loser = self.compete(statement("commit", [batch([a], [ar]), before]),
                                    statement("commit", [batch([b], [br]), before]))
        self.assertTrue(any(v.get("added_observations") == 1 and v.get("added_revisions") == 1 for v in winner.values))
        self.assertNotEqual(loser.returncode, 0)
        self.assertEqual(loser.sqlstate, "PT409")
        rows = self.read("observation_history", row["receipt_id"])["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(json.loads(rows[0]["payload_json"]), a)
        self.assertEqual(self.read("resolution_history", row["receipt_id"])["rows"][0]["revision_id"], ar["revision_id"])
        retry = self.rpc("commit", [batch([a], [ar]), self.fence()])
        self.assertEqual((retry["added_observations"], retry["added_revisions"]), (0, 0))

    def test_20_uncommitted_observation_cannot_enter_a_pinned_fence_and_legacy_writes_share_lock(self):
        row, key = self.receipt("fence")
        initial, rev = observed(row, key, "initial")
        self.rpc("commit", [batch([initial], [rev]), self.fence()])
        newer, _ = observed(row, key, "unchanged", clock="2026-09-02T06:00:00Z")
        legacy, _ = forecast("legacy-" + uuid.uuid4().hex)
        fences = []

        def inspect():
            fences.append(self.fence())
            self.assertEqual(len(self.read("observation_history", row["receipt_id"], fences[-1])["rows"]), 1)
            self.assertEqual(self.read("receipt", legacy["receipt_id"], fences[-1])["rows"], [])

        _, competitor = self.compete(statement("commit", [batch([newer], []), self.fence()]),
            native.rpc_sql("append_receipt", [native.canonical(legacy)]), inspect)
        self.assertEqual(competitor.returncode, 0)
        self.assertEqual(len(fences), 1)
        self.assertEqual(len(self.read("observation_history", row["receipt_id"], fences[0])["rows"]), 1)
        current = self.read("observation_history", row["receipt_id"])["rows"]
        self.assertEqual(len(current), 2)
        self.assertGreater(int(current[-1]["journal_seq"]), int(fences[0]))
        self.assertEqual(self.read("receipt", legacy["receipt_id"], fences[0])["rows"], [])
        self.assertEqual(len(self.read("receipt", legacy["receipt_id"])["rows"]), 1)

    def test_30_invalid_later_revision_rolls_back_earlier_observation_and_revision(self):
        a, ak = self.receipt("rollback-a")
        b, bk = self.receipt("rollback-b")
        ao, ar = observed(a, ak, "a")
        bo, br = observed(b, bk, "b")
        br["resolution"]["contract_id"] = native.digest(["wrong-contract"])
        before = self.fence()
        self.rpc("commit", [batch([ao, bo], [ar, br]), before], expected="PT422")
        self.assertEqual(self.fence(), before)
        for row in (a, b):
            self.assertEqual(self.read("observation_history", row["receipt_id"])["rows"], [])
            self.assertEqual(self.read("resolution_history", row["receipt_id"])["rows"], [])


if __name__ == "__main__":
    unittest.main()
