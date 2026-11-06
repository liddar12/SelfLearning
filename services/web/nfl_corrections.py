"""Authenticated NFL corrections planning. Fail closed to submitted baselines.

Loads a pinned corrections artifact from the existing receipt journal as a
content-addressed ``source_snapshot``. No new migration is required. Mode is
read per request from ``SELFLEARN_CORRECTIONS_MODE`` (default off).
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.adapters.nfl_corrections import (
    CorrectionsError,
    corrections_mode,
    plan_corrections,
    validate_artifact,
)

LOG = logging.getLogger(__name__)


def artifact_snapshot_payload(artifact):
    """Journal ``source_snapshot`` payload: body without nested artifact_id.

    ``object_id`` must equal ``fingerprint(payload)``, which is ``artifact_id``.
    """
    validate_artifact(artifact)
    return {key: value for key, value in artifact.items() if key != "artifact_id"}


def _load_artifact(journal_factory, env):
    artifact_id = env.get("SELFLEARN_CORRECTIONS_ARTIFACT_ID", "")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("sha256:"):
        return None
    if journal_factory is None:
        return None
    with journal_factory() as journal:
        record = journal.get_object(artifact_id)
    if not isinstance(record, dict) or record.get("kind") != "source_snapshot":
        raise CorrectionsError("artifact_object_missing")
    payload = record.get("payload")
    if not isinstance(payload, dict):
        raise CorrectionsError("artifact_payload_missing")
    if payload.get("kind") == "nfl.corrections_envelope.v1":
        candidate = payload.get("artifact")
    elif "artifact_id" in payload:
        candidate = payload
    else:
        candidate = {**payload, "artifact_id": artifact_id}
    return validate_artifact(candidate)


def register_corrections_routes(app, journal_factory, body_reader, env=None):
    env = os.environ if env is None else env

    @app.post("/api/v1/nfl/corrections")
    async def plan(request: Request):
        try:
            data = await body_reader(request)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(422, "Invalid NFL corrections request") from None

        def run():
            mode = corrections_mode(env.get("SELFLEARN_CORRECTIONS_MODE"))
            try:
                artifact = None if mode == "off" else _load_artifact(journal_factory, env)
            except (CorrectionsError, ValueError, TypeError, KeyError, OSError):
                LOG.warning("corrections_artifact_load_failed; serving baseline echo")
                artifact = None
            try:
                return plan_corrections(data, mode=mode, artifact=artifact)
            except CorrectionsError:
                raise HTTPException(422, "Invalid NFL corrections request") from None
            except Exception:
                LOG.exception("corrections_endpoint_fallback")
                # Last-resort fail closed: re-plan in off mode using the same body.
                try:
                    return plan_corrections(data, mode="off", artifact=None)
                except CorrectionsError:
                    raise HTTPException(422, "Invalid NFL corrections request") from None

        return await run_in_threadpool(run)
