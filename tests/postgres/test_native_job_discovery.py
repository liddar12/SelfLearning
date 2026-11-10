"""Real PostgreSQL17 job discovery/claim gate in a fresh loopback database.

No schema adoption, cloud destination or native evidence is inferred from local
WASM tests. CI requires native prerequisites; unconfigured local runs skip the
native class honestly. All operations use separate real psql connections.
"""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch
import uuid

import test_native_concurrency as native
import test_native_observed_concurrency as observed

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "packages/selflearn-core"))
from selflearn_core import learning_jobs as jobs

AUTHORITY = native.digest("native-discovery-authority")
CODE = native.digest("native-current-code")
OPERATION = "bounded_observation"


def statement(action, values):
    names = {"register": "selflearn_jobs_register_v1", "read": "selflearn_jobs_read_v1",
             "append": "selflearn_jobs_append_v1", "discover": "selflearn_jobs_discover_v1"}
    return "SELECT public." + names[action] + "(" + ",".join(native.literal(v) for v in values) + ");"


def command(current, action="claim", *, worker="worker-a", reason=None, object_id=None):
    return jobs.seal_command({"version": 1, "kind": "selflearn.job_command.v1",
        "job_id": current["definition"]["job_id"], "expected_head_event_id": current["head_event_id"],
        "action": action, "worker_id": worker, "lease_id": None if action == "claim" else current["state"]["lease_id"],
        "reason_code": reason, "object_id": object_id}, authority_id=AUTHORITY)


class NativeJobDiscoveryGuardTests(unittest.TestCase):
    def test_required_missing_database_fails_before_any_connection(self):
        with patch.dict(os.environ, {"SELFLEARN_REQUIRE_NATIVE_PG": "1"}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "explicit test database"):
                NativeJobDiscoveryTests.setUpClass()


class NativeJobDiscoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Reuse the unchanged PG17/fresh-schema/loopback/role guards and first
        # two migrations. Apply EVERY later migration in order, including the
        # concurrently reviewed publication extension and discovery RPC.
        observed.NativeObservedConcurrencyTests.setUpClass.__func__(cls)
        for migration in sorted((ROOT / "supabase/migrations").glob("[0-9]*_*.sql")):
            if migration.name <= observed.ADDITIVE.name:
                continue
            sql = migration.read_text(encoding="utf-8")
            if not sql.strip():
                raise RuntimeError("Native discovery gate refuses an empty migration")
            cls.pg.run(sql)

    wait_blocked = native.NativeConcurrencyTests.wait_blocked

    def rpc(self, action, values, expected=None):
        result = self.pg.run("SET ROLE service_role;" + statement(action, values), expected=expected)
        return result.values[0] if result.returncode == 0 else result

    def scope(self):
        return {"domain": "example", "task": "binary.forecast", "producer_id": "native-" + uuid.uuid4().hex}

    def seed(self, scope, **changes):
        now = datetime.now(timezone.utc)
        payload, policy = {"native_input": uuid.uuid4().hex}, {"policy": "native-no-activation"}
        for obj in (payload, policy):
            self.pg.rpc("append_object", ["source_snapshot", native.digest(obj), native.canonical(obj), None])
        spec = jobs.seal_spec({"version": 1, "kind": "selflearn.job_definition.v1",
            "namespace": scope, "operation": OPERATION, "code_id": CODE,
            "input_object_id": native.digest(payload), "policy_object_id": native.digest(policy),
            "not_before_utc": jobs.stamp(now - timedelta(seconds=30)),
            "not_after_utc": jobs.stamp(now + timedelta(minutes=5)), "lease_seconds": 30,
            "max_runtime_seconds": 60, "max_attempts": 3, "max_steps": 4, **changes}, authority_id=AUTHORITY)
        return self.rpc("register", [AUTHORITY, native.canonical(spec)])

    def discover(self, scope, *, code_id=CODE, policy_object_id=None, limit=16, authority_id=AUTHORITY):
        return self.rpc("discover", [authority_id, native.canonical(scope), OPERATION, code_id, policy_object_id, limit])

    def read(self, state):
        return self.rpc("read", [AUTHORITY, state["definition"]["job_id"]])

    def append(self, state, action="claim", **values):
        return self.rpc("append", [AUTHORITY, native.canonical(command(state, action, **values))])

    def test_10_discovered_job_has_one_competing_claim_winner_and_no_partial_loser(self):
        scope = self.scope()
        state = self.seed(scope)
        identity = state["definition"]["job_id"]
        self.assertEqual(self.discover(scope)["job_ids"], [identity])
        first, second = self.pg.session(), self.pg.session()
        try:
            first.send("BEGIN; SET LOCAL ROLE service_role;" + statement("append", [AUTHORITY,
                native.canonical(command(state, worker="winner"))]))
            blocker = first.barrier("native_discovery_claim_uncommitted")
            # MVCC discovery is a hint: it may still list an uncommitted claim
            # without blocking or implying that another worker owns the job.
            self.assertEqual(self.discover(scope)["job_ids"], [identity])
            second.send("SET ROLE service_role;")
            waiter = second.barrier("native_discovery_competitor_connected")
            second.send(statement("append", [AUTHORITY, native.canonical(command(state, worker="loser"))]))
            self.wait_blocked(waiter, blocker)
            first.send("COMMIT;")
            accepted, refused = first.finish(), second.finish()
            self.assertEqual(accepted.returncode, 0)
            self.assertNotEqual(refused.returncode, 0)
            self.assertEqual(refused.sqlstate, "PT409")
        finally:
            second.abort(); first.abort()
        current = self.read(state)
        self.assertEqual(len(current["events"]), 1)
        self.assertEqual(current["state"]["worker_id"], "winner")
        self.assertEqual(current["state"]["attempt"], 1)
        self.assertEqual(self.discover(scope)["job_ids"], [])

    def test_20_expiry_attempt_runtime_and_retry_fairness_use_real_database_clocks(self):
        scope = self.scope()
        older = self.seed(scope)
        untouched = self.seed(scope)
        self.append(older)
        older = self.read(older)
        self.append(older, "release", reason="worker_error")
        self.assertEqual(self.discover(scope, limit=1)["job_ids"], [untouched["definition"]["job_id"]])
        expired_lease = self.seed(scope, lease_seconds=1)
        runtime = self.seed(scope, lease_seconds=1, max_runtime_seconds=1)
        self.append(expired_lease); self.append(runtime)
        exhausted = self.seed(scope, max_attempts=1)
        self.append(exhausted); exhausted = self.read(exhausted)
        self.append(exhausted, "release", reason="worker_error")
        future = self.seed(scope, not_before_utc=jobs.stamp(datetime.now(timezone.utc) + timedelta(minutes=1)))
        expired = self.seed(scope, not_before_utc=jobs.stamp(datetime.now(timezone.utc) - timedelta(minutes=2)),
            not_after_utc=jobs.stamp(datetime.now(timezone.utc) - timedelta(minutes=1)))
        failed = self.seed(scope); self.append(failed); failed = self.read(failed)
        self.append(failed, "fail", reason="invalid_input")
        complete = self.seed(scope); self.append(complete); complete = self.read(complete)
        self.append(complete, "complete", object_id=complete["definition"]["input_object_id"])
        # Wait past the actual persisted lease deadline, not a guessed delay
        # from when Python began creating the fixture.
        lease = self.read(expired_lease)["state"]["lease_until_utc"]
        runtime_deadline = self.read(runtime)["state"]["runtime_deadline_utc"]
        wait_until = max(jobs.clock(lease), jobs.clock(runtime_deadline))
        remaining = (wait_until - datetime.now(timezone.utc)).total_seconds() + .1
        if remaining > 0:
            time.sleep(remaining)
        found = self.discover(scope)
        self.assertEqual(set(found["job_ids"]), {older["definition"]["job_id"],
            untouched["definition"]["job_id"], expired_lease["definition"]["job_id"]})
        self.assertGreaterEqual(jobs.clock(found["observed_utc"]), jobs.clock(lease))
        self.assertEqual(self.read(runtime)["effective_status"], "runtime_limit")
        self.assertNotIn(runtime["definition"]["job_id"], found["job_ids"])

    def test_30_code_policy_filters_find_current_work_beyond_sixteen_incompatible_jobs(self):
        scope = self.scope()
        for _ in range(17):
            self.seed(scope)
        current_policy = {"native_policy": uuid.uuid4().hex}
        policy_id = native.digest(current_policy)
        self.pg.rpc("append_object", ["source_snapshot", policy_id, native.canonical(current_policy), None])
        self.seed(scope, code_id=native.digest("older-worker-code"), policy_object_id=policy_id)
        current = self.seed(scope, policy_object_id=policy_id)
        self.assertNotIn(current["definition"]["job_id"], self.discover(scope)["job_ids"])
        found = self.discover(scope, policy_object_id=policy_id)
        self.assertEqual(found["job_ids"], [current["definition"]["job_id"]])
        self.assertEqual(found["code_id"], CODE)
        self.assertEqual(found["policy_object_id"], policy_id)
        self.assertEqual(self.discover(scope, authority_id=native.digest("other-authority"))["job_ids"], [])

    def test_40_discovery_is_read_only_service_only_bounded_and_security_invoker(self):
        scope = self.scope(); state = self.seed(scope)
        counts = ("SELECT json_build_object('jobs',(SELECT count(*) FROM selflearn_private.learning_jobs_v1),"
            "'events',(SELECT count(*) FROM selflearn_private.learning_job_events_v1),"
            "'receipts',(SELECT count(*) FROM selflearn_private.journal_receipts),"
            "'revisions',(SELECT count(*) FROM selflearn_private.journal_revisions));")
        before = self.pg.one(counts)
        for _ in range(2):
            self.assertEqual(self.discover(scope)["job_ids"], [state["definition"]["job_id"]])
        self.assertEqual(self.pg.one(counts), before)
        parameters = [AUTHORITY, native.canonical(scope), OPERATION, CODE, None, 16]
        for role in ("anon", "authenticated"):
            self.pg.run("SET ROLE " + role + ";" + statement("discover", parameters), expected="42501")
        for limit in (0, 17):
            self.rpc("discover", parameters[:-1] + [limit], expected="PT422")
        metadata = self.pg.one("SELECT json_build_object('definer',prosecdef,'config',proconfig,"
            "'anon',has_function_privilege('anon',oid,'execute'),"
            "'authenticated',has_function_privilege('authenticated',oid,'execute'),"
            "'service',has_function_privilege('service_role',oid,'execute')) FROM pg_catalog.pg_proc "
            "WHERE oid='public.selflearn_jobs_discover_v1(text,text,text,text,text,integer)'::regprocedure;")
        self.assertIs(metadata["definer"], False)
        self.assertIs(metadata["anon"], False); self.assertIs(metadata["authenticated"], False)
        self.assertIs(metadata["service"], True)
        self.assertTrue(any(value in ('search_path=""', 'search_path=') for value in metadata["config"]))


if __name__ == "__main__":
    unittest.main()
