import json
from dataclasses import asdict
from urllib.error import HTTPError, URLError
import pytest
from selflearn_core.store.supabase_store import SupabaseStore, StoreConflict, StoreError
from selflearn_core.types import Prediction, Outcome


def p(id="p1"):
    return Prediction(id=id, ts=100, model_version="v1", task="example", features={}, prediction=.7, horizon_s=10)


class Scripted(SupabaseStore):
    def __init__(self, responses):
        super().__init__("https://example.supabase.co", "sb_secret_test")
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


def test_prediction_duplicate_is_idempotent_only_if_identical():
    record = p()
    store = Scripted([StoreConflict(), [asdict(record)]])
    assert store.append_prediction(record) == record.id
    store = Scripted([StoreConflict(), [asdict(p("changed"))]])
    with pytest.raises(StoreConflict):
        store.append_prediction(record)


def test_outcome_duplicate_cannot_change_original():
    outcome = Outcome("p1", 110, {"hit": True})
    Scripted([StoreConflict(), [asdict(outcome)]]).attach_outcome(outcome)
    with pytest.raises(StoreConflict):
        Scripted([StoreConflict(), [asdict(Outcome("p1", 110, {"hit": False}))]]).attach_outcome(outcome)


def test_pagination_and_resolution_filter_semantics():
    rows = [{**asdict(p(str(i))), "outcomes": asdict(Outcome(str(i), 500, True))} for i in range(3)]
    store = Scripted([rows[:2], rows[2:]])
    store.page_size = 2
    resolved = list(store.resolved("example", since=400, cohort="late"))
    assert len(resolved) == 3
    assert store.calls[0][2]["query"]["outcomes.resolved_ts"] == "gte.400"
    assert "ts" not in store.calls[0][2]["query"]
    assert store.calls[1][2]["query"]["id"] == "gt.1"


def test_unresolved_must_pass_horizon():
    store = Scripted([[{**asdict(p()), "outcomes": None}]])
    assert list(store.unresolved("example", 109)) == []
    store = Scripted([[{**asdict(p()), "outcomes": []}]])
    assert len(list(store.unresolved("example", 110))) == 1


def test_secret_headers_and_configuration_validation():
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    assert "Authorization" not in store._headers
    assert SupabaseStore("https://example.supabase.co", "eyJ.fake.jwt")._headers["Authorization"].startswith("Bearer ")
    for url in ("http://example.com", "https://user:pass@example.com", "https://example.com/path", "https://example.com?key=bad"):
        with pytest.raises(ValueError):
            SupabaseStore(url, "sb_secret_test")
    with pytest.raises(ValueError):
        SupabaseStore("https://example.supabase.co", "[SENSITIVE]")


def test_transport_filters_and_redacts_upstream_errors():
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    class Failed:
        def open(self, request, timeout):
            assert timeout == 12
            assert "sb_secret_test" not in request.full_url
            raise HTTPError(request.full_url, 500, "sensitive SQL response", {}, None)
    store._opener = Failed()
    with pytest.raises(StoreError) as error:
        store.get_prediction("a&select=secret")
    assert "sensitive" not in str(error.value)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, maximum):
        assert maximum == 8 * 1024 * 1024 + 1
        return self.payload[:maximum]


class Opener:
    def __init__(self, payload=b"", error=None):
        self.payload, self.error, self.requests = payload, error, []

    def open(self, request, timeout):
        assert timeout == 12
        self.requests.append(request)
        if self.error:
            raise self.error
        return Response(self.payload)


def test_real_request_serialization_binary_and_minimal_responses():
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    opener = store._opener = Opener(b'{"id":"saved"}')
    assert store.request("POST", "/rest/v1/predictions", data={"id": "p1"}) == {"id": "saved"}
    request = opener.requests[-1]
    assert json.loads(request.data) == {"id": "p1"}
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Prefer") == "return=minimal"
    assert request.get_header("Apikey") == "sb_secret_test"
    opener.payload = b""
    assert store.request("POST", "/rest/v1/outcomes", data={"prediction_id": "p1"}) is None
    opener.payload = b"private-image-bytes"
    assert store.request("POST", "/storage/v1/object/intake/image.png", raw=b"original", content_type="image/png", binary=True) == b"private-image-bytes"
    assert opener.requests[-1].data == b"original"
    assert opener.requests[-1].get_header("Content-type") == "image/png"
    assert opener.requests[-1].get_header("Prefer") is None


@pytest.mark.parametrize("payload", [b"{broken-json", b"\xff", b"x" * (8 * 1024 * 1024 + 1)])
def test_bad_or_oversized_upstream_body_is_sanitized_502(payload):
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    store._opener = Opener(payload)
    with pytest.raises(StoreError) as error:
        store.request("GET", "/rest/v1/predictions")
    assert error.value.status == 502
    assert "broken" not in str(error.value)


@pytest.mark.parametrize("failure,status", [
    (HTTPError("https://example.supabase.co", 409, "sensitive", {}, None), 409),
    (HTTPError("https://example.supabase.co", 302, "sensitive", {"Location": "https://attacker.invalid"}, None), 302),
    (URLError("sensitive transport"), 503), (TimeoutError("sensitive timeout"), 503),
    (OSError("sensitive socket"), 503),
])
def test_transport_failures_are_not_success_or_secret_diagnostics(failure, status):
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    store._opener = Opener(error=failure)
    with pytest.raises(StoreError) as error:
        store.request("GET", "/rest/v1/predictions")
    assert error.value.status == status
    assert "sensitive" not in str(error.value)
    assert len(store._opener.requests) == 1
    assert isinstance(error.value, StoreConflict) is (status == 409)


def test_transport_does_not_follow_redirects_or_allow_arbitrary_paths():
    store = SupabaseStore("https://example.supabase.co", "sb_secret_test")
    from selflearn_core.store.supabase_store import _NoRedirect
    assert _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://attacker.invalid") is None
    store._opener = Opener()
    for path in ("/admin/secrets", "/rest/v1/../admin", "https://attacker.invalid/rest/v1/predictions"):
        with pytest.raises(ValueError, match="Invalid store path"):
            store.request("GET", path)
    assert store._opener.requests == []


def test_recent_status_and_list_shaped_outcome_contract():
    record = {**asdict(p()), "outcomes": [asdict(Outcome("p1", 110, {"hit": True}))]}
    store = Scripted([[record]])
    result = list(store.resolved("example"))
    assert len(result) == 1
    assert result[0].outcome == Outcome("p1", 110, {"hit": True})
    store = Scripted([[record], {"predictions": 1, "outcomes": 1}])
    assert store.recent(1) == [record]
    assert store.calls[0][2]["query"]["order"] == "received_at.desc,id.asc"
    assert store.status() == {"predictions": 1, "outcomes": 1}
    assert store.calls[1] == ("POST", "/rest/v1/rpc/selflearn_status", {"data": {}})
    for limit in (0, 101):
        with pytest.raises(ValueError, match="limit"):
            store.recent(limit)
