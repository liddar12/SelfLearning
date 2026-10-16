"""Independent checks of the test-only stdlib coverage denominator and threads."""
from pathlib import Path
import json
import os
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.measure_critical_coverage import (CRITICAL_FILES, NFL_PRODUCER_FILES,
    CriticalTrace, SuiteResults, executable_lines, summarize_file, suite_sources,
    main, report_name, write_report)


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


def test_v2_and_independent_comparator_are_not_excluded_from_critical_inventory():
    assert len(CRITICAL_FILES) == 38 and len(set(CRITICAL_FILES)) == 38
    for name in ("nfl_capture_application_v2.py", "nfl_capture_application_contracts.py",
                 "nfl_capture_application_journal.py", "nfl_capture_application_job.py",
                 "nfl_capture_applications.py", "compare_nfl_cohort.py", "nfl_review_parity.py",
                 "nfl_review_attachment.py", "owner_intents.py", "owner_intent_journal.py",
                 "supabase_owner_intents.py"):
        assert any(path.endswith("/" + name) for path in CRITICAL_FILES)
    assert "packages/selflearn-core/selflearn_core/owner_intents.py" in CRITICAL_FILES
    assert "services/web/owner_intents.py" in CRITICAL_FILES
    assert "scripts/pinned_cohort_oracle.py" in NFL_PRODUCER_FILES
    assert "scripts/export_capture_application.py" in NFL_PRODUCER_FILES
    assert "scripts/check_learning_parity.py" in NFL_PRODUCER_FILES
    assert "scripts/prop_feature_shadow.py" in NFL_PRODUCER_FILES
    assert "scripts/run_learning_intake.py" in NFL_PRODUCER_FILES
    assert "scripts/export_outcome_capture.py" in NFL_PRODUCER_FILES
    assert "scripts/build_review_parity.py" in NFL_PRODUCER_FILES
    assert "scripts/materialize_calibrator.py" in NFL_PRODUCER_FILES
    assert "scripts/build_review.py" in NFL_PRODUCER_FILES
    assert len(NFL_PRODUCER_FILES) == len(set(NFL_PRODUCER_FILES)) == 28
    for name in ("generation_semantic_gate", "capture_final_participation", "shadow_participation_guard",
                 "build_leg_pool", "build_weekly", "build_predictions", "availability",
                 "validate_data", "excluded_games", "prepublication_semantics", "publication_observations"):
        assert "scripts/" + name + ".py" in NFL_PRODUCER_FILES
    assert "scripts/models/parlay_builder.py" in NFL_PRODUCER_FILES
    assert "scripts/models/my_cards.py" in NFL_PRODUCER_FILES


def test_test_inventory_detects_add_edit_delete_without_hashing_bytecode(tmp_path):
    core = tmp_path / "packages/selflearn-core/tests"
    core.mkdir(parents=True)
    first = core / "test_first.py"
    first.write_text("def test_first(): pass\n", encoding="utf-8")
    original = suite_sources(tmp_path)
    (core / "ignored.pyc").write_bytes(b"compiled")
    assert suite_sources(tmp_path) == original
    first.write_text("def test_first(): assert True\n", encoding="utf-8")
    edited = suite_sources(tmp_path)
    assert edited.keys() == original.keys() and edited != original
    hosted = tmp_path / "tests/hosted"
    hosted.mkdir(parents=True)
    (hosted / "test_new.py").write_text("def test_new(): pass\n", encoding="utf-8")
    added = suite_sources(tmp_path)
    assert len(added) == len(edited) + 1
    first.unlink()
    deleted = suite_sources(tmp_path)
    assert len(deleted) == len(edited) and deleted.keys() != edited.keys()


def test_output_cannot_replace_source_data_test_or_tool_before_measurement(tmp_path):
    for target in ("source.py", "data/injuries.json", "tests/test_example.py",
                   "tools/measure_critical_coverage.py", "docs/architecture/progress.json",
                   "docs/reviews/nested/report.json", "docs/reviews/.hidden.json"):
        with patch("tools.measure_critical_coverage.measure") as measurement:
            with pytest.raises(ValueError, match="direct repository"):
                main(["--root", str(tmp_path), "--nfl-checkout", str(tmp_path),
                      "--output", str(tmp_path / target)])
            measurement.assert_not_called()


def test_report_writer_is_atomic_and_only_updates_direct_json_report(tmp_path):
    output = tmp_path / "docs/reviews/checkpoint.json"
    write_report(tmp_path, output, {"version": 1})
    before = output.stat().st_ino
    write_report(tmp_path, output, {"version": 2})
    assert json.loads(output.read_text()) == {"version": 2}
    assert output.stat().st_ino != before
    assert list(output.parent.iterdir()) == [output]
    assert report_name(tmp_path, output) == "checkpoint.json"


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "directory"])
def test_report_writer_rejects_existing_links_or_directory_and_preserves_target(tmp_path, kind):
    source = tmp_path / "source.py"
    source.write_text("original source\n")
    output = tmp_path / "docs/reviews/checkpoint.json"
    output.parent.mkdir(parents=True)
    if kind == "symlink":
        output.symlink_to(source)
    elif kind == "hardlink":
        os.link(source, output)
    else:
        output.mkdir()
    with pytest.raises(ValueError, match="regular file"):
        write_report(tmp_path, output, {"mutated": True})
    assert source.read_text() == "original source\n"
    assert not list(output.parent.glob(".coverage-*.tmp"))


@pytest.mark.parametrize("component", ["docs", "reviews"])
def test_report_writer_never_follows_parent_symlink(tmp_path, component):
    external = tmp_path / "external"
    external.mkdir()
    if component == "docs":
        (tmp_path / "docs").symlink_to(external, target_is_directory=True)
    else:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs/reviews").symlink_to(external, target_is_directory=True)
    with pytest.raises(OSError):
        write_report(tmp_path, tmp_path / "docs/reviews/checkpoint.json", {"mutated": True})
    assert list(external.iterdir()) == []


def test_report_writer_does_not_remove_an_existing_exclusive_temp_collision(tmp_path):
    output = tmp_path / "docs/reviews/checkpoint.json"
    output.parent.mkdir(parents=True)
    existing = output.parent / ".coverage-collision.tmp"
    existing.write_text("concurrent writer\n")
    with patch("tools.measure_critical_coverage.secrets.token_hex", return_value="collision"):
        with pytest.raises(FileExistsError):
            write_report(tmp_path, output, {"mutated": True})
    assert existing.read_text() == "concurrent writer\n" and not output.exists()


def test_report_writer_preserves_concurrent_report_change_and_cleans_only_own_temp(tmp_path):
    output = tmp_path / "docs/reviews/checkpoint.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"version":1}\n')
    real_fsync = os.fsync
    def concurrent_change(descriptor):
        real_fsync(descriptor)
        output.write_text('{"version":99}\n')
    with patch("tools.measure_critical_coverage.os.fsync", side_effect=concurrent_change):
        with pytest.raises(ValueError, match="changed concurrently"):
            write_report(tmp_path, output, {"version": 2})
    assert json.loads(output.read_text()) == {"version": 99}
    assert list(output.parent.iterdir()) == [output]
