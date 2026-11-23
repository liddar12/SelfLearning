"""Compression changes wire encoding only; trust and decoded limits stay fixed."""
import gzip
import io
import json
import random

import pytest

from selflearn_core.store.supabase_store import SupabaseStore, StoreError, _MAX_RESPONSE_BYTES

GAME = "/rest/v1/rpc/selflearn_journal_read_observed_game_v1"
OBJECTS = "/rest/v1/rpc/selflearn_journal_read_objects_v1"


class Response(io.BytesIO):
    def __init__(self, payload, encoding):
        super().__init__(payload)
        self.encoding = encoding
        self.bytes_read = 0

    def getheader(self, key, default=None):
        assert key == "Content-Encoding"
        return self.encoding if self.encoding is not None else default

    def read(self, maximum):
        value = super().read(maximum)
        self.bytes_read += len(value)
        return value


class Opener:
    def __init__(self, payload, encoding="gzip"):
        self.payload, self.encoding, self.requests = payload, encoding, []
        self.response = None

    def open(self, request, timeout):
        scope_prepare = request.get_method() == "POST" and request.full_url.endswith(
            "/selflearn_journal_prepare_capture_membership_v3"
        )
        assert timeout == (22 if scope_prepare else 12)
        self.requests.append(request)
        self.response = Response(self.payload, self.encoding)
        return self.response


def store(payload, encoding="gzip"):
    value = SupabaseStore("https://offline.supabase.invalid", "sb_secret_compression_fixture")
    value._opener = Opener(payload, encoding)
    return value


@pytest.mark.parametrize("path", [GAME, OBJECTS])
def test_only_scoped_read_rpcs_negotiate_gzip_and_preserve_original_payload_lexemes(path):
    original = b'{"rows":[{"payload_json":"{\\"a\\":1.0,\\"b\\":1,\\"s\\":\\"caf\\u00e9\\"}"}],"watermark":"9"}'
    expected = json.loads(original)
    compressed = gzip.compress(original, mtime=0)
    journal_store = store(compressed)
    assert journal_store.request("POST", path, data={"p_watermark": "9"}) == expected
    request = journal_store._opener.requests[0]
    assert request.get_header("Accept-encoding") == "gzip"
    assert request.get_header("Apikey") == "sb_secret_compression_fixture"
    assert json.loads(request.data) == {"p_watermark": "9"}
    assert expected["rows"][0]["payload_json"].startswith('{"a":1.0,"b":1,')


@pytest.mark.parametrize("encoding", [None, "", "identity"])
def test_server_may_decline_compression_without_changing_results(encoding):
    journal_store = store(b'{"original":1.0}', encoding)
    assert journal_store.request("POST", GAME) == {"original": 1.0}


@pytest.mark.parametrize("path,method,binary", [
    ("/rest/v1/rpc/selflearn_journal_read_v1", "POST", False),
    ("/rest/v1/rpc/selflearn_journal_read_observed_v1", "POST", False),
    ("/rest/v1/rpc/selflearn_journal_commit_capture_application_batch_v2", "POST", False),
    ("/rest/v1/rpc/selflearn_journal_prepare_capture_membership_v3", "POST", False),
    (GAME, "GET", False), (GAME, "POST", True), ("/storage/v1/object/private/x", "GET", True),
])
def test_legacy_writes_and_binary_storage_keep_existing_wire_behavior(path, method, binary):
    journal_store = store(b'{"original":1}', None)
    result = journal_store.request(method, path, binary=binary)
    assert result == (b'{"original":1}' if binary else {"original": 1})
    assert journal_store._opener.requests[0].get_header("Accept-encoding") is None


@pytest.mark.parametrize("mode", ["truncated", "corrupt", "trailing", "concatenated", "invalid_json", "unknown_encoding", "coding_chain"])
def test_malformed_or_unsupported_encoding_is_sanitized_failure(mode):
    payload = gzip.compress(b'{"sensitive":"fixture"}', mtime=0)
    encoding = "gzip"
    if mode == "truncated":
        payload = payload[:-4]
    elif mode == "corrupt":
        payload = payload[:-8] + b"wrongCRC"
    elif mode == "trailing":
        payload += b"trailing"
    elif mode == "concatenated":
        payload += gzip.compress(b"{}", mtime=0)
    elif mode == "invalid_json":
        payload = gzip.compress(b"sensitive invalid json", mtime=0)
    elif mode == "unknown_encoding":
        encoding = "br"
    else:
        encoding = "gzip, identity"
    journal_store = store(payload, encoding)
    with pytest.raises(StoreError) as error:
        journal_store.request("POST", GAME)
    assert error.value.status == 502
    assert "sensitive" not in str(error.value)
    assert len(journal_store._opener.requests) == 1


def test_decoded_limit_is_unchanged_and_compression_bomb_stops_early():
    exact = b'"' + b"x" * (_MAX_RESPONSE_BYTES - 2) + b'"'
    assert len(store(gzip.compress(exact)).request("POST", GAME)) == _MAX_RESPONSE_BYTES - 2
    bomb = gzip.compress(b"x" * (_MAX_RESPONSE_BYTES * 4), mtime=0)
    journal_store = store(bomb)
    with pytest.raises(StoreError) as error:
        journal_store.request("POST", GAME)
    assert error.value.status == 502
    assert journal_store._opener.response.bytes_read <= 65536
    with pytest.raises(StoreError) as identity_error:
        store(exact + b" ", None).request("POST", GAME)
    assert identity_error.value.status == 502


def test_encoded_limit_stays_bounded_even_when_decoded_size_is_valid():
    # Incompressible fixture bytes make gzip framing push encoded size over
    # the same cap. No JSON parsing occurs before both wire bounds pass.
    original = random.Random(17).randbytes(_MAX_RESPONSE_BYTES)
    encoded = gzip.compress(original, compresslevel=0, mtime=0)
    journal_store = store(encoded)
    with pytest.raises(StoreError) as error:
        journal_store.request("POST", OBJECTS)
    assert error.value.status == 502
    assert journal_store._opener.response.bytes_read <= _MAX_RESPONSE_BYTES + 1


def test_multichunk_gzip_decoding_preserves_a_large_original_json_string():
    original = {"payload_json": random.Random(28).randbytes(1024 * 1024).hex(), "lexeme": "1.0"}
    encoded = gzip.compress(json.dumps(original).encode(), mtime=0)
    assert len(encoded) > 65536
    journal_store = store(encoded)
    assert journal_store.request("POST", OBJECTS) == original
    assert journal_store._opener.response.bytes_read == len(encoded)
