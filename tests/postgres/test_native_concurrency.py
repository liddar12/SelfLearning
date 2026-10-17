"""Opt-in native PostgreSQL 17 transaction gate; Python standard library only.

The URI is accepted only for an explicit loopback disposable test database.
There is no Docker startup, package installation, cloud access or production
credential discovery here. Missing opt-in skips honestly unless CI requires it.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import subprocess
import time
import unittest
from unittest.mock import patch
from urllib.parse import unquote, urlsplit
import uuid


DATABASE = "selflearn_journal_test"
ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261015222425_receipt_journal_v1.sql"
RPCS = {
    "append_receipt": "selflearn_journal_append_receipt_v1",
    "append_object": "selflearn_journal_append_object_v1",
    "generation": "selflearn_journal_commit_generation_v1",
    "resolution": "selflearn_journal_append_resolution_v1",
    "read": "selflearn_journal_read_v1",
}


def guarded_environment(uri):
    """Build a non-redirectable connection environment without logging a URI.

    Ambient PGSERVICE/PGHOSTADDR/PGOPTIONS/PGPASSFILE must not redirect a test
    toward another cluster. The original URI and password never enter argv.
    """
    message = "Native tests require a loopback PostgreSQL URI for the disposable selflearn_journal_test database"
    try:
        parsed = urlsplit(uri)
        if (not isinstance(uri, str) or any(ch.isspace() for ch in uri)
                or parsed.scheme not in ("postgres", "postgresql")
                or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
                or parsed.path != "/" + DATABASE or parsed.query or parsed.fragment
                or unquote(parsed.username or "") != "postgres"
                or not parsed.password):
            raise ValueError(message)
        port = 5432 if parsed.port is None else parsed.port
        password = unquote(parsed.password)
        if not 1 <= port <= 65535 or any(ord(ch) < 32 for ch in password):
            raise ValueError(message)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(message) from None
    env = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "TZ", "TMPDIR") if key in os.environ}
    host = parsed.hostname
    env.update({
        "PGHOST": host,
        "PGHOSTADDR": "::1" if host == "::1" else "127.0.0.1",
        "PGPORT": str(port), "PGDATABASE": DATABASE,
        "PGUSER": "postgres", "PGPASSWORD": password,
        "PGPASSFILE": os.devnull, "PGSSLMODE": "disable", "PGCONNECT_TIMEOUT": "5",
        "PGAPPNAME": "selflearn_native_gate",
        "PGOPTIONS": ("-c statement_timeout=10000 -c lock_timeout=8000 "
                      "-c idle_in_transaction_session_timeout=10000 "
                      "-c standard_conforming_strings=on -c timezone=UTC"),
    })
    return env


def validate_preflight(value):
    # Client destination safety is enforced by guarded_environment/PGHOSTADDR.
    # inet_server_addr() is the server's own endpoint, not the client target:
    # GitHub's loopback-published Docker service legitimately reports 172.x.
    # Rejecting that address would break the intended disposable CI topology.
    if (value.get("database") != DATABASE or type(value.get("version")) is not int
            or not 170000 <= value["version"] < 180000
            or value.get("schema_exists") is not False):
        raise RuntimeError("Native gate requires a fresh disposable loopback PostgreSQL 17 database; existing schema was not changed")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return "sha256:" + hashlib.sha256(canonical(value).encode()).hexdigest()


def forecast(label, **extra):
    value = {
        "version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
        "contract_id": digest(["contract", label]),
        "source_receipt_id": digest(["source", label]),
        "source_snapshot_id": digest(["snapshot", label]),
        "event_id": "fixture-" + label, "correlation_group": "fixture-" + label,
        "base_model_version": "fixture-base", "calibrator_version": "identity",
        "base_prob": .6, "served_prob": .7, "features": {"fixture": True, "x": 1.0},
        "forecast_created_utc": "2026-09-01T12:00:00Z", "first_seen_utc": "2026-09-01T12:01:00Z",
        "event_start_utc": "2026-09-02T00:15:00Z",
        "source_timestamps": {"fixture": "2026-09-01T11:00:00Z"},
        "publication_status": "generated_unverified", **extra,
    }
    value.pop("receipt_id", None)
    return {**value, "receipt_id": digest(value)}


def outcome(receipt, hit=True):
    return {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
            "event_id": receipt["event_id"], "status": "FINAL", "hit": hit,
            "source": "explicit native test fixture", "resolved_utc": "2026-09-02T04:15:00Z"}


def revision(receipt, revision_id, *, supersedes=None, hit=True):
    return {"version": 1, "revision_id": revision_id, "supersedes": supersedes,
            "reason": "official fixture correction" if supersedes else None,
            "resolution": outcome(receipt, hit)}


def generation(receipts):
    unsigned = {"version": 1, "receipts": [{"receipt_id": row["source_receipt_id"]} for row in receipts]}
    return {**unsigned, "envelope_id": digest(unsigned)}


def literal(value):
    # All values are data literals, never SQL identifiers. Every test connection
    # explicitly enables standard_conforming_strings, so backslashes are data.
    if value is None:
        return "NULL"
    if type(value) is int:
        return str(value)
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("Unsupported native test SQL parameter")
    return "'" + value.replace("'", "''") + "'"


def rpc_sql(operation, parameters):
    name = RPCS[operation]
    return "SELECT public." + name + "(" + ",".join(literal(value) for value in parameters) + ");"


def documents(output):
    return [json.loads(line) for line in output.decode().splitlines() if line.strip()]


@dataclass
class Result:
    returncode: int
    values: list
    sqlstate: str | None


class NativePsql:
    def __init__(self, executable, env):
        self.command = [executable, "-X", "-A", "-t", "-q", "-w",
                        "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=sqlstate"]
        self.env = env

    @staticmethod
    def result(code, output, error):
        match = re.search(rb"ERROR:\s+([0-9A-Z]{5})\b", error)
        return Result(code, documents(output), match.group(1).decode() if match else None)

    def run(self, sql, *, expected=None):
        try:
            process = subprocess.run(self.command, input=sql.encode(), env=self.env,
                                     capture_output=True, timeout=12, check=False)
        except (subprocess.TimeoutExpired, OSError):
            raise AssertionError("Native PostgreSQL command unavailable or timed out") from None
        result = self.result(process.returncode, process.stdout, process.stderr)
        if expected is None:
            if result.returncode:
                raise AssertionError("Native PostgreSQL command failed; SQLSTATE=" + str(result.sqlstate))
        elif result.returncode == 0 or result.sqlstate != expected:
            raise AssertionError("Expected native SQLSTATE " + expected + "; received " + str(result.sqlstate))
        return result

    def one(self, sql):
        result = self.run(sql)
        if len(result.values) != 1:
            raise AssertionError("Native PostgreSQL did not return one JSON result")
        return result.values[0]

    def rpc(self, operation, parameters, *, expected=None):
        result = self.run("SET ROLE service_role;" + rpc_sql(operation, parameters), expected=expected)
        return result.values[0] if result.returncode == 0 else result

    def session(self):
        return Session(self)


class Session:
    """Two real psql connections with explicit JSON barriers, not timing guesses."""
    def __init__(self, client):
        self.client = client
        self.process = subprocess.Popen(client.command, env=client.env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
        self.buffer = bytearray()
        self.seen = []

    def send(self, sql):
        if self.process.stdin is None:
            raise AssertionError("Native PostgreSQL test session is already closed")
        self.process.stdin.write((sql + "\n").encode())
        self.process.stdin.flush()

    def barrier(self, name):
        self.send("SELECT json_build_object('barrier'," + literal(name) + ",'pid',pg_backend_pid());")
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if b"\n" not in self.buffer:
                remaining = deadline - time.monotonic()
                if self.process.stdout is None or not select.select([self.process.stdout], [], [], max(0, remaining))[0]:
                    break
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.buffer.extend(chunk)
                continue
            line, _, tail = self.buffer.partition(b"\n")
            self.buffer = bytearray(tail)
            if not line.strip():
                continue
            value = json.loads(line)
            self.seen.append(value)
            if isinstance(value, dict) and value.get("barrier") == name:
                return value["pid"]
        raise AssertionError("Native PostgreSQL JSON result barrier did not arrive")

    def finish(self):
        if self.process.stdin is not None:
            self.process.stdin.close()
            self.process.stdin = None
        try:
            output, error = self.process.communicate(timeout=12)
        except subprocess.TimeoutExpired:
            self.abort()
            raise AssertionError("Native PostgreSQL test session timed out") from None
        result = self.client.result(self.process.returncode, bytes(self.buffer) + output, error)
        result.values = self.seen + result.values
        self.buffer.clear()
        return result

    def abort(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class DSNGuardTests(unittest.TestCase):
    def test_only_exact_loopback_disposable_database_is_allowed(self):
        for host in ("localhost", "127.0.0.1", "[::1]"):
            with self.subTest(host=host):
                env = guarded_environment("postgresql://postgres:test_fixture_only@" + host + ":5432/" + DATABASE)
                self.assertEqual(env["PGDATABASE"], DATABASE)
                self.assertIn(env["PGHOSTADDR"], ("127.0.0.1", "::1"))
                self.assertNotIn("PGSERVICE", env)
                self.assertEqual(env["PGPASSFILE"], os.devnull)

    def test_cloud_hosts_ambiguous_database_and_connection_overrides_fail_closed(self):
        for uri in (
            "postgresql://postgres:fixture@cloud.supabase.co/" + DATABASE,
            "postgresql://postgres:fixture@127.0.0.1/production",
            "postgresql://postgres:fixture@localhost./" + DATABASE,
            "postgresql://postgres:fixture@127.0.0.1/" + DATABASE + "?host=cloud.example",
            "postgresql://postgres:fixture@127.0.0.1/" + DATABASE + "#fragment",
            "postgresql://app:fixture@127.0.0.1/" + DATABASE,
            "postgresql://postgres@127.0.0.1/" + DATABASE,
            "postgresql:///" + DATABASE,
            "postgresql://postgres:fixture@127.0.0.1:0/" + DATABASE,
            "postgresql://postgres:fixture@127.0.0.1:99999/" + DATABASE,
            "host=127.0.0.1 dbname=" + DATABASE,
        ):
            with self.subTest(case=uri.split("@")[-1]):
                with self.assertRaises(ValueError):
                    guarded_environment(uri)

    def test_required_native_gate_cannot_turn_missing_configuration_into_a_skip(self):
        with patch.dict(os.environ, {"SELFLEARN_REQUIRE_NATIVE_PG": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "no explicit test database"):
                NativeConcurrencyTests.setUpClass()

    def test_optional_unconfigured_native_gate_is_an_explicit_skip(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(unittest.SkipTest, "no concurrency proof"):
                NativeConcurrencyTests.setUpClass()

    def test_configured_gate_without_psql_is_a_failure_not_a_skip(self):
        env = {"SELFLEARN_REQUIRE_NATIVE_PG": "1",
               "SELFLEARN_NATIVE_PG_DSN": "postgresql://postgres:fixture@127.0.0.1/" + DATABASE}
        with patch.dict(os.environ, env, clear=True), patch("shutil.which", return_value=None):
            with self.assertRaisesRegex(RuntimeError, "psql client is unavailable"):
                NativeConcurrencyTests.setUpClass()

    def test_loopback_published_docker_server_address_is_not_the_client_destination(self):
        env = guarded_environment("postgresql://postgres:fixture@127.0.0.1/" + DATABASE)
        self.assertEqual(env["PGHOSTADDR"], "127.0.0.1")
        validate_preflight({"database": DATABASE, "version": 170005,
                            "address": "172.18.0.2", "schema_exists": False})

    def test_container_topology_does_not_relax_database_version_or_freshness_guards(self):
        valid = {"database": DATABASE, "version": 170005,
                 "address": "172.18.0.2", "schema_exists": False}
        for change in ({"database": "production"}, {"version": 160005},
                       {"version": True}, {"schema_exists": True}, {"schema_exists": None}):
            with self.subTest(change=change):
                with self.assertRaisesRegex(RuntimeError, "fresh disposable"):
                    validate_preflight({**valid, **change})


class NativeConcurrencyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        uri = os.environ.get("SELFLEARN_NATIVE_PG_DSN")
        if not uri:
            if os.environ.get("SELFLEARN_REQUIRE_NATIVE_PG") == "1":
                raise RuntimeError("Required native PostgreSQL gate has no explicit test database configured")
            raise unittest.SkipTest("Native PostgreSQL not configured; no concurrency proof was produced")
        env = guarded_environment(uri)
        executable = shutil.which("psql")
        if not executable:
            raise RuntimeError("Native PostgreSQL was configured but the required psql client is unavailable")
        cls.pg = NativePsql(executable, env)
        preflight = cls.pg.one("SELECT json_build_object('database',current_database(),'version',current_setting('server_version_num')::integer,"
                               "'address',inet_server_addr()::text,'schema_exists',to_regnamespace('selflearn_private') IS NOT NULL);")
        validate_preflight(preflight)
        cls.pg.run("""
          DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
          END $$;
        """)
        roles = cls.pg.one("SELECT json_build_object('service',(SELECT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='service_role'),"
                           "'anon',(SELECT NOT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='anon'),"
                           "'authenticated',(SELECT NOT rolbypassrls AND NOT rolsuper FROM pg_roles WHERE rolname='authenticated'));")
        if roles != {"service": True, "anon": True, "authenticated": True}:
            raise RuntimeError("Native fixture roles are unsafe; existing roles were not altered")
        sql = MIGRATION.read_text()
        if not sql.strip():
            raise RuntimeError("Receipt journal migration is empty")
        cls.pg.run(sql)

    def label(self, suffix):
        return suffix + "-" + uuid.uuid4().hex

    def read(self, view, key=None, watermark=None):
        return self.pg.rpc("read", [view, key, None, 500, watermark])

    def append(self, receipt):
        return self.pg.rpc("append_receipt", [canonical(receipt)])

    def wait_blocked(self, waiter, blocker):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = self.pg.one("SELECT json_build_object('blocked'," + str(blocker) + "=ANY(pg_blocking_pids(" + str(waiter) + ")));")
            if result["blocked"] is True:
                return
            time.sleep(.02)
        self.fail("Competing native writer never entered the expected transaction lock wait")

    def hold_then_compete(self, first_operation, first_params, second_operation, second_params, inspect=None):
        first, second = self.pg.session(), self.pg.session()
        try:
            first.send("BEGIN; SET LOCAL ROLE service_role;" + rpc_sql(first_operation, first_params))
            first_pid = first.barrier("first_write_is_uncommitted")
            second.send("SET ROLE service_role;")
            second_pid = second.barrier("competitor_connected")
            second.send(rpc_sql(second_operation, second_params))
            self.wait_blocked(second_pid, first_pid)
            if inspect is not None:
                inspect()
            first.send("COMMIT;")
            first_result = first.finish()
            second_result = second.finish()
            self.assertEqual(first_result.returncode, 0, "First native writer failed")
            return first_result, second_result
        finally:
            second.abort()
            first.abort()

    def test_10_competing_source_identity_has_one_winner_and_exact_retry_is_idempotent(self):
        for changed in (False, True):
            label = self.label("source-identity")
            original = forecast(label)
            competing = forecast(label, first_seen_utc="2026-09-01T12:02:00Z") if changed else original
            first, second = self.hold_then_compete("append_receipt", [canonical(original)],
                                                  "append_receipt", [canonical(competing)])
            self.assertIn({"version": 1, "added": True}, first.values)
            if changed:
                self.assertNotEqual(second.returncode, 0)
                self.assertEqual(second.sqlstate, "PT409")
                self.assertEqual(self.read("receipt", competing["receipt_id"])["rows"], [])
            else:
                self.assertEqual(second.returncode, 0)
                self.assertIn({"version": 1, "added": False}, second.values)
            stored = self.read("receipt", original["receipt_id"])["rows"]
            self.assertEqual(len(stored), 1)
            self.assertEqual(stored[0]["payload_json"], canonical(original))
            self.assertEqual(self.append(original), {"version": 1, "added": False})

    def test_20_competing_corrections_cannot_fork_one_predecessor(self):
        receipt = forecast(self.label("correction"))
        self.append(receipt)
        first_id, winner_id, loser_id = [self.label(name) for name in ("initial", "winner", "loser")]
        initial = revision(receipt, first_id)
        self.pg.rpc("resolution", [canonical(initial)])
        winner = revision(receipt, winner_id, supersedes=first_id, hit=False)
        loser = revision(receipt, loser_id, supersedes=first_id, hit=True)
        first, second = self.hold_then_compete("resolution", [canonical(winner)], "resolution", [canonical(loser)])
        self.assertIn({"version": 1, "added": True}, first.values)
        self.assertNotEqual(second.returncode, 0)
        self.assertEqual(second.sqlstate, "PT409")
        history = [json.loads(row["payload_json"]) for row in self.read("resolution_history", receipt["receipt_id"])["rows"]]
        self.assertEqual([row["revision_id"] for row in history], [first_id, winner_id])
        self.assertEqual(self.pg.rpc("resolution", [canonical(winner)]), {"version": 1, "added": False})

    def test_30_generation_receipt_conflict_rolls_back_all_partial_inserts(self):
        a, b = forecast(self.label("generation-new")), forecast(self.label("generation-existing"))
        self.append(b)
        changed = {k: v for k, v in b.items() if k != "receipt_id"}
        changed["first_seen_utc"] = "2026-09-01T12:02:00Z"
        changed["receipt_id"] = digest(changed)
        document = generation([a, changed])
        unsigned = {k: v for k, v in document.items() if k != "envelope_id"}
        self.pg.rpc("append_object", ["generation", document["envelope_id"], canonical(document), canonical(unsigned)])
        self.pg.rpc("generation", [document["envelope_id"], canonical([a, changed]), "[]"], expected="PT409")
        self.assertEqual(self.read("receipt", a["receipt_id"])["rows"], [])
        self.assertEqual(self.read("generation_ack", document["envelope_id"])["rows"], [])
        self.assertEqual(self.read("receipt", b["receipt_id"])["rows"][0]["payload_json"], canonical(b))
        ack = self.pg.rpc("generation", [document["envelope_id"], canonical([a, b]), "[]"])
        self.assertTrue(ack["durable"])
        self.assertEqual([row["status"] for row in ack["receipts"]], ["recorded", "duplicate"])

    def test_40_uncommitted_lower_sequence_cannot_arrive_under_an_existing_read_watermark(self):
        a, b = forecast(self.label("watermark-first")), forecast(self.label("watermark-second"))
        fences = []

        def inspect_uncommitted():
            fence = self.read("status")["watermark"]
            fences.append(fence)
            for receipt in (a, b):
                self.assertEqual(self.read("receipt", receipt["receipt_id"], fence)["rows"], [])

        first, second = self.hold_then_compete("append_receipt", [canonical(a)],
            "append_receipt", [canonical(b)], inspect=inspect_uncommitted)
        self.assertIn({"version": 1, "added": True}, first.values)
        self.assertEqual(second.returncode, 0)
        self.assertIn({"version": 1, "added": True}, second.values)
        self.assertEqual(len(fences), 1)
        for receipt in (a, b):
            # The same committed fence cannot gain a late earlier insertion.
            self.assertEqual(self.read("receipt", receipt["receipt_id"], fences[0])["rows"], [])
            current = self.read("receipt", receipt["receipt_id"])["rows"]
            self.assertEqual(len(current), 1)
            self.assertGreater(int(current[0]["journal_seq"]), int(fences[0]))
        a_seq = int(self.read("receipt", a["receipt_id"])["rows"][0]["journal_seq"])
        b_seq = int(self.read("receipt", b["receipt_id"])["rows"][0]["journal_seq"])
        self.assertLess(a_seq, b_seq)


if __name__ == "__main__":
    unittest.main(verbosity=2)
