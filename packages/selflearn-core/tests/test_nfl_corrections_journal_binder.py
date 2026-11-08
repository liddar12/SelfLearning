"""Journal as-of binder: generated_unverified excluded; untrusted never applies."""
from __future__ import annotations


from selflearn_core.adapters.nfl_corrections import (
    fit_corrections_artifact,
    fit_corrections_from_journal,
    plan_corrections,
)
from selflearn_core.adapters.nfl_corrections_policy import TRUSTED_ARTIFACT_SOURCE
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from selflearn_core.store.receipt_journal import ReceiptJournal


def _sealed(*, publication_status="verified_publication", market="qb_pass_yds",
            selection="over", served=0.55):
    features = {
        "market": market, "line": 249.5, "mu": 260.0, "sd": 45.0, "z": 0.2,
        "p_team": 0.55, "selection": selection, "direction": selection,
        "calibration": {"a": 0.2, "b": 0.0, "c": 0.0},
        "correlation_tags": ["game:G1"],
    }
    return seal_receipt({
        "version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
        "contract_id": fingerprint(["nfl", market, selection]),
        "event_id": "nfl:2026:game:G1",
        "correlation_group": "nfl:2026:game:G1",
        "base_prob": served, "served_prob": served,
        "base_model_version": fingerprint("base"),
        "calibrator_version": fingerprint(None),
        "forecast_created_utc": "2026-09-01T14:00:00Z",
        "first_seen_utc": "2026-09-01T14:01:00Z",
        "event_start_utc": "2026-09-02T00:15:00Z",
        "source_snapshot_id": fingerprint("snap"),
        "source_timestamps": {"fixture": "2026-09-01T13:00:00Z"},
        "features": features,
        "source_receipt_id": fingerprint(["src", market, selection]),
        "publication_status": publication_status,
    })


class _FakeJournal:
    def __init__(self, receipts, histories):
        self._receipts = receipts
        self._histories = histories

    def receipts(self):
        return list(self._receipts)

    def resolution_history(self, receipt_id):
        return list(self._histories.get(receipt_id, []))


def test_generated_unverified_receipts_are_fit_ineligible(tmp_path):
    receipt = _sealed(publication_status="generated_unverified")
    with ReceiptJournal(tmp_path / "gen.sqlite") as journal:
        journal.append_receipt(receipt)
        journal.append_resolution(
            {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
             "event_id": receipt["event_id"], "status": "FINAL", "hit": True,
             "resolved_utc": "2026-09-02T04:00:00Z", "source": "fixture"},
            revision_id="rev-1",
        )
        art = fit_corrections_from_journal(
            journal,
            trained_through="2099-01-01T00:00:00Z",
            expires_at="2099-02-01T00:00:00Z",
            fitted_at="2099-01-01T01:00:00Z",
            min_graded_total=1, min_graded_per_market=1,
        )
    assert art["source"] == TRUSTED_ARTIFACT_SOURCE
    assert art["n_graded"] == 0
    assert art["segments"] == {}


def test_binder_as_of_excludes_future_recorded_revisions():
    receipt = _sealed()
    rev1 = {
        "version": 1, "revision_id": "rev-1", "supersedes": None, "reason": None,
        "recorded_utc": "2026-09-02T05:00:00Z",
        "resolution": {
            "receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
            "event_id": receipt["event_id"], "status": "FINAL", "hit": False,
            "resolved_utc": "2026-09-02T04:00:00Z", "source": "fixture",
        },
    }
    rev2 = {
        "version": 1, "revision_id": "rev-2", "supersedes": "rev-1",
        "reason": "official_stat_correction",
        "recorded_utc": "2026-09-10T05:00:00Z",
        "resolution": {
            "receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
            "event_id": receipt["event_id"], "status": "FINAL", "hit": True,
            "resolved_utc": "2026-09-10T04:00:00Z", "source": "fixture-correction",
        },
    }
    journal = _FakeJournal([receipt], {receipt["receipt_id"]: [rev1, rev2]})
    early = fit_corrections_from_journal(
        journal,
        trained_through="2026-09-03T00:00:00Z",
        expires_at="2026-10-01T00:00:00Z",
        fitted_at="2026-09-03T01:00:00Z",
        min_graded_total=1, min_graded_per_market=1,
    )
    late = fit_corrections_from_journal(
        journal,
        trained_through="2026-09-11T00:00:00Z",
        expires_at="2026-10-01T00:00:00Z",
        fitted_at="2026-09-11T01:00:00Z",
        min_graded_total=1, min_graded_per_market=1,
    )
    assert early["n_graded"] == 1
    assert early["segments"]["qb_pass_yds|over"]["raw_bias"] < 0
    assert late["n_graded"] == 1
    assert late["segments"]["qb_pass_yds|over"]["raw_bias"] > 0


def test_default_caller_list_source_cannot_apply_even_with_floors():
    rows = []
    for i in range(60):
        for market in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"):
            rows.append({
                "market": market, "selection": "over", "baseline_prob": 0.6,
                "served_at": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                "kickoff_at": f"2026-10-{(i % 20) + 1:02d}T18:00:00Z",
                "graded_at": f"2026-10-{(i % 20) + 2:02d}T08:00:00Z",
                "outcome": (i % 10) < 4,
            })
    art = fit_corrections_artifact(
        rows, trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z", fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=100, min_graded_per_market=50)
    assert art["source"] != TRUSTED_ARTIFACT_SOURCE
    request = {
        "schema_version": 1, "request_id": "u1", "season": 2026, "week": 9,
        "baseline_model_version": "sha256:" + ("a" * 64),
        "generated_at": "2026-11-06T12:00:00Z",
        "legs": [{"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
                  "selection": "over", "baseline_prob": 0.55}],
        "parlays": [],
    }
    response = plan_corrections(request, mode="on", artifact=art, now="2026-11-02T00:00:00Z")
    assert response["legs"][0]["applied"] is False
    assert response["legs"][0]["reason"] == "untrusted_artifact_source"
