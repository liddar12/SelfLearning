"""Real ASGI routes with an explicit durable SQLite handoff and original NFL graph."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
import pytest

from selflearn_core.adapters.nfl_collection_handoff import SQLiteCollectionHandoff
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import ReceiptJournal
from services.web.application import create_app
from services.web.security import NFL_COLLECTION_PATH as BASE
from .fakes import FakeStore, TEST_KEY
from .publication_fixture import graph

INGEST = "local-forecast-ingest-" + "i" * 40
COLLECTION = "local-collection-handoff-" + "c" * 40
ENV = {"SELFLEARN_OPERATOR_TOKEN": TEST_KEY, "SELFLEARN_NFL_INGEST_TOKEN": INGEST,
       "SELFLEARN_NFL_COLLECTION_MODE": "record", "SELFLEARN_NFL_COLLECTION_REPOSITORY": "fixture/nfl2026",
       "SELFLEARN_NFL_COLLECTION_TOKEN": COLLECTION}


def client_for(path, env=ENV):
    return TestClient(create_app(store=FakeStore(), env=env, journal_factory=lambda: ReceiptJournal(path),
                                 collection_handoff_store=SQLiteCollectionHandoff))


def call(client, method, path, value=None, token=COLLECTION, expected=200):
    response = client.request(method, path, json=value, headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def intent_for(doc, *, run="123", attempt="1", workflow="daily.yml", **overrides):
    gen = doc["generations"][0]
    return {"version": 1, "workflow": workflow, "run_id": run, "run_attempt": attempt,
            "generation_object_id": doc["envelope_id"], "generation_id": gen["generation_id"],
            "recorded_utc": gen["recorded_utc"], "latest_kickoff_utc": doc["receipts"][0]["kickoff_utc"],
            "source_snapshots": gen["source_snapshots"], **overrides}


def endpoint(intent):
    return BASE + "/" + "/".join(intent[k] for k in ("workflow", "run_id", "run_attempt"))


def record(view, **overrides):
    return {"version": 1, "intent_id": view["intent"]["intent_id"], "published_sha": "a" * 40,
            "published_pool_id": view["intent"]["source_snapshots"]["leg_pool"], "previous_pool_id": None,
            **overrides}


def upload(client, doc, sources, *, generation=True):
    for source in sources.values():
        call(client, "POST", "/api/v1/nfl/objects",
             {"version": 1, "kind": "source_snapshot", "object_id": fingerprint(source), "payload": source}, token=INGEST)
    if generation:
        call(client, "POST", "/api/v1/nfl/objects",
             {"version": 1, "kind": "generation", "object_id": doc["envelope_id"], "payload": doc}, token=INGEST)


def test_lost_replies_restart_and_exact_durable_generation_recovery(tmp_path):
    path = tmp_path / "intents.sqlite"
    doc, sources = graph()
    intent = intent_for(doc)
    location = endpoint(intent)
    with client_for(path) as first:
        call(first, "POST", BASE, intent)  # Producer discards the reply.
        upload(first, doc, sources, generation=False)
    with client_for(path) as second:
        viewed = call(second, "GET", location)
        assert viewed["state"] == "awaiting_objects"
        assert viewed["missing_object_ids"] == [doc["envelope_id"]]
        assert viewed["generation_ack"] is None
        assert viewed["intent"]["recorded_utc"] == intent["recorded_utc"]
        assert call(second, "POST", BASE, intent)["intent"] == viewed["intent"]
        recover = {"version": 1, "intent_id": viewed["intent"]["intent_id"]}
        assert call(second, "POST", location + "/recover", recover)["state"] == "awaiting_objects"
        assert call(second, "GET", BASE + "/pending")["pending"] == []
        upload(second, doc, {}, generation=True)
        assert call(second, "GET", location)["state"] == "ready_to_finalize"
        assert call(second, "GET", BASE + "/pending")["pending"] == [{
            "workflow": intent["workflow"], "run_id": intent["run_id"], "run_attempt": intent["run_attempt"],
            "intent_id": recover["intent_id"]}]
        call(second, "POST", location + "/recover", recover)  # Lost finalized ACK.
    with client_for(path) as third:
        finalized = call(third, "GET", location)
        assert finalized["state"] == "awaiting_publisher"
        assert finalized["generation_ack"]["durable"] is True
        assert finalized["intake_complete"] is True
        assert finalized["intent"] == viewed["intent"]
        assert [row["intent_id"] for row in call(third, "GET", BASE + "/pending")["pending"]] == [recover["intent_id"]]
        call(third, "POST", location + "/publisher", record(finalized))  # Lost publisher ACK.
    with client_for(path) as fourth:
        ready = call(fourth, "GET", location)
        assert ready["state"] == "publication_ready"
        assert ready["publication_ready"] is True
        assert ready["publication_verified"] is ready["training_approved"] is False
        assert ready["publisher_receipt"]["disposition"] == "match"
        assert ready["generation_ack"] == finalized["generation_ack"]
        assert [row["intent_id"] for row in call(fourth, "GET", BASE + "/pending")["pending"]] == [recover["intent_id"]]
        assert call(fourth, "POST", location + "/recover", recover)["generation_ack"] == ready["generation_ack"]
        assert call(fourth, "POST", location + "/publisher", record(ready))["publisher_receipt"] == ready["publisher_receipt"]
        altered = {**intent, "recorded_utc": "2026-09-01T12:02:00Z"}
        call(fourth, "POST", BASE, altered, expected=409)
        call(fourth, "POST", location + "/publisher", record(ready, published_sha="b" * 40), expected=409)
        call(fourth, "POST", location + "/recover", {"version": 1, "intent_id": fingerprint("alien")}, expected=409)
    with ReceiptJournal(path) as durable:
        assert len(durable.generations()) == 1
        assert durable.get_object(doc["envelope_id"])["payload"] == doc
        assert durable._db.execute("SELECT count(*) FROM nfl_collection_handoff_v1").fetchone()[0] == 2


@pytest.mark.parametrize("disposition", ["noop", "unchanged", "mismatch"])
def test_publisher_disposition_is_immutable_and_never_enqueues(tmp_path, disposition):
    doc, sources = graph()
    intent = intent_for(doc)
    location = endpoint(intent)
    with client_for(tmp_path / "publisher.sqlite") as client:
        staged = call(client, "POST", BASE, intent)
        match = staged["intent"]["source_snapshots"]["leg_pool"]
        changes = {"noop": {"published_sha": None, "published_pool_id": None},
                   "unchanged": {"previous_pool_id": match},
                   "mismatch": {"published_pool_id": fingerprint("fallback pool")}}[disposition]
        saved = call(client, "POST", location + "/publisher", record(staged, **changes))
        assert saved["state"] == "awaiting_objects" and not saved["publication_ready"]
        assert saved["publisher_receipt"]["disposition"] == disposition
        upload(client, doc, sources)
        final = call(client, "POST", location + "/recover", {"version": 1, "intent_id": staged["intent"]["intent_id"]})
        assert final["state"] == "held_publisher_" + disposition
        assert final["generation_ack"]["durable"] and not final["publication_ready"]


@pytest.mark.parametrize("tamper", ["latest_kickoff_utc", "generation_id", "recorded_utc", "source_snapshots"])
def test_existing_object_graph_must_exactly_match_intent_before_finalize(tmp_path, tamper):
    doc, sources = graph()
    altered = {"latest_kickoff_utc": "2099-12-01T00:00:00Z", "generation_id": fingerprint("foreign generation"),
               "recorded_utc": "2026-09-01T12:02:00Z",
               "source_snapshots": {**doc["generations"][0]["source_snapshots"], "parlay_calibration": fingerprint({})}}
    intent = intent_for(doc, **{tamper: altered[tamper]})
    with client_for(tmp_path / "conflict.sqlite") as client:
        staged = call(client, "POST", BASE, intent)
        upload(client, doc, {**sources, "foreign_source": {}})
        held = call(client, "POST", endpoint(intent) + "/recover",
                    {"version": 1, "intent_id": staged["intent"]["intent_id"]})
        assert held["state"] == "held_invalid_generation"
        assert not held["publication_ready"] and held["generation_ack"] is None


def test_late_graph_and_future_producer_clock_do_not_gain_acceptance(tmp_path):
    now = datetime.now(timezone.utc)
    kickoff = (now - timedelta(minutes=1)).isoformat()
    doc, sources = graph(kickoff)
    intent = intent_for(doc, workflow="gameday.yml")
    with client_for(tmp_path / "late.sqlite") as client:
        staged = call(client, "POST", BASE, intent)
        assert staged["state"] == "held_late"
        upload(client, doc, sources)
        call(client, "POST", endpoint(intent) + "/publisher", record(staged))
        late = call(client, "POST", endpoint(intent) + "/recover",
                    {"version": 1, "intent_id": staged["intent"]["intent_id"]})
        assert late["state"] == "held_late" and late["generation_ack"] is None
        assert not late["publication_ready"]
        assert call(client, "GET", BASE + "/pending")["pending"] == []
        future = (now + timedelta(hours=1)).isoformat()
        call(client, "POST", BASE, intent_for(doc, run="124", recorded_utc=future,
             latest_kickoff_utc="2099-12-01T00:00:00Z"), expected=422)
        call(client, "GET", endpoint({**intent, "run_id": "124"}), expected=404)


def test_publication_readiness_refreshes_database_clock_after_validating_ack(tmp_path):
    doc, sources = graph()
    intent = intent_for(doc)
    path = endpoint(intent)
    with client_for(tmp_path / "cutoff.sqlite") as client:
        staged = call(client, "POST", BASE, intent)
        upload(client, doc, sources)
        call(client, "POST", path + "/recover", {"version": 1, "intent_id": staged["intent"]["intent_id"]})
        ready = call(client, "POST", path + "/publisher", record(staged))
        assert ready["publication_ready"]

        original_read = SQLiteCollectionHandoff.read
        reads = []

        def across_kickoff(store, key):
            result = original_read(store, key)
            reads.append(result["observed_utc"])
            return {**result, "observed_utc": intent["latest_kickoff_utc"]} if len(reads) > 1 else result

        # Advance the test adapter's observed clock between initial status and
        # readiness. Persisted clocks, graph and finalization stay unmodified.
        with patch.object(SQLiteCollectionHandoff, "read", across_kickoff):
            late = call(client, "GET", path)
        assert len(reads) == 2
        assert late["state"] == "held_late" and not late["publication_ready"]
        assert late["generation_ack"] == ready["generation_ack"]


def test_scan_cursor_advances_over_missing_rows_and_omits_other_repository(tmp_path):
    path = tmp_path / "list.sqlite"
    doc, sources = graph()
    with client_for(path) as client:
        original = call(client, "POST", BASE, intent_for(doc))["intent"]["intent_id"]
        for run in ("124", "125", "126", "127"):
            call(client, "POST", BASE, intent_for(doc, run=run, generation_object_id=fingerprint(run)))
        upload(client, doc, sources)
        after = None
        pending = []
        for _ in range(5):
            url = BASE + "/pending?limit=1" + ("&after=" + after if after else "")
            page = call(client, "GET", url)
            pending.extend(page["pending"])
            if page["next_after"] is None:
                break
            assert after is None or page["next_after"] > after
            after = page["next_after"]
        assert [row["intent_id"] for row in pending] == [original]
        assert page["next_after"] is None
        for query in ("limit=51", "limit=1&limit=2", "after=invalid", "workflow=gameday.yml"):
            call(client, "GET", BASE + "/pending?" + query, expected=422)
    with client_for(path, {**ENV, "SELFLEARN_NFL_COLLECTION_REPOSITORY": "another/nfl2026"}) as other:
        call(other, "GET", endpoint(intent_for(doc)), expected=404)
        assert call(other, "GET", BASE + "/pending")["pending"] == []


def test_default_off_and_scope_refuse_mixed_receipt_values(tmp_path):
    doc, _ = graph()
    intent = intent_for(doc)
    path = tmp_path / "scopes.sqlite"
    with client_for(path, {**ENV, "SELFLEARN_NFL_COLLECTION_MODE": "off"}) as disabled:
        call(disabled, "POST", BASE, intent, expected=503)
    with client_for(path) as client:
        call(client, "POST", BASE, intent, token=TEST_KEY, expected=401)
        call(client, "POST", BASE, intent, token=INGEST, expected=401)
        call(client, "POST", "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]}, expected=401)
        call(client, "POST", BASE, {**intent, "workflow": "other.yml"}, expected=422)
        call(client, "POST", BASE, {**intent, "repository": "other/nfl2026"}, expected=422)
        staged = call(client, "POST", BASE, intent)
        loc = endpoint(intent)
        for updates in ({"published_sha": None}, {"published_pool_id": None}, {"published_sha": "x" * 40},
                        {"published_sha": True}, {"previous_pool_id": False}, {"published_sha": None,
                            "published_pool_id": None, "previous_pool_id": fingerprint("mixed")}):
            call(client, "POST", loc + "/publisher", record(staged, **updates), expected=422)
        assert call(client, "GET", loc)["publisher_receipt"] is None
