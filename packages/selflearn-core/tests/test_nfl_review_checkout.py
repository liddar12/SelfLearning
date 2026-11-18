"""Checkout selection for the legacy NFL review QA lane."""

import pytest

from nfl_review_checkout import legacy_review_checkout


def legacy_checkout(path):
    scripts = path / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "build_learning_outcomes.py").touch()
    (scripts / "pinned_cohort_oracle.py").touch()
    return str(path)


def test_accepts_legacy_checkout_through_original_environment_variable(tmp_path):
    checkout = legacy_checkout(tmp_path / "legacy")
    assert legacy_review_checkout({"NFL2026_CHECKOUT": checkout}) == checkout


def test_current_capture_checkout_does_not_opt_in_to_legacy_review(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "capture_outcome_evidence.py").touch()
    assert legacy_review_checkout({"NFL2026_CHECKOUT": str(tmp_path)}) is None
    assert legacy_review_checkout({}) is None


def test_explicit_review_checkout_takes_precedence(tmp_path):
    checkout = legacy_checkout(tmp_path / "legacy")
    assert legacy_review_checkout({
        "NFL2026_CHECKOUT": str(tmp_path / "current"),
        "NFL2026_REVIEW_CHECKOUT": checkout,
    }) == checkout


def test_incomplete_explicit_review_checkout_errors_even_with_legacy_fallback(tmp_path):
    fallback = legacy_checkout(tmp_path / "legacy")
    requested = tmp_path / "incomplete"
    scripts = requested / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "build_learning_outcomes.py").touch()
    with pytest.raises(ValueError, match=r"NFL2026_REVIEW_CHECKOUT=.* is missing scripts/pinned_cohort_oracle\.py"):
        legacy_review_checkout({
            "NFL2026_CHECKOUT": fallback,
            "NFL2026_REVIEW_CHECKOUT": str(requested),
        })
