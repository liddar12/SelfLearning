"""Independent checks of the test-only stdlib coverage denominator and threads."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

from tools.measure_critical_coverage import CriticalTrace, SuiteResults, executable_lines, summarize_file


def target(tmp_path):
    path = tmp_path / "target.py"
    path.write_text(
        '"""A docstring is not an invented executable denominator."""\n'
        "def called():\n"
        "    threaded_only = 3\n"
        "    return threaded_only\n"
        "def never_called():\n"
        "    missed = 7  #pragma: NO COVER\n"
        "    return missed\n", encoding="utf-8")
    return path


def test_worker_thread_is_traced_and_uncalled_function_is_not_excluded(tmp_path):
    path = target(tmp_path)
    tracer = CriticalTrace([path])
    context = {"path": str(path), "source": path.read_text(), "threading": threading}
    before_sys, before_thread = sys.gettrace(), threading.gettrace()
    try:
        tracer.runctx("namespace = {}; exec(compile(source, path, 'exec'), namespace); "
                      "worker = threading.Thread(target=namespace['called']); worker.start(); worker.join()", context, context)
        assert sys.gettrace() is None and threading.gettrace() is None
    finally:
        sys.settrace(before_sys)
        threading.settrace(before_thread)
    summary = summarize_file(path, tracer.results().counts, executable_lines(path))
    assert 3 in summary["covered_lines"] and 4 in summary["covered_lines"]
    assert 6 in summary["missing_lines"] and 7 in summary["missing_lines"]
    assert 0 not in summary["covered_lines"] + summary["missing_lines"]
    assert summary["total"] == summary["covered"] + len(summary["missing_lines"])
    assert sys.gettrace() == before_sys
    assert threading.gettrace() == before_thread


def test_unimported_target_has_zero_hits_but_complete_executable_denominator(tmp_path):
    path = target(tmp_path)
    expected = executable_lines(path)
    summary = summarize_file(path, {}, expected)
    assert {2, 3, 4, 5, 6, 7} <= expected
    assert summary["covered"] == 0 and summary["total"] == len(expected)
    assert summary["percent"] == 0
    assert set(summary["missing_lines"]) == expected


def test_observed_physical_lines_are_included_like_stdlib_trace_source_report(tmp_path):
    path = target(tmp_path)
    summary = summarize_file(path, {(str(path), 2): 1, (str(path), 0): 1}, {3, 4})
    assert summary["covered"] == 1 and summary["total"] == 3
    assert summary["additional_observed_lines"] == [2]
    assert summary["missing_lines"] == [3, 4]


def test_runctx_retains_exit_code_in_explicit_globals_and_locals(tmp_path):
    path = target(tmp_path)
    context = {}
    CriticalTrace([path]).runctx("exit_code = 0", context, context)
    assert context["exit_code"] == 0


def test_collection_hook_counts_items_before_pytest_updates_session_total():
    results = SuiteResults()
    results.pytest_collection_finish(SimpleNamespace(testscollected=0, items=[object(), object()]))
    assert results.collected == 2
