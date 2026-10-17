"""Offline private-storage misconfiguration and readback acceptance checks."""
import hashlib

import pytest
from fastapi.testclient import TestClient

from services.web import private_uploads as private
from services.web.application import create_app
from selflearn_core.store.supabase_store import StoreError
from .fakes import FakeStore, TEST_KEY
from .test_hosted_api import PNG

ASSET = hashlib.sha256(PNG).hexdigest() + ".png"


def client(store):
    result = TestClient(create_app(store=store, env={"SELFLEARN_OPERATOR_TOKEN": TEST_KEY}))
    result.headers["Authorization"] = "Bearer " + TEST_KEY
    return result


@pytest.mark.parametrize("bucket", [None, [], {}, {"id": "other", "public": False},
    {"id": "selflearn-intake", "public": 0}, {"id": "selflearn-intake", "public": "false"},
    {"id": "selflearn-intake", "public": True}])
def test_public_or_unverified_bucket_never_lists_uploads_downloads_or_accepts_image(bucket):
    store = FakeStore()
    store.private_bucket = bucket
    store.objects[ASSET] = PNG
    with client(store) as api:
        assert api.get("/api/uploads").status_code == 503
        assert api.get("/api/uploads/" + ASSET).status_code == 503
        response = api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
        assert response.status_code == 503
        assert "stored privately" not in response.text
    assert store.objects == {ASSET: PNG}


@pytest.mark.parametrize("duplicate_status", [400, 409])
def test_duplicate_replay_requires_actual_matching_authenticated_readback(duplicate_status):
    class Duplicate(FakeStore):
        def __init__(self):
            super().__init__()
            self.objects[ASSET] = PNG
            self.paths = []

        def request(self, method, path, **kwargs):
            self.paths.append((method, path, kwargs))
            if method == "POST" and path.startswith("/storage/v1/object/selflearn-intake/"):
                raise StoreError(duplicate_status)
            return super().request(method, path, **kwargs)

    store = Duplicate()
    with client(store) as api:
        response = api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
        assert response.status_code == 200
        assert response.json()["learning_evidence"] is False
        assert any(path == "/storage/v1/object/authenticated/selflearn-intake/" + ASSET for _, path, _ in store.paths)
        assert all(set(kwargs) <= {"raw", "content_type", "binary"} for _, _, kwargs in store.paths)
        store.objects[ASSET] = b"corrupted"
        assert api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).status_code == 503
        assert api.get("/api/uploads/" + ASSET).status_code == 503


def test_upload_ack_without_readback_or_private_acl_cannot_report_saved():
    class Corrupt(FakeStore):
        def request(self, method, path, **kwargs):
            result = super().request(method, path, **kwargs)
            if method == "POST" and path.startswith("/storage/v1/object/selflearn-intake/"):
                self.objects[ASSET] = b"not exact uploaded bytes"
            return result
    with client(Corrupt()) as api:
        assert api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).status_code == 503

    class AclChanged(FakeStore):
        def request(self, method, path, **kwargs):
            result = super().request(method, path, **kwargs)
            if method == "POST" and path.startswith("/storage/v1/object/selflearn-intake/"):
                self.private_bucket["public"] = True
            return result
    with client(AclChanged()) as api:
        assert api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).status_code == 503


def test_lost_post_reply_no_false_success_then_idempotent_replay_recovers():
    class Lost(FakeStore):
        broken = True
        def request(self, method, path, **kwargs):
            result = super().request(method, path, **kwargs)
            if self.broken and method == "POST" and path.startswith("/storage/v1/object/selflearn-intake/"):
                raise TimeoutError("SECRET_provider_diagnostics")
            return result
    store = Lost()
    with client(store) as api:
        response = api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"})
        assert response.status_code == 503 and "SECRET" not in response.text
        store.broken = False
        assert api.post("/api/uploads", content=PNG, headers={"Content-Type": "image/png"}).status_code == 200
    assert len(store.objects) == 1


def test_invalid_private_ids_bytes_hashes_or_mime_never_reach_storage():
    class NoCalls:
        def request(self, *a, **kw):
            raise AssertionError("storage must not be reached")
    for value in (None, "../secret", "https://other/" + ASSET):
        with pytest.raises(ValueError):
            private.read_upload(NoCalls(), value)
    for asset, raw, mime in ((ASSET, b"wrong", "image/png"), (ASSET, PNG, "text/html"),
                             (ASSET, bytearray(PNG), "image/png"), (ASSET, b"", "image/png")):
        with pytest.raises(ValueError):
            private.stage_upload(NoCalls(), asset, raw, mime)


def test_transport_status_and_error_messages_are_sanitized():
    class Bad:
        def __init__(self, failure):
            self.failure = failure
        def request(self, *a, **kw):
            raise self.failure
    for failure, status in ((StoreError(403), 403), (StoreError(True), 503), (RuntimeError("SECRET"), 503)):
        failure.args = ("SECRET",)
        with pytest.raises(StoreError) as caught:
            private.require_private(Bad(failure))
        assert caught.value.status == status and "SECRET" not in str(caught.value)
