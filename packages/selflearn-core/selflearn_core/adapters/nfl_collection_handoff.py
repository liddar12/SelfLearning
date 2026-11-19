"""Immutable NFL producer run receipts sharing the existing forecast journal."""
from __future__ import annotations

import json
import re

from .. import learning_jobs as dto
from ..probability_receipts import fingerprint, instant
from ..store.receipt_journal import JournalConflict
from ..store.supabase_journal import SupabaseReceiptJournal, _canonical, _decode
from ..store.supabase_store import StoreConflict, StoreError
from .nfl_generation import SOURCE_NAMES

REPOSITORY = re.compile(r"[a-z0-9_.-]{1,100}/[a-z0-9_.-]{1,100}\Z")
RUN_NUMBER = re.compile(r"[1-9][0-9]{0,18}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")
WORKFLOWS = frozenset(("daily.yml", "gameday.yml"))
MAX_SCAN = 50
INTENT_KEYS = {"version", "workflow", "run_id", "run_attempt", "generation_object_id", "generation_id",
               "recorded_utc", "latest_kickoff_utc", "source_snapshots"}
PUBLISHER_KEYS = {"version", "intent_id", "published_sha", "published_pool_id", "previous_pool_id"}
LOCATOR_KEYS = {"workflow", "run_id", "run_attempt"}


def fields(value, keys):
    if type(value) is not dict or set(value) != keys:
        raise ValueError("invalid_collection_fields")


def versioned(value, keys):
    fields(value, keys)
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid_collection_version")


def scope(repository, workflow, run_id, run_attempt):
    if (type(repository) is not str or not REPOSITORY.fullmatch(repository) or type(workflow) is not str
            or workflow not in WORKFLOWS or any(type(v) is not str or not RUN_NUMBER.fullmatch(v)
                                                for v in (run_id, run_attempt))):
        raise ValueError("invalid_collection_scope")
    return repository, workflow, run_id, run_attempt


def intent_record(repository, value):
    versioned(value, INTENT_KEYS)
    scope(repository, value["workflow"], value["run_id"], value["run_attempt"])
    for name in ("generation_object_id", "generation_id"):
        dto.digest(value[name])
    fields(value["source_snapshots"], SOURCE_NAMES)
    for identity in value["source_snapshots"].values():
        dto.digest(identity)
    if dto.clock(value["recorded_utc"]) >= dto.clock(value["latest_kickoff_utc"]):
        raise ValueError("invalid_collection_capture_order")
    record = {**value, "source_snapshots": dict(value["source_snapshots"]), "repository": repository,
              "kind": "nfl.collection_intent.v1"}
    return {**record, "intent_id": fingerprint(record)}


def publisher_record(intent, value):
    versioned(value, PUBLISHER_KEYS)
    if dto.digest(value["intent_id"]) != intent["intent_id"]:
        raise JournalConflict("collection_publisher_intent_conflict")
    sha, pool_id, previous = (value[k] for k in ("published_sha", "published_pool_id", "previous_pool_id"))
    if sha is None:
        if pool_id is not None or previous is not None:
            raise ValueError("invalid_collection_noop")
        disposition = "noop"
    else:
        if type(sha) is not str or not SHA.fullmatch(sha):
            raise ValueError("invalid_collection_publish_sha")
        dto.digest(pool_id)
        if previous is not None:
            dto.digest(previous)
        disposition = ("mismatch" if pool_id != intent["source_snapshots"]["leg_pool"] else
                       "unchanged" if pool_id == previous else "match")
    record = {**value, "kind": "nfl.collection_publisher_receipt.v1", "disposition": disposition}
    return {**record, "receipt_id": fingerprint(record)}


def locator(intent):
    return {key: intent[key] for key in LOCATOR_KEYS} | {"intent_id": intent["intent_id"]}


def object_refs(intent):
    refs = {identity: "source_snapshot" for identity in intent["source_snapshots"].values()}
    if intent["generation_object_id"] in refs:
        raise ValueError("collection_object_kind_conflict")
    refs[intent["generation_object_id"]] = "generation"
    return refs


def validate_graph(intent, generation):
    """Bind the durable envelope to its producer intent before any finalization."""
    gen = generation["generations"][0]
    if (gen["generation_id"] != intent["generation_id"] or gen["recorded_utc"] != intent["recorded_utc"]
            or gen["source_snapshots"] != intent["source_snapshots"] or not generation["receipts"]
            or max(instant(row["kickoff_utc"]) for row in generation["receipts"])
                != instant(intent["latest_kickoff_utc"])):
        raise ValueError("collection_generation_intent_mismatch")


def bounded_scan(limit, after):
    if type(limit) is not int or not 1 <= limit <= MAX_SCAN:
        raise ValueError("invalid_collection_scan_limit")
    if after is not None:
        dto.digest(after)


def normalize_view(value, repository, key):
    """Check independently persisted clocks, scope, IDs, and exact JSON."""
    fields(value, {"version", "observed_utc", "intent", "publisher_receipt", "missing_object_ids"})
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("invalid_collection_view")
    observed = dto.clock(value["observed_utc"])
    original = value["intent"]
    if original is None:
        if value["publisher_receipt"] is not None or value["missing_object_ids"] != []:
            raise ValueError("invalid_collection_absence")
        return value
    fields(original, {"recorded_utc", "document_json"})
    intent = _decode(original["document_json"])
    if (intent_record(repository, {k: intent[k] for k in INTENT_KEYS}) != intent
            or tuple(intent[k] for k in ("workflow", "run_id", "run_attempt")) != key
            or not dto.clock(intent["recorded_utc"]) <= dto.clock(original["recorded_utc"]) <= observed):
        raise ValueError("invalid_collection_stored_intent")
    missing = value["missing_object_ids"]
    if type(missing) is not list or missing != sorted(set(missing)) or not set(missing) <= set(object_refs(intent)):
        raise ValueError("invalid_collection_missing_objects")
    saved = value["publisher_receipt"]
    if saved is not None:
        fields(saved, {"recorded_utc", "document_json"})
        document = _decode(saved["document_json"])
        if (publisher_record(intent, {k: document[k] for k in PUBLISHER_KEYS}) != document
                or not dto.clock(original["recorded_utc"]) <= dto.clock(saved["recorded_utc"]) <= observed):
            raise ValueError("invalid_collection_stored_publisher")
        saved = {**document, "stored_utc": saved["recorded_utc"]}
    return {**value, "intent": {**intent, "stored_utc": original["recorded_utc"]}, "publisher_receipt": saved}


def normalize_scan(value, repository, limit, after):
    versioned(value, {"version", "observed_utc", "pending", "next_after"})
    dto.clock(value["observed_utc"])
    rows, cursor = value["pending"], value["next_after"]
    if type(rows) is not list or len(rows) > limit:
        raise ValueError("invalid_collection_pending")
    ids = []
    for row in rows:
        fields(row, LOCATOR_KEYS | {"intent_id"})
        scope(repository, row["workflow"], row["run_id"], row["run_attempt"])
        ids.append(dto.digest(row["intent_id"]))
    if (ids != sorted(set(ids)) or ids and after is not None and ids[0] <= after
            or cursor is not None and (dto.digest(cursor) <= (after or "") or ids and cursor < ids[-1])):
        raise ValueError("invalid_collection_pending_cursor")
    return value


class SupabaseCollectionHandoff:
    """Use the existing validated journal project and transport for scoped receipts."""

    def __init__(self, journal, repository):
        if not isinstance(journal, SupabaseReceiptJournal):
            raise ValueError("collection_supabase_boundary_required")
        self.journal = journal
        self.repository = repository

    def _call(self, operation, key=(None, None, None), *, record=None, limit=None, after=None):
        try:
            return self.journal._request("POST", "/rest/v1/rpc/selflearn_nfl_collection_handoff_v1", data={
                "p_operation": operation, "p_repository": self.repository, "p_workflow": key[0],
                "p_run_id": key[1], "p_run_attempt": key[2], "p_document_json": _canonical(record) if record else None,
                "p_limit": limit, "p_after": after})
        except StoreConflict:
            raise JournalConflict("immutable_collection_conflict") from None
        except StoreError as error:
            if error.status == 422:
                raise ValueError("invalid_collection_store_request") from None
            raise
        except Exception:
            raise StoreError(503) from None

    def _view(self, operation, key, record=None):
        scope(self.repository, *key)
        value = self._call(operation, key, record=record)
        try:
            return normalize_view(value, self.repository, key)
        except (JournalConflict, ValueError, TypeError, KeyError):
            raise StoreError(502) from None

    def read(self, key):
        return self._view("read", key)

    def put(self, key, stage, record):
        view = self._view("put_" + stage, key, record)
        stored = view["intent" if stage == "intent" else "publisher_receipt"]
        if stored is None or _canonical({k: v for k, v in stored.items() if k != "stored_utc"}) != _canonical(record):
            raise StoreError(502)
        return view

    def pending(self, limit, after):
        bounded_scan(limit, after)
        value = self._call("pending", limit=limit, after=after)
        try:
            return normalize_scan(value, self.repository, limit, after)
        except (ValueError, TypeError, KeyError):
            raise StoreError(502) from None


_SQLITE_SCHEMA = """
CREATE TABLE IF NOT EXISTS nfl_collection_handoff_v1 (
    repository TEXT NOT NULL, workflow TEXT NOT NULL, run_id TEXT NOT NULL, run_attempt TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('intent', 'publisher')), record_id TEXT NOT NULL,
    document_json TEXT NOT NULL, recorded_utc TEXT NOT NULL,
    PRIMARY KEY(repository, workflow, run_id, run_attempt, stage), UNIQUE(record_id)
);
CREATE INDEX IF NOT EXISTS nfl_collection_handoff_scan_v1
    ON nfl_collection_handoff_v1(repository, stage, record_id);
CREATE TRIGGER IF NOT EXISTS nfl_collection_handoff_no_update_v1 BEFORE UPDATE ON nfl_collection_handoff_v1
    BEGIN SELECT RAISE(ABORT, 'immutable NFL collection receipt'); END;
CREATE TRIGGER IF NOT EXISTS nfl_collection_handoff_no_delete_v1 BEFORE DELETE ON nfl_collection_handoff_v1
    BEGIN SELECT RAISE(ABORT, 'immutable NFL collection receipt'); END;
"""


class SQLiteCollectionHandoff:
    """Explicit durable local test adapter using the same receipt journal file."""

    def __init__(self, journal, repository):
        self.journal = journal
        self.repository = repository
        self._db = journal._db
        self._db.executescript(_SQLITE_SCHEMA)

    def _now(self):
        return self._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]

    def _read(self, key):
        rows = {row["stage"]: {"document_json": row["document_json"], "recorded_utc": row["recorded_utc"]}
                for row in self._db.execute("SELECT stage,document_json,recorded_utc FROM nfl_collection_handoff_v1 "
                    "WHERE repository=? AND workflow=? AND run_id=? AND run_attempt=?", (self.repository, *key))}
        original = rows.get("intent")
        missing = []
        if original is not None:
            for identity, kind in object_refs(json.loads(original["document_json"])).items():
                if self._db.execute("SELECT 1 FROM receipt_journal_objects WHERE object_id=? AND kind=?",
                                    (identity, kind)).fetchone() is None:
                    missing.append(identity)
        view = {"version": 1, "observed_utc": self._now(), "intent": original,
                "publisher_receipt": rows.get("publisher"), "missing_object_ids": sorted(missing)}
        return normalize_view(view, self.repository, key)

    def read(self, key):
        scope(self.repository, *key)
        with self._db:
            self._db.execute("BEGIN")
            return self._read(key)

    def put(self, key, stage, record):
        scope(self.repository, *key)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            view = self._read(key)
            old = view["intent" if stage == "intent" else "publisher_receipt"]
            if old is not None:
                if _canonical({k: v for k, v in old.items() if k != "stored_utc"}) != _canonical(record):
                    raise JournalConflict("immutable_collection_conflict")
                return view
            if stage == "publisher":
                if view["intent"] is None or record["intent_id"] != view["intent"]["intent_id"]:
                    raise JournalConflict("collection_publisher_intent_conflict")
            elif dto.clock(record["recorded_utc"]) > dto.clock(view["observed_utc"]):
                raise ValueError("future_collection_clock")
            identity = record["intent_id" if stage == "intent" else "receipt_id"]
            self._db.execute("INSERT INTO nfl_collection_handoff_v1 VALUES (?,?,?,?,?,?,?,?)",
                (self.repository, *key, stage, identity, _canonical(record), view["observed_utc"]))
            return self._read(key)

    def pending(self, limit, after):
        bounded_scan(limit, after)
        with self._db:
            self._db.execute("BEGIN")
            observed = self._now()
            rows = self._db.execute("SELECT record_id,document_json FROM nfl_collection_handoff_v1 "
                "WHERE repository=? AND stage='intent' AND record_id>? ORDER BY record_id LIMIT ?",
                (self.repository, after or "", limit + 1)).fetchall()
            pending = []
            for row in rows[:limit]:
                intent = json.loads(row["document_json"])
                if instant(intent["latest_kickoff_utc"]) <= instant(observed):
                    continue
                key = tuple(intent[k] for k in ("workflow", "run_id", "run_attempt"))
                if not self._read(key)["missing_object_ids"]:
                    pending.append(locator(intent))
            return {"version": 1, "observed_utc": observed, "pending": pending,
                    "next_after": rows[limit - 1]["record_id"] if len(rows) > limit else None}
