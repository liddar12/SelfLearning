"""Independent full-app owner-route checks; local SQLite/ASGI, not cloud proof."""
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from services.web.application import create_app
from services.web.owner_intents import OwnerIntentReceiver, PATH
from services.web.security import MAX_JSON


AUTHORITY = fingerprint("independent-full-app-owner-boundary")
ACTOR = "explicit-local-test-owner"
NS = {"domain": "nfl", "task": "prop.yardage", "producer_id": "nfl2026-local"}
EVIDENCE = {"kind": "unverified-domain-evidence", "value": 1}
OID = fingerprint(EVIDENCE)
OWNER, FORECAST, OUTCOME = (kind + "-fixture-only-" + "x" * 40
                            for kind in ("owner", "forecast", "outcome"))
ENV = {"SELFLEARN_OPERATOR_TOKEN": OWNER, "SELFLEARN_NFL_INGEST_TOKEN": FORECAST,
       "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": OUTCOME}


def command(head=None, action="approve", **changes):
    value = {"version": 1, "kind": "selflearn.owner_intent_command.v1", "namespace": NS.copy(),
             "action": action, "expected_head_event_id": head,
             "pins": {key: fingerprint(key) for key in dto.PIN_KEYS - {"evidence_object_ids"}},
             "effective_from_utc": None,
             "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z",
             "reason": "Explicit local test intent, not a release authorization"}
    value["pins"]["evidence_object_ids"] = [OID]
    value.update(changes)
    return dto.seal_command(value, authority_id=AUTHORITY)


def headers(token=OWNER):
    return {"Authorization": "Bearer " + token}


@pytest.fixture
def runtime(tmp_path):
    path = tmp_path / "independent-owner.sqlite"
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
        journal.append_object("source_snapshot", OID, EVIDENCE)
    calls = []

    @contextmanager
    def factory():
        calls.append("opened")
        with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
            yield OwnerIntentReceiver(journal, authority_id=AUTHORITY, actor_ref=ACTOR, namespace=NS)

    return path, factory, calls


def test_default_off_even_with_unrelated_environment_claims(runtime):
    _, _, calls = runtime
    env = {**ENV, "SELFLEARN_OWNER_INTENTS_ENABLED": "true",
           "SELFLEARN_OWNER_AUTHORITY_ID": AUTHORITY, "SELFLEARN_OWNER_ACTOR_REF": ACTOR}
    with TestClient(create_app(store=object(), env=env)) as client:
        for response in (client.get(PATH, headers=headers()),
                         client.post(PATH, json=command(), headers=headers())):
            assert response.status_code == 503
            assert response.headers["cache-control"] == "no-store"
        assert client.get("/api/health").json()["mode"] == "L0"
    assert calls == []


def test_actual_app_exact_retry_after_revocation_is_not_current_authorization(runtime):
    path, factory, _ = runtime
    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=factory)) as client:
        first = client.post(PATH, json=command(), headers=headers())
        assert first.status_code == 200
        original = first.json()
        revoked = client.post(PATH, json=command(original["event_id"], "revoke"), headers=headers())
        assert revoked.status_code == 200
        assert client.post(PATH, json=command(), headers=headers()).json() == original
        response = client.get(PATH, headers=headers())
        history = response.json()
        assert history["head_sequence"] == 2 and history["head_event_id"] == revoked.json()["event_id"]
        assert history["events"][1]["command"]["action"] == "revoke"
        assert history["content_hash_verified"] is True
        assert history["domain_pin_binding_verified"] is False
        assert history["current_serving_verified"] is False and history["activation"] is False
        assert response.headers["cache-control"] == "no-store"
        assert OWNER not in response.text and FORECAST not in response.text and OUTCOME not in response.text
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as journal:
        assert journal.receipts() == [] and journal.generations() == [] and journal.latest_resolutions() == []


@pytest.mark.parametrize("credential", [None, FORECAST, OUTCOME, "invalid-owner"])
def test_both_verbs_reject_nonoperator_without_opening_journal(runtime, credential):
    _, factory, calls = runtime
    supplied = {} if credential is None else headers(credential)
    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=factory)) as client:
        for response in (client.get(PATH, headers=supplied),
                         client.post(PATH, json=command(), headers=supplied)):
            assert response.status_code == 401
            assert response.headers["cache-control"] == "no-store"
            assert response.headers["www-authenticate"] == "Bearer"
    assert calls == []


def test_origin_and_streamed_body_limits_precede_factory(runtime):
    _, factory, calls = runtime
    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=factory)) as client:
        rejected = client.post(PATH, json=command(), headers={**headers(), "Origin": "https://untrusted.example"})
        assert rejected.status_code == 403
        # Generator has no Content-Length: the middleware must count actual
        # streamed bytes instead of relying on a caller-provided size claim.
        response = client.post(PATH, content=(chunk for chunk in (b"x" * MAX_JSON, b"x")),
                               headers={**headers(), "Content-Type": "application/json"})
        assert response.status_code == 413
        oversized = client.post(PATH, content=b"{}", headers={**headers(),
            "Content-Type": "application/json", "Content-Length": str(MAX_JSON + 1)})
        assert oversized.status_code == 413
        assert response.headers["cache-control"] == "no-store"
    assert calls == []


@pytest.mark.parametrize("raw", ['{"version":1,"version":1}', '{"value":NaN}',
                                '{"value":Infinity}', '[' * 26 + '0' + ']' * 26])
def test_actual_shared_body_parser_rejects_ambiguous_or_unbounded_json(runtime, raw):
    _, factory, calls = runtime
    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=factory)) as client:
        response = client.post(PATH, content=raw, headers={**headers(), "Content-Type": "application/json"})
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
    assert calls == []


def test_persisted_intents_do_not_add_mcp_mutations(runtime):
    _, factory, _ = runtime
    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=factory)) as client:
        assert client.post(PATH, json=command(), headers=headers()).status_code == 200
        response = client.post("/mcp", headers={**headers(), "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": "2025-11-25"},
             json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert response.status_code == 200
        tools = response.json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == {"get_status", "list_tasks", "list_predictions", "get_nfl_forecast_trace"}
        assert all(tool["annotations"]["readOnlyHint"] is True for tool in tools)


def test_factory_fault_is_sanitized_in_actual_app(runtime):
    _, _, calls = runtime

    @contextmanager
    def unavailable():
        calls.append("opened")
        raise RuntimeError("PRIVATE_PROVIDER_DIAGNOSTIC_" + OWNER)
        yield

    with TestClient(create_app(store=object(), env=ENV, owner_intent_factory=unavailable)) as client:
        response = client.get(PATH, headers=headers())
        assert response.status_code == 503 and "PRIVATE_PROVIDER" not in response.text and OWNER not in response.text
        assert response.headers["cache-control"] == "no-store"
    assert calls == ["opened"]
