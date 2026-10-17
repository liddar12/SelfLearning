#!/usr/bin/env python3
"""Compare one explicit, supported NFL frozen cohort; never fit or write state.

The independent NFL oracle is a trusted local checkout, not downloaded code.
Only the engine side imports build_cohort. Supplied database watermarks and
content hashes are not independent authentication or completeness evidence.
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import selectors
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "packages/selflearn-core"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))
from selflearn_core.adapters.nfl_calibration import build_cohort  # noqa: E402
from selflearn_core.probability_receipts import fingerprint  # noqa: E402
from selflearn_core.scoring.binary_receipts import score_binary_receipts  # noqa: E402

MAX_INPUT, MAX_OUTPUT, TIMEOUT = 128 * 1024 * 1024, 32 * 1024 * 1024, 60
IDENTITY_KEYS = ("receipt_id", "source_receipt_id", "contract_id", "revision_id", "revision_hash", "base_prob", "served_prob", "hit")


def _run_oracle(value, checkout):
    """Bound both pipe directions incrementally, plus a real wall-clock deadline."""
    checkout = Path(checkout).resolve()
    path = checkout / "scripts/pinned_cohort_oracle.py"
    if not path.is_file():
        raise ValueError("missing_trusted_local_nfl_oracle")
    raw = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    if len(raw) > MAX_INPUT:
        raise ValueError("analysis_input_too_large")
    process = subprocess.Popen([sys.executable, str(path)], cwd=checkout,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data, offset, deadline = bytearray(), 0, time.monotonic() + TIMEOUT
    try:
        with selectors.DefaultSelector() as selected:
            for stream, mode in ((process.stdin, selectors.EVENT_WRITE), (process.stdout, selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(), False)
                selected.register(stream, mode)
            while selected.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("oracle_deadline_exceeded")
                events = selected.select(min(remaining, 1))
                for key, _ in events:
                    if key.fileobj is process.stdin:
                        try:
                            offset += os.write(key.fd, raw[offset:offset + 65536])
                        except BrokenPipeError:
                            offset = len(raw)
                        if offset == len(raw):
                            selected.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, min(65536, MAX_OUTPUT - len(data) + 1))
                        if not chunk:
                            selected.unregister(process.stdout)
                        data.extend(chunk)
                        if len(data) > MAX_OUTPUT:
                            raise ValueError("oracle_output_too_large")
            code = process.wait(timeout=max(.01, deadline - time.monotonic()))
        if code != 0:
            raise ValueError("invalid_or_unavailable_nfl_oracle_input")
        return json.loads(data)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError("bounded_nfl_oracle_failed") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdin, process.stdout):
            if not stream.closed:
                stream.close()


def compare(value, *, nfl_checkout):
    """Call actual independent NFL code, then compare actual engine membership."""
    frozen = json.loads(json.dumps(value, allow_nan=False))
    original_id = fingerprint(frozen)
    oracle = _run_oracle(frozen, nfl_checkout)
    if oracle.get("supported") is not True:
        return {"version": 1, "status": oracle.get("status", "unsupported"), "parity": False, "input_id": original_id}
    snapshot = frozen["snapshot"]
    cohort = build_cohort(snapshot["receipts"], oracle["source_receipts"], snapshot["revisions"],
        as_of_utc=frozen["as_of_utc"], population=frozen["population"], generation_records=snapshot["generation_records"])
    expected = [{key: row[key] for key in IDENTITY_KEYS} for row in oracle["rows"]]
    actual = [{key: row[key] for key in IDENTITY_KEYS} for row in cohort["rows"]]
    expected_forecasts = [{key: row[key] for key in ("receipt_id", "contract_id")} for row in oracle["forecasts"]]
    actual_forecasts = [{key: row[key] for key in ("receipt_id", "contract_id")} for row in cohort["forecasts"]]
    chosen = {row["receipt_id"]: row["revision_id"] for row in cohort["rows"]}
    outcomes = [node["resolution"] for node in snapshot["revisions"] if chosen.get(node["resolution"]["receipt_id"]) == node["revision_id"]]
    # Restrict to the already frozen exact selected population, not all vintages
    # and not an unfiltered correction history; this scorer is not a selector.
    selected = {row["receipt_id"] for row in cohort["rows"]}
    scores = score_binary_receipts([r for r in snapshot["receipts"] if r["receipt_id"] in selected], outcomes)
    n = len(oracle["rows"])
    scalar = {"n": n, "brier": sum(row["brier"] for row in oracle["rows"]) / n if n else None,
              "log_loss": sum(row["log_loss"] for row in oracle["rows"]) / n if n else None,
              "mean_served_prob": sum(row["served_prob"] for row in oracle["rows"]) / n if n else None}
    scalars_equal = scores["n"] == n and all((scalar[key] is None and scores[key] is None)
        or (scalar[key] is not None and scores[key] is not None and math.isclose(scalar[key], scores[key], rel_tol=1e-12, abs_tol=1e-12))
        for key in ("brier", "log_loss", "mean_served_prob"))
    mismatches = []
    if fingerprint(expected) != fingerprint(actual):
        mismatches.append("exact_receipt_contract_revision_or_probability_set_differs")
    if fingerprint(expected_forecasts) != fingerprint(actual_forecasts):
        mismatches.append("first_eligible_forecast_set_differs")
    if not scalars_equal:
        mismatches.append("independent_nfl_scalar_oracle_differs")
    expected_exclusions = {"future_capture", "duplicate_receipt_copy", "unresolved_at_cutoff", "non_binary_latest_revision"}
    if set(oracle["excluded"]) - expected_exclusions:
        mismatches.append("invalid_or_unverified_original_evidence")
    if not n:
        mismatches.append("no_supported_graded_cohort")
    result = {"version": 1, "kind": "nfl.pinned_cohort_parity.v1", "input_id": original_id,
        "snapshot_id": oracle["snapshot_id"], "pin_basis": oracle["pin_basis"], "population": frozen["population"],
        "as_of_utc": frozen["as_of_utc"], "parity": not mismatches,
        "status": "supported_pinned_cohort_parity" if not mismatches else "mismatch_or_unavailable",
        "mismatches": mismatches, "n": n, "first_eligible_forecasts": len(expected_forecasts),
        "nfl_rows": expected, "engine_rows": actual, "nfl_scalars": scalar,
        "engine_scalars": {key: scores[key] for key in scalar}, "numeric_tolerance": {"absolute": 1e-12, "relative": 1e-12},
        "nfl_excluded": oracle["excluded"], "engine_excluded": cohort["excluded"],
        "raw_producer_excluded": oracle["raw_producer_excluded"],
        "source_code_sha256": {**oracle["source_code_sha256"], **{name: "sha256:" + hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("tools/compare_nfl_cohort.py", "packages/selflearn-core/selflearn_core/adapters/nfl_calibration.py",
                         "packages/selflearn-core/selflearn_core/scoring/binary_receipts.py")}},
        "coverage": {"supplied_receipts": len(snapshot["receipts"]), "supplied_revisions": len(snapshot["revisions"]),
                     "supplied_generation_records": len(snapshot["generation_records"]), "capture_ids": oracle["capture_ids"]},
        "limitations": ["Supported eligible-pool MY yardage cohort only; offered_my unsupported.",
            "Local supplied content pin; neither completeness nor a database watermark is independently authenticated.",
            "Code hashes are local source-file observations, not deployed or loaded-bytecode attestation.",
            "Actual NFL producer/grader and supplied original revision identities; live build_review is not revision-aware.",
            "Not historical acceptance, public serving, provider authentication, model fitting, activation or release permission."]}
    result["report_id"] = fingerprint(result)
    return result


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--nfl-checkout", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        with args.bundle.open("rb") as stream:
            raw = stream.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("analysis_input_too_large")
        result = compare(json.loads(raw, object_pairs_hook=_unique), nfl_checkout=args.nfl_checkout)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["parity"] else 3
    except (ValueError, TypeError, KeyError, OSError, OverflowError, AttributeError, RecursionError):
        print(json.dumps({"version": 1, "status": "invalid_or_unavailable_input", "parity": False}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
