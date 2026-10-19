"""Independent checks of the test-only stdlib coverage denominator and threads."""
from pathlib import Path
import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tools.measure_critical_coverage import (CRITICAL_FILES, NFL_PRODUCER_FILES,
    CriticalTrace, SuiteResults, executable_lines, summarize_file, suite_sources,
    main, measure, report_name, write_report, source_environment)


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
    assert len(CRITICAL_FILES) == 49 and len(set(CRITICAL_FILES)) == 49
    for name in ("nfl_capture_application_v2.py", "nfl_capture_application_contracts.py",
                 "nfl_capture_application_journal.py", "nfl_capture_application_job.py",
                 "nfl_capture_applications.py", "compare_nfl_cohort.py", "nfl_review_parity.py",
                 "nfl_review_attachment.py", "owner_intents.py", "owner_intent_journal.py",
                 "supabase_owner_intents.py", "nfl_owner_evidence.py", "learning_jobs.py",
                 "learning_job_journal.py", "supabase_learning_jobs.py", "nfl_evaluation_job.py",
                 "compute_process.py", "nfl_compute_worker.py", "nfl_bounded_evaluation.py",
                 "nfl_generation.py", "nfl_file_feedback.py", "file_owner_intents.py"):
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
    assert len(NFL_PRODUCER_FILES) == len(set(NFL_PRODUCER_FILES)) == 82
    for path in ('scripts/my_freshness.py', 'scripts/period_publication_gate.py',
                 'scripts/run_my_generation.py', 'app/views/mybets.js',
                 '.github/workflows/daily.yml', '.github/workflows/gameday.yml', '.github/workflows/ci.yml',
                 '.github/workflows/data-ci.yml', 'scripts/pilot_config.py', 'scripts/publish_my_generation.py',
                 'config/selflearning_pilot.json', 'scripts/selected_my_publication_gate.py',
                 'scripts/selected_legacy_bootstrap.py'):
        assert path in NFL_PRODUCER_FILES
    for path in ("scripts/learning_history_guard.py", "app/pool-learning-status.js",
                 "app/views/myparlays.js", "app/theme.css", "data/contracts/leg_pool.schema.json",
                 "scripts/period_operator.py", "scripts/period_my_generation.py", "scripts/period_my_commit_replay.py",
                 "scripts/period_source_eligibility.py", "scripts/scrape/espn.py", "scripts/period_freshness_policy.py"):
        assert path in NFL_PRODUCER_FILES
    for name in ("generation_semantic_gate", "capture_final_participation", "shadow_participation_guard",
                 "build_leg_pool", "build_weekly", "build_predictions", "availability",
                 "validate_data", "excluded_games", "prepublication_semantics", "publication_observations",
                 "generation_capture_runtime", "run_parlay_feedback", "feedback_commit_guard",
                 "shared_feedback_status", "build_learning_loops", "period_calibrator", "period_commit_replay"):
        assert "scripts/" + name + ".py" in NFL_PRODUCER_FILES
    assert "scripts/models/parlay_builder.py" in NFL_PRODUCER_FILES
    assert "scripts/models/my_cards.py" in NFL_PRODUCER_FILES
    assert "app/views/model.js" in NFL_PRODUCER_FILES
    assert "scripts/publish_data.sh" in NFL_PRODUCER_FILES


def test_browser_built_card_freshness_and_resume_sources_are_fingerprinted_not_python_coverage():
    # Browser serving safety belongs in the source-stability boundary, without
    # pretending these JavaScript files contribute to Python executable lines.
    for path in ("app/main.js", "app/views/parlays.js", "app/bet-types.js",
                 "app/built-card-policy.js"):
        assert path in NFL_PRODUCER_FILES
        assert path not in CRITICAL_FILES
    assert len(CRITICAL_FILES) == len(set(CRITICAL_FILES)) == 49


def test_sunday_weather_replay_and_venue_source_boundary_is_not_silently_omitted():
    # These sources now decide which forecasts/venues can affect a served leg.
    # Fingerprinting is stability evidence, not Python coverage of NFL code or
    # proof of a fresh provider response or successful browser interaction.
    for path in ("scripts/weekly_replay.py", "scripts/weather_source_contract.py",
                 "scripts/build_weather_forecast.py", "scripts/build_weather_history.py",
                 "scripts/weather_corpus.py", "scripts/backtest_weather.py",
                 "scripts/signals/weather.py", "scripts/promote_signals.py",
                 "scripts/scrape/stadiums.py", "scripts/scrape/renames.py", "scripts/injury_duration.py",
                 "app/render.js", "app/views/slate.js",
                 "data/contracts/player_weekly.schema.json",
                 "data/contracts/weather_forecast.schema.json",
                 "data/contracts/weather_history.schema.json",
                 "data/contracts/weather_backtest.schema.json"):
        assert path in NFL_PRODUCER_FILES
        assert path not in CRITICAL_FILES
    assert len(CRITICAL_FILES) == len(set(CRITICAL_FILES)) == 49


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


@pytest.mark.parametrize("initial", [None, "", "relative-dependency"])
def test_python_child_imports_selected_checkout_not_inherited_old_package(tmp_path, monkeypatch, initial):
    root = tmp_path / "selected"
    package = root / "packages/selflearn-core/selflearn_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('SOURCE = "selected"\n', encoding="utf-8")
    old = tmp_path / "old"
    (old / "selflearn_core").mkdir(parents=True)
    (old / "selflearn_core/__init__.py").write_text('SOURCE = "old"\n', encoding="utf-8")
    nfl = tmp_path / "nfl"
    nfl.mkdir()
    if initial is None:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    else:
        monkeypatch.setenv("PYTHONPATH", initial)
    monkeypatch.setenv("NFL2026_CHECKOUT", "prior-nfl")
    before_cwd, before_path = Path.cwd(), sys.path[:]
    before_env = {key: os.environ.get(key) for key in ("PYTHONPATH", "NFL2026_CHECKOUT")}
    with source_environment(root, nfl) as prefixes:
        assert prefixes == (str(root), str(package.parent))
        assert sys.path[:2] == list(prefixes)
        assert os.environ["NFL2026_CHECKOUT"] == str(nfl)
        inherited = os.environ["PYTHONPATH"]
        assert inherited.split(os.pathsep)[:2] == list(prefixes)
        # A real fresh child runs in NFL, where the parent's sys.path edit has
        # no effect. An old dependency path follows the selected source paths.
        child_env = {**os.environ, "PYTHONPATH": inherited + os.pathsep + str(old)}
        child = subprocess.run([sys.executable, "-c", "import json,selflearn_core; "
            "print(json.dumps([selflearn_core.SOURCE,selflearn_core.__file__]))"],
            cwd=nfl, env=child_env, text=True, capture_output=True, timeout=10, check=True)
        source, origin = json.loads(child.stdout)
        assert source == "selected" and Path(origin) == package / "__init__.py"
        if initial is not None:
            assert str(before_cwd / initial) in inherited.split(os.pathsep)
    assert Path.cwd() == before_cwd and sys.path == before_path
    assert {key: os.environ.get(key) for key in before_env} == before_env


def test_source_context_restores_absent_environment_and_nested_error(tmp_path, monkeypatch):
    root, nested, nfl = tmp_path / "root", tmp_path / "nested", tmp_path / "nfl"
    for directory in (root, nested, nfl):
        directory.mkdir()
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("NFL2026_CHECKOUT", raising=False)
    before_cwd, before_path = Path.cwd(), sys.path[:]
    with pytest.raises(RuntimeError, match="test collection failed"):
        with source_environment(root, nfl):
            outer_env = {key: os.environ[key] for key in ("PYTHONPATH", "NFL2026_CHECKOUT")}
            outer_path = sys.path[:]
            with pytest.raises(LookupError):
                with source_environment(nested, nfl):
                    assert Path.cwd() == nested
                    raise LookupError("nested failure")
            assert Path.cwd() == root and sys.path == outer_path
            assert {key: os.environ[key] for key in outer_env} == outer_env
            raise RuntimeError("test collection failed")
    assert Path.cwd() == before_cwd and sys.path == before_path
    assert "PYTHONPATH" not in os.environ and "NFL2026_CHECKOUT" not in os.environ


@pytest.mark.parametrize("module", ["selflearn_core", "selflearn_core.adapters.nfl_generation",
                                    "services.web", "services.web.application"])
def test_measure_refuses_cached_modules_from_another_checkout_before_tests(tmp_path, module):
    root, nfl = tmp_path / "selected", tmp_path / "nfl"
    root.mkdir(); nfl.mkdir()
    (root / "target.py").write_text("VALUE = 1\n", encoding="utf-8")
    stale = SimpleNamespace(__file__=str(tmp_path / "old/module.py"))
    with patch("tools.measure_critical_coverage.CRITICAL_FILES", ("target.py",)), \
         patch("tools.measure_critical_coverage.NFL_PRODUCER_FILES", ()), \
         patch.dict(sys.modules, {module: stale}), \
         patch("tools.measure_critical_coverage.CriticalTrace") as tracer:
        with pytest.raises(ValueError, match="another checkout"):
            measure(root, nfl)
        tracer.assert_not_called()


def test_source_context_restores_caller_when_selected_root_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "existing-relative")
    monkeypatch.setenv("NFL2026_CHECKOUT", "existing-nfl")
    before = (Path.cwd(), sys.path[:], os.environ["PYTHONPATH"], os.environ["NFL2026_CHECKOUT"])
    with pytest.raises(FileNotFoundError):
        with source_environment(tmp_path / "missing", tmp_path):
            pytest.fail("A missing selected root must not run tests")
    assert (Path.cwd(), sys.path[:], os.environ["PYTHONPATH"], os.environ["NFL2026_CHECKOUT"]) == before
