"""Run the handoff regression gate and tooling tests without account credentials.

Outputs only named check results and counts, never captured subprocess output.
This verifies repository tests, not a deployed runtime or real broker connection.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from automation_readiness import CREDENTIAL_GROUPS, ROOT, checkout_sha, worktree_dirty

CHECKS = (
    ("selflearn_core_tests", "packages/selflearn-core", ["-m", "pytest", "-q"]),
    ("walkforward_smoke", "packages/selflearn-core", ["-m", "examples.walkforward_demo"]),
    ("execution_core_tests", "packages/execution-core", ["-m", "pytest", "-q"]),
    ("signals_tests", "packages/signals", ["-m", "pytest", "-q"]),
    ("tooling_tests", ".", ["-m", "pytest", "-q", "tests/tooling"]),
)


def test_environment(source: dict[str, str]) -> dict[str, str]:
    env = dict(source)
    for names in CREDENTIAL_GROUPS.values():
        for name in names:
            env.pop(name, None)
    # Also omit common deployment and broker credentials from test subprocesses.
    for name in tuple(env):
        if any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            env.pop(name, None)
    for name in ("PYTHONPATH", "PYTHONHOME", "PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(name, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_checks(root: Path = ROOT) -> dict:
    env = test_environment(dict(os.environ))
    checks = []
    for name, directory, args in CHECKS:
        try:
            result = subprocess.run(
                [sys.executable, *args], cwd=root / directory, env=env,
                capture_output=True, text=True, timeout=120,
            )
            counts = re.findall(r"(\d+) passed", result.stdout)
            checks.append({
                "name": name,
                "status": "passed" if result.returncode == 0 else "failed",
                "exit_code": result.returncode,
                "tests_passed": int(counts[-1]) if counts else None,
            })
        except (OSError, subprocess.SubprocessError):
            checks.append({"name": name, "status": "failed", "exit_code": None, "tests_passed": None})
    return {
        "schema_version": 1,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "checkout_sha": checkout_sha(root),
        "working_tree_dirty": worktree_dirty(root),
        "scope": "local fixture/unit regression checks only",
        "checks": checks,
        "passed": all(check["status"] == "passed" for check in checks),
        "connected_services_verified": False,
        "production_or_live_eligible": False,
        "details": "Subprocess output is withheld; rerun the named check locally to diagnose a failure.",
    }


def main() -> int:
    report = run_checks()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
