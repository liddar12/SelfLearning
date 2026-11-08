"""R3 failing-first: trusted label is not binder origin; CLI cannot forge it."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from selflearn_core.adapters.nfl_corrections import (
    CorrectionsError,
    authorize_corrections_artifact,
    fit_corrections_artifact,
    fit_corrections_from_journal,
    plan_corrections,
)
from selflearn_core.adapters.nfl_corrections_policy import (
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
)
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from selflearn_core.store.receipt_journal import ReceiptJournal

ROOT = Path(__file__).resolve().parents[3]
CLI = ROOT / "tools" / "fit_nfl_corrections.py"


def _rows(n=60):
    rows = []
    for market in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"):
        for i in range(n):
            rows.append({
                "market": market, "selection": "over", "baseline_prob": 0.60,
                "served_at": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                "kickoff_at": f"2026-10-{(i % 20) + 1:02d}T18:00:00Z",
                "graded_at": f"2026-10-{(i % 20) + 2:02d}T08:00:00Z",
                "outcome": (i % 10) < 4,
            })
    return rows


def _request(baseline=0.55):
    return {
        "schema_version": 1, "request_id": "r3", "season": 2026, "week": 9,
        "baseline_model_version": "sha256:" + ("a" * 64),
        "generated_at": "2026-11-06T12:00:00Z",
        "legs": [{"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
                  "selection": "over", "baseline_prob": baseline}],
        "parlays": [],
    }


def test_fit_rejects_trusted_source_without_allow_flag():
    with pytest.raises(CorrectionsError, match="trusted_source_requires_journal_binder"):
        fit_corrections_artifact(
            _rows(), trained_through="2026-11-01T00:00:00Z",
            expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
            source=TRUSTED_ARTIFACT_SOURCE)


def test_plan_refuses_forged_trusted_label_without_verification():
    forged = fit_corrections_artifact(
        _rows(), trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        source=TRUSTED_ARTIFACT_SOURCE, allow_trusted_source=True)
    response = plan_corrections(_request(0.55), mode="on", artifact=forged,
                                now="2026-11-02T00:00:00Z")
    assert response["legs"][0]["applied"] is False
    assert response["legs"][0]["reason"] == "unverified_binder_origin"
    assert response["legs"][0]["adjusted_prob"] == 0.55


def test_authorize_rejects_forged_plant_against_empty_journal(tmp_path):
    forged = fit_corrections_artifact(
        _rows(), trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        source=TRUSTED_ARTIFACT_SOURCE, allow_trusted_source=True)
    with ReceiptJournal(tmp_path / "empty.sqlite") as journal:
        with pytest.raises(CorrectionsError, match="trusted_artifact_journal_mismatch"):
            authorize_corrections_artifact(journal, forged)


def test_cli_refuses_trusted_source_label(tmp_path):
    graded = tmp_path / "graded.json"
    graded.write_text(json.dumps(_rows()), encoding="utf-8")
    out = tmp_path / "art.json"
    proc = subprocess.run(
        [sys.executable, str(CLI), str(graded),
         "--trained-through", "2026-11-01T00:00:00Z",
         "--expires-at", "2026-12-01T00:00:00Z",
         "--fitted-at", "2026-11-01T01:00:00Z",
         "--source", TRUSTED_ARTIFACT_SOURCE,
         "--output", str(out)],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
        env={**dict(**{k: v for k, v in __import__("os").environ.items()}),
             "PYTHONPATH": str(ROOT / "packages" / "selflearn-core")},
    )
    assert proc.returncode != 0
    assert "refusing trusted binder source" in (proc.stderr + proc.stdout)
    assert not out.exists()


def test_legitimate_binder_authorize_and_apply(tmp_path):
    receipt = seal_receipt({
        "version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
        "contract_id": fingerprint(["c"]), "event_id": "nfl:2026:game:G1",
        "correlation_group": "nfl:2026:game:G1",
        "base_prob": 0.55, "served_prob": 0.55,
        "base_model_version": fingerprint("b"),
        "calibrator_version": fingerprint(None),
        "forecast_created_utc": "2026-09-01T14:00:00Z",
        "first_seen_utc": "2026-09-01T14:01:00Z",
        "event_start_utc": "2026-09-02T00:15:00Z",
        "source_snapshot_id": fingerprint("s"),
        "source_timestamps": {"fixture": "2026-09-01T13:00:00Z"},
        "features": {
            "market": "qb_pass_yds", "line": 1.0, "mu": 1.0, "sd": 1.0, "z": 0.0,
            "p_team": 0.5, "selection": "over", "direction": "over",
            "calibration": {"a": 0.0, "b": 0.0, "c": 0.0},
            "correlation_tags": ["game:G1"],
        },
        "source_receipt_id": fingerprint(["src"]),
        "publication_status": "verified_publication",
    })
    # Enough identical-shape receipts for floors via loop
    with ReceiptJournal(tmp_path / "ok.sqlite") as journal:
        for i in range(60):
            for mi, market in enumerate(("qb_pass_yds", "rb_rush_yds", "wr_rec_yds")):
                features = dict(receipt["features"])
                features["market"] = market
                features["selection"] = "over"
                body = {k: v for k, v in receipt.items() if k != "receipt_id"}
                body = {**body,
                        "contract_id": fingerprint(["c", market, i]),
                        "event_id": f"nfl:2026:game:G{i}-{mi}",
                        "correlation_group": f"nfl:2026:game:G{i}-{mi}",
                        "features": features,
                        "source_receipt_id": fingerprint(["src", market, i]),
                        "source_snapshot_id": fingerprint(["snap", i, mi]),
                        "forecast_created_utc": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                        "first_seen_utc": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                        "event_start_utc": f"2026-10-{(i % 20) + 1:02d}T18:00:00Z",
                        "source_timestamps": {
                            "fixture": f"2026-10-{(i % 20) + 1:02d}T09:00:00Z"},
                        }
                row = seal_receipt(body)
                journal.append_receipt(row)
                journal.append_resolution(
                    {"receipt_id": row["receipt_id"], "contract_id": row["contract_id"],
                     "event_id": row["event_id"], "status": "FINAL",
                     "hit": (i % 10) < 4,
                     "resolved_utc": f"2026-10-{min((i % 20) + 2, 28):02d}T08:00:00Z",
                     "source": "fixture"},
                    revision_id=f"r-{mi}-{i}",
                )
        art = fit_corrections_from_journal(
            journal, trained_through="2099-01-01T00:00:00Z",
            expires_at="2099-02-01T00:00:00Z", fitted_at="2099-01-01T01:00:00Z",
            min_graded_total=150, min_graded_per_market=50)
        assert art["source"] == TRUSTED_ARTIFACT_SOURCE
        assert art["n_graded"] >= 150
        authorize_corrections_artifact(journal, art)
        response = plan_corrections(
            _request(0.55), mode="on", artifact=art, binder_verified=True,
            now="2099-01-02T00:00:00Z")
        assert response["legs"][0]["applied"] is True
        # Baseline invariance when applied=false path still exact:
        denied = plan_corrections(
            _request(0.003), mode="on", artifact=art, binder_verified=False,
            now="2099-01-02T00:00:00Z")
        assert denied["legs"][0]["applied"] is False
        assert denied["legs"][0]["adjusted_prob"] == 0.003
