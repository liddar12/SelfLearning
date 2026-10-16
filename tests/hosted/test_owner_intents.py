"""Actual local SQLite + owner middleware/router; NOT hosted Supabase proof."""
import json
from contextlib import contextmanager

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.owner_intent_journal import OwnerIntentJournal
from selflearn_core.store.supabase_store import StoreError
from services.web.owner_intents import OwnerIntentReceiver, PATH, register_owner_intent_routes
from services.web.security import OwnerBoundary, MAX_JSON


AUTHORITY = fingerprint("http-local-owner-authority")
ACTOR = "configured-pilot-owner"
NS = {"domain": "example", "task": "binary.forecast", "producer_id": "local-producer"}
EVIDENCE = {"candidate": "unverified bytes, NOT a statistically verified artifact"}
OID = fingerprint(EVIDENCE)
OPERATOR, INGEST, OUTCOME = (prefix + "-fixture-only-" + "k" * 40 for prefix in ("operator", "forecast", "outcome"))


def command(head=None, action="approve", **changes):
    value = {"version": 1, "kind": "selflearn.owner_intent_command.v1", "namespace": NS.copy(),
        "action": action, "expected_head_event_id": head, "pins": {k: fingerprint(k) for k in dto.PIN_KEYS - {"evidence_object_ids"}},
        "effective_from_utc": None, "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z", "reason": "Explicit operator action"}
    value["pins"]["evidence_object_ids"] = [OID]; value.update(changes)
    return dto.seal_command(value, authority_id=AUTHORITY)


async def strict_body(request):
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out: raise ValueError()
            out[k] = v
        return out
    try:
        return json.loads(await request.body(), object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, RecursionError): raise HTTPException(422, "Invalid JSON") from None


def app(factory=None, *, operator=OPERATOR):
    value = FastAPI()
    value.add_middleware(OwnerBoundary, token=operator, origins={"https://approved.example"}, ingest_token=INGEST, capture_token=OUTCOME)
    register_owner_intent_routes(value, factory, strict_body)
    return TestClient(value)


@pytest.fixture
def factory(tmp_path):
    path = tmp_path / "owner.sqlite"
    with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as j:
        j.append_object("source_snapshot", OID, EVIDENCE)
    @contextmanager
    def context():
        with OwnerIntentJournal(path, authority_id=AUTHORITY, actor_ref=ACTOR) as j:
            yield OwnerIntentReceiver(j, authority_id=AUTHORITY, actor_ref=ACTOR, namespace=NS)
    return context


def headers(token=OPERATOR): return {"Authorization": "Bearer " + token}


def test_owner_only_approve_revoke_and_original_retry_preserve_nonactivation(factory):
    with app(factory) as client:
        first = client.post(PATH, json=command(), headers=headers())
        assert first.status_code == 200
        ack = first.json()
        revoked = client.post(PATH, json=command(ack["event_id"], "revoke"), headers=headers())
        assert revoked.status_code == 200
        assert client.post(PATH, json=command(), headers=headers()).json() == ack
        history = client.get(PATH, headers=headers())
        assert history.status_code == 200 and history.json()["head_sequence"] == 2
        assert history.json()["activation"] is False and history.json()["domain_pin_binding_verified"] is False
        assert history.headers["cache-control"] == "no-store"
        assert history.json()["events"][0]["actor_ref"] == ACTOR
        assert "token" not in json.dumps(history.json())


@pytest.mark.parametrize("token", [INGEST, OUTCOME, "wrong-key"])
def test_other_scopes_cannot_read_or_record_intents(factory, token):
    with app(factory) as client:
        assert client.post(PATH, json=command(), headers=headers(token)).status_code == 401
        assert client.get(PATH, headers=headers(token)).status_code == 401
        assert client.get(PATH, headers=headers()).json()["head_sequence"] == 0


def test_default_disabled_missing_token_bad_origin_and_body_limits(factory):
    with app() as client:
        assert client.get(PATH, headers=headers()).status_code == 503
        assert client.post(PATH, json=command(), headers=headers()).status_code == 503
    with app(factory, operator=None) as client:
        assert client.get(PATH, headers=headers()).status_code == 503
    with app(factory) as client:
        assert client.get(PATH).status_code == 401
        assert client.get(PATH, headers={**headers(), "Origin": "https://wrong.example"}).status_code == 403
        assert client.post(PATH, content="{}", headers={**headers(), "Content-Type": "text/plain"}).status_code == 415
        assert client.post(PATH, content=b"x" * (MAX_JSON + 1), headers={**headers(), "Content-Type": "application/json"}).status_code == 413
        assert client.post(PATH, content='{"version":1,"version":1}', headers={**headers(), "Content-Type": "application/json"}).status_code == 422
        assert client.post(PATH, content='{"version":NaN}', headers={**headers(), "Content-Type": "application/json"}).status_code == 422
        assert client.get(PATH, headers=headers()).json()["head_sequence"] == 0


def test_wrong_namespace_authority_clock_actor_and_head_never_become_success(factory):
    with app(factory) as client:
        for value in (command(namespace={**NS, "producer_id": "other"}),
                      {**command(), "actor_ref": "client-admin"},
                      {**command(), "recorded_utc": "2001-01-01T00:00:00Z"},
                      {**command(), "version": True}):
            assert client.post(PATH, json=value, headers=headers()).status_code == 422
        value = command(); value["command_id"] = fingerprint("different-authority-command")
        assert client.post(PATH, json=value, headers=headers()).status_code == 422
        first = client.post(PATH, json=command(), headers=headers()).json()
        assert client.post(PATH, json=command(reason="A changed command against old head"), headers=headers()).status_code == 409
        assert client.get(PATH, headers=headers()).json()["head_event_id"] == first["event_id"]


def test_case_insensitive_json_and_sanitized_provider_faults(factory):
    with app(factory) as client:
        response = client.post(PATH, content=dto.canonical(command()), headers={**headers(), "Content-Type": "Application/JSON; charset=utf-8"})
        assert response.status_code == 200
    @contextmanager
    def broken(): raise RuntimeError("SECRET_PROVIDER_CREDENTIAL"); yield
    with app(broken) as client:
        result = client.get(PATH, headers=headers())
        assert result.status_code == 503 and "SECRET" not in result.text
    @contextmanager
    def quota(): raise StoreError(429); yield
    with app(quota) as client: assert client.get(PATH, headers=headers()).status_code == 429
    @contextmanager
    def bad_receiver(): yield object()
    with app(bad_receiver) as client: assert client.get(PATH, headers=headers()).status_code == 503


def test_backend_ack_tamper_cannot_echo_metadata_or_claim_success(factory):
    @contextmanager
    def malformed():
        with factory() as receiver:
            original = receiver.journal.append_owner_intent
            receiver.journal.append_owner_intent = lambda value: {**original(value), "secret": "SECRET_BACKEND"}
            yield receiver
    with app(malformed) as client:
        result = client.post(PATH, json=command(), headers=headers())
        assert result.status_code == 503 and "SECRET" not in result.text
    with app(factory) as client:
        # It was committed, but the malformed reply was never a success; retry
        # obtains the original exact event without creating a duplicate.
        assert client.post(PATH, json=command(), headers=headers()).json()["sequence"] == 1


def test_factory_receiver_and_read_corruption_validation(factory):
    with factory() as receiver:
        with pytest.raises(ValueError): OwnerIntentReceiver(receiver.journal, authority_id=fingerprint("wrong"), actor_ref=ACTOR, namespace=NS)
    @contextmanager
    def corrupt():
        with factory() as receiver:
            receiver.journal.read_owner_intents = lambda _: {"events": [], "watermark": "0", "observed_utc": "2099-01-01T00:00:00Z", "complete": True}
            yield receiver
    with app(corrupt) as client: assert client.get(PATH, headers=headers()).status_code == 503
