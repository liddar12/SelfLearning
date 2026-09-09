"""Tests for the sports task contract (selflearn_core.contracts_sports, roadmap S1)."""

import copy
import json

import pytest

from selflearn_core import contracts_sports as S

KICKOFF = 1_757_899_800  # 2026-09-15 00:50 UTC, an arbitrary kickoff
TS = KICKOFF - 3600  # logged an hour before kickoff


def _base(task, features, prediction, **extra):
    rec = {
        "id": f"{task}-0001",
        "ts": TS,
        "model_version": "weekly_split_v2",
        "task": task,
        "features": features,
        "prediction": prediction,
        "horizon_s": 4 * 3600,
        "confidence": None,
        "cohort": "wk2",
        "meta": {"kickoff_ts": KICKOFF},
    }
    rec.update(extra)
    return rec


VALID = {
    "nfl.game": _base(
        "nfl.game",
        {
            "elo_home": 1580.2,
            "elo_away": 1495.7,
            "hfa": 48.0,
            "rest_home": 7,
            "rest_away": 10,
            "roof": "outdoors",
            "week": 2,
            "families_applied": ["rest", "qb_change"],
        },
        {"home_prob": 0.61},
        model_version="elo_v3",
        cohort="wk2",
    ),
    "nfl.player_week": _base(
        "nfl.player_week",
        {
            "position": "WR",
            "team": "DET",
            "opp": "GB",
            "week": 2,
            "dvp_factor": 1.06,
            "weather_factor": 1.0,
            "venue_factor": 1.02,
            "availability": "active",
        },
        {"points": 16.4, "low": 9.1, "high": 24.8},
        cohort="WR",
    ),
    "nfl.parlay_leg": _base(
        "nfl.parlay_leg",
        {
            "market": "player_rec_yds",
            "line": 74.5,
            "mu": 81.2,
            "sd": 27.5,
            "z": -0.24,
            "p_team": 0.58,
            "correlation_tags": ["DET_pass", "wr1"],
        },
        {"model_prob": 0.57},
        model_version="parlay_v2",
        cohort="tier1",
    ),
    "nfl.player_season": _base(
        "nfl.player_season",
        {
            "prior_ppg": 17.9,
            "projected_games": 16.5,
            "candidate_signals": {"target_share_delta": 0.03, "age_curve": -0.4},
        },
        {"points": 268.0, "low": 190.0, "high": 330.0},
        model_version="candidate",
        horizon_s=180 * 86400,
        cohort="WR",
        meta={},
    ),
    "wc.match": _base(
        "wc.match",
        {
            "elo_home": 2010.0,
            "elo_away": 1875.0,
            "momentum": 0.15,
            "lineup_known": True,
            "venue": "MetLife Stadium",
        },
        {"home": 0.52, "draw": 0.25, "away": 0.23},
        model_version="wc_elo_v1",
        cohort="group",
    ),
}

VALID_OUTCOMES = {
    "nfl.game": {"home_won": True, "margin": 7},
    "nfl.player_week": {"points_ppr": 18.3, "played": True},
    "nfl.parlay_leg": {"hit": False},
    "nfl.player_season": {"points_ppr": 251.6},
    "wc.match": {"result": "H", "score": [2, 1]},
}

TASKS = sorted(S.SPORTS_TASKS)


def rec(task):
    return copy.deepcopy(VALID[task])


# --- registry ---------------------------------------------------------------


def test_registry_declares_the_five_roadmap_tasks():
    assert TASKS == ["nfl.game", "nfl.parlay_leg", "nfl.player_season", "nfl.player_week", "wc.match"]
    for task, spec in S.SPORTS_TASKS.items():
        assert spec.task == task
        assert spec.features and spec.prediction and spec.outcome and spec.horizon


# --- one valid record per task ----------------------------------------------


@pytest.mark.parametrize("task", TASKS)
def test_valid_record_passes(task):
    S.validate_sports_record(rec(task))


def test_unknown_task_is_rejected():
    r = rec("nfl.game")
    r["task"] = "nba.game"
    with pytest.raises(ValueError, match="unknown sports task 'nba.game'"):
        S.validate_sports_record(r)


def test_generic_contract_runs_first():
    r = rec("nfl.game")
    del r["horizon_s"]
    with pytest.raises(ValueError, match="missing required fields"):
        S.validate_sports_record(r)


# --- each task's missing-required-key error ---------------------------------


@pytest.mark.parametrize(
    "task,key",
    [(t, k) for t in TASKS for k in S.SPORTS_TASKS[t].features],
)
def test_missing_feature_key_names_the_key(task, key):
    r = rec(task)
    del r["features"][key]
    with pytest.raises(ValueError, match=f"features missing required key '{key}'"):
        S.validate_sports_record(r)


@pytest.mark.parametrize(
    "task,key",
    [(t, k) for t in TASKS for k in S.SPORTS_TASKS[t].prediction],
)
def test_missing_prediction_key_names_the_key(task, key):
    r = rec(task)
    del r["prediction"][key]
    with pytest.raises(ValueError, match=f"prediction missing required key '{key}'"):
        S.validate_sports_record(r)


def test_wrong_feature_type_names_the_key():
    r = rec("nfl.game")
    r["features"]["week"] = "two"
    with pytest.raises(ValueError, match="features.week must be a int, got str"):
        S.validate_sports_record(r)


def test_bool_is_not_a_number():
    r = rec("wc.match")
    r["features"]["momentum"] = True
    with pytest.raises(ValueError, match="features.momentum must be a number"):
        S.validate_sports_record(r)


def test_extra_feature_keys_are_allowed():
    r = rec("nfl.player_week")
    r["features"]["snap_share"] = 0.88
    S.validate_sports_record(r)


# --- market rule: rejected in features, accepted in meta --------------------


@pytest.mark.parametrize("key", S.MARKET_KEYS)
def test_market_key_in_features_is_rejected(key):
    r = rec("nfl.game")
    r["features"][key] = 0.55
    with pytest.raises(ValueError, match=f"features.{key} is a market price"):
        S.validate_sports_record(r)


def test_market_key_nested_in_features_is_rejected():
    r = rec("nfl.player_season")
    r["features"]["candidate_signals"]["market_prob"] = 0.5
    with pytest.raises(ValueError, match="features.candidate_signals.market_prob is a market price"):
        S.validate_sports_record(r)


@pytest.mark.parametrize("key", S.MARKET_KEYS)
def test_market_key_in_meta_is_accepted(key):
    r = rec("nfl.game")
    r["meta"][key] = 0.55
    S.validate_sports_record(r)


# --- probability rules --------------------------------------------------------


def test_nfl_game_accepts_two_vector_summing_to_one():
    r = rec("nfl.game")
    r["prediction"] = {"probs": [0.61, 0.39]}
    S.validate_sports_record(r)


def test_nfl_game_two_vector_must_sum_to_one():
    r = rec("nfl.game")
    r["prediction"] = {"probs": [0.61, 0.49]}
    with pytest.raises(ValueError, match="prediction.probs.home\\+away must sum to 1"):
        S.validate_sports_record(r)


def test_nfl_game_two_vector_must_have_two_entries():
    r = rec("nfl.game")
    r["prediction"] = {"probs": [0.5, 0.3, 0.2]}
    with pytest.raises(ValueError, match="2-vector"):
        S.validate_sports_record(r)


def test_nfl_game_prediction_needs_home_prob_or_probs():
    r = rec("nfl.game")
    r["prediction"] = {"away_prob": 0.4}
    with pytest.raises(ValueError, match="missing required key 'home_prob' \\(or 'probs'\\)"):
        S.validate_sports_record(r)


@pytest.mark.parametrize("value", [-0.1, 1.2, "0.5"])
def test_home_prob_must_be_a_unit_probability(value):
    r = rec("nfl.game")
    r["prediction"]["home_prob"] = value
    with pytest.raises(ValueError, match="prediction.home_prob"):
        S.validate_sports_record(r)


def test_parlay_model_prob_range():
    r = rec("nfl.parlay_leg")
    r["prediction"]["model_prob"] = 1.5
    with pytest.raises(ValueError, match="prediction.model_prob must be in \\[0, 1\\]"):
        S.validate_sports_record(r)


def test_wc_match_vector_must_sum_to_one():
    r = rec("wc.match")
    r["prediction"] = {"home": 0.5, "draw": 0.3, "away": 0.3}
    with pytest.raises(ValueError, match="prediction.home\\+draw\\+away must sum to 1"):
        S.validate_sports_record(r)


def test_wc_match_vector_tolerates_float_roundoff():
    r = rec("wc.match")
    r["prediction"] = {"home": 0.1, "draw": 0.2, "away": 0.7}  # 0.1+0.2+0.7 != 1.0 exactly
    S.validate_sports_record(r)


# --- low <= points <= high ---------------------------------------------------


@pytest.mark.parametrize("task", ["nfl.player_week", "nfl.player_season"])
@pytest.mark.parametrize(
    "band", [(10.0, 9.0, 20.0), (10.0, 21.0, 20.0), (25.0, 15.0, 5.0)]
)
def test_band_ordering_is_enforced(task, band):
    low, points, high = band
    r = rec(task)
    r["prediction"] = {"points": points, "low": low, "high": high}
    with pytest.raises(ValueError, match="low <= points <= high"):
        S.validate_sports_record(r)


@pytest.mark.parametrize("task", ["nfl.player_week", "nfl.player_season"])
def test_band_edges_are_inclusive(task):
    r = rec(task)
    r["prediction"] = {"points": 12.0, "low": 12.0, "high": 12.0}
    S.validate_sports_record(r)


# --- no-lookahead: ts <= meta.kickoff_ts ------------------------------------


def test_ts_after_kickoff_is_rejected():
    r = rec("nfl.game")
    r["ts"] = KICKOFF + 1
    with pytest.raises(ValueError, match="is after meta.kickoff_ts .*\\(no lookahead\\)"):
        S.validate_sports_record(r)


def test_ts_at_kickoff_is_allowed():
    r = rec("nfl.game")
    r["ts"] = KICKOFF
    S.validate_sports_record(r)


def test_record_without_kickoff_ts_skips_the_check():
    r = rec("nfl.game")
    r["meta"] = {}
    r["ts"] = KICKOFF + 99999
    S.validate_sports_record(r)


def test_kickoff_ts_must_be_an_integer():
    r = rec("nfl.game")
    r["meta"]["kickoff_ts"] = "2026-09-15T00:50:00Z"
    with pytest.raises(ValueError, match="meta.kickoff_ts must be an integer"):
        S.validate_sports_record(r)


def test_horizon_must_be_at_least_one_second():
    r = rec("wc.match")
    r["horizon_s"] = 0
    with pytest.raises(ValueError, match="horizon_s must be >= 1"):
        S.validate_sports_record(r)


def test_cohort_is_free_text_or_null():
    r = rec("nfl.player_week")
    r["cohort"] = None
    S.validate_sports_record(r)
    r["cohort"] = "WR / wk2 / tier1"
    S.validate_sports_record(r)
    r["cohort"] = 3
    with pytest.raises(ValueError, match="cohort must be a string or null"):
        S.validate_sports_record(r)


# --- outcome validation -------------------------------------------------------


@pytest.mark.parametrize("task", TASKS)
def test_valid_outcome_passes(task):
    S.validate_sports_outcome(task, VALID_OUTCOMES[task])


@pytest.mark.parametrize(
    "task,key",
    [(t, k) for t in TASKS for k in S.SPORTS_TASKS[t].outcome],
)
def test_missing_outcome_key_names_the_key(task, key):
    o = copy.deepcopy(VALID_OUTCOMES[task])
    del o[key]
    with pytest.raises(ValueError, match=f"outcome missing required key '{key}'"):
        S.validate_sports_outcome(task, o)


def test_outcome_unknown_task():
    with pytest.raises(ValueError, match="unknown sports task"):
        S.validate_sports_outcome("nba.game", {"hit": True})


def test_outcome_wrong_type_names_the_key():
    with pytest.raises(ValueError, match="outcome.hit must be a bool, got int"):
        S.validate_sports_outcome("nfl.parlay_leg", {"hit": 1})


def test_wc_outcome_result_vocabulary():
    with pytest.raises(ValueError, match="outcome.result must be one of"):
        S.validate_sports_outcome("wc.match", {"result": "W", "score": [1, 0]})


@pytest.mark.parametrize("score", [[1], [1, 2, 3], [-1, 0], [1.5, 0], [True, 0]])
def test_wc_outcome_score_shape(score):
    with pytest.raises(ValueError, match="outcome.score must be"):
        S.validate_sports_outcome("wc.match", {"result": "H", "score": score})


@pytest.mark.parametrize(
    "result,score", [("H", [0, 0]), ("D", [2, 1]), ("A", [3, 1])]
)
def test_wc_outcome_result_must_agree_with_score(result, score):
    with pytest.raises(ValueError, match="disagrees with outcome.score"):
        S.validate_sports_outcome("wc.match", {"result": result, "score": score})


def test_wc_outcome_draw_and_away():
    S.validate_sports_outcome("wc.match", {"result": "D", "score": [1, 1]})
    S.validate_sports_outcome("wc.match", {"result": "A", "score": [0, 2]})


def test_nfl_game_outcome_home_won_must_agree_with_margin():
    with pytest.raises(ValueError, match="home_won is true but margin is -3"):
        S.validate_sports_outcome("nfl.game", {"home_won": True, "margin": -3})
    with pytest.raises(ValueError, match="home_won is false but margin is 3"):
        S.validate_sports_outcome("nfl.game", {"home_won": False, "margin": 3})
    S.validate_sports_outcome("nfl.game", {"home_won": False, "margin": 0})  # a tie


def test_unresolved_parlay_leg_has_no_outcome_value():
    """An unresolved leg is never a miss: 'unresolved' is not a valid outcome payload."""
    with pytest.raises(ValueError):
        S.validate_sports_outcome("nfl.parlay_leg", {"hit": None})
    with pytest.raises(ValueError):
        S.validate_sports_outcome("nfl.parlay_leg", {"hit": "unresolved"})


# --- schema / Python parity ---------------------------------------------------


@pytest.mark.parametrize("task", TASKS)
def test_schema_loads_and_targets_its_task(task):
    schema = S.load_task_schema(task)
    assert schema["title"] == task
    assert schema["properties"]["task"] == {"const": task}
    assert set(schema["required"]) == {
        "id", "ts", "model_version", "task", "features", "prediction", "horizon_s"
    }
    assert schema["properties"]["horizon_s"]["minimum"] == 1
    json.dumps(schema)  # round-trips


@pytest.mark.parametrize("task", TASKS)
def test_schema_required_keys_match_python_shapes(task):
    schema = S.load_task_schema(task)
    spec = S.SPORTS_TASKS[task]
    defs = schema["$defs"]

    assert set(defs["features"]["required"]) == set(spec.features)
    assert set(defs["outcome"]["required"]) == set(spec.outcome)

    pred = defs["prediction"]
    if spec.prediction_alt is None:
        assert "oneOf" not in pred
        assert set(pred["required"]) == set(spec.prediction)
    else:
        alternatives = {frozenset(alt["required"]) for alt in pred["oneOf"]}
        assert alternatives == {frozenset(spec.prediction), frozenset(spec.prediction_alt)}


_JSON_TYPE = {
    "number": "number",
    "int": "integer",
    "bool": "boolean",
    "string": "string",
    "list": "array",
    "object": "object",
}


@pytest.mark.parametrize("task", TASKS)
def test_schema_property_types_match_python_types(task):
    schema = S.load_task_schema(task)
    spec = S.SPORTS_TASKS[task]
    defs = schema["$defs"]
    for block, shape in (("features", spec.features), ("outcome", spec.outcome)):
        for key, type_name in shape.items():
            prop = defs[block]["properties"][key]
            if "enum" in prop:  # wc.match result: enum of strings
                assert all(isinstance(v, str) for v in prop["enum"]) and type_name == "string"
            else:
                assert prop["type"] == _JSON_TYPE[type_name], f"{task}.{block}.{key}"


def test_schema_market_keys_are_documented():
    for task in TASKS:
        desc = S.load_task_schema(task)["$defs"]["features"]["description"]
        for key in S.MARKET_KEYS:
            assert key in desc, f"{task}: {key} missing from features description"


def test_load_task_schema_unknown_task():
    with pytest.raises(KeyError):
        S.load_task_schema("nba.game")
