"""Fixed pure NFL compute child. No credentials, journal, scheduler or writes.

The parent uses isolated Python and a fresh owned process group. CPU, core-dump
and regular-file-growth limits are set only in this child, never in its parent.
This is trusted numerical code, not a sandbox for uploaded scripts or plugins.
"""
import json
import sys

from . import learning_jobs as jobs
from . import compute_process as process
from .adapters import nfl_evaluation_job as nfl
from .adapters import nfl_owner_evidence as evidence
from .adapters import nfl_calibration as calibration
from .probability_receipts import fingerprint


def enforce_limits(cpu_seconds, *, resource_module=None):
    jobs.need(process.POSIX and type(cpu_seconds) is int and 1<=cpu_seconds<=300,"compute_limits_unavailable")
    if resource_module is None:
        import resource as resource_module
    required=("RLIMIT_CPU","RLIMIT_CORE","RLIMIT_FSIZE","RLIM_INFINITY","setrlimit","getrlimit")
    jobs.need(all(hasattr(resource_module,k)for k in required),"compute_limits_unavailable")
    r=resource_module
    _soft,hard=r.getrlimit(r.RLIMIT_CPU)
    effective=cpu_seconds if hard==r.RLIM_INFINITY else min(cpu_seconds,hard)
    jobs.need(type(effective) is int and 1<=effective<=cpu_seconds,"compute_limits_unavailable")
    r.setrlimit(r.RLIMIT_CPU,(effective,effective))
    r.setrlimit(r.RLIMIT_CORE,(0,0));r.setrlimit(r.RLIMIT_FSIZE,(0,0))
    values={name:list(r.getrlimit(getattr(r,key)))for name,key in
        (("cpu","RLIMIT_CPU"),("core","RLIMIT_CORE"),("file_size","RLIMIT_FSIZE"))}
    jobs.need(values=={"cpu":[effective,effective],"core":[0,0],"file_size":[0,0]},"compute_limits_unavailable")
    return process.validate_limits(values,cpu_seconds)


def decode(raw):
    jobs.need(type(raw) is bytes and 0<len(raw)<=process.MAX_INPUT,"compute_request_limit")
    def pairs(items):
        value={}
        for key,item in items:
            jobs.need(key not in value,"compute_duplicate_key");value[key]=item
        return value
    def reject(_):raise ValueError("invalid_compute_number")
    return evidence._copy(json.loads(raw.decode("utf-8"),object_pairs_hook=pairs,parse_constant=reject))


def work_id(spec):
    return fingerprint({"version":1,"kind":"selflearn.compute_scope.v1",
        **{k:spec[k]for k in("job_id","definition_id","input_object_id","policy_object_id","code_id")}})


def compute(packet, limits):
    """Pure deterministic handler; resource application belongs to main()."""
    packet=evidence._copy(packet)
    jobs.need(type(packet) is dict and set(packet)=={"version","kind","authority_id","definition","registered_utc","inputs","policy","cpu_seconds"})
    jobs.need(type(packet["version"]) is int and packet["version"]==1 and packet["kind"]=="nfl.compute_request.v1")
    spec=jobs.validate_spec(packet["definition"],authority_id=packet["authority_id"])
    jobs.need(spec["namespace"]==nfl.NS and spec["operation"]==nfl.OPERATION)
    settings=packet["policy"];nfl._policy(settings)
    jobs.need(settings["version"]==2 and type(packet["cpu_seconds"]) is int
        and 1<=packet["cpu_seconds"]<=settings["execution_profile"]["cpu_seconds"])
    limits=process.validate_limits(limits,packet["cpu_seconds"])
    inputs=packet["inputs"];nfl._inputs(inputs,packet["registered_utc"])
    jobs.need(fingerprint(inputs)==spec["input_object_id"]and fingerprint(settings)==spec["policy_object_id"],"compute_source_mismatch")
    jobs.need(jobs.code_identity()==spec["code_id"],"compute_source_changed")
    report=calibration.walk_forward_report(inputs["receipts"],inputs["source_receipts"],inputs["revisions"],
        as_of_utc=inputs["as_of_utc"],population=settings["population"],bootstrap_replicates=settings["bootstrap_replicates"],
        generation_records=inputs["generation_records"])
    report=evidence._copy(json.loads(json.dumps(report,allow_nan=False)))
    jobs.need(jobs.code_identity()==spec["code_id"],"compute_source_changed")
    return {"version":1,"kind":"nfl.compute_response.v1","request_id":fingerprint(packet),"work_id":work_id(spec),
        "profile_id":fingerprint(settings["execution_profile"]),"code_id":spec["code_id"],"report":report,"limits":limits}


def main(argv=None, *, source=None, output=None, errors=None):
    argv=sys.argv[1:]if argv is None else argv
    source=sys.stdin.buffer if source is None else source
    output=sys.stdout.buffer if output is None else output
    errors=sys.stderr.buffer if errors is None else errors
    try:
        jobs.need(type(argv) is list and len(argv)==1 and type(argv[0]) is str and argv[0].isascii()and argv[0].isdigit())
        cpu=int(argv[0]);limits=enforce_limits(cpu)
        packet=decode(source.read(process.MAX_INPUT+1))
        jobs.need(packet.get("cpu_seconds")==cpu and type(packet.get("cpu_seconds")) is int)
        result=compute(packet,limits)
        raw=json.dumps(result,sort_keys=True,separators=(",",":"),ensure_ascii=True,allow_nan=False).encode()
        maximum=packet["policy"]["execution_profile"]["output_bytes"]
        jobs.need(len(raw)<=maximum,"compute_response_limit")
        output.write(raw);output.flush();return 0
    except Exception:
        errors.write(b'compute_worker_refused\n');errors.flush();return 2


if __name__=="__main__":raise SystemExit(main())
