"""Explicit local SQLite job journal; never a hosted/ephemeral fallback.

The database file owner is trusted. Job state does not enter forecast/outcome
watermarks, and no job transition trains a model or writes serving state.
"""
import json

from .. import learning_jobs as dto
from ..probability_receipts import fingerprint, instant
from .receipt_journal import ReceiptJournal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_jobs_v1 (
 authority_id TEXT NOT NULL, job_id TEXT NOT NULL, definition_json TEXT NOT NULL,
 registered_utc TEXT NOT NULL, PRIMARY KEY(authority_id,job_id)
);
CREATE TABLE IF NOT EXISTS learning_job_events_v1 (
 authority_id TEXT NOT NULL, job_id TEXT NOT NULL, sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 128),
 command_id TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE, event_json TEXT NOT NULL,
 PRIMARY KEY(authority_id,job_id,sequence), UNIQUE(authority_id,job_id,command_id),
 FOREIGN KEY(authority_id,job_id) REFERENCES learning_jobs_v1(authority_id,job_id)
);
CREATE TRIGGER IF NOT EXISTS learning_jobs_no_update_v1 BEFORE UPDATE ON learning_jobs_v1
 BEGIN SELECT RAISE(ABORT,'immutable learning job'); END;
CREATE TRIGGER IF NOT EXISTS learning_jobs_no_delete_v1 BEFORE DELETE ON learning_jobs_v1
 BEGIN SELECT RAISE(ABORT,'immutable learning job'); END;
CREATE TRIGGER IF NOT EXISTS learning_job_events_no_update_v1 BEFORE UPDATE ON learning_job_events_v1
 BEGIN SELECT RAISE(ABORT,'immutable learning job event'); END;
CREATE TRIGGER IF NOT EXISTS learning_job_events_no_delete_v1 BEFORE DELETE ON learning_job_events_v1
 BEGIN SELECT RAISE(ABORT,'immutable learning job event'); END;
"""


class LearningJobJournal(ReceiptJournal):
    def __init__(self, path, *, authority_id):
        self.authority_id = dto.digest(authority_id)
        super().__init__(path);self._db.executescript(_SCHEMA)

    def _now(self):
        return self._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]

    def _reference(self, identity, recorded):
        obj = self.get_object(identity)
        dto.need(obj is not None and obj["kind"] == "source_snapshot" and fingerprint(obj["payload"]) == identity
            and instant(obj["received_utc"]) <= instant(recorded), "invalid_learning_job_reference")

    def register_job(self, spec):
        spec = dto.validate_spec(spec, authority_id=self.authority_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            old = self._db.execute("SELECT definition_json FROM learning_jobs_v1 WHERE authority_id=? AND job_id=?",
                (self.authority_id,spec["job_id"])).fetchone()
            if old:
                if old[0] != dto.canonical(spec):raise dto.JobConflict("learning_job_definition_conflict")
            else:
                now = self._now()
                for key in ("input_object_id", "policy_object_id"):self._reference(spec[key], now)
                self._db.execute("INSERT INTO learning_jobs_v1 VALUES(?,?,?,?)",
                    (self.authority_id,spec["job_id"],dto.canonical(spec),now))
            return self._snapshot(spec["job_id"])

    def _snapshot(self, job_id):
        row = self._db.execute("SELECT definition_json,registered_utc FROM learning_jobs_v1 WHERE authority_id=? AND job_id=?",
            (self.authority_id,dto.digest(job_id))).fetchone()
        dto.need(row is not None, "learning_job_absent")
        events = [json.loads(r[0]) for r in self._db.execute("SELECT event_json FROM learning_job_events_v1 WHERE authority_id=? AND job_id=? ORDER BY sequence",
            (self.authority_id,job_id))]
        return dto.complete_snapshot(json.loads(row[0]), events, authority_id=self.authority_id,
            registered_utc=row[1],observed_utc=self._now())

    def read_job(self, job_id):
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._snapshot(job_id)

    def append_job_command(self, command):
        command = dto.validate_command(command, authority_id=self.authority_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            current = self._snapshot(command["job_id"])
            for event in current["events"]:
                if event["command"]["command_id"] == command["command_id"]:
                    dto.need(dto.canonical(event["command"]) == dto.canonical(command))
                    return dto.acknowledgement(event)
            recorded = self._now()
            event = dto.make_event(current["definition"], current["events"], command, authority_id=self.authority_id,
                registered_utc=current["registered_utc"],recorded_utc=recorded)
            if command["object_id"] is not None:self._reference(command["object_id"], recorded)
            self._db.execute("INSERT INTO learning_job_events_v1 VALUES(?,?,?,?,?,?)",
                (self.authority_id,command["job_id"],event["sequence"],command["command_id"],event["event_id"],dto.canonical(event)))
            return dto.acknowledgement(event)
