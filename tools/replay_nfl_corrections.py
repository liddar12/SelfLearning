#!/usr/bin/env python3
"""Fit, register, freeze, or score an NFL correction using an existing local journal."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "selflearn-core"))

from selflearn_core.adapters.nfl_baseline_audit import audit_baseline
from selflearn_core.adapters.nfl_corrections_evaluation import (
    evaluate_correction_holdout,
    freeze_correction_holdout,
    record_correction_shadow,
    register_correction_holdout,
)
from selflearn_core.adapters.nfl_corrections_fit import fit_corrections_from_journal
from selflearn_core.adapters.nfl_corrections_policy import LINEAGE_VERSION
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal


def load_document(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return document


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True, help="existing SQLite publication journal")
    commands = parser.add_subparsers(dest="command", required=True)
    baseline = commands.add_parser("baseline", help="audit forecast, calibration and grading coverage")
    baseline.add_argument("--as-of", required=True)
    fit = commands.add_parser("fit")
    fit.add_argument("--trained-through", required=True)
    fit.add_argument("--fitted-at", required=True)
    fit.add_argument("--expires-at", required=True)
    register = commands.add_parser("register", help="commit a local pre-holdout registration")
    register.add_argument("--artifact", type=Path, required=True)
    register.add_argument("--policy", type=Path, required=True)
    register.add_argument("--start", required=True)
    register.add_argument("--end", required=True)
    capture = commands.add_parser("capture-shadow", help="persist pre-kickoff local shadow decisions")
    capture.add_argument("--artifact", type=Path, required=True)
    capture.add_argument("--registration-id", required=True)
    capture.add_argument("--start", required=True)
    capture.add_argument("--end", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--artifact", type=Path, required=True)
    freeze.add_argument("--start", required=True)
    freeze.add_argument("--end", required=True)
    freeze.add_argument("--policy", type=Path, help="sealed evaluation policy JSON")
    freeze.add_argument("--registration-id", help="use the committed candidate and policy from this journal")
    score = commands.add_parser("score")
    score.add_argument("--artifact", type=Path, required=True)
    score.add_argument("--holdout", type=Path, required=True)
    score.add_argument("--as-of", required=True)
    score.add_argument("--recorded-shadow", action="store_true")
    args = parser.parse_args(argv)
    try:
        writes_journal = args.command in {"register", "capture-shadow"}
        if writes_journal:
            # Refuse an absent or unrelated file before initializing the
            # registration schema on an existing NFL journal.
            with NFLPublicationJournal(args.journal, read_only=True) as journal:
                journal.read_publication_fit_snapshot()
        with NFLPublicationJournal(args.journal, read_only=not writes_journal) as journal:
            if args.command == "baseline":
                result = audit_baseline(journal, as_of_utc=args.as_of)
            elif args.command == "fit":
                result = fit_corrections_from_journal(
                    journal, trained_through=args.trained_through, fitted_at=args.fitted_at,
                    expires_at=args.expires_at, artifact_version=LINEAGE_VERSION,
                )
            elif args.command == "register":
                result = register_correction_holdout(
                    journal, load_document(args.artifact), start_utc=args.start,
                    end_utc=args.end, policy=load_document(args.policy),
                )
            elif args.command == "capture-shadow":
                result = record_correction_shadow(
                    journal, load_document(args.artifact), registration_id=args.registration_id,
                    start_utc=args.start, end_utc=args.end,
                )
            elif args.command == "freeze":
                policy = load_document(args.policy) if args.policy else None
                result = freeze_correction_holdout(
                    journal, load_document(args.artifact), start_utc=args.start,
                    end_utc=args.end, policy=policy, registration_id=args.registration_id,
                )
            else:
                result = evaluate_correction_holdout(
                    journal, load_document(args.artifact), load_document(args.holdout),
                    as_of_utc=args.as_of, recorded_shadow=args.recorded_shadow,
                )
    except (OSError, sqlite3.Error, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"replay refused: {exc}\n")
    json.dump(result, sys.stdout, indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
