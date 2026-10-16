"""Default-off v2 capture commands, not a scheduler or model-adoption API."""
from __future__ import annotations

import copy
import re

from fastapi import HTTPException, Request
from starlette.concurrency import run_in_threadpool

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters import nfl_capture_application_contracts as C
from selflearn_core.adapters.nfl_capture_application_job import (
    register_capture_application, commit_capture_application_step, CaptureApplicationStopped,
    CaptureApplicationScopeMismatch,
)
from selflearn_core.adapters.nfl_capture_application_journal import CaptureApplicationBudgetExceeded
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_store import StoreError
from .nfl_capture_intake import CaptureReceiver

_ORDINAL = re.compile(r"(?:0|[1-9][0-9]{0,3})\Z")
# SQL's ceiling remains500. The public v2 server chooses a lower immutable
# execution policy: the actual1088-member local probe left only0.46s under the
# existing12s SQL transport timeout at496-member batches. No timeout is widened
# and clients cannot supply this setting. Hosted/native timing stays unverified.
DEFAULT_BATCH_MEMBERS = 400


class CaptureApplicationReceiver(CaptureReceiver):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if any(not callable(getattr(self.journal, key, None)) for key in (
                "get_capture_application", "register_capture_application", "commit_capture_application_batch", "stop_capture_application")):
            raise ValueError("invalid_capture_application_receiver_boundary")

    def _scope(self, request):
        captured = request["capture_request"]
        return (captured["destination_id"], captured["producer_id"]) == (self.destination_id, self.producer_id)

    def _raw(self, request):
        pointers = {value["object_id"]: value for value in request["capture_request"]["raw_pointers"]}
        return lambda oid: self.evidence.object_chunks(pointers[oid])

    def register(self, value):
        request = V.validate_request(value)
        if not self._scope(request):
            raise CaptureApplicationScopeMismatch("capture_application_not_found_in_configured_scope")
        ack = register_capture_application(self.journal, request, self._raw(request),
            max_batch_members=DEFAULT_BATCH_MEMBERS,
            max_batch_observations=DEFAULT_BATCH_MEMBERS,
            max_batch_revisions=DEFAULT_BATCH_MEMBERS)
        return {"version": 2, "registration": ack, "scope": "registration_only", "learning_accepted": False}

    def step(self, application_id, step_id):
        # The job performs the scope check on its initial full read, before
        # recovering a result, reading raw bytes or making any state change.
        pointers = None
        def raw(oid):
            nonlocal pointers
            # The immutable request lookup is needed only when raw is actually
            # required. A missing/outage bucket must not block original ACKs.
            if pointers is None:
                application = self.journal.get_capture_application(application_id)
                if application is None or not self._scope(application["request"]):
                    raise CaptureApplicationScopeMismatch("capture_application_not_found_in_configured_scope")
                pointers = {value["object_id"]: value for value in application["request"]["capture_request"]["raw_pointers"]}
            return self.evidence.object_chunks(pointers[oid])
        result = commit_capture_application_step(self.journal, application_id, step_id, raw,
                                                 expected_scope=(self.destination_id, self.producer_id))
        return {"version": 2, "result": result, "scope": "batch_only"}

    def application(self, application_id, *, view="status", after=None, limit=100, ledger_watermark=None, step_id=None):
        V.delivery._hash(application_id)
        if type(view) is not str or view not in C.VIEWS or (view == "batch") != (step_id is not None):
            raise ValueError("invalid_capture_application_view")
        C.integer(limit, 1, 100)
        many = view in ("groups", "batches")
        if not many and after is not None:
            raise ValueError("invalid_capture_application_cursor")
        application = self.journal.get_capture_application(application_id, ledger_watermark=ledger_watermark)
        if application is None or not self._scope(application["request"]):
            return None
        if view == "registration":
            records = [{"request": application["request"],
                        "seal_header": {k: v for k, v in application["seal"].items() if k not in ("groups", "batches")},
                        "registration": application["registration"]}]
        elif view in ("groups", "batches"):
            records = application[view]
        elif view == "batch":
            V.delivery._hash(step_id)
            batch = next((row for row in application["batches"] if row["assignment"]["step_id"] == step_id), None)
            if batch is None:
                return None
            records = [] if batch["result"] is None else [batch["result"]]
        else:
            records = [application[view]] if application[view] is not None else []
        total, start = len(records), 0
        def cursor(row):
            return row["sealed_group"]["group_id"] if view == "groups" else str(row["assignment"]["ordinal"])
        if many and after is not None:
            if view == "groups":
                V.delivery._hash(after)
            elif type(after) is not str or not _ORDINAL.fullmatch(after):
                raise ValueError("invalid_capture_application_cursor")
            indices = [index for index, row in enumerate(records) if cursor(row) == after]
            if not indices or indices[0] == total - 1:
                raise ValueError("capture_application_cursor_after_end")
            start = indices[0] + 1
        chosen = records[start:start + (limit if many else 1)]
        while True:
            page = {"version": 2, "kind": "nfl.capture_application_page.v2", "found": True, "application_id": application_id,
                    "view": view, "ledger_watermark": application["ledger_watermark"],
                    "registration_id": application["registration"]["registration_id"], "seal_id": application["seal"]["seal_id"],
                    "records": chosen, "total_records": total,
                    "next_after": cursor(chosen[-1]) if many and chosen and start + len(chosen) < total else None}
            try:
                V.bounded(page, V.MAX_REQUEST_BYTES)
                return copy.deepcopy(page)
            except V.capacity._WireLimit:
                if len(chosen) <= 1:
                    raise StoreError(503) from None
                chosen = chosen[:-1]


def register_application_routes(app, capture_factory, body_reader):
    def run(operation):
        if capture_factory is None:
            raise HTTPException(503, "Capture applications are disabled; no registration or learning was acknowledged")
        try:
            with capture_factory() as receiver:
                if not isinstance(receiver, CaptureApplicationReceiver):
                    raise StoreError(503)
                return operation(receiver)
        except CaptureApplicationScopeMismatch:
            raise HTTPException(404, "Capture application not found in the configured producer scope") from None
        except CaptureApplicationStopped:
            raise HTTPException(409, "Capture application stopped; read its original terminal report before proceeding") from None
        except JournalConflict:
            raise HTTPException(409, "Capture application conflict; retain original IDs and read current progress") from None
        except CaptureApplicationBudgetExceeded:
            raise HTTPException(503, "Capture operation is pending; recover the same original IDs before retrying") from None
        except StoreError as exc:
            raise HTTPException(exc.status if exc.status in (409, 429) else 503,
                                "Capture backend unavailable; no durable success is inferred") from None
        except (ValueError, TypeError, KeyError, OverflowError):
            raise HTTPException(422, "Invalid capture application evidence or command") from None
        except Exception:
            raise HTTPException(503, "Capture application unavailable; no durable success is inferred") from None

    async def data(request):
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(415, "Capture commands require application/json")
        return await body_reader(request)

    @app.post("/api/v2/nfl/capture-applications")
    async def register(request: Request):
        value = await data(request)
        return await run_in_threadpool(run, lambda receiver: receiver.register(value))

    @app.get("/api/v2/nfl/capture-applications/{application_id}")
    def application(application_id: str, request: Request):
        pairs = request.query_params
        if set(pairs) - {"view", "after", "limit", "ledger_watermark"} or any(len(pairs.getlist(key)) != 1 for key in pairs):
            raise HTTPException(422, "Use allowlisted unique capture-view parameters")
        raw_limit = pairs.get("limit", "100")
        if not re.fullmatch(r"[1-9][0-9]{0,2}", raw_limit):
            raise HTTPException(422, "Use a capture page limit between 1 and 100")
        result = run(lambda receiver: receiver.application(application_id, view=pairs.get("view", "status"),
            after=pairs.get("after"), limit=int(raw_limit), ledger_watermark=pairs.get("ledger_watermark")))
        if result is None:
            raise HTTPException(404, "Capture application not found in the configured producer scope")
        return result

    @app.post("/api/v2/nfl/capture-applications/{application_id}/steps/{step_id}")
    async def step(application_id: str, step_id: str, request: Request):
        value = await data(request)
        if type(value) is not dict or value:
            raise HTTPException(422, "Step commands contain only an empty JSON object")
        return await run_in_threadpool(run, lambda receiver: receiver.step(application_id, step_id))

    @app.get("/api/v2/nfl/capture-applications/{application_id}/steps/{step_id}")
    def original_step(application_id: str, step_id: str):
        result = run(lambda receiver: receiver.application(application_id, view="batch", step_id=step_id))
        if result is None:
            raise HTTPException(404, "Capture step not found in the configured producer scope")
        return result
