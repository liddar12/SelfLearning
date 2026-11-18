"""Live control-state tests. Pure, no threads exercised (logic only)."""

import pytest

from execution_core.control import Autonomy, AutoLockedError, ControlState, RiskLevel, RunState


def test_default_is_the_safe_corner():
    c = ControlState()
    assert c.snapshot() == (RunState.PAUSED, Autonomy.SIM, RiskLevel.OFF)
    assert c.can_place_live() is False
    assert c.can_simulate() is False


def test_autonomous_live_requires_running_auto_and_risk_on():
    c = ControlState(allow_auto=True)
    c.resume(); c.set_autonomy(Autonomy.AUTO); c.set_risk(RiskLevel.MEDIUM)
    assert c.can_place_live() is True         # no human approval needed


def test_pause_halts_immediately():
    c = ControlState(RunState.RUNNING, Autonomy.AUTO, RiskLevel.HIGH, allow_auto=True)
    assert c.can_place_live() is True
    c.pause()
    assert c.can_place_live() is False        # real-time stop


def test_risk_off_blocks_trading_but_keeps_running():
    c = ControlState(RunState.RUNNING, Autonomy.AUTO, RiskLevel.HIGH, allow_auto=True)
    c.set_risk(RiskLevel.OFF)
    assert c.can_place_live() is False and c.can_simulate() is False


def test_sim_autonomy_simulates_but_never_places_live():
    c = ControlState(RunState.RUNNING, Autonomy.SIM, RiskLevel.LOW)
    assert c.can_simulate() is True
    assert c.can_place_live() is False


# --- staged autonomy added for the bot (S0 shadow, S2 approve, S3 auto lock) ---

def test_auto_is_locked_by_default():
    c = ControlState()
    with pytest.raises(AutoLockedError):
        c.set_autonomy(Autonomy.AUTO)
    with pytest.raises(AutoLockedError):
        ControlState(RunState.RUNNING, Autonomy.AUTO, RiskLevel.LOW)


def test_shadow_logs_but_never_orders():
    c = ControlState(RunState.RUNNING, Autonomy.SHADOW, RiskLevel.HIGH)
    assert c.can_log_proposals() is True
    assert c.can_simulate() is False
    assert c.can_place_live() is False
    assert c.can_place_live(approved=True) is False


def test_approve_mode_needs_per_order_approval():
    c = ControlState(RunState.RUNNING, Autonomy.APPROVE, RiskLevel.MEDIUM)
    assert c.requires_approval() is True
    assert c.can_place_live() is False
    assert c.can_place_live(approved=True) is True
    c.pause()
    assert c.can_place_live(approved=True) is False   # pause beats an approval


def test_approve_mode_respects_risk_off():
    c = ControlState(RunState.RUNNING, Autonomy.APPROVE, RiskLevel.OFF)
    assert c.can_place_live(approved=True) is False


def test_sim_never_places_live_even_if_approved():
    c = ControlState(RunState.RUNNING, Autonomy.SIM, RiskLevel.LOW)
    assert c.can_place_live(approved=True) is False
