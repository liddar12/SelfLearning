"""Real fitter + durable jobs on SQLite; source/labels remain synthetic."""
import copy
import datetime as dt
import json
import time
from unittest.mock import patch

import pytest

from selflearn_core import learning_jobs as J
from selflearn_core.adapters import nfl_evaluation_job as N
from selflearn_core.adapters.nfl_calibration import walk_forward_report
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from test_nfl_calibration import corpus

AUTHORITY=fingerprint("nfl-local-shadow-jobs")


@pytest.fixture(scope="module")
def inputs():
    receipts,sources,revisions=corpus(n=110)
    return {"version":1,"kind":"nfl.shadow_job_inputs.v1","receipts":receipts,"source_receipts":sources,
        "revisions":revisions,"generation_records":None,"as_of_utc":"2026-10-15T00:00:00Z"}


def definition(inputs,**changes):
    now=dt.datetime.now(dt.timezone.utc)
    return J.seal_spec({"version":1,"kind":"selflearn.job_definition.v1","namespace":dict(N.NS),"operation":N.OPERATION,
        "input_object_id":fingerprint(inputs),"policy_object_id":fingerprint(N.policy()),"code_id":J.code_identity(),
        "not_before_utc":J.stamp(now-dt.timedelta(seconds=10)),"not_after_utc":J.stamp(now+dt.timedelta(minutes=5)),
        "lease_seconds":60,"max_runtime_seconds":180,"max_attempts":3,"max_steps":2,**changes},authority_id=AUTHORITY)


def staged(tmp_path,inputs,**changes):
    journal=LearningJobJournal(tmp_path/"nfl-jobs.db",authority_id=AUTHORITY)
    for payload in(inputs,N.policy()):journal.append_object("source_snapshot",fingerprint(payload),payload)
    spec=definition(inputs,**changes);journal.register_job(spec);return journal,spec


def test_real_fitter_result_and_typed_artifact_persist_once_recovery_never_refits(tmp_path,inputs):
    journal,spec=staged(tmp_path,inputs);job_id=spec["job_id"]
    with journal:
        with patch.object(N.calibration,"walk_forward_report",wraps=walk_forward_report)as fit:
            result=N.run_shadow_job(journal,job_id,authority_id=AUTHORITY,worker_id="worker-a")
            recovered=N.run_shadow_job(journal,job_id,authority_id=AUTHORITY,worker_id="worker-b")
        assert fit.call_count==1 and result["status"]=="completed_shadow"
        assert recovered["recovered_original"] is True and result["recovered_original"] is False
        assert recovered["result_object_id"]==result["result_object_id"]
        report=json.loads(json.dumps(walk_forward_report(inputs["receipts"],inputs["source_receipts"],inputs["revisions"],
            as_of_utc=inputs["as_of_utc"],population="eligible_pool",bootstrap_replicates=200)))
        assert result["result"]["report"]==report
        assert result["result"]["artifact"] is not None
        assert all(result[k] is False and result["result"][k] is False for k in N.FALSE_FLAGS)
        state=journal.read_job(job_id)
        assert state["state"]["steps"]==2 and len(state["events"])==3
        assert journal.get_object(state["state"]["result_object_id"])["payload"]==result["result"]
        assert journal.receipts()==[] and journal.latest_resolutions()==[] and journal.generations()==[]


def test_explicit_preflight_pause_reopens_file_and_resumes_without_duplicate_checkpoint(tmp_path,inputs):
    journal,spec=staged(tmp_path,inputs);job_id=spec["job_id"]
    with journal:
        with patch.object(N.calibration,"walk_forward_report",wraps=walk_forward_report)as fit:
            paused=N.run_shadow_job(journal,job_id,authority_id=AUTHORITY,worker_id="first",max_phases=1)
        assert paused["status"]=="paused" and fit.call_count==0
        assert journal.read_job(job_id)["effective_status"]=="queued"
    with LearningJobJournal(tmp_path/"nfl-jobs.db",authority_id=AUTHORITY)as fresh:
        result=N.run_shadow_job(fresh,job_id,authority_id=AUTHORITY,worker_id="second",max_phases=1)
        state=fresh.read_job(job_id)
        assert result["status"]=="completed_shadow" and state["state"]["attempt"]==2 and state["state"]["steps"]==2
        assert [e["command"]["action"]for e in state["events"]]==["claim","checkpoint","release","claim","complete"]
        assert state["state"]["checkpoint_object_id"]==paused["checkpoint_object_id"]


def test_successful_computation_with_insufficient_data_never_means_numeric_gate_or_accuracy_pass(tmp_path,inputs):
    empty={**inputs,"receipts":[],"source_receipts":[],"revisions":[]}
    journal,spec=staged(tmp_path,empty)
    with journal:
        result=N.run_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="worker")
        assert result["status"]=="completed_shadow" and result["result"]["artifact"] is None
        assert result["result"]["report"]["shared_shadow_numeric_gate"]["passed"] is False
        assert result["accuracy_improvement_verified"] is False


@pytest.mark.parametrize("fault",["namespace","operation","code","steps","future_cutoff","policy","input_extra","pause_budget"])
def test_incompatible_definition_inputs_and_policy_cannot_start_training(tmp_path,inputs,fault):
    value=copy.deepcopy(inputs);changes={}
    if fault=="namespace":changes["namespace"]={**N.NS,"domain":"another-domain"}
    elif fault=="operation":changes["operation"]="other-operation"
    elif fault=="code":changes["code_id"]=fingerprint("different-code")
    elif fault=="steps":changes["max_steps"]=1
    elif fault=="pause_budget":changes["max_attempts"]=1
    elif fault=="future_cutoff":value["as_of_utc"]="2099-01-01T00:00:00Z"
    elif fault=="input_extra":value["trusted_source"]=True
    journal,spec=staged(tmp_path,value,**changes)
    with journal,patch.object(N.calibration,"walk_forward_report",wraps=walk_forward_report)as fit:
        if fault=="policy":
            with pytest.raises(ValueError):N.policy(bootstrap_replicates=True)
        else:
            with pytest.raises(ValueError):N.run_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="worker",max_phases=1 if fault=="pause_budget"else 2)
        assert fit.call_count==0
        if fault=="pause_budget":assert journal.read_job(spec["job_id"])["events"]==[]


@pytest.mark.parametrize("fault",["code_changed","lease_expired","cancelled"])
def test_changed_code_or_late_fitter_cannot_commit_result_and_cancellation_propagates(tmp_path,inputs,fault):
    journal,spec=staged(tmp_path,inputs,lease_seconds=1 if fault=="lease_expired"else 60);identity=spec["code_id"];changed=False
    def fit(*a,**kw):
        nonlocal changed
        if fault=="cancelled":raise KeyboardInterrupt()
        result=walk_forward_report(*a,**kw)
        if fault=="lease_expired":time.sleep(1.1)
        if fault=="code_changed":changed=True
        return result
    with journal,patch.object(N.calibration,"walk_forward_report",side_effect=fit),patch.object(J,"code_identity",side_effect=lambda:fingerprint("changed")if changed else identity):
        with pytest.raises(KeyboardInterrupt if fault=="cancelled"else ValueError):
            N.run_shadow_job(journal,spec["job_id"],authority_id=AUTHORITY,worker_id="worker")
        state=journal.read_job(spec["job_id"])
        assert state["state"]["result_object_id"] is None and len(state["events"])==2
        rows=journal._db.execute("SELECT payload FROM receipt_journal_objects").fetchall()
        assert not any(json.loads(r[0]).get("kind")=="nfl.shadow_job_result.v1"for r in rows)


def test_complete_package_code_identity_tracks_new_modified_removed_helpers_and_rejects_symlinks(tmp_path):
    root=tmp_path/"package";root.mkdir();source=root/"learning_jobs.py";source.write_text("# fixture package\n")
    with patch.object(J,"__file__",str(source)):
        first=J.code_identity();helper=root/"transitive_helper.py";helper.write_text("# first\n")
        second=J.code_identity();assert first!=second
        helper.write_text("# changed\n");assert J.code_identity()!=second
        helper.unlink();assert J.code_identity()==first
        helper.symlink_to(source)
        with pytest.raises(ValueError,match="learning_job_code_symlink"):J.code_identity()
