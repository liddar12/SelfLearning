"""Scoring. Metrics on resolved predictions only, always by cohort + window,
always with sample size and a confidence interval.

Gate 3: implemented. Pure Python (no third-party deps), matching the package's
zero-dependency promise.

Conventions on the realized payload (set by the resolver / adapters):
  kind='idea'       -> ``outcome.realized['signed_return']`` (float). A hit is
                       ``signed_return > 0`` unless ``realized['hit']`` is set
                       explicitly. Brier uses ``prediction.confidence`` where
                       present.
  kind='regression' -> ``outcome.realized['value']`` (float, the realized
                       quantity) against the numeric ``prediction.prediction``
                       (or ``prediction.prediction['value']``).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

from ..types import ResolvedPrediction, Score

_ROLLING_WINDOWS = {"rolling_30d": 30 * 86_400, "rolling_90d": 90 * 86_400}


@dataclass(frozen=True)
class TaskSpec:
    """Describes how a task is scored.

    kind='regression' -> MAE, MAPE, R^2, sign accuracy.
    kind='idea'       -> hit rate, realized PnL, Brier score on confidence.
    """

    task: str
    kind: str  # 'regression' | 'idea'
    windows: tuple[str, ...] = ("rolling_30d", "all")


def wilson_ci(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a proportion k/n (honest in small samples)."""
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def mean_ci(values: list[float], z: float = 1.96) -> tuple[float, float, float]:
    """Mean with a normal-approximation CI. Returns (mean, lo, hi)."""
    n = len(values)
    if n == 0:
        return (0.0, 0.0, 0.0)
    m = sum(values) / n
    if n == 1:
        return (m, m, m)
    var = sum((v - m) ** 2 for v in values) / (n - 1)
    se = math.sqrt(var / n)
    return (m, m - z * se, m + z * se)


def _predicted_value(r: ResolvedPrediction) -> Optional[float]:
    p = r.prediction.prediction
    if isinstance(p, (int, float)) and not isinstance(p, bool):
        return float(p)
    if isinstance(p, dict) and isinstance(p.get("value"), (int, float)):
        return float(p["value"])
    return None


def _is_hit(r: ResolvedPrediction) -> Optional[bool]:
    realized = r.outcome.realized
    if isinstance(realized, dict):
        if isinstance(realized.get("hit"), bool):
            return realized["hit"]
        sr = realized.get("signed_return")
        if isinstance(sr, (int, float)) and not isinstance(sr, bool):
            return sr > 0
    return None


def _in_window(r: ResolvedPrediction, window: str, now_ts: int) -> bool:
    span = _ROLLING_WINDOWS.get(window)
    if span is None:  # 'all' (and any unknown label falls back to everything)
        return True
    return r.outcome.resolved_ts >= now_ts - span


def _score_ideas(
    rs: list[ResolvedPrediction], spec: TaskSpec, window: str, cohort: Optional[str]
) -> list[Score]:
    hits = [h for h in (_is_hit(r) for r in rs) if h is not None]
    out: list[Score] = []
    common = dict(task=spec.task, window=window, cohort=cohort)
    if hits:
        k, n = sum(hits), len(hits)
        lo, hi = wilson_ci(k, n)
        out.append(Score(metric="hit_rate", value=k / n, n=n, ci_low=lo, ci_high=hi, **common))
    pnl = [
        r.outcome.realized["signed_return"]
        for r in rs
        if isinstance(r.outcome.realized, dict)
        and isinstance(r.outcome.realized.get("signed_return"), (int, float))
        and not isinstance(r.outcome.realized.get("signed_return"), bool)
    ]
    if pnl:
        m, lo, hi = mean_ci(pnl)
        out.append(Score(metric="mean_signed_return", value=m, n=len(pnl), ci_low=lo, ci_high=hi, **common))
    briers = [
        (r.prediction.confidence - (1.0 if h else 0.0)) ** 2
        for r, h in ((r, _is_hit(r)) for r in rs)
        if h is not None and r.prediction.confidence is not None
    ]
    if briers:
        m, lo, hi = mean_ci(briers)
        out.append(Score(metric="brier", value=m, n=len(briers), ci_low=lo, ci_high=hi, **common))
    return out


def _score_regression(
    rs: list[ResolvedPrediction], spec: TaskSpec, window: str, cohort: Optional[str]
) -> list[Score]:
    pairs = []
    for r in rs:
        pred = _predicted_value(r)
        realized = r.outcome.realized
        actual = realized.get("value") if isinstance(realized, dict) else None
        if pred is not None and isinstance(actual, (int, float)) and not isinstance(actual, bool):
            pairs.append((pred, float(actual)))
    if not pairs:
        return []
    n = len(pairs)
    common = dict(task=spec.task, window=window, cohort=cohort)
    out: list[Score] = []

    abs_err = [abs(p - a) for p, a in pairs]
    m, lo, hi = mean_ci(abs_err)
    out.append(Score(metric="mae", value=m, n=n, ci_low=lo, ci_high=hi, **common))

    pct = [abs(p - a) / abs(a) for p, a in pairs if a != 0]
    if pct:
        m, lo, hi = mean_ci(pct)
        out.append(Score(metric="mape", value=m, n=len(pct), ci_low=lo, ci_high=hi, **common))

    signs = [1.0 if (p > 0) == (a > 0) else 0.0 for p, a in pairs if p != 0 and a != 0]
    if signs:
        k = int(sum(signs))
        lo, hi = wilson_ci(k, len(signs))
        out.append(
            Score(metric="sign_accuracy", value=k / len(signs), n=len(signs), ci_low=lo, ci_high=hi, **common)
        )

    mean_a = sum(a for _, a in pairs) / n
    ss_tot = sum((a - mean_a) ** 2 for _, a in pairs)
    ss_res = sum((a - p) ** 2 for p, a in pairs)
    if ss_tot > 0:
        out.append(Score(metric="r2", value=1 - ss_res / ss_tot, n=n, **common))
    return out


def score_task(resolved: Iterable[ResolvedPrediction], spec: TaskSpec) -> list[Score]:
    """Compute per-cohort, per-window scores. Every Score carries n + CI.

    Cohorts: each distinct ``prediction.cohort`` is scored separately, plus a
    pooled ``None`` cohort over everything (regime awareness: the pooled view
    exists but never replaces the per-cohort views). Rolling windows are
    anchored to the latest resolved_ts in the data, so scoring a historical
    batch does not depend on wall-clock time.
    """
    rs = [r for r in resolved if r.prediction.task == spec.task]
    if not rs:
        return []
    scorer = _score_ideas if spec.kind == "idea" else _score_regression
    now_ts = max(r.outcome.resolved_ts for r in rs)

    cohorts: dict[Optional[str], list[ResolvedPrediction]] = {None: rs}
    for r in rs:
        if r.prediction.cohort is not None:
            cohorts.setdefault(r.prediction.cohort, []).append(r)

    out: list[Score] = []
    for window in spec.windows:
        for cohort, members in cohorts.items():
            in_win = [r for r in members if _in_window(r, window, now_ts)]
            if in_win:
                out.extend(scorer(in_win, spec, window, cohort))
    return out
