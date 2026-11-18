"""Select a checkout for the optional legacy NFL review diagnostics."""

import os
from pathlib import Path


REVIEW_CHECKOUT_ENV = "NFL2026_REVIEW_CHECKOUT"
LEGACY_REVIEW_SKIP = f"Set {REVIEW_CHECKOUT_ENV} to opt in to legacy NFL review QA"
REQUIRED_SCRIPTS = (
    "scripts/build_learning_outcomes.py",
    "scripts/pinned_cohort_oracle.py",
)


def legacy_review_checkout(environ=os.environ):
    if REVIEW_CHECKOUT_ENV in environ:
        checkout = environ[REVIEW_CHECKOUT_ENV]
        if not checkout.strip():
            raise ValueError(f"{REVIEW_CHECKOUT_ENV} must name a legacy NFL checkout")
        missing = [script for script in REQUIRED_SCRIPTS if not (Path(checkout) / script).is_file()]
        if missing:
            raise ValueError(f"{REVIEW_CHECKOUT_ENV}={checkout!r} is missing {', '.join(missing)}")
        return checkout

    checkout = environ.get("NFL2026_CHECKOUT")
    if checkout and all((Path(checkout) / script).is_file() for script in REQUIRED_SCRIPTS):
        return checkout
    return None
