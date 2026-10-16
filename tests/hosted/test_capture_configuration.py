"""Pure selection + mocked request readiness; NOT a hosted connection proof."""
import base64
import copy
import importlib
import json
from contextlib import contextmanager

import pytest

from services.web import capture_configuration as config
from selflearn_core import evidence_chunks
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import staging_namespace
from selflearn_core.store.supabase_store import StoreError, SupabaseStore


SECRET = "sb_secret_fixture_backend_not_a_real_key"
ORIGIN = "https://offline.supabase.invalid"
BUCKET = "fixture-capture-private"
ENV = {"SELFLEARN_CAPTURE_BACKEND": "supabase", "SUPABASE_URL": ORIGIN,
       "SUPABASE_SECRET_KEY": SECRET, "SELFLEARN_CAPTURE_EVIDENCE_BUCKET": BUCKET}
STATUS_PATH = "/rest/v1/rpc/selflearn_journal_read_observed_v1"
DELIVERY_PATH = "/rest/v1/rpc/selflearn_journal_read_capture_delivery_v1"
QUOTA_PATH = "/rest/v1/rpc/selflearn_reserve_capture_stage_v1"


def legacy_key(payload=None):
    text = json.dumps({"role": "service_role"} if payload is None else payload)
    claims = base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")
    return "header." + claims + ".fixture-signature"


@pytest.fixture
def requests(monkeypatch):
    class FakeRequests:
        def __init__(self):
            self.calls, self.failure, self.responses = [], None, {}

        def request(self, store, method, path, **kwargs):
            self.calls.append((method, path, copy.deepcopy(kwargs), dict(store._headers), store._url))
            if self.failure is not None:
                raise self.failure
            if path in self.responses:
                response = self.responses[path]
                if isinstance(response, BaseException):
                    raise response
                return copy.deepcopy(response)
            if path == STATUS_PATH:
                return {"version": 1, "rows": [{"schema_version": 1, "backend": "postgres", "journal_seq": "0"}],
                        "next_after": None, "watermark": "0"}
            if path == DELIVERY_PATH:
                return {"version": 1, "result": None, "groups": [], "next_after": None, "total_groups": 0}
            raise AssertionError("unexpected mocked request")

    fake = FakeRequests()
    monkeypatch.setattr(SupabaseStore, "request", lambda store, method, path, **kwargs:
                        fake.request(store, method, path, **kwargs))
    # A regression must never make a real HTTP request, including via an
    # accidentally constructed second adapter instead of our mocked method.
    monkeypatch.setattr("urllib.request.OpenerDirector.open", lambda *args, **kwargs:
                        pytest.fail("network I/O is forbidden in configuration tests"))
    return fake


def assert_unverified(info):
    assert info["production_connection_verified"] is False
    assert info["working_learning_loop"] is False
    assert info["model_adoption"] == "unverified"


def test_import_and_selection_construct_without_requests(requests):
    importlib.reload(config)
    assert requests.calls == []
    assert config.server_supabase_credentials(ENV) == (ORIGIN, SECRET)
    factory, info = config.select_capture_factory(ENV)
    assert callable(factory) and requests.calls == []
    assert info == {"backend": "supabase", "capture_configured": True, "configuration_valid": True,
                    "readiness": "configured_unverified", "production_connection_verified": False,
                    "working_learning_loop": False, "model_adoption": "unverified"}


def test_pure_helper_import_and_selection_never_construct_transport(requests, monkeypatch):
    import urllib.request
    from selflearn_core.store import supabase_store

    def forbidden(*args, **kwargs):
        pytest.fail("pure configuration must not construct a transport/opener")

    try:
        with monkeypatch.context() as scoped:
            scoped.setattr(supabase_store, "SupabaseStore", forbidden)
            scoped.setattr(supabase_store, "build_opener", forbidden)
            scoped.setattr(urllib.request, "build_opener", forbidden)
            importlib.reload(config)
            assert config.server_supabase_credentials(ENV) == (ORIGIN, SECRET)
            factory, info = config.select_capture_factory(ENV)
            assert callable(factory) and info["configuration_valid"] is True
            assert requests.calls == []
    finally:
        # Reload only after scoped patches restore the original definitions.
        # This also isolates later tests if a purity assertion fails.
        importlib.reload(config)


def test_opaque_new_secret_suffix_is_not_an_unsupported_provider_format_rule(requests):
    opaque = "sb_secret_fixture.opaque~suffix:!"
    assert config.server_supabase_credentials({**ENV, "SUPABASE_SECRET_KEY": opaque}) == (ORIGIN, opaque)
    assert requests.calls == []


@pytest.mark.parametrize("selection", [None, "", "disabled"])
def test_default_off_even_with_existing_tokens_and_other_backend(requests, selection):
    env = {**ENV, "SELFLEARN_JOURNAL_BACKEND": "supabase", "SELFLEARN_OPERATOR_TOKEN": "fixture-owner",
           "SELFLEARN_NFL_INGEST_TOKEN": "fixture-generation", "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": "fixture-capture"}
    env.pop("SELFLEARN_CAPTURE_BACKEND")
    if selection is not None:
        env["SELFLEARN_CAPTURE_BACKEND"] = selection
    factory, info = config.select_capture_factory(env)
    assert factory is None and info["readiness"] == "disabled" and not info["capture_configured"]
    assert_unverified(info)
    assert requests.calls == []


@pytest.mark.parametrize("selection", [False, True, 0, 1, [], {}, "Supabase", " supabase ", "sqlite", "SECRET"])
def test_bad_selector_has_no_fallback_or_diagnostic_echo(requests, selection):
    factory, info = config.select_capture_factory({**ENV, "SELFLEARN_CAPTURE_BACKEND": selection})
    assert info["backend"] == "invalid" and info["configuration_valid"] is False
    assert_unverified(info)
    with pytest.raises(StoreError) as caught:
        with factory():
            pytest.fail("invalid selection opened a receiver")
    assert caught.value.status == 503 and str(caught.value) == "Shared store request failed (503)"
    assert requests.calls == []


@pytest.mark.parametrize("overrides", [
    {"SUPABASE_URL": None}, {"SUPABASE_URL": True}, {"SUPABASE_URL": ""},
    {"SUPABASE_URL": "http://offline.supabase.invalid"},
    {"SUPABASE_URL": "https://user:SECRET@offline.supabase.invalid"},
    {"SUPABASE_URL": "https://offline.supabase.invalid/rest/v1"},
    {"SUPABASE_URL": "https://offline.supabase.invalid?SECRET"},
    {"SUPABASE_URL": "https://offline.supabase.invalid#SECRET"},
    {"SUPABASE_URL": "https://offline.supabase.invalid\n"},
    {"SUPABASE_URL": "https://offline.supabase.invalid:SECRET"},
    {"SUPABASE_URL": "https://offline.supabase.invalid:65536"},
    {"SUPABASE_URL": "https://offline.supabase.invalid:0"},
    {"SUPABASE_URL": "https://offline.supabase.invalid\x7f"},
    {"SUPABASE_URL": "https://nonascii-\u00e9.invalid"}, {"SUPABASE_URL": "x" * 2049},
    {"SUPABASE_SECRET_KEY": None}, {"SUPABASE_SECRET_KEY": True}, {"SUPABASE_SECRET_KEY": []},
    {"SUPABASE_SECRET_KEY": ""}, {"SUPABASE_SECRET_KEY": "sb_secret_"},
    {"SUPABASE_SECRET_KEY": "sb_publishable_browser_key"},
    {"SUPABASE_SECRET_KEY": "sb_secret_whitespace SECRET"},
    {"SUPABASE_SECRET_KEY": "sb_secret_\u00e9"}, {"SUPABASE_SECRET_KEY": "sb_secret_" + "x" * 8192},
    {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": None}, {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": False},
    {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": ""}, {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": "../SECRET"},
    {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": "Uppercase"}, {"SELFLEARN_CAPTURE_EVIDENCE_BUCKET": "a" * 64},
])
def test_invalid_configuration_is_private_and_has_no_requests(requests, overrides, caplog):
    factory, info = config.select_capture_factory({**ENV, **overrides})
    assert info["backend"] == "supabase" and not info["configuration_valid"]
    with pytest.raises(StoreError) as caught:
        with factory():
            pytest.fail("invalid configuration opened a receiver")
    assert caught.value.status == 503
    assert "SECRET" not in str(caught.value) and "SECRET" not in caplog.text
    assert SECRET not in json.dumps(info) and SECRET not in caplog.text
    assert requests.calls == []


@pytest.mark.parametrize("new_key", [None, "", False, "sb_publishable_fixture", "sb_secret_"])
def test_invalid_preferred_secret_never_uses_good_legacy_key(requests, new_key):
    env = {**ENV, "SUPABASE_SECRET_KEY": new_key, "SUPABASE_SERVICE_ROLE_KEY": legacy_key()}
    with pytest.raises(ValueError, match="^invalid_server_supabase_configuration$"):
        config.server_supabase_credentials(env)
    assert config.select_capture_factory(env)[1]["configuration_valid"] is False
    assert requests.calls == []


@pytest.mark.parametrize("legacy", [None, False, "", "header..signature", "a.b.c.d", "header.a.signature",
    "header.@@@.signature", legacy_key({"role": "anon"}), legacy_key({"role": ["service_role"]}),
    legacy_key(["service_role"]), legacy_key({}), "x" * 8193])
def test_legacy_key_shape_and_role_fail_closed(requests, legacy):
    env = {key: value for key, value in ENV.items() if key != "SUPABASE_SECRET_KEY"}
    env["SUPABASE_SERVICE_ROLE_KEY"] = legacy
    with pytest.raises(ValueError, match="^invalid_server_supabase_configuration$"):
        config.server_supabase_credentials(env)
    assert requests.calls == []


@pytest.mark.parametrize("claims", ['{"role":"anon","role":"service_role"}', '{"role":"service_role","x":NaN}',
                                   '{"role":"service_role","x":{"a":1,"a":2}}'])
def test_legacy_ambiguous_and_nonfinite_claims_rejected(requests, claims):
    encoded = base64.urlsafe_b64encode(claims.encode()).decode().rstrip("=")
    env = {key: value for key, value in ENV.items() if key != "SUPABASE_SECRET_KEY"}
    env["SUPABASE_SERVICE_ROLE_KEY"] = "header." + encoded + ".signature"
    with pytest.raises(ValueError):
        config.server_supabase_credentials(env)
    assert requests.calls == []


@pytest.mark.parametrize("legacy", [False, True])
def test_explicit_factory_uses_read_only_probes_and_fixed_server_scope(requests, legacy):
    env = {**ENV, "SELFLEARN_CAPTURE_DESTINATION_ID": "SECRET-attacker", "SELFLEARN_CAPTURE_PRODUCER_ID": "attacker",
           "SELFLEARN_CAPTURE_NAMESPACE": "attacker", "SUPABASE_URL": ORIGIN + "/"}
    key = SECRET
    if legacy:
        del env["SUPABASE_SECRET_KEY"]
        key = env["SUPABASE_SERVICE_ROLE_KEY"] = legacy_key()
    factory, info = config.select_capture_factory(env)
    # Configuration snapshots its values. A mutable env is not a per-request
    # authority to change the destination, key, bucket or namespace.
    env.update(SUPABASE_URL="https://attacker.invalid", SUPABASE_SECRET_KEY="sb_secret_attacker",
               SELFLEARN_CAPTURE_EVIDENCE_BUCKET="attacker-bucket")
    with factory() as receiver:
        expected_destination = fingerprint({"base_url": "https://self-learning-beige.vercel.app"})
        expected_namespace = staging_namespace(destination_id=expected_destination, producer_id="nfl2026")
        assert receiver.destination_id == expected_destination and receiver.producer_id == "nfl2026"
        assert receiver.namespace_id == expected_namespace == receiver.quota._namespace_id
        assert receiver.evidence._bucket == BUCKET
        encoded = receiver.evidence._namespace.removeprefix("nfl-")
        assert base64.b32decode(encoded.upper() + "=" * (-len(encoded) % 8)).hex() == expected_namespace[7:]
        assert len(receiver.evidence._namespace) == 56
    assert [call[1] for call in requests.calls] == [STATUS_PATH, DELIVERY_PATH]
    for method, path, kwargs, headers, origin in requests.calls:
        assert method == "POST" and origin == ORIGIN and headers["apikey"] == key
        assert (headers.get("Authorization") == "Bearer " + key) if legacy else "Authorization" not in headers
        assert not any(word in path for word in ("reserve", "storage", "commit", "append"))
    assert info["readiness"] == "configured_unverified"
    assert_unverified(info)


@pytest.mark.parametrize("path,fault", [
    (STATUS_PATH, StoreError(401)), (STATUS_PATH, StoreError(403)), (STATUS_PATH, StoreError(404)),
    (STATUS_PATH, OSError("SECRET provider path")), (STATUS_PATH, ValueError("SECRET SQL")),
    (STATUS_PATH, {"version": 1, "rows": [], "next_after": None, "watermark": "0"}),
    (STATUS_PATH, {"version": 1, "rows": [{"schema_version": True, "backend": "postgres", "journal_seq": "0"}],
                   "next_after": None, "watermark": "0"}),
    (DELIVERY_PATH, StoreError(403)), (DELIVERY_PATH, StoreError(404)),
    (DELIVERY_PATH, RuntimeError("SECRET missing migration")), (DELIVERY_PATH, {}),
    (DELIVERY_PATH, {"version": 1, "result": None, "groups": [], "next_after": None, "total_groups": 1}),
])
def test_schema_permission_and_provider_faults_are_sanitized_503(requests, path, fault, caplog):
    requests.responses[path] = fault
    factory, info = config.select_capture_factory(ENV)
    for _ in range(2):
        with pytest.raises(StoreError) as caught:
            with factory():
                pytest.fail("failed readiness yielded a receiver")
        assert caught.value.status == 503 and "SECRET" not in str(caught.value)
    assert_unverified(info)
    assert "SECRET" not in caplog.text
    assert len([call for call in requests.calls if call[1] == STATUS_PATH]) == 2
    assert not any("storage" in call[1] or "reserve" in call[1] for call in requests.calls)


def test_missing_quota_only_fails_real_stage_before_storage(requests):
    requests.responses[QUOTA_PATH] = StoreError(404)
    factory, info = config.select_capture_factory(ENV)
    with factory() as receiver:
        assert len(requests.calls) == 2
        with pytest.raises(StoreError) as caught:
            receiver.stage(evidence_chunks.make_chunk(b"fixture"))
        assert caught.value.status == 503
    assert [call[1] for call in requests.calls] == [STATUS_PATH, DELIVERY_PATH, QUOTA_PATH]
    assert info["readiness"] == "configured_unverified"


def test_original_result_read_path_never_checks_or_reads_private_storage(requests):
    factory, info = config.select_capture_factory(ENV)
    with factory() as receiver:
        assert receiver.delivery("sha256:" + "1" * 64) is None
    assert [call[1] for call in requests.calls] == [STATUS_PATH, DELIVERY_PATH, DELIVERY_PATH]
    assert not any("storage" in call[1] for call in requests.calls)
    assert_unverified(info)


def test_explicit_injection_wins_without_examining_invalid_environment(requests):
    @contextmanager
    def injected():
        yield "explicit receiver boundary"
    factory, info = config.select_capture_factory({"SELFLEARN_CAPTURE_BACKEND": []}, injected)
    assert factory is injected and info["readiness"] == "injected_unverified"
    assert info["configuration_valid"] is None
    assert_unverified(info)
    assert requests.calls == []
    bad, info = config.select_capture_factory(ENV, injected=False)
    with pytest.raises(StoreError):
        bad()
    assert not info["configuration_valid"]


def test_cancellation_survives_both_readiness_and_operation(requests):
    class Cancelled(BaseException):
        pass
    requests.responses[STATUS_PATH] = Cancelled("not an ordinary provider error")
    factory, _ = config.select_capture_factory(ENV)
    with pytest.raises(Cancelled):
        with factory():
            pytest.fail("cancelled readiness yielded")
    requests.responses.clear()
    with pytest.raises(Cancelled):
        with factory():
            raise Cancelled("caller cancellation")


def test_operation_errors_keep_route_domain_status(requests):
    factory, _ = config.select_capture_factory(ENV)
    with pytest.raises(StoreError) as caught:
        with factory():
            raise StoreError(429)
    assert caught.value.status == 429
