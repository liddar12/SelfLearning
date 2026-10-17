"""Explicit POSIX compute boundary around the existing durable shadow job.

No credential is passed to the fixed child; journal IO remains in the parent.
V2 policies bind the execution profile. Legacy v1 inline policies/results retain
their old shape and cannot claim this boundary. Receipts are local observations,
NOT authenticated execution, source/label authority, or model approval.
"""
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time

from .. import learning_jobs as jobs
from .. import compute_process as process
from ..probability_receipts import fingerprint
from ..nfl_compute_worker import decode,work_id
from . import nfl_evaluation_job as nfl
from . import nfl_owner_evidence as evidence

BOOTSTRAP="import sys;sys.path.insert(0,sys.argv[1]);from selflearn_core.nfl_compute_worker import main;raise SystemExit(main([sys.argv[2]]))"
RECEIPT_KEYS={"version","kind","profile_id","job_id","definition_id","code_id","work_id","report_id",
    "stdin_sha256","stdout_sha256","wall_budget_ms","cpu_budget_seconds","elapsed_ns","stderr_bytes","limits",
    "returncode","isolated_python_requested","process_group_cleanup_requested","execution_authenticated","clock_authenticated"}


def validate_execution_receipt(value, *, spec, report, profile):
    value=jobs.snapshot(value);profile=process.validate_profile(profile)
    jobs.need(type(value) is dict and set(value)==RECEIPT_KEYS and type(value["version"]) is int
        and value["version"]==1 and value["kind"]=="selflearn.posix_computation_receipt.v1")
    jobs.need(all(value[k]==spec[k]for k in("job_id","definition_id","code_id"))
        and value["profile_id"]==fingerprint(profile)and value["work_id"]==work_id(spec)and value["report_id"]==report["report_id"])
    for key in("stdin_sha256","stdout_sha256"):jobs.digest(value[key])
    for key,low,high in(("wall_budget_ms",1,profile["wall_seconds"]*1000),("cpu_budget_seconds",1,profile["cpu_seconds"]),
        ("elapsed_ns",0,301_000_000_000),("stderr_bytes",0,profile["stderr_bytes"])):
        jobs.need(type(value[key]) is int and low<=value[key]<=high)
    process.validate_limits(value["limits"],value["cpu_budget_seconds"])
    jobs.need(type(value["returncode"]) is int and value["returncode"]==0 and value["isolated_python_requested"] is True
        and value["process_group_cleanup_requested"] is True and value["execution_authenticated"] is False and value["clock_authenticated"] is False)
    return value


class BoundedNFLEvaluator:
    def __init__(self, profile):
        jobs.need(process.POSIX,"compute_posix_required")
        self.profile=process.validate_profile(profile);self.profile_id=fingerprint(self.profile);self.current=None

    def __call__(self, inputs, settings, spec, current):
        started=time.monotonic()
        jobs.need(settings["version"]==2 and fingerprint(settings["execution_profile"])==self.profile_id)
        self.current=jobs.snapshot(current,limit=jobs.MAX_HISTORY_BYTES)
        remaining=min((jobs.clock(current["state"][key])-jobs.clock(current["observed_utc"])).total_seconds()
            for key in("lease_until_utc","runtime_deadline_utc"))
        wall_ms=min(self.profile["wall_seconds"]*1000,math.floor(remaining*1000))
        if wall_ms<=0:raise process.ComputeProcessError("compute_deadline_exceeded")
        cpu=min(self.profile["cpu_seconds"],max(1,math.ceil(wall_ms/1000)))
        packet={"version":1,"kind":"nfl.compute_request.v1","authority_id":current["authority_id"],
            "definition":spec,"registered_utc":current["registered_utc"],"inputs":inputs,"policy":settings,"cpu_seconds":cpu}
        payload=json.dumps(packet,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
        root=str(Path(__file__).resolve().parents[2])
        # -I ignores Python environment/user paths; -S disables site hooks; -B
        # prevents bytecode writes. No job-selected executable/module/path/env.
        command=[sys.executable,"-I","-S","-B","-u","-c",BOOTSTRAP,root,str(cpu)]
        environment={key:value for key in("LANG","LC_ALL","TZ")if(value:=os.environ.get(key)) is not None}
        try:
            with tempfile.TemporaryDirectory(prefix="selflearn-compute-")as directory:
                budget=wall_ms/1000-(time.monotonic()-started)
                if budget<=0:raise process.ComputeProcessError("compute_deadline_exceeded")
                result=process.run_worker(command,payload,cwd=directory,environment=environment,wall_seconds=budget,
                    input_bytes=self.profile["input_bytes"],output_bytes=self.profile["output_bytes"],stderr_bytes=self.profile["stderr_bytes"])
        except process.ComputeProcessError:raise
        except OSError:raise process.ComputeProcessError("compute_worker_unavailable") from None
        try:
            response=decode(result["stdout"])
            jobs.need(set(response)=={"version","kind","request_id","work_id","profile_id","code_id","report","limits"}
                and type(response["version"]) is int and response["version"]==1 and response["kind"]=="nfl.compute_response.v1"
                and response["request_id"]==fingerprint(packet)and response["work_id"]==work_id(spec)
                and response["profile_id"]==self.profile_id and response["code_id"]==spec["code_id"])
            report=response["report"]
            jobs.need(type(report) is dict and report.get("report_id")==fingerprint({k:v for k,v in report.items()if k!="report_id"}))
            receipt={"version":1,"kind":"selflearn.posix_computation_receipt.v1","profile_id":self.profile_id,
                **{k:spec[k]for k in("job_id","definition_id","code_id")},"work_id":work_id(spec),"report_id":report["report_id"],
                "stdin_sha256":result["input_sha256"],"stdout_sha256":result["output_sha256"],"wall_budget_ms":wall_ms,
                "cpu_budget_seconds":cpu,"elapsed_ns":result["elapsed_ns"],"stderr_bytes":result["stderr_bytes"],"limits":response["limits"],
                "returncode":result["returncode"],"isolated_python_requested":True,"process_group_cleanup_requested":result["process_group_cleanup_requested"],
                "execution_authenticated":False,"clock_authenticated":False}
            validate_execution_receipt(receipt,spec=spec,report=report,profile=self.profile)
            return nfl.ComputedReport(report,receipt)
        except (ValueError,KeyError,TypeError,OverflowError,RecursionError,UnicodeError):
            raise process.ComputeProcessError("compute_response_refused") from None

    def release_failed_attempt(self, journal, job_id, *, authority_id, worker_id):
        """Best effort after ordinary compute refusal, never after cancellation."""
        if self.current is None:return False
        try:
            now=nfl._live(journal,job_id,self.current["head_event_id"],self.current["state"]["lease_id"])
            journal.append_job_command(nfl._command(now,"release",worker_id,authority_id,reason="worker_error"))
            return True
        except Exception:return False


def run_bounded_shadow_job(journal, job_id, *, authority_id, worker_id, max_phases=2):
    jobs.need(getattr(journal,"authority_id",None)==jobs.digest(authority_id))
    current=journal.read_job(jobs.digest(job_id))
    settings=nfl._object(journal,current["definition"]["policy_object_id"],current["registered_utc"]);nfl._policy(settings)
    jobs.need(settings["version"]==2,"bounded_shadow_policy_required")
    runner=BoundedNFLEvaluator(settings["execution_profile"])
    try:
        return nfl.run_shadow_job(journal,job_id,authority_id=authority_id,worker_id=worker_id,max_phases=max_phases,compute=runner)
    except process.ComputeProcessError as error:
        released=runner.release_failed_attempt(journal,job_id,authority_id=authority_id,worker_id=worker_id)
        return {"status":"held","job_id":job_id,"reason_codes":[str(error)],"lease_released":released,**nfl.FALSE_FLAGS}
