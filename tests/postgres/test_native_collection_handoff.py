"""Opt-in Supabase adapter round-trip on disposable native PostgreSQL 17."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import os
import sys
from threading import Barrier
import unittest

import test_native_concurrency as native
import test_native_publication as publication

sys.path[:0] = [str(native.ROOT), str(native.ROOT / "packages/selflearn-core")]
from services.web.nfl_collection_handoff import status
from selflearn_core.adapters import nfl_collection_handoff as H
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from selflearn_core.store.supabase_store import StoreConflict, StoreError

MIGRATION = native.ROOT / "supabase/migrations/20261119150001_nfl_collection_handoff_v1.sql"
DATABASE = "selflearn_collection_test"
ORIGIN = "https://native-adapter-fixture.example.com"
TOKEN = "local-transport-fixture-key-" + "p" * 40
RPC = "/rest/v1/rpc/"
OPERATIONS = {*native.RPCS.values(), "selflearn_nfl_collection_handoff_v1"}


class NativeCollectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        uri = os.environ.get("SELFLEARN_NATIVE_PG_DSN")
        if not uri:
            if os.environ.get("SELFLEARN_REQUIRE_NATIVE_PG") == "1":
                raise RuntimeError("Required native PostgreSQL gate has no explicit test database configured")
            raise unittest.SkipTest("Native PostgreSQL not configured")
        env = native.guarded_environment(uri, database=DATABASE)
        import psycopg
        cls.driver = psycopg
        cls.connection = {"host": env["PGHOSTADDR"], "port": env["PGPORT"], "dbname": env["PGDATABASE"],
                          "user": env["PGUSER"], "password": env["PGPASSWORD"], "passfile": os.devnull,
                          "sslmode": "disable", "connect_timeout": 5, "options": env["PGOPTIONS"]}
        with cls.driver.connect(**cls.connection, autocommit=True) as db:
            row = db.execute("SELECT current_database(), current_setting('server_version_num')::int, "
                             "to_regnamespace('selflearn_private') IS NOT NULL").fetchone()
            native.validate_preflight(dict(zip(("database", "version", "schema_exists"), row)), database=DATABASE)
            db.execute("""DO $$ BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='anon') THEN CREATE ROLE anon NOLOGIN; END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='service_role') THEN
                    CREATE ROLE service_role NOLOGIN BYPASSRLS;
                END IF;
            END $$;""")
            safe_roles = db.execute("SELECT count(*) FROM pg_roles WHERE NOT rolsuper AND "
                "((rolname IN ('anon','authenticated') AND NOT rolbypassrls) OR "
                "(rolname='service_role' AND rolbypassrls))").fetchone()[0]
            if safe_roles != 3:
                raise RuntimeError("Unsafe native fixture role state")
            db.execute(native.MIGRATION.read_text(encoding="utf-8"))
            db.execute(MIGRATION.read_text(encoding="utf-8"))

    def call_as(self, role, operation, data):
        sql = self.driver.sql
        arguments = [sql.SQL("{} => %s").format(sql.Identifier(name)) for name in data]
        query = sql.SQL("SELECT public.{}({})").format(sql.Identifier(operation), sql.SQL(",").join(arguments))
        with self.driver.connect(**self.connection, autocommit=True) as db:
            db.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            return db.execute(query, tuple(data.values())).fetchone()[0]

    def transport(self, method, path, *, data):
        if method != "POST" or not path.startswith(RPC) or path[len(RPC):] not in OPERATIONS:
            raise AssertionError("unexpected hosted adapter request")
        try:
            return self.call_as("service_role", path[len(RPC):], data)
        except self.driver.Error as error:
            if error.sqlstate == "PT409":
                raise StoreConflict() from None
            if error.sqlstate == "PT422":
                raise StoreError(422) from None
            raise AssertionError(f"unexpected native SQLSTATE {error.sqlstate}: {error}") from error

    def ports(self, repository):
        journal = SupabaseReceiptJournal(ORIGIN, TOKEN, transport=self.transport)
        return H.SupabaseCollectionHandoff(journal, repository), journal

    def fixture(self, repository, *, run="222", kickoff=None):
        authority, generation, _ = publication.publication_fixture(repository,
            datetime.now(timezone.utc), kickoff=kickoff)
        gen = generation["generations"][0]
        request = {"version": 1, "workflow": "daily.yml", "run_id": run, "run_attempt": "1",
                   "generation_object_id": generation["envelope_id"], "generation_id": gen["generation_id"],
                   "recorded_utc": gen["recorded_utc"], "source_snapshots": gen["source_snapshots"],
                   "latest_kickoff_utc": generation["receipts"][0]["kickoff_utc"]}
        return authority.objects, request

    def test_native_staged_objects_recover_and_finalize_after_lost_reply(self):
        repository = "fixture/native-recover"
        key = ("daily.yml", "222", "1")
        objects, request = self.fixture(repository)
        intent = H.intent_record(repository, request)
        store, journal = self.ports(repository)
        missing = {identity for identity in objects if journal.get_object(identity) is None}
        staged = store.put(key, "intent", intent)
        self.assertEqual(set(staged["missing_object_ids"]), missing)
        self.assertEqual(store.pending(16, None)["pending"], [])
        for identity, obj in objects.items():
            self.assertEqual(journal.append_object(obj["kind"], identity, obj["payload"]), identity in missing)
        store, journal = self.ports(repository)
        self.assertEqual(store.put(key, "intent", intent)["intent"], staged["intent"])
        self.assertEqual(store.pending(16, None)["pending"], [H.locator(intent)])
        self.assertEqual(status(store, journal, key)["state"], "ready_to_finalize")
        first = status(store, journal, key, recover_id=intent["intent_id"])
        self.assertEqual(first["state"], "awaiting_publisher")
        self.assertTrue(first["intake_complete"])
        store, journal = self.ports(repository)
        self.assertEqual(status(store, journal, key, recover_id=intent["intent_id"])["generation_ack"], first["generation_ack"])
        self.assertEqual(store.pending(16, None)["pending"], [H.locator(intent)])
        req = {"version": 1, "intent_id": intent["intent_id"], "published_sha": "2" * 40,
               "published_pool_id": intent["source_snapshots"]["leg_pool"], "previous_pool_id": None}
        publisher = H.publisher_record(intent, req)
        recorded = store.put(key, "publisher", publisher)["publisher_receipt"]
        fresh = status(store, journal, key)
        self.assertTrue(fresh["publication_ready"])
        self.assertFalse(fresh["publication_verified"])
        self.assertEqual(fresh["publisher_receipt"], recorded)
        self.assertEqual(store.pending(16, None)["pending"], [H.locator(intent)])
        self.assertEqual(store.put(key, "publisher", publisher)["publisher_receipt"], recorded)
        with self.assertRaises(JournalConflict):
            store.put(key, "publisher", H.publisher_record(intent, {**req, "published_sha": "3" * 40}))
        with self.assertRaises(JournalConflict):
            store.put(key, "intent", H.intent_record(repository, {**request, "generation_id": fingerprint("changed")}))

    def test_native_concurrent_original_write_has_one_winner(self):
        repository = "fixture/native-concurrent"
        key = ("daily.yml", "222", "1")
        _, request = self.fixture(repository)
        barrier = Barrier(2)

        def add(label):
            store, _ = self.ports(repository)
            intent = H.intent_record(repository, {**request, "generation_id": fingerprint(label)})
            barrier.wait(timeout=5)
            try:
                return store.put(key, "intent", intent)["intent"]["intent_id"]
            except JournalConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(add, ("one", "two")))
        self.assertEqual(results.count("conflict"), 1)
        store, _ = self.ports(repository)
        self.assertIn(store.read(key)["intent"]["intent_id"], results)

    def test_native_pending_cursor_and_late_mismatched_publisher(self):
        repository = "fixture/native-late"
        key = ("daily.yml", "222", "1")
        kickoff = publication.clock(datetime.now(timezone.utc) - timedelta(seconds=10))
        objects, request = self.fixture(repository, kickoff=kickoff)
        intent = H.intent_record(repository, request)
        store, journal = self.ports(repository)
        store.put(key, "intent", intent)
        for identity, obj in objects.items():
            journal.append_object(obj["kind"], identity, obj["payload"])
        for run in ("223", "224"):
            missing = H.intent_record(repository, {**request, "run_id": run, "generation_object_id": fingerprint(run)})
            store.put(("daily.yml", run, "1"), "intent", missing)
        publisher = H.publisher_record(intent, {"version": 1, "intent_id": intent["intent_id"],
            "published_sha": "4" * 40, "published_pool_id": fingerprint("mismatch"), "previous_pool_id": None})
        self.assertEqual(store.put(key, "publisher", publisher)["publisher_receipt"]["disposition"], "mismatch")
        late = status(store, journal, key, recover_id=intent["intent_id"])
        self.assertEqual(late["state"], "held_late")
        self.assertIsNone(late["generation_ack"])
        after = None
        for _ in range(3):
            page = store.pending(1, after)
            self.assertEqual(page["pending"], [])
            after = page["next_after"]
        self.assertIsNone(after)

    def test_native_table_update_and_unscoped_rpc_grants_are_refused(self):
        name = "selflearn_nfl_collection_handoff_v1"
        data = {"p_operation": "pending", "p_repository": "fixture/native-auth", "p_workflow": None,
                "p_run_id": None, "p_run_attempt": None, "p_document_json": None, "p_limit": 1, "p_after": None}
        for role in ("anon", "authenticated"):
            with self.assertRaises(self.driver.Error) as refused:
                self.call_as(role, name, data)
            self.assertEqual(refused.exception.sqlstate, "42501")
        with self.driver.connect(**self.connection, autocommit=True) as db:
            db.execute("SET ROLE service_role")
            with self.assertRaises(self.driver.Error) as refused:
                db.execute("UPDATE selflearn_private.nfl_collection_handoff_v1 SET document_json = '{}' WHERE false")
            self.assertEqual(refused.exception.sqlstate, "42501")


if __name__ == "__main__":
    unittest.main()
