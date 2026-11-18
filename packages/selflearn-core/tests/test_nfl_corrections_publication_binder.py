"""Publication is independent evidence on unchanged M1 forecast identities."""
import copy
import hashlib
import json
from datetime import timedelta
from unittest.mock import patch

import pytest

from publication_fit_fixture import FIXTURES, PUBLICATION_TIME, observed_revision, seed_publication_journal
from selflearn_core.adapters.nfl import adapt_prospective
from selflearn_core.adapters.nfl_generation import MANIFEST_KEYS
from selflearn_core.adapters.nfl_corrections_fit import (
    authorize_corrections_artifact, fit_corrections_from_journal,
)
from selflearn_core.adapters.nfl_corrections_policy import CorrectionsError, TRUSTED_ARTIFACT_SOURCE
from selflearn_core.probability_receipts import fingerprint, instant, seal_receipt
from selflearn_core.adapters.nfl_publication_journal import NFLPublicationJournal as ReceiptJournal

CUTOFF = "2026-09-03T00:00:00Z"


class Snapshot:
    def __init__(self, value):
        self.value = value
        self.reads = 0

    def read_publication_fit_snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.value)

    def receipts(self):
        raise AssertionError("must not mix live readers with the pinned snapshot")

    resolution_history = receipts


@pytest.fixture
def snapshot():
    with ReceiptJournal(":memory:") as journal:
        seed_publication_journal(journal)
        value = journal.read_publication_fit_snapshot()
    # Trusted synthetic DB clocks on a detached snapshot allow exact cutoff
    # checks without modifying persisted receipt/proof bytes or the wall clock.
    for gen in value["generation_records"]:
        gen["recorded_utc"] = "2026-09-01T14:01:01Z"
    for rev in value["revisions"]:
        rev["recorded_utc"] = "2026-09-02T05:00:00Z"
    return value


def fit(value, cutoff=CUTOFF):
    return fit_corrections_from_journal(Snapshot(value), trained_through=cutoff,
        expires_at="2099-01-01T00:00:00Z", fitted_at=cutoff,
        min_graded_total=1, min_graded_per_market=1)


def reseal(proof):
    proof["attestation_id"] = fingerprint({k:v for k,v in proof.items() if k != "attestation_id"})


def revision_in(snapshot, receipt_id, *, recorded_utc="2026-09-02T05:00:00Z", **kwargs):
    receipt = next(row for row in snapshot["receipts"] if row["receipt_id"] == receipt_id)
    source = next(row["learning_receipt"] for record in snapshot["generation_records"]
                  for row in record["payload"]["receipts"] if row["receipt_id"] == receipt["source_receipt_id"])
    return {"version": 1, "recorded_utc": recorded_utc, **observed_revision(receipt, source, **kwargs)}


def test_real_m1_shape_fits_only_after_independent_proof_without_receipt_upgrade(snapshot):
    originals = copy.deepcopy(snapshot["receipts"])
    assert all(r["publication_status"] == "generated_unverified" for r in originals)
    assert all("direction" not in r["features"] and "selection" not in r["features"] for r in originals)
    unattested = {**snapshot, "publication_attestations":[]}
    assert fit(unattested)["n_graded"] == 0
    binder = Snapshot(snapshot)
    artifact = fit_corrections_from_journal(binder, trained_through=CUTOFF,
        expires_at="2099-01-01T00:00:00Z", fitted_at=CUTOFF,
        min_graded_total=1, min_graded_per_market=1)
    assert binder.reads == 1
    assert artifact["source"] == TRUSTED_ARTIFACT_SOURCE
    assert artifact["n_graded"] == 3
    assert artifact["fit_audit"] == {"version": 1, "label_policy": "stable_player_ids_exact_fixture_v2",
        "forecast_cohort_count": 3, "graded_count": 3, "excluded_count": 0, "exclusion_reasons": {}}
    assert set(artifact["segments"]) == {m + "|over" for m in ("qb_pass_yds","rb_rush_yds","wr_rec_yds")}
    assert snapshot["receipts"] == originals


def seed_producer_shaped_generation(journal):
    """Issue a fresh synthetic wire graph with actual NFL v1 omitted leg fields.

    Complete source learning receipts retain their canonical direction. Only
    redundant player/rung/leg fields are omitted, as the real producer emits.
    Every changed source/generation is independently hashed and finalized.
    """
    with ReceiptJournal(":memory:") as templates:
        material = seed_publication_journal(templates, publish=False)
    doc, pool = copy.deepcopy(material["doc"]), copy.deepcopy(material["pool"])
    for player in pool["players"]:
        for field in ("direction", "comparison_operator"):
            player.pop(field, None)
        for rung in player["rungs"]:
            for field in ("direction", "comparison_operator"):
                rung.pop(field, None)
    gen = doc["generations"][0]
    gen["source_snapshots"]["leg_pool"] = fingerprint(pool)
    gen["generation_id"] = fingerprint({key:gen[key] for key in MANIFEST_KEYS})
    for row in doc["receipts"]:
        row["generation_id"] = gen["generation_id"]
        for field in ("direction", "comparison_operator"):
            row["leg"].pop(field, None)
    doc["envelope_id"] = fingerprint({key:value for key,value in doc.items() if key != "envelope_id"})
    schedule = json.loads((FIXTURES / "source_snapshot_schedule.json").read_text())["payload"]
    for payload in (pool, schedule, None):
        journal.append_object("source_snapshot", fingerprint(payload), payload)
    journal.append_object("generation", doc["envelope_id"], doc)
    batch = adapt_prospective(journal_docs=[doc])
    assert not batch["rejections"] and len(batch["receipts"]) == 3
    journal.commit_generation(doc["envelope_id"], batch["receipts"], [])
    observation = {"version":1, "policy":{"version":1,"kind":"nfl.pool_http_publication.v1",
        "origin":"https://publication-fixture.example", "path":"/data/leg_pool.json"},
        "observed_started_utc":"2026-09-01T14:02:00Z", "observed_completed_utc":"2026-09-01T14:02:01Z",
        "http_status":200, "body_utf8":json.dumps(pool)}
    with patch.object(journal, "_publication_recorded_utc", return_value=PUBLICATION_TIME):
        journal.commit_publication_attestation(doc["envelope_id"], observation)
    sources = {row["receipt_id"]: row["learning_receipt"] for row in doc["receipts"]}
    for receipt in batch["receipts"]:
        journal.append_resolution(**observed_revision(receipt, sources[receipt["source_receipt_id"]], hit=False))


def test_real_producer_omitted_leg_fields_fit_after_actual_finalize_publication_and_outcome():
    with ReceiptJournal(":memory:") as journal:
        seed_producer_shaped_generation(journal)
        before = journal.read_publication_fit_snapshot()
        assert all("direction" not in row["leg"] and "comparison_operator" not in row["leg"]
                   for row in before["generation_records"][0]["payload"]["receipts"])
        assert all("direction" not in row["features"] and "selection" not in row["features"]
                   for row in before["receipts"])
        artifact = fit_corrections_from_journal(journal, trained_through="2099-01-01T00:00:00Z",
            fitted_at="2099-01-01T01:00:00Z", expires_at="2099-02-01T00:00:00Z",
            min_graded_total=1, min_graded_per_market=1)
        assert artifact["n_graded"] == 3
        assert set(artifact["segments"]) == {market + "|over" for market in
            ("qb_pass_yds", "rb_rush_yds", "wr_rec_yds")}
        authorize_corrections_artifact(journal, artifact)
        assert journal.read_publication_fit_snapshot() == before


@pytest.mark.parametrize("field,value", [("direction","under"), ("direction",None),
    ("comparison_operator","lte"), ("comparison_operator",None)])
def test_omitted_leg_defaults_never_override_explicit_conflicting_or_null_fields(field, value):
    with ReceiptJournal(":memory:") as journal:
        seed_producer_shaped_generation(journal)
        original = journal.read_publication_fit_snapshot()
    # Detached adversarial snapshot only: persisted history is never rewritten.
    broken = copy.deepcopy(original)
    generation = broken["generation_records"][0]
    doc = generation["payload"]
    for row in doc["receipts"]:
        row["leg"][field] = value
    doc["envelope_id"] = fingerprint({key:child for key,child in doc.items() if key != "envelope_id"})
    generation["object_id"] = doc["envelope_id"]
    generation["ack"]["object_id"] = doc["envelope_id"]
    proof = broken["publication_attestations"][0]
    proof["object_id"] = doc["envelope_id"]
    reseal(proof)
    before = copy.deepcopy(broken)
    assert fit(broken, cutoff="2099-01-01T00:00:00Z")["n_graded"] == 0
    assert broken == before


@pytest.mark.parametrize("status", ["generated_unverified", "verified_publication"])
def test_publication_enum_is_never_independent_authority(snapshot, status):
    snapshot["publication_attestations"] = []
    snapshot["receipts"] = [seal_receipt({**{k:v for k,v in r.items() if k != "receipt_id"},
        "publication_status":status}) for r in snapshot["receipts"]]
    assert fit(snapshot)["n_graded"] == 0


@pytest.mark.parametrize("fault", ["generation_after_cutoff", "proof_after_cutoff", "revision_after_cutoff",
    "pool_reference", "generic_reference", "source_reference", "direction", "served_probability", "partition"])
def test_late_or_unbound_evidence_cannot_authorize_a_forecast(snapshot, fault):
    proof = snapshot["publication_attestations"][0]
    if fault == "generation_after_cutoff":
        snapshot["generation_records"][0]["recorded_utc"] = CUTOFF
    elif fault == "proof_after_cutoff":
        proof["recorded_utc"] = CUTOFF
    elif fault == "revision_after_cutoff":
        for rev in snapshot["revisions"]:
            rev["recorded_utc"] = CUTOFF
    elif fault == "pool_reference":
        proof["source_pool_id"] = fingerprint("other pool")
    elif fault == "generic_reference":
        proof["receipts"][0]["receipt_id"] = fingerprint("other generic receipt")
    elif fault == "source_reference":
        proof["receipts"][0]["source_receipt_id"] = fingerprint("other source receipt")
        proof["receipts"].sort(key=lambda r:r["source_receipt_id"])
    elif fault == "direction":
        for row in proof["receipts"]:
            row["direction"] = "under"
    elif fault == "served_probability":
        for row in proof["receipts"]:
            row["served_prob"] = .01
    elif fault == "partition":
        proof["receipts"].pop()
    reseal(proof)
    assert fit(snapshot)["n_graded"] == 0, fault


def test_revision_asof_and_identity_provenance_change_hash_without_changing_measurements(snapshot):
    first = fit(snapshot)
    changed = copy.deepcopy(snapshot)
    original = changed["revisions"][0]["resolution"]
    changed["revisions"][0] = revision_in(changed, original["receipt_id"], hit=original["hit"],
                                          observed_utc="2026-09-02T04:01:00Z")
    second = fit(changed)
    assert first["segments"] == second["segments"]
    assert first["fit_inputs_hash"] != second["fit_inputs_hash"]
    changed = copy.deepcopy(snapshot)
    proof = changed["publication_attestations"][0]
    proof["policy"]["origin"] = "https://independent-publication.example"
    proof["policy_id"] = fingerprint(proof["policy"])
    reseal(proof)
    third = fit(changed)
    assert first["segments"] == third["segments"]
    assert first["fit_inputs_hash"] != third["fit_inputs_hash"]
    assert first["artifact_id"] != third["artifact_id"]


def test_future_correction_and_duplicate_publication_do_not_reweight_original_forecasts(snapshot):
    first = fit(snapshot)
    previous = snapshot["revisions"][0]
    correction = revision_in(snapshot, previous["resolution"]["receipt_id"], hit=False,
        observed_utc="2026-09-10T04:00:00Z", recorded_utc="2026-09-10T05:00:00Z", prior=previous)
    snapshot["revisions"].append(correction)
    snapshot["publication_attestations"].append(copy.deepcopy(snapshot["publication_attestations"][0]))
    assert fit(snapshot) == first
    later = fit(snapshot, cutoff="2026-09-11T00:00:00Z")
    assert later["n_graded"] == first["n_graded"]
    assert later["segments"] != first["segments"]


def test_frozen_cohort_counts_legacy_unresolved_and_qualified_voids_without_training_on_them(snapshot):
    legacy, void, _ = [row["resolution"]["receipt_id"] for row in snapshot["revisions"]]
    snapshot["revisions"] = [revision_in(snapshot, legacy, hit=True, legacy=True),
                             revision_in(snapshot, void, hit=None)]
    artifact = fit(snapshot)
    assert artifact["n_graded"] == 0
    assert artifact["segments"] == {}
    assert artifact["sufficient_total"] is False
    assert artifact["fit_audit"]["forecast_cohort_count"] == 3
    assert artifact["fit_audit"]["excluded_count"] == 3
    assert artifact["fit_audit"]["exclusion_reasons"] == {
        "legacy_outcome": 1, "unresolved_outcome": 1, "void_outcome": 1}


@pytest.mark.parametrize("fault", ["label_string_only", "missing_crosswalk", "different_player",
    "missing_component", "crosswalk_observation_id_mismatch", "crosswalk_clock_mismatch"])
def test_unqualified_identity_evidence_stays_excluded_in_fixed_cohort(snapshot, fault):
    resolution = snapshot["revisions"][0]["resolution"]
    trace = resolution["producer_trace"]
    if fault == "label_string_only":
        resolution["producer_trace"] = {"identity_join_policy": trace["identity_join_policy"]}
    elif fault == "missing_crosswalk":
        del trace["identity_crosswalk"]
    elif fault == "different_player":
        trace["identity_crosswalk"]["player_id"] = "different player"
    elif fault == "missing_component":
        del resolution["source_observations"]["nflverse.rosters"]
    elif fault == "crosswalk_observation_id_mismatch":
        trace["identity_crosswalk"]["observation_id"] = fingerprint("unbound observation")
    elif fault == "crosswalk_clock_mismatch":
        trace["identity_crosswalk"]["observed_utc"] = "2026-09-02T03:59:00Z"
    artifact = fit(snapshot)
    assert artifact["n_graded"] == 2
    assert artifact["fit_audit"]["forecast_cohort_count"] == 3
    assert artifact["fit_audit"]["exclusion_reasons"] == {"unqualified_outcome": 1}


def test_same_legacy_measurement_cannot_be_requalified_by_new_evidence(snapshot):
    rid = snapshot["revisions"][0]["resolution"]["receipt_id"]
    legacy = revision_in(snapshot, rid, hit=True, legacy=True)
    snapshot["revisions"][0] = legacy
    first = fit(snapshot)
    assert first["n_graded"] == 2
    assert first["fit_audit"]["exclusion_reasons"] == {"legacy_outcome": 1}
    replay = revision_in(snapshot, rid, hit=True, prior=legacy,
                         observed_utc="2026-09-02T06:00:00Z", recorded_utc="2026-09-02T06:01:00Z")
    assert replay["revision_id"] == legacy["revision_id"]
    assert replay["resolution"] == legacy["resolution"]

    # A claimed new revision carrying the same numeric label must fail even
    # if a malformed snapshot attempts to append it with a valid content ID.
    appended = revision_in(snapshot, rid, hit=True,
                            observed_utc="2026-09-02T06:00:00Z", recorded_utc="2026-09-02T06:01:00Z")
    appended.update(supersedes=legacy["revision_id"], reason="same value")
    appended["revision_id"] = "nfl.observed_resolution_v2:" + fingerprint({"receipt_id": rid,
        "observation_id": appended["resolution"]["evidence_observation_id"], "supersedes": legacy["revision_id"]})
    snapshot["revisions"].append(appended)
    assert fit(snapshot)["fit_audit"]["exclusion_reasons"] == {"unchanged_outcome_revision": 1}

    corrected = revision_in(snapshot, rid, hit=False, prior=legacy,
                             observed_utc="2026-09-02T06:00:00Z", recorded_utc="2026-09-02T06:01:00Z")
    snapshot["revisions"][-1] = corrected
    assert fit(snapshot)["n_graded"] == 3


def test_prequalification_authorization_artifacts_do_not_replay(snapshot):
    artifact = fit(snapshot)
    legacy = {k: v for k, v in artifact.items() if k not in ("fit_audit", "artifact_id")}
    legacy["artifact_id"] = fingerprint(legacy)
    with pytest.raises(CorrectionsError, match="trusted_artifact_journal_mismatch"):
        authorize_corrections_artifact(Snapshot(snapshot), legacy)


def test_fit_is_order_independent_and_legacy_enum_binder_source_is_not_authorized(snapshot):
    artifact = fit(snapshot)
    shuffled = copy.deepcopy(snapshot)
    for key in ("receipts", "generation_records", "revisions", "publication_attestations"):
        shuffled[key].reverse()
    assert fit(shuffled) == artifact
    legacy = {**artifact, "source":"receipt_journal_as_of_v1"}
    legacy["artifact_id"] = fingerprint({k:v for k,v in legacy.items() if k != "artifact_id"})
    with pytest.raises(CorrectionsError, match="trusted_source_required"):
        authorize_corrections_artifact(Snapshot(snapshot), legacy)


def test_missing_pinned_publication_port_fails_closed():
    class Legacy:
        def receipts(self):
            raise AssertionError("must not trust the legacy enum-only reader")
    with pytest.raises(CorrectionsError, match="publication_snapshot_required"):
        fit_corrections_from_journal(Legacy(), trained_through=CUTOFF,
            expires_at="2099-01-01T00:00:00Z", fitted_at=CUTOFF)


def refresh_vintage(original, seconds):
    """Issue another genuinely sealed source/generation for the same contracts.

    Use the actual NFL adapter to create the new generic identities. These are
    detached trusted journal fixtures, not fabricated upgrades of old receipts.
    """
    def shift(value):
        return (instant(value) + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")
    record = copy.deepcopy(original["generation_records"][0])
    doc = record["payload"]; gen = doc["generations"][0]
    pool = json.loads((FIXTURES / "source_snapshot_leg_pool.json").read_text())["payload"]
    template = pool["players"][0]
    players = []
    gen["pool_generated_utc"] = shift(gen["pool_generated_utc"])
    gen["recorded_utc"] = shift(gen["recorded_utc"])
    pool["generated_utc"] = gen["pool_generated_utc"]
    for row in doc["receipts"]:
        source = row["learning_receipt"]
        source["forecast_created_utc"] = shift(source["forecast_created_utc"])
        source["receipt_id"] = fingerprint({k:v for k,v in source.items() if k != "receipt_id"})
        row.update(receipt_id=source["receipt_id"], recorded_utc=gen["recorded_utc"])
        player = copy.deepcopy(template)
        player.update(gsis_id=source["player_id"], market=source["market"],
            position={"qb_pass_yds":"QB", "rb_rush_yds":"RB", "wr_rec_yds":"WR"}[source["market"]])
        player["rungs"] = [copy.deepcopy(template["rungs"][0])]
        player["rungs"][0]["learning_receipt"] = copy.deepcopy(source)
        players.append(player)
    pool["players"] = players
    gen["source_snapshots"]["leg_pool"] = fingerprint(pool)
    gen["receipt_ids"] = sorted(row["receipt_id"] for row in doc["receipts"])
    gen["generation_id"] = fingerprint({k:gen[k] for k in MANIFEST_KEYS})
    for row in doc["receipts"]:
        row["generation_id"] = gen["generation_id"]
    doc["receipts"].sort(key=lambda row:row["receipt_id"])
    doc["envelope_id"] = fingerprint({k:v for k,v in doc.items() if k != "envelope_id"})
    batch = adapt_prospective(journal_docs=[doc])
    assert not batch["rejections"]
    receipts = batch["receipts"]
    record.update(object_id=doc["envelope_id"], recorded_utc=shift(record["recorded_utc"]),
        ack={"version":1, "object_id":doc["envelope_id"], "durable":True, "rejections":[],
             "receipts":[{"receipt_id":r["receipt_id"], "source_receipt_id":r["source_receipt_id"],
                          "status":"recorded"} for r in receipts]})
    proof = copy.deepcopy(original["publication_attestations"][0])
    proof.update(object_id=doc["envelope_id"], generation_id=gen["generation_id"],
        source_pool_id=fingerprint(pool), recorded_utc=shift(proof["recorded_utc"]))
    for key in ("observed_started_utc", "observed_completed_utc"):
        proof["observation"][key] = shift(proof["observation"][key])
    raw = json.dumps(pool).encode()
    proof["observation"].update(body_sha256="sha256:"+hashlib.sha256(raw).hexdigest(), body_bytes=len(raw))
    sources = {r["receipt_id"]:r["learning_receipt"] for r in doc["receipts"]}
    proof["receipts"] = sorted([{"receipt_id":r["receipt_id"], "source_receipt_id":r["source_receipt_id"],
        "market":sources[r["source_receipt_id"]]["market"], "direction":"over",
        "served_prob":r["served_prob"], "event_start_utc":r["event_start_utc"]} for r in receipts],
        key=lambda row:row["source_receipt_id"])
    reseal(proof)
    revisions = []
    old = {r["receipt_id"]:r for r in original["receipts"]}
    for receipt in receipts:
        prior = next(r for r in original["revisions"] if
            old[r["resolution"]["receipt_id"]]["contract_id"] == receipt["contract_id"])
        revisions.append({"version": 1, "recorded_utc": prior["recorded_utc"],
            **observed_revision(receipt, sources[receipt["source_receipt_id"]], hit=prior["resolution"]["hit"])})
    return {"receipts":receipts, "generation_records":[record], "revisions":revisions,
            "publication_attestations":[proof]}


def append_vintage(snapshot, vintage):
    for key, rows in vintage.items():
        snapshot[key].extend(rows)


def test_refresh_vintages_cannot_satisfy_sample_floors(snapshot):
    original = copy.deepcopy(snapshot)
    for seconds in range(1, 68):
        append_vintage(snapshot, refresh_vintage(original, seconds))
    assert len(snapshot["receipts"]) == 204
    assert len({(r["contract_id"], r["event_id"]) for r in snapshot["receipts"]}) == 3
    artifact = fit_corrections_from_journal(Snapshot(snapshot), trained_through=CUTOFF,
        fitted_at=CUTOFF, expires_at="2099-01-01T00:00:00Z")
    assert artifact["n_graded"] == 3
    assert artifact["sufficient_total"] is False
    assert all(segment["n_eligible"] == 1 and not segment["eligible_for_application"]
               for segment in artifact["segments"].values())
    # The later refreshes neither alter sample weights nor chosen provenance.
    assert fit(snapshot) == fit(original)


def test_later_resolved_vintage_cannot_replace_earlier_unresolved_contract(snapshot):
    first = copy.deepcopy(snapshot)
    later = refresh_vintage(first, 60)
    snapshot["revisions"] = []
    append_vintage(snapshot, later)
    assert len(snapshot["revisions"]) == 3
    assert fit(snapshot)["n_graded"] == 0
    # Resolving the original produces one sample per contract, never six.
    snapshot["revisions"].extend(first["revisions"])
    assert fit(snapshot)["n_graded"] == 3
    assert fit(snapshot) == fit(first)


def test_multiple_attestations_for_same_original_do_not_change_contract_selection(snapshot):
    original = fit(snapshot)
    # A later finalized generation can retain the exact original receipts while
    # identifying a different correlation/selection-policy context.
    generation = copy.deepcopy(snapshot["generation_records"][0])
    doc = generation["payload"]; gen = doc["generations"][0]
    gen["joint_model_version"] = fingerprint("another frozen joint policy")
    gen["generation_id"] = fingerprint({k:gen[k] for k in MANIFEST_KEYS})
    for row in doc["receipts"]:
        row["generation_id"] = gen["generation_id"]
    doc["envelope_id"] = fingerprint({k:v for k,v in doc.items() if k != "envelope_id"})
    generation["object_id"] = doc["envelope_id"]
    generation["ack"]["object_id"] = doc["envelope_id"]
    snapshot["generation_records"].append(generation)
    second = copy.deepcopy(snapshot["publication_attestations"][0])
    second.update(object_id=doc["envelope_id"], generation_id=gen["generation_id"])
    second["policy"]["origin"] = "https://another-publication.example"
    second["policy_id"] = fingerprint(second["policy"])
    second["recorded_utc"] = "2026-09-01T14:03:00Z"
    reseal(second)
    snapshot["publication_attestations"].append(second)
    append_vintage(snapshot, refresh_vintage(snapshot, 60))
    assert fit(snapshot) == original
