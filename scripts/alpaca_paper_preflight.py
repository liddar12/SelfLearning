#!/usr/bin/env python3
"""Check Alpaca PAPER connectivity without placing or changing anything.

Default: validate credential presence locally, with no network requests.
Explicit --check-paper: GET the official paper account endpoint once.

Only the two credential environment variables are used. There is deliberately
no configurable URL, method, account identifier, order path, or live mode.
Redirects are refused. Remote bodies, exception messages, credentials and
account/financial values are never included in the output.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from typing import Any


PAPER_ACCOUNT_URL = "https://paper-api.alpaca.markets/v2/account"
TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 65_536
CREDENTIAL_NAMES = ("ALPACA_API_KEY", "ALPACA_SECRET_KEY")
Transport = Callable[[Mapping[str, str], float], tuple[int, Any]]


class _InvalidResponse(Exception):
    """Internal signal; never carries a remote response or credential."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def paper_account_transport(headers: Mapping[str, str], timeout: float) -> tuple[int, Any]:
    """One read-only request. Do not add a URL or method parameter here."""
    request = urllib.request.Request(
        PAPER_ACCOUNT_URL, method="GET", headers=dict(headers)
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            status = response.status
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        # Do not parse or return error bodies; they may echo sensitive inputs.
        status = exc.code
        exc.close()
        return status, None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise _InvalidResponse()
    try:
        return status, json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _InvalidResponse() from None


def _report(status: str, *, network_checked: bool = False,
            connection_verified: bool = False, **details: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "broker": "alpaca",
        "environment": "paper",
        "endpoint": PAPER_ACCOUNT_URL,
        "status": status,
        "network_checked": network_checked,
        "connection_verified": connection_verified,
        "orders_submitted": 0,
        "options_adapter_supported": False,
        "adapter_scope": "equities_etfs_only",
        **details,
    }


def _placeholder(value: str) -> bool:
    lowered = value.lower()
    return (
        lowered in {"changeme", "change-me", "replace-me", "your_api_key",
                    "your_secret_key", "your_alpaca_api_key", "your_alpaca_secret_key"}
        or (value.startswith("<") and value.endswith(">"))
    )


def run_preflight(*, check_paper: bool = False,
                  environment: Mapping[str, str] | None = None,
                  transport: Transport | None = None) -> dict[str, Any]:
    """Return only fixed status codes and non-secret configuration field names.

    Injection exists for isolated tests. A supplied transport is never invoked
    unless check_paper=True and local credential validation succeeds.
    """
    env = os.environ if environment is None else environment
    credentials = {name: env.get(name, "") for name in CREDENTIAL_NAMES}
    missing = [name for name, value in credentials.items() if not value]
    if missing:
        return _report("missing_credentials", missing_environment=missing)
    invalid = [
        name for name, value in credentials.items()
        if not isinstance(value, str) or any(char.isspace() for char in value)
        or _placeholder(value)
    ]
    if invalid:
        return _report("invalid_configuration", invalid_environment=invalid)
    if not check_paper:
        return _report("configuration_ready_unverified")

    headers = {
        "APCA-API-KEY-ID": credentials["ALPACA_API_KEY"],
        "APCA-API-SECRET-KEY": credentials["ALPACA_SECRET_KEY"],
        "Accept": "application/json",
    }
    try:
        http_status, account = (transport or paper_account_transport)(headers, TIMEOUT_SECONDS)
    except (TimeoutError, socket.timeout):
        return _report("timeout", network_checked=True)
    except urllib.error.URLError as exc:
        status = "timeout" if isinstance(exc.reason, (TimeoutError, socket.timeout)) else "connection_error"
        return _report(status, network_checked=True)
    except _InvalidResponse:
        return _report("invalid_response", network_checked=True)
    except Exception:
        # Never serialize an exception: broker/client errors can include secrets.
        return _report("connection_error", network_checked=True)

    if http_status == 401:
        return _report("authentication_failed", network_checked=True)
    if http_status == 403:
        return _report("access_denied", network_checked=True)
    if http_status == 429:
        return _report("rate_limited", network_checked=True)
    if isinstance(http_status, int) and 300 <= http_status < 400:
        return _report("redirect_refused", network_checked=True)
    if http_status != 200:
        return _report("api_error", network_checked=True)

    # Only these status fields are inspected; no account or financial values
    # are copied into the report. Missing/wrong-type fields fail closed.
    if not isinstance(account, Mapping):
        return _report("invalid_response", network_checked=True)
    if not isinstance(account.get("status"), str) or not account["status"]:
        return _report("incomplete_account_status", network_checked=True)
    required_flags = ("account_blocked", "trading_blocked")
    if any(type(account.get(name)) is not bool for name in required_flags):
        return _report("incomplete_account_status", network_checked=True)
    if "trade_suspended_by_user" in account and type(account["trade_suspended_by_user"]) is not bool:
        return _report("incomplete_account_status", network_checked=True)

    restrictions = []
    if account["status"] != "ACTIVE":
        restrictions.append("account_not_active")
    for name in (*required_flags, "trade_suspended_by_user"):
        if account.get(name) is True:
            restrictions.append(name)
    if restrictions:
        return _report("paper_account_restricted", network_checked=True,
                       connection_verified=True, restrictions=restrictions)
    return _report("paper_connection_ok", network_checked=True,
                   connection_verified=True)


class _SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse normally echoes unknown arguments, which could be secrets.
        self.print_usage(sys.stderr)
        self.exit(2, "Unsupported arguments. Use --help; credentials belong in environment variables.\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--check-paper", action="store_true",
        help="explicitly permit one authenticated GET to Alpaca's fixed paper account endpoint",
    )
    args = parser.parse_args(argv)
    report = run_preflight(check_paper=args.check_paper)
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] in {"configuration_ready_unverified", "paper_connection_ok"}:
        return 0
    if report["status"] in {"missing_credentials", "invalid_configuration"}:
        return 2
    if report["status"] == "paper_account_restricted":
        return 3
    return 4


if __name__ == "__main__":
    raise SystemExit(main())
