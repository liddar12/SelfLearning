"""Default-off, scoped recovery of the original NFL producer run attempt."""
from __future__ import annotations

from contextlib import contextmanager
import sqlite3

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core import learning_jobs as dto
from selflearn_core.adapters import nfl_collection_handoff as handoff
from selflearn_core.adapters import nfl_collection_publication as publication
from selflearn_core.adapters.nfl_generation import validate_generation
from selflearn_core.probability_receipts import instant
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal
from selflearn_core.store.supabase_store import StoreError
from .nfl_ingestion import finalize_generation
from .security import NFL_COLLECTION_PATH


def select_factory(env, journal_factory, injected):
    repository = env.get("SELFLEARN_NFL_COLLECTION_REPOSITORY", "")
    if (env.get("SELFLEARN_NFL_COLLECTION_MODE") != "record" or type(repository) is not str
            or not handoff.REPOSITORY.fullmatch(repository) or journal_factory is None):
        return None, None

    @contextmanager
    def factory():
        with journal_factory() as journal:
            if injected is not None:
                repository_store = injected(journal, repository)
            else:
                if not isinstance(journal, SupabaseReceiptJournal):
                    raise StoreError(503)
                repository_store = handoff.SupabaseCollectionHandoff(journal, repository)
            yield repository_store, journal

    return repository, factory


class OriginalObjects:
    """Avoid rereading large immutable objects during one recovery request."""

    def __init__(self, journal):
        self.journal = journal
        self.objects = {}

    def get_object(self, identity):
        if identity not in self.objects:
            self.objects[identity] = self.journal.get_object(identity)
        return self.objects[identity]

    def __getattr__(self, name):
        return getattr(self.journal, name)


def _response(view, state, *, ack=None, complete=False):
    return {**view, "state": state, "generation_ack": ack, "intake_complete": complete,
            "publication_ready": state == "publication_ready", "publication_verified": False,
            "training_approved": False}


def _complete_intake(ack, doc, identity):
    handoff.versioned(ack, {"version", "object_id", "durable", "receipts", "rejections"})
    if (ack["object_id"] != identity or ack["durable"] is not True
            or type(ack["receipts"]) is not list or type(ack["rejections"]) is not list):
        raise StoreError(502)
    source_ids, generic_ids = [], set()
    for row in ack["receipts"]:
        handoff.fields(row, {"source_receipt_id", "receipt_id", "status"})
        if row["status"] not in ("recorded", "duplicate"):
            raise StoreError(502)
        source_ids.append(dto.digest(row["source_receipt_id"]))
        generic_ids.add(dto.digest(row["receipt_id"]))
    for row in ack["rejections"]:
        handoff.fields(row, {"source_receipt_id", "reason"})
        if not isinstance(row["reason"], str):
            raise StoreError(502)
        source_ids.append(dto.digest(row["source_receipt_id"]))
    if len(generic_ids) != len(ack["receipts"]) or sorted(source_ids) != doc["generations"][0]["receipt_ids"]:
        raise StoreError(502)
    return not ack["rejections"] and bool(ack["receipts"])


def status(store, journal, key, *, recover_id=None, view=None):
    if view is None:
        view = store.read(key)
    intent = view["intent"]
    if intent is None:
        raise HTTPException(404, "Collection intent not found")
    if recover_id is not None and intent["intent_id"] != recover_id:
        raise JournalConflict("collection_recovery_intent_conflict")
    observed = instant(view["observed_utc"])
    late = observed >= instant(intent["latest_kickoff_utc"])
    if view["missing_object_ids"]:
        return _response(view, "held_late" if late else "awaiting_objects")

    oid = intent["generation_object_id"]
    ack = journal.generation_ack(oid)
    if ack is None and (recover_id is None or late):
        return _response(view, "held_late" if late else "ready_to_finalize")
    frozen = OriginalObjects(journal)
    try:
        document = validate_generation(frozen, oid, now_utc=view["observed_utc"])
        handoff.validate_graph(intent, document)
        if instant(frozen.get_object(oid)["received_utc"]) < instant(intent["recorded_utc"]):
            raise ValueError("collection_envelope_before_capture")
    except (ValueError, TypeError, KeyError, OverflowError):
        return _response(view, "held_invalid_generation")

    if ack is None:
        view = store.read(key)
        if instant(view["observed_utc"]) >= instant(intent["latest_kickoff_utc"]):
            return _response(view, "held_late")
        finalize_generation(frozen, oid)
        ack = journal.generation_ack(oid)
        if ack is None:
            raise StoreError(503)
    try:
        complete = _complete_intake(ack, document, oid)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise StoreError(502) from None
    if not complete:
        return _response(view, "held_intake_rejected", ack=ack)
    # A publisher receipt can arrive concurrently. Never authorize enqueue
    # from a database observation obtained before graph and ACK validation.
    view = store.read(key)
    late = instant(view["observed_utc"]) >= instant(intent["latest_kickoff_utc"])
    receipt = view["publisher_receipt"]
    state = ("held_late" if late else "awaiting_publisher" if receipt is None else
             "publication_ready" if receipt["disposition"] == "match" else
             "held_publisher_" + receipt["disposition"])
    return _response(view, state, ack=ack, complete=True)


def register_collection_routes(app, repository, factory, body_reader, *, publication_pending=False):
    def run(operation):
        if factory is None:
            raise HTTPException(503, "NFL collection handoff is disabled")
        try:
            with factory() as (store, journal):
                return operation(store, journal)
        except JournalConflict:
            raise HTTPException(409, "Immutable collection attempt conflict") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid collection handoff fields") from None
        except (sqlite3.Error, OSError):
            raise HTTPException(503, "Collection handoff unavailable") from None

    def key_for(workflow, run_id, run_attempt):
        return handoff.scope(repository, workflow, run_id, run_attempt)[1:]

    def no_query(request):
        if request.query_params:
            raise HTTPException(422, "Unexpected collection query parameters")

    @app.post(NFL_COLLECTION_PATH)
    async def register(request: Request):
        no_query(request)
        value = await body_reader(request)

        def write(store, journal):
            intent = handoff.intent_record(repository, value)
            handoff.object_refs(intent)
            key = key_for(intent["workflow"], intent["run_id"], intent["run_attempt"])
            return status(store, journal, key, view=store.put(key, "intent", intent))

        return await run_in_threadpool(run, write)

    @app.get(NFL_COLLECTION_PATH + "/pending")
    async def pending(request: Request):
        raw = request.query_params.get("limit", "16")
        if (await request.body() or set(request.query_params) - {"limit", "after"}
                or len(request.query_params.multi_items()) != len(request.query_params)
                or len(raw) > 2 or not raw.isascii() or not raw.isdecimal()):
            raise HTTPException(422, "Use an optional bounded limit and cursor")
        def scan(store, _):
            port = publication.attach(store) if publication_pending else store
            return port.pending(int(raw), request.query_params.get("after"))

        return await run_in_threadpool(run, scan)

    run_path = NFL_COLLECTION_PATH + "/{workflow}/{run_id}/{run_attempt}"

    @app.get(run_path)
    async def read(workflow: str, run_id: str, run_attempt: str, request: Request):
        no_query(request)
        if await request.body():
            raise HTTPException(422, "Collection status has no body")
        return await run_in_threadpool(run, lambda store, journal:
            status(store, journal, key_for(workflow, run_id, run_attempt)))

    @app.post(run_path + "/publisher")
    async def publish(workflow: str, run_id: str, run_attempt: str, request: Request):
        no_query(request)
        value = await body_reader(request)

        def write(store, journal):
            key = key_for(workflow, run_id, run_attempt)
            intent = store.read(key)["intent"]
            if intent is None:
                raise HTTPException(404, "Collection intent not found")
            receipt = handoff.publisher_record(intent, value)
            return status(store, journal, key, view=store.put(key, "publisher", receipt))

        return await run_in_threadpool(run, write)

    @app.post(run_path + "/recover")
    async def recover(workflow: str, run_id: str, run_attempt: str, request: Request):
        no_query(request)
        value = await body_reader(request)

        def deliver(store, journal):
            handoff.versioned(value, {"version", "intent_id"})
            return status(store, journal, key_for(workflow, run_id, run_attempt),
                          recover_id=dto.digest(value["intent_id"]))

        return await run_in_threadpool(run, deliver)
