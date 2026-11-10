"""Feature layer: how the wide signal universe becomes point-in-time inputs.

The spine never assumes a causal link (e.g. "war moves gold"). It logs a
snapshot of everything knowable at prediction time and lets the scorer measure,
per cohort, whether predictions conditioned on those features actually beat a
baseline. Alignment is *earned* from resolved outcomes, not declared.

This module is implemented at Gate 1: the taxonomy, the snapshot record, the
no-lookahead validator, and the assembler are real and tested. The concrete
``FeaturePack`` bodies that fetch data live in ``families.py`` and raise
``NotImplementedError`` naming the gate that unlocks them (no silent stubs).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FeatureFamily(str, Enum):
    """The signal families Jimmy named, grouped by what produces them.

    A family is only a namespace + a data-source contract. Whether any family
    carries edge for a given task/cohort is an empirical question the scorer
    answers; nothing here presumes it does.
    """

    # Candles (OHLCV), volume, trend and other price-derived technicals.
    PRICE_ACTION = "price_action"
    # Rates, monetary/fiscal policy, regulation, elections, tariffs.
    MACRO_POLICY = "macro_policy"
    # Wars, regional conflict, sanctions, shipping-lane / chokepoint risk.
    GEOPOLITICS = "geopolitics"
    # Finite goods, precious metals, energy and other commodity balances.
    COMMODITIES = "commodities"
    # AI buildout, data centers, power, water, chips (the Power 2026 surface).
    AI_INFRA = "ai_infra"
    # Market-wide regime: breadth (RSP/SPY), volatility (VIX expected move).
    REGIME = "regime"


@dataclass(frozen=True)
class FeatureSnapshot:
    """A point-in-time reading from one feature pack.

    ``values`` are the features; ``obs_ts`` records, per feature, the unix second
    of the underlying observation. No-lookahead invariant: every ``obs_ts[k]``
    must be <= ``as_of_ts``. Validated by :func:`validate_no_lookahead`.
    """

    pack: str
    family: FeatureFamily
    as_of_ts: int  # the prediction time the caller asked for
    values: dict[str, Any]
    obs_ts: dict[str, int] = field(default_factory=dict)


def validate_no_lookahead(snap: FeatureSnapshot) -> None:
    """Raise ``ValueError`` if any feature observation post-dates ``as_of_ts``.

    This is the structural guard behind guardrail Section 6 ("no lookahead"):
    a macro feature stamped after the prediction moment would leak the future
    into training. Every feature must carry an ``obs_ts`` so this is checkable.
    """
    missing = set(snap.values) - set(snap.obs_ts)
    if missing:
        raise ValueError(
            f"{snap.pack}: features without an observation timestamp: {sorted(missing)}"
        )
    late = {k: t for k, t in snap.obs_ts.items() if t > snap.as_of_ts}
    if late:
        raise ValueError(
            f"{snap.pack}: lookahead — obs after as_of_ts={snap.as_of_ts}: {late}"
        )


def assemble_features(snaps: list[FeatureSnapshot], as_of_ts: int) -> dict[str, Any]:
    """Merge snapshots into one namespaced ``Prediction.features`` dict.

    Keys are namespaced ``{family}.{pack}.{feature}`` so families never collide
    and the scorer can cohort/ablate by family. Validates every snapshot for
    lookahead and confirms each was taken for the same ``as_of_ts`` as the
    prediction it will attach to.
    """
    out: dict[str, Any] = {}
    for snap in snaps:
        if snap.as_of_ts != as_of_ts:
            raise ValueError(
                f"{snap.pack}: snapshot as_of_ts={snap.as_of_ts} != prediction {as_of_ts}"
            )
        validate_no_lookahead(snap)
        for k, v in snap.values.items():
            out[f"{snap.family.value}.{snap.pack}.{k}"] = v
    return out


class FeaturePack(ABC):
    """A source of point-in-time features for one family.

    Implementations fetch only data with an observation time <= ``as_of_ts`` and
    return a :class:`FeatureSnapshot`. Bodies unlock at their gate; the contract
    is fixed now so adapters and the assembler can build against it.
    """

    family: FeatureFamily
    name: str

    @abstractmethod
    def snapshot(self, symbol: str, as_of_ts: int) -> FeatureSnapshot:
        ...
