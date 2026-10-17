"""Pure, bounded integrity checks for private evidence staging.

``uploaded_only`` is an allowed-use marker, NOT proof of an upload, durable
storage, authentication, provider provenance, correct clocks or a learning label.
The module opens no network/filesystem/database resources; supplied reads and
callbacks may perform caller-owned I/O.

Callbacks may stage bytes before a later digest/size/source failure. They must
not publish labels or treat partial execution as a commit; this module provides
neither rollback nor exactly-once delivery. A future HTTP boundary must enforce
its actual incoming wire-byte limit independently: validating an already parsed
dict cannot establish its original whitespace or HTTP body size. Exceptions from
trusted source/fetch/sink callbacks propagate unchanged; a future transport must
sanitize them before logging or returning a response.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import json
import re

MAX_CHUNK_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 3 * 1024 * 1024
DEFAULT_MAX_OBJECT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_OBJECTS = 128
EVIDENCE_SCOPE = "uploaded_only"
CHUNK_KIND = "selflearn.evidence_chunk.v1"
OBJECT_KIND = "selflearn.evidence_object.v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_CHUNK_KEYS = {"version", "kind", "scope", "chunk_id", "raw_bytes", "encoding", "data"}
_POINTER_KEYS = {"version", "kind", "scope", "object_id", "raw_bytes", "chunks"}


class EvidenceIntegrityError(ValueError):
    """A bounded machine code, never untrusted bytes or callback diagnostics."""


@dataclass(frozen=True)
class VerifiedObject:
    """Completed integrity check only; not an upload or durable acknowledgement."""

    object_id: str
    raw_bytes: int
    chunk_count: int
    scope: str = EVIDENCE_SCOPE


@dataclass(frozen=True)
class _Pointer:
    object_id: str
    raw_bytes: int
    chunks: tuple[tuple[str, int], ...]


def _hash(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _digest(value):
    if type(value) is not str or len(value) != 71 or not _HASH.fullmatch(value):
        raise EvidenceIntegrityError("invalid_evidence_digest")
    return value


def _limit(value):
    if type(value) is not int or not 0 <= value <= DEFAULT_MAX_TOTAL_BYTES:
        raise EvidenceIntegrityError("invalid_evidence_limit")
    return value


def _fields(value, keys, kind):
    if type(value) is not dict or len(value) != len(keys) or set(value) != keys:
        raise EvidenceIntegrityError("invalid_evidence_fields")
    # Snapshot scalar values so later callbacks cannot alter their accounting.
    result = value.copy()
    if (type(result["version"]) is not int or result["version"] != 1
            or type(result["kind"]) is not str or result["kind"] != kind
            or type(result["scope"]) is not str or result["scope"] != EVIDENCE_SCOPE):
        raise EvidenceIntegrityError("invalid_evidence_version_or_scope")
    return result


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("utf-8")


def make_chunk(raw: bytes) -> dict:
    """Encode one nonempty chunk; identity is SHA-256 of exact raw bytes."""
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_CHUNK_BYTES:
        raise EvidenceIntegrityError("invalid_evidence_chunk_bytes")
    return {"version": 1, "kind": CHUNK_KIND, "scope": EVIDENCE_SCOPE,
            "chunk_id": _hash(raw), "raw_bytes": len(raw), "encoding": "base64",
            "data": base64.b64encode(raw).decode("ascii")}


def verify_chunk(dto: dict) -> bytes:
    """Verify exact DTO types, canonical base64, byte count and raw digest.

    This checks the canonical serialized DTO limit, not a past incoming HTTP
    body's length. All encoded fields are bounded before decoding/JSON encoding.
    """
    value = _fields(dto, _CHUNK_KEYS, CHUNK_KIND)
    chunk_id = _digest(value["chunk_id"])
    size, data = value["raw_bytes"], value["data"]
    if (type(size) is not int or not 1 <= size <= MAX_CHUNK_BYTES
            or type(value["encoding"]) is not str or value["encoding"] != "base64" or type(data) is not str
            or len(data) != 4 * ((size + 2) // 3)):
        raise EvidenceIntegrityError("invalid_evidence_chunk_encoding_or_size")
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError, UnicodeError):
        raise EvidenceIntegrityError("invalid_evidence_base64") from None
    if len(raw) != size or base64.b64encode(raw).decode("ascii") != data:
        raise EvidenceIntegrityError("noncanonical_evidence_base64_or_size")
    if _hash(raw) != chunk_id:
        raise EvidenceIntegrityError("evidence_chunk_hash_mismatch")
    if len(_encoded(value)) > MAX_REQUEST_BYTES:
        raise EvidenceIntegrityError("evidence_request_exceeds_3_mib")
    return raw


def encode_chunk_request(dto: dict) -> bytes:
    """Return the exact bounded outbound body; does not send or acknowledge it."""
    value = dto.copy() if type(dto) is dict else dto
    verify_chunk(value)
    body = _encoded(value)
    if len(body) > MAX_REQUEST_BYTES:
        raise EvidenceIntegrityError("evidence_request_exceeds_3_mib")
    return body


def _pointer(dto, maximum):
    value = _fields(dto, _POINTER_KEYS, OBJECT_KIND)
    object_id = _digest(value["object_id"])
    size, refs = value["raw_bytes"], value["chunks"]
    if type(size) is not int or not 0 <= size <= maximum:
        raise EvidenceIntegrityError("evidence_object_size_exceeds_limit")
    if (type(refs) is not list or len(refs) != (size + MAX_CHUNK_BYTES - 1) // MAX_CHUNK_BYTES):
        raise EvidenceIntegrityError("noncanonical_evidence_chunk_layout")
    chunks, total = [], 0
    for index, ref in enumerate(refs):
        if type(ref) is not dict or len(ref) != 2 or set(ref) != {"chunk_id", "raw_bytes"}:
            raise EvidenceIntegrityError("invalid_evidence_chunk_reference")
        chunk_id, chunk_size = _digest(ref["chunk_id"]), ref["raw_bytes"]
        expected = min(MAX_CHUNK_BYTES, size - index * MAX_CHUNK_BYTES)
        if type(chunk_size) is not int or chunk_size != expected:
            raise EvidenceIntegrityError("noncanonical_evidence_chunk_layout")
        chunks.append((chunk_id, chunk_size))
        total += chunk_size
    if total != size:
        raise EvidenceIntegrityError("evidence_object_size_mismatch")
    return _Pointer(object_id, size, tuple(chunks))


def chunk_stream(source, emit_chunk, *, max_object_bytes=DEFAULT_MAX_OBJECT_BYTES) -> dict:
    """Read bounded bytes and emit staged chunk DTOs, returning a pointer last.

    Source reads may be short; canonical chunks still fill to 1 MiB except the
    final chunk. A late source/size/callback failure can leave staged chunks but
    returns no complete pointer. Empty generic objects use the empty SHA and no
    chunks; domain-specific capture rules may reject empty sources separately.
    """
    maximum = _limit(max_object_bytes)
    if not callable(getattr(source, "read", None)) or not callable(emit_chunk):
        raise EvidenceIntegrityError("invalid_evidence_stream_callbacks")
    digest, total, refs, ended = hashlib.sha256(), 0, [], False
    while not ended:
        buffer = bytearray()
        while len(buffer) < MAX_CHUNK_BYTES:
            request_size = min(MAX_CHUNK_BYTES - len(buffer), maximum - total - len(buffer) + 1)
            part = source.read(request_size)
            if type(part) is not bytes or len(part) > request_size:
                raise EvidenceIntegrityError("invalid_evidence_stream_read")
            if not part:
                ended = True
                break
            buffer.extend(part)
            if total + len(buffer) > maximum:
                raise EvidenceIntegrityError("evidence_object_size_exceeds_limit")
        if not buffer:
            break
        raw = bytes(buffer)
        dto = make_chunk(raw)
        # Accounting is independent of callback mutations to its DTO argument.
        refs.append({"chunk_id": dto["chunk_id"], "raw_bytes": len(raw)})
        digest.update(raw)
        total += len(raw)
        emit_chunk(dto)
    return {"version": 1, "kind": OBJECT_KIND, "scope": EVIDENCE_SCOPE,
            "object_id": "sha256:" + digest.hexdigest(), "raw_bytes": total, "chunks": refs}


def _verify_object(pointer, fetch_chunk, on_chunk):
    digest, total = hashlib.sha256(), 0
    for chunk_id, size in pointer.chunks:
        dto = fetch_chunk(chunk_id)
        raw = verify_chunk(dto)
        # Verify returned identity AND size before exposing any staging bytes.
        if _hash(raw) != chunk_id or len(raw) != size:
            raise EvidenceIntegrityError("evidence_chunk_reference_mismatch")
        total += len(raw)
        if total > pointer.raw_bytes:
            raise EvidenceIntegrityError("evidence_object_size_mismatch")
        digest.update(raw)
        if on_chunk is not None:
            on_chunk(pointer.object_id, raw)
    if total != pointer.raw_bytes or "sha256:" + digest.hexdigest() != pointer.object_id:
        raise EvidenceIntegrityError("evidence_object_digest_or_size_mismatch")
    return VerifiedObject(pointer.object_id, total, len(pointer.chunks))


def _callbacks(fetch_chunk, on_chunk):
    if not callable(fetch_chunk) or (on_chunk is not None and not callable(on_chunk)):
        raise EvidenceIntegrityError("invalid_evidence_stream_callbacks")


def verify_object(pointer, fetch_chunk, *, on_chunk=None,
                  max_object_bytes=DEFAULT_MAX_OBJECT_BYTES) -> VerifiedObject:
    """Verify one complete object with bounded memory; optional sink is STAGING.

    No success metadata is returned until the final whole-object hash matches.
    The callback signature is ``on_chunk(object_id, raw_bytes)``. Earlier sink
    calls cannot be undone if a later source/hash/callback failure occurs.
    """
    maximum = _limit(max_object_bytes)
    _callbacks(fetch_chunk, on_chunk)
    frozen = _pointer(pointer, maximum)
    return _verify_object(frozen, fetch_chunk, on_chunk)


def verify_objects(pointers, fetch_chunk, *, on_chunk=None,
                   max_object_bytes=DEFAULT_MAX_OBJECT_BYTES,
                   max_total_bytes=DEFAULT_MAX_TOTAL_BYTES) -> tuple[VerifiedObject, ...]:
    """Verify a complete bounded batch, returning metadata only after all pass.

    ALL pointers/order/declared totals are snapshotted before any fetch or sink
    callback. Repeated identical chunks are legitimate bytes; duplicate object
    IDs are refused so aggregate accounting cannot be bypassed by deduplication.
    No list of reconstructed raw objects is materialized.
    """
    maximum, aggregate = _limit(max_object_bytes), _limit(max_total_bytes)
    _callbacks(fetch_chunk, on_chunk)
    if type(pointers) is not list or len(pointers) > MAX_OBJECTS:
        raise EvidenceIntegrityError("invalid_evidence_object_batch")
    frozen = tuple(_pointer(pointer, maximum) for pointer in pointers)
    if len({pointer.object_id for pointer in frozen}) != len(frozen):
        raise EvidenceIntegrityError("duplicate_evidence_object_id")
    if sum(pointer.raw_bytes for pointer in frozen) > aggregate:
        raise EvidenceIntegrityError("evidence_total_size_exceeds_limit")
    results, actual = [], 0
    for pointer in frozen:
        result = _verify_object(pointer, fetch_chunk, on_chunk)
        actual += result.raw_bytes
        if actual > aggregate:
            raise EvidenceIntegrityError("evidence_total_size_exceeds_limit")
        results.append(result)
    return tuple(results)
