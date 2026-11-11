"""Offline tests: no account access, orders, credentials, or network required."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import socket
import unittest
from unittest import mock
import urllib.error


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "alpaca_paper_preflight_under_test", ROOT / "scripts" / "alpaca_paper_preflight.py"
)
preflight = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preflight)

ENV = {"ALPACA_API_KEY": "test-only-key", "ALPACA_SECRET_KEY": "test-only-secret"}
ACCOUNT = {"status": "ACTIVE", "account_blocked": False, "trading_blocked": False}


class PaperPreflightTests(unittest.TestCase):
    def check(self, body=None, status=200):
        transport = mock.Mock(return_value=(status, ACCOUNT if body is None else body))
        report = preflight.run_preflight(check_paper=True, environment=ENV, transport=transport)
        self.assertEqual(transport.call_count, 1)
        return report

    def test_missing_credentials_does_not_contact_broker(self):
        transport = mock.Mock(side_effect=AssertionError("network is forbidden"))
        report = preflight.run_preflight(check_paper=True, environment={}, transport=transport)
        self.assertEqual(report["status"], "missing_credentials")
        self.assertEqual(report["missing_environment"], list(preflight.CREDENTIAL_NAMES))
        transport.assert_not_called()

    def test_offline_default_never_contacts_broker(self):
        transport = mock.Mock(side_effect=AssertionError("network is forbidden"))
        report = preflight.run_preflight(environment=ENV, transport=transport)
        self.assertEqual(report["status"], "configuration_ready_unverified")
        self.assertFalse(report["connection_verified"])
        self.assertFalse(report["network_checked"])
        self.assertFalse(report["options_adapter_supported"])
        transport.assert_not_called()

    def test_partial_credentials_report_names_only(self):
        report = preflight.run_preflight(environment={"ALPACA_API_KEY": ENV["ALPACA_API_KEY"]})
        self.assertEqual(report["missing_environment"], ["ALPACA_SECRET_KEY"])
        self.assertNotIn(ENV["ALPACA_API_KEY"], json.dumps(report))

    def test_invalid_or_placeholder_credentials_never_leave_process(self):
        for value in ("<paper-key>", "your_alpaca_api_key", "bad\nheader", " ", 123):
            with self.subTest(value_type=type(value).__name__):
                transport = mock.Mock()
                report = preflight.run_preflight(check_paper=True,
                    environment={**ENV, "ALPACA_API_KEY": value}, transport=transport)
                self.assertEqual(report["status"], "invalid_configuration")
                transport.assert_not_called()

    def test_success_excludes_account_financial_and_secret_values(self):
        body = {**ACCOUNT, "id": "private-account-id", "account_number": "private-account-number",
                "cash": "12345.67", "equity": "89123.45", "secret_echo": ENV["ALPACA_SECRET_KEY"]}
        report = self.check(body)
        self.assertEqual(report["status"], "paper_connection_ok")
        self.assertTrue(report["connection_verified"])
        self.assertEqual(report["orders_submitted"], 0)
        encoded = json.dumps(report)
        for value in ("private-account-id", "private-account-number", "12345.67", "89123.45", *ENV.values()):
            self.assertNotIn(value, encoded)

    def test_restrictions_use_fixed_codes(self):
        for flag in ("account_blocked", "trading_blocked", "trade_suspended_by_user"):
            with self.subTest(flag=flag):
                report = self.check({**ACCOUNT, flag: True})
                self.assertEqual(report["status"], "paper_account_restricted")
                self.assertEqual(report["restrictions"], [flag])
        report = self.check({**ACCOUNT, "status": "sensitive-unexpected-value"})
        self.assertEqual(report["restrictions"], ["account_not_active"])
        self.assertNotIn("sensitive-unexpected-value", json.dumps(report))

    def test_missing_or_wrong_type_status_fields_fail_closed(self):
        for body in ({}, {"status": "ACTIVE"}, {**ACCOUNT, "trading_blocked": "false"},
                     {**ACCOUNT, "account_blocked": 0}, {**ACCOUNT, "status": None},
                     {**ACCOUNT, "trade_suspended_by_user": "false"}):
            report = self.check(body)
            self.assertEqual(report["status"], "incomplete_account_status")
            self.assertFalse(report["connection_verified"])
        self.assertEqual(self.check([])["status"], "invalid_response")

    def test_http_failures_do_not_expose_bodies(self):
        expected = {401: "authentication_failed", 403: "access_denied", 429: "rate_limited",
                    302: "redirect_refused", 500: "api_error"}
        for status, code in expected.items():
            with self.subTest(status=status):
                report = self.check({"error": ENV["ALPACA_SECRET_KEY"]}, status)
                self.assertEqual(report["status"], code)
                self.assertNotIn(ENV["ALPACA_SECRET_KEY"], json.dumps(report))

    def test_timeout_and_transport_errors_are_sanitized(self):
        for exc, code in ((TimeoutError(ENV["ALPACA_SECRET_KEY"]), "timeout"),
                          (urllib.error.URLError(socket.timeout("timeout")), "timeout"),
                          (urllib.error.URLError(ENV["ALPACA_SECRET_KEY"]), "connection_error"),
                          (RuntimeError(ENV["ALPACA_SECRET_KEY"]), "connection_error")):
            report = preflight.run_preflight(check_paper=True, environment=ENV,
                                            transport=mock.Mock(side_effect=exc))
            self.assertEqual(report["status"], code)
            self.assertNotIn(ENV["ALPACA_SECRET_KEY"], json.dumps(report))

    def test_real_transport_is_fixed_get_account_even_with_live_environment_overrides(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = json.dumps(ACCOUNT).encode()
        opener = mock.Mock()
        opener.open.return_value = response
        env = {**ENV, "APCA_API_BASE_URL": "https://api.alpaca.markets",
               "ALPACA_PAPER_TRADE": "false", "ALPACA_API_URL": "https://example.invalid/v2/orders"}
        with mock.patch.object(preflight.urllib.request, "build_opener", return_value=opener) as build:
            report = preflight.run_preflight(check_paper=True, environment=env)
        self.assertEqual(report["status"], "paper_connection_ok")
        opener.open.assert_called_once()
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://paper-api.alpaca.markets/v2/account")
        self.assertEqual(request.get_method(), "GET")
        self.assertIsNone(request.data)
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 10.0})
        self.assertIsInstance(build.call_args.args[0], preflight._NoRedirect)
        self.assertNotIn("/orders", request.full_url)

    def test_redirect_handler_never_constructs_a_followup_request(self):
        self.assertIsNone(preflight._NoRedirect().redirect_request(
            None, None, 302, "Found", {}, "https://api.alpaca.markets/v2/orders"))
        error = urllib.error.HTTPError(preflight.PAPER_ACCOUNT_URL, 302,
                                      ENV["ALPACA_SECRET_KEY"], {}, io.BytesIO(b"sensitive body"))
        opener = mock.Mock()
        opener.open.side_effect = error
        with mock.patch.object(preflight.urllib.request, "build_opener", return_value=opener):
            report = preflight.run_preflight(check_paper=True, environment=ENV)
        self.assertEqual(report["status"], "redirect_refused")
        opener.open.assert_called_once()
        self.assertNotIn(ENV["ALPACA_SECRET_KEY"], json.dumps(report))

    def test_oversized_or_invalid_payload_is_not_printed(self):
        for payload in (b"x" * (preflight.MAX_RESPONSE_BYTES + 1), b"not-json-sensitive-body"):
            response = mock.MagicMock()
            response.__enter__.return_value = response
            response.status = 200
            response.read.return_value = payload
            opener = mock.Mock()
            opener.open.return_value = response
            with mock.patch.object(preflight.urllib.request, "build_opener", return_value=opener):
                report = preflight.run_preflight(check_paper=True, environment=ENV)
            self.assertEqual(report["status"], "invalid_response")
            self.assertNotIn("sensitive-body", json.dumps(report))

    def test_cli_requires_explicit_online_flag(self):
        for argv, online in (([], False), (["--check-paper"], True)):
            output = io.StringIO()
            with mock.patch.object(preflight, "run_preflight", return_value={"status": "configuration_ready_unverified"}) as run:
                with contextlib.redirect_stdout(output):
                    self.assertEqual(preflight.main(argv), 0)
                run.assert_called_once_with(check_paper=online)
            self.assertEqual(json.loads(output.getvalue())["status"], "configuration_ready_unverified")

    def test_cli_rejects_live_url_order_and_method_controls_without_echoing_arguments(self):
        for flag in ("--url", "--endpoint", "--path", "--method", "--live", "--api-key", "--check"):
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                preflight.main([flag, "sensitive-argument-value"])
            self.assertEqual(raised.exception.code, 2)
            self.assertNotIn("sensitive-argument-value", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
