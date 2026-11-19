#!/usr/bin/env python3
"""Verify an NFL raw outcome capture against an existing local forecast journal."""
import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages" / "selflearn-core"))

from selflearn_core.adapters.nfl_local_outcomes import NFLLocalObservedJournal


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--capture", type=Path, required=True, help="NFL2026 capture manifest path")
    parser.add_argument("--nfl-checkout", type=Path, required=True, help="trusted local NFL2026 checkout")
    parser.add_argument("--nfl-commit", required=True, help="expected full producer commit SHA")
    parser.add_argument("--apply", action="store_true", help="append verified outcomes to the local journal")
    args = parser.parse_args(argv)
    git = ["git", "-C", str(args.nfl_checkout)]
    try:
        head = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output([*git, "status", "--porcelain", "--", "scripts"], text=True)
        if head != args.nfl_commit or dirty:
            raise ValueError("producer_checkout_not_at_clean_expected_commit")
        sys.path.insert(0, str(args.nfl_checkout.resolve()))
        from scripts.run_parlay_feedback import raw_capture
        envelope, chunks, _, _ = raw_capture(args.capture)
        with NFLLocalObservedJournal(args.journal, read_only=True) as journal:
            if args.apply:
                journal.read_publication_fit_snapshot()
            else:
                result = journal.apply_capture(envelope, chunks)
        if args.apply:
            with NFLLocalObservedJournal(args.journal) as journal:
                result = journal.apply_capture(envelope, chunks, write=True)
    except (OSError, ValueError, KeyError, sqlite3.Error, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"local outcome capture refused: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
