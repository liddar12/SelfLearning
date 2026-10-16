"""Default-off, server-owned capture backend configuration.

Import and selection open no resources and read only the supplied mapping.
Opening the factory constructs urllib's HTTP transport (which may consult
system proxy configuration), then checks only journal/ledger read RPCs. This is configuration
readiness, NOT a verified hosted connection, provider authentication, a working
learning loop or model adoption. Quotas must already be provisioned; their
first reservation can still fail before any private Storage write. No bucket,
quota, migration, token or environment value is created or changed here.
"""
from __future__ import annotations

import base64
import json
import re
from contextlib import ExitStack, contextmanager
from urllib.parse import urlsplit

from selflearn_core.adapters.nfl_capture_delivery_journal import NFLCaptureDeliveryJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from selflearn_core.store.supabase_store import StoreError, SupabaseStore

from .nfl_capture_intake import CaptureReceiver


APPROVED_DESTINATION_ORIGIN = "https://self-learning-beige.vercel.app"
PRODUCER_ID = "nfl2026"
DESTINATION_ID = fingerprint({"base_url": APPROVED_DESTINATION_ORIGIN})
_NAMESPACE = staging_namespace(destination_id=DESTINATION_ID, producer_id=PRODUCER_ID)
# Preserve the complete 256-bit namespace within Storage's 63-character limit.
_STORAGE_NAMESPACE = "nfl-" + base64.b32encode(bytes.fromhex(_NAMESPACE[7:])).decode("ascii").rstrip("=").lower()
_PROBE_ID = fingerprint({"kind": "nfl.capture_configuration_probe.v1", "destination_id": DESTINATION_ID,
                         "producer_id": PRODUCER_ID})
_SEQUENCE = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_JWT_PART = re.compile(r"[A-Za-z0-9_-]+\Z")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_server_credential_claim")
        result[key] = value
    return result


def _invalid_number(_value):
    raise ValueError("invalid_server_credential_claim")


def server_supabase_credentials(env):
    """Return explicit server origin/key after a PURE, bounded shape check.

    A legacy ``role=service_role`` claim is not signature or grant verification.
    If the new-secret variable is present but invalid (even empty/None), never
    fall back to a legacy key. Remove it to select the legacy key explicitly.
    This helper is reusable by the journal selector without importing the app.
    """
    try:
        origin = env.get("SUPABASE_URL", "")
        if (type(origin) is not str or not 1 <= len(origin) <= 2048
                or not origin.isascii() or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in origin)):
            raise ValueError("invalid_server_origin")
        # Match the existing adapter's plain-HTTPS-origin rules, without
        # creating its opener/ProxyHandler or consulting process/system proxy
        # configuration during PURE selection. Accessing .port also rejects
        # malformed/out-of-range ports; no DNS or connection occurs.
        parts = urlsplit(origin)
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or parts.password or parts.query or parts.fragment or parts.path not in ("", "/")
                or parts.port == 0):
            raise ValueError("invalid_server_origin")
        if "SUPABASE_SECRET_KEY" in env:
            key = env["SUPABASE_SECRET_KEY"]
            if (type(key) is not str or not len("sb_secret_") < len(key) <= 8192
                    or not key.startswith("sb_secret_") or any(not 33 <= ord(char) <= 126 for char in key)):
                raise ValueError("invalid_server_secret")
        else:
            key = env.get("SUPABASE_SERVICE_ROLE_KEY", "")
            if type(key) is not str or not 1 <= len(key) <= 8192:
                raise ValueError("invalid_server_service_key")
            parts = key.split(".")
            if len(parts) != 3 or any(not _JWT_PART.fullmatch(part) for part in parts):
                raise ValueError("invalid_server_service_key")
            claims = parts[1]
            payload = json.loads(base64.b64decode(claims + "=" * (-len(claims) % 4),
                                altchars=b"-_", validate=True).decode("utf-8"),
                                object_pairs_hook=_unique_object, parse_constant=_invalid_number)
            if type(payload) is not dict or type(payload.get("role")) is not str or payload["role"] != "service_role":
                raise ValueError("invalid_server_service_role")
        return origin.rstrip("/"), key
    except Exception:
        # Never surface URL/key/claims/provider diagnostics. Cancellation and
        # other BaseException subclasses are deliberately not intercepted.
        raise ValueError("invalid_server_supabase_configuration") from None


def _selection(backend, configured, valid, readiness):
    return {"backend": backend, "capture_configured": configured, "configuration_valid": valid,
            "readiness": readiness, "production_connection_verified": False,
            "working_learning_loop": False, "model_adoption": "unverified"}


def _unavailable():
    raise StoreError(503)


def select_capture_factory(env, injected=None):
    """Return a receiver context factory and honest STATIC configuration status.

    Explicit injection is trusted test/application composition. Otherwise only
    the exact ``SELFLEARN_CAPTURE_BACKEND=supabase`` selector opts in. Existing
    store/journal settings or any token alone never enable capture intake.
    No caller-provided destination, producer or namespace is consulted.
    """
    if injected is not None:
        if not callable(injected):
            return _unavailable, _selection("invalid", False, False, "invalid_configuration")
        return injected, _selection("injected", True, None, "injected_unverified")
    selection = env.get("SELFLEARN_CAPTURE_BACKEND", "")
    if selection is None or (type(selection) is str and selection in ("", "disabled")):
        return None, _selection("disabled", False, True, "disabled")
    if type(selection) is not str or selection != "supabase":
        return _unavailable, _selection("invalid", False, False, "invalid_configuration")
    try:
        origin, key = server_supabase_credentials(env)
        bucket = env.get("SELFLEARN_CAPTURE_EVIDENCE_BUCKET")
        # Reuse the private adapter's exact bucket/namespace validators; there
        # is no Storage request until an actual stage/raw-read operation.
        SupabaseEvidenceStore(_unavailable, bucket=bucket, namespace=_STORAGE_NAMESPACE)
    except Exception:
        return _unavailable, _selection("supabase", False, False, "invalid_configuration")

    @contextmanager
    def capture_factory():
        with ExitStack() as stack:
            try:
                store = SupabaseStore(origin, key)
                journal = stack.enter_context(NFLCaptureDeliveryJournal(origin, key, transport=store.request))
                status = journal.status()
                if (type(status) is not dict or set(status) != {"schema_version", "backend", "watermark"}
                        or type(status["schema_version"]) is not int or status["schema_version"] != 1
                        or type(status["backend"]) is not str or status["backend"] != "postgres"
                        or type(status["watermark"]) is not str or not _SEQUENCE.fullmatch(status["watermark"])):
                    raise StoreError(503)
                # Read-only missing-ID probe verifies the additive delivery
                # RPC exists and can be executed. It reserves/writes nothing.
                if journal.get_capture_delivery(_PROBE_ID) is not None:
                    raise StoreError(503)
                receiver = CaptureReceiver(journal,
                    SupabaseEvidenceStore(store.request, bucket=bucket, namespace=_STORAGE_NAMESPACE),
                    CaptureStagingQuota(store.request, namespace_id=_NAMESPACE),
                    destination_id=DESTINATION_ID, producer_id=PRODUCER_ID)
            except Exception:
                raise StoreError(503) from None
            # Operation errors retain their domain types/status for the route's
            # existing sanitized boundary; do not turn conflicts into success.
            yield receiver

    return capture_factory, _selection("supabase", True, True, "configured_unverified")
