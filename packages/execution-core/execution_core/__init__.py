"""execution-core: broker-agnostic execution — one Broker port, many adapters.

SimBroker (working), plus Schwab/Alpaca/Robinhood adapter stubs. A pure,
exhaustively-testable risk gate and an idempotent OMS sit in front of every
broker. No self-learning logic lives here; the engine wires this to the spine.
"""

from .broker import Broker
from .approvals import ApprovalQueue, ApprovalRequest, ApprovalState
from .control import Autonomy, AutoLockedError, ControlState, RiskLevel, RunState
from .domain import (
    Balances,
    BrokerCapabilities,
    Order,
    OrderAck,
    OrderIntent,
    OrderState,
    OrderType,
    Position,
    Quote,
    Side,
    TimeInForce,
)
from .oms import OMS
from .risk import AccountState, GateResult, Limits, check, drawdown_breached
from .sizing import daily_sigma_pct, unusual_move, vol_scaled_qty
from .sim_broker import SimBroker

__all__ = [
    "Broker", "SimBroker", "OMS",
    "OrderIntent", "Order", "OrderAck", "OrderState", "OrderType",
    "Side", "TimeInForce", "Quote", "Position", "Balances", "BrokerCapabilities",
    "check", "Limits", "AccountState", "GateResult",
    "ControlState", "RunState", "Autonomy", "RiskLevel", "AutoLockedError",
    "ApprovalQueue", "ApprovalRequest", "ApprovalState", "drawdown_breached",
    "daily_sigma_pct", "vol_scaled_qty", "unusual_move",
]
__version__ = "0.0.1"
