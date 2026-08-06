"""Gate 3 tests: general scorer, pure-Python calibration, SQLite registry,
and the L1 calibration policy."""

import pytest

from selflearn_core.registry import SqliteRegistry
from selflearn_core.scoring import TaskSpec, fit_calibrator, score_task
from selflearn_core.scoring.calibration import IsotonicCalibrator, PlattCalibrator
from selflearn_core.types import Outcome, Prediction, ResolvedPrediction, Score
from selflearn_core.updater.policy import Change, Level1Calibration

DAY = 86_400
T0 = 1_700_000_000


def rp(i, task="scanner", cohort=None, confidence=None, signed_return=0.01,
       resolved_offset=0, prediction=None, realized=None):
    p = Prediction(
        id=f"p{i}", ts=T0 + i, model_version="m1", task=task,
        features={}, prediction=prediction if prediction is not None else {"side": "call"},
        horizon_s=DAY, confidence=confidence, cohort=cohort,
    )
    o = Outcome(
        prediction_id=p.id, resolved_ts=T0 + i + DAY + resolved_offset,
        realized=realized if realized is not None else {"signed_return": signed_return},
    )
    return ResolvedPrediction(prediction=p, outcome=o)


# ---------------------------------------------------------------- scorer


def test_idea_hit_rate_with_ci_and_cohorts():
    rs = [rp(i, cohort="NVDA", signed_return=0.02) for i in range(6)] + [
        rp(10 + i, cohort="XOM", signed_return=-0.01) for i in range(4)
    ]
    scores = score_task(rs, TaskSpec(task="scanner", kind="idea", windows=("all",)))
    by = {(s.cohort, s.metric): s for s in scores}
    pooled = by[(None, "hit_rate")]
    assert pooled.n == 10 and pooled.value == pytest.approx(0.6)
    assert pooled.ci_low < 0.6 < pooled.ci_high  # never a bare point estimate
    assert by[("NVDA", "hit_rate")].value == 1.0
    assert by[("XOM", "hit_rate")].value == 0.0


def test_idea_brier_uses_confidence_only():
    rs = [rp(0, confidence=0.9, signed_return=0.02),   # hit, brier .01
          rp(1, confidence=0.9, signed_return=-0.02),  # miss, brier .81
          rp(2, confidence=None, signed_return=0.02)]  # excluded from brier
    scores = score_task(rs, TaskSpec(task="scanner", kind="idea", windows=("all",)))
    brier = next(s for s in scores if s.metric == "brier" and s.cohort is None)
    assert brier.n == 2
    assert brier.value == pytest.approx((0.01 + 0.81) / 2)


def test_rolling_window_anchored_to_latest_resolution():
    old = rp(0, signed_return=0.01)                       # resolves at T0+1d
    new = rp(0, signed_return=0.01, resolved_offset=90 * DAY)  # resolves ~91d later
    scores = score_task([old, new], TaskSpec(task="scanner", kind="idea", windows=("rolling_30d",)))
    hit = next(s for s in scores if s.metric == "hit_rate" and s.cohort is None)
    assert hit.n == 1  # the old one fell out of the 30d window


def test_regression_metrics():
    rs = [
        rp(i, task="power_h1", prediction=p, realized={"value": a})
        for i, (p, a) in enumerate([(10.0, 12.0), (20.0, 18.0), (-5.0, -4.0), (7.0, 7.0)])
    ]
    scores = score_task(rs, TaskSpec(task="power_h1", kind="regression", windows=("all",)))
    by = {s.metric: s for s in scores if s.cohort is None}
    assert by["mae"].value == pytest.approx((2 + 2 + 1 + 0) / 4)
    assert by["sign_accuracy"].value == 1.0
    assert 0 < by["r2"].value <= 1
    assert by["mae"].n == 4


def test_unrelated_tasks_are_ignored():
    rs = [rp(0, task="other")]
    assert score_task(rs, TaskSpec(task="scanner", kind="idea")) == []


# ---------------------------------------------------------------- calibration


def _resolved_for_calibration(n_lo=10, n_hi=10):
    # low-confidence ideas mostly miss, high-confidence mostly hit
    rs = []
    for i in range(n_lo):
        rs.append(rp(i, confidence=0.2, signed_return=0.01 if i % 5 == 0 else -0.01))
    for i in range(n_hi):
        rs.append(rp(100 + i, confidence=0.8, signed_return=0.01 if i % 5 != 0 else -0.01))
    return rs


def test_isotonic_is_monotone_and_ordered():
    cal = fit_calibrator(_resolved_for_calibration(), method="isotonic")
    assert isinstance(cal, IsotonicCalibrator)
    lo, hi = cal.apply(0.2), cal.apply(0.8)
    assert lo <= hi
    assert lo == pytest.approx(0.2, abs=0.15)   # ~20% of low-conf hit
    assert hi == pytest.approx(0.8, abs=0.15)   # ~80% of high-conf hit
    # monotone across the whole range
    xs = [i / 20 for i in range(21)]
    ys = [cal.apply(x) for x in xs]
    assert all(a <= b + 1e-9 for a, b in zip(ys, ys[1:]))


def test_platt_learns_direction():
    cal = fit_calibrator(_resolved_for_calibration(), method="platt")
    assert isinstance(cal, PlattCalibrator)
    assert cal.apply(0.8) > cal.apply(0.2)
    assert 0.0 <= cal.apply(0.0) <= 1.0 and 0.0 <= cal.apply(1.0) <= 1.0


def test_calibration_refuses_tiny_samples():
    with pytest.raises(ValueError):
        fit_calibrator([rp(0, confidence=0.5)], method="isotonic")


def test_calibration_unknown_method():
    with pytest.raises(ValueError):
        fit_calibrator(_resolved_for_calibration(), method="magic")


# ---------------------------------------------------------------- registry


def test_registry_lifecycle(tmp_path):
    from selflearn_core.store import SqliteStore

    db = str(tmp_path / "reg.sqlite")
    SqliteStore(db).init_schema()  # creates the registry table
    reg = SqliteRegistry(db, now=lambda: T0)

    v1 = reg.register("scanner", {"prompt": "a"})
    assert reg.get(v1) == {"prompt": "a"}
    assert reg.status(v1) == "candidate"
    assert reg.live_scores("scanner") == []

    reg.promote(v1)
    assert reg.status(v1) == "live"

    scores = [Score(task="scanner", metric="hit_rate", value=0.6, n=30, window="all")]
    reg.record_scores(v1, scores)
    live = reg.live_scores("scanner")
    assert len(live) == 1 and live[0].value == 0.6 and live[0].n == 30

    # promote a second config; v1 retires; rollback revives v1
    reg2 = SqliteRegistry(db, now=lambda: T0 + 10)
    v2 = reg2.register("scanner", {"prompt": "b"})
    reg2.promote(v2)
    assert reg2.status(v1) == "retired" and reg2.status(v2) == "live"
    reg2.rollback("scanner")
    assert reg2.status(v1) == "live" and reg2.status(v2) == "retired"


def test_registry_rollback_without_history_raises(tmp_path):
    from selflearn_core.store import SqliteStore

    db = str(tmp_path / "reg.sqlite")
    SqliteStore(db).init_schema()
    reg = SqliteRegistry(db)
    with pytest.raises(LookupError):
        reg.rollback("scanner")


# ---------------------------------------------------------------- L1 policy


def _score(task="scanner", metric="hit_rate", n=30, cohort=None, window="all", value=0.6):
    return Score(task=task, metric=metric, value=value, n=n, window=window,
                 cohort=cohort, ci_low=0.4, ci_high=0.8)


def test_l1_proposes_only_above_min_sample():
    pol = Level1Calibration(min_resolved=30)
    assert pol.propose([_score(n=29)]) == []
    changes = pol.propose([_score(n=30), _score(metric="brier", value=0.31)])
    assert len(changes) == 1
    c = changes[0]
    assert isinstance(c, Change) and c.kind == "calibrate" and c.task == "scanner"
    assert "brier=0.310" in c.rationale


def test_l1_ignores_cohort_and_window_slices():
    pol = Level1Calibration(min_resolved=30)
    assert pol.propose([_score(cohort="NVDA")]) == []
    assert pol.propose([_score(window="rolling_30d")]) == []
