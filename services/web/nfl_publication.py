"""Default-off publication observation. Never fit, pin, promote or rewrite."""
from __future__ import annotations

import logging
import sqlite3

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.store.receipt_journal import JournalConflict
from .nfl_ingestion import request_fields
from .nfl_publication_verifier import PublicationUnavailable

LOG = logging.getLogger(__name__)


def register_publication_routes(app, journal_factory, body_reader, verifier):
    @app.post("/api/v1/nfl/publication-attestations")
    async def attest(request: Request):
        try:
            value = request_fields(await body_reader(request), ("version", "object_id"))
        except (ValueError, TypeError):
            raise HTTPException(422, "Use the original finalized generation ID only") from None
        if verifier is None or journal_factory is None:
            raise HTTPException(503, "Publication verification is disabled or unconfigured")

        def run():
            try:
                with journal_factory() as journal:
                    # Recover a durable original BEFORE another HTTP observation.
                    # A retry after kickoff cannot move the original clocks.
                    original = journal.get_publication_attestation(value["object_id"])
                    if original is not None:
                        return original
                    if journal.generation_ack(value["object_id"]) is None:
                        raise ValueError("generation_not_finalized")
                    observation = verifier.observe()
                    return journal.commit_publication_attestation(value["object_id"], observation)
            except JournalConflict:
                raise HTTPException(409, "Immutable publication evidence conflict") from None
            except PublicationUnavailable:
                raise HTTPException(503, "Public artifact unavailable; no new evidence acknowledged") from None
            except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
                raise HTTPException(422, "Publication evidence does not match the frozen generation") from None
            except (sqlite3.Error, OSError, AttributeError):
                LOG.warning("Publication persistence unavailable; no acknowledgement returned")
                raise HTTPException(503, "Publication persistence unavailable; retry the same generation") from None

        return await run_in_threadpool(run)
