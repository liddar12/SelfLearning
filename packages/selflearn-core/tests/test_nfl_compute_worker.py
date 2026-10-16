"""Pure worker contracts plus actual child-only Unix resource enforcement."""
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

from selflearn_core import compute_process as P
from selflearn_core import learning_jobs as J
from selflearn_core import nfl_compute_worker as W
from selflearn_core.adapters import nfl_evaluation_job as N
from selflearn_core.probability_receipts import fingerprint
from test_nfl_evaluation_job import inputs,definition,AUTHORITY


def packet(inputs,**changes):
    settings=N.policy(execution_profile=P.profile())
    return {"version":1,"kind":"nfl.compute_request.v1","authority_id":AUTHORITY,
        "definition":definition(inputs,policy_object_id=fingerprint(settings)),"registered_utc":"2026-10-16T00:00:00Z",
        "inputs":inputs,"policy":settings,"cpu_seconds":30,**changes}


def limits(cpu=30):return {"cpu":[cpu,cpu],"core":[0,0],"file_size":[0,0]}


class Resource:
    RLIMIT_CPU,RLIMIT_CORE,RLIMIT_FSIZE,RLIM_INFINITY=0,1,2,-1
    def __init__(self,hard=-1):self.values={0:(-1,hard),1:(100,100),2:(100,100)};self.calls=[]
    def getrlimit(self,key):return self.values[key]
    def setrlimit(self,key,value):self.calls.append((key,value));self.values[key]=value


@pytest.mark.parametrize("hard,effective",[(-1,30),(100,30),(2,2)])
def test_resource_readback_and_existing_hard_limit_are_respected_without_touching_parent(hard,effective):
    resource=Resource(hard)
    assert W.enforce_limits(30,resource_module=resource)==limits(effective)
    assert resource.calls==[(0,(effective,effective)),(1,(0,0)),(2,(0,0))]


@pytest.mark.parametrize("cpu,hard",[(True,-1),(0,-1),(301,-1),(30,0),(30,False)])
def test_invalid_resource_profile_fails_closed(cpu,hard):
    with pytest.raises(ValueError):W.enforce_limits(cpu,resource_module=Resource(hard))


def test_missing_capabilities_or_failed_readback_refuse_limits():
    with pytest.raises(ValueError):W.enforce_limits(30,resource_module=object())
    resource=Resource()
    with patch.object(resource,"setrlimit"),pytest.raises(ValueError):W.enforce_limits(30,resource_module=resource)
    with patch.object(P,"POSIX",False),pytest.raises(ValueError):W.enforce_limits(30,resource_module=resource)


@pytest.mark.parametrize("raw",[b"",b"x"*(P.MAX_INPUT+1),b'{"a":1,"a":2}',b'{"a":NaN}',b'{"a":Infinity}',b'\xff'])
def test_decode_rejects_oversize_duplicates_nonfinite_and_bad_utf8(raw):
    with pytest.raises(ValueError):W.decode(raw)


def test_compute_runs_actual_fitter_with_pinned_sources_and_limits(inputs):
    value=packet(inputs);response=W.compute(value,limits())
    assert response["kind"]=="nfl.compute_response.v1"and response["report"]["candidate"] is not None
    assert response["request_id"]==fingerprint(value)and response["work_id"]==W.work_id(value["definition"])
    assert response["profile_id"]==fingerprint(value["policy"]["execution_profile"])
    assert response["limits"]==limits()


@pytest.mark.parametrize("fault",["extra","version","kind","authority","namespace","operation","inline_policy","cpu_bool","cpu_high","future","input_hash","policy_hash","bad_limits","code_before","code_after"])
def test_worker_refuses_mismatched_scope_sources_profile_or_changed_code(inputs,fault):
    empty={**inputs,"receipts":[],"source_receipts":[],"revisions":[]};value=packet(empty);readback=limits()
    identity=value["definition"]["code_id"]
    if fault=="extra":value["extra"]=True
    elif fault=="version":value["version"]=True
    elif fault=="kind":value["kind"]="other"
    elif fault=="authority":value["authority_id"]=fingerprint("other")
    elif fault in("namespace","operation"):
        spec=value["definition"]
        changed={**spec,"namespace":{**N.NS,"domain":"other"}}if fault=="namespace"else{**spec,"operation":"other"}
        value["definition"]=J.seal_spec({k:v for k,v in changed.items()if k not in("job_id","definition_id")},authority_id=AUTHORITY)
    elif fault=="inline_policy":value["policy"]=N.policy()
    elif fault=="cpu_bool":value["cpu_seconds"]=True
    elif fault=="cpu_high":value["cpu_seconds"]=31
    elif fault=="future":value["inputs"]["as_of_utc"]="2099-01-01T00:00:00Z"
    elif fault=="input_hash":value["inputs"]["receipts"]=[{}]
    elif fault=="policy_hash":value["policy"]["bootstrap_replicates"]=201
    elif fault=="bad_limits":readback["core"]=[False,0]
    ids=[fingerprint("changed"),identity]if fault=="code_before"else[identity,fingerprint("changed")]if fault=="code_after"else[identity,identity]
    with patch.object(J,"code_identity",side_effect=ids),pytest.raises(ValueError):W.compute(value,readback)


def test_main_uses_limits_before_reading_and_writes_bounded_canonical_response(inputs):
    empty={**inputs,"receipts":[],"source_receipts":[],"revisions":[]};value=packet(empty)
    source=io.BytesIO(json.dumps(value).encode());output,errors=io.BytesIO(),io.BytesIO()
    def enforce(cpu):
        assert source.tell()==0 and cpu==30;return limits()
    # Fake ONLY kernel enforcement in the parent-unit test, never lower its RLIMITs.
    with patch.object(W,"enforce_limits",side_effect=enforce):
        assert W.main(["30"],source=source,output=output,errors=errors)==0
    response=json.loads(output.getvalue());assert response["report"]["candidate"] is None
    assert response["request_id"]==fingerprint(value)and errors.getvalue()==b""
    assert len(output.getvalue())<=value["policy"]["execution_profile"]["output_bytes"]


@pytest.mark.parametrize("fault",["argv","bool_cpu","cpu_mismatch","decode","output_limit","limit_error"])
def test_main_refusal_is_static_and_never_echoes_payload_or_exception(inputs,fault):
    empty={**inputs,"receipts":[],"source_receipts":[],"revisions":[]};value=packet(empty)
    argv=["30"]
    if fault=="argv":argv=["private-secret"]
    elif fault=="bool_cpu":value["cpu_seconds"]=True
    elif fault=="cpu_mismatch":value["cpu_seconds"]=29
    elif fault=="output_limit":value["policy"]["execution_profile"]["output_bytes"]=1;value["definition"]=definition(empty,policy_object_id=fingerprint(value["policy"]))
    raw=b"private-secret"if fault=="decode"else json.dumps(value).encode()
    output,errors=io.BytesIO(),io.BytesIO()
    with patch.object(W,"enforce_limits",side_effect=OSError("private-secret")if fault=="limit_error"else lambda cpu:limits()):
        assert W.main(argv,source=io.BytesIO(raw),output=output,errors=errors)==2
    assert output.getvalue()==b""and errors.getvalue()==b"compute_worker_refused\n"


def test_actual_child_cpu_limit_terminates_busy_loop_before_wall_budget(tmp_path):
    root=str(Path(W.__file__).resolve().parent.parent)
    script="import sys;sys.path.insert(0,sys.argv[1]);from selflearn_core.nfl_compute_worker import enforce_limits;enforce_limits(1)\nwhile True:pass"
    with pytest.raises(P.ComputeProcessError,match="^compute_worker_refused$"):
        P.run_worker([sys.executable,"-I","-S","-B","-u","-c",script,root],b"",cwd=tmp_path,environment={},wall_seconds=8)


def test_actual_child_core_and_regular_file_growth_limits(tmp_path):
    root=str(Path(W.__file__).resolve().parent.parent);target=tmp_path/"child-owned-empty-file"
    script="""import sys,json,os
sys.path.insert(0,sys.argv[1])
from selflearn_core.nfl_compute_worker import enforce_limits
limits=enforce_limits(3)
try:
    with open(sys.argv[2],'wb') as f:f.write(b'x'*100)
except OSError:pass
print(json.dumps({'limits':limits,'size':os.stat(sys.argv[2]).st_size}))
"""
    result=P.run_worker([sys.executable,"-I","-S","-B","-u","-c",script,root,str(target)],b"",
        cwd=tmp_path,environment={},wall_seconds=5)
    response=json.loads(result["stdout"])
    assert response["limits"]==limits(3)and response["size"]==0 and target.stat().st_size==0
