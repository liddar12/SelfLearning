"""Gate 1 tests for the feature layer: taxonomy is stable, the no-lookahead
validator bites, and the assembler namespaces by family. No data wiring here —
the concrete packs' bodies raise until their gate.
"""

import pytest

from selflearn_core.features import (
    ALL_PACKS,
    FeatureFamily,
    FeatureSnapshot,
    assemble_features,
    validate_no_lookahead,
)


def test_family_taxonomy_is_complete():
    names = {f.value for f in FeatureFamily}
    assert names == {
        "price_action",
        "macro_policy",
        "geopolitics",
        "commodities",
        "ai_infra",
        "regime",
    }


def test_every_pack_declares_a_known_family():
    for pack in ALL_PACKS:
        assert isinstance(pack.family, FeatureFamily)
        assert pack.name


def test_validate_no_lookahead_passes_on_past_observations():
    snap = FeatureSnapshot(
        pack="price_action",
        family=FeatureFamily.PRICE_ACTION,
        as_of_ts=1000,
        values={"close": 100.0, "vol": 5.0},
        obs_ts={"close": 1000, "vol": 999},
    )
    validate_no_lookahead(snap)  # no raise


def test_validate_no_lookahead_rejects_future_observation():
    snap = FeatureSnapshot(
        pack="geopolitics",
        family=FeatureFamily.GEOPOLITICS,
        as_of_ts=1000,
        values={"risk_index": 0.8},
        obs_ts={"risk_index": 1001},  # after the prediction moment
    )
    with pytest.raises(ValueError, match="lookahead"):
        validate_no_lookahead(snap)


def test_validate_requires_obs_ts_for_every_feature():
    snap = FeatureSnapshot(
        pack="commodities",
        family=FeatureFamily.COMMODITIES,
        as_of_ts=1000,
        values={"gold": 2400.0},
        obs_ts={},  # missing
    )
    with pytest.raises(ValueError, match="observation timestamp"):
        validate_no_lookahead(snap)


def test_assemble_namespaces_by_family_and_pack():
    snaps = [
        FeatureSnapshot(
            pack="price_action",
            family=FeatureFamily.PRICE_ACTION,
            as_of_ts=1000,
            values={"close": 100.0},
            obs_ts={"close": 1000},
        ),
        FeatureSnapshot(
            pack="commodities",
            family=FeatureFamily.COMMODITIES,
            as_of_ts=1000,
            values={"gold": 2400.0},
            obs_ts={"gold": 990},
        ),
    ]
    feats = assemble_features(snaps, as_of_ts=1000)
    assert feats == {
        "price_action.price_action.close": 100.0,
        "commodities.commodities.gold": 2400.0,
    }


def test_assemble_rejects_mismatched_as_of():
    snap = FeatureSnapshot(
        pack="macro_policy",
        family=FeatureFamily.MACRO_POLICY,
        as_of_ts=900,  # taken for a different moment than the prediction
        values={"fed_funds": 4.5},
        obs_ts={"fed_funds": 800},
    )
    with pytest.raises(ValueError, match="as_of_ts"):
        assemble_features([snap], as_of_ts=1000)
