"""Tests for the bot additions: extended risk gate, vol sizing, approval queue."""

import math

import pytest

from execution_core import (
    AccountState,
    ApprovalQueue,
    ApprovalState,
    Balances,
    Limits,
    OrderIntent,
    OrderType,
    Position,
    Quote,
    Side,
    check,
    daily_sigma_pct,
    drawdown_breached,
    unusual_move,
    vol_scaled_qty,
)

Q = Quote("AAPL", 99.0, 101.0, ts=0)


def _state(**kw):
    eq = kw.pop("equity", 100_000)
    bal = Balances(cash=eq, equity=eq, buying_power=eq)
    return AccountState(kw.pop("positions", []), bal, **kw)


def _limits(**kw):
    base = dict(max_position_notional=10_000, max_gross_notional=50_000, daily_loss_cap=2_000)
    base.update(kw)
    return Limits(**base)


def _buy(qty=10, **kw):
    return OrderIntent("AAPL", Side.BUY, qty, **kw)


def _limit_buy(px=101.0, qty=10, meta=None):
    return OrderIntent("AAPL", Side.BUY, qty, OrderType.LIMIT, limit_price=px, meta=meta or {})


# --- backward compatibility ---------------------------------------------------

def test_new_limits_default_off():
    assert check(_buy(), Q, _state(), _limits(), "k").ok is True


# --- daily trade cap ------------------------------------------------------------

def test_trade_cap_blocks_new_entries_only():
    lim = _limits(max_trades_per_day=3)
    assert check(_buy(), Q, _state(trades_today=2), lim, "k").ok is True
    res = check(_buy(), Q, _state(trades_today=3), lim, "k")
    assert res.ok is False and "trade cap" in res.reason
    sell = OrderIntent("AAPL", Side.SELL, 5)
    held = [Position("AAPL", 10, 100.0)]
    assert check(sell, Q, _state(trades_today=3, positions=held), lim, "k").ok is True  # exits allowed


# --- drawdown kill switch -------------------------------------------------------

def test_drawdown_kill_switch_trips_at_threshold():
    lim = _limits(drawdown_kill_pct=0.10)
    assert check(_buy(), Q, _state(equity=91_000, equity_high=100_000), lim, "k").ok is True
    st = _state(equity=90_000, equity_high=100_000)
    assert drawdown_breached(st, lim) is True
    res = check(_buy(), Q, st, lim, "k")
    assert res.ok is False and "drawdown" in res.reason


def test_latched_kill_switch_blocks_everything():
    res = check(_buy(), Q, _state(kill_switch_tripped=True), _limits(), "k")
    assert res.ok is False and "manual reset" in res.reason


# --- order type and collar --------------------------------------------------------

def test_limit_only_rejects_market():
    res = check(_buy(), Q, _state(), _limits(limit_only=True), "k")
    assert res.ok is False and "limit orders only" in res.reason
    assert check(_limit_buy(), Q, _state(), _limits(limit_only=True), "k").ok is True


def test_collar_rejects_chasing_limit():
    lim = _limits(limit_only=True, collar_pct=0.01)
    assert check(_limit_buy(px=102.0), Q, _state(), lim, "k").ok is True     # within 1% of ask
    res = check(_limit_buy(px=103.0), Q, _state(), lim, "k")
    assert res.ok is False and "collar" in res.reason


def test_limit_without_price_rejected():
    intent = OrderIntent("AAPL", Side.BUY, 1, OrderType.LIMIT)
    assert check(intent, Q, _state(), _limits(), "k").ok is False


# --- earnings blackout --------------------------------------------------------------

@pytest.mark.parametrize("meta,ok", [
    ({"days_to_earnings": 1}, False),
    ({"days_to_earnings": 2}, False),
    ({"days_to_earnings": 3}, True),
    ({"no_earnings": True}, True),      # ETF
    ({}, False),                        # unknown date -> fail flat
])
def test_earnings_blackout(meta, ok):
    lim = _limits(earnings_blackout_days=2)
    assert check(_buy(meta=meta), Q, _state(), lim, "k").ok is ok


# --- options -----------------------------------------------------------------------

def _opt(**meta):
    m = {"instrument": "option", "defined_risk": True, "max_loss": 1_444, "multiplier": 100}
    m.update(meta)
    return OrderIntent("NVDA_SPREAD", Side.BUY, 2, OrderType.LIMIT, limit_price=2.83, meta=m)


OQ = Quote("NVDA_SPREAD", 2.78, 2.88, ts=0)


def test_options_rejected_unless_enabled():
    res = check(_opt(), OQ, _state(), _limits(), "k")
    assert res.ok is False and "not enabled" in res.reason


def test_defined_risk_spread_under_cap_passes():
    lim = _limits(allow_options=True, max_loss_per_trade=1_500, min_pop_credit=0.70)
    assert check(_opt(credit=True, pop=0.72), OQ, _state(), lim, "k").ok is True


@pytest.mark.parametrize("meta,needle", [
    ({"defined_risk": False}, "defined-risk"),
    ({"max_loss": None}, "missing max_loss"),
    ({"max_loss": 2_740}, "per-trade cap"),
    ({"credit": True}, "missing probability"),
    ({"credit": True, "pop": 0.67}, "below floor"),
])
def test_option_rejections(meta, needle):
    lim = _limits(allow_options=True, max_loss_per_trade=1_500, min_pop_credit=0.70)
    res = check(_opt(**meta), OQ, _state(), lim, "k")
    assert res.ok is False and needle in res.reason


def test_option_multiplier_applies_to_notional():
    lim = _limits(allow_options=True, max_position_notional=500)
    res = check(_opt(), OQ, _state(), lim, "k")          # 2 * 2.88 * 100 = 576 > 500
    assert res.ok is False and "position size" in res.reason


# --- sizing --------------------------------------------------------------------------

def test_vix_divided_by_sixteen():
    assert daily_sigma_pct(16) == pytest.approx(16 / math.sqrt(252))
    assert daily_sigma_pct(19.74) == pytest.approx(1.2435, abs=1e-3)


def test_vol_scaled_qty_halves_when_vol_doubles():
    q16 = vol_scaled_qty(100, 100.0, 16)
    q32 = vol_scaled_qty(100, 100.0, 32)
    assert q16 == 99 and q32 == 49
    assert vol_scaled_qty(100, 100.0, 16, max_notional=5_000) == 50
    assert vol_scaled_qty(0, 100.0, 16) == 0


def test_unusual_move_uses_two_sigma():
    assert unusual_move(-0.5, 16) is False
    assert unusual_move(-2.1, 16) is True
    assert unusual_move(2.0, 19.74) is False      # 1.6 sigma at VIX 19.74


# --- approval queue --------------------------------------------------------------------

def test_approval_release_is_single_use():
    qq = ApprovalQueue(ttl_s=60)
    qq.submit(_buy(), "a1", now_ts=0)
    assert qq.release("a1", now_ts=1) is None                  # not approved yet
    assert qq.approve("a1", who="jimmy", now_ts=2).state == ApprovalState.APPROVED
    assert qq.release("a1", now_ts=3) is not None
    assert qq.release("a1", now_ts=4) is None                  # never twice


def test_approval_expires():
    qq = ApprovalQueue(ttl_s=60)
    qq.submit(_buy(), "a2", now_ts=0)
    assert qq.approve("a2", who="jimmy", now_ts=61).state == ApprovalState.EXPIRED
    assert qq.release("a2", now_ts=62) is None


def test_reject_is_final_and_resubmit_is_idempotent():
    qq = ApprovalQueue()
    r1 = qq.submit(_buy(), "a3", now_ts=0)
    assert qq.submit(_buy(qty=99), "a3", now_ts=5) is r1
    qq.reject("a3", who="jimmy", now_ts=1, note="too close to earnings")
    assert qq.approve("a3", who="jimmy", now_ts=2).state == ApprovalState.REJECTED
    assert qq.pending(now_ts=3) == []


def test_decision_requires_named_decider():
    qq = ApprovalQueue()
    qq.submit(_buy(), "a4", now_ts=0)
    with pytest.raises(ValueError):
        qq.approve("a4", who="", now_ts=1)
