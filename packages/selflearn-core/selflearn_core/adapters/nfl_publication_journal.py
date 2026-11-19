"""Explicit NFL publication journal composition over generic storage ports.

Generic receipt/outcome journals never import domain policy. Applications opt
into these specializations to verify NFL pool evidence and its pinned fit view.
"""
from __future__ import annotations

import json
import time

from ..probability_receipts import fingerprint, instant, validate_probability_receipt
from ..store.receipt_journal import ReceiptJournal, _json
from ..store.supabase_journal import SupabaseReceiptJournal, _canonical, _clock, _decode
from ..store.supabase_store import StoreError
from .nfl_publication import build_publication_attestation, validate_publication_attestation

_PUBLICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_journal_publication_attestations (
    object_id TEXT PRIMARY KEY REFERENCES receipt_journal_generation_commits(object_id),
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS receipt_journal_publication_evidence (
    object_id TEXT PRIMARY KEY REFERENCES receipt_journal_publication_attestations(object_id),
    observation TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS receipt_journal_publications_no_update
BEFORE UPDATE ON receipt_journal_publication_attestations BEGIN SELECT RAISE(ABORT, 'immutable publication attestation'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_publications_no_delete
BEFORE DELETE ON receipt_journal_publication_attestations BEGIN SELECT RAISE(ABORT, 'immutable publication attestation'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_publication_evidence_no_update
BEFORE UPDATE ON receipt_journal_publication_evidence BEGIN SELECT RAISE(ABORT, 'immutable publication evidence'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_publication_evidence_no_delete
BEFORE DELETE ON receipt_journal_publication_evidence BEGIN SELECT RAISE(ABORT, 'immutable publication evidence'); END;
CREATE TABLE IF NOT EXISTS nfl_correction_registrations (
    registration_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS nfl_correction_registrations_no_update
BEFORE UPDATE ON nfl_correction_registrations BEGIN SELECT RAISE(ABORT, 'immutable correction registration'); END;
CREATE TRIGGER IF NOT EXISTS nfl_correction_registrations_no_delete
BEFORE DELETE ON nfl_correction_registrations BEGIN SELECT RAISE(ABORT, 'immutable correction registration'); END;
CREATE TABLE IF NOT EXISTS nfl_correction_registration_observations (
    registration_id TEXT PRIMARY KEY REFERENCES nfl_correction_registrations(registration_id),
    observed_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS nfl_correction_observations_no_update
BEFORE UPDATE ON nfl_correction_registration_observations BEGIN SELECT RAISE(ABORT, 'immutable correction observation'); END;
CREATE TRIGGER IF NOT EXISTS nfl_correction_observations_no_delete
BEFORE DELETE ON nfl_correction_registration_observations BEGIN SELECT RAISE(ABORT, 'immutable correction observation'); END;
"""


class NFLPublicationJournal(ReceiptJournal):
    """Opt-in SQLite publication policy and evidence, preserving original IDs."""

    def __init__(self, path, *, read_only=False):
        super().__init__(path, read_only=read_only)
        if not read_only:
            try:
                self._db.executescript(_PUBLICATION_SCHEMA)
            except Exception:
                self.close()
                raise

    def _publication_recorded_utc(self):
        """Database insertion clock, taken after acquiring the writer lock.

        This is an internal trusted-fixture seam, never a caller timestamp or
        a claim that the eventual transaction commit completed at this time.
        """
        return self._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]

    def correction_evaluation_utc(self):
        return self._publication_recorded_utc()

    def get_correction_registration(self, registration_id):
        row = self._db.execute(
            "SELECT r.payload, o.observed_utc FROM nfl_correction_registrations r "
            "JOIN nfl_correction_registration_observations o USING (registration_id) "
            "WHERE r.registration_id=?", (registration_id,),
        ).fetchone()
        return {**json.loads(row["payload"]), "durable_observed_utc": row["observed_utc"]} if row else None

    def commit_correction_registration(self, plan):
        """Reserve an evaluation window for a validated artifact and policy."""
        plan = json.loads(_json(plan))
        plan_id = fingerprint(plan)
        start, end = instant(plan["start_utc"]), instant(plan["end_utc"])
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self._db.execute(
                "SELECT payload FROM nfl_correction_registrations WHERE plan_id=?", (plan_id,),
            ).fetchone()
            if existing is None:
                recorded = self.correction_evaluation_utc()
                now = instant(recorded)
                if not (instant(plan["fitted_at"]) <= now < start < end
                        and instant(plan["policy"]["registered_utc"]) <= now):
                    raise ValueError("registration_must_follow_fit_and_precede_holdout")
                for row in self._db.execute("SELECT payload FROM nfl_correction_registrations"):
                    other = json.loads(row[0])["plan"]
                    if start < instant(other["end_utc"]) and instant(other["start_utc"]) < end:
                        raise ValueError("overlapping_correction_registration")
                body = {"version": 1, "plan": plan, "recorded_utc": recorded}
                registration = {**body, "registration_id": fingerprint(body)}
                self._db.execute(
                    "INSERT INTO nfl_correction_registrations VALUES (?,?,?)",
                    (registration["registration_id"], plan_id, _json(registration)),
                )
            else:
                registration = json.loads(existing[0])
        return self._observe_correction_registration(registration, start)

    def _observe_correction_registration(self, registration, start):
        registration_id = registration["registration_id"]
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = self.get_correction_registration(registration_id)
            if existing is not None:
                return existing
            # The prior transaction has committed. Its insertion time alone
            # cannot establish that the plan became durable before the window.
            observed = self.correction_evaluation_utc()
            if not instant(registration["recorded_utc"]) <= instant(observed) < start:
                raise ValueError("durable_registration_not_observed_before_holdout")
            self._db.execute(
                "INSERT INTO nfl_correction_registration_observations VALUES (?,?)",
                (registration_id, observed),
            )
        return {**registration, "durable_observed_utc": observed}

    def publication_generation_receipts(self, object_id):
        """One PK join of the frozen generation ACK, never receipt history."""
        rows = self._db.execute("""
            SELECT r.payload FROM receipt_journal_generation_commits c
            JOIN json_each(c.payload,'$.receipts') member
            JOIN receipt_journal_receipts r
              ON r.receipt_id=json_extract(member.value,'$.receipt_id')
             AND r.source_receipt_id=json_extract(member.value,'$.source_receipt_id')
            WHERE c.object_id=? ORDER BY r.receipt_id
        """, (object_id,)).fetchall()
        result = [json.loads(row[0]) for row in rows]
        for receipt in result:
            validate_probability_receipt(receipt)
        return result

    def get_publication_attestation(self, object_id):
        row = self._db.execute(
            "SELECT payload FROM receipt_journal_publication_attestations WHERE object_id=?", (object_id,)).fetchone()
        if row is None:
            return None
        record = validate_publication_attestation(json.loads(row["payload"]))
        if record["object_id"] != object_id:
            raise ValueError("publication_attestation_object_mismatch")
        return record

    def commit_publication_attestation(self, object_id, observation):
        """Store verified evidence and its compact result atomically.

        Existing originals are recovered before any observation/clock access.
        A retry never changes first observation, origin, evidence or receipt IDs.
        """
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self.get_publication_attestation(object_id)
            if old is not None:
                return old
            # Detach the trusted observation before validation/persistence so
            # a caller cannot mutate its body/policy between the two writes.
            observation = json.loads(_json(observation))
            recorded = self._publication_recorded_utc()
            if type(observation) is not dict:
                raise ValueError("invalid_publication_fields")
            completed = instant(observation.get("observed_completed_utc"))
            lag = (completed - instant(recorded)).total_seconds()
            if 0 < lag < 0.001:
                # SQLite's clock is truncated to milliseconds, whereas the
                # verifier retains microseconds. Wait for a real DB tick;
                # never truncate/backdate the HTTP completion or manufacture
                # an insertion time. A clock >1ms ahead is not quantization.
                for _ in range(2):
                    time.sleep(0.001)
                    recorded = self._publication_recorded_utc()
                    if instant(recorded) >= completed:
                        break
            record = build_publication_attestation(self, object_id, observation, recorded)
            self._db.execute(
                "INSERT INTO receipt_journal_publication_attestations(object_id,payload) VALUES (?,?)",
                (object_id, _json(record)))
            self._db.execute(
                "INSERT INTO receipt_journal_publication_evidence(object_id,observation) VALUES (?,?)",
                (object_id, _json(observation)))
        return record

    def read_publication_fit_snapshot(self):
        """Read all fit authorities in one SQLite read transaction.

        The monotone count watermark describes this local immutable journal;
        it is not interchangeable with the Supabase journal sequence. Exact
        publication bytes stay private in the evidence table, outside fit reads.
        """
        with self._db:
            self._db.execute("BEGIN")
            watermark = str(sum(self._db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                for table in ("receipt_journal_receipts", "receipt_journal_objects", "receipt_journal_generation_commits",
                              "receipt_journal_revisions", "receipt_journal_publication_attestations")))
            receipts = self.receipts()
            records = self.generations()
            revisions = [row for receipt in receipts for row in self.resolution_history(receipt["receipt_id"])]
            attested = [row[0] for row in self._db.execute(
                "SELECT object_id FROM receipt_journal_publication_attestations ORDER BY object_id")]
            publications = [self.get_publication_attestation(oid) for oid in attested]
            return {"version": 1, "watermark": watermark, "receipts": receipts,
                    "generation_records": records, "revisions": revisions,
                    "publication_attestations": publications}


class NFLSupabasePublicationJournal(SupabaseReceiptJournal):
    """Opt-in NFL verification over the service-only publication RPC contract."""

    def publication_generation_receipts(self, object_id):
        rows, _ = self._read("publication_generation_receipts", key=object_id,
                             rpc_operation="publication_read")
        return sorted(self._receipt_rows(rows), key=lambda row: row["receipt_id"])

    @staticmethod
    def _publication_row(row):
        if set(row) != {"object_id", "attestation_json", "recorded_utc", "journal_seq"}:
            raise StoreError(502)
        result = _decode(row["attestation_json"])
        try:
            validate_publication_attestation(result)
            if (result["object_id"] != row["object_id"]
                    or result["recorded_utc"] != _clock(row["recorded_utc"])):
                raise ValueError("publication_row_mismatch")
        except (ValueError, KeyError, TypeError, OverflowError):
            raise StoreError(502) from None
        return result

    def get_publication_attestation(self, object_id):
        rows, _ = self._read("publication_attestation", key=object_id,
                             rpc_operation="publication_read")
        row = self._single(rows)
        if row is None:
            return None
        result = self._publication_row(row)
        if result["object_id"] != object_id:
            raise StoreError(502)
        return result

    def commit_publication_attestation(self, object_id, observation):
        """Commit exact HTTP evidence and its membership in one DB transaction.

        The database selects recorded_utc after the shared writer lock. A retry
        returns the original result before inspecting new observation input.
        No client timestamp can authorize a late membership.
        """
        prior = self.get_publication_attestation(object_id)
        if prior is not None:
            return prior
        # Reproduce the original pool/receipt mapping before persistence. The
        # final database time can only move preevent members to the late set.
        if not isinstance(observation, dict):
            raise ValueError("invalid_publication_observation")
        build_publication_attestation(self, object_id, observation,
                                      observation.get("observed_completed_utc"))
        response = self._rpc("publication", {
            "p_object_id": object_id, "p_observation_json": _canonical(observation),
        })
        try:
            if (set(response) != {"version", "attestation", "replayed"}
                    or type(response["replayed"]) is not bool):
                raise ValueError("invalid_publication_commit_response")
            result = validate_publication_attestation(response["attestation"])
            if result["object_id"] != object_id:
                raise ValueError("publication_object_mismatch")
            if response["replayed"]:
                return result
            expected = build_publication_attestation(
                self, object_id, observation, result["recorded_utc"])
            if _canonical(expected) != _canonical(result):
                raise ValueError("publication_commit_mismatch")
        except (ValueError, KeyError, TypeError, OverflowError):
            raise StoreError(502) from None
        return result

    def read_publication_fit_snapshot(self):
        """Pin receipts, finalized generations, revisions and attestations once."""
        def read(view, **kwargs):
            return self._read(view, rpc_operation="publication_read", **kwargs)
        result = self._snapshot(read)
        rows, _ = read("publication_attestations", watermark=result["watermark"])
        values = [self._publication_row(row) for row in rows]
        if len({row["object_id"] for row in values}) != len(values):
            raise StoreError(502)
        generations = {row["object_id"]: row for row in result["generation_records"]}
        receipts = {row["receipt_id"]: row for row in result["receipts"]}
        for value in values:
            generation = generations.get(value["object_id"])
            if generation is None:
                raise StoreError(502)
            manifests = generation["payload"].get("generations", [])
            if (len(manifests) != 1 or value["generation_id"] != manifests[0].get("generation_id")
                    or value["source_pool_id"] != manifests[0].get("source_snapshots", {}).get("leg_pool")):
                raise StoreError(502)
            expected = {(r["source_receipt_id"], r["receipt_id"])
                        for r in generation["ack"]["receipts"]}
            actual = {(r["source_receipt_id"], r["receipt_id"])
                      for r in value["receipts"] + value["rejections"]}
            if actual != expected or any(r["receipt_id"] not in receipts
                                         for r in value["receipts"] + value["rejections"]):
                raise StoreError(502)
            for member in value["receipts"]:
                stored = receipts[member["receipt_id"]]
                if (stored["source_receipt_id"] != member["source_receipt_id"]
                        or _canonical(stored["served_prob"]) != _canonical(member["served_prob"])
                        or stored["event_start_utc"] != member["event_start_utc"]):
                    raise StoreError(502)
        result["publication_attestations"] = sorted(values, key=lambda row: row["object_id"])
        return result
