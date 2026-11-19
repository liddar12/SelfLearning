"""Shared constants and validators for NFL corrections."""
from __future__ import annotations

import re

from ..probability_receipts import finite_number

KIND = "nfl.corrections_artifact.v1"
METHOD = "market_bias_shrinkage_v1"
MODEL_VERSION = "corrections_v1"
SCHEMA_VERSION = 1
LINEAGE_VERSION = 2
FIRST_DURABLE_PUBLICATION = "first_durable_publication_v2"
LINEAGE_KIND = "nfl.corrections_artifact.v2"
LINEAGE_MODEL_VERSION = "corrections_v2"
LINEAGE_POLICY = "exact_frozen_base_calibrator_v1"
LINEAGE_KEYS = ("base_model_version", "calibrator_version")
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
    "segment_market_and_selection",
)
VOID_STATUSES = frozenset({"push", "void"})
# Only journal-bound as-of fits may set applied=true. Caller lists / digests alone
# are never runtime trust. Publication must be independently attested without
# changing the original capture receipt. Enum-only v1 artifacts are untrusted.
TRUSTED_ARTIFACT_SOURCE = "receipt_journal_publication_as_of_v2"
LINEAGE_ARTIFACT_SOURCE = "receipt_journal_publication_as_of_lineage_v1"
UNTRUSTED_ARTIFACT_SOURCE = "graded_caller_list_untrusted"
TRUSTED_ARTIFACT_SOURCES = frozenset({TRUSTED_ARTIFACT_SOURCE, LINEAGE_ARTIFACT_SOURCE})
REQUEST_KEYS = frozenset({
    "schema_version", "request_id", "season", "week", "baseline_model_version",
    "generated_at", "legs", "parlays",
})
LEG_KEYS = frozenset({"leg_id", "game_id", "market", "selection", "baseline_prob"})
PARLAY_KEYS = frozenset({"parlay_id", "leg_ids", "baseline_prob"})
BASELINE_GENERATION_KEYS = frozenset({"workflow", "run_id", "run_attempt", "intent_id",
                                      "generation_object_id", "generation_id"})
# Consumer-visible provenance (breaking additive expansion vs first draft response).
ARTIFACT_PUBLIC_KEYS = (
    "artifact_id", "model_version", "method", "trained_through", "fitted_at",
    "expires_at", "n_graded", "source", "eligibility_rules", "fit_inputs_hash",
    "prior_strength", "min_graded_total", "min_graded_per_market", "leg_cap_pp",
    "parlay_cap_pp", "parlay_cap_rel", "clamp", "segments", "sufficient_total",
)


def artifact_source_trusted(source):
    return isinstance(source, str) and source in TRUSTED_ARTIFACT_SOURCES


def is_digest(value):
    return isinstance(value, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is not None


def lineage_pair(value):
    pair = tuple(value.get(key) for key in LINEAGE_KEYS)
    if not all(is_digest(item) for item in pair):
        raise CorrectionsError("missing_baseline_lineage")
    return pair


class CorrectionsError(ValueError):
    """Invalid request or artifact shape."""


def corrections_mode(raw):
    if not isinstance(raw, str) or not raw.strip():
        return "off"
    value = raw.strip().casefold()
    return value if value in MODES else "off"


def is_void_status(status):
    return isinstance(status, str) and status.casefold() in VOID_STATUSES


def segment_key(market, selection):
    return _text(market, name="market", maximum=100) + "|" + _text(
        selection, name="selection", maximum=100)


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
