"""Independent oracles and adversarial streaming tests; no storage/network."""
import base64
import copy
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
from unittest import mock
import unittest

from selflearn_core import evidence_chunks as chunks


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def stage(raw, **kwargs):
    objects = {}
    pointer = chunks.chunk_stream(io.BytesIO(raw), lambda dto: objects.setdefault(dto["chunk_id"], dto), **kwargs)
    return pointer, objects


class EvidenceChunksTest(unittest.TestCase):
    def test_golden_raw_identity_and_canonical_outbound_body(self):
        dto = chunks.make_chunk(b"f")
        self.assertEqual(dto, {"version": 1, "kind": "selflearn.evidence_chunk.v1",
            "scope": "uploaded_only", "chunk_id": "sha256:252f10c83610ebca1a059c0bae8255eba2f95be4d1d7bcfa89d7248a82d9f111",
            "raw_bytes": 1, "encoding": "base64", "data": "Zg=="})
        body = chunks.encode_chunk_request(dto)
        self.assertEqual(body, json.dumps(dto, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode())
        self.assertEqual(chunks.verify_chunk(json.loads(body)), b"f")

    def test_binary_and_max_chunk_roundtrip_stay_below_three_mib(self):
        for raw in (b"\x00\xff\x80abc", bytes(range(256)) * 4096):
            dto = chunks.make_chunk(raw)
            self.assertEqual(dto["chunk_id"], digest(raw))
            self.assertEqual(chunks.verify_chunk(dto), raw)
            self.assertLessEqual(len(chunks.encode_chunk_request(dto)), chunks.MAX_REQUEST_BYTES)
        for raw in (b"", bytearray(b"a"), "a", b"x" * (chunks.MAX_CHUNK_BYTES + 1)):
            with self.subTest(kind=type(raw)), self.assertRaises(ValueError):
                chunks.make_chunk(raw)

    def test_chunk_types_keys_hash_and_encoding_are_exact(self):
        base = chunks.make_chunk(b"f")
        changes = [{"version": True}, {"version": 1.0}, {"kind": None}, {"scope": "verified_label"},
                   {"encoding": "gzip"}, {"encoding": True}, {"scope": None}, {"kind": ["selflearn.evidence_chunk.v1"]},
                   {"raw_bytes": True}, {"raw_bytes": 1.0}, {"raw_bytes": 0},
                   {"raw_bytes": -1}, {"raw_bytes": chunks.MAX_CHUNK_BYTES + 1}, {"data": b"Zg=="},
                   {"data": "Zh=="}, {"data": "Zg=\n"}, {"data": "Zg--"}, {"data": "Zg"},
                   {"data": "\u00e9g=="}, {"chunk_id": digest(b"g")}, {"chunk_id": "SHA256:" + "a" * 64},
                   {"chunk_id": "sha256:" + "A" * 64}, {"data": "Zw=="}, {"extra": 1}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                chunks.verify_chunk({**base, **change})
        for value in (None, [], {k: v for k, v in base.items() if k != "scope"}):
            with self.assertRaises(ValueError):
                chunks.verify_chunk(value)

    def test_encoded_oversize_is_rejected_before_decode_or_json_allocation(self):
        dto = {**chunks.make_chunk(b"a"), "data": "a" * (chunks.MAX_REQUEST_BYTES + 1)}
        with mock.patch.object(chunks.base64, "b64decode", side_effect=AssertionError("must not decode")), \
                mock.patch.object(chunks.json, "dumps", side_effect=AssertionError("must not encode")):
            with self.assertRaises(ValueError):
                chunks.verify_chunk(dto)

    def test_empty_and_multichunk_pointers_match_independent_oracles(self):
        for raw in (b"", b"a", b"a" * chunks.MAX_CHUNK_BYTES + b"tail"):
            pointer, objects = stage(raw)
            self.assertEqual(pointer["object_id"], digest(raw))
            observed = bytearray()
            result = chunks.verify_object(pointer, objects.__getitem__, on_chunk=lambda oid, part: observed.extend(part))
            self.assertEqual(bytes(observed), raw)
            self.assertEqual((result.object_id, result.raw_bytes, result.chunk_count, result.scope),
                             (digest(raw), len(raw), len(pointer["chunks"]), "uploaded_only"))
            with self.assertRaises(FrozenInstanceError):
                result.raw_bytes = 999
            self.assertNotIn("durable", result.__dict__)
            self.assertNotIn("authenticated", result.__dict__)

    def test_short_reads_still_produce_canonical_chunks(self):
        raw = b"abc" * 400000
        class Short(io.BytesIO):
            def read(self, n):
                self.asserted_max = max(n, getattr(self, "asserted_max", 0))
                return super().read(min(n, 997))
        source, emitted = Short(raw), []
        pointer = chunks.chunk_stream(source, emitted.append)
        self.assertEqual([dto["raw_bytes"] for dto in emitted], [chunks.MAX_CHUNK_BYTES, len(raw) - chunks.MAX_CHUNK_BYTES])
        self.assertEqual(pointer["object_id"], digest(raw))
        self.assertLessEqual(source.asserted_max, chunks.MAX_CHUNK_BYTES)

    def test_repeated_identical_chunk_ids_preserve_multiplicity(self):
        raw = b"q" * (2 * chunks.MAX_CHUNK_BYTES)
        pointer, objects = stage(raw)
        self.assertEqual(len(objects), 1)
        self.assertEqual(pointer["chunks"][0], pointer["chunks"][1])
        calls = []
        result = chunks.verify_object(pointer, lambda key: (calls.append(key), objects[key])[1])
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.raw_bytes, len(raw))

    def test_order_full_hash_and_noncanonical_decomposition_fail(self):
        raw = b"a" * chunks.MAX_CHUNK_BYTES + b"b" * chunks.MAX_CHUNK_BYTES
        pointer, objects = stage(raw)
        changed = copy.deepcopy(pointer)
        changed["chunks"].reverse()
        with self.assertRaisesRegex(ValueError, "digest"):
            chunks.verify_object(changed, objects.__getitem__)
        changed = {**pointer, "object_id": digest(b"other")}
        with self.assertRaisesRegex(ValueError, "digest"):
            chunks.verify_object(changed, objects.__getitem__)
        left, right = chunks.make_chunk(b"ab"), chunks.make_chunk(b"cd")
        changed = {**pointer, "object_id": digest(b"abcd"), "raw_bytes": 4,
                   "chunks": [{"chunk_id": d["chunk_id"], "raw_bytes": d["raw_bytes"]} for d in (left, right)]}
        with self.assertRaisesRegex(ValueError, "layout"):
            chunks.verify_object(changed, lambda _: self.fail("must not fetch"))

    def test_pointer_types_refs_and_limits_fail_before_fetch(self):
        pointer, objects = stage(b"abc")
        changes = [{"version": True}, {"scope": "trained"}, {"object_id": None}, {"raw_bytes": True},
                   {"raw_bytes": 3.0}, {"raw_bytes": -1}, {"raw_bytes": chunks.DEFAULT_MAX_OBJECT_BYTES + 1},
                   {"chunks": ()}, {"chunks": []}, {"chunks": [{"chunk_id": digest(b"abc"), "raw_bytes": True}]},
                   {"chunks": [{"chunk_id": digest(b"abc"), "raw_bytes": 2}]},
                   {"chunks": [{"chunk_id": digest(b"abc"), "raw_bytes": 3, "extra": 1}]}, {"extra": 1}]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                chunks.verify_object({**pointer, **change}, lambda _: self.fail("must not fetch"))
        for maximum in (True, -1, 1.0, chunks.DEFAULT_MAX_OBJECT_BYTES + 1):
            with self.assertRaises(ValueError):
                chunks.verify_object(pointer, objects.__getitem__, max_object_bytes=maximum)
        with self.assertRaises(ValueError):
            chunks.verify_object(pointer, objects.__getitem__, max_object_bytes=2)

    def test_wrong_returned_chunk_identity_or_size_never_reaches_sink(self):
        pointer, objects = stage(b"abc")
        for dto in (chunks.make_chunk(b"def"), chunks.make_chunk(b"abcd")):
            sink = mock.Mock()
            with self.assertRaises(ValueError):
                chunks.verify_object(pointer, lambda _: dto, on_chunk=sink)
            sink.assert_not_called()
        with self.assertRaises(KeyError):
            chunks.verify_object(pointer, {}.__getitem__)

    def test_batch_prevalidates_all_pointers_duplicates_and_total(self):
        a, objects = stage(b"a")
        b, other = stage(b"b")
        objects.update(other)
        for pointers, kwargs in (([a, a], {}), ([a, {**b, "raw_bytes": True}], {}),
                                 ([a, b], {"max_total_bytes": 1}), ((a, b), {}),
                                 ([a] * (chunks.MAX_OBJECTS + 1), {})):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                chunks.verify_objects(pointers, lambda _: self.fail("must not fetch"), **kwargs)
        self.assertEqual(chunks.verify_objects([], objects.__getitem__, max_total_bytes=0), ())
        result = chunks.verify_objects([a, b], objects.__getitem__, max_total_bytes=2)
        self.assertEqual([r.object_id for r in result], [a["object_id"], b["object_id"]])

    def test_multiobject_exact_aggregate_limit_and_one_byte_over(self):
        unit = chunks.make_chunk(b"q" * chunks.MAX_CHUNK_BYTES)
        large = {"version": 1, "kind": chunks.OBJECT_KIND, "scope": chunks.EVIDENCE_SCOPE,
                 "object_id": digest(b"placeholder"), "raw_bytes": chunks.DEFAULT_MAX_TOTAL_BYTES,
                 "chunks": [{"chunk_id": unit["chunk_id"], "raw_bytes": unit["raw_bytes"]} for _ in range(64)]}
        small, objects = stage(b"a")
        with self.assertRaisesRegex(ValueError, "total_size"):
            chunks.verify_objects([large, small], lambda _: self.fail("must reject declared total before fetch"))
        a, objects = stage(b"aa")
        b, other = stage(b"bbb")
        objects.update(other)
        self.assertEqual(sum(r.raw_bytes for r in chunks.verify_objects([a, b], objects.__getitem__,
                                                                       max_total_bytes=5)), 5)

    def test_staging_callback_failures_propagate_without_success_metadata(self):
        pointer, objects = stage(b"a")
        def broken(*args):
            raise RuntimeError("fixture sink failure")
        with self.assertRaises(RuntimeError):
            chunks.chunk_stream(io.BytesIO(b"a"), broken)
        with self.assertRaises(RuntimeError):
            chunks.verify_objects([pointer], objects.__getitem__, on_chunk=broken)

    def test_fetch_callback_cannot_mutate_any_snapshotted_pointer_or_order(self):
        a, objects = stage(b"a")
        b, other = stage(b"b")
        objects.update(other)
        pointers = [a, b]
        expected = [a["object_id"], b["object_id"]]
        def fetch(key):
            b["chunks"].clear()
            b["object_id"] = digest(b"corrupt")
            pointers.reverse()
            return objects[key]
        self.assertEqual([r.object_id for r in chunks.verify_objects(pointers, fetch)], expected)

    def test_emit_callback_mutations_do_not_change_stream_pointer(self):
        raw, emitted = b"abc", []
        def emit(dto):
            emitted.append(copy.deepcopy(dto))
            dto.update(chunk_id=digest(b"wrong"), raw_bytes=999, data="bad")
        pointer = chunks.chunk_stream(io.BytesIO(raw), emit)
        self.assertEqual(pointer["object_id"], digest(raw))
        self.assertEqual(pointer["chunks"], [{"chunk_id": digest(raw), "raw_bytes": 3}])
        self.assertEqual(chunks.verify_object(pointer, lambda _: emitted[0]).raw_bytes, 3)

    def test_late_size_hash_or_source_failure_is_staging_not_rollback(self):
        emitted = []
        raw = b"x" * chunks.MAX_CHUNK_BYTES + b"y"
        with self.assertRaises(ValueError):
            chunks.chunk_stream(io.BytesIO(raw), emitted.append, max_object_bytes=chunks.MAX_CHUNK_BYTES)
        self.assertEqual(len(emitted), 1)
        class Broken:
            def __init__(self):
                self.calls = 0
            def read(self, n):
                self.calls += 1
                if self.calls == 1:
                    return b"a" * n
                raise OSError("fixture source failure")
        with self.assertRaises(OSError):
            chunks.chunk_stream(Broken(), emitted.append)
        self.assertEqual(len(emitted), 2)
        pointer, objects = stage(b"abc")
        pointer["object_id"] = digest(b"different")
        staged = []
        with self.assertRaises(ValueError):
            chunks.verify_object(pointer, objects.__getitem__, on_chunk=lambda oid, raw: staged.append(raw))
        self.assertEqual(staged, [b"abc"])

    def test_stream_and_verifier_callbacks_and_read_types_are_bounded(self):
        for source, emit in ((None, lambda _: None), (io.BytesIO(), None)):
            with self.assertRaises(ValueError):
                chunks.chunk_stream(source, emit)
        for value in ("bad", bytearray(b"a"), b"ab"):
            class Bad:
                def read(self, n):
                    return value
            with self.assertRaises(ValueError):
                chunks.chunk_stream(Bad(), lambda _: None, max_object_bytes=0)
        pointer, objects = stage(b"a")
        for fetch, sink in ((None, None), (objects.__getitem__, False)):
            with self.assertRaises(ValueError):
                chunks.verify_object(pointer, fetch, on_chunk=sink)
        for maximum in (True, -1, chunks.DEFAULT_MAX_TOTAL_BYTES + 1):
            with self.assertRaises(ValueError):
                chunks.verify_objects([pointer], objects.__getitem__, max_total_bytes=maximum)

    def test_exact_64_mib_is_streamed_without_full_object_materialization(self):
        class Repeated:
            def __init__(self, count):
                self.remaining, self.largest_read = count, 0
            def read(self, n):
                self.largest_read = max(n, self.largest_read)
                take = min(n, self.remaining)
                self.remaining -= take
                return b"z" * take
        source, staged, counts = Repeated(chunks.DEFAULT_MAX_OBJECT_BYTES), {}, []
        pointer = chunks.chunk_stream(source, lambda dto: staged.setdefault(dto["chunk_id"], dto))
        oracle = hashlib.sha256()
        for _ in range(64):
            oracle.update(b"z" * chunks.MAX_CHUNK_BYTES)
        self.assertEqual(pointer["object_id"], "sha256:" + oracle.hexdigest())
        self.assertEqual(len(staged), 1)
        result = chunks.verify_objects([pointer], staged.__getitem__, on_chunk=lambda oid, part: counts.append(len(part)))
        self.assertEqual(result[0].raw_bytes, 64 * chunks.MAX_CHUNK_BYTES)
        self.assertEqual(counts, [chunks.MAX_CHUNK_BYTES] * 64)
        self.assertLessEqual(source.largest_read, chunks.MAX_CHUNK_BYTES)
        with self.assertRaises(ValueError):
            chunks.chunk_stream(Repeated(chunks.DEFAULT_MAX_OBJECT_BYTES + 1), lambda _: None)


if __name__ == "__main__":
    unittest.main()
