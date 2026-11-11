"""Offline development/configuration inventory. Never grants trading readiness.

Does not source .env, access the network, import broker adapters, or print
credential values. Credential presence is not authenticated service access.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "automation-readiness.json"
HANDOFF_COMMIT = "46be142de18823ec86be2e33d8a421ce272616a8"
EXPECTED_CONFIG = {
    "profile": "development",
    "mode": "SHADOW",
    "capital_usd": 2000,
    "live_trading_enabled": False,
    "scheduled_execution_enabled": False,
    "live_risk_policy_approved": False,
}
CREDENTIAL_GROUPS = {
    "alpaca_paper": ("ALPACA_API_KEY", "ALPACA_SECRET_KEY"),
    "supabase": ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "xai": ("XAI_API_KEY",),
}
MODULES = ("pytest", "selflearn_core", "execution_core", "signals")
OPTIONAL_CLIS = ("git", "node", "npm", "gh", "codex", "vercel", "supabase", "netlify")
COMPONENT_PATHS = {
    "reconciliation": "packages/execution-core/execution_core/reconcile.py",
    "signal_registry": "packages/selflearn-core/selflearn_core/signals_registry.py",
    "never_regress": "packages/selflearn-core/selflearn_core/scoring/never_regress.py",
    "options_odds": "apps/liddar-execution/options/odds.py",
    "judgment_providers": "apps/liddar-execution/judgment",
    "shadow_runner": "apps/liddar-execution/runner/premarket.py",
}


def config_errors(config: object) -> list[str]:
    if not isinstance(config, dict):
        return ["CONFIG_MUST_BE_AN_OBJECT"]
    errors = []
    if set(config) != set(EXPECTED_CONFIG):
        errors.append("CONFIG_FIELDS_MISMATCH")
    for key, expected in EXPECTED_CONFIG.items():
        value = config.get(key)
        if type(value) is not type(expected) or value != expected:
            errors.append("INVALID_" + key.upper())
    return errors


def load_config(path: Path) -> tuple[object, list[str]]:
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return None, ["CONFIG_UNREADABLE_OR_INVALID_JSON"]
    return config, config_errors(config)


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def checkout_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = result.stdout.strip()
    return value if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", value) else None


def credential_inventory(env: Mapping[str, str]) -> dict:
    report = {}
    for service, names in CREDENTIAL_GROUPS.items():
        present = {name: bool(env.get(name, "").strip()) for name in names}
        report[service] = {
            "variables_present": present,
            "configuration": "present_unverified" if all(present.values()) else "missing_or_partial",
            "connection": "not_checked",
        }
    return report


def worktree_dirty(root: Path) -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True,
            text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(result.stdout) if result.returncode == 0 else None


def build_report(
    root: Path = ROOT,
    config_path: Path = DEFAULT_CONFIG,
    env: Mapping[str, str] | None = None,
) -> dict:
    _, errors = load_config(config_path)
    modules = {name: module_available(name) for name in MODULES}
    python_supported = sys.version_info >= (3, 11)
    development_ok = python_supported and all(modules.values()) and not errors
    return {
        "schema_version": 1,
        "purpose": "offline_development_inventory",
        "source": {
            "checkout_sha": checkout_sha(root),
            "working_tree_dirty": worktree_dirty(root),
            "handoff_baseline_sha": HANDOFF_COMMIT,
            "handoff": "docs/codex-handoff-2026-11-10.md",
        },
        "safety": {
            "mode": "SHADOW" if not errors else "invalid",
            "network_probes_performed": False,
            "broker_methods_called": False,
            "live_trading_enabled": False,
            "scheduled_execution_enabled": False,
            "live_risk_policy_approved": False,
            "configuration_valid": not errors,
            "configuration_errors": errors,
        },
        "development": {
            "status": "ready_for_local_checks" if development_ok else "needs_setup",
            "python_supported": python_supported,
            "module_presence": modules,
            "optional_cli_presence": {name: shutil.which(name) is not None for name in OPTIONAL_CLIS},
            "cli_note": "CLI presence is optional; it does not verify connector or account access.",
        },
        "credentials": credential_inventory(os.environ if env is None else env),
        "connected_services": {
            name: "not_checked"
            for name in ("github", "vercel", "supabase", "netlify", "alpaca", "schwab", "grace")
        },
        "application": {
            "source_presence": {
                name: (root / path).exists() for name, path in COMPONENT_PATHS.items()
            },
            "source_note": "A present file may be a stub. Presence is not functional verification.",
            "tests": "not_run_by_this_command",
            "shadow_runtime": "not_verified",
            "paper_execution": "not_verified",
            "options_execution": "not_verified",
        },
        "production_and_live": {
            "eligible": False,
            "blockers": [
                "HANDOFF_VERCEL_PRODUCTION_COLLISION_REQUIRES_VERIFIED_RESOLUTION",
                "HOSTED_PREDICTION_STORE_REQUIRES_CONNECTION_AND_END_TO_END_VERIFICATION",
                "BROKER_PERMISSIONS_AND_LIVE_RISK_POLICY_REQUIRE_OWNER_AUTHORIZATION",
                "APPLICATION_AND_RECOVERY_BEHAVIOR_REQUIRE_FUNCTIONAL_VERIFICATION",
            ],
            "capital_context_usd": 2000,
            "historical_1500_trade_loss_cap_approved": False,
            "note": "This diagnostic never enables live trading or schedules.",
        },
    }


def main(argv: list[str] | None = None) -> int:
    class SafeParser(argparse.ArgumentParser):
        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(2, "Unsupported arguments. Use --help; never pass credentials as arguments.\n")
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--require-dev", action="store_true",
        help="Exit nonzero unless local dependencies and SHADOW configuration are present.",
    )
    args = parser.parse_args(argv)
    report = build_report(config_path=args.config)
    print(json.dumps(report, indent=2, sort_keys=True))
    if not report["safety"]["configuration_valid"]:
        return 2
    if args.require_dev and report["development"]["status"] != "ready_for_local_checks":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
