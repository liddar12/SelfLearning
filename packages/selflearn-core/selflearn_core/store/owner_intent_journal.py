"""Local immutable owner decisions, separate from forecast/outcome history.

The SQLite file owner is trusted; this is not a role/tenant authorization layer.
Object references are checked in the same serialized transaction. Exact domain
IDs, actual serving state and operator authentication are NOT verified here.
"""
import sqlite3

from .. import owner_intents as dto
from ..probability_receipts import fingerprint, instant
from .receipt_journal import ReceiptJournal, JournalConflict


_SCHEMA = """
CREATE TABLE IF NOT EXISTS owner_intent_events_v1 (
 ledger_seq INTEGER PRIMARY KEY AUTOINCREMENT,
 authority_id TEXT NOT NULL, namespace_json TEXT NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence BETWEEN 1 AND 256),
 command_id TEXT NOT NULL, event_id TEXT NOT NULL UNIQUE,
 previous_event_id TEXT, payload_json TEXT NOT NULL,
 UNIQUE(authority_id,namespace_json,sequence),
 UNIQUE(authority_id,namespace_json,command_id)
);
CREATE INDEX IF NOT EXISTS owner_intent_scope_v1
 ON owner_intent_events_v1(authority_id,namespace_json,ledger_seq);
CREATE TRIGGER IF NOT EXISTS owner_intent_no_update_v1 BEFORE UPDATE ON owner_intent_events_v1
 BEGIN SELECT RAISE(ABORT,'immutable owner intent'); END;
CREATE TRIGGER IF NOT EXISTS owner_intent_no_delete_v1 BEFORE DELETE ON owner_intent_events_v1
 BEGIN SELECT RAISE(ABORT,'immutable owner intent'); END;
"""


class OwnerIntentJournal(ReceiptJournal):
    def __init__(self, path, *, authority_id, actor_ref, page_size=100):
        self.authority_id, self.actor_ref = dto.digest(authority_id), dto.actor(actor_ref)
        dto.need(type(page_size) is int and 1 <= page_size <= 100)
        self.page_size = page_size
        super().__init__(path)
        self._db.executescript(_SCHEMA)

    def _rows(self, namespace, watermark=None):
        return self._db.execute("SELECT ledger_seq,payload_json FROM owner_intent_events_v1 "
            "WHERE authority_id=? AND namespace_json=? AND ledger_seq<=? ORDER BY sequence",
            (self.authority_id, dto.canonical(dto.namespace(namespace)), watermark if watermark is not None else 9223372036854775807)).fetchall()

    def append_owner_intent(self, command):
        command = dto.validate_command(command, authority_id=self.authority_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            rows = self._rows(command["namespace"])
            events = [dto.decode(r["payload_json"], limit=dto.MAX_COMMAND_BYTES + 4096) for r in rows]
            dto.validate_history(events, authority_id=self.authority_id, expected_namespace=command["namespace"])
            for event in events:
                if event["command"]["command_id"] == command["command_id"]:
                    dto.need(dto.canonical(event["command"]) == dto.canonical(command))
                    return dto.acknowledgement(event)
            if command["expected_head_event_id"] != (events[-1]["event_id"] if events else None):
                raise JournalConflict("owner_head_conflict")
            recorded = self._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]
            for ref in command["pins"]["evidence_object_ids"]:
                obj = self.get_object(ref)
                dto.need(obj is not None and obj["kind"] == "source_snapshot"
                    and fingerprint(obj["payload"]) == ref and instant(obj["received_utc"]) <= instant(recorded), "invalid_owner_evidence_reference")
            event = dto.make_event(command, events, authority_id=self.authority_id,
                                   actor_ref=self.actor_ref, recorded_utc=recorded)
            self._db.execute("INSERT INTO owner_intent_events_v1(authority_id,namespace_json,sequence,command_id,event_id,previous_event_id,payload_json) VALUES(?,?,?,?,?,?,?)",
                (self.authority_id,dto.canonical(command["namespace"]),event["sequence"],command["command_id"],event["event_id"],event["previous_event_id"],dto.canonical(event)))
            return dto.acknowledgement(event)

    def read_owner_intents_page(self, namespace, *, after=None, watermark=None, limit=None):
        namespace = dto.namespace(namespace)
        size = self.page_size if limit is None else limit
        dto.need(type(size) is int and 1 <= size <= 100)
        cursor = dto.sequence(after) if after is not None else 0
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            ceiling = self._db.execute("SELECT COALESCE(MAX(ledger_seq),0) FROM owner_intent_events_v1").fetchone()[0]
            fence = dto.sequence(watermark) if watermark is not None else ceiling
            dto.need(0 <= cursor <= fence <= ceiling, "invalid_owner_watermark")
            rows = self._rows(namespace, fence)
            events = [dto.decode(r["payload_json"], limit=dto.MAX_COMMAND_BYTES + 4096) for r in rows]
            dto.validate_history(events, authority_id=self.authority_id, expected_namespace=namespace)
            selected = [r for r in rows if r["ledger_seq"] > cursor][:size + 1]
            has_more = len(selected) > size; selected = selected[:size]
            page = {"version": 1, "authority_id": self.authority_id, "namespace": namespace,
                "rows": [{"event": dto.decode(r["payload_json"], limit=dto.MAX_COMMAND_BYTES + 4096), "ledger_seq": str(r["ledger_seq"])} for r in selected],
                "next_after": str(selected[-1]["ledger_seq"]) if has_more else None,
                "watermark": str(fence), "head_event_id": events[-1]["event_id"] if events else None,
                "head_sequence": len(events), "observed_utc": self._db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]}
            dto.snapshot(page)
            return page

    def read_owner_intents(self, namespace):
        page = self.read_owner_intents_page(namespace, limit=100)
        rows = list(page["rows"])
        cursor = page["next_after"]
        while cursor is not None:
            current = self.read_owner_intents_page(namespace, after=cursor, watermark=page["watermark"], limit=100)
            rows.extend(current["rows"]); cursor = current["next_after"]
        return dto.complete_snapshot([r["event"] for r in rows], authority_id=self.authority_id,
            expected_namespace=namespace, watermark=page["watermark"], observed_utc=page["observed_utc"])
