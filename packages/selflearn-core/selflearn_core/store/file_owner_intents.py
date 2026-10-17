"""Repository-compatible persistence for the existing generic owner-intent DTO.

The configured filesystem writer, actor and evidence reader are trusted. This
port does not authenticate an operator, interpret domain scope, approve a model
or attest Git publication. Exact retry returns a historical local durable ACK,
not current authority. Readers pin one immutable chain without taking a lock.
Only append takes a nonblocking process lock and replaces its small head last.
"""
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .. import owner_intents as dto
from ..probability_receipts import fingerprint, instant
from .receipt_journal import JournalConflict

MAX_EVENT_BYTES = dto.MAX_COMMAND_BYTES + 4096
MAX_EVIDENCE_BYTES = 128 * 1024 * 1024
MAX_TOTAL_EVIDENCE_BYTES = 256 * 1024 * 1024
HEAD_KEYS = {"version", "kind", "authority_id", "namespace", "head_sequence", "head_event_id", "pointer_id"}


def _now():
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


class FileOwnerIntentJournal:
    def __init__(self, root, *, authority_id, actor_ref=None, evidence_reader=None, clock=_now):
        path = Path(root).absolute()
        dto.need(not path.is_symlink(), "owner_file_symlink")
        self.root = path.resolve()
        self.authority_id = dto.digest(authority_id)
        self.actor_ref = dto.actor(actor_ref) if actor_ref is not None else None
        dto.need(evidence_reader is None or callable(evidence_reader))
        dto.need(callable(clock))
        self.evidence_reader, self.clock = evidence_reader, clock

    def _path(self, *parts):
        path = self.root
        dto.need(not path.is_symlink(), "owner_file_symlink")
        for part in parts:
            dto.need(type(part) is str and part not in ("", ".", "..") and "/" not in part and "\\" not in part)
            path = path / part
            dto.need(not path.is_symlink(), "owner_file_symlink")
        return path

    def _scope(self, namespace):
        return fingerprint({"authority_id": self.authority_id, "namespace": dto.namespace(namespace)})[7:]

    def _head_path(self, namespace):
        return self._path("heads", self._scope(namespace) + ".json")

    def _read(self, path, maximum):
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            dto.need(stat.S_ISREG(info.st_mode) and info.st_size <= maximum, "owner_file_size_or_type")
            raw = stream.read(maximum + 1)
        dto.need(len(raw) <= maximum, "owner_file_size_or_type")
        return dto.decode(raw.decode("utf-8"), limit=maximum)

    def _head(self, namespace):
        path = self._head_path(namespace)
        if not path.exists():
            return None
        value = self._read(path, 4096)
        dto.need(type(value) is dict and set(value) == HEAD_KEYS
                 and type(value["version"]) is int and value["version"] == 1
                 and value["kind"] == "selflearn.file_owner_head.v1")
        dto.need(value["authority_id"] == self.authority_id
                 and dto.canonical(value["namespace"]) == dto.canonical(dto.namespace(namespace)))
        dto.need(type(value["head_sequence"]) is int and 1 <= value["head_sequence"] <= dto.MAX_EVENTS)
        dto.digest(value["head_event_id"])
        dto.need(value["pointer_id"] == fingerprint({k: v for k, v in value.items() if k != "pointer_id"}),
                 "owner_file_head_hash_mismatch")
        return value

    def _events(self, namespace, head):
        events, current, total = [], head["head_event_id"] if head else None, 2
        for sequence in range(head["head_sequence"] if head else 0, 0, -1):
            dto.digest(current)
            event = self._read(self._path("events", current[7:] + ".json"), MAX_EVENT_BYTES)
            dto.validate_event(event, authority_id=self.authority_id, expected_namespace=namespace)
            dto.need(event["event_id"] == current and event["sequence"] == sequence, "incomplete_owner_history")
            total += len(dto.canonical(event)) + (1 if events else 0)
            dto.need(total <= dto.MAX_HISTORY_BYTES, "owner_intent_capacity_exceeded")
            events.append(event)
            current = event["previous_event_id"]
        dto.need(current is None, "incomplete_owner_history")
        events.reverse()
        dto.validate_history(events, authority_id=self.authority_id, expected_namespace=namespace)
        return events

    @contextmanager
    def lock(self, namespace):
        """Local cooperating writers only; no remote Git/hosted transaction claim."""
        import fcntl
        key = hashlib.sha256((str(self.root) + ":" + self._scope(namespace)).encode()).hexdigest()
        path = Path(tempfile.gettempdir()) / ("selflearn-owner-" + key + ".lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as stream:
            dto.need(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "owner_file_lock_type")
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield

    @staticmethod
    def _sync(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0))
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _write(self, path, value, *, immutable):
        raw = dto.canonical(value).encode("utf-8")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path(*path.relative_to(self.root).parts)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".owner-", delete=False) as stream:
                temporary = Path(stream.name)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            if immutable:
                try:
                    os.link(temporary, path, follow_symlinks=False)
                except FileExistsError:
                    dto.need(dto.canonical(self._read(path, MAX_EVENT_BYTES)) == raw.decode("utf-8"),
                             "owner_file_immutable_conflict")
            else:
                os.replace(temporary, path)
            self._sync(path.parent)
            self._sync(self.root)
            self._sync(self.root.parent)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def _read_evidence(self, oid):
        """Read the configured immutable-object port; this is not source auth."""
        dto.digest(oid)
        dto.need(callable(self.evidence_reader), "owner_file_evidence_reader_unavailable")
        def pairs(items):
            result = {}
            for key, value in items:
                dto.need(key not in result, "duplicate_owner_evidence_key")
                result[key] = value
            return result
        def reject(_):
            raise ValueError("nonfinite_owner_evidence")
        raw = self.evidence_reader(oid)
        dto.need(type(raw) is bytes and 0 < len(raw) <= MAX_EVIDENCE_BYTES, "invalid_owner_evidence_bytes")
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
        dto.need(type(value) is dict and set(value) == {"object_id", "kind", "payload", "received_utc"}
                 and value["object_id"] == oid and value["kind"] == "source_snapshot"
                 and fingerprint(value["payload"]) == oid, "invalid_owner_evidence_reference")
        instant(value["received_utc"])
        return value, len(raw)

    def get_object(self, object_id):
        """ReceiptJournal-compatible immutable evidence lookup, without writes."""
        return self._read_evidence(object_id)[0]

    def _evidence(self, refs, recorded):
        """Reader returns exact bounded JSON bytes, not a caller's valid flag."""
        total = 0
        for oid in refs:
            value, size = self._read_evidence(oid)
            total += size
            dto.need(total <= MAX_TOTAL_EVIDENCE_BYTES, "owner_file_evidence_limit")
            dto.need(instant(value["received_utc"]) <= instant(recorded), "invalid_owner_evidence_reference")

    def append_owner_intent(self, command):
        dto.need(self.actor_ref is not None, "owner_file_read_only")
        command = dto.validate_command(command, authority_id=self.authority_id)
        namespace = command["namespace"]
        with self.lock(namespace):
            head = self._head(namespace)
            events = self._events(namespace, head)
            for event in events:
                if event["command"]["command_id"] == command["command_id"]:
                    dto.need(dto.canonical(event["command"]) == dto.canonical(command))
                    # A previous reply can have failed after rename but before
                    # directory sync. Retry does not refit/re-authorize anything.
                    self._sync(self._head_path(namespace).parent)
                    self._sync(self.root)
                    self._sync(self.root.parent)
                    return dto.acknowledgement(event)
            if command["expected_head_event_id"] != (head["head_event_id"] if head else None):
                raise JournalConflict("owner_head_conflict")
            recorded = self.clock()
            event = dto.make_event(command, events, authority_id=self.authority_id,
                                   actor_ref=self.actor_ref, recorded_utc=recorded)
            self._evidence(command["pins"]["evidence_object_ids"], recorded)
            self._write(self._path("events", event["event_id"][7:] + ".json"), event, immutable=True)
            if self._head(namespace) != head:
                raise JournalConflict("owner_head_conflict")
            new = {"version": 1, "kind": "selflearn.file_owner_head.v1", "authority_id": self.authority_id,
                   "namespace": namespace, "head_sequence": event["sequence"], "head_event_id": event["event_id"]}
            new["pointer_id"] = fingerprint(new)
            self._write(self._head_path(namespace), new, immutable=False)
            return dto.acknowledgement(event)

    def read_owner_intents(self, namespace):
        namespace = dto.namespace(namespace)
        head = self._head(namespace)
        events = self._events(namespace, head)
        return dto.complete_snapshot(events, authority_id=self.authority_id, expected_namespace=namespace,
            watermark=str(head["head_sequence"] if head else 0), observed_utc=self.clock())
