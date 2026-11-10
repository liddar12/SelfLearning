"""Live control the engine reads every cycle and the terminal writes in real time.

Levers, adjustable at any moment:

- PAUSE / RESUME (your kill switch),
- AUTONOMY: SHADOW (log proposals only) / SIM (paper fills) / APPROVE (live, every
  order waits for a human approval) / AUTO (live, no approval in the path),
- RISK LEVEL (Off / Low / Medium / High).

Defaults are the safe corner: PAUSED + SIM + OFF. AUTO is locked unless the
ControlState is built with ``allow_auto=True``; that flag is the stage-S3
promotion and is set by a person, never by the learning loop. A change takes
effect on the engine's next cycle. Thread-safe: the engine loop reads while the
control API writes. The hard risk gate and auto-halts run regardless of this
state; control can only make the system more conservative, never bypass a cap.
"""

from __future__ import annotations

from enum import Enum
from threading import RLock


class RunState(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"


class Autonomy(str, Enum):
    SHADOW = "shadow"    # log proposals only; no orders, not even simulated (stage S0)
    SIM = "sim"          # route to SimBroker / paper only (stage S1)
    APPROVE = "approve"  # live, but every order waits for a human approval (stage S2)
    AUTO = "auto"        # live, no human approval in the path (stage S3, locked by default)


class RiskLevel(str, Enum):
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class AutoLockedError(PermissionError):
    """Raised when AUTO is requested on a ControlState that was not unlocked."""


class ControlState:
    def __init__(
        self,
        run: RunState = RunState.PAUSED,
        autonomy: Autonomy = Autonomy.SIM,
        risk: RiskLevel = RiskLevel.OFF,
        allow_auto: bool = False,
    ) -> None:
        if autonomy == Autonomy.AUTO and not allow_auto:
            raise AutoLockedError("AUTO autonomy is locked; construct with allow_auto=True")
        self._lock = RLock()
        self._run = run
        self._auto = autonomy
        self._risk = risk
        self._allow_auto = allow_auto

    # --- real-time levers (terminal writes) ---
    def pause(self) -> None:
        with self._lock:
            self._run = RunState.PAUSED

    def resume(self) -> None:
        with self._lock:
            self._run = RunState.RUNNING

    def set_risk(self, level: RiskLevel) -> None:
        with self._lock:
            self._risk = level

    def set_autonomy(self, a: Autonomy) -> None:
        with self._lock:
            if a == Autonomy.AUTO and not self._allow_auto:
                raise AutoLockedError("AUTO autonomy is locked; promotion to S3 is manual")
            self._auto = a

    # --- engine reads each cycle ---
    def snapshot(self) -> tuple[RunState, Autonomy, RiskLevel]:
        with self._lock:
            return (self._run, self._auto, self._risk)

    def _active(self) -> bool:
        return self._run == RunState.RUNNING and self._risk != RiskLevel.OFF

    def can_log_proposals(self) -> bool:
        """The pipeline may run and log proposals (every mode, when running)."""
        with self._lock:
            return self._run == RunState.RUNNING

    def can_simulate(self) -> bool:
        """Simulated (paper) order allowed? SIM only, running, risk on."""
        with self._lock:
            return self._active() and self._auto == Autonomy.SIM

    def requires_approval(self) -> bool:
        """Live orders must be approved one by one by a person."""
        with self._lock:
            return self._auto == Autonomy.APPROVE

    def can_place_live(self, approved: bool = False) -> bool:
        """May a live order go to the broker now?

        AUTO: yes when running with risk on. APPROVE: only with ``approved=True``
        for this specific order. SHADOW and SIM: never.
        """
        with self._lock:
            if not self._active():
                return False
            if self._auto == Autonomy.AUTO:
                return True
            if self._auto == Autonomy.APPROVE:
                return approved
            return False
