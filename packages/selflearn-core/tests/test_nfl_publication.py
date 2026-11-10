"""Publication integrity and SQLite transaction acceptance; no HTTP/host claims."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_publication import (
    MAX_BODY_BYTES, build_publication_attestation, validate_publication_attestation, validate_policy,
)
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal as ReceiptJournal

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/nfl-ingest-m1"
STARTED = "2026-09-01T14:01:10Z"
COMPLETED = "2026-09-01T14:01:20Z"
RECORDED = "2026-09-01T14:02:00Z"
KICKOFF = "2026-09-02T00:15:00Z"


def seed(journal, *, finalize=True, empty=False):
    for name in ("leg_pool", "schedule", "parlay_calibration"):
        request = json.loads((FIXTURES / ("source_snapshot_" + name + ".json")).read_text())
        journal.append_object(request["kind"], request["object_id"], request["payload"])
    obj = json.loads((FIXTURES / "generation_envelope.json").read_text())
    doc = obj["payload"]
    if empty:
        doc["receipts"] = []
        doc["generations"][0]["receipt_ids"] = []
        doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    oid = doc["envelope_id"]
    journal.append_object("generation", oid, doc)
    if finalize:
        batch = adapt_prospective(journal_docs=[doc])
        journal.commit_generation(oid, batch["receipts"], batch["rejections"])
    pool = json.loads((FIXTURES / "source_snapshot_leg_pool.json").read_text())["payload"]
    observation = {"version": 1, "policy": {"version": 1, "kind": "nfl.pool_http_publication.v1",
        "origin": "https://publication-fixture.example", "path": "/data/leg_pool.json"},
        "observed_started_utc": STARTED, "observed_completed_utc": COMPLETED,
        "http_status": 200, "body_utf8": json.dumps(pool, indent=2)}
    return oid, observation


class PublicationTests(unittest.TestCase):
    def test_new_proof_reads_only_finalized_members_once_never_unrelated_history(self):
        from test_probability_receipts import receipt as unrelated_receipt
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            original = journal.publication_generation_receipts(oid)
            journal.append_receipt(unrelated_receipt("unrelated-domain-history"))
            read_members = journal.publication_generation_receipts
            seen = []
            def bounded(identity):
                rows = read_members(identity)
                seen.append([r["receipt_id"] for r in rows])
                return rows
            with patch.object(journal, "receipts", side_effect=AssertionError("whole-history scan")), \
                    patch.object(journal, "get_receipt", side_effect=AssertionError("per-member lookup")), \
                    patch.object(journal, "publication_generation_receipts", side_effect=bounded), \
                    patch.object(journal, "_publication_recorded_utc", return_value=RECORDED):
                record = journal.commit_publication_attestation(oid, observation)
            self.assertEqual(seen, [[r["receipt_id"] for r in original]])
            self.assertEqual([r["receipt_id"] for r in record["receipts"]], [r["receipt_id"] for r in original])
            self.assertEqual(record["status"], "accepted")

    def test_missing_duplicate_or_different_finalized_member_fails_before_new_evidence(self):
        from test_probability_receipts import receipt as unrelated_receipt
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            members = journal.publication_generation_receipts(oid)
            for rows in ([], members * 2, [unrelated_receipt("wrong-membership")]):
                with self.subTest(rows=len(rows)), patch.object(journal, "publication_generation_receipts", return_value=rows), \
                        patch.object(journal, "_publication_recorded_utc", return_value=RECORDED):
                    with self.assertRaises(ValueError):
                        journal.commit_publication_attestation(oid, observation)
                self.assertIsNone(journal.get_publication_attestation(oid))

    def test_public_body_limit_is_four_mib_independent_of_intake_object_limit(self):
        from selflearn_core.adapters.nfl_publication import parse_publication_body
        self.assertEqual(MAX_BODY_BYTES, 4194304)
        # Legal JSON whitespace makes raw HTTP bigger without fabricating any
        # forecast/source content or expanding the intake object's size limit.
        raw = "{}" + " " * (MAX_BODY_BYTES - 2)
        parsed, body = parse_publication_body(raw)
        self.assertEqual(parsed, {})
        self.assertEqual(len(body), MAX_BODY_BYTES)
        with self.assertRaisesRegex(ValueError, "body_size"):
            parse_publication_body(raw + " ")

    def test_bounded_generic_join_preserves_integer_float_feature_types(self):
        from selflearn_core.probability_receipts import seal_receipt
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            original = journal.publication_generation_receipts(oid)[0]
            changed = {k: copy.deepcopy(v) for k, v in original.items() if k != "receipt_id"}
            numeric = next(k for k, v in changed["features"].items() if type(v) is float and v == int(v))
            changed["features"][numeric] = int(changed["features"][numeric])
            forged = seal_receipt(changed)
            ack = journal.generation_ack(oid)
            ack["receipts"][0]["receipt_id"] = forged["receipt_id"]
            with patch.object(journal, "generation_ack", return_value=ack), \
                    patch.object(journal, "publication_generation_receipts", return_value=[forged]):
                with self.assertRaisesRegex(ValueError, "generic_receipt_mismatch"):
                    build_publication_attestation(journal, oid, observation, RECORDED)

    def test_pure_exact_public_body_joins_original_generic_receipts(self):
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            before = journal.receipts(), journal.generations()
            record = build_publication_attestation(journal, oid, observation, RECORDED)
            self.assertIs(validate_publication_attestation(record), record)
            self.assertEqual(record["status"], "accepted")
            self.assertEqual(record["receipts"][0]["direction"], "over")
            self.assertEqual(record["receipts"][0]["receipt_id"], journal.receipts()[0]["receipt_id"])
            self.assertEqual(record["receipts"][0]["served_prob"], journal.receipts()[0]["served_prob"])
            self.assertNotIn("body_utf8", record["observation"])
            self.assertEqual((journal.receipts(), journal.generations()), before)
            self.assertEqual(journal.receipts()[0]["publication_status"], "generated_unverified")

    def test_policy_requires_explicit_plain_dns_https_origin(self):
        valid = {"version": 1, "kind": "nfl.pool_http_publication.v1",
                 "origin": "https://publication-fixture.example", "path": "/data/leg_pool.json"}
        for origin in ("", "http://public.example", "https://localhost", "https://sub.localhost",
                       "https://127.0.0.1", "https://[::1]", "https://public.example:443",
                       "https://127.1", "https://0x7f.1",
                       "https://user:pass@public.example", "https://public.example/",
                       "https://public.example?token=x", "https://public.example#x"):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                validate_policy({**valid, "origin": origin})
        self.assertIs(validate_policy(valid), valid)

    def test_body_http_clocks_and_generation_authority_are_revalidated(self):
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            for update in ({"body_utf8": observation["body_utf8"].replace('"week": 6', '"week": 7')},
                           {"body_utf8": '{"same":1,"same":1}'}, {"body_utf8": '{"n":NaN}'},
                           {"body_utf8": '"' + "x" * MAX_BODY_BYTES + '"'},
                           {"body_utf8": "[" * 25 + "1" + "]" * 25},
                           {"body_utf8": '{"x":"\\u0000"}'}, {"http_status": 201},
                           {"observed_started_utc": "2026-09-01T13:59:00Z"},
                           {"observed_completed_utc": "2026-09-01T14:03:00Z"}):
                with self.subTest(update=list(update)), self.assertRaises(ValueError):
                    build_publication_attestation(journal, oid, {**observation, **update}, RECORDED)
            forged = journal.generation_ack(oid)
            forged["receipts"][0]["receipt_id"] = "sha256:" + "0" * 64
            with patch.object(journal, "generation_ack", return_value=forged), self.assertRaisesRegex(
                    ValueError, "publication_generic_receipt_mismatch"):
                build_publication_attestation(journal, oid, observation, RECORDED)
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal, finalize=False)
            with self.assertRaises(ValueError):
                build_publication_attestation(journal, oid, observation, RECORDED)

    def test_kickoff_boundaries_are_exhaustive_withheld_results(self):
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            for observed, recorded in ((COMPLETED, KICKOFF), (KICKOFF, KICKOFF)):
                record = build_publication_attestation(journal, oid,
                    {**observation, "observed_completed_utc": observed}, recorded)
                self.assertEqual(record["status"], "withheld")
                self.assertEqual(record["receipts"], [])
                self.assertEqual(len(record["rejections"]), len(journal.receipts()))
                self.assertEqual(record["rejections"][0]["reason"], "publication_not_preevent")
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal, empty=True)
            record = build_publication_attestation(journal, oid, observation, RECORDED)
            self.assertEqual(record["status"], "no_supported_membership")
            self.assertEqual(record["receipts"], [])

    def test_record_hash_policy_and_partition_tampering_fail(self):
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            original = build_publication_attestation(journal, oid, observation, RECORDED)
            for mode in ("hash", "duplicate", "probability", "direction", "clock", "policy"):
                record = copy.deepcopy(original)
                if mode == "hash":
                    record["generation_id"] = "sha256:" + "1" * 64
                elif mode == "duplicate":
                    record["receipts"].append(copy.deepcopy(record["receipts"][0]))
                elif mode == "probability":
                    record["receipts"][0]["served_prob"] = True
                elif mode == "direction":
                    record["receipts"][0]["direction"] = "under"
                elif mode == "clock":
                    record["recorded_utc"] = KICKOFF
                else:
                    record["policy_id"] = "sha256:" + "2" * 64
                if mode != "hash":
                    record["attestation_id"] = fingerprint({k: v for k, v in record.items() if k != "attestation_id"})
                with self.subTest(mode=mode), self.assertRaises(ValueError):
                    validate_publication_attestation(record)

    def test_sqlite_clock_lock_atomicity_original_replay_and_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "journal.sqlite"
            with ReceiptJournal(path) as journal:
                oid, observation = seed(journal)
                before = journal.receipts(), journal.generations()

                def clock():
                    self.assertTrue(journal._db.in_transaction)
                    return RECORDED
                journal._db.execute("CREATE TRIGGER publication_injected_failure BEFORE INSERT ON "
                    "receipt_journal_publication_evidence BEGIN SELECT RAISE(ABORT,'synthetic evidence failure'); END")
                with patch.object(journal, "_publication_recorded_utc", side_effect=clock), self.assertRaises(sqlite3.Error):
                    journal.commit_publication_attestation(oid, observation)
                self.assertIsNone(journal.get_publication_attestation(oid))
                self.assertEqual(journal._db.execute("SELECT count(*) FROM receipt_journal_publication_evidence").fetchone()[0], 0)
                journal._db.execute("DROP TRIGGER publication_injected_failure")
                with patch.object(journal, "_publication_recorded_utc", side_effect=clock):
                    record = journal.commit_publication_attestation(oid, observation)
                with patch.object(journal, "_publication_recorded_utc", side_effect=AssertionError("retry read clock")):
                    self.assertEqual(journal.commit_publication_attestation(oid, None), record)
                stored = journal._db.execute("SELECT observation FROM receipt_journal_publication_evidence").fetchone()[0]
                self.assertEqual(json.loads(stored), observation)
                for table in ("receipt_journal_publication_attestations", "receipt_journal_publication_evidence"):
                    with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                        journal._db.execute("DELETE FROM " + table)
                    journal._db.rollback()
                    with self.subTest(table=table), self.assertRaises(sqlite3.IntegrityError):
                        journal._db.execute("UPDATE " + table + " SET object_id=object_id")
                    journal._db.rollback()
                snapshot = journal.read_publication_fit_snapshot()
                self.assertEqual(snapshot["publication_attestations"], [record])
                self.assertEqual(snapshot["revisions"], [])
                self.assertEqual((snapshot["receipts"], snapshot["generation_records"]), before)
                self.assertEqual(set(snapshot), {"version", "watermark", "receipts", "generation_records", "revisions", "publication_attestations"})
            with ReceiptJournal(path) as restarted:
                self.assertEqual(restarted.get_publication_attestation(oid), record)
                self.assertEqual(restarted.commit_publication_attestation(oid, {}), record)

    def test_sqlite_precision_waits_for_real_tick_without_backdating_completion(self):
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            observation["observed_completed_utc"] = "2026-09-01T14:01:20.123851Z"
            with patch.object(journal, "_publication_recorded_utc", side_effect=[
                    "2026-09-01T14:01:20.123Z", "2026-09-01T14:01:20.124Z"]), patch(
                    "selflearn_core.adapters.nfl_publication_journal.time.sleep") as sleep:
                record = journal.commit_publication_attestation(oid, observation)
            sleep.assert_called_once_with(0.001)
            self.assertEqual(record["recorded_utc"], "2026-09-01T14:01:20.124Z")
            self.assertEqual(record["observation"]["observed_completed_utc"], observation["observed_completed_utc"])
        for stamp in ("2026-09-01T14:01:20.122Z", "2026-09-01T14:01:20.123Z"):
            with self.subTest(stamp=stamp), ReceiptJournal(":memory:") as journal:
                oid, observation = seed(journal)
                observation["observed_completed_utc"] = "2026-09-01T14:01:20.123851Z"
                with patch.object(journal, "_publication_recorded_utc", return_value=stamp), patch(
                        "selflearn_core.adapters.nfl_publication_journal.time.sleep") as sleep, self.assertRaises(ValueError):
                    journal.commit_publication_attestation(oid, observation)
                self.assertLessEqual(sleep.call_count, 2)
                if stamp.endswith("122Z"):
                    sleep.assert_not_called()
                self.assertIsNone(journal.get_publication_attestation(oid))
        with ReceiptJournal(":memory:") as journal:
            oid, observation = seed(journal)
            observation["observed_completed_utc"] = "2026-09-02T00:14:59.999851Z"
            with patch.object(journal, "_publication_recorded_utc", side_effect=[
                    "2026-09-02T00:14:59.999Z", KICKOFF]), patch("selflearn_core.adapters.nfl_publication_journal.time.sleep"):
                record = journal.commit_publication_attestation(oid, observation)
            self.assertEqual(record["status"], "withheld")
            self.assertEqual(record["receipts"], [])


if __name__ == "__main__":
    unittest.main()
