"""Real NFL fixture → observed adapter → actual PostgreSQL migration/RPCs.

All evidence is synthetic local test data; no network, source data writes,
coefficient fitting, deployment or performance claim. Native concurrency and
hosted PostgREST remain separate checks.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_observed_resolution import BRIDGE_VERSION, resolve_observed
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.scoring.binary_receipts import score_binary_receipts
from test_adapter_integration import PostgresBridge, db

DELIVERED = "2026-10-01T00:00:00Z"


@pytest.fixture(scope="module")
def producer():
    checkout = Path(os.environ.get("NFL2026_CHECKOUT", "")).resolve()
    required = checkout / "tests/fixtures/week6_outcomes.py"
    if not os.environ.get("NFL2026_CHECKOUT") or not required.is_file():
        raise RuntimeError("Explicit updated NFL2026_CHECKOUT is required for actual producer SQL evidence")
    script = """
import json,pathlib,runpy,sys,tempfile
sys.path.insert(0,sys.argv[1])
from scripts import build_learning_outcomes as outcomes
f=runpy.run_path(str(pathlib.Path(sys.argv[1])/'tests/fixtures/week6_outcomes.py'))
with tempfile.TemporaryDirectory() as tmp:
 root=pathlib.Path(tmp); a,_,_=f['generation'](root)
 b,_,_=f['generation'](root,'2026-09-01T13:00:00Z'); plan=f['plan'](root)
 inputs=[f['bundle'](),f['bundle'](observed='2026-09-03T14:00:00Z'),
         f['bundle'](value='20',observed='2026-09-04T12:00:00Z'),
         f['bundle'](value='80',observed='2026-09-05T12:00:00Z')]
 # Published week but target absent from stats and team participation sheet.
 inputs.append(f['bundle'](observed='2026-09-06T12:00:00Z',
  stats_rows=[f['stats_row']('20',player_display_name='Other Receiver')],
  snap_rows=[f['snap_row'](player='Other Receiver')]))
 print(json.dumps({'docs':[a,b],'plan':plan,'observations':[outcomes.build(plan,x) for x in inputs]}))
"""
    result = subprocess.run([sys.executable, "-c", script, str(checkout)],
                            capture_output=True, text=True, cwd=checkout, timeout=30, check=False)
    if result.returncode:
        raise AssertionError("Actual NFL capture/outcome producer failed")
    return json.loads(result.stdout)


@pytest.fixture
def journal(producer):
    bridge = PostgresBridge()
    try:
        store = db(bridge)
        # Fixture index only: observation bodies persist through real SQL as
        # immutable generic source objects. Production still needs an
        # authoritative observation index and atomic fence+revision commit;
        # this harness does not claim to implement that missing store port.
        store._fixture_observation_objects = set()
        batch = adapt_prospective(journal_docs=producer["docs"])
        assert batch["rejections"] == [] and len(batch["receipts"]) == 4
        for request in producer["plan"]["source_objects"]:
            store.append_object(request["kind"], request["object_id"], request["payload"])
        for doc in producer["docs"]:
            store.append_object("generation", doc["envelope_id"], doc)
            source_ids = {row["receipt_id"] for row in doc["receipts"]}
            selected = [r for r in batch["receipts"] if r["source_receipt_id"] in source_ids]
            store.commit_generation(doc["envelope_id"], selected, [])
        yield store
    finally:
        bridge.close()


def resolve(journal, document):
    history = journal.read_snapshot()["revisions"]
    observations = [journal.get_object(oid)["payload"] for oid in sorted(journal._fixture_observation_objects)]
    return resolve_observed(journal.receipts(), document["source_receipts"], document["observed_legs"],
                            document["game_observations"], observed_utc=DELIVERED,
                            existing_revisions=history, existing_observations=observations)


def append(journal, batch):
    assert batch["rejections"] == [], batch["summary"]
    assert len(batch["proposals"]) == 4
    for record in batch["observation_proposals"]:
        oid = fingerprint(record)
        journal.append_object("source_snapshot", oid, record)
        journal._fixture_observation_objects.add(oid)
    return [journal.append_resolution(**proposal) for proposal in batch["proposals"]]


def test_actual_producer_vintages_replays_corrections_and_pinned_offer_history(journal, producer):
    receipts = copy.deepcopy(journal.receipts())
    initial, unchanged, correction, restored, void = producer["observations"]
    first = resolve(journal, initial)
    assert append(journal, first) == [True] * 4
    fence = journal.status()["watermark"]
    first_histories = {r["receipt_id"]: journal.resolution_history(r["receipt_id"]) for r in receipts}
    assert all(row["resolution"]["evidence_basis"] == BRIDGE_VERSION for row in first["proposals"])
    assert all("source_updated_utc" not in row["resolution"] for row in first["proposals"])
    assert append(journal, resolve(journal, unchanged)) == [False] * 4
    assert {r["receipt_id"]: journal.resolution_history(r["receipt_id"]) for r in receipts} == first_histories
    assert append(journal, resolve(journal, correction)) == [True] * 4
    assert all(row["hit"] is False for row in journal.latest_resolutions())
    assert append(journal, resolve(journal, initial)) == [False] * 4
    assert all(row["hit"] is False for row in journal.latest_resolutions()), "old delivery must not revert the latest head"
    # A genuinely newer A after B is a new observation, not a fake official ID.
    assert append(journal, resolve(journal, restored)) == [True] * 4
    assert all(row["hit"] is True for row in journal.latest_resolutions())
    old_rows, _ = journal._read("latest_resolutions", watermark=fence)
    assert len(old_rows) == 4
    assert all(json.loads(row["payload_json"])["resolution"]["actual"] == 80 for row in old_rows)
    # DNP remains explicitly inferred, never a fabricated binary loss.
    assert append(journal, resolve(journal, void)) == [True] * 4
    latest = journal.latest_resolutions()
    assert all(row["status"] == "VOID" and row["hit"] is None for row in latest)
    assert all(row["participation_basis"] == "r106_team_snap_absence_inference" for row in latest)
    assert score_binary_receipts(journal.receipts(), latest)["n"] == 0
    assert journal.receipts() == receipts
    assert all(len(journal.resolution_history(r["receipt_id"])) == 4 for r in receipts)
    snapshot = journal.read_snapshot()
    assert len(snapshot["receipts"]) == 4
    assert len(snapshot["generation_records"]) == 2
    assert len(snapshot["revisions"]) == 16
    assert all(g["ack"]["durable"] is True for g in snapshot["generation_records"])
    for row in snapshot["revisions"]:
        assert row["resolution"]["evidence_basis"] == BRIDGE_VERSION
        assert "source_revision_id" not in row["resolution"]
