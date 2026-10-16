"""Real trusted processes + adversarial supervisor boundaries, no cloud IO."""
import hashlib
import os
import subprocess
import sys
import time
from unittest.mock import patch

import pytest

from selflearn_core import compute_process as P


def command(script):
    return [sys.executable,"-I","-S","-B","-u","-c",script]


def run(tmp_path, script, payload=b"", **changes):
    return P.run_worker(command(script),payload,cwd=tmp_path,environment={},wall_seconds=5,**changes)


def ready(path):
    deadline=time.monotonic()+5
    while not path.exists():
        if time.monotonic()>deadline:raise AssertionError("fixture startup failed")
        time.sleep(.01)


def test_real_large_bidirectional_pipes_exact_cap_and_no_environment_or_stderr_exposure(tmp_path):
    payload=b"x"*200000
    script="import os,sys;sys.stderr.buffer.write(b'private-fixture');sys.stderr.flush();sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.flush()"
    with patch.dict(os.environ,{"SELFLEARN_OPERATOR_TOKEN":"fixture-must-not-leak"}):
        result=run(tmp_path,script,payload,output_bytes=len(payload),stderr_bytes=15)
        absent=run(tmp_path,"import os,sys;sys.stdout.buffer.write(str('SELFLEARN_OPERATOR_TOKEN' in os.environ).encode())")
    assert result["stdout"]==payload and absent["stdout"]==b"False"
    assert result["stderr_bytes"]==15 and "private-fixture"not in repr(result)
    assert result["input_sha256"]==result["output_sha256"]=="sha256:"+hashlib.sha256(payload).hexdigest()
    assert result["returncode"]==0 and result["process_group_cleanup_requested"] is True and result["elapsed_ns"]>0


@pytest.mark.parametrize("channel,reason",[("stdout","compute_output_limit"),("stderr","compute_stderr_limit")])
def test_real_stream_overflow_is_bounded_and_static(tmp_path,channel,reason):
    script=f"import sys;sys.{channel}.buffer.write(b'private-fixture'*10000);sys.{channel}.flush()"
    with pytest.raises(P.ComputeProcessError,match=f"^{reason}$")as error:
        run(tmp_path,script,output_bytes=32,stderr_bytes=32)
    assert "private-fixture"not in str(error.value)


@pytest.mark.parametrize("script",["import time;time.sleep(10)","import os,time;[os.close(i) for i in (0,1,2)];time.sleep(10)"])
def test_real_hanging_worker_and_hang_after_pipe_eof_are_killed(tmp_path,script):
    begin=time.monotonic()
    with pytest.raises(P.ComputeProcessError,match="^compute_deadline_exceeded$"):
        P.run_worker(command(script),b"",cwd=tmp_path,environment={},wall_seconds=.4)
    assert time.monotonic()-begin<3


def test_real_early_input_refusal_and_nonzero_exit_are_static(tmp_path):
    with pytest.raises(P.ComputeProcessError,match="^compute_stdin_refused$"):
        run(tmp_path,"import os,time;os.close(0);time.sleep(.1)",b"x"*P.MAX_INPUT)
    with pytest.raises(P.ComputeProcessError,match="^compute_worker_refused$"):
        run(tmp_path,"import sys;sys.stdin.buffer.read();sys.stderr.write('private');sys.exit(7)")


@pytest.mark.parametrize("closed",[False,True])
def test_exited_leader_still_cleans_live_descendant_before_return(tmp_path,closed):
    # Prepare the actual exited leader/live descendant BEFORE the tested clock.
    # Startup/readiness has its own five-second fixture budget, never 150ms.
    marker,arm,effect=(tmp_path/name for name in("ready","arm","forbidden-effect"))
    script="""import os,sys,time
from pathlib import Path
pid=os.fork()
if pid:
    os._exit(0)
if sys.argv[4]=='closed':
    [os.close(i) for i in (0,1,2)]
Path(sys.argv[1]).write_text(str(os.getpid()))
deadline=time.monotonic()+10
while not Path(sys.argv[2]).exists() and time.monotonic()<deadline:
    time.sleep(.01)
if Path(sys.argv[2]).exists():Path(sys.argv[3]).write_text('bad')
os._exit(0)
"""
    child=subprocess.Popen(command(script)+[str(marker),str(arm),str(effect),"closed"if closed else"open"],
        cwd=tmp_path,env={},stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        start_new_session=True,close_fds=True,bufsize=0)
    try:
        ready(marker);assert child.wait(timeout=1)==0
        with patch.object(P.subprocess,"Popen",return_value=child):
            if closed:
                result=P.run_worker(command("pass"),b"",cwd=tmp_path,environment={},wall_seconds=.15)
                assert result["stdout"]==b""and result["process_group_cleanup_requested"] is True
            else:
                with pytest.raises(P.ComputeProcessError,match="compute_deadline_exceeded"):
                    P.run_worker(command("pass"),b"",cwd=tmp_path,environment={},wall_seconds=.15)
        arm.write_text("now")
        time.sleep(.2)
        assert not effect.exists()
        assert all(stream.closed for stream in(child.stdin,child.stdout,child.stderr))
    finally:
        P._cleanup(child)


def test_startup_consumes_deadline_and_late_created_child_is_cleaned(tmp_path):
    original=subprocess.Popen;created=[]
    def delayed(*args,**kwargs):
        child=original(*args,**kwargs);created.append(child);time.sleep(.2);return child
    with patch.object(P.subprocess,"Popen",side_effect=delayed):
        with pytest.raises(P.ComputeProcessError,match="compute_deadline_exceeded"):
            P.run_worker(command("import time;time.sleep(10)"),b"",cwd=tmp_path,environment={},wall_seconds=.05)
    assert len(created)==1 and created[0].poll() is not None


@pytest.mark.parametrize("exception",[KeyboardInterrupt,SystemExit])
def test_real_child_is_cleaned_before_cancellation_propagates(tmp_path,exception):
    original=subprocess.Popen;created=[]
    def tracked(*args,**kwargs):
        child=original(*args,**kwargs);created.append(child);return child
    with patch.object(P.subprocess,"Popen",side_effect=tracked),patch.object(P.selectors.DefaultSelector,"select",side_effect=exception):
        with pytest.raises(exception):run(tmp_path,"import time;time.sleep(10)")
    assert created[0].poll() is not None
    assert all(stream.closed for stream in(created[0].stdin,created[0].stdout,created[0].stderr))


@pytest.mark.parametrize("change",[{"command":[]},{"command":[False]},{"environment":{"a":False}},
    {"wall_seconds":True},{"wall_seconds":float("nan")},{"wall_seconds":0},{"wall_seconds":301},
    {"input_bytes":False},{"output_bytes":0},{"stderr_bytes":P.MAX_STDERR+1},{"payload":"no"},
    {"payload":b"xx","input_bytes":1}])
def test_invalid_configuration_refused_before_spawn(tmp_path,change):
    args={"command":command("pass"),"payload":b"","cwd":tmp_path,"environment":{},"wall_seconds":1,**change}
    with patch.object(P.subprocess,"Popen")as start,pytest.raises(P.ComputeProcessError):P.run_worker(**args)
    start.assert_not_called()


def test_unavailable_platform_and_os_errors_are_closed_and_static(tmp_path):
    with patch.object(P,"POSIX",False),pytest.raises(P.ComputeProcessError,match="compute_posix_required"):
        run(tmp_path,"pass")
    with patch.object(P.subprocess,"Popen",side_effect=OSError("private-path")),pytest.raises(P.ComputeProcessError,match="^compute_worker_unavailable$"):
        run(tmp_path,"pass")


@pytest.mark.parametrize("field,value",[("version",True),("kind","other"),("wall_seconds",0),("cpu_seconds",True),
    ("cpu_seconds",301),("input_bytes",P.MAX_INPUT+1),("output_bytes",0),("stderr_bytes",False)])
def test_profile_types_keys_and_budgets_are_pinned(field,value):
    profile=P.profile();profile[field]=value
    with pytest.raises(ValueError):P.validate_profile(profile)


def test_profile_rejects_extra_keys_cpu_over_wall_and_copies_input():
    good=P.profile();assert P.validate_profile(good)==good and P.validate_profile(good) is not good
    for value in ({**good,"extra":True},{**good,"cpu_seconds":31}):
        with pytest.raises(ValueError):P.validate_profile(value)


@pytest.mark.parametrize("value",[None,{"cpu":[1,1],"core":[False,0],"file_size":[0,0]},
    {"cpu":[True,True],"core":[0,0],"file_size":[0,0]},
    {"cpu":[1,2],"core":[0,0],"file_size":[0,0]},
    {"cpu":[2,2],"core":[0,0],"file_size":[0,0]},
    {"cpu":[1,1],"core":[0,0],"file_size":[0.0,0]}])
def test_limit_readback_is_strict_and_does_not_accept_boolean_or_float_zero(value):
    with pytest.raises(ValueError):P.validate_limits(value,1)


def test_cleanup_failure_cannot_be_success_or_mask_cancellation(tmp_path):
    original=P._cleanup
    def fail(child):original(child);return "compute_group_cleanup_failed"
    with patch.object(P,"_cleanup",side_effect=fail):
        with pytest.raises(P.ComputeProcessError,match="^compute_group_cleanup_failed$"):run(tmp_path,"pass")
        with patch.object(P.selectors.DefaultSelector,"select",side_effect=KeyboardInterrupt),pytest.raises(KeyboardInterrupt):
            run(tmp_path,"import time;time.sleep(10)")


def test_cleanup_records_each_failed_operation_without_leaking_private_errors():
    class Stream:
        closed=False
        def close(self):raise OSError("private-stream")
    class Child:
        pid=999999999
        stdin=stdout=stderr=Stream()
        def poll(self):return None
        def kill(self):raise OSError("private-worker")
    with patch.object(P.os,"killpg",side_effect=OSError("private-group")):
        assert P._cleanup(Child())=="compute_pipe_cleanup_failed"


def test_slow_wait_cannot_turn_a_late_exit_into_success(tmp_path):
    original=subprocess.Popen
    def delayed_wait(*args,**kwargs):
        child=original(*args,**kwargs);wait=child.wait
        def slow(*args,**kwargs):
            result=wait(*args,**kwargs);time.sleep(.25);return result
        child.wait=slow;return child
    with patch.object(P.subprocess,"Popen",side_effect=delayed_wait),pytest.raises(P.ComputeProcessError,match="compute_deadline_exceeded"):
        P.run_worker(command("pass"),b"",cwd=tmp_path,environment={},wall_seconds=.2)
