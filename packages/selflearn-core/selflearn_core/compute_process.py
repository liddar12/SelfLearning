"""Bounded POSIX pipes/process group for explicitly trusted worker commands.

No shell, command discovery, environment inheritance, database, network or IO on
import. This is NOT a hostile-code sandbox: a deliberately escaping descendant
or privileged process is outside its contract. Fixed domain workers must never
execute uploaded code. Startup consumes the deadline, but OS process creation
itself cannot always be interrupted; a late-created worker is killed before
accepting a result. Limits apply to computation, not parent database calls.
"""
import hashlib
import math
import os
import selectors
import signal
import subprocess
import sys
import time

from . import learning_jobs as jobs

POSIX = os.name == "posix" and hasattr(os,"killpg")
MAX_INPUT, MAX_OUTPUT, MAX_STDERR = 4 * 1024 * 1024, 3 * 1024 * 1024, 16384
PROFILE_KEYS = {"version","kind","wall_seconds","cpu_seconds","input_bytes","output_bytes","stderr_bytes"}


class ComputeProcessError(ValueError):
    """Static reason only; raw output, argv, environment and stderr stay private."""


def profile(*, wall_seconds=30, cpu_seconds=30, input_bytes=MAX_INPUT, output_bytes=MAX_OUTPUT, stderr_bytes=MAX_STDERR):
    return validate_profile({"version":1,"kind":"selflearn.posix_compute_profile.v1","wall_seconds":wall_seconds,
        "cpu_seconds":cpu_seconds,"input_bytes":input_bytes,"output_bytes":output_bytes,"stderr_bytes":stderr_bytes})


def validate_profile(value):
    value = jobs.snapshot(value)
    jobs.need(type(value) is dict and set(value)==PROFILE_KEYS and type(value["version"]) is int
        and value["version"]==1 and value["kind"]=="selflearn.posix_compute_profile.v1","invalid_compute_profile")
    for key, maximum in (("wall_seconds",300),("cpu_seconds",300),("input_bytes",MAX_INPUT),("output_bytes",MAX_OUTPUT),("stderr_bytes",MAX_STDERR)):
        jobs.need(type(value[key]) is int and 1<=value[key]<=maximum,"invalid_compute_profile")
    jobs.need(value["cpu_seconds"]<=value["wall_seconds"],"invalid_compute_profile")
    return value


def validate_limits(value, cpu_seconds):
    """Strict typed readback; JSON false/0.0 cannot stand in for integer zero."""
    value=jobs.snapshot(value)
    jobs.need(type(cpu_seconds) is int and 1<=cpu_seconds<=300,"compute_limits_unavailable")
    jobs.need(type(value) is dict and set(value)=={"cpu","core","file_size"},"compute_limits_unavailable")
    for key in ("cpu","core","file_size"):
        jobs.need(type(value[key]) is list and len(value[key])==2 and all(type(x) is int for x in value[key]),"compute_limits_unavailable")
    jobs.need(1<=value["cpu"][0]<=cpu_seconds and value["cpu"][0]==value["cpu"][1]
        and value["core"]==[0,0]and value["file_size"]==[0,0],"compute_limits_unavailable")
    return value


def _need(condition, reason):
    if not condition:raise ComputeProcessError(reason)


def _cleanup(process):
    issue = None
    try:os.killpg(process.pid,signal.SIGKILL)
    except ProcessLookupError:pass
    except OSError:issue = "compute_group_cleanup_failed"
    try:
        if process.poll() is None:process.kill()
        process.wait(timeout=1)
    except (OSError,subprocess.SubprocessError):issue = "compute_worker_cleanup_failed"
    for stream in (process.stdin,process.stdout,process.stderr):
        try:
            if stream is not None and not stream.closed:stream.close()
        except OSError:issue = "compute_pipe_cleanup_failed"
    return issue


def run_worker(command, payload, *, cwd, environment, wall_seconds, input_bytes=MAX_INPUT,
               output_bytes=MAX_OUTPUT, stderr_bytes=MAX_STDERR):
    """Run one owned group; drain/write incrementally within explicit bounds.

    The caller owns the command/environment as trusted program configuration.
    Stderr is charged but never retained/returned, even on success. Cancellation
    propagates after cleanup; cleanup failure cannot become a normal success.
    """
    _need(POSIX,"compute_posix_required")
    _need(type(command) in (list,tuple) and 0<len(command)<=32 and all(type(x) is str and x and len(x)<=8192 for x in command),"invalid_compute_command")
    _need(type(environment) is dict and all(type(k) is str and type(v) is str for k,v in environment.items()),"invalid_compute_environment")
    _need(type(wall_seconds) in (int,float) and math.isfinite(wall_seconds) and 0<wall_seconds<=300,"invalid_compute_wall_budget")
    for value, maximum in ((input_bytes,MAX_INPUT),(output_bytes,MAX_OUTPUT),(stderr_bytes,MAX_STDERR)):
        _need(type(value) is int and 0<value<=maximum,"invalid_compute_pipe_budget")
    _need(type(payload) is bytes and len(payload)<=input_bytes,"compute_input_limit")
    started = time.monotonic_ns();deadline = time.monotonic()+wall_seconds;process = None
    output, offset, errors = bytearray(), 0, 0
    try:
        process = subprocess.Popen(command,cwd=cwd,env=dict(environment),stdin=subprocess.PIPE,stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,start_new_session=True,close_fds=True,bufsize=0)
        with selectors.DefaultSelector() as selected:
            for stream,mode in ((process.stdin,selectors.EVENT_WRITE),(process.stdout,selectors.EVENT_READ),(process.stderr,selectors.EVENT_READ)):
                os.set_blocking(stream.fileno(),False);selected.register(stream,mode)
            while selected.get_map():
                remaining = deadline-time.monotonic();_need(remaining>0,"compute_deadline_exceeded")
                for key,_mask in selected.select(min(remaining,.1)):
                    stream = key.fileobj
                    try:
                        if stream is process.stdin:
                            if offset==len(payload):selected.unregister(stream);stream.close()
                            else:offset+=os.write(key.fd,payload[offset:offset+65536])
                        else:
                            maximum,used = (output_bytes,len(output)) if stream is process.stdout else (stderr_bytes,errors)
                            raw = os.read(key.fd,min(65536,maximum-used+1))
                            if not raw:selected.unregister(stream)
                            elif stream is process.stdout:
                                output.extend(raw);_need(len(output)<=output_bytes,"compute_output_limit")
                            else:
                                errors+=len(raw);_need(errors<=stderr_bytes,"compute_stderr_limit")
                    except (BlockingIOError,InterruptedError):continue
                    except BrokenPipeError:raise ComputeProcessError("compute_stdin_refused") from None
            remaining = deadline-time.monotonic();_need(remaining>0,"compute_deadline_exceeded")
            code = process.wait(timeout=remaining)
            _need(time.monotonic()<=deadline,"compute_deadline_exceeded")
            _need(offset==len(payload),"compute_input_incomplete")
            _need(code==0,"compute_worker_refused")
    except ComputeProcessError:
        raise
    except subprocess.TimeoutExpired:
        raise ComputeProcessError("compute_deadline_exceeded") from None
    except (OSError,ValueError,subprocess.SubprocessError):
        raise ComputeProcessError("compute_worker_unavailable") from None
    finally:
        active_exception = sys.exc_info()[0] is not None
        issue = _cleanup(process) if process is not None else None
        if issue and not active_exception:raise ComputeProcessError(issue)
    return {"stdout":bytes(output),"elapsed_ns":time.monotonic_ns()-started,"stderr_bytes":errors,"returncode":0,
        "input_sha256":"sha256:"+hashlib.sha256(payload).hexdigest(),
        "output_sha256":"sha256:"+hashlib.sha256(output).hexdigest(),"process_group_cleanup_requested":True}
