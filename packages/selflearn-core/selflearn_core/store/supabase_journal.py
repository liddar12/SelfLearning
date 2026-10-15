"""Server-only durable receipt journal over the bounded Supabase RPC contract.

This adapter deliberately has no filesystem or SQLite fallback. JSON payloads
cross the database boundary as canonical *text*: Python equality (``True == 1``)
and JSONB numeric normalization must not turn changed evidence into a replay.
Database-owned insertion clocks are returned separately from client payloads.
"""
from __future__ import annotations

import json
import re

from ..outcome_revisions import validate_journal_resolution
from ..probability_receipts import fingerprint, instant, validate_probability_receipt
from .receipt_journal import JournalConflict
from .supabase_store import StoreConflict, StoreError, SupabaseStore


_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SEQUENCE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_RPC = {
    "receipt": "selflearn_journal_append_receipt_v1",
    "object": "selflearn_journal_append_object_v1",
    "generation": "selflearn_journal_commit_generation_v1",
    "revision": "selflearn_journal_append_resolution_v1",
    "read": "selflearn_journal_read_v1",
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False)


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = item
    return value


def _invalid_constant(_):
    raise ValueError("non_json_constant")


def _decode(payload, *, canonical=True):
    """Never accept a normalized, ambiguous or malformed upstream payload."""
    if not isinstance(payload, str):
        raise StoreError(502)
    try:
        value = json.loads(payload, object_pairs_hook=_pairs,
                           parse_constant=_invalid_constant)
        if canonical and _canonical(value) != payload:
            raise ValueError("noncanonical_payload")
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise StoreError(502) from None
    return value


def _clock(value):
    try:
        instant(value)
    except (ValueError, TypeError, OverflowError):
        raise StoreError(502) from None
    return value


def _hash(value):
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _object_payload(kind, object_id, payload):
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
    return _canonical(payload)


def _revision_envelope(resolution, revision_id, supersedes, reason):
    for name, value in (("revision_id", revision_id), ("supersedes", supersedes)):
        if name == "supersedes" and value is None:
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("invalid_" + name)
    if reason is not None and (not isinstance(reason, str) or not reason.strip()):
        raise ValueError("invalid_revision_reason")
    if supersedes is not None and reason is None:
        raise ValueError("correction_reason_required")
    if not isinstance(resolution, dict):
        raise ValueError("missing_outcome")
    if not isinstance(resolution.get("receipt_id"), str):
        raise ValueError("invalid_outcome_receipt_id")
    return {"version": 1, "revision_id": revision_id, "supersedes": supersedes,
            "reason": reason, "resolution": resolution}


def _partition(document, receipts, rejections):
    if (not isinstance(document, dict) or not isinstance(document.get("receipts"), list)
            or not isinstance(receipts, list) or not isinstance(rejections, list)):
        raise ValueError("generation_result_partition_mismatch")
    expected = []
    for row in document["receipts"]:
        if not isinstance(row, dict) or not _hash(row.get("receipt_id")):
            raise ValueError("generation_result_partition_mismatch")
        expected.append(row["receipt_id"])
    for row in receipts:
        validate_probability_receipt(row)
    for row in rejections:
        if (not isinstance(row, dict) or not _hash(row.get("source_receipt_id"))
                or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise ValueError("invalid_generation_rejection")
    accepted = [row["source_receipt_id"] for row in receipts]
    rejected = [row["source_receipt_id"] for row in rejections]
    if (len(set(expected)) != len(expected)
            or len(set(accepted + rejected)) != len(accepted + rejected)
            or set(expected) != set(accepted + rejected)):
        raise ValueError("generation_result_partition_mismatch")


class SupabaseReceiptJournal:
    """ReceiptJournal-compatible hosted adapter with an injectable transport.

    ``transport`` may be a SupabaseStore-like object or its bound ``request``
    callable. Explicit configuration is always validated; missing credentials
    cannot quietly select a local journal. One instance is safe to close or use
    as a context manager, matching the local journal's calling convention.
    """

    def __init__(self, url, key, *, transport=None, page_size=500):
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ValueError("journal_page_size_must_be_1_to_500")
        base = SupabaseStore(url, key)
        self._request = (base.request if transport is None else
                         transport.request if hasattr(transport, "request") else transport)
        if not callable(self._request):
            raise ValueError("journal_transport_must_be_callable")
        self.page_size = page_size

    def close(self):
        """The HTTP transport holds no database/file connection to close."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _rpc(self, operation, data):
        try:
            response = self._request("POST", "/rest/v1/rpc/" + _RPC[operation], data=data)
        except StoreConflict:
            raise JournalConflict("immutable_journal_conflict") from None
        except StoreError:
            raise
        except Exception:
            # Injected transports and SDK-free HTTP alternatives must obey the
            # same redaction boundary as SupabaseStore.request().
            raise StoreError(503) from None
        if (not isinstance(response, dict) or type(response.get("version")) is not int
                or response["version"] != 1):
            raise StoreError(502)
        return response

    def _append(self, operation, data):
        response = self._rpc(operation, data)
        if set(response) != {"version", "added"} or type(response.get("added")) is not bool:
            raise StoreError(502)
        return response["added"]

    @staticmethod
    def _sequence(value):
        if not isinstance(value, str) or not _SEQUENCE.fullmatch(value) or len(value) > 20:
            raise StoreError(502)
        return int(value)

    def _read(self, view, *, key=None, watermark=None):
        """Read an entire bounded-keyset view at one server-pinned watermark."""
        if key is not None and not isinstance(key, str):
            raise ValueError("journal_read_key_must_be_text")
        if watermark is not None:
            self._sequence(watermark)
        rows, after = [], None
        # A generation contains the entire producer document and may be close
        # to the 3 MiB request boundary. Count-bounded pages of 500 such rows
        # would exceed the transport's 8 MiB response cap by orders of magnitude.
        limit = 1 if view in {"status", "object", "receipt", "generation_ack", "generations"} else self.page_size
        while True:
            response = self._rpc("read", {
                "p_view": view, "p_key": key, "p_after": after,
                "p_limit": limit, "p_watermark": watermark,
            })
            if set(response) != {"version", "rows", "next_after", "watermark"}:
                raise StoreError(502)
            high = self._sequence(response["watermark"])
            if watermark is not None and response["watermark"] != watermark:
                raise StoreError(502)
            watermark = response["watermark"]
            page = response["rows"]
            if not isinstance(page, list) or len(page) > limit:
                raise StoreError(502)
            previous = self._sequence(after) if after is not None else -1
            for row in page:
                if not isinstance(row, dict):
                    raise StoreError(502)
                sequence = self._sequence(row.get("journal_seq"))
                if not previous < sequence <= high:
                    raise StoreError(502)
                previous = sequence
            next_after = response["next_after"]
            if next_after is not None:
                self._sequence(next_after)
                if not page or next_after != page[-1]["journal_seq"]:
                    raise StoreError(502)
            rows.extend(page)
            if next_after is None:
                return rows, watermark
            after = next_after

    @staticmethod
    def _single(rows):
        if len(rows) > 1:
            raise StoreError(502)
        return rows[0] if rows else None

    @staticmethod
    def _receipt_row(row):
        if set(row) != {"receipt_id", "payload_json", "received_utc", "journal_seq"}:
            raise StoreError(502)
        receipt = _decode(row["payload_json"])
        try:
            validate_probability_receipt(receipt)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None
        if receipt["receipt_id"] != row["receipt_id"]:
            raise StoreError(502)
        _clock(row["received_utc"])
        return receipt

    @staticmethod
    def _object_row(row):
        if set(row) != {"object_id", "kind", "payload_json", "received_utc", "journal_seq"}:
            raise StoreError(502)
        payload = _decode(row["payload_json"])
        try:
            _object_payload(row["kind"], row["object_id"], payload)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(502) from None
        return {"object_id": row["object_id"], "kind": row["kind"], "payload": payload,
                "received_utc": _clock(row["received_utc"])}

    @staticmethod
    def _ack(ack, object_id, document=None):
        if (not isinstance(ack, dict) or set(ack) != {"version", "object_id", "durable", "receipts", "rejections"}
                or type(ack.get("version")) is not int or ack["version"] != 1
                or ack.get("object_id") != object_id or ack.get("durable") is not True
                or not isinstance(ack.get("receipts"), list) or not isinstance(ack.get("rejections"), list)):
            raise StoreError(502)
        identities = []
        for row in ack["receipts"]:
            if (not isinstance(row, dict) or set(row) != {"source_receipt_id", "receipt_id", "status"}
                    or not _hash(row.get("source_receipt_id")) or not _hash(row.get("receipt_id"))
                    or row.get("status") not in ("recorded", "duplicate")):
                raise StoreError(502)
            identities.append(row["source_receipt_id"])
        for row in ack["rejections"]:
            if (not isinstance(row, dict) or not _hash(row.get("source_receipt_id"))
                    or not isinstance(row.get("reason"), str) or not row["reason"].strip()):
                raise StoreError(502)
            identities.append(row["source_receipt_id"])
        if len(set(identities)) != len(identities):
            raise StoreError(502)
        if document is not None:
            try:
                expected = [row["receipt_id"] for row in document["receipts"]]
                if len(set(expected)) != len(expected) or set(identities) != set(expected):
                    raise ValueError("generation_ack_partition_mismatch")
            except (ValueError, KeyError, TypeError):
                raise StoreError(502) from None
        return ack

    def status(self):
        rows, watermark = self._read("status")
        row = self._single(rows)
        if (row is None or set(row) != {"schema_version", "backend", "journal_seq"}
                or type(row.get("schema_version")) is not int or row["schema_version"] != 1
                or row.get("backend") != "postgres"):
            raise StoreError(502)
        return {"schema_version": 1, "backend": "postgres", "watermark": watermark}

    def append_receipt(self, receipt):
        validate_probability_receipt(receipt)
        return self._append("receipt", {"p_receipt_json": _canonical(receipt)})

    def append_object(self, kind, object_id, payload):
        encoded = _object_payload(kind, object_id, payload)
        # The database derives canonical unsigned text itself and verifies the
        # digest. Sending it again would nearly double a generation request.
        return self._append("object", {"p_kind": kind, "p_object_id": object_id,
            "p_payload_json": encoded, "p_hash_json": None})

    def get_object(self, object_id):
        rows, _ = self._read("object", key=object_id)
        row = self._single(rows)
        if row is None:
            return None
        result = self._object_row(row)
        if result["object_id"] != object_id:
            raise StoreError(502)
        return result

    def get_receipt(self, receipt_id):
        rows, _ = self._read("receipt", key=receipt_id)
        row = self._single(rows)
        if row is None:
            return None
        result = self._receipt_row(row)
        if result["receipt_id"] != receipt_id:
            raise StoreError(502)
        return result

    def receipts(self):
        rows, _ = self._read("receipts")
        result = self._receipt_rows(rows)
        # Match the local backend's deterministic ordering, independently of
        # insertion order used by the server's safe pagination cursor.
        return sorted(result, key=lambda row: row["receipt_id"])

    @classmethod
    def _receipt_rows(cls, rows):
        result = [cls._receipt_row(row) for row in rows]
        source_keys = {(r["domain"], r["task"], r["source_receipt_id"]) for r in result}
        if len({r["receipt_id"] for r in result}) != len(result) or len(source_keys) != len(result):
            raise StoreError(502)
        return result

    def generation_ack(self, object_id):
        rows, _ = self._read("generation_ack", key=object_id)
        row = self._single(rows)
        if row is None:
            return None
        if set(row) != {"object_id", "ack_json", "journal_seq"} or row["object_id"] != object_id:
            raise StoreError(502)
        # ACKs are server-constructed JSON, not content-addressed producer text.
        # Preserve strict JSON types/keys, but do not demand Python whitespace
        # or key ordering from PostgreSQL's json_build_object serializer.
        return self._ack(_decode(row["ack_json"], canonical=False), object_id)

    def commit_generation(self, object_id, receipts, rejections):
        obj = self.get_object(object_id)
        if obj is None or obj["kind"] != "generation":
            raise ValueError("generation_object_missing")
        _partition(obj["payload"], receipts, rejections)
        ack = self._ack(self._rpc("generation", {
            "p_object_id": object_id, "p_receipts_json": _canonical(receipts),
            "p_rejections_json": _canonical(rejections),
        }), object_id, obj["payload"])
        actual = sorted((r["source_receipt_id"], r["receipt_id"]) for r in ack["receipts"])
        expected = sorted((r["source_receipt_id"], r["receipt_id"]) for r in receipts)
        if actual != expected or _canonical(ack["rejections"]) != _canonical(rejections):
            raise StoreError(502)
        return ack

    @classmethod
    def _generation_rows(cls, rows):
        result, seen = [], set()
        for row in rows:
            if set(row) != {"object_id", "payload_json", "ack_json", "recorded_utc", "journal_seq"}:
                raise StoreError(502)
            payload = _decode(row["payload_json"])
            try:
                _object_payload("generation", row["object_id"], payload)
            except (ValueError, TypeError, KeyError, OverflowError):
                raise StoreError(502) from None
            if row["object_id"] in seen:
                raise StoreError(502)
            seen.add(row["object_id"])
            result.append({"object_id": row["object_id"], "payload": payload,
                "ack": cls._ack(_decode(row["ack_json"], canonical=False), row["object_id"], payload),
                "recorded_utc": _clock(row["recorded_utc"])})
        return sorted(result, key=lambda row: (instant(row["recorded_utc"]), row["object_id"]))

    def generations(self):
        rows, _ = self._read("generations")
        return self._generation_rows(rows)

    def append_resolution(self, resolution, *, revision_id, supersedes=None, reason=None):
        envelope = _revision_envelope(resolution, revision_id, supersedes, reason)
        receipt = self.get_receipt(resolution["receipt_id"])
        if receipt is None:
            raise ValueError("unknown_receipt")
        validate_journal_resolution(receipt, resolution)
        # Head selection and correction-time checks are in the same database
        # transaction as the insert, never a stale client-side compare/write.
        return self._append("revision", {"p_revision_json": _canonical(envelope)})

    @staticmethod
    def _revision_rows(rows, receipt_map):
        result, seen = [], set()
        for row in rows:
            if set(row) != {"revision_id", "payload_json", "recorded_utc", "journal_seq"}:
                raise StoreError(502)
            payload = _decode(row["payload_json"])
            try:
                if (not isinstance(payload, dict) or set(payload) != {"version", "revision_id", "supersedes", "reason", "resolution"}
                        or type(payload.get("version")) is not int or payload["version"] != 1):
                    raise ValueError("invalid_revision_envelope")
                _revision_envelope(payload["resolution"], payload["revision_id"], payload["supersedes"], payload["reason"])
                validate_journal_resolution(receipt_map[payload["resolution"]["receipt_id"]], payload["resolution"])
                if (payload["revision_id"] != row["revision_id"] or row["revision_id"] in seen
                        or instant(payload["resolution"]["resolved_utc"]) > instant(_clock(row["recorded_utc"]))):
                    raise ValueError("invalid_revision_identity_or_clock")
            except (ValueError, KeyError, TypeError, OverflowError):
                raise StoreError(502) from None
            seen.add(row["revision_id"])
            result.append({**payload, "recorded_utc": row["recorded_utc"]})
        return result

    def resolution_history(self, receipt_id):
        rows, watermark = self._read("resolution_history", key=receipt_id)
        if not rows:
            return []
        receipt_rows, _ = self._read("receipt", key=receipt_id, watermark=watermark)
        row = self._single(receipt_rows)
        if row is None:
            raise StoreError(502)
        receipt = self._receipt_row(row)
        if receipt["receipt_id"] != receipt_id:
            raise StoreError(502)
        revisions = self._revision_rows(rows, {receipt_id: receipt})
        self._validate_chains(revisions)
        return revisions

    def latest_resolutions(self):
        rows, watermark = self._read("latest_resolutions")
        receipt_rows, _ = self._read("receipts", watermark=watermark)
        receipts = self._receipt_rows(receipt_rows)
        revisions = self._revision_rows(rows, {r["receipt_id"]: r for r in receipts})
        resolutions = [row["resolution"] for row in revisions]
        if len({row["receipt_id"] for row in resolutions}) != len(resolutions):
            raise StoreError(502)
        return sorted(resolutions, key=lambda row: row["receipt_id"])

    @staticmethod
    def _validate_chains(revisions):
        heads = {}
        for revision in revisions:
            rid = revision["resolution"]["receipt_id"]
            previous = heads.get(rid)
            if revision["supersedes"] != (previous["revision_id"] if previous else None):
                raise StoreError(502)
            if previous and (
                    instant(revision["resolution"]["resolved_utc"]) < instant(previous["resolution"]["resolved_utc"])
                    or instant(revision["recorded_utc"]) < instant(previous["recorded_utc"])):
                raise StoreError(502)
            heads[rid] = revision

    def read_snapshot(self):
        """Return one complete evaluation snapshot pinned to a database fence.

        Individual list methods each have their own consistent watermark. This
        method instead reuses the first read's fence for every dependent view,
        preserving all FINAL/VOID corrections rather than only current heads.
        The DB contract serializes commit/sequence assignment so a concurrent
        writer cannot commit later underneath the already pinned watermark.
        """
        receipt_rows, watermark = self._read("receipts")
        receipts = self._receipt_rows(receipt_rows)
        receipt_map = {r["receipt_id"]: r for r in receipts}
        generation_rows, _ = self._read("generations", watermark=watermark)
        generations = self._generation_rows(generation_rows)
        for generation in generations:
            for accepted in generation["ack"]["receipts"]:
                stored = receipt_map.get(accepted["receipt_id"])
                if stored is None or stored["source_receipt_id"] != accepted["source_receipt_id"]:
                    raise StoreError(502)
        revision_rows, _ = self._read("revisions", watermark=watermark)
        revisions = self._revision_rows(revision_rows, receipt_map)
        self._validate_chains(revisions)
        return {"version": 1, "watermark": watermark,
                "receipts": sorted(receipts, key=lambda row: row["receipt_id"]),
                "generation_records": generations, "revisions": revisions}
