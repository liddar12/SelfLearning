#!/usr/bin/env python3
"""Local, default-off review diagnostic; no network, writes, fitting or adoption.

The input is a caller-supplied FULL observed snapshot, original generation/source
graph and raw captures. A content pin and an approved namespace CLAIM detect
changed inputs; neither authenticates their origin or proves database-wide
completeness. Only the existing independent comparator selects/scorers rows.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
for location in (ROOT, ROOT / "packages/selflearn-core"):
    if str(location) not in sys.path:
        sys.path.insert(0, str(location))

from tools import compare_nfl_cohort as comparator  # noqa: E402
from selflearn_core.adapters.nfl_observed_journal import _validate_fences  # noqa: E402
from selflearn_core.outcome_revisions import validate_journal_resolution  # noqa: E402
from selflearn_core.probability_receipts import fingerprint, instant, validate_probability_receipt  # noqa: E402
from selflearn_core.store.supabase_journal import SupabaseReceiptJournal, _object_payload, _revision_envelope  # noqa: E402
from selflearn_core.store.supabase_store import StoreError  # noqa: E402

MAX_INPUT = 128 * 1024 * 1024
MAX_OUTPUT = 1024 * 1024
MAX_OBSERVATIONS = 20000
ORIGIN = "https://self-learning-beige.vercel.app"
NAMESPACE = {"destination_id": fingerprint({"base_url": ORIGIN}), "producer_id": "nfl2026"}
SNAPSHOT_KEYS = {"version", "watermark", "receipts", "generation_records", "revisions", "observations", "observation_records"}
PROJECTION_KEYS = ("version", "watermark", "receipts", "generation_records", "revisions")
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEQUENCE = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("nonfinite_json")


def read_input(path):
    """Read one explicitly selected local artifact, never discover or fetch it."""
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        raise ValueError("review_parity_input_exceeds_byte_limit")
    return json.loads(raw, object_pairs_hook=unique, parse_constant=_reject_constant)


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("invalid_review_parity_shape")


def _full_snapshot(snapshot, exported):
    _keys(snapshot, SNAPSHOT_KEYS)
    if type(snapshot["version"]) is not int or snapshot["version"] != 1:
        raise ValueError("invalid_review_snapshot_version")
    watermark = snapshot["watermark"]
    if watermark is not None and (type(watermark) is not str or not SEQUENCE.fullmatch(watermark)
                                  or int(watermark) > 9223372036854775807):
        raise ValueError("invalid_review_snapshot_watermark")
    for key, bound in (("receipts", 5000), ("generation_records", 256), ("revisions", 20000),
                       ("observations", MAX_OBSERVATIONS), ("observation_records", MAX_OBSERVATIONS)):
        if type(snapshot[key]) is not list or len(snapshot[key]) > bound:
            raise ValueError("review_snapshot_population_limit")
    receipts = {}
    source_keys = set()
    for receipt in snapshot["receipts"]:
        validate_probability_receipt(receipt)
        key = (receipt["domain"], receipt["task"], receipt["source_receipt_id"])
        if receipt["receipt_id"] in receipts or key in source_keys:
            raise ValueError("duplicate_review_receipt")
        if receipt["domain"] != "nfl" or receipt["task"] != "nfl.parlay_leg":
            raise ValueError("unsupported_review_snapshot_domain")
        if instant(receipt["first_seen_utc"]) > exported:
            raise ValueError("review_receipt_after_snapshot_export")
        receipts[receipt["receipt_id"]] = receipt
        source_keys.add(key)
    accepted, generations = set(), set()
    for row in snapshot["generation_records"]:
        _keys(row, {"object_id", "payload", "ack", "recorded_utc"})
        _object_payload("generation", row["object_id"], row["payload"])
        try:
            SupabaseReceiptJournal._ack(row["ack"], row["object_id"], row["payload"])
        except StoreError:
            raise ValueError("invalid_review_generation_partition") from None
        if row["object_id"] in generations or instant(row["recorded_utc"]) > exported:
            raise ValueError("invalid_review_generation_identity_or_clock")
        generations.add(row["object_id"])
        for accepted_row in row["ack"]["receipts"]:
            receipt = receipts.get(accepted_row["receipt_id"])
            if receipt is None or receipt["source_receipt_id"] != accepted_row["source_receipt_id"]:
                raise ValueError("review_generation_receipt_mapping_mismatch")
            accepted.add(receipt["receipt_id"])
    if accepted != set(receipts):
        raise ValueError("incomplete_review_generation_partition")
    revisions = set()
    for row in snapshot["revisions"]:
        _keys(row, {"version", "revision_id", "supersedes", "reason", "resolution", "recorded_utc"})
        if type(row["version"]) is not int or row["version"] != 1 or row["revision_id"] in revisions:
            raise ValueError("invalid_review_revision_identity")
        _revision_envelope(row["resolution"], row["revision_id"], row["supersedes"], row["reason"])
        receipt = receipts[row["resolution"]["receipt_id"]]
        validate_journal_resolution(receipt, row["resolution"])
        if not instant(row["resolution"]["resolved_utc"]) <= instant(row["recorded_utc"]) <= exported:
            raise ValueError("invalid_review_revision_clock")
        revisions.add(row["revision_id"])
    observations = {}
    for row in snapshot["observations"]:
        if type(row) is not dict or type(row.get("observation_id")) is not str or row["observation_id"] in observations:
            raise ValueError("invalid_review_observation_identity")
        observations[row["observation_id"]] = row
    records = set()
    for row in snapshot["observation_records"]:
        _keys(row, {"observation_id", "payload", "recorded_utc"})
        oid = row["observation_id"]
        if (type(oid) is not str or oid in records or oid not in observations
                or fingerprint(row["payload"]) != fingerprint(observations[oid])
                or row["payload"].get("observation_id") != oid
                or not instant(row["payload"]["observed_utc"]) <= instant(row["recorded_utc"]) <= exported):
            raise ValueError("invalid_review_observation_record")
        records.add(oid)
    if records != set(observations):
        raise ValueError("incomplete_review_observation_partition")
    # Existing engine fence validation includes ALL unchanged confirmations;
    # the independent NFL raw regrader below remains a separate implementation.
    _validate_fences(snapshot["receipts"], snapshot["revisions"], snapshot["observations"], exported)


def evaluate(value, *, expected_input_id, nfl_checkout, now_utc):
    """Return a staged local diagnostic. No reads except trusted oracle/code files."""
    if len(encoded(value)) > MAX_INPUT:
        raise ValueError("review_parity_input_exceeds_byte_limit")
    frozen = copy.deepcopy(value)
    _keys(frozen, {"version", "kind", "namespace", "snapshot_exported_utc", "analysis"})
    if (type(frozen["version"]) is not int or frozen["version"] != 1
            or frozen["kind"] != "nfl.review_parity_input.v1"
            or type(expected_input_id) is not str or not HASH.fullmatch(expected_input_id)
            or fingerprint(frozen) != expected_input_id):
        raise ValueError("invalid_review_parity_content_pin")
    _keys(frozen["namespace"], NAMESPACE)
    if frozen["namespace"] != NAMESPACE:
        raise ValueError("unapproved_review_namespace_claim")
    analysis = frozen["analysis"]
    _keys(analysis, {"version", "snapshot", "export_plan", "captures", "as_of_utc", "population"})
    if type(analysis["version"]) is not int or analysis["version"] != 1:
        raise ValueError("invalid_review_analysis_version")
    exported = instant(frozen["snapshot_exported_utc"])
    if not instant(analysis["as_of_utc"]) <= exported <= instant(now_utc):
        raise ValueError("review_parity_future_cutoff_or_export")
    snapshot = analysis["snapshot"]
    _full_snapshot(snapshot, exported)
    projection = {key: snapshot[key] for key in PROJECTION_KEYS}
    compared = {**analysis, "snapshot": projection}
    report = comparator.compare(compared, nfl_checkout=nfl_checkout)
    has_fence = snapshot["watermark"] is not None
    parity = has_fence and report.get("parity") is True
    first, graded = report.get("first_eligible_forecasts", 0), report.get("n", 0)
    reasons = list(report.get("mismatches", []))
    if not has_fence:
        reasons.append("missing_supplied_database_watermark")
    supported = report.get("status") == "supported_pinned_cohort_parity"
    scope = None
    if report.get("kind") == "nfl.pinned_cohort_parity.v1":
        documents = [item["request"]["payload"] for item in analysis["export_plan"]["generations"]]
        scope = {"season": documents[0]["season"], "weeks": sorted({doc["week"] for doc in documents}),
                 "population": analysis["population"], "market_family": "MY_player_yardage_only"}
    status = ("unsupported_local_content_pin" if not has_fence else
              "partial_pinned_cohort_parity" if parity and graded < first else
              "supported_pinned_cohort_parity" if parity else
              "unsupported_population" if analysis["population"] != "eligible_pool" else "mismatch_or_unavailable")
    result = {"version": 1, "kind": "nfl.review_parity_diagnostic.v1", "status": status, "parity": parity,
        "input_id": expected_input_id, "full_snapshot_id": fingerprint(snapshot), "projection_id": fingerprint(projection),
        "comparator_input_id": fingerprint(compared), "comparator_report_id": report.get("report_id"),
        "namespace": frozen["namespace"], "namespace_authenticated": False, "completeness_authenticated": False,
        "pin_basis": "supplied_observed_snapshot_with_claimed_watermark" if has_fence else "local_content_pin_only",
        "snapshot_watermark": snapshot["watermark"], "as_of_utc": analysis["as_of_utc"],
        "snapshot_exported_utc": frozen["snapshot_exported_utc"], "population": analysis["population"], "scope": scope,
        "coverage": {"supplied_receipts": len(snapshot["receipts"]), "supplied_revisions": len(snapshot["revisions"]),
            "supplied_generation_records": len(snapshot["generation_records"]), "supplied_observations": len(snapshot["observations"]),
            "first_eligible_forecasts": first, "graded_contracts": graded,
            "graded_fraction": graded / first if first else None, "all_first_eligible_contracts_graded": bool(parity and graded == first and first),
            "nfl_excluded": report.get("nfl_excluded", {}), "engine_excluded": report.get("engine_excluded", {}),
            "raw_producer_excluded": report.get("raw_producer_excluded", {}), "capture_ids": report.get("coverage", {}).get("capture_ids", [])},
        "nfl_set_id": fingerprint(report.get("nfl_rows", [])) if supported else None,
        "engine_set_id": fingerprint(report.get("engine_rows", [])) if supported else None,
        "nfl_scalars": report.get("nfl_scalars"), "engine_scalars": report.get("engine_scalars"),
        "numeric_tolerance": report.get("numeric_tolerance"), "reason_codes": sorted(set(reasons)),
        "source_code_sha256": {**report.get("source_code_sha256", {}), **{name: "sha256:" + hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("tools/nfl_review_parity.py", "packages/selflearn-core/selflearn_core/adapters/nfl_observed_journal.py",
                "packages/selflearn-core/selflearn_core/adapters/nfl_observed_resolution.py", "packages/selflearn-core/selflearn_core/probability_receipts.py",
                "packages/selflearn-core/selflearn_core/outcome_revisions.py", "packages/selflearn-core/selflearn_core/store/supabase_journal.py")}},
        "source_authentication": "not_performed", "live_review_replaced": False, "model_adoption": False,
        "limitations": ["Eligible MY yardage pool only; not Game/Week cards or offered_my.",
            "Namespace and database watermark are supplied claims, not authentication or sequence attestation.",
            "Validated supplied partitions cannot prove that whole omitted database commits never existed.",
            "Recorded clocks are checked; original database sequences/transactions may be absent from this export.",
            "Source hashes describe local files, not deployed or loaded-bytecode attestation; parity is not an accuracy gain.",
            "Default live review/settlement/refit remain unchanged; no activation or historical acceptance claim."]}
    result["diagnostic_id"] = fingerprint(result)
    if len(encoded(result)) > MAX_OUTPUT:
        raise ValueError("review_parity_output_exceeds_byte_limit")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-input-id", required=True)
    parser.add_argument("--nfl-checkout", type=Path, required=True)
    parser.add_argument("--now-utc", default=None)
    args = parser.parse_args(argv)
    try:
        result = evaluate(read_input(args.bundle), expected_input_id=args.expected_input_id, nfl_checkout=args.nfl_checkout,
                          now_utc=args.now_utc or datetime.now(timezone.utc).isoformat())
        print(encoded(result).decode())
        return 0 if result["parity"] else 3
    except (ValueError, TypeError, KeyError, OSError, OverflowError, AttributeError, RecursionError):
        print('{"version":1,"status":"invalid_or_unavailable_review_input","parity":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
