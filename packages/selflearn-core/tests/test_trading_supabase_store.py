"""Scoped RPC contracts with synthetic tokens; no hosted requests or accounts."""

import base64
import json
import threading
import time
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from selflearn_core.store import TradingStorageError, TradingSupabaseStore
from selflearn_core.store.trading_supabase_store import RPCResponse, _http_transport
from selflearn_core.types import Outcome, Prediction

REF = "abcdefghijklmnopqrst"
URL = f"https://{REF}.supabase.co"
PUBLIC_KEY = "sb_publishable_fixture"
WORKER_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
SNAPSHOT = "2026-11-10T12:00:00+00:00"


def token(**overrides):
    claims = {"sub": WORKER_ID, "role": "authenticated", "aud": "authenticated",
              "iss": f"{URL}/auth/v1", "exp": int(time.time()) + 3600, **overrides}
    encode = lambda x: base64.urlsafe_b64encode(json.dumps(x).encode()).decode().rstrip("=")
    return f"{encode({'alg': 'HS256'})}.{encode(claims)}.fixture_signature"


def prediction(pid="p1", task="scanner", cohort="SPY"):
    return Prediction(id=pid, ts=1000, model_version="fixture-v1", task=task,
                      features={"observation_ts": 1000}, prediction={"direction": "up"},
                      horizon_s=100, confidence=.7, cohort=cohort)


def row(pid="p1", receipt="2026-11-10T11:00:00+00:00", task="scanner", cohort="SPY"):
    return {**asdict(prediction(pid, task, cohort)), "received_at": receipt,
            "actor_user_id": WORKER_ID}


def resolved_row(pid="p1", receipt="2026-11-10T11:00:00+00:00", **kwargs):
    return {**row(pid, receipt, **kwargs), "outcomes": [{
        "prediction_id": pid, "resolved_ts": 1200, "realized": {"hit": True},
        "meta": {}, "received_at": "2026-11-10T11:30:00+00:00",
        "actor_user_id": WORKER_ID,
    }]}


class Transport:
    def __init__(self, *, principal="worker", replies=None):
        self.calls = []
        self.principal = principal
        self.replies = {name: list(values) for name, values in (replies or {}).items()}

    def __call__(self, url, *, headers, body, timeout):
        rpc = url.rsplit("/", 1)[-1]
        params = json.loads(body)
        self.calls.append((rpc, params, dict(headers), url, timeout))
        if rpc == "trade_bot_status":
            result = {"schema_version": "shared-trading-v1", "principal_kind": self.principal,
                      "server_time": SNAPSHOT}
        else:
            result = self.replies[rpc].pop(0)
        if isinstance(result, Exception):
            raise result
        if isinstance(result, RPCResponse):
            return result
        return RPCResponse(200, json.dumps(result).encode())


def store(transport, **options):
    return TradingSupabaseStore(url=URL, expected_project_ref=REF,
                                publishable_key=PUBLIC_KEY,
                                access_token_provider=lambda: token(), transport=transport,
                                **options)


@pytest.mark.parametrize("url", ["http://localhost", "https://evil.example", URL + "/rest/v1",
                                  URL + ":443", URL + "?redirect=evil", "https://user:pass@" + REF + ".supabase.co"])
def test_target_must_match_explicit_project(url):
    with pytest.raises(ValueError, match="expected project"):
        TradingSupabaseStore(url=url, expected_project_ref=REF, publishable_key=PUBLIC_KEY,
                             access_token_provider=lambda: token())


@pytest.mark.parametrize("key", ["sb_secret_do_not_send", token(role="service_role"), token()])
def test_elevated_or_user_api_keys_refused(key):
    with pytest.raises(ValueError):
        TradingSupabaseStore(url=URL, expected_project_ref=REF, publishable_key=key,
                             access_token_provider=lambda: token())


def test_legacy_anon_key_supported_without_elevating_bearer():
    public = token(role="anon", ref=REF)
    transport = Transport()
    worker = token()
    instance = TradingSupabaseStore(url=URL, expected_project_ref=REF, publishable_key=public,
                                    access_token_provider=lambda: worker, transport=transport)
    instance.check_ready()
    headers = transport.calls[0][2]
    assert headers["apikey"] == public
    assert headers["Authorization"] == f"Bearer {worker}"


@pytest.mark.parametrize("claims", [{"role": "service_role"}, {"role": "anon"},
                                     {"iss": "https://other.supabase.co/auth/v1"},
                                     {"exp": 1}, {"sub": "invalid"}, {"aud": "service_role"}])
def test_unscoped_worker_tokens_never_leave_process(claims):
    transport = Transport()
    instance = TradingSupabaseStore(url=URL, expected_project_ref=REF, publishable_key=PUBLIC_KEY,
                                    access_token_provider=lambda: token(**claims), transport=transport)
    with pytest.raises(TradingStorageError, match="scoped Auth worker token"):
        instance.check_ready()
    assert transport.calls == []


def test_owner_principal_cannot_use_worker_store():
    transport = Transport(principal="owner")
    with pytest.raises(TradingStorageError, match="not authorized"):
        store(transport).append_prediction(prediction())
    assert [c[0] for c in transport.calls] == ["trade_bot_status"]


def test_writes_only_fixed_rpc_with_current_worker_token(monkeypatch):
    monkeypatch.setenv("SUPABASE_SECRET_KEY", "sb_secret_never_used")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "elevated_never_used")
    tokens = [token(jti="status"), token(jti="write")]
    transport = Transport(replies={"trade_bot_append_prediction": ["p1"]})
    instance = TradingSupabaseStore(url=URL, expected_project_ref=REF, publishable_key=PUBLIC_KEY,
                                    access_token_provider=lambda: tokens.pop(0), transport=transport)
    assert instance.append_prediction(prediction()) == "p1"
    call = transport.calls[-1]
    assert call[0] == "trade_bot_append_prediction"
    assert call[1] == {"p_record": asdict(prediction())}
    assert call[3] == f"{URL}/rest/v1/rpc/trade_bot_append_prediction"
    assert call[2]["apikey"] == PUBLIC_KEY
    assert call[2]["Authorization"] != transport.calls[0][2]["Authorization"]
    assert "never_used" not in repr(transport.calls)
    assert "received_at" not in call[1]["p_record"]


@pytest.mark.parametrize("task", ["nfl.game", "wc.match", "trading.", "trading.foo?x=1", " scanner", "public.predictions"])
def test_foreign_or_manipulated_tasks_rejected_before_network(task):
    transport = Transport()
    instance = store(transport)
    with pytest.raises(ValueError):
        instance.append_prediction(prediction(task=task))
    with pytest.raises(ValueError):
        list(instance.resolved(task))
    assert transport.calls == []


def test_outcome_write_and_get_roundtrip():
    transport = Transport(replies={"trade_bot_attach_outcome": ["p1"],
                                   "trade_bot_get_prediction": [row(), None]})
    instance = store(transport)
    outcome = Outcome("p1", 1200, {"hit": True})
    assert instance.attach_outcome(outcome) is None
    assert instance.get_prediction("p1") == prediction()
    assert instance.get_prediction("missing") is None
    assert transport.calls[1][1] == {"p_record": asdict(outcome)}


def test_resolved_pages_use_fixed_receipt_cutoff_and_filter_after_scan():
    first = resolved_row("c", "2026-11-10T11:00:00+00:00")
    second = resolved_row("b", "2026-11-10T10:00:00+00:00", cohort="MSFT")
    third = resolved_row("a", "2026-11-10T09:00:00+00:00")
    third["outcomes"][0]["resolved_ts"] = 1300
    transport = Transport(replies={"trade_bot_list_resolved": [[first, second], [third]]})
    result = list(store(transport, page_size=2).resolved("scanner", since=1250, cohort="SPY"))
    assert [r.prediction.id for r in result] == ["a"]
    calls = [c for c in transport.calls if c[0] == "trade_bot_list_resolved"]
    assert len(calls) == 2
    assert all(c[1]["p_as_of"] == SNAPSHOT for c in calls)
    assert "p_before_id" not in calls[0][1]
    assert calls[1][1]["p_before_id"] == "b"
    assert calls[1][1]["p_before_received_at"] == second["received_at"]
    assert result[0].outcome.prediction_id == "a"


@pytest.mark.parametrize("bad_kind", ["foreign_task", "duplicate", "wrong_order", "future_prediction", "future_outcome", "wrong_join"])
def test_invalid_or_cross_domain_pages_fail_without_partial_results(bad_kind):
    first = resolved_row("b", "2026-11-10T11:00:00+00:00")
    second = resolved_row("a", "2026-11-10T10:00:00+00:00")
    if bad_kind == "foreign_task":
        second["task"] = "nfl.game"
    elif bad_kind == "duplicate":
        second["id"] = "b"
    elif bad_kind == "wrong_order":
        first, second = second, first
    elif bad_kind == "future_prediction":
        first["received_at"] = "2026-11-11T00:00:00+00:00"
    elif bad_kind == "future_outcome":
        first["outcomes"][0]["received_at"] = "2026-11-11T00:00:00+00:00"
    else:
        second["outcomes"][0]["prediction_id"] = "nfl-prediction"
    transport = Transport(replies={"trade_bot_list_resolved": [[first, second]]})
    with pytest.raises(TradingStorageError):
        store(transport).resolved("scanner")


def test_page_limit_never_silently_truncates():
    transport = Transport(replies={"trade_bot_list_resolved": [[resolved_row()]]})
    with pytest.raises(TradingStorageError, match="page limit"):
        store(transport, page_size=1, max_pages=1).resolved("scanner")


def test_repeated_cursor_fails_on_next_page():
    transport = Transport(replies={"trade_bot_list_resolved": [[resolved_row()], [resolved_row()]]})
    with pytest.raises(TradingStorageError, match="duplicate"):
        store(transport, page_size=1).resolved("scanner")


def test_unresolved_rpc_preserves_task_and_resolution_horizon():
    transport = Transport(replies={"trade_bot_list_unresolved": [[row()]]})
    assert [p.id for p in store(transport).unresolved("scanner", 1100)] == ["p1"]
    assert transport.calls[-1][1]["p_now_ts"] == 1100
    transport = Transport(replies={"trade_bot_list_unresolved": [[row()]]})
    with pytest.raises(TradingStorageError, match="before its horizon"):
        store(transport).unresolved("scanner", 1099)


@pytest.mark.parametrize("response", [RPCResponse(307, b"secret_redirect"), RPCResponse(500, b"secret_body"),
                                      RPCResponse(200, b'{"bad":NaN}'), RPCResponse(200, b'{"bad":1e999}'),
                                      RPCResponse(200, b'not_json'), RuntimeError("secret_exception")])
def test_upstream_errors_are_sanitized_and_never_retried(response):
    transport = Transport(replies={"trade_bot_get_prediction": [response]})
    with pytest.raises(TradingStorageError) as raised:
        store(transport).get_prediction("p1")
    assert "secret" not in str(raised.value)
    assert len(transport.calls) == 2


def test_nonfinite_or_oversized_records_fail_before_any_network():
    transport = Transport()
    instance = store(transport)
    with pytest.raises(ValueError):
        instance.append_prediction(replace(prediction(), prediction={"price": float("nan")}))
    with pytest.raises(ValueError, match="safe bound"):
        instance.append_prediction(replace(prediction(), features={"text": "x" * 64000}))
    assert transport.calls == []


def test_real_transport_does_not_follow_redirect_or_forward_authorization(monkeypatch):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(self.path)
            self.send_response(307)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/target")
            self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    try:
        response = _http_transport(f"http://127.0.0.1:{server.server_port}/source",
                                   headers={"Authorization": "Bearer synthetic_fixture"},
                                   body=b"{}", timeout=2)
        assert response.status == 307
        assert calls == ["/source"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
