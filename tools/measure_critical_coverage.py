#!/usr/bin/env python3
"""Measure critical Python line coverage using the existing pytest + stdlib trace.

This is test-only tooling: no runtime package, cloud call, dependency installation,
model activation or data regeneration. The default run includes the complete
selflearn-core and hosted suites, with the real NFL checkout required explicitly.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
import trace
from zoneinfo import ZoneInfo


CRITICAL_FILES = (
    "packages/selflearn-core/selflearn_core/evidence_chunks.py",
    "packages/selflearn-core/selflearn_core/store/receipt_journal.py",
    "packages/selflearn-core/selflearn_core/store/supabase_journal.py",
    "packages/selflearn-core/selflearn_core/store/supabase_evidence.py",
    "packages/selflearn-core/selflearn_core/store/capture_staging.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_observed_journal.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_resolution.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_observed_resolution.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_observed_job.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_evidence.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_labels.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_job.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_delivery_journal.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_delivery_job.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_capture_application.py",
    "packages/selflearn-core/selflearn_core/outcome_revisions.py",
    "packages/selflearn-core/selflearn_core/adapters/nfl_calibration.py",
    "services/web/nfl_ingestion.py",
    "services/web/private_uploads.py",
    "services/web/nfl_trace.py",
    "services/web/nfl_capture_intake.py",
    "services/web/security.py",
    "services/web/application.py",
    "packages/selflearn-core/selflearn_core/store/supabase_store.py",
)
SUITES = ("packages/selflearn-core/tests", "tests/hosted")
NFL_PRODUCER_FILES = ("scripts/learning_journal.py", "scripts/learning_receipts.py",
                      "scripts/build_my_cards.py", "scripts/export_learning.py",
                      "scripts/capture_outcome_evidence.py", "scripts/build_learning_outcomes.py")


class CriticalTrace(trace.Trace):
    """Count only target files, retaining Trace.runctx's new-thread tracing."""

    def __init__(self, paths):
        self.targets = {str(Path(path).resolve()) for path in paths}
        self._paths = {}
        super().__init__(count=True, trace=False)

    def globaltrace_lt(self, frame, why, arg):
        if why != "call":
            return None
        filename = frame.f_code.co_filename
        if filename not in self._paths:
            self._paths[filename] = (None if filename.startswith("<")
                                     else str(Path(filename).resolve()))
        return self.localtrace if self._paths[filename] in self.targets else None


def executable_lines(path):
    """Use trace's recursive bytecode line table, without pragma exclusions.

CPython may expose a synthetic module RESUME location 0. It is not a physical
source line and is likewise absent from trace's own 1-based source report.
"""
    return {line for line in trace._find_executable_linenos(str(path)) if line > 0}


def summarize_file(path, counts, executable):
    key = str(Path(path).resolve())
    observed = set()
    for (filename, line), count in counts.items():
        if count > 0 and line > 0 and str(Path(filename).resolve()) == key:
            observed.add(line)
    # Like stdlib trace's source report, actual line events also count. Unlike
    # that report, no '#pragma: NO COVER' can remove a missed executable line.
    denominator = set(executable) | observed
    covered = denominator & observed
    return {"covered": len(covered), "total": len(denominator),
            "percent": round(100 * len(covered) / len(denominator), 4) if denominator else None,
            "covered_lines": sorted(covered), "missing_lines": sorted(denominator - covered),
            "enumerated_executable_lines": len(executable),
            "additional_observed_lines": sorted(observed - set(executable))}


class SuiteResults:
    def __init__(self):
        self.collected = 0
        self.outcomes = Counter()

    def pytest_collection_finish(self, session):
        # pytest assigns session.testscollected only after this hook returns.
        # The authoritative collected items already exist at this boundary.
        self.collected = len(session.items)

    def pytest_runtest_logreport(self, report):
        if report.when == "call":
            self.outcomes["xfailed" if report.skipped and hasattr(report, "wasxfail")
                          else "xpassed" if report.passed and hasattr(report, "wasxfail")
                          else report.outcome] += 1
        elif report.failed:
            self.outcomes["errors"] += 1
        elif report.skipped and report.when == "setup":
            self.outcomes["xfailed" if hasattr(report, "wasxfail") else "skipped"] += 1

    def pytest_collectreport(self, report):
        if report.failed:
            self.outcomes["collection_errors"] += 1


def _hash(path):
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _head(path):
    run = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=False)
    return run.stdout.strip() if run.returncode == 0 else None


def measure(root, nfl_checkout):
    root, nfl_checkout = Path(root).resolve(), Path(nfl_checkout).resolve()
    if any(not (nfl_checkout / name).is_file() for name in NFL_PRODUCER_FILES):
        raise ValueError("NFL checkout with the real learning journal and exporter is required")
    paths = [root / name for name in CRITICAL_FILES]
    if any(not path.is_file() for path in paths):
        raise ValueError("Every critical target must exist; targets cannot be silently excluded")
    target_names = {str(path) for path in paths}
    already_imported = [name for name, module in tuple(sys.modules.items())
                        if getattr(module, "__file__", None)
                        and str(Path(module.__file__).resolve()) in target_names]
    if already_imported:
        raise ValueError("Run in a fresh interpreter; critical targets are already imported")
    before = {name: _hash(root / name) for name in CRITICAL_FILES}
    nfl_before = {name: _hash(nfl_checkout / name) for name in NFL_PRODUCER_FILES}
    line_tables = {name: executable_lines(root / name) for name in CRITICAL_FILES}
    repo_head = _head(root)
    nfl_head = _head(nfl_checkout)
    old_cwd = Path.cwd()
    previous_checkout = os.environ.get("NFL2026_CHECKOUT")
    old_path = sys.path[:]
    old_sys_trace, old_thread_trace = sys.gettrace(), threading.gettrace()
    os.environ["NFL2026_CHECKOUT"] = str(nfl_checkout)
    sys.path[:0] = [str(root), str(root / "packages/selflearn-core")]
    os.chdir(root)
    plugin = SuiteResults()
    start = time.monotonic()
    tracer = CriticalTrace(paths)
    try:
        import pytest  # existing dev dependency, never installed by this tool
        context = {"pytest": pytest, "args": ["-q", *SUITES], "plugin": plugin}
        tracer.runctx("exit_code = int(pytest.main(args, plugins=[plugin]))", context, context)
    finally:
        # Trace.runctx intentionally clears these hooks rather than restoring
        # them. A caller's debugger/instrumentation must not be left disabled.
        sys.settrace(old_sys_trace)
        threading.settrace(old_thread_trace)
        os.chdir(old_cwd)
        sys.path[:] = old_path
        if previous_checkout is None:
            os.environ.pop("NFL2026_CHECKOUT", None)
        else:
            os.environ["NFL2026_CHECKOUT"] = previous_checkout
    after = {name: _hash(root / name) for name in CRITICAL_FILES}
    nfl_after = {name: _hash(nfl_checkout / name) for name in NFL_PRODUCER_FILES}
    counts = tracer.results().counts
    rows = []
    for name in CRITICAL_FILES:
        rows.append({"path": name, "source_sha256_before": before[name],
                     "source_sha256_after": after[name],
                     **summarize_file(root / name, counts, line_tables[name])})
    covered, total = sum(row["covered"] for row in rows), sum(row["total"] for row in rows)
    now = datetime.now(timezone.utc)
    return {
        "version": 1, "metric": "Python executable line coverage; not branch coverage",
        "tool": "stdlib trace.Trace.runctx; trace._find_executable_linenos",
        "tool_source_sha256": _hash(Path(__file__)),
        "python": platform.python_version(), "generated_utc": now.isoformat(),
        "generated_chicago": now.astimezone(ZoneInfo("America/Chicago")).isoformat(),
        "repository_head": repo_head, "root": str(root), "nfl_checkout": str(nfl_checkout),
        "nfl_head": nfl_head, "nfl_integration_enabled": True,
        "nfl_sources": [{"path": name, "source_sha256_before": nfl_before[name],
                         "source_sha256_after": nfl_after[name]} for name in NFL_PRODUCER_FILES],
        "pytest_args": ["-q", *SUITES], "pytest_exit_code": context["exit_code"],
        "tests_collected": plugin.collected, "test_outcomes": dict(sorted(plugin.outcomes.items())),
        "elapsed_seconds": round(time.monotonic() - start, 3),
        "source_stable": (before == after and nfl_before == nfl_after
                          and repo_head == _head(root) and nfl_head == _head(nfl_checkout)),
        "covered": covered, "total": total,
        "percent": round(100 * covered / total, 4) if total else None,
        "files": rows,
        "limitations": [
            "Line coverage is not branch, acceptance-criterion, statistical or production verification.",
            "Includes every enumerated executable target line, even in uncalled functions; no pragma exclusions.",
            "Synthetic line 0 is not a physical source line; observed physical line events are also included.",
            "Trace.runctx traces the current thread and threads created during the run, not pre-existing threads.",
            "Child-process execution is not traced. These measured suites use fake Supabase transports, not a live database round trip.",
            "The separate PostgreSQL SQL/adapter/native suites are not included in this Python line-coverage run.",
            "Per-file source hashes identify dirty local code; a commit SHA alone does not describe this measurement.",
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--nfl-checkout", default=os.environ.get("NFL2026_CHECKOUT"))
    parser.add_argument("--output", help="optional generated evidence JSON path")
    args = parser.parse_args(argv)
    if not args.nfl_checkout:
        parser.error("--nfl-checkout or NFL2026_CHECKOUT is required; cross-repo tests must not silently skip")
    result = measure(args.root, args.nfl_checkout)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for row in result["files"]:
        print(f"{row['covered']}/{row['total']} ({row['percent']}%) {row['path']}")
    print(f"TOTAL {result['covered']}/{result['total']} ({result['percent']}%) executable lines; "
          f"pytest={result['pytest_exit_code']}; source_stable={result['source_stable']}")
    return 0 if result["pytest_exit_code"] == 0 and result["source_stable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
