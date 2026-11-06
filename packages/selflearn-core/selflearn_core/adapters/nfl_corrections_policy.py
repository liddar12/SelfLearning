"""Shared constants and tiny validators for NFL corrections v1."""
from __future__ import annotations

from ..probability_receipts import finite_number

KIND = "nfl.corrections_artifact.v1"
METHOD = "market_bias_shrinkage_v1"
MODEL_VERSION = "corrections_v1"
SCHEMA_VERSION = 1
HARD_LEG_CAP_PP = 0.03
HARD_PARLAY_CAP_PP = 0.03
HARD_PARLAY_CAP_REL = 0.15
HARD_CLAMP = (0.01, 0.99)
DEFAULT_PRIOR_STRENGTH = 50.0
DEFAULT_MIN_GRADED_TOTAL = 200
DEFAULT_MIN_GRADED_PER_MARKET = 50
MAX_LEGS = 500
MAX_PARLAYS = 200
MAX_LEG_IDS_PER_PARLAY = 12
MODES = frozenset(("off", "shadow", "on"))
ELIGIBILITY_RULES = (
    "served_at_before_kickoff",
    "exclude_push_void",
    "exclude_after_trained_through",
    "baseline_prob_as_served_in_(0,1)",
    "binary_outcome_only",
)
REQUEST_KEYS = frozenset({
    "schema_version", "request_id", "season", "week", "baseline_model_version",
    "generated_at", "legs", "parlays",
})
LEG_KEYS = frozenset({"leg_id", "game_id", "market", "selection", "baseline_prob"})
PARLAY_KEYS = frozenset({"parlay_id", "leg_ids", "baseline_prob"})
ARTIFACT_PUBLIC_KEYS = (
    "artifact_id", "model_version", "method", "trained_through", "fitted_at",
    "expires_at", "n_graded", "source",
)


class CorrectionsError(ValueError):
    """Invalid request or artifact shape."""


def corrections_mode(raw):
    if not isinstance(raw, str) or not raw.strip():
        return "off"
    value = raw.strip().casefold()
    return value if value in MODES else "off"


def _prob(value):
    if not finite_number(value):
        raise CorrectionsError("invalid_probability")
    number = float(value)
    if not 0.0 < number < 1.0:
        raise CorrectionsError("invalid_probability")
    return number


def _text(value, *, name, maximum=200):
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise CorrectionsError("invalid_" + name)
    return value


def _cap_pp(value, *, hard):
    if not finite_number(value) or value <= 0 or value > hard:
        raise CorrectionsError("invalid_cap_pp")
    return float(value)
