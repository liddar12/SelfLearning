"""Real SQLite jobs + publication ports sharing one file; no live HTTP."""
import copy
from datetime import timedelta
import json
from unittest.mock import patch

import pytest

from services.web import nfl_publication_jobs as W
from services.web.nfl_ingestion import finalize_generation
from services.web.nfl_publication_verifier import PublicPoolVerifier, PublicationUnavailable
from selflearn_core import learning_jobs as dto
from selflearn_core.adapters.nfl import canonical_key
from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal
from selflearn_core.probability_receipts import fingerprint, instant
from selflearn_core.store.learning_job_journal import LearningJobJournal
from .publication_fixture import graph

AUTHORITY = fingerprint("local SQLite publication worker authority")
ORIGIN = "https://publication-worker.example.com"
NOW = "2030-01-01T12:00:00.000000Z"
CODE = fingerprint("trusted explicit worker test code identity")


class Clock:
    def __init__(self): self.value=instant(NOW)
    def stamp(self): return dto.stamp(self.value)
    def advance(self,seconds): self.value+=timedelta(seconds=seconds)


@pytest.fixture
def ports(tmp_path):
    path=tmp_path/"persistent.sqlite";clock=Clock();calls=[]
    with LearningJobJournal(path,authority_id=AUTHORITY) as jobs, NFLPublicationJournal(path) as journal:
        doc,sources=graph(kickoff="2030-01-01T13:00:00Z")
        for value in sources.values():journal.append_object("source_snapshot",fingerprint(value),value)
        journal.append_object("generation",doc["envelope_id"],doc)
        ack=finalize_generation(journal,doc["envelope_id"])
        assert ack["durable"] and len(ack["receipts"])==1
        def fetch(url):
            calls.append(url)
            return 200,{"Content-Type":"application/json"},json.dumps(sources["leg_pool"]).encode(),url
        verifier=PublicPoolVerifier(ORIGIN,transport=fetch,clock=clock.stamp)
        with patch.object(jobs,"_now",side_effect=clock.stamp),patch.object(journal,"_publication_recorded_utc",side_effect=clock.stamp),patch.object(W,"code_identity",return_value=CODE):
            yield jobs,journal,verifier,clock,calls,doc,path


def enqueue(ports):
    jobs,journal,verifier,_,_,doc,_=ports
    return W.enqueue(jobs,journal,doc["envelope_id"],verifier.policy,CODE)


def run(ports,**kwargs):
    jobs,journal,verifier,*_=ports
    return W.run_once(jobs,journal,verifier,"fixture-worker",CODE,**kwargs)


def test_disabled_configuration_never_scans_code_or_opens_ports():
    with patch.object(W,"code_identity",side_effect=AssertionError("code IO")):
        for env in ({},{"SELFLEARN_PUBLICATION_WORKER_MODE":"on"},{"SELFLEARN_PUBLICATION_WORKER_MODE":"RECORD"}):
            assert W.from_environment(env) is None
        enabled=W.from_environment({"SELFLEARN_PUBLICATION_WORKER_MODE":"record"})
    assert enabled["operation"]==W.OPERATION and enabled["lease_seconds"]==60


def test_real_enqueue_record_complete_and_restart_preserve_originals(ports):
    jobs,journal,verifier,clock,calls,doc,path=ports
    original=copy.deepcopy(journal.receipts())
    queued=enqueue(ports);again=enqueue(ports)
    assert queued["definition"]==again["definition"]
    spec=queued["definition"]
    wrapper=jobs.get_object(spec["input_object_id"])
    assert wrapper["kind"]=="source_snapshot" and wrapper["payload"]["object_id"]==doc["envelope_id"]
    assert spec["max_attempts"]==8 and spec["max_steps"]==1
    assert instant(spec["not_after_utc"])==instant("2030-01-01T14:00:00Z")
    result=run(ports)
    assert result["status"]=="recording_completed" and result["recording_complete"] is True
    assert result["publication_status"]=="accepted" and result["publication_verified"] is True
    assert all(result[k] is False for k in W.FALSE_FLAGS)
    assert len(calls)==1 and journal.receipts()==original
    current=jobs.read_job(spec["job_id"])
    assert current["effective_status"]=="succeeded"
    stored=jobs.get_object(current["state"]["result_object_id"])["payload"]
    assert stored["job_id"]==spec["job_id"] and stored["definition_id"]==spec["definition_id"]
    assert stored["attestation_id"]==journal.get_publication_attestation(doc["envelope_id"])["attestation_id"]
    with LearningJobJournal(path,authority_id=AUTHORITY) as fresh_jobs, NFLPublicationJournal(path) as fresh_publication:
        with patch.object(fresh_jobs,"_now",side_effect=clock.stamp):
            assert fresh_jobs.read_job(spec["job_id"])["state"]["result_object_id"]==current["state"]["result_object_id"]
        assert fresh_publication.get_publication_attestation(doc["envelope_id"])["attestation_id"]==stored["attestation_id"]
    assert run(ports)["status"]=="idle" and len(calls)==1


def test_withheld_publication_is_completed_recording_not_forecast_success(ports):
    jobs,journal,_,clock,calls,doc,_=ports
    queued=enqueue(ports);clock.advance(3601)
    result=run(ports)
    assert result["recording_complete"] is True
    assert result["publication_status"]=="withheld" and result["publication_verified"] is False
    assert result["accepted_receipts"]==0 and result["rejected_receipts"]==1
    assert jobs.read_job(queued["definition"]["job_id"])["effective_status"]=="succeeded"
    assert len(calls)==1


@pytest.mark.parametrize("fault",["unavailable","different_pool"])
def test_transient_fetch_or_pool_mismatch_releases_then_recovers(ports,fault):
    jobs,journal,verifier,_,calls,doc,_=ports
    queued=enqueue(ports);original=verifier.transport
    if fault=="unavailable":
        def broken(_):raise PublicationUnavailable("private upstream diagnostic")
    else:
        def broken(url):return 200,{"Content-Type":"application/json"},b'{"wrong":"pool"}',url
    verifier.transport=broken
    first=run(ports)
    assert first["status"]=="pending" and first["recording_complete"] is False
    current=jobs.read_job(queued["definition"]["job_id"])
    assert current["effective_status"]=="queued" and current["state"]["attempt"]==1
    assert journal.get_publication_attestation(doc["envelope_id"]) is None
    verifier.transport=original
    second=run(ports)
    assert second["recording_complete"] is True and len(calls)==1


def test_retry_attempt_ceiling_is_immutable_and_no_ninth_observation(ports):
    jobs,_,verifier,_,_,_,_=ports
    queued=enqueue(ports);calls=[]
    def broken(_):calls.append(1);raise PublicationUnavailable("temporary")
    verifier.transport=broken
    for _ in range(8):assert run(ports)["status"]=="pending"
    assert run(ports)["status"]=="idle" and len(calls)==8
    current=jobs.read_job(queued["definition"]["job_id"])
    assert current["effective_status"]=="attempt_limit" and current["state"]["attempt"]==8


def test_crash_after_proof_before_completion_recovers_after_lease_expiry_without_http(ports):
    jobs,journal,_,clock,calls,doc,_=ports
    queued=enqueue(ports);original=jobs.append_object
    class Crash(BaseException):pass
    def crash(kind,identity,payload):
        if payload.get("kind")=="nfl.publication_job_result.v1":raise Crash()
        return original(kind,identity,payload)
    with patch.object(jobs,"append_object",side_effect=crash),pytest.raises(Crash):run(ports)
    proof=journal.get_publication_attestation(doc["envelope_id"])
    assert proof is not None and len(calls)==1
    assert jobs.read_job(queued["definition"]["job_id"])["effective_status"]=="running"
    clock.advance(61)
    result=run(ports)
    assert result["recording_complete"] is True and result["attestation_id"]==proof["attestation_id"]
    assert len(calls)==1
    assert jobs.read_job(queued["definition"]["job_id"])["state"]["attempt"]==2


def test_fresh_lease_read_refuses_cached_claim_ack_without_ownership(ports):
    jobs,_,_,_,calls,_,_=ports
    queued=enqueue(ports);job_id=queued["definition"]["job_id"]
    current=jobs.read_job(job_id)
    command=W._command(current,"claim","other-worker",AUTHORITY)
    historical_ack=jobs.append_job_command(command)
    assert historical_ack["current_lease_authority"] is False
    # Discovery IDs are not authority. Inject the old ID and a cached ACK while
    # keeping actual SQLite fresh reads authoritative and owned by another worker.
    discovered={"version":1,"authority_id":AUTHORITY,"namespace":W.NAMESPACE,"operation":W.OPERATION,
        "code_id":CODE,"policy_object_id":queued["definition"]["policy_object_id"],"observed_utc":NOW,"job_ids":[job_id]}
    with patch.object(jobs,"list_claimable_jobs",return_value=discovered),patch.object(jobs,"append_job_command",return_value=historical_ack):
        result=run(ports)
    assert result["status"]=="pending" and calls==[]


def test_changed_code_or_policy_never_observes_or_reuses_wrong_policy_proof(ports):
    jobs,journal,verifier,_,calls,doc,_=ports
    enqueue(ports)
    with pytest.raises(ValueError,match="code_changed"):
        W.run_once(jobs,journal,verifier,"worker",fingerprint("other code"))
    assert run(ports)["recording_complete"] is True
    assert len(calls)==1
    other=PublicPoolVerifier("https://different-publication.example.com",transport=lambda _:(_ for _ in ()).throw(AssertionError("HTTP")))
    W.enqueue(jobs,journal,doc["envelope_id"],other.policy,CODE)
    result=W.run_once(jobs,journal,other,"worker",CODE)
    assert result["status"]=="pending" and result["recording_complete"] is False
    assert len(calls)==1


def test_late_response_budget_preserves_original_proof_for_next_invocation(ports):
    jobs,journal,verifier,clock,calls,doc,_=ports
    enqueue(ports);time=[0.0];original=journal.commit_publication_attestation
    def late(*args):
        proof=original(*args);time[0]=1000;return proof
    with patch.object(journal,"commit_publication_attestation",side_effect=late):
        pending=run(ports,monotonic=lambda:time[0])
    assert pending["status"]=="pending" and journal.get_publication_attestation(doc["envelope_id"])
    clock.advance(61)  # Expired invocation starts no cleanup I/O; lease expiry recovers.
    recovered=run(ports)
    assert recovered["recording_complete"] is True and len(calls)==1


def test_worker_claims_only_one_of_two_distinct_policy_jobs(ports):
    jobs,journal,verifier,_,calls,doc,_=ports
    first=enqueue(ports)
    other_policy={**verifier.policy,"origin":"https://other-publication.example.com"}
    second=W.enqueue(jobs,journal,doc["envelope_id"],other_policy,CODE)
    result=run(ports)
    assert result["recording_complete"] is True and len(calls)==1
    assert jobs.read_job(first["definition"]["job_id"])["state"]["attempt"]==1
    assert jobs.read_job(second["definition"]["job_id"])["state"]["attempt"]==0


def test_discovery_policy_filter_prevents_seventeen_old_origins_starving_current_job(ports):
    jobs,journal,verifier,_,calls,doc,_=ports
    old=[]
    for index in range(17):
        old.append(W.enqueue(jobs,journal,doc["envelope_id"],
            {**verifier.policy,"origin":f"https://old{index}.example.com"},CODE))
    current=enqueue(ports)
    result=run(ports)
    assert result["recording_complete"] is True and result["job_id"]==current["definition"]["job_id"]
    assert len(calls)==1
    assert all(jobs.read_job(row["definition"]["job_id"])["state"]["attempt"]==0 for row in old)


def test_existing_proof_recovery_never_scans_receipt_history_or_fetches_each_generic(ports):
    jobs,journal,verifier,_,calls,doc,_=ports
    queued=enqueue(ports)
    journal.commit_publication_attestation(doc["envelope_id"],verifier.observe())
    with patch.object(journal,"receipts",side_effect=AssertionError("unbounded history scan")),patch.object(journal,"get_receipt",side_effect=AssertionError("N remote lookups")):
        result=run(ports)
    assert result["recording_complete"] is True and len(calls)==1


def test_lost_completion_reply_recovers_exact_result_without_release_or_more_http(ports):
    jobs,journal,_,_,calls,_,_=ports
    queued=enqueue(ports);append=jobs.append_job_command;commands=[]
    def lost(command):
        commands.append(command["action"])
        response=append(command)
        if command["action"]=="complete":raise OSError("lost committed completion response")
        return response
    with patch.object(jobs,"append_job_command",side_effect=lost):result=run(ports)
    assert result["recording_complete"] is True and result["recovered_completion"] is True
    assert commands==["claim","complete"] and len(calls)==1
    assert jobs.read_job(queued["definition"]["job_id"])["state"]["result_object_id"]==result["result_object_id"]


def test_wrong_committed_completion_result_cannot_be_recovered_as_success(ports):
    jobs,journal,_,_,calls,_,_=ports
    enqueue(ports);append=jobs.append_job_command
    bad={"version":1,"kind":"unrelated.result"};bad_id=fingerprint(bad)
    jobs.append_object("source_snapshot",bad_id,bad)
    def wrong(command):
        if command["action"]=="complete":
            altered={k:v for k,v in command.items() if k!="command_id"};altered["object_id"]=bad_id
            append(dto.seal_command(altered,authority_id=AUTHORITY))
            raise OSError("lost wrong result reply")
        return append(command)
    with patch.object(jobs,"append_job_command",side_effect=wrong):result=run(ports)
    assert result["recording_complete"] is False and result["status"]=="pending"
    assert len(calls)==1


def test_exhausted_invocation_starts_no_cleanup_requests(ports):
    jobs,journal,_,_,calls,_,_=ports
    enqueue(ports);clock=[0.0];reads=[];original=jobs.read_job;commit=journal.commit_publication_attestation
    def track(job_id):reads.append(clock[0]);return original(job_id)
    def late(*args):
        result=commit(*args);clock[0]=1000;return result
    with patch.object(jobs,"read_job",side_effect=track),patch.object(journal,"commit_publication_attestation",side_effect=late):
        result=run(ports,monotonic=lambda:clock[0])
    assert result["status"]=="pending" and len(calls)==1
    assert all(value==0 for value in reads)


def test_mixed_original_kickoffs_record_partial_proof_as_completed_job(ports):
    jobs,journal,verifier,clock,calls,_,_=ports
    doc,sources=graph(kickoff="2030-01-01T13:00:00Z")
    pool=sources["leg_pool"];second=copy.deepcopy(pool["players"][0])
    second.update(game_id="G2",gsis_id="second-player")
    source=second["rungs"][0]["learning_receipt"]
    source.update(game_id="G2",player_id="second-player")
    source["features"]["correlation_tags"]=["game:G2"]
    source["contract_id"]=fingerprint(list(canonical_key(source,2026)))
    source["receipt_id"]=fingerprint({k:v for k,v in source.items() if k!="receipt_id"})
    pool["players"].append(second)
    game={**sources["schedule"]["games"][0],"game_id":"G2","kickoff_utc":"2030-01-01T14:00:00Z"}
    sources["schedule"]["games"].append(game)
    row=copy.deepcopy(doc["receipts"][0])
    row.update(receipt_id=source["receipt_id"],learning_receipt=copy.deepcopy(source),kickoff_utc=game["kickoff_utc"])
    row["leg"].update(game_id="G2",gsis_id="second-player")
    doc["receipts"].append(row);gen=doc["generations"][0]
    gen["source_snapshots"]={k:fingerprint(v) for k,v in sources.items()}
    gen["receipt_ids"]=sorted(r["receipt_id"] for r in doc["receipts"])
    gen["generation_id"]=fingerprint({k:gen[k] for k in MANIFEST_KEYS})
    for item in doc["receipts"]:item["generation_id"]=gen["generation_id"]
    doc["envelope_id"]=fingerprint({k:v for k,v in doc.items() if k!="envelope_id"})
    for value in sources.values():journal.append_object("source_snapshot",fingerprint(value),value)
    journal.append_object("generation",doc["envelope_id"],doc)
    assert len(finalize_generation(journal,doc["envelope_id"])["receipts"])==2
    def fetch(url):
        calls.append(url)
        return 200,{"Content-Type":"application/json"},json.dumps(pool).encode(),url
    verifier.transport=fetch
    queued=W.enqueue(jobs,journal,doc["envelope_id"],verifier.policy,CODE)
    clock.advance(3601)
    result=run(ports)
    assert result["recording_complete"] is True and result["publication_status"]=="partial"
    assert result["accepted_receipts"]==1 and result["rejected_receipts"]==1
    assert jobs.read_job(queued["definition"]["job_id"])["effective_status"]=="succeeded"
    assert result["model_adoption"] is False and len(calls)==1


def test_policy_mutation_during_observation_never_commits_wrong_origin(ports):
    _,journal,verifier,_,_,doc,_=ports
    enqueue(ports);original=verifier.transport
    def changed(url):
        response=original(url);verifier.policy["origin"]="https://changed.example.com";return response
    verifier.transport=changed
    result=run(ports)
    assert result["recording_complete"] is False and result["status"]=="pending"
    assert journal.get_publication_attestation(doc["envelope_id"]) is None


def test_resealed_recovered_proof_before_generation_is_not_accepted(ports):
    _,journal,verifier,_,calls,doc,_=ports
    enqueue(ports)
    original=journal.commit_publication_attestation(doc["envelope_id"],verifier.observe())
    bad=copy.deepcopy(original)
    bad["observation"]["observed_started_utc"]="2026-09-01T14:00:00Z"
    bad["attestation_id"]=fingerprint({k:v for k,v in bad.items() if k!="attestation_id"})
    with patch.object(journal,"get_publication_attestation",return_value=bad):result=run(ports)
    assert result["status"]=="pending" and result["recording_complete"] is False
    assert len(calls)==1
    assert journal.get_publication_attestation(doc["envelope_id"])==original


def test_idle_describes_only_current_code_policy_not_the_whole_queue(ports):
    jobs,journal,verifier,_,calls,doc,_=ports
    old=W.enqueue(jobs,journal,doc["envelope_id"],
        {**verifier.policy,"origin":"https://old-policy.example.com"},CODE)
    result=run(ports)
    assert result["status"]=="idle" and result["candidate_count"]==0
    assert result["selection_scope"]=="current_code_and_publication_policy_only"
    assert result["code_id"]==CODE and result["policy_id"]==fingerprint(verifier.policy)
    assert jobs.read_job(old["definition"]["job_id"])["effective_status"]=="queued" and calls==[]


def test_new_proof_path_never_scans_global_receipts_or_fetches_each_generic(ports):
    _,journal,_,_,calls,_,_=ports
    enqueue(ports)
    with patch.object(journal,"receipts",side_effect=AssertionError("unbounded history scan")),patch.object(journal,"get_receipt",side_effect=AssertionError("N generic lookups")):
        result=run(ports)
    assert result["recording_complete"] is True and len(calls)==1
