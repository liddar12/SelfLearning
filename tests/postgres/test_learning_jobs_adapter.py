"""Actual Python job port -> PostgreSQL-WASM. No hosted/native inference."""
import copy
import datetime as dt
import os
import subprocess
import time

import pytest

from selflearn_core import learning_jobs as J
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_learning_jobs import SupabaseLearningJobJournal
from selflearn_core.store.supabase_store import StoreError
from test_adapter_integration import PostgresBridge, ROOT

AUTHORITY=fingerprint("actual-job-port-authority")


class JobBridge(PostgresBridge):
    def __init__(self):
        if not os.environ.get("SELFLEARN_PGLITE_MODULE"):raise RuntimeError("Explicit already-installed PostgreSQL-WASM module required")
        self.process=subprocess.Popen(["node","tests/postgres/learning_jobs_bridge.mjs"],cwd=ROOT,
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding="utf-8",bufsize=1)
        self.calls,self.counter=[],0
        try:
            self.ready=self._read(timeout=30)
            assert self.ready.get("ready") is True and self.ready.get("engine")=="postgresql-wasm"
        except BaseException:self.close();raise


def port(transport):
    return SupabaseLearningJobJournal("https://offline.supabase.invalid","sb_secret_jobs_offline_fixture",
        authority_id=AUTHORITY,transport=transport)


def definition(journal,**changes):
    inputs,policy={"synthetic_job_input":True},{"synthetic_job_policy":True}
    for payload in(inputs,policy):journal.append_object("source_snapshot",fingerprint(payload),payload)
    now=dt.datetime.now(dt.timezone.utc)
    return J.seal_spec({"version":1,"kind":"selflearn.job_definition.v1",
        "namespace":{"domain":"elections","task":"election.binary","producer_id":"local-pilot"},
        "operation":"shadow_evaluation","input_object_id":fingerprint(inputs),"policy_object_id":fingerprint(policy),
        "code_id":fingerprint("synthetic-job-code"),"not_before_utc":J.stamp(now-dt.timedelta(seconds=10)),
        "not_after_utc":J.stamp(now+dt.timedelta(minutes=5)),"lease_seconds":30,"max_runtime_seconds":60,
        "max_attempts":3,"max_steps":4,**changes},authority_id=AUTHORITY)


def command(state,action="claim",*,worker="worker-a",object_id=None,reason=None):
    return J.seal_command({"version":1,"kind":"selflearn.job_command.v1","job_id":state["definition"]["job_id"],
        "expected_head_event_id":state["head_event_id"],"action":action,"worker_id":worker,
        "lease_id":None if action=="claim"else state["state"]["lease_id"],"object_id":object_id,"reason_code":reason},authority_id=AUTHORITY)


@pytest.fixture
def actual():
    bridge=JobBridge()
    try:yield port(bridge),bridge
    finally:bridge.close()


def test_actual_python_sql_roundtrip_history_reexecution_scope_and_original_lost_ack(actual):
    journal,bridge=actual;spec=definition(journal);state=journal.register_job(spec);job_id=spec["job_id"]
    status=journal.status();assert journal.register_job(spec)["registered_utc"]==state["registered_utc"]
    claim=command(state);first=journal.append_job_command(claim);state=journal.read_job(job_id)
    payload={"checkpoint":1,"float_preserved":1.0};oid=fingerprint(payload)
    journal.append_object("source_snapshot",oid,payload);checkpoint=command(state,"checkpoint",object_id=oid)
    original=bridge.request
    def lost_reply(method,path,*,data):
        result=original(method,path,data=data)
        if path.endswith("jobs_append_v1"):raise StoreError(503)
        return result
    with pytest.raises(StoreError):port(lost_reply).append_job_command(checkpoint)
    recovered=journal.append_job_command(checkpoint);assert recovered["sequence"]==2
    state=journal.read_job(job_id);journal.append_job_command(command(state,"complete",object_id=oid))
    done=journal.read_job(job_id);assert done["effective_status"]=="succeeded" and done["state"]["steps"]==2
    assert journal.append_job_command(claim)==first and first["current_lease_authority"] is False
    assert journal.get_object(oid)["payload"]==payload
    # Job transitions alone do not advance the prediction/outcome watermark.
    expected=journal.status();journal.read_job(job_id);assert journal.status()==expected
    assert bridge.ready["engine"]=="postgresql-wasm" and status["schema_version"]==1


def test_actual_sql_expiry_reclaim_and_new_python_worker_reject_old_fence(actual):
    journal,bridge=actual;spec=definition(journal,lease_seconds=1);state=journal.register_job(spec);job_id=spec["job_id"]
    journal.append_job_command(command(state));old=journal.read_job(job_id)
    time.sleep(1.1)
    replacement=port(bridge);state=replacement.read_job(job_id);assert state["effective_status"]=="expired_lease"
    replacement.append_job_command(command(state,worker="worker-b"));state=replacement.read_job(job_id)
    assert state["state"]["attempt"]==2 and state["state"]["lease_id"]!=old["state"]["lease_id"]
    with pytest.raises(J.JobConflict):journal.append_job_command(command(old,"release",reason="paused"))
    replacement.append_job_command(command(state,"fail",worker="worker-b",reason="evaluation_failed"))
    assert replacement.read_job(job_id)["effective_status"]=="failed"


def test_actual_sql_rehashed_response_state_or_false_ack_is_not_a_valid_transition(actual):
    journal,bridge=actual;spec=definition(journal);state=journal.register_job(spec);job_id=spec["job_id"]
    original=bridge.request
    def false_ack(method,path,*,data):
        result=original(method,path,data=data)
        if path.endswith("jobs_append_v1"):result["sequence"]=99
        return result
    claim=command(state)
    with pytest.raises(StoreError)as error:port(false_ack).append_job_command(claim)
    assert error.value.status==502
    assert journal.append_job_command(claim)["sequence"]==1
    def rehashed_state(method,path,*,data):
        result=copy.deepcopy(original(method,path,data=data))
        if path.endswith("jobs_read_v1"):
            event=result["events"][0];event["state"]["attempt"]=7
            event["event_id"]=fingerprint({k:v for k,v in event.items()if k!="event_id"})
            result["state"]=event["state"];result["head_event_id"]=event["event_id"]
        return result
    with pytest.raises(StoreError)as error:port(rehashed_state).read_job(job_id)
    assert error.value.status==502
    assert journal.read_job(job_id)["state"]["attempt"]==1


def test_actual_nfl_shadow_fitter_pause_resume_and_original_result_through_full_sql_chain(actual):
    import json
    import sys
    from unittest.mock import patch
    from selflearn_core.adapters import nfl_evaluation_job as N
    from selflearn_core.adapters.nfl_calibration import walk_forward_report
    previous_path=sys.path[:];sys.path.insert(0,str(ROOT/"packages/selflearn-core/tests"))
    try:
        from test_nfl_calibration import corpus
        receipts,sources,revisions=corpus(n=110)
    finally:sys.path[:]=previous_path
    journal,bridge=actual
    inputs={"version":1,"kind":"nfl.shadow_job_inputs.v1","receipts":receipts,"source_receipts":sources,
        "revisions":revisions,"generation_records":None,"as_of_utc":"2026-10-15T00:00:00Z"}
    settings=N.policy()
    for payload in(inputs,settings):journal.append_object("source_snapshot",fingerprint(payload),payload)
    now=dt.datetime.now(dt.timezone.utc)
    spec=J.seal_spec({"version":1,"kind":"selflearn.job_definition.v1","namespace":dict(N.NS),"operation":N.OPERATION,
        "input_object_id":fingerprint(inputs),"policy_object_id":fingerprint(settings),"code_id":J.code_identity(),
        "not_before_utc":J.stamp(now-dt.timedelta(seconds=10)),"not_after_utc":J.stamp(now+dt.timedelta(minutes=5)),
        "lease_seconds":120,"max_runtime_seconds":180,"max_attempts":3,"max_steps":2},authority_id=AUTHORITY)
    journal.register_job(spec);job_id=spec["job_id"]
    with patch.object(N.calibration,"walk_forward_report",wraps=walk_forward_report)as fitter:
        pause=N.run_shadow_job(journal,job_id,authority_id=AUTHORITY,worker_id="first",max_phases=1)
        assert pause["status"]=="paused"and fitter.call_count==0
        result=N.run_shadow_job(port(bridge),job_id,authority_id=AUTHORITY,worker_id="replacement",max_phases=1)
        recovered=N.run_shadow_job(port(bridge),job_id,authority_id=AUTHORITY,worker_id="retry")
    assert fitter.call_count==1 and result["result"]["report"]["candidate"] is not None
    assert recovered["recovered_original"] is True and recovered["result_object_id"]==result["result_object_id"]
    assert all(result[k] is False for k in N.FALSE_FLAGS)
    state=journal.read_job(job_id)
    assert state["effective_status"]=="succeeded"and state["state"]["steps"]==2 and state["state"]["attempt"]==2
    assert [e["command"]["action"]for e in state["events"]]==["claim","checkpoint","release","claim","complete"]
    saved=journal.get_object(state["state"]["result_object_id"])["payload"]
    assert saved==result["result"] and saved["report"]["report_id"]==result["result"]["report"]["report_id"]
    assert len(json.dumps(saved))<3*1024*1024


def test_actual_bounded_nfl_child_result_and_execution_receipt_through_full_sql_chain(actual):
    import json
    import sys
    from unittest.mock import patch
    from selflearn_core import compute_process as P
    from selflearn_core.adapters import nfl_bounded_evaluation as B
    from selflearn_core.adapters import nfl_evaluation_job as N
    previous_path=sys.path[:];sys.path.insert(0,str(ROOT/"packages/selflearn-core/tests"))
    try:
        from test_nfl_calibration import corpus
        receipts,sources,revisions=corpus(n=110)
    finally:sys.path[:]=previous_path
    journal,bridge=actual
    inputs={"version":1,"kind":"nfl.shadow_job_inputs.v1","receipts":receipts,"source_receipts":sources,
        "revisions":revisions,"generation_records":None,"as_of_utc":"2026-10-15T00:00:00Z"}
    settings=N.policy(execution_profile=P.profile())
    for value in(inputs,settings):journal.append_object("source_snapshot",fingerprint(value),value)
    now=dt.datetime.now(dt.timezone.utc)
    spec=J.seal_spec({"version":1,"kind":"selflearn.job_definition.v1","namespace":dict(N.NS),"operation":N.OPERATION,
        "input_object_id":fingerprint(inputs),"policy_object_id":fingerprint(settings),"code_id":J.code_identity(),
        "not_before_utc":J.stamp(now-dt.timedelta(seconds=10)),"not_after_utc":J.stamp(now+dt.timedelta(minutes=5)),
        "lease_seconds":120,"max_runtime_seconds":180,"max_attempts":3,"max_steps":2},authority_id=AUTHORITY)
    journal.register_job(spec)
    with patch.object(P,"run_worker",wraps=P.run_worker)as worker:
        result=B.run_bounded_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="bounded")
        recovered=B.run_bounded_shadow_job(port(bridge),spec["job_id"],authority_id=AUTHORITY,worker_id="retry")
    assert worker.call_count==1 and result["status"]=="completed_shadow"and recovered["recovered_original"] is True
    assert result["result_object_id"]==recovered["result_object_id"]
    state=journal.read_job(spec["job_id"]);saved=journal.get_object(state["state"]["result_object_id"])["payload"]
    assert saved==result["result"]and saved["report"]["candidate"] is not None
    assert saved["kind"]=="nfl.shadow_job_result.v2"and saved["execution"]["isolated_python_requested"] is True
    B.validate_execution_receipt(saved["execution"],spec=spec,report=saved["report"],profile=settings["execution_profile"])
    assert saved["execution"]["execution_authenticated"] is False and all(saved[k] is False for k in N.FALSE_FLAGS)
    assert len(json.dumps(saved))<3*1024*1024
