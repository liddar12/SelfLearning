"""Prepare a repository-local development environment, never a broker session.

Without --install, print the plan only. --install explicitly permits package
downloads. No .env files are sourced, and captured installer output is not
printed because index URLs or subprocess errors can contain credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("selflearn-core", "execution-core", "signals")


def install(root: Path = ROOT, *, infrastructure: bool = False) -> int:
    if sys.version_info < (3, 11):
        print("BOOTSTRAP_PYTHON_311_REQUIRED")
        return 2
    infra_root = root / "tools" / "automation" / "infrastructure"
    if infrastructure:
        if not all((infra_root / name).is_file() for name in (
            "package.json", "package-lock.json", "verify-tools.mjs",
        )):
            print("BOOTSTRAP_INFRASTRUCTURE_MANIFESTS_MISSING")
            return 2
        if shutil.which("node") is None or shutil.which("npm") is None:
            print("BOOTSTRAP_NODE_AND_NPM_REQUIRED")
            return 2
    destination = root / ".venv"
    if destination.is_symlink() or (
        destination.exists() and not (destination / "pyvenv.cfg").is_file()
    ):
        print("BOOTSTRAP_REFUSED_NON_VIRTUALENV_DESTINATION")
        return 2
    try:
        venv.EnvBuilder(with_pip=True).create(destination)
    except (OSError, subprocess.SubprocessError):
        print("BOOTSTRAP_VIRTUALENV_CREATION_FAILED")
        return 2
    python = destination / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    steps = [
        ("pinned_tools", root, [
            str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
            "-r", str(root / "requirements-dev.lock"),
        ]),
        ("local_packages", root, [
            str(python), "-I", "-m", "pip", "install", "--disable-pip-version-check",
            "--no-deps", "--no-build-isolation",
            *[part for package in PACKAGES for part in ("-e", str(root / "packages" / package))],
        ]),
    ]
    if infrastructure:
        steps.extend([
            ("infrastructure_install", infra_root, ["npm", "ci", "--no-audit", "--no-fund"]),
            ("infrastructure_versions", infra_root, ["npm", "run", "--silent", "verify"]),
        ])
    for name, directory, command in steps:
        try:
            result = subprocess.run(
                command, cwd=directory, capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            print(json.dumps({"step": name, "status": "failed", "details": "withheld"}))
            return 2
        if result.returncode:
            print(json.dumps({
                "step": name, "status": "failed",
                "exit_code": result.returncode, "details": "withheld",
            }))
            return 2
        print(json.dumps({
            "step": name,
            "status": "verified" if name == "infrastructure_versions" else "installed",
        }))
    print("Development environment installed in this checkout only.")
    print("Next: .venv/bin/python scripts/verify_dev.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(2, "Unsupported arguments. Use --help; never pass credentials as arguments.\n")
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument(
        "--install", action="store_true",
        help="Create .venv and download the pinned test/build dependencies.",
    )
    parser.add_argument(
        "--infrastructure", action="store_true",
        help="With --install, also run pinned npm ci and the local CLI version verifier; no login or deploy.",
    )
    args = parser.parse_args(argv)
    if not args.install:
        print(json.dumps({
            "action": "plan_only", "network_access": False,
            "environment": ".venv",
            "packages": list(PACKAGES),
            "infrastructure_requested": args.infrastructure,
            "next_command": "python3 scripts/bootstrap_dev.py --install" + (
                " --infrastructure" if args.infrastructure else ""
            ),
            "scope": "this checkout only; no remote account setup",
        }, indent=2))
        return 0
    return install(infrastructure=args.infrastructure)


if __name__ == "__main__":
    raise SystemExit(main())
