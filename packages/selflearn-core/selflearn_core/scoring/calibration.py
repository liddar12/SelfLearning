"""Confidence calibration. The mechanism behind the L1 updater: make 'HIGH'
mean what the data says it means.

Gate 3: implemented in pure Python — isotonic regression via the pool-adjacent-
violators algorithm (PAVA) and Platt scaling via Newton iterations on the 1-D
logistic likelihood — so the package keeps its zero-third-party-dependency
promise and calibration runs everywhere the scaffold imports. (scikit-learn
remains an optional ``scoring`` extra for later, heavier models; it is not
needed here.)

Only resolved predictions that carry a confidence AND a determinable hit
(``realized['hit']`` or the sign of ``realized['signed_return']``) participate.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from bisect import bisect_left
from math import exp
from typing import Iterable

from ..types import ResolvedPrediction
from .metrics import _is_hit

MIN_SAMPLES = 5  # below this, fitting a calibrator is noise, not learning


class Calibrator(ABC):
    @abstractmethod
    def apply(self, confidence: float) -> float:
        """Map a raw confidence to a calibrated probability."""


class IsotonicCalibrator(Calibrator):
    """Stepwise-constant monotone map fit by PAVA."""

    def __init__(self, thresholds: list[float], values: list[float]) -> None:
        # thresholds[i] is the raw-confidence upper bound (inclusive) of step i.
        self._thresholds = thresholds
        self._values = values

    def apply(self, confidence: float) -> float:
        i = min(bisect_left(self._thresholds, confidence), len(self._values) - 1)
        return min(1.0, max(0.0, self._values[i]))


class PlattCalibrator(Calibrator):
    """Logistic map sigma(a*conf + b)."""

    def __init__(self, a: float, b: float) -> None:
        self.a, self.b = a, b

    def apply(self, confidence: float) -> float:
        z = self.a * confidence + self.b
        z = max(-35.0, min(35.0, z))
        return 1.0 / (1.0 + exp(-z))


def _pairs(resolved: Iterable[ResolvedPrediction]) -> list[tuple[float, float]]:
    """(confidence, hit as 0/1), sorted by confidence."""
    pts = []
    for r in resolved:
        h = _is_hit(r)
        if h is not None and r.prediction.confidence is not None:
            pts.append((float(r.prediction.confidence), 1.0 if h else 0.0))
    pts.sort(key=lambda t: t[0])
    return pts


def _fit_isotonic(pts: list[tuple[float, float]]) -> IsotonicCalibrator:
    # Pool tied confidences first (the fit must be single-valued at each x),
    # then PAVA: merge adjacent blocks until block means are non-decreasing.
    blocks: list[list[float]] = []  # [max_x, sum_y, count]
    for x, y in pts:
        if blocks and blocks[-1][0] == x:
            blocks[-1][1] += y
            blocks[-1][2] += 1.0
        else:
            blocks.append([x, y, 1.0])
    merged: list[list[float]] = []
    for b in blocks:
        merged.append(b)
        while len(merged) > 1 and merged[-2][1] / merged[-2][2] >= merged[-1][1] / merged[-1][2]:
            last = merged.pop()
            merged[-1][0] = last[0]
            merged[-1][1] += last[1]
            merged[-1][2] += last[2]
    thresholds = [b[0] for b in merged]
    values = [b[1] / b[2] for b in merged]
    return IsotonicCalibrator(thresholds, values)


def _fit_platt(pts: list[tuple[float, float]], iters: int = 50) -> PlattCalibrator:
    # Newton's method on the 2-parameter logistic negative log-likelihood.
    a, b = 1.0, 0.0
    for _ in range(iters):
        g_a = g_b = h_aa = h_ab = h_bb = 0.0
        for x, y in pts:
            z = max(-35.0, min(35.0, a * x + b))
            p = 1.0 / (1.0 + exp(-z))
            w = p * (1 - p)
            g_a += (p - y) * x
            g_b += p - y
            h_aa += w * x * x
            h_ab += w * x
            h_bb += w
        h_aa += 1e-6
        h_bb += 1e-6
        det = h_aa * h_bb - h_ab * h_ab
        if abs(det) < 1e-12:
            break
        da = (h_bb * g_a - h_ab * g_b) / det
        db = (h_aa * g_b - h_ab * g_a) / det
        a, b = a - da, b - db
        if abs(da) < 1e-9 and abs(db) < 1e-9:
            break
    return PlattCalibrator(a, b)


def fit_calibrator(resolved: Iterable[ResolvedPrediction], method: str = "isotonic") -> Calibrator:
    """Fit Platt (logistic) or isotonic calibration on resolved predictions.

    Raises ``ValueError`` when fewer than ``MIN_SAMPLES`` usable points exist
    (small-sample honesty: refuse rather than fit noise) or on an unknown
    method.
    """
    pts = _pairs(resolved)
    if len(pts) < MIN_SAMPLES:
        raise ValueError(
            f"need >= {MIN_SAMPLES} resolved predictions with confidence + hit, got {len(pts)}"
        )
    if method == "isotonic":
        return _fit_isotonic(pts)
    if method == "platt":
        return _fit_platt(pts)
    raise ValueError(f"unknown method {method!r}; use 'isotonic' or 'platt'")
