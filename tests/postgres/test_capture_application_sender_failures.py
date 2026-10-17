"""Actual small three-batch sender failures; no hosted/native/provider claims.

Real NFL forecast/capture code, real Python binder/kernel, actual ASGI and
PostgreSQL-WASM/quota are exercised. Private Storage is explicitly in memory.
The test server alone selects a smaller immutable4-member batch ceiling; the
public sender supplies neither normalized labels nor receipt/group subsets.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import pytest

from services.web.nfl_capture_applications import CaptureApplicationReceiver
from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_application_job import register_capture_application
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from test_capture_application_api import ApplicationAPITransport
from test_capture_outcomes_atomic import admit
from hosted.test_capture_application_api import receiver_for, api_client, OUTCOME

ORIGIN = "https://self-learning-beige.vercel.app"
DESTINATION = fingerprint({"base_url": ORIGIN})
BASE = "/api/v2/nfl/capture-applications"


@pytest.fixture(scope="module")
def bounded_producer(tmp_path_factory):
    checkout = os.environ.get("NFL2026_CHECKOUT")
    if not checkout:
        raise RuntimeError("Set NFL2026_CHECKOUT to the reviewed real NFL producer checkout")
    directory = tmp_path_factory.mktemp("real-small-three-batch-capture")
    script = r'''
import copy,datetime as dt,importlib.util,json,math,sys
from pathlib import Path
from scripts import export_capture_application as sender
spec=importlib.util.spec_from_file_location('f',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
root=Path(sys.argv[1]);journal=root/'original-journal'
for vintage in range(2):
 created=f'2026-09-01T{12+vintage:02d}:00:00Z'
 pool=f.cards._fixture_pool(created);pool.update(week=6,base_model_version='fixture-base')
 prototype=copy.deepcopy(pool['players'][0]);players=[]
 for index in range(3):
  player=copy.deepcopy(prototype);player.update(player=f'Receiver {index}',team='SEA',gsis_id=f'espn-{index}')
  for rung in player['rungs']:
   z=(player['mu']-rung['line'])/30;coef={'a':math.log(rung['model_prob']/(1-rung['model_prob'])),'b':0,'c':0}
   features={'market':player['market'],'line':rung['line'],'mu':player['mu'],'sd':30,'z':z,
    'p_team':player['p_team'],'calibration':coef,'correlation_tags':['game:G1']}
   context={'season':2026,'week':6,'base_model_version':'fixture-base','forecast_created_utc':created,
    'source_snapshot_id':f.lr.fingerprint({'created':created}),'source_timestamps':{'fixture':created},
    'generation_sha':'local-small-three-batch-fixture'}
   receipt=f.lr.make_receipt({**player,**rung},f.lr.base_probability(coef,z,player['p_team']),None,context,features=features)
   rung.update(model_prob=receipt['served_prob'],learning_receipt=receipt)
  players.append(player)
 pool['players']=players
 schedule={'season':2026,'games':[{'game_id':'G1','week':6,'home':'SEA','away':'LAR',
  'status':'STATUS_SCHEDULED','kickoff_utc':f.KICKOFF}]}
 recorded=(f.lr.instant(created)+dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
 f.learning_journal.record(pool,schedule,None,[],recorded,str(journal))
bundle=f.bundle(stats_rows=[f.stats_row('80',player_display_name=f'Receiver {i}') for i in range(3)],
 snap_rows=[f.snap_row(player=f'Receiver {i}') for i in range(3)])
path=f.capture.save_capture(bundle,root/'raw-authority')
print(json.dumps({'journal':f.plan(journal),'sender':sender.plan(path,sender.ORIGIN)}))
'''
    run = subprocess.run([sys.executable, "-c", script, str(directory)], cwd=checkout,
        capture_output=True, text=True, timeout=30)
    if run.returncode:
        raise RuntimeError("Real bounded NFL producer/capture fixture failed")
    value = json.loads(run.stdout)
    sys.path.insert(0, str(Path(checkout).resolve()))
    try:
        sender = importlib.import_module("scripts.export_capture_application")
    finally:
        sys.path.pop(0)
    if Path(sender.__file__).resolve() != Path(checkout).resolve() / "scripts/export_capture_application.py":
        raise RuntimeError("Imported sender is not the reviewed real NFL checkout")
    return value, sender


class SmallBatchReceiver(CaptureApplicationReceiver):
    """Trusted TEST SERVER policy, never a new caller-selected public option."""
    def register(self, value):
        request = V.validate_request(value)
        assert self._scope(request)
        ack = register_capture_application(self.journal, request, self._raw(request), max_batch_members=4)
        return {"version": 2, "registration": ack, "scope": "registration_only", "learning_accepted": False}


@contextmanager
def real_context(produced):
    bridge = ApplicationAPITransport()
    try:
        journal = NFLCaptureApplicationJournal("https://offline.supabase.invalid",
            "sb_secret_sender_failures_fixture_only", transport=bridge, page_size=100)
        plan = produced["journal"]
        admit(journal, plan["source_objects"], [row["request"]["payload"] for row in plan["generations"]])
        base, storage, _unused_fake = receiver_for(journal, destination=DESTINATION)
        namespace = staging_namespace(destination_id=DESTINATION, producer_id="nfl2026")
        bridge.request("TEST_ADMIN", "/test/provision-capture-staging", data={"namespace_id": namespace,
            "max_total_bytes": 64 * 1024 * 1024, "max_chunks": 65536, "enabled": True})
        receiver = SmallBatchReceiver(journal, base.evidence, CaptureStagingQuota(bridge.request, namespace_id=namespace),
            destination_id=DESTINATION, producer_id="nfl2026")
        with api_client(receiver) as client:
            yield journal, storage, client
    finally:
        bridge.close()


class InProcessTransport:
    """Exact HTTP bytes; faults never write fabricated evidence into SQL."""
    def __init__(self, client):
        self.client, self.calls = client, []
        self.lose_middle, self.lost, self.corrupt = False, [], None

    def __call__(self, method, path, body):
        self.calls.append((method, path, body))
        response = self.client.request(method, path, content=body, headers={
            "Authorization": "Bearer " + OUTCOME, "Content-Type": "application/json"})
        status, raw = response.status_code, bytes(response.content)
        if method == "POST" and "/steps/" in path and status == 200 and self.lose_middle:
            result = response.json()["result"]
            if result["ordinal"] == 1:
                self.lose_middle = False
                self.lost.append(copy.deepcopy(result))
                raise TimeoutError("SECRET_LOST_MIDDLE_REPLY_AFTER_REAL_SQL")
        if self.corrupt and method == "GET" and status == 200:
            raw = self.corrupt(path, response.json())
        return status, raw


def test_actual_lost_middle_batch_stays_pending_then_recovers_without_duplicate_or_partial_success(
        bounded_producer, tmp_path):
    produced, sender = bounded_producer
    with real_context(produced) as (journal, storage, client):
        original = copy.deepcopy(journal.receipts())
        assert len(original) == 12
        transport = InProcessTransport(client)
        outbox = tmp_path / "separate-outbox"
        value = sender.send(produced["sender"], outbox, OUTCOME, transport=transport,
            attempts=1, max_steps=1, sleep=lambda _: None)
        assert value["status"] == "pending" and value["learning_accepted"] is False
        aid = produced["sender"]["request"]["application_id"]
        first = journal.get_capture_application(aid)
        assert [b["member_count"] for b in first["seal"]["batches"]] == [4, 4, 4]
        assert first["terminal"] is None
        transport.lose_middle = True
        with patch.object(sender.O, "_validated", side_effect=AssertionError("No client raw reload after registration")):
            middle = sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
                attempts=1, max_steps=1, sleep=lambda _: None)
            assert middle["status"] == "pending" and middle["learning_accepted"] is False
            assert middle["reason"] == "application_pending_step_limit"
            state = journal.get_capture_application(aid)
            assert state["terminal"] is None and len(transport.lost) == 1
            assert sum(row["result"] is not None for row in state["batches"]) == 2
            assert len(journal.read_observed_snapshot()["revisions"]) == 8
            final = sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
                attempts=1, max_steps=1, sleep=lambda _: None)
        assert final["status"] == "terminal_original" and final["complete_success"] is True
        assert final["result"]["counts"]["accepted_members"] == 12
        assert final["result"]["counts"]["accepted_groups"] == 3
        posts = [path for method, path, body in transport.calls if method == "POST" and "/steps/" in path]
        assert len(posts) == len(set(posts)) == 3, "Lost original must not send another batch/duplicate command"
        durable = journal.read_observed_snapshot()
        assert durable["receipts"] == original and len(durable["observations"]) == len(durable["revisions"]) == 12
        assert len(storage.objects) == 3


@pytest.mark.parametrize("fault", ["omit_complete_group", "reseal_wrong_terminal_count"])
def test_actual_sender_refuses_partial_or_resealed_wrong_report_and_recovers_true_original(
        bounded_producer, tmp_path, fault):
    produced, sender = bounded_producer
    with real_context(produced) as (journal, storage, client):
        transport = InProcessTransport(client); outbox = tmp_path / "separate-outbox"
        final = sender.send(produced["sender"], outbox, OUTCOME, transport=transport,
            attempts=1, max_steps=3, sleep=lambda _: None)
        assert final["complete_success"] is True
        aid = produced["sender"]["request"]["application_id"]
        before, snapshot = len(transport.calls), copy.deepcopy(journal.read_observed_snapshot())
        storage_before = len(storage.calls)
        def corrupt(path, value):
            view = parse_qs(urlsplit(path).query).get("view", [None])[0]
            if fault == "omit_complete_group" and view == "groups":
                assert len(value["records"]) == 3
                value["records"].pop(); value["total_records"] -= 1; value["next_after"] = None
            elif fault == "reseal_wrong_terminal_count" and view == "terminal":
                terminal = value["records"][0]
                terminal["counts"]["accepted_members"] -= 4
                terminal["terminal_result_id"] = fingerprint({k: v for k, v in terminal.items() if k != "terminal_result_id"})
            return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
        transport.corrupt = corrupt
        refused = sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
            attempts=1, max_steps=1, sleep=lambda _: None)
        assert refused["status"] == "pending" and refused["reason"] == "application_protocol_refused"
        assert refused["learning_accepted"] is refused["complete_success"] is False
        assert all(method == "GET" for method, path, body in transport.calls[before:])
        assert len(storage.calls) == storage_before and journal.read_observed_snapshot() == snapshot
        transport.corrupt = None
        recovered = sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
            attempts=1, max_steps=1, sleep=lambda _: None)
        assert recovered == final
        assert len(storage.calls) == storage_before and journal.read_observed_snapshot() == snapshot
