"""Sports task contract (roadmap S1): the five sports ``task`` values and their
feature / prediction / outcome shapes, plus dependency-free validators.

This layers on :mod:`selflearn_core.contracts`: ``validate_sports_record`` first
runs ``validate_prediction_record`` (the generic record contract) and then the
task-specific checks declared in ``SPORTS_TASKS``. Adapters (S2: NFL2026, S6/S8:
WC2026) produce records that satisfy it; the scorer (S3) trusts it.

Rules that hold for every sports task:

* ``cohort`` is free text (position / week / tier) or null.
* ``model_version`` names the shipped rule (``weekly_split_v2``, ``candidate``,
  ``parlay_v2``, ...) so a rule change is a new version, never an overwrite.
* Market prices are **measured, never an input**: keys such as ``implied_prob``
  or ``closing_line`` are rejected anywhere inside ``features`` and allowed only
  under ``meta`` (``meta.market_prob``, ...).
* ``horizon_s`` >= 1, and when ``meta.kickoff_ts`` is present the record's ``ts``
  must be <= it (no lookahead: the prediction was logged before the event).

The JSON Schemas under ``schemas/sports/`` mirror these shapes for readers in
other languages; a test keeps their required keys equal to the Python shapes.
Nothing here needs a third-party package.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from importlib import resources
from typing import Any, Optional

from .contracts import validate_prediction_record

# --- owner policy: market prices are measured (meta), never an input (features) ---
MARKET_KEYS = (
    "implied_prob",
    "market_prob",
    "closing_line",
    "moneyline",
    "spread_line",
    "kalshi",
    "polymarket",
)

# Tolerance for "probabilities sum to 1" (floating-point round-off).
PROB_SUM_TOL = 1e-6

WC_RESULTS = ("H", "D", "A")

# Type vocabulary used by the shapes below. "number" and "int" exclude bool on
# purpose (bool is an int subclass in Python).
_TYPES: dict[str, tuple[type, ...]] = {
    "number": (int, float),
    "int": (int,),
    "bool": (bool,),
    "string": (str,),
    "list": (list,),
    "object": (dict,),
}


@dataclass(frozen=True)
class TaskSpec:
    """Declared shape of one sports task.

    ``features`` / ``prediction`` / ``outcome`` map required key -> type name
    (see ``_TYPES``). ``prediction_alt`` is an alternative prediction shape
    accepted in place of ``prediction`` (``nfl.game`` accepts a 2-vector).
    Extra keys are allowed in all three blocks (adapters may carry more than the
    minimum); the market-key ban and the value rules are enforced separately.
    """

    task: str
    features: dict[str, str]
    prediction: dict[str, str]
    outcome: dict[str, str]
    horizon: str  # human-readable horizon rule, mirrored in the docs
    prediction_alt: Optional[dict[str, str]] = None
    notes: tuple[str, ...] = field(default_factory=tuple)


SPORTS_TASKS: dict[str, TaskSpec] = {
    "nfl.game": TaskSpec(
        task="nfl.game",
        features={
            "elo_home": "number",
            "elo_away": "number",
            "hfa": "number",
            "rest_home": "number",
            "rest_away": "number",
            "roof": "string",
            "week": "int",
            "families_applied": "list",
        },
        prediction={"home_prob": "number"},
        prediction_alt={"probs": "list"},
        outcome={"home_won": "bool", "margin": "int"},
        horizon="kickoff -> final (~4 h); horizon_s >= 1",
        notes=(
            "home_prob in [0, 1]; alternatively {probs: [home, away]} summing to 1.",
            "margin is home points minus away points.",
        ),
    ),
    "nfl.player_week": TaskSpec(
        task="nfl.player_week",
        features={
            "position": "string",
            "team": "string",
            "opp": "string",
            "week": "int",
            "dvp_factor": "number",
            "weather_factor": "number",
            "venue_factor": "number",
            "availability": "string",
        },
        prediction={"points": "number", "low": "number", "high": "number"},
        outcome={"points_ppr": "number", "played": "bool"},
        horizon="kickoff -> stats final; horizon_s >= 1",
        notes=("points are league-priced PPR; low <= points <= high.",),
    ),
    "nfl.parlay_leg": TaskSpec(
        task="nfl.parlay_leg",
        features={
            "market": "string",
            "line": "number",
            "mu": "number",
            "sd": "number",
            "z": "number",
            "p_team": "number",
            "correlation_tags": "list",
        },
        prediction={"model_prob": "number"},
        outcome={"hit": "bool"},
        horizon="as the leg's game; horizon_s >= 1",
        notes=(
            "model_prob in [0, 1].",
            "An unresolved leg is simply never resolved; it is never recorded as a miss.",
        ),
    ),
    "nfl.player_season": TaskSpec(
        task="nfl.player_season",
        features={
            "prior_ppg": "number",
            "projected_games": "number",
            "candidate_signals": "object",
        },
        prediction={"points": "number", "low": "number", "high": "number"},
        outcome={"points_ppr": "number"},
        horizon="season; horizon_s >= 1",
        notes=("low <= points <= high.",),
    ),
    "wc.match": TaskSpec(
        task="wc.match",
        features={
            "elo_home": "number",
            "elo_away": "number",
            "momentum": "number",
            "lineup_known": "bool",
            "venue": "string",
        },
        prediction={"home": "number", "draw": "number", "away": "number"},
        outcome={"result": "string", "score": "list"},
        horizon="kickoff -> full time; horizon_s >= 1",
        notes=(
            "home + draw + away = 1 (each in [0, 1]).",
            "result is one of H | D | A; score is [home_goals, away_goals] as ints.",
        ),
    ),
}


def schema_filename(task: str) -> str:
    """``nfl.game`` -> ``nfl_game.schema.json`` (file under ``schemas/sports/``)."""
    return task.replace(".", "_") + ".schema.json"


def load_task_schema(task: str) -> dict[str, Any]:
    """Return the parsed JSON Schema shipped for ``task``."""
    if task not in SPORTS_TASKS:
        raise KeyError(f"unknown sports task {task!r}; known: {sorted(SPORTS_TASKS)}")
    text = (
        resources.files(__package__)
        .joinpath("schemas/sports/" + schema_filename(task))
        .read_text(encoding="utf-8")
    )
    return json.loads(text)


# --- helpers ---------------------------------------------------------------


def _is_type(value: Any, type_name: str) -> bool:
    if type_name in ("number", "int") and isinstance(value, bool):
        return False
    if type_name == "number" and isinstance(value, float) and not math.isfinite(value):
        return False
    return isinstance(value, _TYPES[type_name])


def _check_shape(task: str, block_name: str, block: Any, shape: dict[str, str]) -> None:
    """Required keys present and of the declared type; names the offending key."""
    if not isinstance(block, dict):
        raise ValueError(f"{task}: {block_name} must be an object")
    for key, type_name in shape.items():
        if key not in block:
            raise ValueError(f"{task}: {block_name} missing required key '{key}'")
        if not _is_type(block[key], type_name):
            raise ValueError(
                f"{task}: {block_name}.{key} must be a {type_name}, "
                f"got {type(block[key]).__name__}"
            )


def _find_market_key(obj: Any, path: str) -> Optional[str]:
    """Return the dotted path of the first market key found anywhere under ``obj``."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            here = f"{path}.{k}"
            if isinstance(k, str) and k.lower() in MARKET_KEYS:
                return here
            found = _find_market_key(v, here)
            if found:
                return found
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found = _find_market_key(v, f"{path}[{i}]")
            if found:
                return found
    return None


def _check_prob(task: str, where: str, value: Any) -> None:
    if not _is_type(value, "number"):
        raise ValueError(f"{task}: {where} must be a number, got {type(value).__name__}")
    if not 0 <= value <= 1:
        raise ValueError(f"{task}: {where} must be in [0, 1], got {value!r}")


def _check_prob_vector(task: str, where: str, names: list[str], values: list[Any]) -> None:
    for name, v in zip(names, values):
        _check_prob(task, f"{where}{name}", v)
    total = sum(values)
    if abs(total - 1.0) > PROB_SUM_TOL:
        raise ValueError(f"{task}: {where}{'+'.join(names)} must sum to 1, got {total!r}")


def _check_band(task: str, pred: dict[str, Any]) -> None:
    low, points, high = pred["low"], pred["points"], pred["high"]
    if not low <= points <= high:
        raise ValueError(
            f"{task}: prediction must satisfy low <= points <= high, "
            f"got low={low!r} points={points!r} high={high!r}"
        )


# --- prediction-side checks per task ----------------------------------------


def _check_prediction(spec: TaskSpec, pred: Any) -> None:
    task = spec.task
    if not isinstance(pred, dict):
        raise ValueError(f"{task}: prediction must be an object")

    if task == "nfl.game":
        if "home_prob" in pred:
            _check_prob(task, "prediction.home_prob", pred["home_prob"])
        elif "probs" in pred:
            probs = pred["probs"]
            if not isinstance(probs, list) or len(probs) != 2:
                raise ValueError(f"{task}: prediction.probs must be a 2-vector [home, away]")
            _check_prob_vector(task, "prediction.probs.", ["home", "away"], probs)
        else:
            raise ValueError(
                f"{task}: prediction missing required key 'home_prob' (or 'probs')"
            )
        return

    _check_shape(task, "prediction", pred, spec.prediction)

    if task == "nfl.parlay_leg":
        _check_prob(task, "prediction.model_prob", pred["model_prob"])
    elif task in ("nfl.player_week", "nfl.player_season"):
        _check_band(task, pred)
    elif task == "wc.match":
        names = ["home", "draw", "away"]
        _check_prob_vector(task, "prediction.", names, [pred[n] for n in names])


# --- public API --------------------------------------------------------------


def validate_sports_record(rec: Any) -> None:
    """Raise ``ValueError`` unless ``rec`` is a valid record for a sports task.

    Runs the generic ``validate_prediction_record`` first, then: known task,
    feature shape, market-key ban inside ``features``, prediction shape and
    value rules, ``cohort`` free text or null, and the no-lookahead check
    ``ts <= meta.kickoff_ts`` when ``meta.kickoff_ts`` is present.
    """
    validate_prediction_record(rec)
    task = rec["task"]
    spec = SPORTS_TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown sports task {task!r}; known: {sorted(SPORTS_TASKS)}")

    features = rec["features"]
    _check_shape(task, "features", features, spec.features)
    market_key = _find_market_key(features, "features")
    if market_key:
        raise ValueError(
            f"{task}: {market_key} is a market price and is not allowed in features "
            f"(measured, never an input); put it under meta instead"
        )

    _check_prediction(spec, rec["prediction"])

    cohort = rec.get("cohort")
    if cohort is not None and not isinstance(cohort, str):
        raise ValueError(f"{task}: cohort must be a string or null")

    meta = rec.get("meta")
    if meta is not None:
        if not isinstance(meta, dict):
            raise ValueError(f"{task}: meta must be an object")
        kickoff = meta.get("kickoff_ts")
        if kickoff is not None:
            if not _is_type(kickoff, "int"):
                raise ValueError(f"{task}: meta.kickoff_ts must be an integer (unix seconds)")
            if rec["ts"] > kickoff:
                raise ValueError(
                    f"{task}: ts ({rec['ts']}) is after meta.kickoff_ts ({kickoff}); "
                    f"predictions must be logged at or before kickoff (no lookahead)"
                )


def validate_sports_outcome(task: str, outcome: Any) -> None:
    """Raise ``ValueError`` unless ``outcome`` is a valid realized payload for ``task``.

    ``outcome`` is what goes into ``Outcome.realized``. An unresolved event has
    no outcome at all (the record simply stays open); never encode "unresolved"
    as a value here.
    """
    spec = SPORTS_TASKS.get(task)
    if spec is None:
        raise ValueError(f"unknown sports task {task!r}; known: {sorted(SPORTS_TASKS)}")
    _check_shape(task, "outcome", outcome, spec.outcome)

    if task == "wc.match":
        if outcome["result"] not in WC_RESULTS:
            raise ValueError(f"{task}: outcome.result must be one of {WC_RESULTS}")
        score = outcome["score"]
        if len(score) != 2 or not all(_is_type(g, "int") and g >= 0 for g in score):
            raise ValueError(
                f"{task}: outcome.score must be [home_goals, away_goals] as non-negative ints"
            )
        home_goals, away_goals = score
        expected = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
        if outcome["result"] != expected:
            raise ValueError(
                f"{task}: outcome.result {outcome['result']!r} disagrees with "
                f"outcome.score {score!r} (expected {expected!r})"
            )
    elif task == "nfl.game":
        margin, home_won = outcome["margin"], outcome["home_won"]
        if home_won and margin <= 0:
            raise ValueError(f"{task}: outcome.home_won is true but margin is {margin}")
        if not home_won and margin > 0:
            raise ValueError(f"{task}: outcome.home_won is false but margin is {margin}")
