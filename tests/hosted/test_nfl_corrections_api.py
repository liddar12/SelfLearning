"""Hosted boundary tests for POST /api/v1/nfl/corrections. Local fakes only."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from selflearn_core.adapters.nfl_corrections import (
    fit_corrections_artifact,
    fit_corrections_from_journal,
)
from selflearn_core.adapters.nfl_corrections_policy import (
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
)
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from selflearn_core.store.receipt_journal import ReceiptJournal
from services.web.application import create_app
from services.web.nfl_corrections import artifact_snapshot_payload
from .fakes import FakeStore, TEST_KEY

OPERATOR = TEST_KEY
INGEST = "test-nfl-ingest-credential-" + "a" * 32
OUTCOME = "test-nfl-outcome-credential-" + "b" * 32
CORRECTIONS = "test-nfl-corrections-credential-" + "c" * 32


def graded_rows(n=60):
    rows = []
    for market in ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds"):
        for i in range(n):
            rows.append({
                "market": market,
                "selection": "over",
                "baseline_prob": 0.60,
                "served_at": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                "kickoff_at": f"2026-10-{(i % 20) + 1:02d}T18:00:00Z",
                "graded_at": f"2026-10-{(i % 20) + 2:02d}T08:00:00Z",
                "outcome": (i % 10) < 4,
            })
    return rows


def forged_trusted_artifact():
    """Caller-list grades stamped with trusted label via binder bypass flag.
    Used only to prove hosted apply refuses without journal authorization.
    """
    return fit_corrections_artifact(
        graded_rows(),
        trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=150,
        min_graded_per_market=50,
        source=TRUSTED_ARTIFACT_SOURCE,
        allow_trusted_source=True,
    )


def untrusted_artifact():
    return fit_corrections_artifact(
        graded_rows(),
        trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        fitted_at="2026-11-01T01:00:00Z",
        min_graded_total=150,
        min_graded_per_market=50,
        source=UNTRUSTED_ARTIFACT_SOURCE,
    )


def _sealed_receipt(i, market, *, hit):
    day = (i % 20) + 1
    selection = "over"
    served = 0.60
    features = {
        "market": market, "line": 249.5, "mu": 260.0, "sd": 45.0, "z": 0.2,
        "p_team": 0.55, "selection": selection, "direction": selection,
        "calibration": {"a": 0.2, "b": 0.0, "c": 0.0},
        "correlation_tags": [f"game:G{i}"],
    }
    return seal_receipt({
        "version": 1, "domain": "nfl", "task": "nfl.parlay_leg",
        "contract_id": fingerprint(["nfl", market, selection, i]),
        "event_id": f"nfl:2026:game:G{i}",
        "correlation_group": f"nfl:2026:game:G{i}",
        "base_prob": served, "served_prob": served,
        "base_model_version": fingerprint("base"),
        "calibrator_version": fingerprint(None),
        "forecast_created_utc": f"2026-10-{day:02d}T10:00:00Z",
        "first_seen_utc": f"2026-10-{day:02d}T10:00:00Z",
        "event_start_utc": f"2026-10-{day:02d}T18:00:00Z",
        "source_snapshot_id": fingerprint(["snap", i]),
        "source_timestamps": {"fixture": f"2026-10-{day:02d}T09:00:00Z"},
        "features": features,
        "source_receipt_id": fingerprint(["src", market, i]),
        "publication_status": "verified_publication",
    }), hit, day


def populate_verified_journal(journal, n_per=60):
    markets = ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds")
    for mi, market in enumerate(markets):
        for i in range(n_per):
            hit = (i % 10) < 4
            receipt, hit, day = _sealed_receipt(i + 1000 * mi, market, hit=hit)
            graded_day = min(day + 1, 28)
            journal.append_receipt(receipt)
            journal.append_resolution(
                {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
                 "event_id": receipt["event_id"], "status": "FINAL", "hit": hit,
                 "resolved_utc": f"2026-10-{graded_day:02d}T08:00:00Z",
                 "source": "fixture"},
                revision_id=f"rev-{mi}-{i}-{receipt['receipt_id'][-8:]}",
            )


def binder_artifact(journal):
    # trained_through must be after DB recorded_utc of revisions (wall clock).
    return fit_corrections_from_journal(
        journal,
        trained_through="2099-01-01T00:00:00Z",
        expires_at="2099-02-01T00:00:00Z",
        fitted_at="2099-01-01T01:00:00Z",
        min_graded_total=150,
        min_graded_per_market=50,
    )


def request_body():
    return {
        "schema_version": 1,
        "request_id": "api-req-1",
        "season": 2026,
        "week": 9,
        "baseline_model_version": "sha256:" + ("d" * 64),
        "generated_at": "2026-11-06T12:00:00Z",
        "legs": [
            {"leg_id": "L1", "game_id": "G1", "market": "qb_pass_yds",
             "selection": "over", "baseline_prob": 0.55},
            {"leg_id": "L2", "game_id": "G1", "market": "rb_rush_yds",
             "selection": "over", "baseline_prob": 0.52},
        ],
        "parlays": [{"parlay_id": "P1", "leg_ids": ["L1", "L2"], "baseline_prob": 0.286}],
    }


def env_for(mode="off", *, artifact_id=None, corrections_token=CORRECTIONS):
    value = {
        "SELFLEARN_OPERATOR_TOKEN": OPERATOR,
        "SELFLEARN_NFL_INGEST_TOKEN": INGEST,
        "SELFLEARN_NFL_OUTCOME_INGEST_TOKEN": OUTCOME,
        "SELFLEARN_NFL_CORRECTIONS_TOKEN": corrections_token,
        "SELFLEARN_CORRECTIONS_MODE": mode,
    }
    if artifact_id:
        value["SELFLEARN_CORRECTIONS_ARTIFACT_ID"] = artifact_id
    return value


def store_artifact(path, art):
    with ReceiptJournal(path) as journal:
        journal.append_object("source_snapshot", art["artifact_id"],
                              artifact_snapshot_payload(art))


def client_for(path, *, mode="off", art=None, prepare=None):
    if art is None:
        art = untrusted_artifact()
    with ReceiptJournal(path) as journal:
        if prepare is not None:
            prepare(journal)
        journal.append_object("source_snapshot", art["artifact_id"],
                              artifact_snapshot_payload(art))

    @contextmanager
    def factory():
        with ReceiptJournal(path) as journal:
            yield journal

    app = create_app(store=FakeStore(), env=env_for(mode, artifact_id=art["artifact_id"]),
                     journal_factory=factory)
    return TestClient(app), art


def post(client, body=None, token=CORRECTIONS):
    return client.post("/api/v1/nfl/corrections", json=body or request_body(),
                       headers={"Authorization": "Bearer " + token})


def test_missing_corrections_token_fails_closed_503(tmp_path):
    path = tmp_path / "c.sqlite"
    art = untrusted_artifact()
    store_artifact(path, art)

    @contextmanager
    def factory():
        with ReceiptJournal(path) as journal:
            yield journal

    env = env_for("on", artifact_id=art["artifact_id"], corrections_token="")
    with TestClient(create_app(store=FakeStore(), env=env, journal_factory=factory)) as client:
        assert post(client, token=CORRECTIONS).status_code == 503


def test_wrong_scope_tokens_are_rejected(tmp_path):
    client, _ = client_for(tmp_path / "scope.sqlite", mode="on")
    with client:
        for token in (OPERATOR, INGEST, OUTCOME, "", "short"):
            assert post(client, token=token).status_code in (401, 503)
        assert post(client).status_code == 200


def test_duplicate_token_configuration_is_503(tmp_path):
    path = tmp_path / "dup.sqlite"
    art = untrusted_artifact()
    store_artifact(path, art)

    @contextmanager
    def factory():
        with ReceiptJournal(path) as journal:
            yield journal

    env = env_for("on", artifact_id=art["artifact_id"])
    env["SELFLEARN_NFL_CORRECTIONS_TOKEN"] = INGEST
    with TestClient(create_app(store=FakeStore(), env=env, journal_factory=factory)) as client:
        assert post(client, token=INGEST).status_code == 503


def test_off_mode_echoes_baseline(tmp_path):
    client, _ = client_for(tmp_path / "off.sqlite", mode="off")
    body = request_body()
    body["legs"][0]["baseline_prob"] = 0.005
    with client:
        response = post(client, body)
        assert response.status_code == 200
        value = response.json()
        assert value["corrections_mode"] == "off"
        assert value["request_id"] == "api-req-1"
        assert value["artifact"] is None
        assert value["legs"][0]["adjusted_prob"] == 0.005
        assert value["legs"][0]["applied"] is False


def test_shadow_never_applies(tmp_path):
    client, art = client_for(tmp_path / "shadow.sqlite", mode="shadow", art=untrusted_artifact())
    with client:
        response = post(client)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["corrections_mode"] == "shadow"
        assert value["artifact"]["artifact_id"] == art["artifact_id"]
        assert all(row["applied"] is False for row in value["legs"])
        assert all(row["adjusted_prob"] == row["baseline_prob"] for row in value["legs"])


def test_forged_trusted_label_plant_cannot_apply(tmp_path):
    """R3 finding: planted trusted source_snapshot without binder journal match."""
    art = forged_trusted_artifact()
    assert art["source"] == TRUSTED_ARTIFACT_SOURCE
    client, _ = client_for(tmp_path / "forge.sqlite", mode="on", art=art)
    body = request_body()
    body["legs"][0]["baseline_prob"] = 0.55
    with client:
        response = post(client, body)
        assert response.status_code == 200, response.text
        leg = response.json()["legs"][0]
        assert leg["applied"] is False
        assert leg["adjusted_prob"] == 0.55
        assert leg["delta"] == 0.0


def test_legitimate_journal_binder_artifact_can_apply(tmp_path):
    path = tmp_path / "binder.sqlite"
    with ReceiptJournal(path) as journal:
        populate_verified_journal(journal, n_per=60)
        art = binder_artifact(journal)
        assert art["source"] == TRUSTED_ARTIFACT_SOURCE
        assert art["n_graded"] >= 150
        journal.append_object("source_snapshot", art["artifact_id"],
                              artifact_snapshot_payload(art))

    @contextmanager
    def factory():
        with ReceiptJournal(path) as journal:
            yield journal

    app = create_app(store=FakeStore(),
                     env=env_for("on", artifact_id=art["artifact_id"]),
                     journal_factory=factory)
    with TestClient(app) as client:
        # now must be before expires_at for apply
        body = request_body()
        response = post(client, body)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["artifact"]["artifact_id"] == art["artifact_id"]
        assert any(row["applied"] for row in value["legs"]), value["legs"]
        for row in value["legs"]:
            if row["applied"]:
                assert row["reason"] == "learned_segment_bias"
            else:
                assert row["adjusted_prob"] == row["baseline_prob"]


def test_invalid_body_is_422(tmp_path):
    client, _ = client_for(tmp_path / "bad.sqlite", mode="on")
    with client:
        assert post(client, {"schema_version": 1}).status_code == 422
