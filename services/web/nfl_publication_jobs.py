"""Opt-in persistent publication recording; never fitting or model adoption.

A job lease fences job completion, not the independently idempotent publication
transaction. Recover the original attestation before observing HTTP again.
Deadlines are cooperative checks, not hard process/network execution ceilings.
"""
from __future__ import annotations

from datetime import timedelta
import hashlib
import os
from pathlib import Path
import re
import stat
import time

from selflearn_core import learning_jobs as dto
from selflearn_core.probability_receipts import fingerprint, instant
from selflearn_core.adapters.nfl import validate_nfl_receipt
from selflearn_core.adapters.nfl_generation import validate_generation
from selflearn_core.adapters.nfl_publication import validate_policy, validate_publication_attestation

NAMESPACE = {"domain":"nfl", "task":"publication.recording", "producer_id":"nfl2026"}
OPERATION = "nfl.publication.record"
LEASE_SECONDS, MAX_RUNTIME_SECONDS, MAX_ATTEMPTS, MAX_STEPS = 60, 3600, 8, 1
RECOVERY_SECONDS, INVOCATION_SECONDS = 3600, 35
FALSE_FLAGS = {"learning_enabled":False, "model_adoption":False, "fitting_run":False,
               "browser_display_verified":False, "hard_execution_limit":False}


class InvocationExpired(RuntimeError):
    """No additional I/O starts after the cooperative invocation deadline."""


class CheckedPort:
    """Check the invocation budget around each underlying port operation."""
    def __init__(self,port,check):self.port,self.check=port,check
    def __getattr__(self,name):
        value=getattr(self.port,name)
        if not callable(value):return value
        def invoke(*args,**kwargs):
            self.check();result=value(*args,**kwargs);self.check();return result
        return invoke


def from_environment(env):
    """No credentials, code scanning, database access or implicit activation."""
    if env.get("SELFLEARN_PUBLICATION_WORKER_MODE") != "record":
        return None
    return {"mode":"record", "namespace":dict(NAMESPACE), "operation":OPERATION,
        "invocation_budget_seconds":INVOCATION_SECONDS, "lease_seconds":LEASE_SECONDS,
        "max_runtime_seconds":MAX_RUNTIME_SECONDS, "max_attempts":MAX_ATTEMPTS,
        "max_steps":MAX_STEPS, "recovery_seconds":RECOVERY_SECONDS}


def code_identity():
    """Conservative local source identity, not hosted execution authentication."""
    root = Path(__file__).resolve().parent
    sources, total = [], 0
    for path in sorted(root.glob("*.py")):
        dto.need(not path.is_symlink(), "publication_worker_code_symlink")
        descriptor = os.open(path, os.O_RDONLY | getattr(os,"O_NOFOLLOW",0) | getattr(os,"O_NONBLOCK",0))
        with os.fdopen(descriptor,"rb") as handle:
            dto.need(stat.S_ISREG(os.fstat(handle.fileno()).st_mode))
            raw = handle.read(8*1024*1024+1)
        total += len(raw)
        dto.need(len(raw) <= 8*1024*1024 and total <= 32*1024*1024 and len(sources) < 256)
        sources.append({"path":path.name, "sha256":hashlib.sha256(raw).hexdigest()})
    return fingerprint({"kind":"nfl.publication_worker_code.v1", "core":dto.code_identity(), "web_sources":sources})


def _source(port, identity):
    value = port.get_object(identity)
    dto.need(type(value) is dict and value.get("kind") == "source_snapshot"
             and fingerprint(value.get("payload")) == identity, "publication_job_reference_invalid")
    return value["payload"]


def _generation(jobs, journal, object_id):
    dto.digest(object_id)
    obj = journal.get_object(object_id)
    paired = jobs.get_object(object_id)
    dto.need(type(obj) is dict and type(paired) is dict and obj.get("kind") == "generation"
        and paired.get("kind") == "generation" and fingerprint(obj["payload"]) == fingerprint(paired["payload"]),
        "publication_job_generation_not_shared")
    doc = validate_generation(journal, object_id, now_utc=obj["received_utc"])
    gen = doc["generations"][0]
    ack = journal.generation_ack(object_id)
    dto.need(type(ack) is dict and set(ack)=={"version","object_id","durable","receipts","rejections"}
        and type(ack["version"]) is int and ack["version"]==1
        and ack["durable"] is True and ack["object_id"] == object_id
        and type(ack["receipts"]) is list and type(ack["rejections"]) is list,
             "publication_job_generation_unfinalized")
    rows = {r["receipt_id"]:r for r in doc["receipts"]}
    dto.need(len(rows)==len(doc["receipts"]) and len(rows)<=10000,"publication_job_membership_invalid")
    accepted,seen,generic_ids={},{},set()
    for row in ack["receipts"]:
        dto.need(type(row) is dict and set(row)=={"source_receipt_id","receipt_id","status"}
            and row["status"] in ("recorded","duplicate"),"publication_job_membership_invalid")
        sid,rid=dto.digest(row["source_receipt_id"]),dto.digest(row["receipt_id"])
        dto.need(sid not in seen and rid not in generic_ids,"publication_job_membership_invalid")
        accepted[sid]=rid;seen[sid]=True;generic_ids.add(rid)
    for row in ack["rejections"]:
        dto.need(type(row) is dict and set(row)=={"source_receipt_id","reason"}
            and type(row["reason"]) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,99}",row["reason"]),
            "publication_job_membership_invalid")
        sid=dto.digest(row["source_receipt_id"])
        dto.need(sid not in seen,"publication_job_membership_invalid");seen[sid]=True
    dto.need(bool(accepted) and set(seen)==set(rows),"publication_job_membership_invalid")
    # Finalized ACKs are immutable, atomically committed storage authority for
    # original generic IDs. Re-minting would lose an earlier capture time; an
    # all-history scan or N remote receipt lookups is unnecessary here. The
    # publication private write and returned proof independently bind these IDs.
    expected = {}
    for sid,rid in accepted.items():
        source = rows[sid]["learning_receipt"]
        # Frozen NFL v1 legs omit these redundant fixed-contract fields. Match
        # ingestion's read-only view; explicit conflicts/null remain untouched.
        leg={"direction":"over","comparison_operator":"gte",**rows[sid]["leg"]}
        validate_nfl_receipt(source,leg,gen["season"],gen["week"])
        expected[sid] = {"receipt_id":rid, "source_receipt_id":sid,
            "market":source["market"], "direction":source["direction"],
            "served_prob":source["served_prob"], "event_start_utc":rows[sid]["kickoff_utc"]}
    return gen, expected


def _settings(policy):
    validate_policy(policy)
    return {"version":1, "kind":"nfl.publication_job_policy.v1", "publication_policy":dict(policy),
        "lease_seconds":LEASE_SECONDS, "max_runtime_seconds":MAX_RUNTIME_SECONDS,
        "max_attempts":MAX_ATTEMPTS, "max_steps":MAX_STEPS, "recovery_seconds":RECOVERY_SECONDS}


def _definition(jobs, generation, expected, object_id, policy, code_id):
    settings = _settings(policy)
    inputs = {"version":1, "kind":"nfl.publication_job_input.v1", "object_id":object_id,
              "generation_id":generation["generation_id"], "policy_id":fingerprint(policy)}
    spec = dto.seal_spec({"version":1, "kind":"selflearn.job_definition.v1",
        "namespace":dict(NAMESPACE), "operation":OPERATION,
        "input_object_id":fingerprint(inputs), "policy_object_id":fingerprint(settings), "code_id":code_id,
        "not_before_utc":generation["recorded_utc"],
        "not_after_utc":dto.stamp(max(instant(r["event_start_utc"]) for r in expected.values()) + timedelta(seconds=RECOVERY_SECONDS)),
        "lease_seconds":LEASE_SECONDS, "max_runtime_seconds":MAX_RUNTIME_SECONDS,
        "max_attempts":MAX_ATTEMPTS, "max_steps":MAX_STEPS}, authority_id=jobs.authority_id)
    return spec, inputs, settings


def enqueue(jobs, journal, object_id, policy, code_id):
    """Explicit recording-only enqueue; callers own enabled/paired-port config."""
    dto.need(code_id == code_identity(), "publication_job_code_changed")
    gen, expected = _generation(jobs,journal,object_id)
    spec, inputs, settings = _definition(jobs,gen,expected,object_id,policy,code_id)
    for value in (inputs,settings):
        jobs.append_object("source_snapshot",fingerprint(value),value)
        dto.need(fingerprint(_source(journal,fingerprint(value))) == fingerprint(value),
                 "publication_job_input_not_shared")
    return jobs.register_job(spec)


def _command(current, action, worker_id, authority_id, *, object_id=None, reason=None):
    return dto.seal_command({"version":1,"kind":"selflearn.job_command.v1",
        "job_id":current["definition"]["job_id"], "expected_head_event_id":current["head_event_id"],
        "action":action, "worker_id":worker_id,
        "lease_id":None if action == "claim" else current["state"]["lease_id"],
        "object_id":object_id, "reason_code":reason},authority_id=authority_id)


def _read(jobs, job_id):
    value = jobs.read_job(job_id)
    checked = dto.complete_snapshot(value["definition"],value["events"],authority_id=jobs.authority_id,
        registered_utc=value["registered_utc"],observed_utc=value["observed_utc"])
    dto.need(dto.canonical(value) == dto.canonical(checked), "publication_job_snapshot_invalid")
    return checked


def _live(jobs, job_id, worker_id, lease_id, head):
    current = _read(jobs,job_id)
    dto.need(current["effective_status"] == "running" and current["state"]["worker_id"] == worker_id
        and current["state"]["lease_id"] == lease_id and current["head_event_id"] == head,
        "publication_job_lease_changed")
    return current


def _context(jobs,journal,current,policy,code_id):
    spec = dto.validate_spec(current["definition"],authority_id=jobs.authority_id)
    dto.need(spec["namespace"] == NAMESPACE and spec["operation"] == OPERATION and spec["code_id"] == code_id,
             "publication_job_scope_changed")
    inputs, settings = (_source(jobs,spec[k]) for k in ("input_object_id","policy_object_id"))
    dto.need(type(inputs) is dict and set(inputs) == {"version","kind","object_id","generation_id","policy_id"}
        and type(inputs["version"]) is int and inputs["version"] == 1
        and inputs["kind"] == "nfl.publication_job_input.v1", "publication_job_input_invalid")
    dto.need(dto.canonical(settings) == dto.canonical(_settings(policy))
        and inputs["policy_id"] == fingerprint(policy), "publication_job_policy_changed")
    gen, expected = _generation(jobs,journal,inputs["object_id"])
    rebuilt,_,_ = _definition(jobs,gen,expected,inputs["object_id"],policy,code_id)
    dto.need(dto.canonical(spec) == dto.canonical(rebuilt) and inputs["generation_id"] == gen["generation_id"],
             "publication_job_definition_changed")
    return inputs,gen,expected


def _proof(value,inputs,gen,expected,policy):
    validate_publication_attestation(value)
    dto.need(value["object_id"] == inputs["object_id"] and value["generation_id"] == gen["generation_id"]
        and value["source_pool_id"] == gen["source_snapshots"]["leg_pool"]
        and value["policy_id"] == fingerprint(policy) and value["policy"] == policy,
        "publication_job_proof_changed")
    actual = {r["source_receipt_id"]:r["receipt_id"] for r in value["receipts"]+value["rejections"]}
    dto.need(actual == {sid:r["receipt_id"] for sid,r in expected.items()}, "publication_job_proof_membership")
    completed,recorded = instant(value["observation"]["observed_completed_utc"]),instant(value["recorded_utc"])
    dto.need(instant(gen["recorded_utc"])<=instant(value["observation"]["observed_started_utc"]),
             "publication_job_proof_before_generation")
    for row in value["receipts"]:
        dto.need(dto.canonical(row) == dto.canonical(expected[row["source_receipt_id"]]), "publication_job_proof_member")
    for row in value["rejections"]:
        kickoff = instant(expected[row["source_receipt_id"]]["event_start_utc"])
        dto.need(completed >= kickoff or recorded >= kickoff, "publication_job_false_late_rejection")
    return value


def _result(spec,inputs,proof):
    return {"version":1,"kind":"nfl.publication_job_result.v1",
        **{k:spec[k] for k in ("job_id","definition_id","input_object_id","policy_object_id","code_id")},
        "object_id":inputs["object_id"],"generation_id":inputs["generation_id"],
        "attestation_id":proof["attestation_id"],"publication_status":proof["status"],
        "accepted_receipts":len(proof["receipts"]),"rejected_receipts":len(proof["rejections"]),
        "recording_complete":True, **FALSE_FLAGS}


def _status(status, **values):
    return {"version":1,"kind":"nfl.publication_worker_status.v1","status":status,
        "recording_complete":False,"publication_verified":False,
        "selection_scope":"current_code_and_publication_policy_only",
        "cooperative_budget_seconds":INVOCATION_SECONDS,"recovery_seconds":RECOVERY_SECONDS,
        "runtime_bound":"earlier_of_not_after_and_first_claim_plus_3600_seconds", **FALSE_FLAGS,**values}


def _run_once(jobs,journal,verifier,worker_id,code_id,*,monotonic=time.monotonic):
    """Claim at most one job; discovery and cached ACKs confer no ownership."""
    dto.name(worker_id)
    dto.need(code_id == code_identity(), "publication_job_code_changed")
    policy = dict(validate_policy(verifier.policy))
    deadline = monotonic()+INVOCATION_SECONDS
    def check():
        if monotonic() >= deadline:raise InvocationExpired()
    def call(fn,*args,**kwargs):
        check();value=fn(*args,**kwargs);check();return value
    jobs,journal=CheckedPort(jobs,check),CheckedPort(journal,check)
    policy_object_id=fingerprint(_settings(policy))
    discovery = call(jobs.list_claimable_jobs,NAMESPACE,OPERATION,code_id=code_id,policy_object_id=policy_object_id,limit=16)
    dto.need(type(discovery) is dict and set(discovery) == {"version","authority_id","namespace","operation","code_id","policy_object_id","observed_utc","job_ids"}
        and type(discovery["version"]) is int and discovery["version"] == 1
        and discovery["authority_id"] == jobs.authority_id and discovery["namespace"] == NAMESPACE
        and discovery["operation"] == OPERATION and discovery["code_id"] == code_id
        and discovery["policy_object_id"]==policy_object_id
        and type(discovery["job_ids"]) is list and len(discovery["job_ids"]) <= 16
        and len(set(discovery["job_ids"])) == len(discovery["job_ids"]), "publication_job_discovery_invalid")
    dto.clock(discovery["observed_utc"])
    for job_id in discovery["job_ids"]:
        dto.digest(job_id)
        current = call(_read,jobs,job_id)
        try:
            inputs,gen,expected = call(_context,jobs,journal,current,policy,code_id)
        except ValueError:
            # Old policy jobs are left intact, not failed or redefined. Scan
            # at most the bounded discovery page for a compatible definition.
            continue
        break
    else:
        return _status("idle",code_id=code_id,policy_id=fingerprint(policy),
            candidate_count=len(discovery["job_ids"]))
    claim = _command(current,"claim",worker_id,jobs.authority_id)
    try:
        call(jobs.append_job_command,claim)  # Its cached ACK is not lease authority.
        current = call(_read,jobs,job_id)
        dto.need(current["effective_status"] == "running" and current["state"]["lease_id"] == claim["command_id"]
            and current["state"]["worker_id"] == worker_id
            and current["events"][-1]["command"]["command_id"] == claim["command_id"],
            "publication_job_lease_changed")
        head = current["head_event_id"]
        proof = call(journal.get_publication_attestation,inputs["object_id"])
        if proof is None:
            current = call(_live,jobs,job_id,worker_id,claim["command_id"],head)
            observation = call(verifier.observe)
            dto.need(observation.get("policy") == policy,"publication_job_policy_changed")
            dto.need(code_id == call(code_identity),"publication_job_code_changed")
            current = call(_live,jobs,job_id,worker_id,claim["command_id"],head)
            proof = call(journal.commit_publication_attestation,inputs["object_id"],observation)
        proof = _proof(proof,inputs,gen,expected,policy)
        current = call(_live,jobs,job_id,worker_id,claim["command_id"],head)
        result = _result(current["definition"],inputs,proof); result_id = fingerprint(result)
        call(jobs.append_object,"source_snapshot",result_id,result)
        dto.need(dto.canonical(call(_source,jobs,result_id)) == dto.canonical(result),"publication_job_result_changed")
        dto.need(code_id == call(code_identity),"publication_job_code_changed")
        current = call(_live,jobs,job_id,worker_id,claim["command_id"],head)
        call(jobs.append_job_command,_command(current,"complete",worker_id,jobs.authority_id,object_id=result_id))
        complete = call(_read,jobs,job_id)
        dto.need(complete["effective_status"] == "succeeded" and complete["state"]["result_object_id"] == result_id
            and dto.canonical(call(_source,jobs,result_id)) == dto.canonical(result),"publication_job_completion_unverified")
        return _status("recording_completed",recording_complete=True,publication_verified=bool(proof["receipts"]),
            job_id=job_id,definition_id=current["definition"]["definition_id"],result_object_id=result_id,
            attestation_id=proof["attestation_id"],publication_status=proof["status"],
            accepted_receipts=len(proof["receipts"]),rejected_receipts=len(proof["rejections"]))
    except Exception:
        # A lost proof/completion reply is recovered from its immutable original
        # next time. Release only if a fresh DB read still grants this lease.
        try:
            live = _read(jobs,job_id)
            if (live["effective_status"]=="succeeded" and "result_id" in locals()
                    and live["state"]["result_object_id"]==result_id):
                persisted=_source(jobs,result_id)
                original=_proof(journal.get_publication_attestation(inputs["object_id"]),inputs,gen,expected,policy)
                dto.need(dto.canonical(persisted)==dto.canonical(_result(live["definition"],inputs,original))
                    and fingerprint(persisted)==result_id,"publication_job_completion_unverified")
                return _status("recording_completed",recording_complete=True,publication_verified=bool(original["receipts"]),
                    job_id=job_id,definition_id=live["definition"]["definition_id"],result_object_id=result_id,
                    attestation_id=original["attestation_id"],publication_status=original["status"],
                    accepted_receipts=len(original["receipts"]),rejected_receipts=len(original["rejections"]),
                    recovered_completion=True)
            if (live["effective_status"] == "running" and live["state"]["lease_id"] == claim["command_id"]
                    and live["state"]["worker_id"] == worker_id):
                jobs.append_job_command(_command(live,"release",worker_id,jobs.authority_id,reason="worker_error"))
        except Exception:
            pass
        return _status("pending",job_id=job_id,reason="publication_recording_pending")


def run_once(jobs,journal,verifier,worker_id,code_id,*,monotonic=time.monotonic):
    try:
        return _run_once(jobs,journal,verifier,worker_id,code_id,monotonic=monotonic)
    except InvocationExpired:
        return _status("pending",reason="publication_invocation_budget")
