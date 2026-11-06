"""Hosted boundary tests for POST /api/v1/nfl/corrections. Local fakes only."""
from __future__ import annotations

from contextlib import contextmanager

from fastapi.testclient import TestClient

from selflearn_core.adapters.nfl_corrections import fit_corrections_artifact
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
                "baseline_prob": 0.60,
                "served_at": f"2026-10-{(i % 20) + 1:02d}T10:00:00Z",
                "kickoff_at": f"2026-10-{(i % 20) + 1:02d}T18:00:00Z",
                "graded_at": f"2026-10-{(i % 20) + 2:02d}T08:00:00Z",
                "outcome": (i % 10) < 4,
            })
    return rows


def artifact():
    return fit_corrections_artifact(
        graded_rows(),
        trained_through="2026-11-01T00:00:00Z",
        expires_at="2026-12-01T00:00:00Z",
        fitted_at="2026-11-01T01:00:00Z",
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


def client_for(path, *, mode="off", art=None):
    if art is None:
        art = artifact()
    store_artifact(path, art)
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
    art = artifact()
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
    art = artifact()
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


def test_shadow_and_on_modes(tmp_path):
    for mode, expect_applied in (("shadow", False), ("on", True)):
        client, art = client_for(tmp_path / f"{mode}.sqlite", mode=mode)
        with client:
            response = post(client)
            assert response.status_code == 200, response.text
            value = response.json()
            assert value["corrections_mode"] == mode
            assert value["artifact"]["artifact_id"] == art["artifact_id"]
            assert any(row["applied"] is expect_applied for row in value["legs"]) or mode == "shadow"
            if mode == "shadow":
                assert all(row["applied"] is False for row in value["legs"])
                assert all(row["adjusted_prob"] == row["baseline_prob"] for row in value["legs"])
            else:
                assert any(row["applied"] for row in value["legs"])


def test_invalid_body_is_422(tmp_path):
    client, _ = client_for(tmp_path / "bad.sqlite", mode="on")
    with client:
        assert post(client, {"schema_version": 1}).status_code == 422
