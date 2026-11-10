"""Explicit local SQLite job journal; never a hosted/ephemeral fallback.

The database file owner is trusted. Job state does not enter forecast/outcome
watermarks, and no job transition trains a model or writes serving state.
"""
import json

from .. import learning_jobs as dto
from .. import learning_job_discovery as discovery
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
CREATE INDEX IF NOT EXISTS learning_job_discovery_scope_v1 ON learning_jobs_v1 (
 authority_id,json_extract(definition_json,'$.namespace.domain'),json_extract(definition_json,'$.namespace.task'),
 json_extract(definition_json,'$.namespace.producer_id'),json_extract(definition_json,'$.operation'),
 json_extract(definition_json,'$.code_id'),json_extract(definition_json,'$.policy_object_id'),registered_utc,job_id
);
CREATE INDEX IF NOT EXISTS learning_job_latest_event_v1 ON learning_job_events_v1(authority_id,job_id,sequence DESC);
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
        self._db.create_function("learning_job_clock_us_v1", 1, discovery.clock_microseconds, deterministic=True)

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

    def list_claimable_jobs(self, namespace, operation, *, code_id=None, policy_object_id=None, limit=16):
        """Return due IDs at one DB clock; each still needs an atomic claim.

        Scoped expression indexes and one indexed latest-event lookup per job
        avoid loading histories into Python. Retried jobs move behind older
        untouched work by their latest event clock, then registration and ID.
        """
        _, namespace, operation, code_id, policy_object_id, limit = discovery.scope(
            self.authority_id, namespace, operation, code_id, policy_object_id, limit)
        with self._db:
            self._db.execute("BEGIN")
            observed = self._now()
            parameters = {"authority": self.authority_id, "domain": namespace["domain"], "task": namespace["task"],
                "producer": namespace["producer_id"], "operation": operation, "code": code_id,
                "policy": policy_object_id, "now": discovery.clock_microseconds(observed), "limit": limit}
            code_clause = " AND json_extract(j.definition_json,'$.code_id')=:code" if code_id is not None else ""
            if policy_object_id is not None:
                code_clause += " AND json_extract(j.definition_json,'$.policy_object_id')=:policy"
            rows = self._db.execute("""
                SELECT j.job_id FROM learning_jobs_v1 j
                LEFT JOIN learning_job_events_v1 e ON e.authority_id=j.authority_id AND e.job_id=j.job_id
                  AND e.sequence=(SELECT max(head.sequence) FROM learning_job_events_v1 head
                                  WHERE head.authority_id=j.authority_id AND head.job_id=j.job_id)
                WHERE j.authority_id=:authority AND json_extract(j.definition_json,'$.namespace.domain')=:domain
                  AND json_extract(j.definition_json,'$.namespace.task')=:task
                  AND json_extract(j.definition_json,'$.namespace.producer_id')=:producer
                  AND json_extract(j.definition_json,'$.operation')=:operation
                """ + code_clause + """
                  AND learning_job_clock_us_v1(j.registered_utc)<=:now
                  AND learning_job_clock_us_v1(json_extract(j.definition_json,'$.not_before_utc'))<=:now
                  AND learning_job_clock_us_v1(json_extract(j.definition_json,'$.not_after_utc'))>:now
                  AND coalesce(e.sequence,0)<128
                  AND coalesce(json_extract(e.event_json,'$.state.attempt'),0)<json_extract(j.definition_json,'$.max_attempts')
                  AND (json_extract(e.event_json,'$.state.runtime_deadline_utc') IS NULL
                       OR learning_job_clock_us_v1(json_extract(e.event_json,'$.state.runtime_deadline_utc'))>:now)
                  AND (e.event_json IS NULL OR learning_job_clock_us_v1(json_extract(e.event_json,'$.recorded_utc'))<=:now)
                  AND (coalesce(json_extract(e.event_json,'$.state.status'),'queued')='queued'
                       OR (json_extract(e.event_json,'$.state.status')='running'
                           AND learning_job_clock_us_v1(json_extract(e.event_json,'$.state.lease_until_utc'))<=:now))
                ORDER BY learning_job_clock_us_v1(coalesce(json_extract(e.event_json,'$.recorded_utc'),j.registered_utc)),
                         learning_job_clock_us_v1(j.registered_utc),j.job_id LIMIT :limit
                """, parameters).fetchall()
            return discovery.validate({"version": 1, "authority_id": self.authority_id, "namespace": namespace,
                "operation": operation, "code_id": code_id, "policy_object_id": policy_object_id, "observed_utc": observed,
                "job_ids": [row[0] for row in rows]}, authority_id=self.authority_id,
                namespace=namespace, operation=operation, code_id=code_id, policy_object_id=policy_object_id, limit=limit)

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
