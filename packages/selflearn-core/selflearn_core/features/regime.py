"""Market-regime feature packs: RSP/SPY breadth and the VIX expected move.

``compute_breadth`` is a line-for-line port of ``buildPayload`` in
liddar-terminal ``api/breadth.js`` (minus the live intraday replacement, which
has no point-in-time meaning in a backtest). Parity is pinned by a test fixture
generated from the JS. If one changes, change the other.

``VolRegimePack`` turns VIX into a one-sigma daily move (VIX / sqrt(252), the
"divide by 16" rule) and measures today's index move in sigmas, using the
PRIOR close's VIX because VIX rises during selloffs. It sizes a normal move;
it does not predict direction.

Both packs follow the spine's rules: only bars closed at or before
``as_of_ts`` are used, every value carries an observation time, and a pack with
too little data returns empty values instead of guessing.
"""

from __future__ import annotations

import math
from typing import Any, Optional

from .bars import Bar, BarSource
from .base import FeatureFamily, FeaturePack, FeatureSnapshot

SQRT_TRADING_DAYS = math.sqrt(252)  # ≈ 15.87
DIRECTION_DEAD_ZONE = 0.003          # same 0.3% dead zone as api/breadth.js
UNUSUAL_SIGMAS = 2.0


def _sma(arr: list[float], i: int, n: int) -> Optional[float]:
    if i + 1 < n:
        return None
    return sum(arr[i - n + 1 : i + 1]) / n


def compute_breadth(num: list[Bar], den: list[Bar]) -> dict[str, Any]:
    """RSP/SPY-style ratio regime from two bar series aligned on timestamp.

    Returns {} when fewer than 60 aligned points exist (same floor as the JS).
    """
    den_by_ts = {b.ts: b.close for b in den if b.close}
    ts, ratio = [], []
    for b in sorted(num, key=lambda x: x.ts):
        d = den_by_ts.get(b.ts)
        if b.close and d:
            ts.append(b.ts)
            ratio.append(b.close / d)
    if len(ratio) < 60:
        return {}

    n = len(ratio) - 1
    last = ratio[n]
    dma50 = _sma(ratio, n, 50)
    dma200 = _sma(ratio, n, 200)
    base = ratio[max(0, n - 20)]
    chg20 = (last - base) / base
    direction = "rising" if chg20 > DIRECTION_DEAD_ZONE else "falling" if chg20 < -DIRECTION_DEAD_ZONE else "flat"

    lows = []
    for end in (n - 40, n - 20, n):
        start = max(0, end - 19)
        if start >= 0 and end > start:
            lows.append(min(ratio[start : end + 1]))
    higher_lows = len(lows) == 3 and lows[0] < lows[1] < lows[2]

    out: dict[str, Any] = {
        "ratio": round(last, 5),
        "chg20": round(chg20, 6),
        "direction": direction,
        "higher_lows": higher_lows,
        "_obs_ts": ts[n],
    }
    if dma50 is not None:
        out["dma50"] = round(dma50, 5)
        out["vs50dma"] = "above" if last >= dma50 else "below"
    if dma200 is not None:
        out["dma200"] = round(dma200, 5)
        out["vs200dma"] = "above" if last >= dma200 else "below"
    return out


def regime_string(b: dict[str, Any]) -> str:
    """The same one-line regime string the terminal injects into scanner prompts."""
    if not b:
        return ""
    d = b["direction"]
    parts = [f"breadth {'flat' if d == 'flat' else 'wave ' + ('up' if d == 'rising' else 'down')}"]
    if "vs50dma" in b:
        parts.append(f"ratio {b['vs50dma']} 50dma")
    if "vs200dma" in b:
        parts.append(f"{b['vs200dma']} 200dma")
    if b.get("higher_lows"):
        parts.append("higher lows intact")
    return ", ".join(parts)


class BreadthPack(FeaturePack):
    """Equal-weight vs cap-weight breadth regime (default RSP/SPY)."""

    family = FeatureFamily.REGIME
    name = "breadth"

    def __init__(self, source: BarSource, numerator: str = "RSP", denominator: str = "SPY",
                 lookback_bars: int = 260) -> None:
        self.source = source
        self.numerator = numerator
        self.denominator = denominator
        self.lookback_bars = lookback_bars

    def snapshot(self, symbol: str, as_of_ts: int) -> FeatureSnapshot:
        """``symbol`` is ignored: breadth is market-wide. Kept for the FeaturePack contract."""
        num = [b for b in self.source.bars(self.numerator, as_of_ts, self.lookback_bars) if b.ts <= as_of_ts]
        den = [b for b in self.source.bars(self.denominator, as_of_ts, self.lookback_bars) if b.ts <= as_of_ts]
        b = compute_breadth(num, den)
        obs = b.pop("_obs_ts", as_of_ts)
        return FeatureSnapshot(self.name, self.family, as_of_ts, b, {k: obs for k in b})


def compute_vol_regime(index_bars: list[Bar], vol_bars: list[Bar]) -> dict[str, Any]:
    """Today's index move measured against the prior close's implied daily move.

    Needs the last two index closes and a vol close strictly before the last
    index bar. Returns {} otherwise.
    """
    idx = sorted(index_bars, key=lambda b: b.ts)
    if len(idx) < 2 or not idx[-2].close:
        return {}
    today, prev = idx[-1], idx[-2]
    prior_vol = [v for v in sorted(vol_bars, key=lambda b: b.ts) if v.ts < today.ts and v.close > 0]
    if not prior_vol:
        return {}
    vix = prior_vol[-1].close
    sigma = vix / SQRT_TRADING_DAYS
    move = (today.close / prev.close - 1.0) * 100.0
    sigmas = move / sigma
    return {
        "vix_prior_close": round(vix, 4),
        "daily_sigma_pct": round(sigma, 4),
        "index_move_pct": round(move, 4),
        "move_in_sigmas": round(sigmas, 3),
        "unusual_move": abs(sigmas) > UNUSUAL_SIGMAS,
        "_obs_ts": {"vol": prior_vol[-1].ts, "index": today.ts},
    }


class VolRegimePack(FeaturePack):
    """VIX ÷ 16 expected move and the unusual-move flag (default ^VIX vs SPY)."""

    family = FeatureFamily.REGIME
    name = "vol"

    def __init__(self, source: BarSource, vol_symbol: str = "^VIX", index_symbol: str = "SPY") -> None:
        self.source = source
        self.vol_symbol = vol_symbol
        self.index_symbol = index_symbol

    def snapshot(self, symbol: str, as_of_ts: int) -> FeatureSnapshot:
        idx = [b for b in self.source.bars(self.index_symbol, as_of_ts, 5) if b.ts <= as_of_ts]
        vol = [b for b in self.source.bars(self.vol_symbol, as_of_ts, 5) if b.ts <= as_of_ts]
        v = compute_vol_regime(idx, vol)
        obs = v.pop("_obs_ts", None)
        obs_ts: dict[str, int] = {}
        if obs:
            for k in v:
                obs_ts[k] = obs["vol"] if k in ("vix_prior_close", "daily_sigma_pct") else obs["index"]
        return FeatureSnapshot(self.name, self.family, as_of_ts, v, obs_ts)
