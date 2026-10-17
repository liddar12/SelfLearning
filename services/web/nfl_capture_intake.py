"""Explicitly injected, scoped capture intake; no automatic backend activation.

A token alone never enables intake. The operator must separately supply the
server-owned journal, private evidence store and durable staging quota. Storage
staging is not provider authentication, outcome acceptance or model adoption.
The bounded v1 delivery still rejects complete captures above 500 members.
"""
import copy

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core import evidence_chunks
from selflearn_core.adapters.nfl_capture_delivery_job import commit_capture_delivery, _request
from selflearn_core.adapters.nfl_capture_delivery_journal import _result
from selflearn_core.adapters.nfl_capture_labels import POLICY
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import staging_namespace, validate_reservation, HASH
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError


class CaptureReceiver:
    """Server configuration only; callers cannot select a backend or namespace."""

    def __init__(self, journal, evidence_store, staging_quota, *, destination_id, producer_id):
        self.namespace_id = staging_namespace(destination_id=destination_id, producer_id=producer_id)
        if (any(not callable(getattr(journal, key, None)) for key in
                ("get_capture_delivery", "read_observed_snapshot", "get_object", "commit_capture_delivery"))
                or not callable(getattr(evidence_store, "put_chunk", None))
                or not callable(getattr(evidence_store, "object_chunks", None))
                or not callable(getattr(staging_quota, "reserve", None))):
            raise ValueError("invalid_capture_receiver_boundary")
        self.journal, self.evidence, self.quota = journal, evidence_store, staging_quota
        self.destination_id, self.producer_id = destination_id, producer_id

    def stage(self, value):
        dto = copy.deepcopy(value)
        raw = evidence_chunks.verify_chunk(dto)
        reserved = self.quota.reserve(dto["chunk_id"], len(raw))
        try:
            validate_reservation(reserved, namespace_id=self.namespace_id, chunk_id=dto["chunk_id"], raw_bytes=len(raw))
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(503) from None
        # Reservation is durable BEFORE any Storage write. Failed or abandoned
        # writes stay charged; the exact ID can retry without another charge.
        result = self.evidence.put_chunk(dto)
        if (type(result) is not dict or set(result) != {"version", "kind", "scope", "bucket", "key", "chunk_id", "raw_bytes", "learning_accepted", "durable_ack"}
                or type(result.get("version")) is not int or result["version"] != 1
                or result.get("kind") != "selflearn.evidence_stage.v1"
                or result.get("scope") != "uploaded_only" or result.get("chunk_id") != dto["chunk_id"]
                or type(result.get("raw_bytes")) is not int or result["raw_bytes"] != len(raw)
                or result.get("learning_accepted") is not False or result.get("durable_ack") is not False
                or any(type(result[key]) is not str or not 1 <= len(result[key]) <= 1024 for key in ("bucket", "key"))):
            raise StoreError(503)
        return result

    def deliver(self, value):
        request = _request(value)
        if request["destination_id"] != self.destination_id or request["producer_id"] != self.producer_id:
            raise ValueError("capture_receiver_scope_mismatch")
        pointers = {p["object_id"]: p for p in request["raw_pointers"]}
        result = commit_capture_delivery(self.journal, request,
                    lambda oid: self.evidence.object_chunks(pointers[oid]))
        try:
            _result(result["result"], request=request)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(503) from None
        return result

    def delivery(self, delivery_id):
        if type(delivery_id) is not str or not HASH.fullmatch(delivery_id):
            raise ValueError("invalid_capture_delivery_id")
        value = self.journal.get_capture_delivery(delivery_id)
        if value is None:
            return None
        try:
            _result(value, delivery_id=delivery_id)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise StoreError(503) from None
        expected = fingerprint({"version": 1, "kind": "nfl.capture_delivery.v1", "destination_id": self.destination_id,
            "producer_id": self.producer_id, "capture_id": value["capture_id"], "binding_policy": POLICY})
        # A known delivery digest is not authorization to inspect another
        # producer/destination's result in the shared private journal.
        return value if delivery_id == expected else None


def register_capture_routes(app, capture_factory, body_reader):
    def run(operation):
        if capture_factory is None:
            raise HTTPException(503, "Capture intake is disabled; no staging or learning was acknowledged")
        try:
            with capture_factory() as receiver:
                if not isinstance(receiver, CaptureReceiver):
                    raise StoreError(503)
                return operation(receiver)
        except JournalConflict:
            raise HTTPException(409, "Immutable capture conflict; retain the original delivery intent") from None
        except StoreError as exc:
            code = exc.status if exc.status in (409, 429) else 503
            raise HTTPException(code, "Capture staging or journal is unavailable; retain the original immutable IDs") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid capture evidence or configured producer scope") from None
        except Exception:
            raise HTTPException(503, "Capture intake unavailable; no durable success is inferred") from None

    async def data(request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Capture requests require application/json")
        return await body_reader(request)

    @app.post("/api/v1/nfl/capture-chunks")
    async def stage(request: Request):
        value = await data(request)
        return await run_in_threadpool(run, lambda receiver: receiver.stage(value))

    @app.post("/api/v1/nfl/capture-deliveries")
    async def deliver(request: Request):
        value = await data(request)
        return await run_in_threadpool(run, lambda receiver: receiver.deliver(value))

    @app.get("/api/v1/nfl/capture-deliveries/{delivery_id}")
    def delivery(delivery_id: str):
        if not HASH.fullmatch(delivery_id):
            raise HTTPException(422, "Use an exact capture delivery ID")
        result = run(lambda receiver: receiver.delivery(delivery_id))
        if result is None:
            raise HTTPException(404, "Capture delivery not found in the configured producer scope")
        return result
