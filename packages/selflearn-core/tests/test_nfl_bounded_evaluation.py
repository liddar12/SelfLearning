"""Real isolated fitter, durable recovery, policy/receipt and privacy boundaries."""
import copy
import datetime as dt
import hashlib
import json
import os
import sys
from unittest.mock import patch

import pytest

from selflearn_core import compute_process as P
from selflearn_core import learning_jobs as J
from selflearn_core import nfl_compute_worker as W
from selflearn_core.adapters import nfl_bounded_evaluation as B
from selflearn_core.adapters import nfl_evaluation_job as N
from selflearn_core.adapters.nfl_calibration import walk_forward_report
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from test_nfl_evaluation_job import inputs,definition,AUTHORITY


def staged(tmp_path,inputs,*,profile=None,**changes):
    profile=P.profile()if profile is None else profile
    settings=N.policy(execution_profile=profile)
    journal=LearningJobJournal(tmp_path/"bounded-jobs.db",authority_id=AUTHORITY)
    for value in(inputs,settings):journal.append_object("source_snapshot",fingerprint(value),value)
    spec=definition(inputs,policy_object_id=fingerprint(settings),**changes)
    journal.register_job(spec);return journal,spec,settings


def leased(journal,spec):
    state=journal.read_job(spec["job_id"])
    journal.append_job_command(N._command(state,"claim","test",AUTHORITY))
    return journal.read_job(spec["job_id"])


def run(journal,spec,**changes):
    return B.run_bounded_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="test",**changes)


def emulated_worker(command,payload,**kwargs):
    """Pure worker logic for adversarial receipts; NOT process-level proof."""
    packet=W.decode(payload);cpu=packet["cpu_seconds"]
    response=W.compute(packet,{"cpu":[cpu,cpu],"core":[0,0],"file_size":[0,0]})
    raw=json.dumps(response,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    return {"stdout":raw,"elapsed_ns":100,"stderr_bytes":0,"returncode":0,
        "input_sha256":"sha256:"+hashlib.sha256(payload).hexdigest(),
        "output_sha256":"sha256:"+hashlib.sha256(raw).hexdigest(),"process_group_cleanup_requested":True}


def test_real_isolated_fitter_matches_parent_and_completed_retry_never_starts_child(tmp_path,inputs):
    journal,spec,settings=staged(tmp_path,inputs)
    with journal:
        with patch.object(P,"run_worker",wraps=P.run_worker)as compute:
            result=run(journal,spec);recovered=run(journal,spec)
        assert compute.call_count==1 and result["status"]=="completed_shadow"
        assert recovered["recovered_original"] is True and recovered["result_object_id"]==result["result_object_id"]
        expected=json.loads(json.dumps(walk_forward_report(inputs["receipts"],inputs["source_receipts"],inputs["revisions"],
            as_of_utc=inputs["as_of_utc"],population=settings["population"],bootstrap_replicates=200)))
        value=result["result"];assert value["report"]==expected and value["artifact"] is not None
        assert value["version"]==2 and value["kind"]=="nfl.shadow_job_result.v2"
        execution=value["execution"]
        assert execution["isolated_python_requested"] is True and execution["process_group_cleanup_requested"] is True
        assert execution["execution_authenticated"] is False and execution["clock_authenticated"] is False
        assert execution["limits"]["cpu"][0]<=30 and execution["limits"]["core"]==execution["limits"]["file_size"]==[0,0]
        B.validate_execution_receipt(execution,spec=spec,report=expected,profile=settings["execution_profile"])
        assert all(value[k] is False and result[k] is False for k in N.FALSE_FLAGS)
        current=journal.read_job(spec["job_id"])
        assert current["effective_status"]=="succeeded"and current["state"]["steps"]==2
        assert journal.get_object(current["state"]["result_object_id"])["payload"]==value


def test_real_preflight_pause_new_instance_resume_preserves_checkpoint(tmp_path,inputs):
    journal,spec,_=staged(tmp_path,inputs)
    with journal,patch.object(P,"run_worker",wraps=P.run_worker)as compute:
        pause=run(journal,spec,max_phases=1)
        assert pause["status"]=="paused"and compute.call_count==0
    with LearningJobJournal(tmp_path/"bounded-jobs.db",authority_id=AUTHORITY)as fresh:
        result=run(fresh,spec,max_phases=1);state=fresh.read_job(spec["job_id"])
        assert result["status"]=="completed_shadow"and state["state"]["checkpoint_object_id"]==pause["checkpoint_object_id"]
        assert [e["command"]["action"]for e in state["events"]]==["claim","checkpoint","release","claim","complete"]


def test_bounded_policy_requires_matching_executor_before_any_claim(tmp_path,inputs):
    journal,spec,settings=staged(tmp_path,inputs)
    with journal:
        for compute in(None,lambda *args:None,B.BoundedNFLEvaluator(P.profile(wall_seconds=29,cpu_seconds=29))):
            with pytest.raises(ValueError,match="shadow_job_bounded_executor_required"):
                N.run_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="test",compute=compute)
        assert journal.read_job(spec["job_id"])["events"]==[]
    assert N.policy()["version"]==1
    with pytest.raises(ValueError):N._policy({**settings,"execution_profile":{}})


def test_v1_policy_cannot_claim_bounded_execution_or_silently_upgrade(tmp_path,inputs):
    from test_nfl_evaluation_job import staged as legacy
    journal,spec=legacy(tmp_path,inputs)
    with journal:
        with pytest.raises(ValueError,match="bounded_shadow_policy_required"):run(journal,spec)
        with pytest.raises(ValueError,match="inline_policy_cannot_claim_bounded_execution"):
            N.run_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="test",compute=B.BoundedNFLEvaluator(P.profile()))
        assert journal.read_job(spec["job_id"])["events"]==[]


def test_fixed_child_command_no_secrets_environment_inheritance_or_job_selected_path(tmp_path,inputs):
    journal,spec,_=staged(tmp_path,inputs)
    original=P.run_worker
    def inspect(command,payload,**kwargs):
        assert command[:6]==[sys.executable,"-I","-S","-B","-u","-c"]and command[6]==B.BOOTSTRAP
        assert set(kwargs["environment"])<={"LANG","LC_ALL","TZ"}
        assert "fixture-secret"not in payload.decode()and "bounded-jobs.db"not in payload.decode()
        assert str(tmp_path)not in repr(command)
        return original(command,payload,**kwargs)
    with journal,patch.dict(os.environ,{"SELFLEARN_OPERATOR_TOKEN":"fixture-secret","SUPABASE_SECRET_KEY":"fixture-secret","PYTHONPATH":"untrusted"}),patch.object(P,"run_worker",side_effect=inspect):
        assert run(journal,spec)["status"]=="completed_shadow"


def test_real_timeout_releases_only_own_attempt_and_keeps_checkpoint_retryable(tmp_path,inputs):
    journal,spec,_=staged(tmp_path,inputs,profile=P.profile(wall_seconds=1,cpu_seconds=1))
    original=P.run_worker
    def hanging(command,payload,**kwargs):
        return original([sys.executable,"-I","-S","-B","-u","-c","import time;time.sleep(10)"],payload,**kwargs)
    with journal,patch.object(P,"run_worker",side_effect=hanging):
        held=run(journal,spec);state=journal.read_job(spec["job_id"])
        assert held["status"]=="held"and held["reason_codes"]==["compute_deadline_exceeded"]and held["lease_released"] is True
        assert state["effective_status"]=="queued"and state["state"]["checkpoint_object_id"] is not None
        assert state["state"]["result_object_id"] is None and state["events"][-1]["command"]["reason_code"]=="worker_error"
    # Explicitly use the same immutable job/profile; no reset of logical ID/budget.
    with LearningJobJournal(tmp_path/"bounded-jobs.db",authority_id=AUTHORITY)as fresh:
        with patch.object(P,"run_worker",side_effect=emulated_worker):result=run(fresh,spec)
        assert result["status"]=="completed_shadow"and fresh.read_job(spec["job_id"])["state"]["attempt"]==2


def test_compute_budget_honors_earlier_server_deadline_and_preparation_time(tmp_path,inputs):
    journal,spec,settings=staged(tmp_path,inputs)
    with journal:
        state=leased(journal,spec)
        state["state"]["lease_until_utc"]=J.stamp(J.clock(state["observed_utc"])+dt.timedelta(seconds=4.5))
        state["state"]["runtime_deadline_utc"]=J.stamp(J.clock(state["observed_utc"])+dt.timedelta(seconds=2.5))
        with patch.object(B.time,"monotonic",side_effect=[100,100.25]),patch.object(P,"run_worker",side_effect=emulated_worker)as work:
            result=B.BoundedNFLEvaluator(settings["execution_profile"])(inputs,settings,spec,state)
        assert work.call_args.kwargs["wall_seconds"]==2.25
        assert result.execution["wall_budget_ms"]==2500 and result.execution["cpu_budget_seconds"]==3
        for ticks in([100,103],[100,100]):
            if ticks==[100,100]:state["state"]["lease_until_utc"]=state["observed_utc"]
            with patch.object(B.time,"monotonic",side_effect=ticks),patch.object(P,"run_worker")as start:
                with pytest.raises(P.ComputeProcessError,match="compute_deadline_exceeded"):
                    B.BoundedNFLEvaluator(settings["execution_profile"])(inputs,settings,spec,state)
                start.assert_not_called()


@pytest.mark.parametrize("fault",["not_json","duplicate","wrong_kind","wrong_request","wrong_work","wrong_profile","wrong_code","report_hash","extra","bad_limits"])
def test_malformed_child_output_never_persists_result(tmp_path,inputs,fault):
    journal,spec,_=staged(tmp_path,inputs)
    def corrupted(*args,**kwargs):
        result=emulated_worker(*args,**kwargs);response=json.loads(result["stdout"])
        if fault=="not_json":raw=b"private-not-json"
        elif fault=="duplicate":raw=b'{"version":1,"version":1}'
        else:
            if fault=="wrong_kind":response["kind"]="other"
            elif fault.startswith("wrong_"):response[{"wrong_request":"request_id","wrong_work":"work_id","wrong_profile":"profile_id","wrong_code":"code_id"}[fault]]=fingerprint("bad")
            elif fault=="report_hash":response["report"]["report_id"]=fingerprint("bad")
            elif fault=="extra":response["extra"]=True
            elif fault=="bad_limits":response["limits"]["core"]=[False,0]
            raw=json.dumps(response).encode()
        result["stdout"]=raw;return result
    with journal,patch.object(P,"run_worker",side_effect=corrupted):
        held=run(journal,spec);state=journal.read_job(spec["job_id"])
        assert held["reason_codes"]==["compute_response_refused"]and held["lease_released"] is True
        assert state["state"]["result_object_id"] is None


@pytest.mark.parametrize("key,value",[("version",True),("kind","other"),("profile_id",fingerprint("bad")),
    ("job_id",fingerprint("bad")),("definition_id",fingerprint("bad")),("code_id",fingerprint("bad")),
    ("work_id",fingerprint("bad")),("report_id",fingerprint("bad")),("stdin_sha256","bad"),("stdout_sha256","bad"),
    ("wall_budget_ms",True),("cpu_budget_seconds",31),("elapsed_ns",-1),("stderr_bytes",16385),("returncode",False),
    ("isolated_python_requested",False),("process_group_cleanup_requested",False),("execution_authenticated",True),("clock_authenticated",True)])
def test_receipt_scope_and_false_authority_flags_cannot_be_rehashed_away(tmp_path,inputs,key,value):
    # Fast insufficient-data report still executes real pure worker logic.
    inputs={**inputs,"receipts":[],"source_receipts":[],"revisions":[]}
    journal,spec,settings=staged(tmp_path,inputs)
    with journal,patch.object(P,"run_worker",side_effect=emulated_worker):result=run(journal,spec)
    original=result["result"];changed=copy.deepcopy(original);changed["execution"][key]=value
    with pytest.raises(ValueError):N._result(changed,spec,settings)
    with pytest.raises(ValueError):N._result(original,spec,N.policy())
    with pytest.raises(ValueError):N._result({**original,"extra":True},spec,settings)


def test_cancellation_propagates_without_any_post_cancellation_journal_write(tmp_path,inputs):
    journal,spec,_=staged(tmp_path,inputs);cancelled=False
    original=journal.append_job_command
    def guard(*args,**kwargs):
        assert cancelled is False;return original(*args,**kwargs)
    def cancel(*args,**kwargs):
        nonlocal cancelled
        cancelled=True;raise KeyboardInterrupt
    with journal,patch.object(journal,"append_job_command",side_effect=guard),patch.object(P,"run_worker",side_effect=cancel):
        with pytest.raises(KeyboardInterrupt):run(journal,spec)
        assert journal.read_job(spec["job_id"])["state"]["result_object_id"] is None


def test_failed_cleanup_release_never_touches_new_head_or_masks_compute_error(tmp_path,inputs):
    journal,spec,settings=staged(tmp_path,inputs);runner=B.BoundedNFLEvaluator(settings["execution_profile"])
    assert runner.release_failed_attempt(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="test") is False
    with journal:
        state=leased(journal,spec);runner.current=copy.deepcopy(state)
        journal.append_job_command(N._command(state,"release","test",AUTHORITY,reason="paused"))
        before=journal.read_job(spec["job_id"])
        assert runner.release_failed_attempt(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="test") is False
        assert journal.read_job(spec["job_id"])["head_event_id"]==before["head_event_id"]
    with patch.object(P,"POSIX",False),pytest.raises(ValueError,match="compute_posix_required"):
        B.BoundedNFLEvaluator(settings["execution_profile"])


def test_parent_temp_directory_failure_is_static_and_releases_own_lease(tmp_path,inputs):
    journal,spec,_=staged(tmp_path,inputs)
    with journal,patch.object(B.tempfile,"TemporaryDirectory",side_effect=OSError("private-parent-path")):
        result=run(journal,spec)
        assert result["reason_codes"]==["compute_worker_unavailable"]and result["lease_released"] is True
        assert "private-parent-path"not in repr(result)
