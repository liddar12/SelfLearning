"""Pre-trade risk gate. Pure function, no I/O, exhaustively testable.

The ONLY path from a signal to a broker runs through check(). It returns a pass
or one specific rejection reason. No check may be skipped; there is no bypass.

Every limit added after the original gate is optional (None/False = off), so
existing callers keep their behavior. When a limit is on and the information it
needs is missing, the gate rejects: fail flat, never guess.

Options note: OrderIntent is single-symbol. A multi-leg option position is sent
as one intent whose ``meta`` declares ``instrument="option"``, ``defined_risk``,
``max_loss`` (dollars for the whole order), and, for credit spreads, ``pop``
(probability of profit, 0..1) from the odds engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .domain import Balances, OrderIntent, OrderType, Position, Quote, Side


@dataclass(frozen=True)
class Limits:
    max_position_notional: float
    max_gross_notional: float
    daily_loss_cap: float                      # positive number; session halts at -cap
    symbol_allowlist: Optional[frozenset] = None  # None = allow any
    # --- added for the bot (all optional; None/False = off) ---
    max_trades_per_day: Optional[int] = None   # new entries per session (Chief gate)
    drawdown_kill_pct: Optional[float] = None  # e.g. 0.10 = halt at -10% from equity high
    limit_only: bool = False                   # reject market orders
    collar_pct: Optional[float] = None         # limit may sit at most this far through the touch
    earnings_blackout_days: Optional[int] = None  # no entry within N sessions of a report
    max_loss_per_trade: Optional[float] = None    # dollars; options must declare max_loss
    min_pop_credit: Optional[float] = None        # credit spreads: probability-of-profit floor
    allow_options: bool = False                   # options rejected unless explicitly allowed


@dataclass(frozen=True)
class AccountState:
    positions: list[Position]
    balances: Balances
    day_realized_pnl: float = 0.0
    # --- added for the bot ---
    trades_today: int = 0                      # entries already sent this session
    equity_high: Optional[float] = None        # high-water mark for the drawdown kill switch
    kill_switch_tripped: bool = False          # latched; only a manual reset clears it


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: Optional[str] = None


def _fill_price(intent: OrderIntent, q: Quote) -> float:
    return q.ask if intent.side == Side.BUY else q.bid


def drawdown_breached(state: AccountState, limits: Limits) -> bool:
    """True when equity has fallen drawdown_kill_pct or more below its high."""
    if limits.drawdown_kill_pct is None or not state.equity_high:
        return False
    floor = state.equity_high * (1.0 - limits.drawdown_kill_pct)
    return state.balances.equity <= floor + 1e-9   # tolerance so exactly -10% trips


def _check_options(intent: OrderIntent, limits: Limits) -> Optional[str]:
    m = intent.meta or {}
    if m.get("instrument") != "option":
        return None
    if not limits.allow_options:
        return "options not enabled"
    if m.get("defined_risk") is not True:
        return "options must be defined-risk (no naked short options)"
    max_loss = m.get("max_loss")
    if not isinstance(max_loss, (int, float)) or max_loss <= 0:
        return "option order missing max_loss"
    if limits.max_loss_per_trade is not None and max_loss > limits.max_loss_per_trade:
        return f"max loss {max_loss:.0f} exceeds per-trade cap {limits.max_loss_per_trade:.0f}"
    if m.get("credit") is True and limits.min_pop_credit is not None:
        pop = m.get("pop")
        if not isinstance(pop, (int, float)):
            return "credit spread missing probability of profit"
        if pop < limits.min_pop_credit:
            return f"probability of profit {pop:.2f} below floor {limits.min_pop_credit:.2f}"
    return None


def _check_earnings(intent: OrderIntent, limits: Limits) -> Optional[str]:
    if limits.earnings_blackout_days is None or intent.side != Side.BUY:
        return None
    m = intent.meta or {}
    if m.get("no_earnings") is True:          # ETFs, index products
        return None
    dte = m.get("days_to_earnings")
    if not isinstance(dte, int):
        return "earnings date unknown"
    if 0 <= dte <= limits.earnings_blackout_days:
        return f"earnings in {dte} sessions (blackout {limits.earnings_blackout_days})"
    return None


def _check_order_type(intent: OrderIntent, quote: Quote, limits: Limits) -> Optional[str]:
    if limits.limit_only and intent.order_type != OrderType.LIMIT:
        return "limit orders only"
    if intent.order_type == OrderType.LIMIT:
        if intent.limit_price is None or intent.limit_price <= 0:
            return "limit order missing limit price"
        if limits.collar_pct is not None:
            if intent.side == Side.BUY and intent.limit_price > quote.ask * (1 + limits.collar_pct):
                return "limit price outside collar"
            if intent.side == Side.SELL and intent.limit_price < quote.bid * (1 - limits.collar_pct):
                return "limit price outside collar"
    return None


def check(
    intent: OrderIntent,
    quote: Optional[Quote],
    state: AccountState,
    limits: Limits,
    client_key: str,
    wash_sale: bool = False,
) -> GateResult:
    """All checks must pass. First failure returns its reason."""
    if not client_key:
        return GateResult(False, "missing idempotency key")
    if intent.qty <= 0:
        return GateResult(False, "non-positive quantity")
    if state.kill_switch_tripped:
        return GateResult(False, "kill switch tripped; manual reset required")
    if drawdown_breached(state, limits):
        return GateResult(False, "drawdown kill switch: equity below limit from high")
    if limits.symbol_allowlist is not None and intent.symbol not in limits.symbol_allowlist:
        return GateResult(False, f"symbol not in allowlist: {intent.symbol}")
    if wash_sale:
        return GateResult(False, "wash-sale: order would void a harvested loss")
    if quote is None:
        return GateResult(False, "no quote available")
    if state.day_realized_pnl <= -limits.daily_loss_cap:
        return GateResult(False, "daily loss cap hit; session halted")
    if (
        limits.max_trades_per_day is not None
        and intent.side == Side.BUY
        and state.trades_today >= limits.max_trades_per_day
    ):
        return GateResult(False, f"daily trade cap reached ({limits.max_trades_per_day})")

    for sub in (
        _check_order_type(intent, quote, limits),
        _check_options(intent, limits),
        _check_earnings(intent, limits),
    ):
        if sub:
            return GateResult(False, sub)

    multiplier = float((intent.meta or {}).get("multiplier", 1))  # 100 for US equity options
    notional = intent.qty * _fill_price(intent, quote) * multiplier
    if notional > limits.max_position_notional:
        return GateResult(False, "position size cap exceeded")
    if intent.side == Side.BUY and notional > state.balances.buying_power:
        return GateResult(False, "insufficient buying power")

    gross = sum(abs(p.qty) * p.avg_price for p in state.positions) + notional
    if gross > limits.max_gross_notional:
        return GateResult(False, "gross exposure cap exceeded")

    return GateResult(True, None)
