"""Actual NFL raw fixture → Python v2 journal → PostgreSQL-WASM SQL.

Private chunks are explicitly fake in-memory bytes; no hosted Storage, HTTP,
native concurrency, production runtime or external source authentication proof.
Missing reviewed PGlite/NFL fixture dependencies fail, rather than skip.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest

from selflearn_core.adapters import nfl_capture_application_v2 as V
from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal
from selflearn_core.adapters import nfl_capture_application_job as job
from selflearn_core.adapters.nfl_capture_delivery_journal import make_capture_delivery_request
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import StoreError,StoreConflict
from selflearn_core.store.receipt_journal import JournalConflict
from selflearn_core.adapters.nfl_capture_application_journal import CaptureApplicationBudgetExceeded
from test_adapter_integration import PostgresBridge, ROOT
from test_capture_outcomes_atomic import raw_producer, admit, stage

REGISTER = "/rest/v1/rpc/selflearn_journal_register_capture_application_v2"
COMMIT = "/rest/v1/rpc/selflearn_journal_commit_capture_application_batch_v2"


class ApplicationBridge(PostgresBridge):
    """Same tested pipe contract, fixed additive-v2 migration/RPC allowlist."""
    def __init__(self):
        if not os.environ.get("SELFLEARN_PGLITE_MODULE"):
            raise RuntimeError("Set SELFLEARN_PGLITE_MODULE to the reviewed local module")
        self.process = subprocess.Popen(["node", "tests/postgres/capture_application_bridge.mjs"], cwd=ROOT,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1)
        self.calls, self.counter, self.errors = [], 0, []
        try:
            self.ready = self._read(timeout=30)
            if self.ready.get("ready") is not True or self.ready.get("engine") != "postgresql-wasm":
                raise RuntimeError("Actual v2 PostgreSQL bridge did not initialize")
        except Exception:
            self.close()
            raise

    def _read(self, **kwargs):
        response = super()._read(**kwargs)
        if response.get("ok") is False:
            self.errors.append((response.get("code"),response.get("message")))
        return response


@pytest.fixture
def application_bridge():
    bridge = ApplicationBridge()
    try:
        yield bridge
    finally:
        bridge.close()


@pytest.fixture
def journal(application_bridge):
    return NFLCaptureApplicationJournal("https://offline.supabase.invalid", "sb_secret_application_fixture_only",
        transport=application_bridge, page_size=1)


@pytest.fixture(scope="module")
def missing_stat_capture(raw_producer):
    # The actual producer's raw capture, NOT normalized caller result flags.
    script = r'''
import base64,importlib.util,json
from pathlib import Path
spec=importlib.util.spec_from_file_location('f',Path('tests/fixtures/week6_outcomes.py'))
f=importlib.util.module_from_spec(spec);spec.loader.exec_module(f)
c=f.bundle(value='')
print(json.dumps({'manifest':c['manifest'],'observations':c['observations'],
 'raw':{k:base64.b64encode(v).decode() for k,v in c['raw_objects'].items()}}))
'''
    result=subprocess.run([sys.executable,"-c",script],cwd=os.environ["NFL2026_CHECKOUT"],
        capture_output=True,text=True,timeout=30)
    if result.returncode:
        raise RuntimeError("Actual local NFL missing-stat raw capture failed")
    return json.loads(result.stdout)


def initial(journal, raw_producer):
    plan = raw_producer["initial"]
    admit(journal, plan["source_objects"], [item["request"]["payload"] for item in plan["generations"]])


def prepare(journal, capture, **limits):
    envelope, chunks = stage(capture)
    original = make_capture_delivery_request(envelope, list(chunks.pointers.values()),
        destination_id=fingerprint("local-v2-application-fixture"), producer_id="nfl2026")
    request = V.make_request(original)
    ctx = V.bind_context(journal, request, chunks.read,
        clock=lambda: datetime.now(timezone.utc).isoformat())
    return ctx, V.prepare_seal(ctx, **limits), chunks


def commit_first(journal, ctx, seal):
    assignment = seal["batches"][0]
    original = {g["group_id"]: g for g in ctx["staged"]["groups"]}
    groups, observations, proposals = [], [], []
    for gid in assignment["group_ids"]:
        group, obs, rev = V.group_proofs(ctx, original[gid])
        groups.append(group); observations.extend(obs); proposals.extend(rev)
    return journal.commit_capture_application_batch(seal["application_id"], assignment["step_id"],
        groups, observations, proposals, watermark=ctx["snapshot"]["watermark"])


def test_actual_original_forecast_raw_register_active_commit_terminal_and_pinned_reads(journal, raw_producer):
    initial(journal, raw_producer)
    before = journal.read_observed_snapshot()
    ctx, seal, chunks = prepare(journal, raw_producer["captures"][0])
    assert len(before["receipts"]) == 4 and len(seal["groups"]) == len(seal["batches"]) == 1
    ack = journal.register_capture_application(ctx["request"], seal, watermark=before["watermark"])
    assert ack["status"] == "registered" and ack["learning_accepted"] is False
    active = journal.get_capture_application(seal["application_id"])
    assert active["registration"] == ack and active["terminal"] is None
    assert active["seal"] == seal
    assert active["status"]["status"] == "active"
    assert active["status"]["durable_ack"] is False and active["status"]["execution_ready"] is True
    assert journal.get_capture_application_batch(seal["application_id"], seal["batches"][0]["step_id"]) is None
    pinned = active["ledger_watermark"]
    result = commit_first(journal, ctx, seal)
    assert (result["added_observations"], result["added_revisions"]) == (4, 4)
    complete = journal.get_capture_application(seal["application_id"])
    assert complete["terminal"]["status"] == "accepted"
    assert complete["terminal"]["processing_complete"] is complete["terminal"]["complete_success"] is True
    assert complete["terminal"]["counts"]["members"] == complete["terminal"]["counts"]["accepted_members"] == 4
    assert complete["status"]["execution_ready"] is False
    assert complete["registration"] == ack, "Original registration ACK never changes into progress"
    assert journal.get_capture_application_batch(seal["application_id"], result["step_id"]) == result
    historical = journal.get_capture_application(seal["application_id"], ledger_watermark=pinned)
    assert historical == active
    after = journal.read_observed_snapshot()
    assert after["receipts"] == before["receipts"]
    assert len(after["observations"]) == len(after["revisions"]) == 4
    assert all(row["resolution"]["hit"] is True for row in after["revisions"])
    assert chunks.calls, "Actual raw bytes passed the real verifier/binder"


@pytest.mark.parametrize("admitted,low_byte_limit", [(False,False),(True,False),(False,True),(True,True)])
def test_zero_planned_registration_terminalizes_without_outcome_writes(journal, raw_producer, missing_stat_capture, admitted, low_byte_limit):
    if admitted:
        initial(journal, raw_producer)
    before = journal.read_observed_snapshot()
    ctx, seal, _ = prepare(journal, raw_producer["captures"][0] if low_byte_limit else missing_stat_capture,
        **({"max_batch_bytes":100} if low_byte_limit else {}))
    assert seal["batches"] == []
    ack = journal.register_capture_application(ctx["request"], seal, watermark=before["watermark"])
    assert ack["next_step_id"] is None and ack["learning_accepted"] is False
    result = journal.get_capture_application(seal["application_id"])
    assert result["terminal"]["status"] == ("withheld" if admitted else "no_supported_membership")
    assert result["terminal"]["processing_complete"] is True
    assert result["terminal"]["complete_success"] is result["terminal"]["learning_accepted"] is False
    assert journal.read_observed_snapshot() == before


@pytest.mark.parametrize("route", [REGISTER, COMMIT])
def test_lost_committed_reply_recovers_exact_original_without_hidden_write_retry(journal, application_bridge, raw_producer, route):
    initial(journal, raw_producer)
    ctx, seal, _ = prepare(journal, raw_producer["captures"][0])
    actual = journal._request
    lost = []

    def lose_once(method, path, **kwargs):
        result = actual(method, path, **kwargs)
        if path == route and not lost:
            lost.append(copy.deepcopy(result))
            raise TimeoutError("synthetic local lost reply after actual SQL commit")
        return result

    journal._request = lose_once
    if route == REGISTER:
        with pytest.raises(StoreError):
            journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
        stored = journal.get_capture_application(seal["application_id"])
        assert stored["registration"] == lost[0] and stored["terminal"] is None
        recovered = journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
        assert recovered == lost[0]
        assert sum(c["path"] == REGISTER for c in application_bridge.calls) == 2, "Only the explicit operator retry writes again"
    else:
        journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
        with pytest.raises(StoreError):
            commit_first(journal, ctx, seal)
        recovered = journal.get_capture_application_batch(seal["application_id"], seal["batches"][0]["step_id"])
        assert recovered == lost[0]
        assert commit_first(journal, ctx, seal) == recovered
        assert sum(c["path"] == COMMIT for c in application_bridge.calls) == 1
        assert len(journal.read_observed_snapshot()["revisions"]) == 4


def test_later_membership_and_correction_never_reinterpret_original_application(journal, raw_producer):
    initial(journal, raw_producer)
    ctx, seal, _ = prepare(journal, raw_producer["captures"][0])
    ack = journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
    first = commit_first(journal, ctx, seal)
    original = journal.get_capture_application(seal["application_id"])
    admit(journal, raw_producer["complete"]["source_objects"], [raw_producer["extra"]])
    corrected_ctx, corrected_seal, _ = prepare(journal, raw_producer["captures"][2])
    assert sum(len(g["receipt_ids"]) for g in corrected_seal["groups"]) == 6
    journal.register_capture_application(corrected_ctx["request"], corrected_seal, watermark=corrected_ctx["snapshot"]["watermark"])
    correction = commit_first(journal, corrected_ctx, corrected_seal)
    assert correction["added_observations"] == correction["added_revisions"] == 6
    assert all(row["hit"] is False for row in journal.latest_resolutions())
    assert journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"]) == ack
    assert commit_first(journal, ctx, seal) == first
    current_original = journal.get_capture_application(seal["application_id"])
    assert current_original["terminal"] == original["terminal"]
    assert current_original["terminal"]["counts"]["members"] == 4
    assert all(row["hit"] is False for row in journal.latest_resolutions())


def test_actual_resumable_job_registers_steps_then_recovers_before_raw_or_clock(journal,raw_producer):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    clock=lambda:datetime.now(timezone.utc).isoformat()
    ack=job.register_capture_application(journal,ctx["request"],chunks.read,clock=clock)
    assert ack["learning_accepted"] is False
    result=job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],chunks.read,clock=clock)
    assert (result["added_observations"],result["added_revisions"])==(4,4)
    original=journal.get_capture_application(seal["application_id"])
    assert original["terminal"]["complete_success"] is True
    def forbidden(*args):raise AssertionError("Exact original recovery must precede raw reads/source clock")
    assert job.register_capture_application(journal,ctx["request"],forbidden,clock=forbidden)==ack
    assert job.commit_capture_application_step(journal,seal["application_id"],result["step_id"],forbidden,clock=forbidden)==result
    assert journal.get_capture_application(seal["application_id"])["terminal"]==original["terminal"]


def test_job_complete_membership_change_terminalizes_before_raw_or_outcome_writes(journal,raw_producer):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    ack=job.register_capture_application(journal,ctx["request"],chunks.read,
        clock=lambda:datetime.now(timezone.utc).isoformat())
    admit(journal,raw_producer["complete"]["source_objects"],[raw_producer["extra"]])
    before=journal.read_observed_snapshot()
    assert len(before["receipts"])==6
    def forbidden(*args):raise AssertionError("Membership mismatch must stop before raw/source-clock work")
    result=job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],forbidden,clock=forbidden)
    assert result["status"]=="not_applied"
    assert result["added_observations"]==result["added_revisions"]==0
    stored=journal.get_capture_application(seal["application_id"])
    assert stored["terminal"]["status"]=="incomplete_membership_changed"
    assert stored["terminal"]["counts"]["members"]==4
    assert stored["terminal"]["current_membership_id"]!=stored["seal"]["membership_id"]
    assert stored["terminal"]["processing_complete"] is stored["terminal"]["complete_success"] is False
    assert journal.read_observed_snapshot()==before
    assert job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],forbidden,clock=forbidden)==result


@pytest.mark.parametrize("phase",["register","step"])
def test_actual_cas_change_rebinds_complete_raw_context_but_semantic_conflict_does_not(journal,raw_producer,phase):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    bind_reads=len(chunks.calls)
    clock=lambda:datetime.now(timezone.utc).isoformat()
    calls=[]
    method="register_capture_application" if phase=="register" else "commit_capture_application_batch"
    if phase=="step":
        ack=job.register_capture_application(journal,ctx["request"],chunks.read,clock=clock)
    real=getattr(journal,method)
    def race(*args,**kwargs):
        calls.append(kwargs["watermark"])
        if len(calls)==1:
            payload={"test":"unrelated actual evidence write", "phase":phase}
            journal.append_object("source_snapshot",fingerprint(payload),payload)
        return real(*args,**kwargs)
    setattr(journal,method,race)
    start=len(chunks.calls)
    if phase=="register":
        result=job.register_capture_application(journal,ctx["request"],chunks.read,clock=clock)
        assert result["status"]=="registered"
    else:
        result=job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],chunks.read,clock=clock)
        assert result["added_observations"]==result["added_revisions"]==4
    assert len(calls)==2 and int(calls[1])>int(calls[0])
    assert len(chunks.calls)-start==2*bind_reads,"Actual changed CAS must re-read all raw input, not reuse normalized labels"


def test_actual_semantic_vector_conflict_never_retries_same_fence_or_rebinds_raw_twice(journal,raw_producer):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    journal.register_capture_application(ctx["request"],seal,watermark=ctx["snapshot"]["watermark"])
    request=copy.deepcopy(ctx["request"]["capture_request"])
    for source in request["envelope"]["observations"].values():source["http"]["etag"]="metadata-only restamp fixture"
    envelope=request["envelope"]
    changed_observations={}
    for ref in envelope["manifest"]["sources"]:
        source=envelope["observations"][ref["observation_id"]]
        ref["observation_id"]=fingerprint(source)
        changed_observations[ref["observation_id"]]=source
    envelope["observations"]=changed_observations
    envelope["manifest"]["capture_id"]=fingerprint({key:value for key,value in envelope["manifest"].items() if key!="capture_id"})
    changed=V.make_request(make_capture_delivery_request(request["envelope"],request["raw_pointers"],
        destination_id=request["destination_id"],producer_id=request["producer_id"]))
    assert changed["application_id"]!=seal["application_id"]
    assert V.source_vector_id(changed["capture_request"]["envelope"])==seal["source_vector_id"]
    start=len(chunks.calls)
    with pytest.raises(JournalConflict,match="conflict_without_changed_fence"):
        job.register_capture_application(journal,changed,chunks.read,clock=lambda:datetime.now(timezone.utc).isoformat())
    assert len(chunks.calls)-start==start,"Only the one new full bind is attempted, not three blind 409 retries"
    assert journal.get_capture_application(changed["application_id"]) is None


@pytest.mark.parametrize("failure",[TimeoutError("temporary raw timeout"),StoreError(503)])
def test_transient_raw_failures_leave_active_application_not_terminal(journal,raw_producer,failure):
    initial(journal,raw_producer)
    ctx,seal,_=prepare(journal,raw_producer["captures"][0])
    ack=journal.register_capture_application(ctx["request"],seal,watermark=ctx["snapshot"]["watermark"])
    before=journal.read_observed_snapshot()
    def unavailable(_oid):raise failure
    with pytest.raises(type(failure)):
        job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],unavailable,
            clock=lambda:datetime.now(timezone.utc).isoformat())
    assert journal.get_capture_application(seal["application_id"])["terminal"] is None
    assert journal.read_observed_snapshot()==before


def test_verified_integrity_fault_stops_durably_without_evidence_partial_success(journal,raw_producer):
    initial(journal,raw_producer)
    ctx,seal,_=prepare(journal,raw_producer["captures"][0])
    ack=journal.register_capture_application(ctx["request"],seal,watermark=ctx["snapshot"]["watermark"])
    before=journal.read_observed_snapshot()
    lengths={row["raw_sha256"]:row["raw_bytes"] for row in ctx["request"]["capture_request"]["envelope"]["raw_objects"]}
    def corrupt(oid):yield b"x"*lengths[oid]
    with pytest.raises(job.CaptureApplicationStopped) as caught:
        job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],corrupt,
            clock=lambda:datetime.now(timezone.utc).isoformat())
    result=caught.value.result
    assert result["status"]=="incomplete_integrity_or_capacity"
    assert result["reason_codes"]==["source_integrity_failed"]
    assert result["complete_success"] is result["processing_complete"] is False
    assert journal.get_capture_application(seal["application_id"])["terminal"]==result
    assert journal.read_observed_snapshot()==before
    def forbidden(*args):raise AssertionError("An existing incomplete terminal must recover before raw/source clock")
    with pytest.raises(job.CaptureApplicationStopped) as recovered:
        job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],forbidden,clock=forbidden)
    assert recovered.value.result==result


def test_injected_runtime_capacity_stop_and_budget_pending_are_distinct(journal,application_bridge,raw_producer):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    ack=journal.register_capture_application(ctx["request"],seal,watermark=ctx["snapshot"]["watermark"])
    ticks=iter([0,2])
    def forbidden(*args):raise AssertionError("Expired budget must not read raw/source clock")
    calls=len(application_bridge.calls)
    with pytest.raises(CaptureApplicationBudgetExceeded):
        job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],forbidden,
            clock=forbidden,max_run_seconds=1,monotonic=lambda:next(ticks))
    assert len(application_bridge.calls)==calls
    assert journal.get_capture_application(seal["application_id"])["terminal"] is None
    # A deterministic capacity guard is deliberately injected. The resulting
    # terminal is actual SQL; this is NOT measured natural wire growth.
    before=journal.read_observed_snapshot()
    with patch.object(V,"batch_wire",side_effect=V.capacity._WireLimit("injected capacity test")):
        with pytest.raises(job.CaptureApplicationStopped) as caught:
            job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],chunks.read,
                clock=lambda:datetime.now(timezone.utc).isoformat())
    assert caught.value.result["reason_codes"]==["runtime_capacity_exceeded"]
    assert caught.value.result["complete_success"] is False
    assert journal.read_observed_snapshot()==before


@pytest.mark.parametrize("phase",["register","step"])
def test_job_conflict_after_actual_commit_recovers_original_before_rebind(journal,application_bridge,raw_producer,phase):
    initial(journal,raw_producer)
    ctx,seal,chunks=prepare(journal,raw_producer["captures"][0])
    clock=lambda:datetime.now(timezone.utc).isoformat()
    route=REGISTER if phase=="register" else COMMIT
    if phase=="step":
        ack=job.register_capture_application(journal,ctx["request"],chunks.read,clock=clock)
    real=journal._request
    lost=[]
    def concurrent_original(method,path,**kwargs):
        response=real(method,path,**kwargs)
        if path==route and not lost:
            lost.append(copy.deepcopy(response))
            raise StoreConflict()
        return response
    journal._request=concurrent_original
    before=len(chunks.calls)
    if phase=="register":result=job.register_capture_application(journal,ctx["request"],chunks.read,clock=clock)
    else:result=job.commit_capture_application_step(journal,seal["application_id"],ack["next_step_id"],chunks.read,clock=clock)
    assert result==lost[0]
    assert len(chunks.calls)-before==5,"Recover a now-known original before any second full bind"
    assert sum(row["path"]==route for row in application_bridge.calls)==1
