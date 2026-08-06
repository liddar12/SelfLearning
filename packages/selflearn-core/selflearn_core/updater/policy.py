"""Feedback policies with staged autonomy. The updater turns scores into
proposed changes; it never applies them without a manual gate, and it stays at
Level 0 for a task until that task has ``min_resolved`` resolved outcomes
(default 30, guardrail Section 6).

Levels:
  L0 monitor only (no-op)         <- start here, always safe
  L1 calibration layer            <- default target
  L2 weight/ensemble adjustment
  L3 prompt/param optimization
  L4 trained meta-model

Bodies for L1..L4 unlock across Gates 3+. L0 is implemented now: it is the
no-op, and being able to run L0 is the point of "monitor only".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Iterable

from ..types import Score

DEFAULT_MIN_RESOLVED = 30  # TODO(jimmy): confirm threshold


@dataclass(frozen=True)
class Change:
    """A proposed change. Never auto-applied; requires a manual gate."""

    task: str
    kind: str  # 'calibrate' | 'reweight' | 'param' | 'meta'
    detail: dict[str, Any]
    rationale: str


class UpdatePolicy(ABC):
    level: int

    @abstractmethod
    def propose(self, scores: Iterable[Score]) -> list[Change]:
        """Inspect scores; return proposed changes (possibly empty)."""


class Level0Monitor(UpdatePolicy):
    """Log and score, change nothing. Fully implemented: the no-op is the job."""

    level = 0

    def propose(self, scores: Iterable[Score]) -> list[Change]:
        return []


class Level1Calibration(UpdatePolicy):
    """Propose fitting a confidence calibrator for tasks whose sample is real.

    Gate 3: implemented. Rule: for each (task, cohort=None pooled, window='all')
    hit_rate score with n >= min_resolved, propose a 'calibrate' change; if a
    brier score is present its value goes in the rationale. Proposes only —
    applying the calibrator stays behind the manual gate.
    """

    level = 1

    def __init__(self, min_resolved: int = DEFAULT_MIN_RESOLVED, method: str = "isotonic") -> None:
        self.min_resolved = min_resolved
        self.method = method

    def propose(self, scores: Iterable[Score]) -> list[Change]:
        scores = list(scores)
        briers = {
            s.task: s.value
            for s in scores
            if s.metric == "brier" and s.cohort is None and s.window == "all"
        }
        changes: list[Change] = []
        for s in scores:
            if (
                s.metric == "hit_rate"
                and s.cohort is None
                and s.window == "all"
                and s.n >= self.min_resolved
            ):
                brier = briers.get(s.task)
                rationale = (
                    f"{s.task}: n={s.n} resolved (>= {self.min_resolved}), "
                    f"hit_rate={s.value:.3f} [{s.ci_low:.3f}, {s.ci_high:.3f}]"
                )
                if brier is not None:
                    rationale += f", brier={brier:.3f}"
                changes.append(
                    Change(
                        task=s.task,
                        kind="calibrate",
                        detail={"method": self.method, "n": s.n},
                        rationale=rationale,
                    )
                )
        return changes


class Level2Ensemble(UpdatePolicy):
    level = 2

    def propose(self, scores: Iterable[Score]) -> list[Change]:
        raise NotImplementedError("Later gate: ensemble reweighting.")


class Level3ParamOpt(UpdatePolicy):
    level = 3

    def propose(self, scores: Iterable[Score]) -> list[Change]:
        raise NotImplementedError("Later gate: prompt/param optimization.")


class Level4MetaModel(UpdatePolicy):
    level = 4

    def propose(self, scores: Iterable[Score]) -> list[Change]:
        raise NotImplementedError("Later gate: trained meta-model.")
