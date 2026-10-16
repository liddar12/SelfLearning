"""Actual Python/ASGI -> private owner SQL on PostgreSQL-WASM, not native/cloud.

This opt-in suite fails if the reviewed already-installed WASM is absent.
It never installs dependencies, opens a listener, or accesses a provider.
"""
import copy
from contextlib import contextmanager
import os
import subprocess

import pytest
from fastapi.testclient import TestClient

from selflearn_core import owner_intents as dto
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.store.supabase_owner_intents import SupabaseOwnerIntentJournal
from selflearn_core.store.supabase_store import StoreError
from services.web.application import create_app
from services.web.owner_intents import OwnerIntentReceiver, PATH
from test_adapter_integration import PostgresBridge, ROOT
from tests.hosted.fakes import FakeStore, TEST_KEY


AUTHORITY, ACTOR = fingerprint("actual-owner-port-authority"), "configured-pilot-owner"
NS = {"domain": "elections", "task": "election.binary", "producer_id": "local-pilot"}
EVIDENCE = {"unverified_candidate": {"a": 0.0, "b": 1.0, "zero": -0.0, "unicode": "é🚀"}}
OID = fingerprint(EVIDENCE)


class OwnerBridge(PostgresBridge):
    def __init__(self):
        if not os.environ.get("SELFLEARN_PGLITE_MODULE"):
            raise RuntimeError("Explicit reviewed SELFLEARN_PGLITE_MODULE is required; no SQL test ran")
        self.process = subprocess.Popen(["node", "tests/postgres/owner_intents_bridge.mjs"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1)
        self.calls, self.counter = [], 0
        try:
            self.ready = self._read(timeout=30)
            if self.ready.get("engine") != "postgresql-wasm" or self.ready.get("ready") is not True:
                raise RuntimeError("Actual owner PostgreSQL bridge failed initialization")
        except BaseException:
            self.close(); raise


def port(bridge, **options):
    return SupabaseOwnerIntentJournal("https://offline.supabase.invalid", "sb_secret_owner_offline_fixture",
        authority_id=AUTHORITY, actor_ref=ACTOR, transport=bridge, page_size=1, **options)


def command(head=None, action="approve", **changes):
    value = {"version": 1, "kind": "selflearn.owner_intent_command.v1", "namespace": dict(NS),
        "action": action, "expected_head_event_id": head,
        "pins": {k: fingerprint(k) for k in dto.PIN_KEYS - {"evidence_object_ids"}},
        "effective_from_utc": None, "expires_utc": None if action == "revoke" else "2099-01-01T00:00:00Z",
        "reason": "Actual local SQL operator intent; no model activation"}
    value["pins"]["evidence_object_ids"] = [OID]; value.update(changes)
    return dto.seal_command(value, authority_id=AUTHORITY)


@pytest.fixture
def actual():
    bridge = OwnerBridge()
    try:
        journal = port(bridge)
        assert journal.append_object("source_snapshot", OID, EVIDENCE) is True
        yield journal, bridge
    finally:
        bridge.close()


def test_actual_python_port_source_lexemes_clocks_paging_and_original_lost_ack(actual):
    journal, bridge = actual
    baseline = journal.status()
    first = journal.append_owner_intent(command())
    revoked = journal.append_owner_intent(command(first["event_id"], "revoke"))
    assert journal.append_owner_intent(command()) == first
    result = journal.read_owner_intents(NS)
    assert result["head_sequence"] == 2 and result["head_event_id"] == revoked["event_id"]
    assert result["events"][0]["actor_ref"] == ACTOR
    assert journal.get_object(OID)["payload"] == EVIDENCE
    assert all(result[k] == v for k, v in dto.FLAGS.items())
    assert baseline == journal.status()  # Separate owner sequence, not labels.
    assert bridge.ready["engine"] == "postgresql-wasm"
    assert any(c["data"].get("p_watermark") == result["watermark"] for c in bridge.calls)


def test_actual_cas_missing_proof_and_false_ack_readback_do_not_claim_success(actual):
    journal, bridge = actual
    first = journal.append_owner_intent(command())
    with pytest.raises(JournalConflict): journal.append_owner_intent(command(reason="New intent with stale head"))
    bad = command(first["event_id"]); bad["pins"]["evidence_object_ids"] = [fingerprint("missing")]
    bad = dto.seal_command({k: v for k, v in bad.items() if k != "command_id"}, authority_id=AUTHORITY)
    with pytest.raises(StoreError) as error: journal.append_owner_intent(bad)
    assert error.value.status == 422
    original = bridge.request
    def false_ack(method, path, *, data):
        response = original(method, path, data=data)
        if path.endswith("append_owner_intent_v1"): response["sequence"] = 99
        return response
    malformed = port(false_ack)
    with pytest.raises(StoreError) as error: malformed.append_owner_intent(command(first["event_id"], "revoke"))
    assert error.value.status == 502
    assert journal.append_owner_intent(command(first["event_id"], "revoke"))["sequence"] == 2
    assert journal.read_owner_intents(NS)["head_sequence"] == 2


def test_actual_application_operator_boundary_to_sql_and_complete_get(actual):
    journal, bridge = actual
    @contextmanager
    def factory():
        yield OwnerIntentReceiver(port(bridge), authority_id=AUTHORITY, actor_ref=ACTOR, namespace=NS)
    env = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": "forecast-fixture-" + "f" * 40,
           "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": "outcome-fixture-" + "o" * 40}
    app = create_app(store=FakeStore(), env=env, owner_intent_factory=factory)
    owner = {"Authorization": "Bearer " + TEST_KEY}
    with TestClient(app) as client:
        first = client.post(PATH, json=command(), headers=owner)
        assert first.status_code == 200 and first.json()["activation"] is False
        second = client.post(PATH, json=command(first.json()["event_id"], "revoke"), headers=owner)
        assert second.status_code == 200
        assert client.post(PATH, json=command(), headers=owner).json() == first.json()
        before = len(bridge.calls)
        for token in (env["SELFLEARN_NFL_INGEST_TOKEN"], env["SELFLEARN_NFL_OUTCOME_INGEST_TOKEN"]):
            assert client.get(PATH, headers={"Authorization": "Bearer " + token}).status_code == 401
            assert client.post(PATH, json=command(), headers={"Authorization": "Bearer " + token}).status_code == 401
        assert len(bridge.calls) == before
        result = client.get(PATH, headers=owner)
        assert result.status_code == 200 and result.json()["complete"] is True and result.json()["head_sequence"] == 2
        assert result.headers["cache-control"] == "no-store"
        assert result.json()["domain_pin_binding_verified"] is False
