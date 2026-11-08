"""Authenticated NFL corrections planning. Fail closed to submitted baselines.

Loads a pinned corrections artifact from the existing receipt journal as a
content-addressed ``source_snapshot``. Trusted binder labels are re-authorized
against the live journal as-of before ``applied=true`` is possible. No new
migration is required. Mode is read per request from
``SELFLEARN_CORRECTIONS_MODE`` (default off).
"""
from __future__ import annotations

import logging
import os

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.adapters.nfl_corrections import (
    CorrectionsError,
    authorize_corrections_artifact,
    corrections_mode,
    plan_corrections,
    validate_artifact,
)
from selflearn_core.adapters.nfl_corrections_policy import artifact_source_trusted

LOG = logging.getLogger(__name__)


def artifact_snapshot_payload(artifact):
    """Journal ``source_snapshot`` payload: body without nested artifact_id.

    ``object_id`` must equal ``fingerprint(payload)``, which is ``artifact_id``.
    """
    validate_artifact(artifact)
    return {key: value for key, value in artifact.items() if key != "artifact_id"}


def _load_artifact(journal_factory, env):
    """Load pinned snapshot; authorize trusted labels against the live journal."""
    artifact_id = env.get("SELFLEARN_CORRECTIONS_ARTIFACT_ID", "")
    if not isinstance(artifact_id, str) or not artifact_id.startswith("sha256:"):
        return None, False
    if journal_factory is None:
        return None, False
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
        artifact = validate_artifact(candidate)
        if not artifact_source_trusted(artifact.get("source")):
            return artifact, False
        authorize_corrections_artifact(journal, artifact)
        return artifact, True


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
            binder_verified = False
            try:
                if mode == "off":
                    artifact = None
                else:
                    artifact, binder_verified = _load_artifact(journal_factory, env)
            except (CorrectionsError, ValueError, TypeError, KeyError, OSError):
                LOG.warning("corrections_artifact_load_failed; serving baseline echo")
                artifact = None
                binder_verified = False
            try:
                return plan_corrections(
                    data, mode=mode, artifact=artifact, binder_verified=binder_verified)
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
