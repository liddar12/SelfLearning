"""Recover exactly one publication job declared for an NFL collection attempt."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core import learning_jobs as dto
from selflearn_core.adapters import nfl_collection_handoff as handoff
from selflearn_core.adapters import nfl_collection_publication as publication
from selflearn_core.probability_receipts import instant
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError
from . import nfl_publication_jobs as worker
from .nfl_collection_handoff import status as collection_status
from .security import NFL_COLLECTION_PATH


class OriginalJobObjects:
    """Read only the stored declaration's authority and content references."""

    def __init__(self, jobs, authority_id):
        self.jobs = jobs
        self.authority_id = authority_id

    def get_object(self, identity):
        return self.jobs.get_object(identity)


class GuardedEnqueueJobs(OriginalJobObjects):
    """Keep existing pinned enqueue validation and guard its final DB write."""

    def __init__(self, jobs, store, record):
        super().__init__(jobs, jobs.authority_id)
        self.store = store
        self.record = record

    def find_job(self, identity):
        return self.jobs.find_job(identity)

    def append_object(self, kind, identity, payload):
        return self.jobs.append_object(kind, identity, payload)

    def register_job(self, definition):
        return self.store.register(self.jobs, self.record, definition)


def _response(intent, prepared, state, job=None):
    completed = job is not None and job["snapshot"]["effective_status"] == "succeeded"
    proof = job["attestation"] if job is not None else None
    return {"version": 1, "intent_id": intent["intent_id"], "prepared": prepared, "state": state,
            "job": job, "job_registered": job is not None, "recording_complete": completed,
            "publication_verified": completed and proof is not None and bool(proof["receipts"]),
            "publication_evidence_available": proof is not None, "training_approved": False}


def _observed_job(intent, prepared, job):
    snapshot = publication.check_job(job["snapshot"], prepared)
    state = "job_terminal" if snapshot["effective_status"] in publication.TERMINAL else "registered"
    return _response(intent, prepared, state, job)


def observe(base, journal, jobs, publication_journal, policy, key, *, enqueue_id=None):
    databases = [getattr(port, "_db", None) for port in (journal, jobs, publication_journal)]
    if any(isinstance(db, sqlite3.Connection) for db in databases):
        if not all(isinstance(db, sqlite3.Connection) for db in databases):
            raise StoreError(503)
        paths = [db.execute("PRAGMA database_list").fetchone()[2] for db in databases]
        if not all(paths) or len({Path(path).resolve() for path in paths}) != 1:
            raise StoreError(503)
    view = base.read(key)
    intent = view["intent"]
    if intent is None:
        raise HTTPException(404, "Collection intent not found")
    if enqueue_id is not None and enqueue_id != intent["intent_id"]:
        raise JournalConflict("collection_publication_intent_conflict")
    store = publication.attach(base)
    prepared = store.read(intent, view["publisher_receipt"])
    if prepared is not None:
        original = store.find(prepared)
        if original is not None:
            objects = OriginalJobObjects(jobs, prepared["declaration"]["authority_id"])
            job = worker.delivery_view(objects, publication_journal, original)
            return _observed_job(intent, prepared, job)
        if instant(view["observed_utc"]) >= instant(intent["latest_kickoff_utc"]):
            return _response(intent, prepared, "held_late")
    if enqueue_id is None:
        if prepared is not None:
            state = ("prepared" if dto.canonical(worker.declaration(jobs, policy)) == dto.canonical(prepared["declaration"])
                     else "held_declaration_changed")
        else:
            gate = collection_status(base, journal, key, view=view)
            state = "awaiting_publication" if gate["publication_ready"] else gate["state"]
        return _response(intent, prepared, state)

    gate = collection_status(base, journal, key, view=view)
    if not gate["publication_ready"]:
        return _response(intent, prepared, gate["state"])
    current = worker.declaration(jobs, policy)
    if prepared is not None and dto.canonical(prepared["declaration"]) != dto.canonical(current):
        return _response(intent, prepared, "held_declaration_changed")
    try:
        if prepared is None:
            record = publication.prepared_record(intent, gate["publisher_receipt"], current)
            prepared = store.prepare(intent, gate["publisher_receipt"], record)
        definition = prepared["definition"]
        pinned = GuardedEnqueueJobs(jobs, store, prepared)
        job = worker.enqueue_pinned(pinned, publication_journal, prepared["generation_object_id"],
                                   definition["job_id"], definition["definition_id"], policy)
    except publication.CollectionPublicationCutoff:
        return _response(intent, prepared, "held_late")
    return _observed_job(intent, prepared, job)


def register_publication_handoff_routes(app, repository, factory, pair_factory, verifier, body_reader):
    path = NFL_COLLECTION_PATH + "/{workflow}/{run_id}/{run_attempt}/publication"

    def run(workflow, run_id, run_attempt, value=None):
        if factory is None or pair_factory is None or verifier is None:
            raise HTTPException(503, "Collection publication handoff is disabled")
        try:
            key = handoff.scope(repository, workflow, run_id, run_attempt)[1:]
            intent_id = None
            if value is not None:
                handoff.versioned(value, {"version", "intent_id"})
                intent_id = dto.digest(value["intent_id"])
            with factory() as (store, journal), pair_factory() as (jobs, publication_journal):
                return observe(store, journal, jobs, publication_journal, verifier.policy, key, enqueue_id=intent_id)
        except HTTPException:
            raise
        except (JournalConflict, dto.JobConflict):
            raise HTTPException(409, "Collection publication conflict. Recover the same immutable attempt.") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid collection publication handoff") from None
        except (OSError, sqlite3.Error):
            raise HTTPException(503, "Collection publication unavailable. Retry the original intent.") from None

    @app.get(path)
    async def inspect(workflow: str, run_id: str, run_attempt: str, request: Request):
        if request.query_params or await request.body():
            raise HTTPException(422, "Publication status has no query or body")
        return await run_in_threadpool(run, workflow, run_id, run_attempt)

    @app.post(path)
    async def enqueue(workflow: str, run_id: str, run_attempt: str, request: Request):
        if request.query_params:
            raise HTTPException(422, "Unexpected collection publication query")
        value = await body_reader(request)
        if type(value) is not dict:
            raise HTTPException(422, "Use the original collection intent ID")
        return await run_in_threadpool(run, workflow, run_id, run_attempt, value)
