"""Pure, opt-in attachment of a supplied NFL parity diagnostic to a review.

This validates bounded plain-JSON metadata and content hashes, not execution,
provider authenticity or database completeness. It opens no resources and does
not run the comparator, grade rows, fit models or activate learning. The caller
must obtain the diagnostic through the separate pinned-snapshot evaluator.
"""
from __future__ import annotations

import json
import math
import re

from selflearn_core.probability_receipts import fingerprint, instant

MAX_REVIEW_BYTES = 64 * 1024 * 1024
MAX_DIAGNOSTIC_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 1_000_000
MAX_STRING_CHARS = 1024 * 1024
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEQUENCE = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
NAMESPACE = {"destination_id": fingerprint({"base_url": "https://self-learning-beige.vercel.app"}),
             "producer_id": "nfl2026"}
DIAGNOSTIC_KEYS = frozenset((
    "version", "kind", "status", "parity", "input_id", "full_snapshot_id", "projection_id",
    "comparator_input_id", "comparator_report_id", "namespace", "namespace_authenticated",
    "completeness_authenticated", "pin_basis", "snapshot_watermark", "as_of_utc", "snapshot_exported_utc",
    "population", "scope", "coverage", "nfl_set_id", "engine_set_id", "nfl_scalars", "engine_scalars",
    "numeric_tolerance", "reason_codes", "source_code_sha256", "source_authentication",
    "live_review_replaced", "model_adoption", "limitations", "diagnostic_id"))
COVERAGE_KEYS = frozenset(("supplied_receipts", "supplied_revisions", "supplied_generation_records",
    "supplied_observations", "first_eligible_forecasts", "graded_contracts", "graded_fraction",
    "all_first_eligible_contracts_graded", "nfl_excluded", "engine_excluded", "raw_producer_excluded", "capture_ids"))
STATUSES = frozenset(("supported_pinned_cohort_parity", "partial_pinned_cohort_parity",
    "unsupported_local_content_pin", "unsupported_population", "mismatch_or_unavailable"))


def _fail():
    raise ValueError("invalid_engine_parity_attachment")


def _snapshot(value, maximum):
    """Copy only plain JSON with bounded traversal and actual canonical bytes."""
    nodes, used = 0, 0

    def visit(item, depth):
        nonlocal nodes, used
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            _fail()
        kind = type(item)
        if kind is dict:
            used += 2 + max(0, len(item) - 1)
            result = {}
            for key, child in item.items():
                if type(key) is not str:
                    _fail()
                frozen_key = visit(key, depth + 1)
                used += 1
                result[frozen_key] = visit(child, depth + 1)
        elif kind is list:
            used += 2 + max(0, len(item) - 1)
            result = [visit(child, depth + 1) for child in item]
        elif kind in (str, int, float, bool) or item is None:
            if (kind is str and (len(item) > MAX_STRING_CHARS or len(item) > maximum - used)
                    or kind is int and item.bit_length() > 1024
                    or kind is float and not math.isfinite(item)):
                _fail()
            used += len(json.dumps(item, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode())
            result = item
        else:
            _fail()
        if used > maximum:
            _fail()
        return result

    return visit(value, 0)


def _keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        _fail()


def _hash(value):
    if type(value) is not str or not HASH.fullmatch(value):
        _fail()


def _integer(value, low=0, high=20000):
    if type(value) is not int or not low <= value <= high:
        _fail()


def _texts(value, maximum=100, width=4096, *, ordered=False):
    if (type(value) is not list or len(value) > maximum
            or any(type(v) is not str or not v or len(v) > width for v in value)
            or ordered and value != sorted(set(value))):
        _fail()


def _exclusions(value):
    if type(value) is not dict or len(value) > 256:
        _fail()
    for key, count in value.items():
        if not key or len(key) > 200:
            _fail()
        _integer(count, high=1_000_000)


def _scalars(value, n=None):
    _keys(value, {"n", "brier", "log_loss", "mean_served_prob"})
    _integer(value["n"])
    if n is not None and value["n"] != n:
        _fail()
    n = value["n"]
    for key in ("brier", "log_loss", "mean_served_prob"):
        number = value[key]
        if n == 0:
            if number is not None:
                _fail()
        elif (type(number) not in (float, int) or not math.isfinite(number) or number < 0
                or key != "log_loss" and number > 1):
            _fail()


def _diagnostic(value, expected_input_id, review, now_utc):
    _keys(value, DIAGNOSTIC_KEYS)
    _hash(expected_input_id)
    for key in ("input_id", "full_snapshot_id", "projection_id", "comparator_input_id", "diagnostic_id", "comparator_report_id"):
        _hash(value[key])
    if (type(value["version"]) is not int or value["version"] != 1
            or value["kind"] != "nfl.review_parity_diagnostic.v1"
            or value["input_id"] != expected_input_id or value["status"] not in STATUSES
            or type(value["parity"]) is not bool
            or fingerprint({k: v for k, v in value.items() if k != "diagnostic_id"}) != value["diagnostic_id"]):
        _fail()
    if value["namespace"] != NAMESPACE or type(value["namespace"]) is not dict:
        _fail()
    for flag in ("namespace_authenticated", "completeness_authenticated", "model_adoption", "live_review_replaced"):
        if value[flag] is not False:
            _fail()
    if value["source_authentication"] != "not_performed" or value["population"] != "eligible_pool":
        _fail()
    _keys(value["scope"], {"season", "weeks", "population", "market_family"})
    scope = value["scope"]
    _integer(scope["season"], 2000, 2100)
    if (scope["season"] != review["season"] or scope["population"] != "eligible_pool"
            or scope["market_family"] != "MY_player_yardage_only" or type(scope["weeks"]) is not list
            or not scope["weeks"] or len(scope["weeks"]) > 18):
        _fail()
    for week in scope["weeks"]:
        _integer(week, 1, 18)
        if (review["review_through_week"] is not None and week > review["review_through_week"]
                or type(review["weeks"].get(str(week))) is not dict):
            _fail()
    if scope["weeks"] != sorted(set(scope["weeks"])):
        _fail()
    if (instant(value["as_of_utc"]) != instant(review["generated_utc"])
            or not instant(value["as_of_utc"]) <= instant(value["snapshot_exported_utc"]) <= instant(now_utc)):
        _fail()
    watermark = value["snapshot_watermark"]
    if watermark is None:
        if (value["pin_basis"] != "local_content_pin_only" or value["parity"] is not False
                or value["status"] != "unsupported_local_content_pin"):
            _fail()
    elif (type(watermark) is not str or not SEQUENCE.fullmatch(watermark) or int(watermark) > 9223372036854775807
          or value["pin_basis"] != "supplied_observed_snapshot_with_claimed_watermark"
          or value["status"] == "unsupported_local_content_pin"):
        _fail()
    coverage = value["coverage"]
    _keys(coverage, COVERAGE_KEYS)
    for key in ("supplied_receipts", "supplied_revisions", "supplied_generation_records", "supplied_observations",
                "first_eligible_forecasts", "graded_contracts"):
        bound = 256 if key == "supplied_generation_records" else 5000 if key in ("supplied_receipts", "first_eligible_forecasts") else 20000
        _integer(coverage[key], high=bound)
    first, graded = coverage["first_eligible_forecasts"], coverage["graded_contracts"]
    if (not graded <= first <= coverage["supplied_receipts"] or graded > coverage["supplied_revisions"]
            or coverage["graded_fraction"] != (graded / first if first else None)
            or first and type(coverage["graded_fraction"]) is not float
            or coverage["all_first_eligible_contracts_graded"] is not bool(value["parity"] and graded == first and first)):
        _fail()
    for key in ("nfl_excluded", "engine_excluded", "raw_producer_excluded"):
        _exclusions(coverage[key])
    _texts(coverage["capture_ids"], maximum=256, width=71, ordered=True)
    for capture_id in coverage["capture_ids"]:
        _hash(capture_id)
    for key in ("nfl_set_id", "engine_set_id"):
        if value[key] is not None:
            _hash(value[key])
    if (value["nfl_set_id"] is None) != (value["engine_set_id"] is None):
        _fail()
    _scalars(value["nfl_scalars"], graded)
    _scalars(value["engine_scalars"])
    _keys(value["numeric_tolerance"], {"absolute", "relative"})
    if any(type(value["numeric_tolerance"][k]) not in (int, float) or value["numeric_tolerance"][k] != 1e-12
           for k in ("absolute", "relative")):
        _fail()
    _texts(value["reason_codes"], width=200, ordered=True)
    _texts(value["limitations"], maximum=32)
    if not value["limitations"] or type(value["source_code_sha256"]) is not dict or not value["source_code_sha256"]:
        _fail()
    for path, digest in value["source_code_sha256"].items():
        if not path or len(path) > 512:
            _fail()
        _hash(digest)
    if value["parity"]:
        if (not graded or value["engine_scalars"]["n"] != graded
                or value["nfl_set_id"] != value["engine_set_id"] or value["nfl_set_id"] is None
                or value["reason_codes"] or value["status"] != ("partial_pinned_cohort_parity" if graded < first else "supported_pinned_cohort_parity")
                or any(not math.isclose(value["nfl_scalars"][key], value["engine_scalars"][key], rel_tol=1e-12, abs_tol=1e-12)
                       for key in ("brier", "log_loss", "mean_served_prob"))):
            _fail()
    elif value["status"] in ("supported_pinned_cohort_parity", "partial_pinned_cohort_parity"):
        _fail()


def attach_engine_parity(review, diagnostic=None, *, expected_input_id=None, now_utc=None):
    """Return a separate diagnostic block; never alter legacy review fields.

    All optional inputs absent is a detached, bounded plain-JSON no-op without
    schema/clock requirements. Review.generated_utc is the explicit
    review as-of and must equal the diagnostic cutoff by UTC instant. Existing
    attachments are never overwritten. A self-hash is not a signature: future
    callers must run the trusted evaluator, not accept arbitrary upload claims.
    """
    if diagnostic is None and expected_input_id is None and now_utc is None:
        return _snapshot(review, MAX_REVIEW_BYTES)
    if diagnostic is None or expected_input_id is None or now_utc is None:
        _fail()
    try:
        base = _snapshot(review, MAX_REVIEW_BYTES)
        value = _snapshot(diagnostic, MAX_DIAGNOSTIC_BYTES)
        if type(base) is not dict or "engine_parity" in base or type(base.get("weeks")) is not dict:
            _fail()
        _integer(base.get("season"), 2000, 2100)
        if "review_through_week" not in base:
            _fail()
        if base["review_through_week"] is not None:
            _integer(base["review_through_week"], 1, 22)
        if type(base.get("generated_utc")) is not str or type(now_utc) is not str:
            _fail()
        _diagnostic(value, expected_input_id, base, now_utc)
        attachment = {"version": 1, "kind": "nfl.review_engine_parity_attachment.v1",
            "scope": "local_pinned_MY_yardage_diagnostic_only", "review_input_id": fingerprint(base),
            "review_season": base["season"], "review_as_of_utc": base["generated_utc"],
            "verification_utc": now_utc, "verification_clock_authenticated": False,
            "expected_input_id": expected_input_id, "diagnostic_id": value["diagnostic_id"], "diagnostic": value,
            "display_only": True, "legacy_review_unchanged": True, "live_learning": False,
            "namespace_authenticated": False, "completeness_authenticated": False,
            "source_authentication": "not_performed", "model_adoption": False, "live_review_replaced": False,
            "accuracy_improvement_claimed": False,
            "verification_basis": "supplied_content_hashes_not_authenticated_execution",
            "limitations": ["Separate MY yardage diagnostic; not Game/Week cards, offered bets or legacy settlement.",
                "A matching content hash authenticates neither the evaluator nor provider bytes or full database membership.",
                "Numeric parity does not prove accuracy improvement, public serving, live learning or model adoption.",
                "Legacy weeks, learning, refit, as-offered and settlement fields are preserved unchanged."]}
        attachment["attachment_id"] = fingerprint(attachment)
        base["engine_parity"] = attachment
        return base
    except (ValueError, TypeError, KeyError, OverflowError, RecursionError, AttributeError):
        raise ValueError("invalid_engine_parity_attachment") from None


MAX_CLI_INPUT = 65 * 1024 * 1024
MAX_CLI_OUTPUT = 66 * 1024 * 1024


def _cli_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail()
            result[key] = value
        return result

    def constant(_value):
        _fail()

    return json.loads(raw, object_pairs_hook=unique, parse_constant=constant)


def main(argv=None):
    """Explicit stdin/stdout transport only; no file, network or env discovery."""
    import argparse
    import sys

    class Parser(argparse.ArgumentParser):
        def error(self, _message):
            _fail()

    try:
        parser = Parser(description="Opt-in local review attachment over bounded stdin/stdout")
        parser.add_argument("--attach-stdin", action="store_true")
        args = parser.parse_args(argv)
        if not args.attach_stdin:
            print('{"version":1,"status":"disabled","parity":false}')
            return 0
        raw = sys.stdin.buffer.read(MAX_CLI_INPUT + 1)
        if len(raw) > MAX_CLI_INPUT:
            _fail()
        value = _cli_json(raw)
        _keys(value, {"version", "review", "diagnostic", "expected_input_id", "now_utc"})
        if type(value["version"]) is not int or value["version"] != 1 or value["diagnostic"] is None:
            _fail()
        result = attach_engine_parity(value["review"], value["diagnostic"],
            expected_input_id=value["expected_input_id"], now_utc=value["now_utc"])
        encoded = json.dumps(result, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()
        if len(encoded) > MAX_CLI_OUTPUT:
            _fail()
        sys.stdout.buffer.write(encoded + b"\n")
        return 0 if result["engine_parity"]["diagnostic"]["parity"] else 3
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError, AttributeError):
        print('{"version":1,"status":"invalid_or_unavailable_review_attachment","parity":false}')
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
