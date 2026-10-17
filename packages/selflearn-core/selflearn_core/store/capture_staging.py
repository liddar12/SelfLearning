"""Explicit staging reservations, not raw Storage or learning acceptance.

The RPC owns atomic all-time accounting; this adapter never refunds, deletes,
provisions a quota, resets a period or claims a hosted connection. A reserved
but failed upload remains charged and can retry its exact immutable chunk ID.
"""
import re

from ..probability_receipts import fingerprint, instant
from .supabase_journal import _canonical
from .supabase_store import StoreError

HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")


def staging_namespace(*, destination_id, producer_id):
    if (type(destination_id) is not str or not HASH.fullmatch(destination_id)
            or type(producer_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}", producer_id)):
        raise ValueError("invalid_capture_staging_scope")
    return fingerprint({"version": 1, "destination_id": destination_id, "producer_id": producer_id})


def validate_reservation(value, *, namespace_id, chunk_id, raw_bytes):
    keys = {"version", "kind", "namespace_id", "chunk_id", "raw_bytes", "reservation_id",
            "reserved_total_bytes", "reserved_chunks", "recorded_utc", "scope", "quota_reserved",
            "learning_accepted", "durable_ack"}
    identity = {"version": 1, "namespace_id": namespace_id, "chunk_id": chunk_id, "raw_bytes": raw_bytes}
    if (type(value) is not dict or set(value) != keys
            or type(value.get("version")) is not int or value["version"] != 1
            or value["kind"] != "selflearn.capture_stage_reservation.v1"
            or value["namespace_id"] != namespace_id or value["chunk_id"] != chunk_id
            or type(value["raw_bytes"]) is not int or value["raw_bytes"] != raw_bytes
            or value["reservation_id"] != fingerprint(identity)
            or type(value["reserved_total_bytes"]) is not int
            or not raw_bytes <= value["reserved_total_bytes"] <= 8589934592
            or type(value["reserved_chunks"]) is not int or not 1 <= value["reserved_chunks"] <= 65536
            or value["scope"] != "staging_budget_only" or value["quota_reserved"] is not True
            or value["learning_accepted"] is not False or value["durable_ack"] is not False):
        raise ValueError("invalid_capture_staging_ack")
    instant(value["recorded_utc"])
    return value


class CaptureStagingQuota:
    """No I/O at construction; explicitly inject one server-owned namespace."""

    def __init__(self, request, *, namespace_id):
        if not callable(request) or type(namespace_id) is not str or not HASH.fullmatch(namespace_id):
            raise ValueError("invalid_capture_staging_boundary")
        self._request, self._namespace_id = request, namespace_id

    def reserve(self, chunk_id, raw_bytes):
        if (type(chunk_id) is not str or not HASH.fullmatch(chunk_id)
                or type(raw_bytes) is not int or not 1 <= raw_bytes <= 1048576):
            raise ValueError("invalid_capture_staging_chunk")
        data = {"p_namespace_id": self._namespace_id, "p_chunk_id": chunk_id, "p_raw_bytes": raw_bytes}
        try:
            value = self._request("POST", "/rest/v1/rpc/selflearn_reserve_capture_stage_v1", data=data)
            # Snapshot before returning, never retain a mutable transport result.
            import json
            value = json.loads(_canonical(value))
            return validate_reservation(value, namespace_id=self._namespace_id, chunk_id=chunk_id, raw_bytes=raw_bytes)
        except StoreError as exc:
            status = exc.status if type(exc.status) is int and exc.status in (409, 422, 429, 503) else 503
            raise StoreError(status) from None
        except Exception:
            raise StoreError(503) from None
