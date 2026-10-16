"""RPC boundary tests use an explicitly fake SQLite transport; SQL is separate."""
import copy

import pytest

from selflearn_core import learning_jobs as J
from selflearn_core.store.supabase_learning_jobs import SupabaseLearningJobJournal
from selflearn_core.store.supabase_store import StoreConflict,StoreError
from test_learning_jobs import AUTHORITY,opened,command


def port(transport):
    return SupabaseLearningJobJournal("https://offline.supabase.invalid","sb_secret_jobs_offline_fixture",
        authority_id=AUTHORITY,transport=transport)


class FakeRPC:
    def __init__(self,journal):self.journal,self.calls=journal,[]
    def request(self,method,path,*,data):
        self.calls.append((method,path,copy.deepcopy(data)))
        assert method=="POST"and data["p_authority_id"]==AUTHORITY
        import json
        if path.endswith("jobs_register_v1"):return self.journal.register_job(json.loads(data["p_definition_json"]))
        if path.endswith("jobs_append_v1"):return self.journal.append_job_command(json.loads(data["p_command_json"]))
        if path.endswith("jobs_read_v1"):return self.journal.read_job(data["p_job_id"])
        raise AssertionError("Unexpected RPC")


def test_construct_is_no_io_register_scope_roundtrip_and_original_ack_readback(tmp_path):
    journal,state=opened(tmp_path/"jobs.db")
    with journal:
        rpc=FakeRPC(journal);client=port(rpc);assert rpc.calls==[]
        assert client.register_job(state["definition"])["definition"]==state["definition"]
        original=client.append_job_command(command(state));current=client.read_job(state["definition"]["job_id"])
        assert current["state"]["lease_id"]==original["command_id"]
        assert client.append_job_command(command(state))==original
        assert all(call[1].startswith("/rest/v1/rpc/selflearn_jobs_")for call in rpc.calls)


@pytest.mark.parametrize("kind",["conflict","typed_conflict","typed_error","bad_status","exception","cancel"])
def test_provider_diagnostics_never_cross_new_job_boundary_and_cancellation_is_visible(kind):
    class BadStatus(StoreError):
        @property
        def status(self):raise RuntimeError("private-status")
        @status.setter
        def status(self,value):pass
    def transport(*a,**k):
        if kind=="conflict":raise StoreConflict()
        if kind=="typed_conflict":raise J.JobConflict("private-conflict")
        if kind=="typed_error":raise StoreError("private-status")
        if kind=="bad_status":raise BadStatus()
        if kind=="cancel":raise KeyboardInterrupt()
        raise RuntimeError("private-provider-secret")
    client=port(transport)
    if kind=="cancel":
        with pytest.raises(KeyboardInterrupt):client.read_job("sha256:"+"a"*64)
    else:
        with pytest.raises((J.JobConflict,StoreError))as error:client.read_job("sha256:"+"a"*64)
        assert "private"not in str(error.value)and error.value.__cause__ is None
        if isinstance(error.value,StoreError):assert error.value.status==503


@pytest.mark.parametrize("fault",["snapshot","definition","head","activation","extra","false_ack","missing_history"])
def test_forged_readback_or_invented_ack_never_passes(tmp_path,fault):
    journal,state=opened(tmp_path/"jobs.db")
    with journal:
        rpc=FakeRPC(journal)
        def tamper(method,path,*,data):
            result=copy.deepcopy(rpc.request(method,path,data=data))
            if fault=="false_ack"and path.endswith("jobs_append_v1"):result["sequence"]=99
            if path.endswith("jobs_read_v1"):
                if fault=="snapshot":result={"status":"ok"}
                elif fault=="definition":result["definition"]["max_steps"]=1
                elif fault=="head":result["head_event_id"]="sha256:"+"f"*64
                elif fault=="activation":result["activation"]=True
                elif fault=="extra":result["approved"]=True
                elif fault=="missing_history":result["events"]=[]
            return result
        with pytest.raises(StoreError)as error:port(tamper).append_job_command(command(state))
        assert error.value.status==502
        assert len(journal.read_job(state["definition"]["job_id"])["events"])==1
