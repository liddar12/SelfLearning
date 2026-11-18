"""Volatility-scaled position sizing. Pure functions, no I/O.

The VIX ÷ 16 rule: VIX is the market's annualized 30-day implied volatility for
the S&P 500, so VIX / sqrt(252) ≈ VIX / 15.87 is a one-standard-deviation daily
move in percent. The same conversion works for a single name's implied vol.
It sizes a normal move; it says nothing about direction.

Sizing rule: risk the same dollars per expected daily move. At VIX 32 the same
budget buys half the position it buys at VIX 16. Code sizes; LLMs never do.
"""

from __future__ import annotations

import math

TRADING_DAYS = 252
SQRT_TRADING_DAYS = math.sqrt(TRADING_DAYS)  # ≈ 15.87


def daily_sigma_pct(annualized_vol_pct: float) -> float:
    """Annualized vol in percent (e.g. VIX 19.74) -> one-sigma daily move in percent."""
    if annualized_vol_pct <= 0:
        raise ValueError("annualized vol must be positive")
    return annualized_vol_pct / SQRT_TRADING_DAYS


def vol_scaled_qty(
    risk_budget_dollars: float,
    price: float,
    annualized_vol_pct: float,
    max_notional: float | None = None,
) -> int:
    """Whole shares such that one expected daily move costs about risk_budget_dollars.

    qty = budget / (price * daily_sigma), floored, optionally capped by max_notional.
    Returns 0 when the budget cannot buy one share at that volatility.
    """
    if risk_budget_dollars <= 0 or price <= 0:
        return 0
    move_per_share = price * daily_sigma_pct(annualized_vol_pct) / 100.0
    qty = math.floor(risk_budget_dollars / move_per_share)
    if max_notional is not None:
        qty = min(qty, math.floor(max_notional / price))
    return max(qty, 0)


def unusual_move(move_pct: float, prior_close_vol_pct: float, threshold_sigmas: float = 2.0) -> bool:
    """True when |move| exceeds threshold × expected daily move.

    Use the vol reading from the PRIOR close: VIX rises during a selloff, so the
    same-day reading understates how surprising the move was.
    """
    return abs(move_pct) > threshold_sigmas * daily_sigma_pct(prior_close_vol_pct)
