"""Hosted boundary tests for POST /api/v1/nfl/corrections. Local fakes only."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import runpy

from fastapi.testclient import TestClient
import pytest

from selflearn_core.adapters.nfl import canonical_key
from selflearn_core.adapters.nfl_collection_handoff import SQLiteCollectionHandoff
from selflearn_core.adapters.nfl_corrections import (
    fit_corrections_artifact,
    fit_corrections_from_journal,
    authorize_corrections_artifact,
    plan_corrections,
)
from selflearn_core.adapters.nfl_corrections_policy import (
    TRUSTED_ARTIFACT_SOURCE,
    UNTRUSTED_ARTIFACT_SOURCE,
)
from selflearn_core.probability_receipts import fingerprint, seal_receipt
from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal as ReceiptJournal
from services.web.application import create_app
from services.web.nfl_corrections import artifact_snapshot_payload
from .fakes import FakeStore, TEST_KEY
from .publication_fixture import graph

OPERATOR = TEST_KEY
INGEST = "test-nfl-ingest-credential-" + "a" * 32
OUTCOME = "test-nfl-outcome-credential-" + "b" * 32
CORRECTIONS = "test-nfl-corrections-credential-" + "c" * 32
COLLECTION = "test-nfl-collection-credential-" + "d" * 32
COLLECTION_PATH = "/api/v1/nfl/collection-intents"


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
    # Build actual M1-shaped immutable captures and separate publication proof;
    # an enum-only generic receipt is no longer a trusted fitting fixture.
    fixture = Path(__file__).resolve().parents[2] / "packages/selflearn-core/tests/publication_fit_fixture.py"
    seed = runpy.run_path(str(fixture))["seed_publication_journal"]
    return seed(journal, n_per=n_per)


def binder_artifact(journal, *, fit_delay=timedelta(), artifact_version=1):
    # trained_through must be after DB recorded_utc of revisions (wall clock).
    cutoff = datetime.now(timezone.utc)
    return fit_corrections_from_journal(
        journal,
        trained_through=cutoff.isoformat(),
        expires_at=(cutoff + timedelta(days=1)).isoformat(),
        fitted_at=(cutoff + fit_delay).isoformat(),
        min_graded_total=150,
        min_graded_per_market=50,
        artifact_version=artifact_version,
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


def test_legitimate_v1_journal_binder_artifact_is_read_only(tmp_path):
    path = tmp_path / "binder.sqlite"
    with ReceiptJournal(path) as journal:
        seeded = populate_verified_journal(journal, n_per=60)
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
        body = request_body()
        body["baseline_model_version"] = seeded["receipts"][0]["base_model_version"]
        response = post(client, body)
        assert response.status_code == 200, response.text
        value = response.json()
        assert value["artifact"]["artifact_id"] == art["artifact_id"]
        assert_baseline(value, "missing_baseline_lineage")


def lineage_fixture(path, *, delay=timedelta()):
    with ReceiptJournal(path) as journal:
        populate_verified_journal(journal, n_per=50)
        artifact = binder_artifact(journal, fit_delay=delay, artifact_version=2)
        assert artifact["n_graded"] == 150
        assert authorize_corrections_artifact(journal, artifact) == artifact
        journal.append_object("source_snapshot", artifact["artifact_id"], artifact_snapshot_payload(artifact))
    return artifact


def lineage_client(path, artifact, *, mode="on", collection=True):
    env = env_for(mode, artifact_id=artifact["artifact_id"])
    if collection:
        env.update(SELFLEARN_NFL_COLLECTION_MODE="record", SELFLEARN_NFL_COLLECTION_REPOSITORY="fixture/nfl2026",
                   SELFLEARN_NFL_COLLECTION_TOKEN=COLLECTION)
    return TestClient(create_app(store=FakeStore(), env=env, journal_factory=lambda: ReceiptJournal(path),
                                 collection_handoff_store=SQLiteCollectionHandoff))


def scoped_post(client, path, payload, token, expected=200):
    response = client.post(path, json=payload, headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected, response.text
    return response.json()


def original_request(client, *, run_id="1001", variant="matching", finalize=True):
    now = datetime.now(timezone.utc)
    kickoff = (now + (timedelta(minutes=-1) if variant == "late" else timedelta(hours=1))).isoformat()
    doc, sources = graph(kickoff)
    gen = doc["generations"][0]
    pool = sources["leg_pool"]
    first = pool["players"][0]
    if variant == "mixed":
        other = deepcopy(first)
        other["gsis_id"] = "fixture-other-player"
        pool["players"].append(other)
        doc["receipts"].append(deepcopy(doc["receipts"][0]))
    for i, (player, row) in enumerate(zip(pool["players"], doc["receipts"])):
        source = player["rungs"][0]["learning_receipt"]
        source["player_id"] = player["gsis_id"]
        row["leg"]["gsis_id"] = player["gsis_id"]
        if variant == "base":
            source["base_model_version"] = pool["base_model_version"] = fingerprint("untrained base")
        if variant == "calibrator" or (variant == "mixed" and i == 1):
            source["calibrator"] = {"a": 0, "b": 1}
            source["calibrator_version"] = fingerprint(source["calibrator"])
        source["contract_id"] = fingerprint(list(canonical_key(source, doc["season"])))
        source["receipt_id"] = fingerprint({k: v for k, v in source.items() if k != "receipt_id"})
        row.update(learning_receipt=deepcopy(source), receipt_id=source["receipt_id"])
    gen["offered_cards"] = []
    gen["source_snapshots"] = {name: fingerprint(value) for name, value in sources.items()}
    gen["receipt_ids"] = sorted(row["receipt_id"] for row in doc["receipts"])
    gen["generation_id"] = fingerprint({key: gen[key] for key in MANIFEST_KEYS})
    for row in doc["receipts"]:
        row["generation_id"] = gen["generation_id"]
    doc["envelope_id"] = fingerprint({k: v for k, v in doc.items() if k != "envelope_id"})
    reference = {"workflow": "daily.yml", "run_id": run_id, "run_attempt": "1",
                 "generation_object_id": doc["envelope_id"], "generation_id": gen["generation_id"]}
    request = {"version": 1, **reference, "recorded_utc": gen["recorded_utc"], "latest_kickoff_utc": kickoff,
               "source_snapshots": gen["source_snapshots"]}
    intent = scoped_post(client, COLLECTION_PATH, request, COLLECTION)["intent"]
    reference["intent_id"] = intent["intent_id"]
    objects = [("source_snapshot", fingerprint(value), value) for value in sources.values()]
    objects.append(("generation", doc["envelope_id"], doc))
    for kind, identity, value in objects:
        scoped_post(client, "/api/v1/nfl/objects", {"version": 1, "kind": kind,
            "object_id": identity, "payload": value}, INGEST)
    if finalize:
        ack = scoped_post(client, "/api/v1/nfl/generations", {"version": 1, "object_id": doc["envelope_id"]}, INGEST)
        assert ack["durable"] and not ack["rejections"] and len(ack["receipts"]) == len(doc["receipts"])
    source = first["rungs"][0]["learning_receipt"]
    leg = {"leg_id": "L1", "game_id": source["game_id"], "market": source["market"],
           "selection": source["direction"], "baseline_prob": source["served_prob"],
           "source_receipt_id": source["receipt_id"], "contract_id": source["contract_id"]}
    return {"schema_version": 2, "request_id": "qualified-correction", "season": doc["season"], "week": doc["week"],
            "generated_at": gen["pool_generated_utc"], "baseline_model_version": source["base_model_version"],
            "calibrator_version": source["calibrator_version"], "baseline_generation": reference, "legs": [leg],
            "parlays": [{"parlay_id": "P1", "leg_ids": ["L1"], "baseline_prob": 0.4}]}


def assert_baseline(value, reason):
    for row in value["legs"] + value["parlays"]:
        assert row["reason"] == reason
        assert row["applied"] is False
        assert row["adjusted_prob"] == row["baseline_prob"]
        assert row["delta"] == 0


def test_authorized_v2_original_matching_lineage_applies_only_legs(tmp_path):
    path = tmp_path / "matching.sqlite"
    art = lineage_fixture(path)
    with lineage_client(path, art) as client:
        body = original_request(client)
        response = post(client, body)
    assert response.status_code == 200
    value = response.json()
    assert value["schema_version"] == 2
    assert value["artifact"]["lineage"] == art["lineage"]
    assert value["legs"][0]["applied"] is True, value
    assert value["legs"][0]["reason"] == "learned_segment_bias"
    assert value["legs"][0]["adjusted_prob"] < body["legs"][0]["baseline_prob"]
    row = value["parlays"][0]
    assert row["reason"] == "unverified_parlay_baseline" and row["applied"] is False
    assert row["adjusted_prob"] == row["baseline_prob"] == 0.4
    with lineage_client(path, art, mode="shadow") as client:
        assert_baseline(post(client, body).json(), "shadow_baseline")


@pytest.mark.parametrize(("variant", "reason"), [
    ("base", "incompatible_baseline_lineage"), ("calibrator", "incompatible_baseline_lineage"),
    ("mixed", "mixed_serving_lineage"), ("late", "late_baseline_generation"),
])
def test_actual_serving_authority_holds_mismatched_or_late_lineage(tmp_path, variant, reason):
    path = tmp_path / "hold.sqlite"
    art = lineage_fixture(path)
    with lineage_client(path, art) as client:
        body = original_request(client, variant=variant)
        assert body["legs"][0]["baseline_prob"] == 0.62
        response = post(client, body)
    assert response.status_code == 200
    assert_baseline(response.json(), reason)


def test_scoped_request_must_resolve_exact_frozen_receipt_and_terms(tmp_path):
    path = tmp_path / "references.sqlite"
    art = lineage_fixture(path)
    with lineage_client(path, art) as client:
        body = original_request(client)
        with ReceiptJournal(path) as journal:
            envelope_id = body["baseline_generation"]["generation_object_id"]
            original_pool_id = journal.get_object(envelope_id)["payload"]["generations"][0]["source_snapshots"]["leg_pool"]
        scoped_post(client, "/api/v1/nfl/objects", {"version": 1, "kind": "source_snapshot",
            "object_id": original_pool_id, "payload": {"forged_pool": True}}, INGEST, expected=422)
        for field, value, reason in (
            ("baseline_model_version", fingerprint("caller declaration"), "incompatible_baseline_lineage"),
            ("calibrator_version", None, "missing_baseline_lineage"),
            ("generated_at", "2026-01-01T00:00:00Z", "unverified_serving_lineage"),
            ("baseline_generation", {**body["baseline_generation"], "generation_id": fingerprint("wrong")},
             "unverified_serving_lineage"),
        ):
            assert_baseline(post(client, {**body, field: value}).json(), reason)
        for field, value in (("source_receipt_id", fingerprint("foreign receipt")),
                             ("contract_id", fingerprint("foreign contract")), ("game_id", "G-other"),
                             ("market", "rb_rush_yds"), ("selection", "under"), ("baseline_prob", 0.59)):
            wrong = {**body, "legs": [{**body["legs"][0], field: value}]}
            assert_baseline(post(client, wrong).json(), "unverified_baseline_receipt")
        assert_baseline(post(client, {**body, "legs": [body["legs"][0],
            {**body["legs"][0], "leg_id": "L2"}]}).json(), "duplicate_baseline_receipt")
        raw_only = original_request(client, run_id="1002", finalize=False)
        assert_baseline(post(client, raw_only).json(), "unverified_serving_lineage")
    with lineage_client(path, art, collection=False) as client:
        assert_baseline(post(client, body).json(), "missing_baseline_lineage")
    assert_baseline(plan_corrections(body, mode="on", artifact=art, binder_verified=True), "missing_baseline_lineage")

    edited = artifact_snapshot_payload(art)
    edited["fit_inputs_hash"] = fingerprint("forged training cohort")
    forged = {**edited, "artifact_id": fingerprint(edited)}
    store_artifact(path, forged)
    with lineage_client(path, forged) as client:
        assert_baseline(post(client, body).json(), "unverified_serving_lineage")

def test_authorized_future_v2_fit_echoes_baseline(tmp_path):
    path = tmp_path / "future.sqlite"
    art = lineage_fixture(path, delay=timedelta(hours=2))
    with lineage_client(path, art) as client:
        body = original_request(client)
    for mode in ("on", "shadow"):
        with lineage_client(path, art, mode=mode) as client:
            response = post(client, body)
        assert response.status_code == 200
        value = response.json()
        assert value["artifact"]["artifact_id"] == art["artifact_id"]
        assert_baseline(value, "artifact_not_yet_fitted")


def test_invalid_body_is_422(tmp_path):
    client, _ = client_for(tmp_path / "bad.sqlite", mode="on")
    with client:
        assert post(client, {"schema_version": 1}).status_code == 422
