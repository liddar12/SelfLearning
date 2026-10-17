"""Dependency-free, server-only Supabase adapter for the shared learning spine.

No SDK/global environment dependency; local users still use SqliteStore. Hosted
writes are immutable and idempotent: a replay succeeds, a changed ID conflicts.
The migration grants INSERT columns explicitly, making received_at server-owned.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Iterator, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .base import StorageBackend
from ..types import Outcome, Prediction, ResolvedPrediction


class StoreError(RuntimeError):
    """Sanitized upstream failure; never includes credentials or response bodies."""
    def __init__(self, status: int = 503):
        self.status = status
        super().__init__(f"Shared store request failed ({status})")


class StoreConflict(StoreError):
    def __init__(self):
        super().__init__(409)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class SupabaseStore(StorageBackend):
    page_size = 500

    def __init__(self, url: str, key: str):
        parts = urlsplit(url)
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")):
            raise ValueError("A plain HTTPS Supabase origin is required")
        if not key or key == "[SENSITIVE]" or any(c.isspace() for c in key):
            raise ValueError("A server-side Supabase key is required")
        self._url = url.rstrip("/")
        self._headers = {"apikey": key, "Accept": "application/json"}
        # New sb_secret keys are not JWTs. Legacy service_role JWTs also require
        # Authorization; never send a new secret as a bearer JWT.
        if not key.startswith("sb_secret_"):
            self._headers["Authorization"] = f"Bearer {key}"
        self._opener = build_opener(_NoRedirect())

    def request(self, method: str, path: str, *, query=None, data=None,
                raw: bytes | None = None, content_type: str | None = None,
                binary: bool = False) -> Any:
        if not path.startswith(("/rest/v1/", "/storage/v1/")) or ".." in path:
            raise ValueError("Invalid store path")
        headers = dict(self._headers)
        body = raw
        if data is not None:
            body = json.dumps(data, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        elif content_type:
            headers["Content-Type"] = content_type
        if method == "POST" and path in ("/rest/v1/predictions", "/rest/v1/outcomes"):
            headers["Prefer"] = "return=minimal"
        url = self._url + path + ("?" + urlencode(query) if query else "")
        req = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener.open(req, timeout=12) as response:
                payload = response.read(8 * 1024 * 1024 + 1)
                if len(payload) > 8 * 1024 * 1024:
                    raise StoreError(502)
        except HTTPError as exc:
            if exc.code == 409:
                raise StoreConflict() from None
            raise StoreError(exc.code) from None
        except (URLError, TimeoutError, OSError):
            raise StoreError() from None
        if binary:
            return payload
        try:
            return json.loads(payload) if payload else None
        except (ValueError, UnicodeError):
            raise StoreError(502) from None

    @staticmethod
    def _prediction(row: dict) -> Prediction:
        return Prediction(**{k: row[k] for k in Prediction.__dataclass_fields__})

    @staticmethod
    def _outcome(row: dict) -> Outcome:
        return Outcome(**{k: row[k] for k in Outcome.__dataclass_fields__})

    def append_prediction(self, p: Prediction) -> str:
        try:
            self.request("POST", "/rest/v1/predictions", data=asdict(p))
        except StoreConflict:
            if self.get_prediction(p.id) != p:
                raise
        return p.id

    def attach_outcome(self, o: Outcome) -> None:
        try:
            self.request("POST", "/rest/v1/outcomes", data=asdict(o))
        except StoreConflict:
            rows = self.request("GET", "/rest/v1/outcomes", query={
                "prediction_id": "eq." + o.prediction_id, "limit": 1})
            if not rows or self._outcome(rows[0]) != o:
                raise

    def get_prediction(self, id: str) -> Optional[Prediction]:
        rows = self.request("GET", "/rest/v1/predictions", query={"id": "eq." + id, "limit": 1})
        return self._prediction(rows[0]) if rows else None

    def _pages(self, task: str, *, since=None, cohort=None) -> Iterator[dict]:
        # Stable keyset pagination, not a silently truncated 1,000-row REST fetch.
        last = None
        while True:
            query = {"select": "*,outcomes(*)", "task": "eq." + task,
                     "order": "id.asc", "limit": self.page_size}
            if last is not None:
                query["id"] = "gt." + last
            if since is not None:
                query["outcomes.resolved_ts"] = "gte." + str(since)
            if cohort is not None:
                query["cohort"] = "eq." + cohort
            rows = self.request("GET", "/rest/v1/predictions", query=query)
            yield from rows
            if len(rows) < self.page_size:
                break
            last = rows[-1]["id"]

    def unresolved(self, task: str, now_ts: int) -> Iterator[Prediction]:
        for row in self._pages(task):
            if not row.get("outcomes") and row["ts"] + row["horizon_s"] <= now_ts:
                yield self._prediction(row)

    def resolved(self, task: str, since: Optional[int] = None,
                 cohort: Optional[str] = None) -> Iterator[ResolvedPrediction]:
        for row in self._pages(task, since=since, cohort=cohort):
            outcome = row.get("outcomes")
            if outcome:
                if isinstance(outcome, list):
                    outcome = outcome[0]
                yield ResolvedPrediction(self._prediction(row), self._outcome(outcome))

    def recent(self, limit: int = 25) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        return self.request("GET", "/rest/v1/predictions", query={
            "select": "*,outcomes(*)", "order": "received_at.desc,id.asc", "limit": limit})

    def status(self) -> dict:
        return self.request("POST", "/rest/v1/rpc/selflearn_status", data={})
