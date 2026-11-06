#!/usr/bin/env python3
"""Offline NFL corrections artifact fitter. Local fixtures only; no cloud writes.

Reads graded JSON records, writes a sealed ``nfl.corrections_artifact.v1``
document. Persist that document as an existing journal ``source_snapshot``
(object_id = artifact_id) and pin it with ``SELFLEARN_CORRECTIONS_ARTIFACT_ID``.
No Supabase migration is required.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "selflearn-core"))

from selflearn_core.adapters.nfl_corrections import fit_corrections_artifact


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graded_json", type=Path, help="JSON list of graded leg records")
    parser.add_argument("--trained-through", required=True)
    parser.add_argument("--expires-at", required=True)
    parser.add_argument("--fitted-at")
    parser.add_argument("--source", default="graded_caller_list_untrusted")
    parser.add_argument("--prior-strength", type=float, default=50.0)
    parser.add_argument("--min-graded-total", type=int, default=200)
    parser.add_argument("--min-graded-per-market", type=int, default=50)
    parser.add_argument("--leg-cap-pp", type=float, default=0.03)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    records = json.loads(args.graded_json.read_text(encoding="utf-8"))
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
    args.output.write_text(json.dumps(artifact, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(artifact["artifact_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
