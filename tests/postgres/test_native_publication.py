"""Native PostgreSQL17 publication transactions; no cloud credentials/writes.

This suite needs the same explicit fresh loopback DSN as the existing native
gates. Missing prerequisites skip locally and FAIL when CI requires them.
Scripted adapter tests are separate and cannot stand in for these tests.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
import uuid

import test_native_concurrency as native
import test_native_observed_concurrency as observed

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "supabase/migrations/20261110011758_publication_attestations_v1.sql"
sys.path[:0] = [str(ROOT / "packages/selflearn-core"), str(ROOT / "packages/selflearn-core/tests")]
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_generation import LEG_KEYS, MANIFEST_KEYS, validate_generation
from selflearn_core.adapters.nfl_publication import build_publication_attestation
from test_nfl_adapter import fixture


def clock(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class FrozenJournal:
    def __init__(self, objects, receipts, ack):
        self.objects, self.rows, self.ack = objects, receipts, ack

    def get_object(self, key):
        return self.objects.get(key)

    def receipts(self):
        return self.rows

    def publication_generation_receipts(self, _):
        accepted = {r["receipt_id"] for r in self.ack["receipts"]}
        return [r for r in self.rows if r["receipt_id"] in accepted]

    def generation_ack(self, _):
        return self.ack


def publication_fixture(label, now, kickoff=None, count=1):
    """Independent synthetic source passed through actual NFL/core validators."""
    if type(count) is not int or not 1 <= count <= 1000:
        raise ValueError("Synthetic fixture count must be 1..1000")
    created, captured = clock(now - timedelta(minutes=3)), clock(now - timedelta(minutes=2))
    kickoff = kickoff or clock(now + timedelta(minutes=20))
    legs = []
    for i in range(count):
        doc, _, _ = fixture(player=f"{label}-{i}")
        leg = copy.deepcopy(doc["cards"][0]["legs"][0])
        source = leg.pop("learning_receipt")
        source.update(forecast_created_utc=created, source_timestamps={"weekly": created})
        # Valid frozen metadata: every SQL fixture preserves these types/lexemes.
        source["features"]["numeric_metadata"] = {"negative_zero": -0.0, "integer_float": 1.0,
                                                   "large_float": 1e20, "tiny_float": 1e-20}
        source["receipt_id"] = native.digest({k: v for k, v in source.items() if k != "receipt_id"})
        leg["learning_receipt"] = source
        legs.append(leg)
    pool = {"season": 2026, "week": 6, "generated_utc": created,
            "base_model_version": source["base_model_version"], "players": [{"rungs": [leg]} for leg in legs]}
    schedule = {"season": 2026, "games": [{"game_id": leg["game_id"], "week": 6,
                "status": "STATUS_SCHEDULED", "kickoff_utc": kickoff}]}
    snapshots = {"leg_pool": native.digest(pool), "schedule": native.digest(schedule),
                 "parlay_calibration": native.digest(None)}
    gen = {"version": 1, "season": 2026, "week": 6, "pool_generated_utc": created,
        "source_snapshots": snapshots, "offered_cards": [],
        "selection_policy_version": native.digest("policy"), "joint_model_version": native.digest("joint"),
        "recorded_utc": captured, "receipt_ids": sorted(leg["learning_receipt"]["receipt_id"] for leg in legs),
        "publication_status": "generated_unverified", "population": "eligible_pool_prop_rungs_before_display_dedupe"}
    gen["generation_id"] = native.digest({k: gen[k] for k in MANIFEST_KEYS})
    rows = [{"receipt_id": leg["learning_receipt"]["receipt_id"],
        "leg": {k: v for k, v in leg.items() if k in LEG_KEYS},
        "learning_receipt": leg["learning_receipt"], "generation_id": gen["generation_id"], "recorded_utc": captured,
        "kickoff_utc": kickoff, "publication_status": "generated_unverified"} for leg in legs]
    generation = {"version": 1, "season": 2026, "week": 6, "generations": [gen], "receipts": rows}
    generation["envelope_id"] = native.digest(generation)
    objects = {identity: {"object_id": identity, "kind": "source_snapshot", "payload": payload}
               for identity, payload in zip(snapshots.values(), [pool, schedule, None])}
    objects[generation["envelope_id"]] = {"object_id": generation["envelope_id"], "kind": "generation", "payload": generation}
    adapted = adapt_prospective(journal_docs=[generation])
    if adapted["rejections"] or len(adapted["receipts"]) != count:
        raise AssertionError("Native publication fixture must pass actual NFL adapter")
    ack = {"version": 1, "object_id": generation["envelope_id"], "durable": True,
        "receipts": [{"source_receipt_id": r["source_receipt_id"], "receipt_id": r["receipt_id"], "status": "recorded"}
                     for r in adapted["receipts"]], "rejections": []}
    journal = FrozenJournal(objects, adapted["receipts"], ack)
    validate_generation(journal, generation["envelope_id"], now_utc=clock(now))
    observation = {"version": 1, "policy": {"version": 1, "kind": "nfl.pool_http_publication.v1",
        "origin": "https://nfl.example.com", "path": "/data/leg_pool.json"},
        "observed_started_utc": clock(now - timedelta(seconds=20)),
        "observed_completed_utc": clock(now - timedelta(seconds=10)), "http_status": 200,
        "body_utf8": json.dumps(pool, indent=2, ensure_ascii=False) + "\n"}
    return journal, generation, observation


def statement(object_id, observation):
    return "SELECT public.selflearn_journal_commit_publication_v1(" + native.literal(object_id) + "," + native.literal(observation) + ");"


class PublicationFixtureTests(unittest.TestCase):
    def test_fixture_passes_actual_generation_adapter_and_publication_join_without_SQL(self):
        now = datetime.now(timezone.utc)
        journal, doc, obs = publication_fixture("fixture-only", now)
        result = build_publication_attestation(journal, doc["envelope_id"], obs, clock(now))
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(len(result["receipts"]), 1)


class NativePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The existing harness refuses cloud hosts and existing schemas before
        # writes, applies the two prerequisite migrations, and checks PG17.
        observed.NativeObservedConcurrencyTests.setUpClass.__func__(cls)
        cls.pg.run(MIGRATION.read_text(encoding="utf-8"))

    compete = observed.NativeObservedConcurrencyTests.compete

    def setup_fixture(self, kickoff=None):
        now = datetime.now(timezone.utc)
        journal, doc, obs = publication_fixture(uuid.uuid4().hex, now, kickoff)
        for object_id, obj in journal.objects.items():
            self.pg.rpc("append_object", [obj["kind"], object_id, native.canonical(obj["payload"]), None])
        journal.ack = self.pg.rpc("generation", [doc["envelope_id"], native.canonical(journal.rows), "[]"])
        return journal, doc, obs

    def commit(self, doc, observation, expected=None):
        result = self.pg.run("SET ROLE service_role;" + statement(doc["envelope_id"], native.canonical(observation)), expected=expected)
        return result.values[0] if not result.returncode else result

    def read(self, view, key=None, watermark=None):
        parameters = [view, key, None, 1 if view in ("generations", "publication_attestation", "publication_attestations") else 500, watermark]
        sql = "SELECT public.selflearn_journal_read_publication_v1(" + ",".join(native.literal(p) for p in parameters) + ");"
        return self.pg.one("SET ROLE service_role;" + sql)

    def test_10_actual_DB_result_matches_pure_join_and_retains_exact_raw_body_and_ACK(self):
        journal, doc, obs = self.setup_fixture()
        answer = self.commit(doc, obs)
        record = answer["attestation"]
        self.assertFalse(answer["replayed"])
        self.assertEqual(record, build_publication_attestation(journal, doc["envelope_id"], obs, record["recorded_utc"]))
        stored = self.pg.one("SELECT json_build_object('observation',observation_json::json,'ack',ack_json::json) "
            "FROM selflearn_private.journal_publication_attestations WHERE object_id=" + native.literal(doc["envelope_id"]) + ";")
        self.assertEqual(stored, {"observation": obs, "ack": journal.ack})
        self.assertEqual(record["observation"]["body_sha256"], "sha256:" + hashlib.sha256(obs["body_utf8"].encode()).hexdigest())
        unrelated = native.forecast('outside-publication-membership-' + uuid.uuid4().hex)
        self.pg.rpc("append_receipt", [native.canonical(unrelated)])
        bounded = self.read("publication_generation_receipts", doc["envelope_id"])
        self.assertEqual({row["receipt_id"] for row in bounded["rows"]}, {row["receipt_id"] for row in journal.rows})
        replay = self.pg.one("SET ROLE service_role;" + statement(doc["envelope_id"], "malformed new evidence"))
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["attestation"], record)

    def test_20_two_connections_replay_original_and_pinned_snapshot_excludes_uncommitted_evidence(self):
        _, doc, obs = self.setup_fixture()
        before = self.read("receipts")["watermark"]
        fences = []
        def inspect():
            fences.append(self.read("receipts")["watermark"])
            self.assertEqual(self.read("publication_attestation", doc["envelope_id"], before)["rows"], [])
        winner, replay = self.compete(statement(doc["envelope_id"], native.canonical(obs)),
            statement(doc["envelope_id"], "not JSON"), inspect)
        self.assertEqual(replay.returncode, 0)
        first = next(v for v in winner.values if "attestation" in v)
        second = next(v for v in replay.values if "attestation" in v)
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        self.assertEqual(first["attestation"], second["attestation"])
        self.assertEqual(fences, [before])
        self.assertEqual(self.read("publication_attestation", doc["envelope_id"], before)["rows"], [])

    def test_30_waiting_on_shared_writer_lock_uses_late_DB_clock_not_early_transaction_start(self):
        kickoff = datetime.now(timezone.utc) + timedelta(seconds=3)
        _, doc, obs = self.setup_fixture(clock(kickoff))
        blocker, waiter = self.pg.session(), self.pg.session()
        try:
            blocker.send("BEGIN; SELECT pg_advisory_xact_lock(619044,1);")
            blocker.barrier("publication_lock_held")
            waiter.send("BEGIN; SET LOCAL ROLE service_role;" + statement(doc["envelope_id"], native.canonical(obs)) + "COMMIT;")
            # Wait across the real event boundary; transaction_timestamp() or a
            # clock selected before the lock would falsely admit the receipt.
            delay = (kickoff - datetime.now(timezone.utc)).total_seconds() + .2
            if delay > 0:
                time.sleep(delay)
            blocker.send("COMMIT;")
            self.assertEqual(blocker.finish().returncode, 0)
            result = waiter.finish()
            self.assertEqual(result.returncode, 0)
            value = next(v["attestation"] for v in result.values if "attestation" in v)
            self.assertEqual(value["status"], "withheld")
            self.assertEqual(value["receipts"], [])
            self.assertEqual(value["rejections"][0]["reason"], "publication_not_preevent")
        finally:
            waiter.abort()
            blocker.abort()

    def test_40_body_failure_rolls_back_all_evidence_and_immutable_rows_refuse_changes(self):
        _, doc, obs = self.setup_fixture()
        before = self.read("receipts")["watermark"]
        bad = {**obs, "body_utf8": '{"players":[],"players":[]}'}
        self.commit(doc, bad, expected="PT422")
        self.assertEqual(self.read("publication_attestation", doc["envelope_id"])["rows"], [])
        self.assertEqual(self.read("receipts")["watermark"], before)
        duplicate_outer = native.canonical(obs)[:-1] + ',"body_utf8":"{}"}'
        self.pg.run("SET ROLE service_role;" + statement(doc["envelope_id"], duplicate_outer), expected="PT422")
        invalid_policy = copy.deepcopy(obs)
        invalid_policy["policy"]["kind"] = "untrusted-policy"
        self.commit(doc, invalid_policy, expected="PT422")
        for origin in ("https://127.1", "https://0x7f.1"):
            unsafe = copy.deepcopy(obs)
            unsafe["policy"]["origin"] = origin
            self.commit(doc, unsafe, expected="PT422")
            self.assertEqual(self.read("publication_attestation", doc["envelope_id"])["rows"], [])
        # An error AFTER insertion must roll back evidence/ACK/membership, not
        # leave a durable attestation while returning an error to the caller.
        self.pg.run("""
          CREATE FUNCTION selflearn_private.native_publication_abort() RETURNS trigger
          LANGUAGE plpgsql AS $$ BEGIN RAISE SQLSTATE 'PT422' USING MESSAGE='native injected abort'; END $$;
          CREATE TRIGGER native_publication_abort AFTER INSERT ON selflearn_private.journal_publication_attestations
          FOR EACH ROW EXECUTE FUNCTION selflearn_private.native_publication_abort();
        """)
        try:
            self.commit(doc, obs, expected="PT422")
            self.assertEqual(self.read("publication_attestation", doc["envelope_id"])["rows"], [])
            self.assertEqual(self.read("receipts")["watermark"], before)
        finally:
            self.pg.run("DROP TRIGGER native_publication_abort ON selflearn_private.journal_publication_attestations;"
                        "DROP FUNCTION selflearn_private.native_publication_abort();")
        self.commit(doc, obs)
        for sql in ["UPDATE selflearn_private.journal_publication_attestations SET ack_json=ack_json",
                    "DELETE FROM selflearn_private.journal_publication_attestations",
                    "TRUNCATE selflearn_private.journal_publication_attestations"]:
            self.pg.run(sql, expected="PT409")

    def test_50_only_service_can_execute_RPC_and_even_service_cannot_directly_insert_or_stamp(self):
        _, doc, obs = self.setup_fixture()
        for role in ("anon", "authenticated"):
            self.pg.run("SET ROLE " + role + ";" + statement(doc["envelope_id"], native.canonical(obs)), expected="42501")
            self.pg.run("SET ROLE " + role + "; SELECT * FROM selflearn_private.journal_publication_attestations;", expected="42501")
        self.pg.run("SET ROLE service_role; INSERT INTO selflearn_private.journal_publication_attestations "
            "(object_id,observation_json) VALUES ('x','{}');", expected="42501")
        self.pg.run("SET ROLE service_role; UPDATE selflearn_private.journal_publication_attestations SET stored_utc=now();", expected="42501")


if __name__ == "__main__":
    unittest.main()
