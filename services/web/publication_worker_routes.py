"""Scoped, default-off recording jobs; no active schedule or model promotion.

The production pair is built from the already-validated journal connection.
The explicit injected pair is a trusted test seam, never an HTTP field. Local
SQLite is only accepted through that seam, with both ports on the same file.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import uuid

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core import learning_jobs as protocol
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError
from .nfl_ingestion import request_fields
from . import nfl_publication_jobs as worker


def select_worker_factory(env, injected, journal_factory, verifier):
    """Configuration only: no reads, job registration, network or source scan."""
    status = {"configured": False, "readiness": "disabled", "recording_only": True,
              "scheduler_verified": False, "automatic_promotion": False}
    if worker.from_environment(env) is None:
        return None, status
    status["readiness"] = "invalid_configuration"
    try:
        authority = protocol.digest(env.get("SELFLEARN_PUBLICATION_WORKER_AUTHORITY_ID"))
        if verifier is None or env.get("SELFLEARN_PUBLICATION_MODE") != "record":
            return None, status
        if injected is not None:
            if not callable(injected):
                return None, status
            factory = injected
        else:
            builder = getattr(journal_factory, "publication_job_factory", None)
            if not callable(builder):
                return None, status
            factory = builder(authority)
    except (ValueError, TypeError):
        return None, status

    @contextmanager
    def checked_pair():
        with factory() as pair:
            if type(pair) is not tuple or len(pair) != 2:
                raise StoreError(503)
            jobs, journal = pair
            if jobs.authority_id != authority:
                raise StoreError(503)
            # Catch accidental split SQLite injection before any mutation.
            # Hosted construction above shares the exact HTTP transport.
            databases = [getattr(port, "_db", None) for port in pair]
            if any(isinstance(db, sqlite3.Connection) for db in databases):
                if not all(isinstance(db, sqlite3.Connection) for db in databases):
                    raise StoreError(503)
                paths = [db.execute("PRAGMA database_list").fetchone()[2] for db in databases]
                if not all(paths) or Path(paths[0]).resolve() != Path(paths[1]).resolve():
                    raise StoreError(503)
            yield jobs, journal

    return checked_pair, {**status, "configured": True, "readiness": "configured_unverified"}


def register_worker_routes(app, pair_factory, body_reader, verifier):
    def run(operation):
        if pair_factory is None or verifier is None:
            raise HTTPException(503, "Publication worker is disabled or unconfigured")
        try:
            with pair_factory() as (jobs, journal):
                return operation(jobs, journal)
        except HTTPException:
            raise
        except (protocol.JobConflict, JournalConflict):
            raise HTTPException(409, "Publication job state changed; retry its immutable identity") from None
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise HTTPException(422, "Publication job does not match its frozen recording policy") from None
        except Exception:
            # Job/evidence may have committed before a transport failure. Never
            # assert nothing was written; retry the same ID and inspect its state.
            raise HTTPException(503, "Publication job unavailable; recover using the same immutable identity") from None

    @app.post("/api/v1/nfl/publication-jobs")
    async def enqueue(request: Request):
        try:
            value = request_fields(await body_reader(request), ("version", "object_id"))
            if request.query_params:
                raise ValueError("unsupported_job_parameters")
        except (ValueError, TypeError):
            raise HTTPException(422, "Use the original finalized generation ID only") from None
        return await run_in_threadpool(run, lambda jobs, journal: worker.enqueue(
            jobs, journal, value["object_id"], verifier.policy, worker.code_identity()))

    @app.get("/api/v1/nfl/publication-jobs/{job_id}")
    def read_job(job_id: str, request: Request):
        try:
            protocol.digest(job_id)
            if request.query_params:
                raise ValueError("unsupported_job_parameters")
        except (ValueError, TypeError):
            raise HTTPException(422, "Use the immutable job ID only") from None
        def read(jobs, journal):
            value = jobs.read_job(job_id)
            if (value["definition"]["namespace"] != worker.NAMESPACE
                    or value["definition"]["operation"] != worker.OPERATION):
                raise HTTPException(404, "Publication job not found")
            return value
        return run(read)

    @app.api_route("/api/v1/nfl/publication-worker", methods=["GET", "POST"])
    async def invoke(request: Request):
        if request.query_params:
            raise HTTPException(422, "Worker controls are server-owned")
        if request.method == "POST":
            value = await body_reader(request)
            if type(value) is not dict or set(value) != {"version"} or type(value["version"]) is not int or value["version"] != 1:
                raise HTTPException(422, "Use version 1 without client worker controls")
        elif await request.body():
            raise HTTPException(422, "Scheduled worker requests have no body")
        return await run_in_threadpool(run, lambda jobs, journal: worker.run_once(
            jobs, journal, verifier, "publication-" + uuid.uuid4().hex, worker.code_identity()))
