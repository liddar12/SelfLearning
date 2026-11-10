"""Regressions for offline behavior, secret redaction, and honest readiness."""

import json
from pathlib import Path
import socket
import subprocess
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import automation_readiness as readiness
import bootstrap_dev
import verify_dev


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "configuration.json"
    path.write_text(json.dumps(readiness.EXPECTED_CONFIG), encoding="utf-8")
    return path


@pytest.fixture
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Network is forbidden in offline readiness")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(readiness, "checkout_sha", lambda root: "a" * 40)
    monkeypatch.setattr(readiness, "module_available", lambda name: True)
    monkeypatch.setattr(readiness.shutil, "which", lambda name: None)


def test_complete_environment_never_means_connected_or_live(config_file, offline, tmp_path):
    secret = "UNIQUE_SENSITIVE_VALUE_MUST_NOT_APPEAR"
    env = {key: secret for names in readiness.CREDENTIAL_GROUPS.values() for key in names}
    report = readiness.build_report(tmp_path, config_file, env)
    assert report["development"]["status"] == "ready_for_local_checks"
    assert all(value == "not_checked" for value in report["connected_services"].values())
    assert all(value["connection"] == "not_checked" for value in report["credentials"].values())
    assert report["application"]["tests"] == "not_run_by_this_command"
    assert report["production_and_live"]["eligible"] is False
    assert report["safety"]["network_probes_performed"] is False
    assert report["safety"]["broker_methods_called"] is False
    assert secret not in json.dumps(report)


def test_missing_credentials_are_reported_without_blocking_local_work(config_file, offline, tmp_path):
    report = readiness.build_report(tmp_path, config_file, {})
    assert report["development"]["status"] == "ready_for_local_checks"
    assert all(value["configuration"] == "missing_or_partial" for value in report["credentials"].values())


@pytest.mark.parametrize("key,value", [
    ("mode", "AUTO"),
    ("mode", "SIM"),
    ("live_trading_enabled", True),
    ("scheduled_execution_enabled", True),
    ("live_risk_policy_approved", True),
    ("capital_usd", 1500),
    ("live_trading_enabled", 0),
])
def test_unsafe_or_wrongly_typed_config_is_rejected(key, value):
    config = dict(readiness.EXPECTED_CONFIG, **{key: value})
    assert "INVALID_" + key.upper() in readiness.config_errors(config)


def test_unrecognized_config_fields_are_not_echoed():
    config = dict(readiness.EXPECTED_CONFIG)
    config["SECRET_VALUE_AS_A_FIELD"] = "sensitive"
    errors = readiness.config_errors(config)
    assert errors == ["CONFIG_FIELDS_MISMATCH"]
    assert "SECRET_VALUE" not in json.dumps(errors)


def test_malformed_config_is_not_silently_replaced(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{NOT_A_SECRET_TO_ECHO", encoding="utf-8")
    _, errors = readiness.load_config(path)
    assert errors == ["CONFIG_UNREADABLE_OR_INVALID_JSON"]
    assert "NOT_A_SECRET" not in str(errors)


def test_file_presence_does_not_establish_a_working_runtime(config_file, offline, tmp_path):
    for path in readiness.COMPONENT_PATHS.values():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("# stub", encoding="utf-8")
    report = readiness.build_report(tmp_path, config_file, {})
    assert all(report["application"]["source_presence"].values())
    assert report["application"]["shadow_runtime"] == "not_verified"
    assert report["production_and_live"]["eligible"] is False


def test_require_dev_exits_nonzero_for_missing_dependency(config_file, offline, monkeypatch, capsys):
    monkeypatch.setattr(readiness, "module_available", lambda name: name != "pytest")
    assert readiness.main(["--config", str(config_file), "--require-dev"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["development"]["status"] == "needs_setup"


def test_invalid_mode_is_not_echoed(config_file, offline, capsys):
    config = dict(readiness.EXPECTED_CONFIG, mode="SECRET_TEXT")
    config_file.write_text(json.dumps(config), encoding="utf-8")
    assert readiness.main(["--config", str(config_file)]) == 2
    output = capsys.readouterr().out
    assert "SECRET_TEXT" not in output
    assert json.loads(output)["safety"]["mode"] == "invalid"


def test_bootstrap_default_is_plan_only(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Default bootstrap must not install")
    monkeypatch.setattr(bootstrap_dev, "install", forbidden)
    assert bootstrap_dev.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["network_access"] is False
    assert report["action"] == "plan_only"


def test_infrastructure_flag_alone_never_installs(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        raise AssertionError("Infrastructure flag alone must not permit installation")
    monkeypatch.setattr(bootstrap_dev, "install", forbidden)
    assert bootstrap_dev.main(["--infrastructure"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["network_access"] is False
    assert report["infrastructure_requested"] is True


def test_bootstrap_refuses_existing_non_venv(tmp_path, capsys):
    (tmp_path / ".venv").mkdir()
    assert bootstrap_dev.install(tmp_path) == 2
    assert "REFUSED_NON_VIRTUALENV" in capsys.readouterr().out


def test_bootstrap_does_not_print_installer_secrets(tmp_path, monkeypatch, capsys):
    class FakeBuilder:
        def __init__(self, **kwargs):
            pass
        def create(self, path):
            path.mkdir()
            (path / "pyvenv.cfg").write_text("home = fixture")
    monkeypatch.setattr(bootstrap_dev.venv, "EnvBuilder", FakeBuilder)
    monkeypatch.setattr(bootstrap_dev.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=[], returncode=1, stdout="SENSITIVE_INDEX_URL", stderr="SENSITIVE_INDEX_URL",
    ))
    assert bootstrap_dev.install(tmp_path) == 2
    output = capsys.readouterr().out
    assert "SENSITIVE_INDEX_URL" not in output
    assert "failed" in output


def test_verification_subprocesses_do_not_receive_account_credentials():
    source = {
        "PATH": "/usr/bin", "ALPACA_API_KEY": "a", "SUPABASE_URL": "private",
        "OTHER_ACCESS_TOKEN": "b", "DATABASE_PASSWORD": "c", "USER_SECRET": "d",
    }
    env = verify_dev.test_environment(source)
    assert env["PATH"] == "/usr/bin"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert "ALPACA_API_KEY" not in env
    assert "SUPABASE_URL" not in env
    assert "OTHER_ACCESS_TOKEN" not in env
    assert "DATABASE_PASSWORD" not in env
    assert "USER_SECRET" not in env
    assert "ALPACA_API_KEY" in source


def test_verification_reports_counts_not_raw_output(tmp_path, monkeypatch):
    monkeypatch.setattr(verify_dev, "checkout_sha", lambda root: "a" * 40)
    monkeypatch.setattr(verify_dev.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        args=[], returncode=0, stdout="3 passed\nSECRET_IN_SUBPROCESS", stderr="SECRET_IN_SUBPROCESS",
    ))
    report = verify_dev.run_checks(tmp_path)
    assert report["passed"] is True
    assert report["production_or_live_eligible"] is False
    assert report["connected_services_verified"] is False
    assert all(check["tests_passed"] == 3 for check in report["checks"])
    assert "SECRET_IN_SUBPROCESS" not in json.dumps(report)


@pytest.mark.parametrize("module", [readiness, bootstrap_dev])
def test_argument_errors_do_not_echo_unknown_secret(module, capsys):
    with pytest.raises(SystemExit) as exc:
        module.main(["--SECRET_VALUE_IN_ARGUMENT"])
    assert exc.value.code == 2
    assert "SECRET_VALUE_IN_ARGUMENT" not in capsys.readouterr().err
