"""Approval queue for stage S2 (APPROVE autonomy).

Every proposed live order waits here until a person approves or rejects it.
Approvals are per order (keyed by client_key), expire, and are single-use: an
approval releases exactly one send. Decisions are returned as records so the
caller can write them to the owner-intent journal; this module does no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from threading import RLock
from typing import Optional

from .domain import OrderIntent


class ApprovalState(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    RELEASED = "released"   # approved and handed to the OMS exactly once


@dataclass(frozen=True)
class ApprovalRequest:
    client_key: str
    intent: OrderIntent
    created_ts: int
    expires_ts: int
    state: ApprovalState = ApprovalState.PENDING
    decided_by: Optional[str] = None
    decided_ts: Optional[int] = None
    note: str = ""


class ApprovalQueue:
    def __init__(self, ttl_s: int = 15 * 60) -> None:
        if ttl_s <= 0:
            raise ValueError("ttl_s must be positive")
        self._ttl = ttl_s
        self._lock = RLock()
        self._items: dict[str, ApprovalRequest] = {}

    def submit(self, intent: OrderIntent, client_key: str, now_ts: int) -> ApprovalRequest:
        """Queue an order for approval. Resubmitting the same key returns the existing request."""
        if not client_key:
            raise ValueError("client_key required")
        with self._lock:
            if client_key in self._items:
                return self._items[client_key]
            req = ApprovalRequest(client_key, intent, now_ts, now_ts + self._ttl)
            self._items[client_key] = req
            return req

    def _expire(self, req: ApprovalRequest, now_ts: int) -> ApprovalRequest:
        if req.state in (ApprovalState.PENDING, ApprovalState.APPROVED) and now_ts > req.expires_ts:
            req = replace(req, state=ApprovalState.EXPIRED, decided_ts=now_ts)
            self._items[req.client_key] = req
        return req

    def _decide(self, client_key: str, state: ApprovalState, who: str, now_ts: int, note: str) -> ApprovalRequest:
        if not who:
            raise ValueError("a decision needs a named decider")
        with self._lock:
            req = self._items.get(client_key)
            if req is None:
                raise KeyError(client_key)
            req = self._expire(req, now_ts)
            if req.state != ApprovalState.PENDING:
                return req          # no-op: already decided, expired, or released
            req = replace(req, state=state, decided_by=who, decided_ts=now_ts, note=note)
            self._items[client_key] = req
            return req

    def approve(self, client_key: str, who: str, now_ts: int, note: str = "") -> ApprovalRequest:
        return self._decide(client_key, ApprovalState.APPROVED, who, now_ts, note)

    def reject(self, client_key: str, who: str, now_ts: int, note: str = "") -> ApprovalRequest:
        return self._decide(client_key, ApprovalState.REJECTED, who, now_ts, note)

    def release(self, client_key: str, now_ts: int) -> Optional[OrderIntent]:
        """Hand an approved order to the OMS. Returns the intent once, then None forever."""
        with self._lock:
            req = self._items.get(client_key)
            if req is None:
                return None
            req = self._expire(req, now_ts)
            if req.state != ApprovalState.APPROVED:
                return None
            self._items[client_key] = replace(req, state=ApprovalState.RELEASED)
            return req.intent

    def pending(self, now_ts: int) -> list[ApprovalRequest]:
        with self._lock:
            return [r for r in (self._expire(r, now_ts) for r in list(self._items.values()))
                    if r.state == ApprovalState.PENDING]

    def get(self, client_key: str) -> Optional[ApprovalRequest]:
        with self._lock:
            return self._items.get(client_key)
