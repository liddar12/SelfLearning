"""Scoped RPC storage for trading inside a shared Supabase project.

This opt-in adapter never addresses public NFL tables, uses no elevated project
key, and never retries requests. An independently provisioned Auth worker and
the shared-trading-v1 migration are prerequisites. The token provider owns
private session refresh; decoded claims below are only rejection checks, not
authentication. Supabase verifies the signature and database permissions.
"""

from __future__ import annotations

import base64
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Mapping, Optional, Protocol
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..types import Outcome, Prediction, ResolvedPrediction
from .base import StorageBackend

_MAX_RESPONSE = 8 * 1024 * 1024
_MAX_RECORD = 64_000
_MAX_INTEGER = 9_007_199_254_740_991
_TASK = re.compile(r"trading\.[a-z][a-z0-9_.-]*\Z")
_LEGACY_TASKS = frozenset({"scanner", "trade_decision", "price_action_trend"})
_UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
_RPCS = frozenset({
    "trade_bot_status", "trade_bot_append_prediction", "trade_bot_attach_outcome",
    "trade_bot_get_prediction", "trade_bot_list_resolved", "trade_bot_list_unresolved",
})


class TradingStorageError(RuntimeError):
    """Fixed, credential-safe errors; never contains raw upstream bodies."""


@dataclass(frozen=True)
class RPCResponse:
    status: int
    body: bytes


class RPCTransport(Protocol):
    def __call__(self, url: str, *, headers: Mapping[str, str], body: bytes,
                 timeout: float) -> RPCResponse: ...


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_transport(url: str, *, headers: Mapping[str, str], body: bytes,
                    timeout: float) -> RPCResponse:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            data = response.read(_MAX_RESPONSE + 1)
            return RPCResponse(response.status, data)
    except HTTPError as error:
        # Do not read/retain provider error bodies or redirect destinations.
        status = error.code
        error.close()
        return RPCResponse(status, b"")


def _claims(token: str) -> dict[str, Any]:
    if not isinstance(token, str) or not 20 <= len(token) <= 8192:
        raise ValueError("Invalid scoped credential.")
    parts = token.split(".")
    if len(parts) != 3 or any(not re.fullmatch(r"[A-Za-z0-9_-]+", p) for p in parts):
        raise ValueError("Invalid scoped credential.")
    try:
        value = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
    except (ValueError, UnicodeError):
        raise ValueError("Invalid scoped credential.") from None
    if not isinstance(value, dict):
        raise ValueError("Invalid scoped credential.")
    return value


def _text(value: Any, *, maximum: int = 200) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


def _integer(value: Any, *, minimum: int = 0, maximum: int = _MAX_INTEGER) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _trading_task(task: Any) -> bool:
    return _text(task) and (task in _LEGACY_TASKS or bool(_TASK.fullmatch(task)))


def _timestamp(value: Any) -> datetime:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError
        return parsed.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise TradingStorageError("Storage returned an invalid receipt timestamp.") from None


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Record must contain finite JSON values.") from None


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Nonfinite JSON number")
    return number


def _reject_constant(value: str) -> None:
    raise ValueError("Nonfinite JSON constant")


def _prediction(value: Any) -> Prediction:
    if not isinstance(value, dict):
        raise ValueError("Invalid prediction record.")
    required = ("id", "ts", "model_version", "task", "features", "prediction", "horizon_s")
    if any(k not in value for k in required):
        raise ValueError("Invalid prediction record.")
    if not all(_text(value[k]) for k in ("id", "model_version")) or not _trading_task(value["task"]):
        raise ValueError("Invalid trading prediction identity or task.")
    if not _integer(value["ts"]) or not _integer(value["horizon_s"], minimum=1, maximum=315_576_000):
        raise ValueError("Invalid prediction time or horizon.")
    if not isinstance(value["features"], dict) or not isinstance(value.get("meta", {}), dict):
        raise ValueError("Invalid prediction metadata.")
    confidence = value.get("confidence")
    if confidence is not None and (type(confidence) not in (int, float)
                                   or not math.isfinite(confidence) or not 0 <= confidence <= 1):
        raise ValueError("Invalid prediction confidence.")
    cohort = value.get("cohort")
    if cohort is not None and (not isinstance(cohort, str) or len(cohort) > 200):
        raise ValueError("Invalid prediction cohort.")
    return Prediction(**{k: value[k] for k in required}, confidence=confidence,
                      cohort=cohort, meta=value.get("meta", {}))


def _outcome(value: Any, prediction_id: str) -> Outcome:
    if (not isinstance(value, dict) or "realized" not in value
            or not _integer(value.get("resolved_ts"))
            or not isinstance(value.get("meta", {}), dict)
            or value.get("prediction_id", prediction_id) != prediction_id):
        raise ValueError("Invalid outcome record or relationship.")
    return Outcome(prediction_id=prediction_id, resolved_ts=value["resolved_ts"],
                   realized=value["realized"], meta=value.get("meta", {}))


class TradingSupabaseStore(StorageBackend):
    """Explicit worker-only store. Constructor performs no network calls.

    ``access_token_provider`` supplies a current Supabase Auth user token for
    every request, including refresh when needed. Provision the worker in the
    database allowlist separately. This class does not sign in, manage accounts,
    persist tokens, create a schema, schedule work, or place orders.

    Paged scans use a fixed database-issued receipt cutoff, not an exported
    MVCC snapshot. For a repeatable evaluation dataset, finish ingestion and
    settle pending transactions before scanning, or use a separately persisted
    evidence manifest. Cursor checks cannot detect every late-committing row.
    """

    def __init__(self, *, url: str, expected_project_ref: str, publishable_key: str,
                 access_token_provider: Callable[[], str],
                 transport: RPCTransport = _http_transport,
                 page_size: int = 100, max_pages: int = 100, timeout: float = 12.0) -> None:
        if (not isinstance(expected_project_ref, str)
                or not re.fullmatch(r"[a-z0-9]{20}", expected_project_ref)
                or url not in (f"https://{expected_project_ref}.supabase.co",
                               f"https://{expected_project_ref}.supabase.co/")):
            raise ValueError("Supabase URL must match the explicitly expected project.")
        if not isinstance(publishable_key, str):
            raise ValueError("A public Supabase API key is required.")
        if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]+", publishable_key):
            claims = _claims(publishable_key)
            if claims.get("role") != "anon" or claims.get("ref", expected_project_ref) != expected_project_ref:
                raise ValueError("An elevated project key is not permitted.")
        if (not callable(access_token_provider) or not callable(transport)
                or not _integer(page_size, minimum=1, maximum=500)
                or not _integer(max_pages, minimum=1, maximum=1000)
                or type(timeout) not in (int, float) or not math.isfinite(timeout)
                or not 0 < timeout <= 60):
            raise ValueError("Invalid trading-store configuration.")
        self._url = f"https://{expected_project_ref}.supabase.co"
        self._key = publishable_key
        self._token_provider = access_token_provider
        self._transport = transport
        self._page_size, self._max_pages, self._timeout = page_size, max_pages, timeout

    def _token(self) -> str:
        try:
            token = self._token_provider()
            claims = _claims(token)
            expiry = claims.get("exp")
            audience = claims.get("aud")
            if (claims.get("role") != "authenticated"
                    or not _UUID.fullmatch(claims.get("sub", ""))
                    or claims.get("iss") != f"{self._url}/auth/v1"
                    or not (audience == "authenticated" or isinstance(audience, list) and "authenticated" in audience)
                    or type(expiry) not in (int, float) or not math.isfinite(expiry)
                    or expiry <= time.time()):
                raise ValueError
            return token
        except Exception:
            raise TradingStorageError("A current scoped Auth worker token is required.") from None

    def _rpc(self, name: str, params: dict[str, Any]) -> Any:
        if name not in _RPCS:
            raise TradingStorageError("Unsupported trading storage operation.")
        body = _json_bytes(params)
        token = self._token()
        try:
            response = self._transport(
                f"{self._url}/rest/v1/rpc/{name}",
                headers={"apikey": self._key, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json", "Accept": "application/json"},
                body=body, timeout=self._timeout,
            )
        except Exception:
            raise TradingStorageError("Trading storage transport failed; no request was retried.") from None
        if not isinstance(response, RPCResponse) or type(response.status) is not int:
            raise TradingStorageError("Trading storage returned an invalid response.")
        if not 200 <= response.status < 300:
            # Fixed status code only. A timed-out write may have been committed;
            # the caller must reconcile its ID before deciding whether to retry.
            raise TradingStorageError(f"Trading storage request failed (HTTP {response.status}).")
        if not isinstance(response.body, bytes) or len(response.body) > _MAX_RESPONSE:
            raise TradingStorageError("Trading storage response exceeded its safe bound.")
        try:
            return json.loads(response.body, parse_constant=_reject_constant, parse_float=_finite_float)
        except (ValueError, UnicodeError):
            raise TradingStorageError("Trading storage returned invalid JSON.") from None

    def check_ready(self) -> dict[str, str]:
        status = self._rpc("trade_bot_status", {})
        if (not isinstance(status, dict) or status.get("schema_version") != "shared-trading-v1"
                or status.get("principal_kind") != "worker"):
            raise TradingStorageError("Trading worker storage is not authorized or configured.")
        _timestamp(status.get("server_time"))
        return {"schema_version": status["schema_version"], "principal_kind": "worker",
                "server_time": status["server_time"]}

    def append_prediction(self, p: Prediction) -> str:
        record = asdict(p)
        _prediction(record)
        if len(_json_bytes(record)) > _MAX_RECORD:
            raise ValueError("Prediction record exceeds its safe bound.")
        self.check_ready()
        result = self._rpc("trade_bot_append_prediction", {"p_record": record})
        if result != p.id:
            raise TradingStorageError("Storage returned an unexpected prediction identity.")
        return p.id

    def attach_outcome(self, o: Outcome) -> None:
        if not _text(o.prediction_id):
            raise ValueError("Invalid prediction identity.")
        record = asdict(o)
        _outcome(record, o.prediction_id)
        if len(_json_bytes(record)) > _MAX_RECORD:
            raise ValueError("Outcome record exceeds its safe bound.")
        self.check_ready()
        result = self._rpc("trade_bot_attach_outcome", {"p_record": record})
        if result != o.prediction_id:
            raise TradingStorageError("Storage returned an unexpected outcome relationship.")

    def get_prediction(self, id: str) -> Optional[Prediction]:
        if not _text(id):
            raise ValueError("Invalid prediction identity.")
        self.check_ready()
        row = self._rpc("trade_bot_get_prediction", {"p_id": id})
        if row is None:
            return None
        try:
            prediction = _prediction(row)
            _timestamp(row.get("received_at"))
            if prediction.id != id:
                raise ValueError
            return prediction
        except (ValueError, TypeError):
            raise TradingStorageError("Storage returned an invalid scoped prediction.") from None

    def _scan(self, name: str, task: str, **extra: Any) -> list[dict[str, Any]]:
        if not _trading_task(task):
            raise ValueError("Task is outside the trading store.")
        snapshot = self.check_ready()["server_time"]
        cutoff = _timestamp(snapshot)
        params = {"p_task": task, "p_limit": self._page_size, "p_as_of": snapshot, **extra}
        previous: Optional[tuple[datetime, str]] = None
        seen: set[str] = set()
        output = []
        for _ in range(self._max_pages):
            rows = self._rpc(name, params)
            if not isinstance(rows, list) or len(rows) > self._page_size:
                raise TradingStorageError("Storage returned an invalid page.")
            for row in rows:
                try:
                    prediction = _prediction(row)
                    receipt = _timestamp(row.get("received_at"))
                    cursor = (receipt, prediction.id)
                    if (prediction.task != task or receipt > cutoff or prediction.id in seen
                            or previous is not None and cursor >= previous):
                        raise ValueError
                    if name == "trade_bot_list_resolved":
                        outcomes = row.get("outcomes")
                        if (not isinstance(outcomes, list) or len(outcomes) != 1
                                or not isinstance(outcomes[0], dict)
                                or _timestamp(outcomes[0].get("received_at")) > cutoff):
                            raise ValueError
                except (ValueError, TypeError):
                    raise TradingStorageError("Storage returned a mixed, duplicate, or unordered page.") from None
                seen.add(prediction.id)
                previous = cursor
                output.append(row)
            if len(rows) < self._page_size:
                return output
            params.update(p_before_received_at=rows[-1]["received_at"], p_before_id=rows[-1]["id"])
        raise TradingStorageError("Trading storage scan exceeded its page limit; results were not returned.")

    def unresolved(self, task: str, now_ts: int) -> Iterator[Prediction]:
        if not _integer(now_ts):
            raise ValueError("Invalid resolution time.")
        rows = self._scan("trade_bot_list_unresolved", task, p_now_ts=now_ts)
        predictions = [_prediction(row) for row in rows]
        if any(p.ts + p.horizon_s > now_ts for p in predictions):
            raise TradingStorageError("Storage returned a prediction before its horizon.")
        return iter(predictions)

    def resolved(self, task: str, since: Optional[int] = None,
                 cohort: Optional[str] = None) -> Iterator[ResolvedPrediction]:
        if since is not None and not _integer(since):
            raise ValueError("Invalid resolution cutoff.")
        if cohort is not None and (not isinstance(cohort, str) or len(cohort) > 200):
            raise ValueError("Invalid cohort.")
        rows = self._scan("trade_bot_list_resolved", task)
        output = []
        for row in rows:
            try:
                prediction = _prediction(row)
                outcomes = row.get("outcomes")
                if not isinstance(outcomes, list) or len(outcomes) != 1:
                    raise ValueError
                outcome = _outcome(outcomes[0], prediction.id)
                _timestamp(outcomes[0].get("received_at"))
                if outcome.resolved_ts < prediction.ts:
                    raise ValueError
            except (ValueError, TypeError):
                raise TradingStorageError("Storage returned an invalid outcome join.") from None
            if (since is None or outcome.resolved_ts >= since) and (cohort is None or prediction.cohort == cohort):
                output.append(ResolvedPrediction(prediction, outcome))
        return iter(output)
