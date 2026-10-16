"""Real local persistence/concurrency/crash tests; no hosted/native claims."""
import copy
import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from selflearn_core import learning_jobs as J
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal

AUTHORITY = fingerprint("local-job-owner")
NS = {"domain": "example", "task": "binary.forecast", "producer_id": "fixture"}


def spec(**changes):
    now = dt.datetime.now(dt.timezone.utc)
    return J.seal_spec({"version": 1, "kind": "selflearn.job_definition.v1", "namespace": NS,
        "operation": "shadow_evaluation", "input_object_id": fingerprint({"synthetic_input": True}),
        "policy_object_id": fingerprint({"synthetic_policy": True}), "code_id": fingerprint("fixture-code"),
        "not_before_utc": J.stamp(now - dt.timedelta(seconds=10)), "not_after_utc": J.stamp(now + dt.timedelta(minutes=5)),
        "lease_seconds": 30, "max_runtime_seconds": 60, "max_attempts": 3, "max_steps": 4, **changes}, authority_id=AUTHORITY)


def command(current, action="claim", *, worker="worker-a", object_id=None, reason=None):
    return J.seal_command({"version": 1, "kind": "selflearn.job_command.v1", "job_id": current["definition"]["job_id"],
        "expected_head_event_id": current["head_event_id"], "action": action, "worker_id": worker,
        "lease_id": None if action == "claim" else current["state"]["lease_id"], "object_id": object_id,
        "reason_code": reason}, authority_id=AUTHORITY)


def opened(path, definition=None):
    journal = LearningJobJournal(path, authority_id=AUTHORITY)
    for value in ({"synthetic_input": True}, {"synthetic_policy": True}):
        journal.append_object("source_snapshot", fingerprint(value), value)
    definition = definition or spec()
    return journal, journal.register_job(definition)


def object_ref(journal, value):
    identity = fingerprint(value);journal.append_object("source_snapshot", identity, value)
    return identity


def test_duplicate_registration_original_acks_and_completion_do_not_duplicate_job_or_samples(tmp_path):
    journal, current = opened(tmp_path / "jobs.db")
    with journal:
        duplicate = journal.register_job(current["definition"])
        assert duplicate["definition"] == current["definition"] and duplicate["events"] == []
        claim = command(current);original = journal.append_job_command(claim)
        assert journal.append_job_command(claim) == original
        current = journal.read_job(current["definition"]["job_id"])
        result_id = object_ref(journal, {"synthetic_result": True, "activation": False})
        finish = command(current, "complete", object_id=result_id)
        completed = journal.append_job_command(finish)
        assert journal.append_job_command(finish) == completed
        assert journal.append_job_command(claim) == original  # old ACK != current lease
        assert original["current_lease_authority"] is False and original["activation"] is False
        done = journal.read_job(current["definition"]["job_id"])
        assert done["effective_status"] == "succeeded" and done["state"]["result_object_id"] == result_id
        assert len(done["events"]) == 2 and done["exactly_once_compute"] is False
        assert journal.receipts() == []
        for table in ("receipt_journal_receipts", "receipt_journal_revisions", "receipt_journal_generation_commits"):
            assert journal._db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
        with pytest.raises(J.JobConflict, match="learning_job_terminal"):
            journal.append_job_command(command(done))


def test_real_two_connection_claim_race_has_one_winner(tmp_path):
    path = tmp_path / "jobs.db";journal, current = opened(path);journal.close()
    barrier = threading.Barrier(2);results = []
    def worker(label):
        with LearningJobJournal(path, authority_id=AUTHORITY) as local:
            cmd = command(current, worker=label);barrier.wait(timeout=5)
            try:results.append(("accepted", local.append_job_command(cmd)["sequence"]))
            except J.JobConflict as error:results.append(("refused", str(error)))
    threads = [threading.Thread(target=worker, args=(label,)) for label in ("worker-a", "worker-b")]
    for thread in threads:thread.start()
    for thread in threads:thread.join(timeout=10);assert not thread.is_alive()
    assert sorted(x[0] for x in results) == ["accepted", "refused"]
    assert next(x[1] for x in results if x[0] == "refused") == "learning_job_head_conflict"
    with LearningJobJournal(path, authority_id=AUTHORITY) as local:
        assert len(local.read_job(current["definition"]["job_id"])["events"]) == 1


def test_manual_and_ingestion_duplicate_times_cannot_create_new_logical_job_or_extend_budget(tmp_path):
    journal,current=opened(tmp_path/"jobs.db")
    with journal:
        original=current["definition"]
        changed={k:v for k,v in original.items()if k not in("job_id","definition_id")}
        changed.update(not_after_utc="2099-01-01T00:00:00Z",max_runtime_seconds=3600,max_attempts=8)
        extended=J.seal_spec(changed,authority_id=AUTHORITY)
        assert extended["job_id"]==original["job_id"] and extended["definition_id"]!=original["definition_id"]
        with pytest.raises(J.JobConflict,match="learning_job_definition_conflict"):journal.register_job(extended)
        assert journal.read_job(original["job_id"])["definition"]==original
        assert journal._db.execute("SELECT COUNT(*) FROM learning_jobs_v1").fetchone()[0]==1


def test_real_crash_reopens_durable_checkpoint_and_expired_worker_cannot_write(tmp_path):
    path = tmp_path / "jobs.db";journal, current = opened(path, spec(lease_seconds=1));job_id = current["definition"]["job_id"]
    journal.close()
    source = """
import json,os,sys
from selflearn_core import learning_jobs as J
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
path,authority,job_id=sys.argv[1:]
with LearningJobJournal(path,authority_id=authority)as db:
 def cmd(state,action,oid=None):return J.seal_command({'version':1,'kind':'selflearn.job_command.v1','job_id':job_id,
  'expected_head_event_id':state['head_event_id'],'action':action,'worker_id':'crashed-worker',
  'lease_id':None if action=='claim'else state['state']['lease_id'],'object_id':oid,'reason_code':None},authority_id=authority)
 db.append_job_command(cmd(db.read_job(job_id),'claim'));state=db.read_job(job_id)
 payload={'checkpoint':1};oid=fingerprint(payload);db.append_object('source_snapshot',oid,payload)
 db.append_job_command(cmd(state,'checkpoint',oid))
 os._exit(17)
"""
    child = subprocess.run([sys.executable,"-B","-c",source,str(path),AUTHORITY,job_id], capture_output=True,timeout=10)
    assert child.returncode == 17, child.stderr.decode()
    with LearningJobJournal(path, authority_id=AUTHORITY) as journal:
        old = journal.read_job(job_id);assert old["state"]["checkpoint_object_id"] == fingerprint({"checkpoint": 1})
        stale = command(old,"complete",worker="crashed-worker",object_id=object_ref(journal,{"result":1}))
        time.sleep(1.1)  # actual database-owned expiry, not an uploaded/faked clock
        assert journal.read_job(job_id)["effective_status"] == "expired_lease"
        journal.append_job_command(command(journal.read_job(job_id),worker="replacement-worker"))
        with pytest.raises(J.JobConflict,match="learning_job_head_conflict"):journal.append_job_command(stale)
        fresh = journal.read_job(job_id);assert fresh["state"]["attempt"] == 2
        assert fresh["state"]["checkpoint_object_id"] == old["state"]["checkpoint_object_id"]
        forged = command(fresh,"complete",worker="replacement-worker",object_id=object_ref(journal,{"result":2}))
        forged["lease_id"] = old["state"]["lease_id"]
        forged = J.seal_command({k:v for k,v in forged.items()if k!="command_id"},authority_id=AUTHORITY)
        with pytest.raises(J.JobConflict,match="learning_job_lease_conflict"):journal.append_job_command(forged)
        journal.append_job_command(command(fresh,"complete",worker="replacement-worker",object_id=fingerprint({"result":2})))
        assert journal.read_job(job_id)["effective_status"] == "succeeded"


def test_checkpoint_reserves_completion_step_and_cannot_renew_without_progress(tmp_path):
    journal, state = opened(tmp_path/"jobs.db", spec(max_steps=2))
    with journal:
        journal.append_job_command(command(state));state = journal.read_job(state["definition"]["job_id"])
        first = object_ref(journal,{"checkpoint":1});journal.append_job_command(command(state,"checkpoint",object_id=first))
        state = journal.read_job(state["definition"]["job_id"])
        with pytest.raises(J.JobConflict,match="learning_job_step_limit"):
            journal.append_job_command(command(state,"checkpoint",object_id=object_ref(journal,{"checkpoint":2})))
        journal.append_job_command(command(state,"complete",object_id=first))
        assert journal.read_job(state["definition"]["job_id"])["state"]["steps"] == 2
    journal, state = opened(tmp_path/"other.db")
    with journal:
        journal.append_job_command(command(state));state=journal.read_job(state["definition"]["job_id"])
        oid=object_ref(journal,{"checkpoint":1});journal.append_job_command(command(state,"checkpoint",object_id=oid))
        state=journal.read_job(state["definition"]["job_id"])
        with pytest.raises(J.JobConflict,match="learning_job_checkpoint_not_advanced"):
            journal.append_job_command(command(state,"checkpoint",object_id=oid))


@pytest.mark.parametrize("mode",["late","early","attempts","runtime","missing_ref","wrong_worker"])
def test_late_triggers_attempt_runtime_and_reference_boundaries(tmp_path,mode):
    now=dt.datetime.now(dt.timezone.utc);changes={}
    if mode=="late":changes.update(not_before_utc=J.stamp(now-dt.timedelta(minutes=2)),not_after_utc=J.stamp(now-dt.timedelta(minutes=1)))
    elif mode=="early":changes.update(not_before_utc=J.stamp(now+dt.timedelta(minutes=1)))
    elif mode=="attempts":changes.update(max_attempts=1)
    elif mode=="runtime":changes.update(max_runtime_seconds=1,lease_seconds=1)
    journal,state=opened(tmp_path/"jobs.db",spec(**changes))
    with journal:
        if mode in("late","early"):
            assert state["effective_status"]==("expired"if mode=="late"else"not_due")
            with pytest.raises(J.JobConflict):journal.append_job_command(command(state))
            assert journal.read_job(state["definition"]["job_id"])["events"]==[];return
        journal.append_job_command(command(state));state=journal.read_job(state["definition"]["job_id"])
        if mode=="attempts":
            journal.append_job_command(command(state,"release",reason="paused"));state=journal.read_job(state["definition"]["job_id"])
            assert state["effective_status"]=="attempt_limit"
            with pytest.raises(J.JobConflict,match="learning_job_attempt_limit"):journal.append_job_command(command(state))
        elif mode=="runtime":
            time.sleep(1.1);state=journal.read_job(state["definition"]["job_id"])
            assert state["effective_status"]=="runtime_limit"
            with pytest.raises(J.JobConflict,match="learning_job_runtime_limit"):journal.append_job_command(command(state))
        elif mode=="missing_ref":
            with pytest.raises(ValueError,match="invalid_learning_job_reference"):
                journal.append_job_command(command(state,"complete",object_id=fingerprint("absent")))
        else:
            with pytest.raises(J.JobConflict,match="learning_job_lease_conflict"):
                journal.append_job_command(command(state,"release",worker="impostor",reason="paused"))


@pytest.mark.parametrize("mutation",["bool_budget","unknown_key","wrong_hash","wrong_authority","unsafe_namespace","unicode","huge_int","nested_alias"])
def test_definition_protocol_is_strict_and_bounded_before_encoding(mutation):
    value=spec();authority=AUTHORITY
    if mutation=="bool_budget":value["max_steps"]=True
    elif mutation=="unknown_key":value["source_url"]="https://not-a-job-input.invalid"
    elif mutation=="wrong_hash":value["code_id"]=fingerprint("different")
    elif mutation=="wrong_authority":authority=fingerprint("another-owner")
    elif mutation=="unsafe_namespace":value["namespace"]={**NS,"producer_id":"../unsafe"}
    elif mutation=="unicode":value["operation"]="private-é"
    elif mutation=="huge_int":value["max_runtime_seconds"]=10**1000
    else:
        node={"x":"a"*1000};value["nested"]=[node]*1000
    with pytest.raises(ValueError):J.validate_spec(value,authority_id=authority)


def test_histories_reexecute_states_reject_tampering_and_sqlite_rows_are_immutable(tmp_path):
    journal,state=opened(tmp_path/"jobs.db")
    with journal:
        journal.append_job_command(command(state));state=journal.read_job(state["definition"]["job_id"])
        tampered=copy.deepcopy(state["events"]);tampered[0]["state"]["attempt"]=7
        tampered[0]["event_id"]=fingerprint({k:v for k,v in tampered[0].items()if k!="event_id"})
        with pytest.raises(ValueError,match="learning_job_event_mismatch"):
            J.complete_snapshot(state["definition"],tampered,authority_id=AUTHORITY,registered_utc=state["registered_utc"],observed_utc=state["observed_utc"])
        for table in("learning_jobs_v1","learning_job_events_v1"):
            for operation in("UPDATE "+table+" SET authority_id=authority_id","DELETE FROM "+table):
                with pytest.raises(sqlite3.IntegrityError,match="immutable learning job"):
                    with journal._db:journal._db.execute(operation)
        with LearningJobJournal(tmp_path/"jobs.db",authority_id=fingerprint("different-owner")) as other:
            with pytest.raises(ValueError,match="learning_job_absent"):other.read_job(state["definition"]["job_id"])


def test_failure_is_terminal_and_complete_maximum_legal_history_stays_bounded(tmp_path):
    journal,current=opened(tmp_path/"jobs.db")
    with journal:
        journal.append_job_command(command(current));current=journal.read_job(current["definition"]["job_id"])
        journal.append_job_command(command(current,"fail",reason="evaluation_failed"))
        assert journal.read_job(current["definition"]["job_id"])["effective_status"]=="failed"
    definition=spec(max_attempts=8,max_steps=32,max_runtime_seconds=3600)
    recorded=dt.datetime.now(dt.timezone.utc);registered=J.stamp(recorded-dt.timedelta(seconds=1));history=[]
    def append(action,oid=None,reason=None):
        nonlocal recorded
        state={"definition":definition,"head_event_id":history[-1]["event_id"]if history else None,
            "state":history[-1]["state"]if history else J.initial_state()}
        recorded+=dt.timedelta(milliseconds=1)
        history.append(J.make_event(definition,history,command(state,action,object_id=oid,reason=reason),
            authority_id=AUTHORITY,registered_utc=registered,recorded_utc=J.stamp(recorded)))
    append("claim")
    for n in range(31):append("checkpoint",fingerprint(["checkpoint",n]))
    for attempt in range(7):append("release",reason="paused");append("claim")
    append("complete",fingerprint("complete"))
    result=J.complete_snapshot(definition,history,authority_id=AUTHORITY,registered_utc=registered,observed_utc=J.stamp(recorded))
    assert len(history)==47 and result["state"]["steps"]==32 and result["state"]["attempt"]==8
    assert result["effective_status"]=="succeeded"and len(J.canonical(result))<J.MAX_HISTORY_BYTES
