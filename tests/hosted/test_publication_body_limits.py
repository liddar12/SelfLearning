"""Public raw bytes have their own cap; canonical intake limits stay intact."""
import hashlib
import json

import pytest

from selflearn_core.adapters.nfl_publication import MAX_BODY_BYTES, parse_publication_body
from services.web.nfl_publication_verifier import PublicPoolVerifier, PublicationUnavailable
from services.web.security import MAX_LEARNING_JSON


def test_real_pool_size_regression_and_exact_public_ceiling():
    assert MAX_BODY_BYTES == 4 * 1024 * 1024
    assert MAX_LEARNING_JSON == 3 * 1024 * 1024
    for size in (3216964, MAX_BODY_BYTES):
        body = b'{"fixture":true}'
        body += b" " * (size - len(body))
        value, exact = parse_publication_body(body.decode())
        assert value == {"fixture": True} and exact == body
        verifier = PublicPoolVerifier("https://public-fixture.example.com", transport=lambda url:
            (200, {"Content-Type": "application/json"}, body, url))
        observed = verifier.observe()
        assert len(observed["body_utf8"].encode()) == size
        assert hashlib.sha256(observed["body_utf8"].encode()).digest() == hashlib.sha256(body).digest()


def test_one_byte_above_public_ceiling_is_refused_by_parser_and_verifier():
    body = b"{}" + b" " * (MAX_BODY_BYTES - 1)
    assert len(body) == MAX_BODY_BYTES + 1
    with pytest.raises(ValueError):
        parse_publication_body(body.decode())
    verifier = PublicPoolVerifier("https://public-fixture.example.com", transport=lambda url:
        (200, {"Content-Type": "application/json"}, body, url))
    with pytest.raises((ValueError, PublicationUnavailable)):
        verifier.observe()


def test_raw_byte_cap_and_json_escaped_envelope_are_distinct_bounds():
    body = '{"x":"' + '\x7f' * (MAX_BODY_BYTES - 8) + '"}'
    assert len(body.encode()) == MAX_BODY_BYTES
    assert len(json.dumps({"body_utf8": body}, ensure_ascii=True).encode()) > 20 * 1024 * 1024
    parsed, raw = parse_publication_body(body)
    assert len(parsed["x"]) == MAX_BODY_BYTES - 8 and raw == body.encode()
