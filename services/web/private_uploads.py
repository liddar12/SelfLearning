"""Private screenshot storage checks; never a learning-evidence authority."""
import hashlib
import re

from selflearn_core.store.supabase_store import StoreError

BUCKET = "selflearn-intake"
ASSET = re.compile(r"[a-f0-9]{64}\.(png|jpg|webp)\Z")
MAX_BYTES = 3 * 1024 * 1024


def _call(store, method, path, **kwargs):
    try:
        return store.request(method, path, **kwargs)
    except StoreError as exc:
        status = exc.status if type(exc.status) is int and 400 <= exc.status <= 599 else 503
        raise StoreError(status) from None
    except Exception:
        raise StoreError(503) from None


def require_private(store):
    row = _call(store, "GET", "/storage/v1/bucket/" + BUCKET)
    if (type(row) is not dict or row.get("id") != BUCKET
            or type(row.get("public")) is not bool or row["public"]):
        raise StoreError(502)


def read_upload(store, asset_id):
    if type(asset_id) is not str or not ASSET.fullmatch(asset_id):
        raise ValueError("invalid_private_asset_id")
    require_private(store)
    raw = _call(store, "GET", "/storage/v1/object/authenticated/" + BUCKET + "/" + asset_id, binary=True)
    if (type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES
            or hashlib.sha256(raw).hexdigest() != asset_id.split(".")[0]):
        raise StoreError(502)
    return raw


def stage_upload(store, asset_id, raw, mime):
    if (type(asset_id) is not str or not ASSET.fullmatch(asset_id)
            or type(raw) is not bytes or not 1 <= len(raw) <= MAX_BYTES
            or hashlib.sha256(raw).hexdigest() != asset_id.split(".")[0]
            or mime not in ("image/png", "image/jpeg", "image/webp")):
        raise ValueError("invalid_private_upload")
    require_private(store)
    try:
        _call(store, "POST", "/storage/v1/object/" + BUCKET + "/" + asset_id,
              raw=raw, content_type=mime)
    except StoreError as exc:
        # Both legacy400 and new409 duplicate responses need authenticated
        # readback. Other400 errors are not successes if the object is absent.
        if exc.status not in (400, 409):
            raise
    if read_upload(store, asset_id) != raw:
        raise StoreError(502)
    # This verifies privacy now, not an immutable ACL or future retention.
    return {"id": asset_id, "status": "stored privately; awaiting manual review",
            "analysis_performed": False, "learning_evidence": False}
