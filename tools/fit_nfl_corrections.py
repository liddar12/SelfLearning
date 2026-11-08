#!/usr/bin/env python3
"""Offline NFL corrections artifact fitter. Local fixtures only; no cloud writes.

Reads graded JSON records, writes a sealed ``nfl.corrections_artifact.v1``
document. This caller-list tool cannot claim binder origin
(``receipt_journal_as_of_v1``). Digests detect mutation only.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "selflearn-core"))

from selflearn_core.adapters.nfl_corrections import CorrectionsError, fit_corrections_artifact
from selflearn_core.adapters.nfl_corrections_policy import (
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graded_json", type=Path, help="JSON list of graded leg records")
    parser.add_argument("--trained-through", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--fitted-at")
    parser.add_argument("--source", default=UNTRUSTED_ARTIFACT_SOURCE)
    parser.add_argument("--prior-strength", type=float, default=50.0)
    parser.add_argument("--min-graded-total", type=int, default=200)
    parser.add_argument("--min-graded-per-market", type=int, default=50)
    parser.add_argument("--leg-cap-pp", type=float, default=0.03)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.source == TRUSTED_ARTIFACT_SOURCE:
        raise SystemExit(
            "refusing trusted binder source on caller-list CLI; "
            "use fit_corrections_from_journal")
    records = json.loads(args.graded_json.read_text(encoding="utf-8"))
    try:
        artifact = fit_corrections_artifact(
            records,
            trained_through=args.trained_through,
            expires_at=args.expires_at,
            fitted_at=args.fitted_at,
            source=args.source,
            prior_strength=args.prior_strength,
            min_graded_total=args.min_graded_total,
            min_graded_per_market=args.min_graded_per_market,
            leg_cap_pp=args.leg_cap_pp,
        )
    except CorrectionsError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(artifact["artifact_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
