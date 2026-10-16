"""Real NFL1088-member sender → ASGI → SQL whole-group application.

The original forecast/capture code runs, not a reimplemented normalized-label
producer. SQL is PostgreSQL-WASM and private Storage is an in-memory fake.
This explicitly does NOT prove hosted/native concurrency, provider authenticity,
production rollout, model adoption or prediction improvement.
"""
from __future__ import annotations

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.capture_staging import CaptureStagingQuota, staging_namespace
from test_capture_application_api import ApplicationAPITransport
from test_capture_outcomes_atomic import admit
from hosted.test_capture_application_api import receiver_for, api_client, OUTCOME


ORIGIN = "https://self-learning-beige.vercel.app"
DESTINATION = fingerprint({"base_url": ORIGIN})
BASE = "/api/v2/nfl/capture-applications"
CHUNKS = "/api/v1/nfl/capture-chunks"


class TimedSQLBridge(ApplicationAPITransport):
    def __init__(self):
        self.timings = []
        super().__init__()

    def request(self, method, path, *, data):
        began = time.monotonic()
        try:
            return super().request(method, path, data=data)
        finally:
            self.timings.append({"method": method, "path": path,
                                 "seconds": time.monotonic() - began})


@pytest.fixture(scope="module")
def large_producer(tmp_path_factory):
    """1088 original rungs,185 players,4 accepted vintages; no sports data edit."""
    selected = os.environ.get("NFL2026_CHECKOUT")
    if not selected:
        raise RuntimeError("Set NFL2026_CHECKOUT to the reviewed real producer checkout")
    checkout = Path(selected).resolve()
    directory = tmp_path_factory.mktemp("real-nfl1088-application")
    script = r'''
import copy,datetime as dt,importlib.util,json,math,sys
from pathlib import Path
from scripts import export_capture_application as sender
spec=importlib.util.spec_from_file_location('fixture',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
root=Path(sys.argv[1]);journal=root/'original-journal'
for vintage in range(4):
 created=f'2026-09-01T{12+vintage:02d}:00:00Z'
 pool=f.cards._fixture_pool(created);pool.update(week=6,base_model_version='fixture-base')
 prototype=copy.deepcopy(pool['players'][0]);players=[]
 for index in (range(185) if vintage<2 else range(173) if vintage==2 else range(1)):
  player=copy.deepcopy(prototype);player.update(player=f'Receiver {index}',team='SEA',gsis_id=f'espn-{index}')
  for rung in player['rungs']:
   z=(player['mu']-rung['line'])/30
   coef={'a':math.log(rung['model_prob']/(1-rung['model_prob'])),'b':0,'c':0}
   features={'market':player['market'],'line':rung['line'],'mu':player['mu'],'sd':30,'z':z,
    'p_team':player['p_team'],'calibration':coef,'correlation_tags':['game:G1']}
   context={'season':2026,'week':6,'base_model_version':'fixture-base','forecast_created_utc':created,
    'source_snapshot_id':f.lr.fingerprint({'created':created}),'source_timestamps':{'fixture':created},
    'generation_sha':'local-expanded-fixture'}
   receipt=f.lr.make_receipt({**player,**rung},f.lr.base_probability(coef,z,player['p_team']),None,context,features=features)
   rung.update(model_prob=receipt['served_prob'],learning_receipt=receipt)
  players.append(player)
 pool['players']=players
 schedule={'season':2026,'games':[{'game_id':'G1','week':6,'home':'SEA','away':'LAR',
  'status':'STATUS_SCHEDULED','kickoff_utc':f.KICKOFF}]}
 recorded=(f.lr.instant(created)+dt.timedelta(minutes=1)).isoformat().replace('+00:00','Z')
 f.learning_journal.record(pool,schedule,None,[],recorded,str(journal))
bundle=f.bundle(stats_rows=[f.stats_row('80',player_display_name=f'Receiver {i}') for i in range(185)],
 snap_rows=[f.snap_row(player=f'Receiver {i}') for i in range(185)])
capture_path=f.capture.save_capture(bundle,root/'raw-authority')
print(json.dumps({'journal':f.plan(journal),'sender':sender.plan(capture_path,sender.ORIGIN)}))
'''
    run = subprocess.run([sys.executable, "-c", script, str(directory)], cwd=checkout,
        capture_output=True, text=True, timeout=90)
    if run.returncode:
        raise RuntimeError("Real local NFL1088 producer fixture failed")
    value = json.loads(run.stdout)
    assert len(value["journal"]["generations"]) == 4
    return value


@pytest.fixture(scope="module")
def v2_sender():
    checkout = Path(os.environ["NFL2026_CHECKOUT"]).resolve()
    sys.path.insert(0, str(checkout))
    try:
        sender = importlib.import_module("scripts.export_capture_application")
    finally:
        sys.path.pop(0)
    if Path(sender.__file__).resolve() != checkout / "scripts/export_capture_application.py":
        raise RuntimeError("The imported v2 sender is not the reviewed NFL checkout")
    assert sender.ORIGIN == ORIGIN
    return sender


class TimedASGITransport:
    """Actual request bytes; controlled lost replies happen AFTER real SQL."""
    def __init__(self, client, *, lose_registration=True, lose_final=True):
        self.client, self.calls, self.responses, self.timings = client, [], [], []
        self.lose_registration, self.lose_final = lose_registration, lose_final
        self.lost = []

    def __call__(self, method, path, body):
        assert method in ("GET", "POST")
        assert path == BASE or path == CHUNKS or path.startswith(BASE + "/sha256%3A")
        assert body is None or type(body) is bytes and len(body) <= 3 * 1024 * 1024
        self.calls.append((method, path, body))
        began = time.monotonic()
        reply = self.client.request(method, path, content=body, headers={
            "Authorization": "Bearer " + OUTCOME, "Content-Type": "application/json"})
        self.timings.append({"method": method, "path": path, "seconds": time.monotonic() - began,
                             "status": reply.status_code})
        self.responses.append((reply.status_code, bytes(reply.content)))
        assert reply.headers["cache-control"] == "no-store"
        if method == "POST" and path == BASE and reply.status_code == 200 and self.lose_registration:
            self.lose_registration = False
            self.lost.append("registration")
            raise TimeoutError("PRIVATE_LOST_REGISTRATION_REPLY")
        if method == "POST" and "/steps/" in path and reply.status_code == 200 and self.lose_final:
            result = reply.json()["result"]
            if result["ordinal"] == 2:
                self.lose_final = False
                self.lost.append("final_batch")
                raise TimeoutError("PRIVATE_LOST_FINAL_BATCH_REPLY")
        return reply.status_code, bytes(reply.content)


def test_real1088_sender_resumes_whole_groups_lost_replies_and_original_without_raw(
        large_producer, v2_sender, tmp_path):
    bridge = TimedSQLBridge()
    try:
        # Use the existing maximum page size, never1088 single-member requests.
        journal = NFLCaptureApplicationJournal("https://offline.supabase.invalid",
            "sb_secret_large_application_fixture_only", transport=bridge, page_size=100)
        plan = large_producer["journal"]
        admit(journal, plan["source_objects"], [r["request"]["payload"] for r in plan["generations"]])
        originals = copy.deepcopy(journal.receipts())
        assert len(originals) == 1088
        receiver, storage, _ = receiver_for(journal, destination=DESTINATION)
        namespace = staging_namespace(destination_id=DESTINATION, producer_id="nfl2026")
        bridge.request("TEST_ADMIN", "/test/provision-capture-staging", data={"namespace_id": namespace,
            "max_total_bytes": 64 * 1024 * 1024, "max_chunks": 65536, "enabled": True})
        receiver.quota = CaptureStagingQuota(bridge.request, namespace_id=namespace)
        sender_plan = large_producer["sender"]
        outbox = tmp_path / "separate-outbox"
        with api_client(receiver) as client:
            transport = TimedASGITransport(client)
            # A step-limited first process must remain pending, not claim the
            # registration/first batch as acceptance of the full capture.
            first = v2_sender.send(sender_plan, outbox, OUTCOME, transport=transport,
                attempts=2, max_steps=1, max_run_seconds=300, sleep=lambda _: None)
            assert first["status"] == "pending" and first["learning_accepted"] is False, first
            assert first["reason"] == "application_pending_step_limit", first
            aid = sender_plan["request"]["application_id"]
            middle = journal.get_capture_application(aid)
            assert middle["registration"]["member_count"] == 1088
            assert middle["registration"]["group_count"] == 185
            assert middle["registration"]["batch_count"] == 3
            assert middle["seal"]["limits"]["members"] == 400
            assert middle["seal"]["limits"]["observations"] == 400
            assert middle["seal"]["limits"]["revisions"] == 400
            # Different vintage counts create6/8-member groups. Never split
            # the next whole group merely to fill a two-member spare slot.
            assert [b["member_count"] for b in middle["seal"]["batches"]] == [400, 398, 290]
            assert middle["terminal"] is None
            assert sum(b["result"] is not None for b in middle["batches"]) == 1
            # A fresh sender invocation recovers pinned original work, then
            # applies only the two remaining server-assigned whole batches.
            final = v2_sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
                attempts=2, max_steps=2, max_run_seconds=300, sleep=lambda _: None)
            assert final["status"] == "terminal_original" and final["complete_success"] is True, final
            terminal = final["result"]
            assert terminal["status"] == "accepted" and terminal["processing_complete"] is True
            assert terminal["counts"]["members"] == terminal["counts"]["accepted_members"] == 1088
            assert terminal["counts"]["groups"] == terminal["counts"]["accepted_groups"] == 185
            assert terminal["counts"]["committed_batches"] == 3
            assert transport.lost == ["registration", "final_batch"]
            after = journal.read_observed_snapshot()
            assert after["receipts"] == originals
            assert len(after["observations"]) == len(after["revisions"]) == 1088
            assert all(row["resolution"]["hit"] is True for row in after["revisions"])
            storage_before, sql_before = len(storage.calls), len(bridge.calls)
            storage.bucket["public"] = True
            # The original can be recovered even if the saved capture cannot
            # be loaded; recovery must not re-enter raw verification/staging.
            from unittest.mock import patch
            with patch.object(v2_sender.O, "_validated", side_effect=AssertionError("no raw reload")):
                original = v2_sender.resume(outbox, aid, ORIGIN, OUTCOME, transport=transport,
                    attempts=2, max_steps=1, max_run_seconds=300, sleep=lambda _: None)
            assert original == final and len(storage.calls) == storage_before
            assert not any(c["path"].endswith(("register_capture_application_v2", "commit_capture_application_batch_v2"))
                           for c in bridge.calls[sql_before:])
            posts = [(m, p) for m, p, _ in transport.calls if m == "POST"]
            assert sum(p == BASE for _, p in posts) == 1
            assert sum("/steps/" in p for _, p in posts) == 3
            usage = bridge.request("TEST_ADMIN", "/test/capture-staging-summary", data={"namespace_id": namespace})
            assert usage["reserved_chunks"] == len(storage.objects)
            assert int(usage["reserved_total_bytes"]) == sum(len(raw) for raw in storage.objects.values())
            if os.environ.get("SELFLEARN_ASSERT_LOCAL_CAPTURE_BUDGET") == "1":
                # Explicit standalone performance probe only: slower CI or
                # overlapping test runs must not masquerade as platform proof.
                assert max(row["seconds"] for row in bridge.timings) < 12, bridge.timings
                assert max(row["seconds"] for row in transport.timings) < 45, transport.timings
            # Print measured timings, not a platform-readiness assertion. The
            # unmocked job45s and local bridge20s bounds remain in force.
            print(json.dumps({"scope": "actual_NFL1088_sender_ASGI_PostgreSQL_WASM_fake_Storage",
                "http_requests": transport.timings,
                "sql_requests": len(bridge.timings),
                "max_sql_request_seconds": max(row["seconds"] for row in bridge.timings),
                "slowest_sql_requests": sorted(bridge.timings, key=lambda row: row["seconds"], reverse=True)[:5],
                "local_budget_probe_enabled": os.environ.get("SELFLEARN_ASSERT_LOCAL_CAPTURE_BUDGET") == "1",
                "hosted_native_verified": False}, sort_keys=True))
    finally:
        bridge.close()
