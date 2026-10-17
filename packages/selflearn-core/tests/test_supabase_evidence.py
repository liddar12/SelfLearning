"""Offline transport contract; never contacts Supabase or provisions storage."""
import copy
import hashlib
import io
import unittest

from selflearn_core import evidence_chunks as ev
from selflearn_core.store.supabase_evidence import SupabaseEvidenceStore
from selflearn_core.store.supabase_store import StoreError


class FakeStorage:
    def __init__(self):
        self.calls, self.objects = [], {}
        self.bucket = {"id": "private-evidence", "public": False}
        self.duplicate = 409
        self.post_error = None
        self.get_override = None
        self.callback = None

    def __call__(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if self.callback:
            self.callback(method, path)
        if path == "/storage/v1/bucket/private-evidence":
            return self.bucket
        if method == "POST":
            key = path.removeprefix("/storage/v1/object/")
            if key in self.objects:
                raise StoreError(self.duplicate)
            self.objects[key] = kwargs["raw"]
            if self.post_error:
                raise self.post_error
            return {"untrusted_ack": "not used"}
        key = path.removeprefix("/storage/v1/object/authenticated/")
        if self.get_override is not None:
            return self.get_override
        if key not in self.objects:
            raise StoreError(404)
        return self.objects[key]


class SupabaseEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.transport = FakeStorage()
        self.store = SupabaseEvidenceStore(self.transport, bucket="private-evidence", namespace="nfl-2026")
        self.dto = ev.make_chunk(b"verified raw bytes")

    def test_constructor_no_io_and_strict_explicit_configuration(self):
        self.assertEqual([], self.transport.calls)
        for bucket in (None, 3, "ab", "bad/name", "bad..name", "bad?x=1", "https://bad", "UPPER", "a" * 64):
            with self.subTest(bucket=bucket), self.assertRaisesRegex(ValueError, "invalid_evidence_bucket"):
                SupabaseEvidenceStore(self.transport, bucket=bucket, namespace="safe")
        for namespace in (None, 3, "", "../x", "a/b", "a.b", "a?b", "UPPER", "a" * 64):
            with self.subTest(namespace=namespace), self.assertRaisesRegex(ValueError, "invalid_evidence_namespace"):
                SupabaseEvidenceStore(self.transport, bucket="private-evidence", namespace=namespace)
        with self.assertRaisesRegex(ValueError, "invalid_evidence_transport"):
            SupabaseEvidenceStore(None, bucket="private-evidence", namespace="safe")
        class Text(str):
            pass
        with self.assertRaises(ValueError):
            SupabaseEvidenceStore(self.transport, bucket=Text("private-evidence"), namespace="safe")

    def test_upload_private_hash_path_exact_readback_no_overwrite_or_learning_ack(self):
        result = self.store.put_chunk(self.dto)
        digest = self.dto["chunk_id"][7:]
        key = f"nfl-2026/chunks/{digest[:2]}/{digest}.bin"
        self.assertEqual(key, result["key"])
        self.assertEqual("uploaded_only", result["scope"])
        self.assertIs(result["learning_accepted"], False)
        self.assertIs(result["durable_ack"], False)
        self.assertEqual(["GET", "POST", "GET"], [c[0] for c in self.transport.calls])
        post = self.transport.calls[1]
        self.assertEqual("/storage/v1/object/private-evidence/" + key, post[1])
        self.assertEqual({"raw": b"verified raw bytes", "content_type": "application/octet-stream"}, post[2])
        self.assertEqual(self.dto, self.store.get_chunk(self.dto["chunk_id"]))
        self.assertNotIn("http", str(result))

    def test_each_operation_rechecks_typed_private_bucket_before_access(self):
        self.store.put_chunk(self.dto)
        pointer = ev.chunk_stream(io.BytesIO(b"verified raw bytes"), lambda _: None)
        for bad in (None, [], {}, {"id": "other", "public": False},
                    {"id": "private-evidence", "public": 0},
                    {"id": "private-evidence", "public": "false"},
                    {"id": "private-evidence", "public": True}):
            self.transport.bucket = bad
            for operation in (lambda: self.store.put_chunk(self.dto),
                              lambda: self.store.get_chunk(self.dto["chunk_id"]),
                              lambda: list(self.store.object_chunks(pointer))):
                before = len(self.transport.calls)
                with self.assertRaises(StoreError) as caught:
                    operation()
                self.assertEqual(502, caught.exception.status)
                self.assertEqual(before + 1, len(self.transport.calls))

    def test_legacy400_and_new409_replays_require_matching_authenticated_bytes(self):
        expected = self.store.put_chunk(self.dto)
        for code in (400, 409):
            self.transport.duplicate = code
            self.assertEqual(expected, self.store.put_chunk(self.dto))
            self.transport.get_override = b"corrupted"
            with self.assertRaises(StoreError) as caught:
                self.store.put_chunk(self.dto)
            self.assertEqual(502, caught.exception.status)
            self.transport.get_override = None
        self.transport.duplicate = 403
        before = len(self.transport.calls)
        with self.assertRaises(StoreError) as caught:
            self.store.put_chunk(self.dto)
        self.assertEqual(403, caught.exception.status)
        self.assertEqual(before + 2, len(self.transport.calls))

    def test_arbitrary400_is_not_treated_as_duplicate_without_readback(self):
        def request(method, path, **kwargs):
            if "/bucket/" in path:
                return {"id": "private-evidence", "public": False}
            raise StoreError(400 if method == "POST" else 404)
        store = SupabaseEvidenceStore(request, bucket="private-evidence", namespace="safe")
        with self.assertRaises(StoreError) as caught:
            store.put_chunk(self.dto)
        self.assertEqual(404, caught.exception.status)

    def test_malformed_upload_ack_ignored_and_success_needs_exact_readback(self):
        self.transport.get_override = "not binary"
        for value in ("not binary", b"", b"wrong bytes", b"a" * (ev.MAX_CHUNK_BYTES + 1)):
            self.transport.get_override = value
            with self.assertRaises(StoreError) as caught:
                self.store.put_chunk(self.dto)
            self.assertEqual(502, caught.exception.status)

    def test_lost_post_reply_returns_no_ack_stable_retry_recovers(self):
        self.transport.post_error = TimeoutError("TOKEN_must_not_leak")
        with self.assertRaises(StoreError) as caught:
            self.store.put_chunk(self.dto)
        self.assertEqual(503, caught.exception.status)
        self.assertNotIn("TOKEN", str(caught.exception))
        self.transport.post_error = None
        self.assertFalse(self.store.put_chunk(self.dto)["learning_accepted"])
        self.assertEqual(1, len(self.transport.objects))

    def test_transport_exceptions_sanitized_and_status_bounded(self):
        for failure, status in ((RuntimeError("TOKEN"), 503), (StoreError(401), 401),
                                (StoreError(True), 503), (StoreError(200), 503),
                                (StoreError(999), 503)):
            failure.args = ("TOKEN",)
            def request(*args, **kwargs):
                raise failure
            store = SupabaseEvidenceStore(request, bucket="private-evidence", namespace="safe")
            with self.assertRaises(StoreError) as caught:
                store.get_chunk(self.dto["chunk_id"])
            self.assertEqual(status, caught.exception.status)
            self.assertNotIn("TOKEN", str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            self.assertTrue(caught.exception.__suppress_context__)

    def test_invalid_dto_and_digest_rejected_before_io(self):
        bad = copy.deepcopy(self.dto)
        bad["data"] = "invalid"
        with self.assertRaises(ev.EvidenceIntegrityError):
            self.store.put_chunk(bad)
        for digest in (None, True, "sha256:" + "A" * 64, "../../other", self.dto["chunk_id"] + "?x"):
            with self.assertRaises(ev.EvidenceIntegrityError):
                self.store.get_chunk(digest)
        self.assertEqual([], self.transport.calls)

    def test_pointer_stream_freezes_metadata_and_verifies_all_canonical_chunks(self):
        raw = b"a" * ev.MAX_CHUNK_BYTES + b"tail"
        pointer = ev.chunk_stream(io.BytesIO(raw), self.store.put_chunk)
        frames = self.store.object_chunks(pointer)
        pointer["chunks"].clear()
        pointer["object_id"] = "sha256:" + "0" * 64
        self.assertEqual(raw, b"".join(frames))
        self.assertEqual(2, len(self.transport.objects))

    def test_later_whole_object_failure_cannot_be_a_complete_success(self):
        pointer = ev.chunk_stream(io.BytesIO(b"a" * ev.MAX_CHUNK_BYTES + b"tail"), self.store.put_chunk)
        pointer["object_id"] = "sha256:" + "0" * 64
        frames = self.store.object_chunks(pointer)
        self.assertEqual(ev.MAX_CHUNK_BYTES, len(next(frames)))
        self.assertEqual(b"tail", next(frames))
        with self.assertRaises(StoreError) as caught:
            next(frames)
        self.assertEqual(502, caught.exception.status)

    def test_empty_pointer_bound_and_bad_layout(self):
        pointer = ev.chunk_stream(io.BytesIO(b""), lambda _: None)
        self.assertEqual([], list(self.store.object_chunks(pointer)))
        with self.assertRaises(ev.EvidenceIntegrityError):
            self.store.object_chunks(pointer, max_object_bytes=True)
        pointer["raw_bytes"] = 1
        with self.assertRaises(ev.EvidenceIntegrityError):
            self.store.object_chunks(pointer)

    def test_upload_and_stream_immune_to_callback_metadata_mutation(self):
        raw = b"verified raw bytes"
        self.transport.callback = lambda *_: self.dto.update(chunk_id="changed", raw_bytes=99, data="changed")
        result = self.store.put_chunk(self.dto)
        self.assertEqual("sha256:" + hashlib.sha256(raw).hexdigest(), result["chunk_id"])
        self.assertEqual(len(raw), result["raw_bytes"])


if __name__ == "__main__":
    unittest.main()
