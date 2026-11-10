"""Server-observed public pool bytes; no caller URLs, credentials or timestamps.

This proves HTTP retrievability of one exact pool, not that a browser displayed
every rung. Production origin is explicit operator configuration, never a
guessed domain. Socket limits are bounded I/O; the host still owns hard timeout.
"""
from __future__ import annotations

from datetime import datetime, timezone
import ssl
import time
import urllib.error
import urllib.request

from selflearn_core.adapters.nfl_publication import validate_policy

MAX_BODY_BYTES = 3 * 1024 * 1024
REQUEST_TIMEOUT = 12
OBSERVATION_BUDGET = 20


class PublicationUnavailable(OSError):
    """Bounded machine reason; never expose provider bodies or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise PublicationUnavailable("publication_redirect_refused")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def fetch_public_pool(url):
    """Anonymous TLS GET with no proxy, redirect, cookie or auth inheritance."""
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()))
    request = urllib.request.Request(url, headers={
        "Accept": "application/json", "Accept-Encoding": "identity",
        "User-Agent": "SelfLearning-Publication-Verifier/1"}, method="GET")
    start = time.monotonic()
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT) as response:
            chunks, length = [], 0
            while True:
                if time.monotonic() - start >= OBSERVATION_BUDGET:
                    raise PublicationUnavailable("publication_observation_timeout")
                chunk = response.read1(min(65536, MAX_BODY_BYTES + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > MAX_BODY_BYTES:
                    raise PublicationUnavailable("publication_body_too_large")
            return response.status, dict(response.headers), b"".join(chunks), response.geturl()
    except (OSError, ValueError, urllib.error.URLError):
        raise PublicationUnavailable("publication_fetch_unavailable") from None


class PublicPoolVerifier:
    def __init__(self, origin, *, transport=None, clock=utc_now, monotonic=time.monotonic):
        self.policy = {"version": 1, "kind": "nfl.pool_http_publication.v1",
                       "origin": origin, "path": "/data/leg_pool.json"}
        validate_policy(self.policy)
        self.url = origin + self.policy["path"]
        self.transport = transport or fetch_public_pool
        self.clock, self.monotonic = clock, monotonic

    def observe(self):
        start, begun = self.monotonic(), self.clock()
        try:
            status, headers, raw, final_url = self.transport(self.url)
            finished = self.clock()
            if self.monotonic() - start >= OBSERVATION_BUDGET:
                raise PublicationUnavailable("publication_observation_timeout")
            if type(status) is not int or status != 200 or final_url != self.url:
                raise PublicationUnavailable("publication_http_refused")
            if not isinstance(headers, dict) or type(raw) is not bytes or not 0 < len(raw) <= MAX_BODY_BYTES:
                raise PublicationUnavailable("publication_body_refused")
            normalized = {str(k).lower(): str(v).strip().lower() for k, v in headers.items()}
            if normalized.get("content-encoding", "identity") not in ("", "identity"):
                raise PublicationUnavailable("publication_encoding_refused")
            if normalized.get("content-type", "").split(";", 1)[0] != "application/json":
                raise PublicationUnavailable("publication_content_type_refused")
            return {"version": 1, "policy": dict(self.policy),
                    "observed_started_utc": begun, "observed_completed_utc": finished,
                    "http_status": status, "body_utf8": raw.decode("utf-8", errors="strict")}
        except PublicationUnavailable:
            raise
        except Exception:
            raise PublicationUnavailable("publication_observation_unavailable") from None


def select_verifier(env, transport=None):
    if env.get("SELFLEARN_PUBLICATION_MODE") != "record":
        return None
    try:
        return PublicPoolVerifier(env.get("SELFLEARN_NFL_PUBLICATION_ORIGIN"), transport=transport)
    except (ValueError, TypeError):
        return None
