"""Local, append-only receipt and outcome-revision journal (stdlib SQLite).

This is separate from the legacy prediction store. A local insertion timestamp
is not evidence of public publication. No fitting or promotion happens here.
"""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from ..probability_receipts import fingerprint, instant, validate_probability_receipt
from ..outcome_revisions import validate_journal_resolution


class JournalConflict(ValueError):
    """An immutable ID was reused or a correction did not extend the head."""


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS receipt_journal_meta (
    version INTEGER PRIMARY KEY CHECK (version = 1)
);
INSERT OR IGNORE INTO receipt_journal_meta VALUES (1);
CREATE TABLE IF NOT EXISTS receipt_journal_receipts (
    receipt_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    task TEXT NOT NULL,
    source_receipt_id TEXT NOT NULL,
    payload TEXT NOT NULL,
    received_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(domain, task, source_receipt_id)
);
CREATE TABLE IF NOT EXISTS receipt_journal_revisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    revision_id TEXT NOT NULL UNIQUE,
    receipt_id TEXT NOT NULL REFERENCES receipt_journal_receipts(receipt_id),
    supersedes TEXT UNIQUE REFERENCES receipt_journal_revisions(revision_id),
    payload TEXT NOT NULL,
    recorded_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS receipt_journal_initial_revision
    ON receipt_journal_revisions(receipt_id) WHERE supersedes IS NULL;
CREATE INDEX IF NOT EXISTS receipt_journal_revision_receipt
    ON receipt_journal_revisions(receipt_id, sequence);
CREATE TRIGGER IF NOT EXISTS receipt_journal_receipts_no_update
BEFORE UPDATE ON receipt_journal_receipts BEGIN SELECT RAISE(ABORT, 'immutable receipt'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_receipts_no_delete
BEFORE DELETE ON receipt_journal_receipts BEGIN SELECT RAISE(ABORT, 'immutable receipt'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_revisions_no_update
BEFORE UPDATE ON receipt_journal_revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_revisions_no_delete
BEFORE DELETE ON receipt_journal_revisions BEGIN SELECT RAISE(ABORT, 'immutable revision'); END;
CREATE TABLE IF NOT EXISTS receipt_journal_objects (
    object_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('source_snapshot', 'generation')),
    payload TEXT NOT NULL,
    received_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE IF NOT EXISTS receipt_journal_generation_commits (
    object_id TEXT PRIMARY KEY REFERENCES receipt_journal_objects(object_id),
    payload TEXT NOT NULL,
    recorded_utc TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TRIGGER IF NOT EXISTS receipt_journal_objects_no_update
BEFORE UPDATE ON receipt_journal_objects BEGIN SELECT RAISE(ABORT, 'immutable object'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_objects_no_delete
BEFORE DELETE ON receipt_journal_objects BEGIN SELECT RAISE(ABORT, 'immutable object'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_commits_no_update
BEFORE UPDATE ON receipt_journal_generation_commits BEGIN SELECT RAISE(ABORT, 'immutable generation commit'); END;
CREATE TRIGGER IF NOT EXISTS receipt_journal_commits_no_delete
BEFORE DELETE ON receipt_journal_generation_commits BEGIN SELECT RAISE(ABORT, 'immutable generation commit'); END;
"""


class ReceiptJournal:
    """One connection per instance; callers close it or use a context manager.

    Append methods return True on insertion, False for an exact replay. SQLite
    serializes writers before checking IDs/chain heads. Client-supplied receipt
    fields cannot replace the journal's database-owned insertion timestamps.
    """

    def __init__(self, path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(_SCHEMA)
        if [r[0] for r in self._db.execute("SELECT version FROM receipt_journal_meta")] != [1]:
            self._db.close()
            raise ValueError("unsupported_journal_schema")

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def append_receipt(self, receipt):
        validate_probability_receipt(receipt)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            return self._append_receipt(receipt)

    def _append_receipt(self, receipt):
        """Caller owns the write transaction; never independently commits."""
        payload = _json(receipt)
        same = self._db.execute(
            "SELECT payload FROM receipt_journal_receipts WHERE receipt_id = ? "
            "OR (domain = ? AND task = ? AND source_receipt_id = ?)",
            (receipt["receipt_id"], receipt["domain"], receipt["task"], receipt["source_receipt_id"])
        ).fetchall()
        if same:
            if len(same) != 1 or same[0]["payload"] != payload:
                raise JournalConflict("receipt_or_source_id_conflict")
            return False
        self._db.execute(
            "INSERT INTO receipt_journal_receipts "
            "(receipt_id,domain,task,source_receipt_id,payload) VALUES (?,?,?,?,?)",
            (receipt["receipt_id"], receipt["domain"], receipt["task"], receipt["source_receipt_id"], payload))
        return True

    def append_object(self, kind, object_id, payload):
        """Store exact content-addressed inputs; an object ACK is not ingestion."""
        if kind == "generation":
            if not isinstance(payload, dict) or payload.get("envelope_id") != object_id:
                raise ValueError("generation_object_id_mismatch")
            expected = fingerprint({k: v for k, v in payload.items() if k != "envelope_id"})
        elif kind == "source_snapshot":
            expected = fingerprint(payload)
        else:
            raise ValueError("unsupported_object_kind")
        if object_id != expected:
            raise ValueError("object_hash_mismatch")
        encoded = _json(payload)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            same = self._db.execute("SELECT kind,payload FROM receipt_journal_objects WHERE object_id = ?",
                                    (object_id,)).fetchone()
            if same:
                if same["kind"] != kind or same["payload"] != encoded:
                    raise JournalConflict("object_id_conflict")
                return False
            self._db.execute("INSERT INTO receipt_journal_objects (object_id,kind,payload) VALUES (?,?,?)",
                             (object_id, kind, encoded))
        return True

    def get_object(self, object_id):
        row = self._db.execute("SELECT kind,payload,received_utc FROM receipt_journal_objects WHERE object_id = ?",
                               (object_id,)).fetchone()
        return ({"object_id": object_id, "kind": row["kind"], "payload": json.loads(row["payload"]),
                 "received_utc": row["received_utc"]} if row else None)

    def generation_ack(self, object_id):
        row = self._db.execute("SELECT payload FROM receipt_journal_generation_commits WHERE object_id = ?",
                               (object_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def generations(self):
        """Only durably finalized generations, with a database-owned as-of clock.

        An uploaded object alone is not an accepted generation. This preserves
        later offered-card membership without rewriting first-seen receipts.
        """
        return [{"object_id": row["object_id"], "payload": json.loads(row["payload"]),
                 "ack": json.loads(row["ack"]), "recorded_utc": row["recorded_utc"]}
                for row in self._db.execute(
                    "SELECT o.object_id,o.payload,c.payload AS ack,c.recorded_utc FROM receipt_journal_objects o "
                    "JOIN receipt_journal_generation_commits c ON c.object_id=o.object_id "
                    "WHERE o.kind='generation' ORDER BY c.recorded_utc,o.object_id")]

    def commit_generation(self, object_id, receipts, rejections):
        """Commit accepted receipts AND the complete result atomically.

        Source validation belongs to the NFL boundary. The store additionally
        enforces an exhaustive disjoint partition, immutable replay and rollback
        on any failed insert. No partially written generation can be ACKed.
        """
        for receipt in receipts:
            validate_probability_receipt(receipt)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            obj = self.get_object(object_id)
            if obj is None or obj["kind"] != "generation":
                raise ValueError("generation_object_missing")
            expected = [row["receipt_id"] for row in obj["payload"].get("receipts", [])]
            accepted = [row["source_receipt_id"] for row in receipts]
            rejected = [row.get("source_receipt_id") for row in rejections]
            if (len(set(expected)) != len(expected) or len(set(accepted + rejected)) != len(accepted + rejected)
                    or set(expected) != set(accepted + rejected)):
                raise ValueError("generation_result_partition_mismatch")
            old = self.generation_ack(object_id)
            if old:
                stored = [(r["source_receipt_id"], r["receipt_id"]) for r in old["receipts"]]
                incoming = [(r["source_receipt_id"], r["receipt_id"]) for r in receipts]
                if sorted(stored) != sorted(incoming) or old["rejections"] != rejections:
                    raise JournalConflict("generation_commit_conflict")
                return {**old, "receipts": [{**r, "status": "duplicate"} for r in old["receipts"]]}
            rows = []
            for receipt in receipts:
                added = self._append_receipt(receipt)
                rows.append({"source_receipt_id": receipt["source_receipt_id"],
                             "receipt_id": receipt["receipt_id"],
                             "status": "recorded" if added else "duplicate"})
            ack = {"version": 1, "object_id": object_id, "durable": True,
                   "receipts": rows, "rejections": rejections}
            self._db.execute("INSERT INTO receipt_journal_generation_commits (object_id,payload) VALUES (?,?)",
                             (object_id, _json(ack)))
        return ack

    def get_receipt(self, receipt_id):
        row = self._db.execute("SELECT payload FROM receipt_journal_receipts WHERE receipt_id = ?",
                               (receipt_id,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def receipts(self):
        return [json.loads(row["payload"]) for row in self._db.execute(
            "SELECT payload FROM receipt_journal_receipts ORDER BY receipt_id")]

    def append_resolution(self, resolution, *, revision_id, supersedes=None, reason=None):
        for key, value in (("revision_id", revision_id), ("supersedes", supersedes)):
            if (value is None and key == "supersedes"):
                continue
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("invalid_" + key)
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("invalid_revision_reason")
        if supersedes is not None and reason is None:
            raise ValueError("correction_reason_required")
        if not isinstance(resolution, dict):
            raise ValueError("missing_outcome")
        if not isinstance(resolution.get("receipt_id"), str):
            raise ValueError("invalid_outcome_receipt_id")
        envelope = {"version": 1, "revision_id": revision_id, "supersedes": supersedes,
                    "reason": reason, "resolution": resolution}
        payload = _json(envelope)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            receipt = self.get_receipt(resolution.get("receipt_id"))
            if receipt is None:
                raise ValueError("unknown_receipt")
            validate_journal_resolution(receipt, resolution)
            old = self._db.execute("SELECT payload FROM receipt_journal_revisions WHERE revision_id = ?",
                                   (revision_id,)).fetchone()
            if old:
                if old["payload"] != payload:
                    raise JournalConflict("revision_id_conflict")
                return False
            latest = self._db.execute(
                "SELECT revision_id,payload FROM receipt_journal_revisions "
                "WHERE receipt_id = ? ORDER BY sequence DESC LIMIT 1", (receipt["receipt_id"],)).fetchone()
            if supersedes != (latest["revision_id"] if latest else None):
                raise JournalConflict("correction_must_extend_current_receipt_head")
            if latest:
                previous = json.loads(latest["payload"])["resolution"]
                if instant(resolution["resolved_utc"]) < instant(previous["resolved_utc"]):
                    raise ValueError("correction_time_regressed")
            self._db.execute(
                "INSERT INTO receipt_journal_revisions (revision_id,receipt_id,supersedes,payload) VALUES (?,?,?,?)",
                (revision_id, receipt["receipt_id"], supersedes, payload))
        return True

    def resolution_history(self, receipt_id):
        return [{**json.loads(row["payload"]), "recorded_utc": row["recorded_utc"]}
                for row in self._db.execute(
                    "SELECT payload,recorded_utc FROM receipt_journal_revisions WHERE receipt_id = ? ORDER BY sequence",
                    (receipt_id,))]

    def latest_resolutions(self):
        return [json.loads(row["payload"])["resolution"] for row in self._db.execute(
            "SELECT r.payload FROM receipt_journal_revisions r WHERE NOT EXISTS "
            "(SELECT 1 FROM receipt_journal_revisions newer WHERE newer.supersedes = r.revision_id) "
            "ORDER BY r.receipt_id")]
