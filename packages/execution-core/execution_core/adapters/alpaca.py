"""Alpaca adapter: the paper-trading target for stages S0/S1.

Official Trading API v2 with a real paper endpoint (Schwab has none), so this
is where the bot proves the Broker port before any live money. Defaults to
paper; live requires ``paper=False`` explicitly.

Equities and ETFs only in this version. Option orders (OCC symbols, multi-leg
order class) are rejected with a clear error until they get their own gate.

Network calls go through an injectable ``transport`` so tests run against
fixtures with no keys and no network. The default transport uses urllib
(stdlib, no new dependency). Credentials come from the constructor or the
ALPACA_API_KEY / ALPACA_SECRET_KEY environment variables (same names as the
official Alpaca MCP server) and are only required when a call is made.

VERIFY against https://docs.alpaca.markets before the first paper run:
endpoint paths, the 422 on a duplicate client_order_id, and status names.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

from ..broker import Broker
from ..domain import (
    Balances,
    BrokerCapabilities,
    Order,
    OrderAck,
    OrderIntent,
    OrderState,
    OrderType,
    Position,
    Side,
)

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"

# (method, url, headers, json_body or None) -> (http_status, parsed_json_or_None)
Transport = Callable[[str, str, dict, Optional[dict]], tuple[int, Any]]

_STATUS = {
    "new": OrderState.SENT,
    "accepted": OrderState.SENT,
    "pending_new": OrderState.SENT,
    "accepted_for_bidding": OrderState.SENT,
    "calculated": OrderState.SENT,
    "held": OrderState.SENT,
    "partially_filled": OrderState.PARTIAL,
    "filled": OrderState.FILLED,
    "canceled": OrderState.CANCELLED,
    "expired": OrderState.CANCELLED,
    "done_for_day": OrderState.CANCELLED,
    "replaced": OrderState.CANCELLED,
    "pending_cancel": OrderState.SENT,
    "pending_replace": OrderState.SENT,
    "rejected": OrderState.REJECTED,
    "suspended": OrderState.REJECTED,
    "stopped": OrderState.PARTIAL,
}


class AlpacaError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"Alpaca HTTP {status}: {str(body)[:300]}")
        self.status = status
        self.body = body


def urllib_transport(method: str, url: str, headers: dict, body: Optional[dict]) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else None
        except ValueError:
            return e.code, raw.decode(errors="replace")


def _f(v: Any) -> float:
    return float(v) if v not in (None, "") else 0.0


class AlpacaBroker(Broker):
    def __init__(
        self,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        paper: bool = True,
        transport: Optional[Transport] = None,
    ) -> None:
        self._key = api_key
        self._secret = secret_key
        self.paper = paper
        self.base_url = PAPER_URL if paper else LIVE_URL
        self._transport = transport or urllib_transport

    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="alpaca", official_api=True, options=True, streaming=True,
            fractional=True,
            notes=("paper endpoint" if self.paper else "LIVE endpoint")
            + "; adapter v1 places equities/ETFs only",
        )

    # --- plumbing ---------------------------------------------------------
    def _headers(self) -> dict:
        key = self._key or os.environ.get("ALPACA_API_KEY")
        secret = self._secret or os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise AlpacaError(0, "missing ALPACA_API_KEY / ALPACA_SECRET_KEY")
        return {
            "APCA-API-KEY-ID": key,
            "APCA-API-SECRET-KEY": secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _call(self, method: str, path: str, body: Optional[dict] = None,
              ok: tuple[int, ...] = (200,)) -> tuple[int, Any]:
        status, data = self._transport(method, self.base_url + path, self._headers(), body)
        if status not in ok:
            raise AlpacaError(status, data)
        return status, data

    @staticmethod
    def _to_order(o: dict, intent: Optional[OrderIntent] = None) -> Order:
        if intent is None:
            otype = OrderType.LIMIT if o.get("type") == "limit" else OrderType.MARKET
            lp = o.get("limit_price")
            intent = OrderIntent(
                symbol=o["symbol"],
                side=Side.BUY if o.get("side") == "buy" else Side.SELL,
                qty=_f(o.get("qty")),
                order_type=otype,
                limit_price=_f(lp) if lp not in (None, "") else None,
            )
        return Order(
            client_key=o.get("client_order_id", ""),
            broker_order_id=o.get("id", ""),
            intent=intent,
            state=_STATUS.get(o.get("status", ""), OrderState.SENT),
            filled_qty=_f(o.get("filled_qty")),
            avg_fill_price=_f(o.get("filled_avg_price")),
        )

    # --- Broker port ------------------------------------------------------
    def get_positions(self) -> list[Position]:
        _, data = self._call("GET", "/v2/positions")
        return [
            Position(symbol=p["symbol"], qty=_f(p.get("qty")), avg_price=_f(p.get("avg_entry_price")))
            for p in (data or [])
        ]

    def get_balances(self) -> Balances:
        _, a = self._call("GET", "/v2/account")
        return Balances(cash=_f(a.get("cash")), equity=_f(a.get("equity")),
                        buying_power=_f(a.get("buying_power")))

    def get_order(self, client_key: str) -> Optional[Order]:
        q = urllib.parse.urlencode({"client_order_id": client_key})
        status, data = self._call("GET", f"/v2/orders:by_client_order_id?{q}", ok=(200, 404))
        if status == 404 or not data:
            return None
        return self._to_order(data)

    def place_order(self, intent: OrderIntent, client_key: str) -> OrderAck:
        if not client_key:
            raise ValueError("client_key required (idempotency)")
        if (intent.meta or {}).get("instrument") == "option":
            raise NotImplementedError("Alpaca adapter v1 places equities/ETFs only; options need their own gate")
        existing = self.get_order(client_key)           # idempotent: never send a key twice
        if existing is not None:
            return OrderAck(client_key, existing.broker_order_id, existing.state)
        body: dict[str, Any] = {
            "symbol": intent.symbol,
            "qty": str(intent.qty),
            "side": intent.side.value,
            "type": intent.order_type.value,
            "time_in_force": intent.tif.value,
            "client_order_id": client_key,
        }
        if intent.order_type == OrderType.LIMIT:
            if intent.limit_price is None:
                raise ValueError("limit order missing limit_price")
            body["limit_price"] = str(intent.limit_price)
        status, data = self._call("POST", "/v2/orders", body, ok=(200, 201, 422))
        if status == 422:
            again = self.get_order(client_key)          # duplicate key raced us: report the original
            if again is not None:
                return OrderAck(client_key, again.broker_order_id, again.state)
            raise AlpacaError(status, data)
        o = self._to_order(data, intent)
        return OrderAck(client_key, o.broker_order_id, o.state)

    def cancel(self, broker_order_id: str) -> None:
        self._call("DELETE", f"/v2/orders/{urllib.parse.quote(broker_order_id)}", ok=(200, 204))
