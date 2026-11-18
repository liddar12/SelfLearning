"""Robinhood adapter — stub. Optional future ring-fenced live sleeve.

Update 2026-11-10: Robinhood launched Agentic Trading (beta) on May 27, 2026, an
official MCP server that trades a separate agentic account. That is the only
supported route; reverse-engineered clients stay off-limits (ToS, stability,
lockout risk). The flag below stays False because this engine has no official
REST surface to call. An MCP-backed implementation would flip it after review.
See docs/brokers.md.
"""

from __future__ import annotations

from typing import Optional

from ..broker import Broker
from ..domain import Balances, BrokerCapabilities, Order, OrderAck, OrderIntent, Position


class RobinhoodBroker(Broker):
    def capabilities(self) -> BrokerCapabilities:
        return BrokerCapabilities(
            name="robinhood", official_api=False, options=True, streaming=False,
            fractional=True,
            notes="No REST API for this engine; official route is the Agentic Trading MCP (ring-fenced account). Not implemented.",
        )

    def get_positions(self) -> list[Position]:
        raise NotImplementedError("Unofficial API — not implemented. See module docstring.")

    def get_balances(self) -> Balances:
        raise NotImplementedError("Unofficial API — not implemented. See module docstring.")

    def place_order(self, intent: OrderIntent, client_key: str) -> OrderAck:
        raise NotImplementedError("Unofficial API — not implemented. See module docstring.")

    def cancel(self, broker_order_id: str) -> None:
        raise NotImplementedError("Unofficial API — not implemented. See module docstring.")

    def get_order(self, client_key: str) -> Optional[Order]:
        raise NotImplementedError("Unofficial API — not implemented. See module docstring.")
