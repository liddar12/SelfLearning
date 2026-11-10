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
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal as ReceiptJournal
from publication_fit_fixture import seed_publication_journal

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
    with ReceiptJournal(tmp_path / "ok.sqlite") as journal:
        seeded = seed_publication_journal(journal, n_per=60)
        originals = journal.receipts()
        assert all(r["publication_status"] == "generated_unverified" for r in originals)
        assert all("selection" not in r["features"] and "direction" not in r["features"] for r in originals)
        art = fit_corrections_from_journal(
            journal, trained_through="2099-01-01T00:00:00Z",
            expires_at="2099-02-01T00:00:00Z", fitted_at="2099-01-01T01:00:00Z",
            min_graded_total=150, min_graded_per_market=50)
        assert art["source"] == TRUSTED_ARTIFACT_SOURCE
        assert art["n_graded"] == 180
        authorize_corrections_artifact(journal, art)
        response = plan_corrections(
            _request(0.55), mode="on", artifact=art, binder_verified=True,
            now="2099-01-02T00:00:00Z")
        assert response["legs"][0]["applied"] is True
        denied = plan_corrections(
            _request(0.003), mode="on", artifact=art, binder_verified=False,
            now="2099-01-02T00:00:00Z")
        assert denied["legs"][0]["applied"] is False
        assert denied["legs"][0]["adjusted_prob"] == 0.003
        assert journal.receipts() == originals
