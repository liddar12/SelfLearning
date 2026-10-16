"""Authenticated NFL generation intake. No fitting, publication claim or promotion.

The API has one server-owned journal. There is no client-selectable tenant,
database, path or namespace. A production journal implementation is deliberately
not selected here; only explicitly injected durable implementations can ACK.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_generation import (LEG_KEYS, MANIFEST_KEYS, SOURCE_NAMES,
    validate_generation as validate_frozen_generation)
from selflearn_core.store.receipt_journal import JournalConflict

LOG = logging.getLogger(__name__)
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")


def request_fields(value, fields):
    if (not isinstance(value, dict) or set(value) != set(fields)
            or type(value.get("version")) is not int or value["version"] != 1
            or not isinstance(value.get("object_id"), str) or not HASH.fullmatch(value["object_id"])):
        raise ValueError("invalid_ingestion_request")
    return value


def validate_generation(journal, object_id):
    """Join the exact stored source bodies, not just plausible digest strings.

    The joint/selection versions remain producer-declared hashes. We validate
    the exact correlation input's hash, not an independently recovered selector
    source or a claim that joint probabilities are calibrated.
    """
    return validate_frozen_generation(journal, object_id,
        now_utc=datetime.now(timezone.utc).isoformat())


def finalize_generation(journal, object_id):
    committed = journal.generation_ack(object_id)
    if committed is not None:
        # A delivery retry is not a new evaluation under today's adapter. The
        # immutable transaction result remains authoritative after code changes.
        return {**committed, "receipts": [{**row, "status": "duplicate"}
                                         for row in committed["receipts"]]}
    doc = validate_generation(journal, object_id)
    batch = adapt_prospective(journal_docs=[doc], existing_receipts=journal.receipts())
    rejected = []
    for row in batch["rejections"]:
        if not isinstance(row.get("source_receipt_id"), str):
            raise ValueError("invalid_generation_rejection")
        reason = row.get("reason")
        # A downstream contract validator may produce a descriptive exception,
        # including user-provided field names. Persist only bounded machine
        # codes, and quarantine that row rather than aborting valid neighbors.
        if not isinstance(reason, str) or not REASON.fullmatch(reason):
            reason = "invalid_receipt_contract"
        rejected.append({"source_receipt_id": row["source_receipt_id"], "reason": reason})
    return journal.commit_generation(object_id, batch["receipts"], rejected)


def register_nfl_routes(app, journal_factory, body_reader):
    def run(operation):
        if journal_factory is None:
            raise HTTPException(503, "Durable NFL journal is not configured; nothing was acknowledged")
        try:
            with journal_factory() as journal:
                return operation(journal)
        except JournalConflict:
            raise HTTPException(409, "Immutable NFL data conflict") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid NFL generation or source evidence") from None
        except (sqlite3.Error, OSError):
            LOG.warning("NFL journal operation failed; no durable acknowledgement returned")
            raise HTTPException(503, "NFL journal unavailable; retry with the same immutable IDs") from None

    @app.post("/api/v1/nfl/objects")
    async def put_object(request: Request):
        try:
            data = request_fields(await body_reader(request), ("version", "kind", "object_id", "payload"))
        except ValueError:
            raise HTTPException(422, "Invalid NFL object request") from None
        def write(journal):
            added = journal.append_object(data["kind"], data["object_id"], data["payload"])
            return {"version": 1, "object_id": data["object_id"], "durable": True,
                    "status": "recorded" if added else "duplicate"}
        return await run_in_threadpool(run, write)

    @app.post("/api/v1/nfl/generations")
    async def finalize(request: Request):
        try:
            data = request_fields(await body_reader(request), ("version", "object_id"))
        except ValueError:
            raise HTTPException(422, "Invalid NFL generation request") from None
        return await run_in_threadpool(run, lambda journal: finalize_generation(journal, data["object_id"]))
