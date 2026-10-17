"""Bounded CLI transport tests; fake diagnostic DTOs are not learning proof."""
import io
import json
import unittest
from unittest.mock import patch

from tools import nfl_review_attachment as A
from test_nfl_review_attachment import review, diagnostic, reseal


class Capture(io.TextIOWrapper):
    def __init__(self, raw=b""):
        super().__init__(io.BytesIO(raw), encoding="utf-8")


def request():
    value = diagnostic()
    return {"version": 1, "review": review(), "diagnostic": value,
            "expected_input_id": value["input_id"], "now_utc": "2026-10-02T00:00:00Z"}


def call(value, args=None):
    raw = value if type(value) is bytes else json.dumps(value).encode()
    source, sink = Capture(raw), Capture()
    with patch("sys.stdin", source), patch("sys.stdout", sink):
        code = A.main(["--attach-stdin"] if args is None else args)
        sink.flush()
        output = sink.buffer.getvalue()
    return code, json.loads(output)


class ReviewAttachmentCLITests(unittest.TestCase):
    def test_default_off_never_reads_stdin(self):
        source, sink = Capture(), Capture()
        with patch.object(source.buffer, "read", side_effect=AssertionError("disabled stdin read")), \
             patch("sys.stdin", source), patch("sys.stdout", sink):
            code = A.main([]); sink.flush()
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(sink.buffer.getvalue()), {"version": 1, "status": "disabled", "parity": False})

    def test_true_and_unknown_parity_have_distinct_exit_codes_but_valid_attached_reviews(self):
        value = request(); code, result = call(value)
        self.assertEqual(code, 0)
        self.assertEqual(result["engine_parity"]["verification_utc"], value["now_utc"])
        self.assertIs(result["engine_parity"]["verification_clock_authenticated"], False)
        self.assertEqual({k: v for k, v in result.items() if k != "engine_parity"}, value["review"])
        value["diagnostic"].update(parity=False, status="unsupported_local_content_pin", snapshot_watermark=None,
            pin_basis="local_content_pin_only", reason_codes=["missing_supplied_database_watermark"])
        value["diagnostic"]["coverage"]["all_first_eligible_contracts_graded"] = False
        reseal(value["diagnostic"])
        code, result = call(value)
        self.assertEqual(code, 3)
        self.assertIs(result["engine_parity"]["diagnostic"]["parity"], False)
        self.assertIs(result["engine_parity"]["live_learning"], False)

    def test_duplicate_nonfinite_unknown_and_missing_fields_are_static_refusals(self):
        values = [b'{"SECRET":1,"SECRET":2}', b'{"SECRET":NaN}', b'{"SECRET":1e400}', b'[]', b'null']
        for change in (lambda v: v.update(version=True), lambda v: v.update(version=1.0),
                       lambda v: v.update(extra="SECRET"), lambda v: v.pop("review"),
                       lambda v: v.update(diagnostic=None), lambda v: v.update(expected_input_id="SECRET")):
            value = request(); change(value); values.append(value)
        for value in values:
            with self.subTest(value=type(value).__name__):
                code, result = call(value)
                self.assertEqual(code, 2)
                self.assertEqual(result, {"version": 1, "status": "invalid_or_unavailable_review_attachment", "parity": False})
                self.assertNotIn("SECRET", json.dumps(result))

    def test_actual_input_output_caps_and_unknown_flags_do_not_echo_private_values(self):
        for constant in ("MAX_CLI_INPUT", "MAX_CLI_OUTPUT"):
            with patch.object(A, constant, 1):
                code, result = call(request())
                self.assertEqual(code, 2); self.assertFalse(result["parity"])
        code, result = call(request(), ["--SECRET-private-argument"])
        self.assertEqual(code, 2); self.assertNotIn("SECRET", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
