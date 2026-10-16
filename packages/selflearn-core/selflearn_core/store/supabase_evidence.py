"""Opt-in private raw-evidence staging, not a learning acceptance boundary.

The caller supplies an authenticated server-side request callable, bucket and
namespace. Construction performs no I/O and reads no environment variables.
No bucket creation, signed/public URLs, overwrite, deletion or route mounting.
Every operation verifies the bucket is private; upload success additionally
requires exact authenticated readback. Provider ACLs can change later: this is
not an object lock, producer authentication or a perpetual privacy guarantee.

Compose ``chunk_stream(source, store.put_chunk)`` to stage a bounded object.
Partial failures can leave orphan chunks. Atomic tenant quotas, retention/GC,
scoped intake authorization and a durable group-result ledger remain required
before enabling an external intake endpoint. Never expose a service key.
"""
from __future__ import annotations

import hashlib
import re

from .. import evidence_chunks as evidence
from .supabase_store import StoreError

_BUCKET = re.compile(r"[a-z][a-z0-9-]{2,62}\Z")
_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9_-]{0,62}\Z")


class SupabaseEvidenceStore:
    """Immutable-by-client, hash-addressed chunks in an explicitly private bucket.

    ``request`` follows SupabaseStore.request, including its bounded response
    reads and redirect refusal. An injected replacement is a trusted transport,
    not untrusted user input. No request is made during initialization.
    """

    def __init__(self, request, *, bucket: str, namespace: str):
        if not callable(request):
            raise ValueError("invalid_evidence_transport")
        if type(bucket) is not str or not _BUCKET.fullmatch(bucket):
            raise ValueError("invalid_evidence_bucket")
        if type(namespace) is not str or not _NAMESPACE.fullmatch(namespace):
            raise ValueError("invalid_evidence_namespace")
        self._request = request
        self._bucket = bucket
        self._namespace = namespace

    def _call(self, method, path, **kwargs):
        try:
            return self._request(method, path, **kwargs)
        except StoreError as exc:
            # Reconstruct even our error type: a custom transport could attach
            # sensitive text, subclass messages or an invalid status.
            status = exc.status if type(exc.status) is int and 400 <= exc.status <= 599 else 503
            raise StoreError(status) from None
        except Exception:
            raise StoreError(503) from None

    def _private(self):
        row = self._call("GET", "/storage/v1/bucket/" + self._bucket)
        if (type(row) is not dict or row.get("id") != self._bucket
                or type(row.get("public")) is not bool or row["public"]):
            raise StoreError(502)

    def _key(self, chunk_id):
        digest = evidence._digest(chunk_id)[7:]
        return self._namespace + "/chunks/" + digest[:2] + "/" + digest + ".bin"

    def _read(self, chunk_id, key):
        raw = self._call("GET", "/storage/v1/object/authenticated/" + self._bucket + "/" + key,
                         binary=True)
        if (type(raw) is not bytes or not 1 <= len(raw) <= evidence.MAX_CHUNK_BYTES
                or "sha256:" + hashlib.sha256(raw).hexdigest() != chunk_id):
            raise StoreError(502)
        return raw

    def put_chunk(self, dto: dict) -> dict:
        """Stage exact bytes; legacy400/new409 need the same exact readback.

        The POST response never serves as evidence that matching bytes exist.
        A lost POST response returns no success; a subsequent stable-ID replay
        can recover if authenticated readback verifies the original bytes.
        """
        raw = evidence.verify_chunk(dto)
        chunk_id = "sha256:" + hashlib.sha256(raw).hexdigest()
        key = self._key(chunk_id)
        self._private()
        try:
            self._call("POST", "/storage/v1/object/" + self._bucket + "/" + key,
                       raw=raw, content_type="application/octet-stream")
        except StoreError as exc:
            if exc.status not in (400, 409):
                raise
        # No x-upsert header, no trusting a duplicate error or upload ACK.
        if self._read(chunk_id, key) != raw:
            raise StoreError(502)
        return {"version": 1, "kind": "selflearn.evidence_stage.v1",
                "scope": evidence.EVIDENCE_SCOPE, "bucket": self._bucket,
                "key": key, "chunk_id": chunk_id, "raw_bytes": len(raw),
                "learning_accepted": False, "durable_ack": False}

    def get_chunk(self, chunk_id: str) -> dict:
        """Authenticated chunk integrity only; no label or learning ACK."""
        key = self._key(chunk_id)
        self._private()
        return evidence.make_chunk(self._read(chunk_id, key))

    def object_chunks(self, pointer: dict, *, max_object_bytes=evidence.DEFAULT_MAX_OBJECT_BYTES):
        """Return canonical raw frames; completion requires consuming through EOF.

        Metadata is validated/frozen at the call, before callbacks/iteration.
        Earlier yielded bytes are STAGING: a later whole-object failure cannot
        retract them. No full-object buffer or success receipt is returned.
        """
        frozen = evidence._pointer(pointer, evidence._limit(max_object_bytes))

        def frames():
            self._private()
            digest, total = hashlib.sha256(), 0
            for chunk_id, size in frozen.chunks:
                raw = self._read(chunk_id, self._key(chunk_id))
                if len(raw) != size:
                    raise StoreError(502)
                digest.update(raw)
                total += len(raw)
                yield raw
            if total != frozen.raw_bytes or "sha256:" + digest.hexdigest() != frozen.object_id:
                raise StoreError(502)

        return frames()
