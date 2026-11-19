"""Immutable collection-to-publication handoff and deadline-guarded registration."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .. import learning_jobs as dto
from ..probability_receipts import fingerprint, instant
from ..store.receipt_journal import JournalConflict
from ..store.supabase_journal import _canonical, _decode
from ..store.supabase_store import StoreConflict, StoreError
from . import nfl_collection_handoff as handoff
from . import nfl_publication_delivery as delivery

KIND = "nfl.collection_publication_definition.v1"
TERMINAL = frozenset(("succeeded", "failed", "expired", "runtime_limit", "attempt_limit"))


class CollectionPublicationCutoff(ValueError):
    """The database would register a new job outside the producer window."""


def prepared_record(intent, publisher, declaration):
    if (publisher is None or publisher["intent_id"] != intent["intent_id"] or publisher["disposition"] != "match"
            or publisher["published_sha"] is None or publisher["published_pool_id"] != intent["source_snapshots"]["leg_pool"]
            or publisher["published_pool_id"] == publisher["previous_pool_id"]):
        raise ValueError("collection_publication_receipt_mismatch")
    delivery.validate_declaration(declaration, declaration["policy"], declaration["authority_id"])
    # Definition v2 uses only the maximum of the eligible kickoff times. The
    # collection gate has independently joined and verified the full ACK and
    # exact latest kickoff for these original receipts before first preparation.
    definition, _, _ = delivery.definition(intent["generation_object_id"], intent["generation_id"], intent["recorded_utc"],
        [intent["latest_kickoff_utc"]], declaration["policy"], declaration["code_id"], declaration["authority_id"])
    record = {"version": 1, "kind": KIND, "intent_id": intent["intent_id"],
              "publisher_receipt_id": publisher["receipt_id"], "generation_object_id": intent["generation_object_id"],
              "generation_id": intent["generation_id"], "declaration": declaration, "definition": definition}
    return {**record, "prepared_id": fingerprint(record)}


def _saved(value, intent, publisher):
    if value is None:
        return None
    handoff.fields(value, {"document_json", "stored_utc"})
    record = _decode(value["document_json"])
    original = prepared_record(intent, publisher, record["declaration"])
    if (_canonical(original) != value["document_json"] or not
            dto.clock(publisher["stored_utc"]) <= dto.clock(value["stored_utc"]) < dto.clock(intent["latest_kickoff_utc"])):
        raise ValueError("invalid_collection_publication_record")
    return {**record, "stored_utc": value["stored_utc"]}


def check_job(snapshot, record):
    return delivery.validate_snapshot(snapshot, record["definition"], record["declaration"]["authority_id"])


class SupabaseCollectionPublication:
    def __init__(self, base):
        self.base = base

    def _call(self, operation, *, intent_id=None, record=None, limit=None, after=None):
        try:
            return self.base.journal._request("POST", "/rest/v1/rpc/selflearn_nfl_collection_publication_v1", data={
                "p_operation": operation, "p_repository": self.base.repository, "p_intent_id": intent_id,
                "p_document_json": _canonical(record) if record is not None else None,
                "p_limit": limit, "p_after": after})
        except StoreConflict:
            raise JournalConflict("immutable_collection_publication_conflict") from None
        except StoreError as error:
            if error.status == 410:
                raise CollectionPublicationCutoff("collection_publication_cutoff") from None
            raise
        except Exception:
            raise StoreError(503) from None

    def _view(self, value, intent, publisher):
        try:
            return _saved(value, intent, publisher)
        except (ValueError, TypeError, KeyError):
            raise StoreError(502) from None

    def read(self, intent, publisher):
        return self._view(self._call("read", intent_id=intent["intent_id"]), intent, publisher)

    def find(self, record):
        value = self._call("find", intent_id=record["intent_id"])
        try:
            return None if value is None else check_job(value, record)
        except (ValueError, TypeError, KeyError):
            raise StoreError(502) from None

    def prepare(self, intent, publisher, record):
        stored = self._view(self._call("prepare", intent_id=intent["intent_id"], record=record), intent, publisher)
        if stored is None or _canonical({k: v for k, v in stored.items() if k != "stored_utc"}) != _canonical(record):
            raise StoreError(502)
        return stored

    def register(self, jobs, record, definition):
        if dto.canonical(definition) != dto.canonical(record["definition"]) or jobs.authority_id != record["declaration"]["authority_id"]:
            raise JournalConflict("collection_job_definition_changed")
        value = self._call("register", intent_id=record["intent_id"])
        try:
            return check_job(value, record)
        except (ValueError, TypeError, KeyError):
            raise StoreError(502) from None

    def pending(self, limit, after):
        handoff.bounded_scan(limit, after)
        value = self._call("pending", limit=limit, after=after)
        try:
            return handoff.normalize_scan(value, self.base.repository, limit, after)
        except (ValueError, TypeError, KeyError):
            raise StoreError(502) from None


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS nfl_collection_publication_v1 (
    intent_id TEXT PRIMARY KEY REFERENCES nfl_collection_handoff_v1(record_id),
    document_json TEXT NOT NULL, stored_utc TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS nfl_collection_publication_no_update_v1 BEFORE UPDATE ON nfl_collection_publication_v1
    BEGIN SELECT RAISE(ABORT, 'immutable NFL collection publication definition'); END;
CREATE TRIGGER IF NOT EXISTS nfl_collection_publication_no_delete_v1 BEFORE DELETE ON nfl_collection_publication_v1
    BEGIN SELECT RAISE(ABORT, 'immutable NFL collection publication definition'); END;
"""


class SQLiteCollectionPublication:
    """Explicit test seam, using the same SQLite file as the forecast/job ports."""

    def __init__(self, base):
        self.base = base
        self._db = base._db
        self._exists = self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                       ("nfl_collection_publication_v1",)).fetchone() is not None

    @staticmethod
    def _row(db, identity):
        value = db.execute("SELECT document_json,stored_utc FROM nfl_collection_publication_v1 WHERE intent_id=?",
                           (identity,)).fetchone()
        return dict(value) if value is not None else None

    def read(self, intent, publisher):
        return _saved(self._row(self._db, intent["intent_id"]) if self._exists else None, intent, publisher)

    def find(self, record):
        authority, job_id = record["declaration"]["authority_id"], record["definition"]["job_id"]
        with self._db:
            self._db.execute("BEGIN")
            stored = self._db.execute("SELECT definition_json,registered_utc FROM learning_jobs_v1 "
                                      "WHERE authority_id=? AND job_id=?", (authority, job_id)).fetchone()
            if stored is None:
                return None
            events = [json.loads(row[0]) for row in self._db.execute("SELECT event_json FROM learning_job_events_v1 "
                      "WHERE authority_id=? AND job_id=? ORDER BY sequence", (authority, job_id))]
            result = dto.complete_snapshot(json.loads(stored[0]), events, authority_id=authority,
                                           registered_utc=stored[1], observed_utc=self.base._now())
            return check_job(result, record)

    def prepare(self, intent, publisher, record):
        if not self._exists:
            self._db.executescript(_SQLITE_SCHEMA)
            self._exists = True
        key = tuple(intent[k] for k in ("workflow", "run_id", "run_attempt"))
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self.base._read(key)
            old = self.read(current["intent"], current["publisher_receipt"])
            if old is not None:
                if _canonical({k: v for k, v in old.items() if k != "stored_utc"}) != _canonical(record):
                    raise JournalConflict("immutable_collection_publication_conflict")
                return old
            if (_canonical(prepared_record(current["intent"], current["publisher_receipt"], record["declaration"]))
                    != _canonical(record)):
                raise JournalConflict("collection_publication_record_changed")
            now = self.base._now()
            if instant(now) >= instant(intent["latest_kickoff_utc"]):
                raise CollectionPublicationCutoff("collection_publication_cutoff")
            ack = self._db.execute("SELECT payload FROM receipt_journal_generation_commits WHERE object_id=?",
                                   (intent["generation_object_id"],)).fetchone()
            result = json.loads(ack[0]) if ack is not None else None
            if not result or result["durable"] is not True or not result["receipts"] or result["rejections"]:
                raise JournalConflict("collection_publication_intake_incomplete")
            self._db.execute("INSERT INTO nfl_collection_publication_v1 VALUES (?,?,?)",
                             (intent["intent_id"], _canonical(record), now))
            return self.read(current["intent"], current["publisher_receipt"])

    def register(self, jobs, record, definition):
        db = jobs._db
        paths = [connection.execute("PRAGMA database_list").fetchone()[2] for connection in (db, self._db)]
        if not all(paths) or Path(paths[0]).resolve() != Path(paths[1]).resolve():
            raise StoreError(503)
        spec = dto.validate_spec(definition, authority_id=jobs.authority_id)
        if dto.canonical(spec) != dto.canonical(record["definition"]) or jobs.authority_id != record["declaration"]["authority_id"]:
            raise JournalConflict("collection_job_definition_changed")
        identity = record["intent_id"]
        with db:
            db.execute("BEGIN IMMEDIATE")
            stored = self._row(db, identity)
            original = db.execute("SELECT document_json FROM nfl_collection_handoff_v1 "
                "WHERE repository=? AND stage='intent' AND record_id=?", (self.base.repository, identity)).fetchone()
            if stored is None or original is None or _decode(stored["document_json"])["prepared_id"] != record["prepared_id"]:
                raise JournalConflict("collection_publication_definition_missing")
            old = db.execute("SELECT definition_json FROM learning_jobs_v1 WHERE authority_id=? AND job_id=?",
                             (jobs.authority_id, spec["job_id"])).fetchone()
            if old is not None:
                if old[0] != dto.canonical(spec):
                    raise dto.JobConflict("learning_job_definition_conflict")
            else:
                kickoff = instant(json.loads(original[0])["latest_kickoff_utc"])
                observed = jobs._now()
                if instant(observed) >= kickoff:
                    raise CollectionPublicationCutoff("collection_publication_cutoff")
                for field in ("input_object_id", "policy_object_id"):
                    jobs._reference(spec[field], observed)
                recorded = jobs._now()
                if instant(recorded) >= kickoff:
                    raise CollectionPublicationCutoff("collection_publication_cutoff")
                db.execute("INSERT INTO learning_jobs_v1 VALUES (?,?,?,?)",
                           (jobs.authority_id, spec["job_id"], dto.canonical(spec), recorded))
            return check_job(jobs._snapshot(spec["job_id"]), record)

    def pending(self, limit, after):
        handoff.bounded_scan(limit, after)
        with self._db:
            self._db.execute("BEGIN")
            observed = self.base._now()
            rows = self._db.execute("SELECT record_id,document_json FROM nfl_collection_handoff_v1 "
                "WHERE repository=? AND stage='intent' AND record_id>? ORDER BY record_id LIMIT ?",
                (self.base.repository, after or "", limit + 1)).fetchall()
            pending = []
            for row in rows[:limit]:
                intent = json.loads(row["document_json"])
                if not self._pending(intent, observed):
                    continue
                pending.append(handoff.locator(intent))
            return {"version": 1, "observed_utc": observed, "pending": pending,
                    "next_after": rows[limit - 1]["record_id"] if len(rows) > limit else None}

    def _pending(self, intent, observed):
        key = tuple(intent[k] for k in ("workflow", "run_id", "run_attempt"))
        view = self.base._read(key)
        if view["missing_object_ids"]:
            return False
        stage = self.read(view["intent"], view["publisher_receipt"])
        open_window = instant(observed) < instant(intent["latest_kickoff_utc"])
        if stage is None:
            return open_window
        definition = stage["definition"]
        authority = stage["declaration"]["authority_id"]
        registered = self._db.execute("SELECT definition_json FROM learning_jobs_v1 WHERE authority_id=? AND job_id=?",
                                      (authority, definition["job_id"])).fetchone()
        if registered is None:
            return open_window
        if registered[0] != dto.canonical(definition) or instant(observed) >= instant(definition["not_after_utc"]):
            return False
        head = self._db.execute("SELECT event_json FROM learning_job_events_v1 "
            "WHERE authority_id=? AND job_id=? ORDER BY sequence DESC LIMIT 1", (authority, definition["job_id"])).fetchone()
        if head is None:
            return True
        state = json.loads(head[0])["state"]
        if state["status"] in ("succeeded", "failed"):
            return False
        now = instant(observed)
        if state["runtime_deadline_utc"] and now >= instant(state["runtime_deadline_utc"]):
            return False
        return state["attempt"] < definition["max_attempts"] or (
            state["status"] == "running" and now < instant(state["lease_until_utc"]))


def attach(base):
    if isinstance(base, handoff.SupabaseCollectionHandoff):
        return SupabaseCollectionPublication(base)
    if isinstance(base, handoff.SQLiteCollectionHandoff) and isinstance(base._db, sqlite3.Connection):
        return SQLiteCollectionPublication(base)
    raise StoreError(503)
