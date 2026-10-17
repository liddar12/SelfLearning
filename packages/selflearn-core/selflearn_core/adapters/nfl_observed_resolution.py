"""NFL outcomes observed after FINAL, without invented provider update clocks.

Pure normalized-evidence boundary. The caller must join authenticated raw source
captures to frozen pool identities; IDs and caller clocks alone do not prove
authenticity. These are observed provider measurements, not guarantees that a
provider has published its last correction or that a sportsbook settled a bet.
The strict provider-clock resolver remains a different, unchanged interface.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import copy
import re

from .nfl import canonical_key, validate_nfl_receipt
from .nfl_resolution import FINAL_STATUSES, _history, _proposal
from ..outcome_revisions import validate_journal_resolution
from ..probability_receipts import finite_number, fingerprint, instant, validate_probability_receipt

BRIDGE_VERSION = "nfl.observed_resolution_v2"
DNP_BASIS = "r106_team_snap_absence_inference"
STAT_SOURCE = "nflverse.stats_player"
SNAP_SOURCE = "nflverse.snap_counts"
GAME_SOURCE = "espn.scoreboard"
OBSERVATION_KIND = "nfl.observed_measurement.v1"
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_PARITY = ("base_prob", "served_prob", "base_model_version", "calibrator_version",
           "forecast_created_utc", "source_snapshot_id", "source_timestamps", "features", "publication_status")


def _text(value, reason):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(reason)
    return value


def _component(source, observation_id, observed_utc, observed):
    if not isinstance(observation_id, str) or not _HASH.fullmatch(observation_id):
        raise ValueError("missing_source_observation_id")
    if instant(observed_utc) > observed:
        raise ValueError("source_after_observation")
    return {"source": source, "observation_id": observation_id, "observed_utc": observed_utc}


def _source(receipt, source, leg):
    season, week = source.get("season"), source.get("week")
    if (type(season) is not int or type(week) is not int or not 1 <= week <= 18
            or type(leg.get("season")) is not int or type(leg.get("week")) is not int
            or leg["season"] != season or leg["week"] != week):
        raise ValueError("score_period_mismatch")
    normalized = dict(leg)
    normalized.setdefault("model_prob", source.get("served_prob"))
    key = validate_nfl_receipt(source, normalized, season, week)
    if canonical_key(leg, season) != key:
        raise ValueError("score_contract_mismatch")
    if (receipt["domain"] != "nfl" or receipt["task"] != "nfl.parlay_leg"
            or receipt["event_id"] != f"nfl:{season}:game:{key[1]}"
            or receipt["contract_id"] != source["contract_id"]
            or receipt["source_receipt_id"] != source["receipt_id"]):
        raise ValueError("persisted_source_identity_mismatch")
    for field in _PARITY:
        if fingerprint(receipt[field]) != fingerprint(source.get(field)):
            raise ValueError("persisted_source_" + field + "_mismatch")
    return key


def _evidence(receipt, source, leg, game, observed):
    key = _source(receipt, source, leg)
    if not isinstance(game, dict) or game.get("status") not in FINAL_STATUSES:
        raise ValueError("game_not_final")
    for field, expected in (("season", key[0]), ("week", source["week"]), ("game_id", key[1])):
        if type(game.get(field)) is not type(expected) or game[field] != expected:
            raise ValueError("final_game_identity_mismatch")
    kickoff = instant(game.get("kickoff_utc"))
    if kickoff != instant(receipt["event_start_utc"]):
        raise ValueError("final_game_kickoff_mismatch")
    for field in ("home", "away"):
        _text(game.get(field), "missing_final_team_identity")
    if game["home"] == game["away"]:
        raise ValueError("conflicting_final_team_identity")
    for field in ("home_score", "away_score"):
        if type(game.get(field)) is not int or game[field] < 0:
            raise ValueError("missing_final_score")
    if "team" in leg and leg["team"] != game[key[5]]:
        raise ValueError("frozen_team_game_mismatch")
    if "opp" in leg and leg["opp"] != game["away" if key[5] == "home" else "home"]:
        raise ValueError("frozen_opponent_game_mismatch")
    if game.get("source") != GAME_SOURCE:
        raise ValueError("unsupported_final_game_source")
    if leg.get("source") not in (STAT_SOURCE, SNAP_SOURCE):
        raise ValueError("unsupported_stat_source")
    if leg.get("authoritative") is not True or leg.get("status") not in FINAL_STATUSES:
        raise ValueError("missing_normalized_final_evidence")
    if any(leg.get(field) is not None for field in ("source_updated_utc", "source_revision_id")):
        raise ValueError("provider_clock_policy_not_observed")
    if any(game.get(field) is not None for field in ("final_utc", "source_updated_utc", "upstream_updated_utc")):
        raise ValueError("provider_clock_policy_not_observed")
    components = {
        GAME_SOURCE: _component(GAME_SOURCE, game.get("observation_id"), game.get("observed_utc"), observed),
        leg["source"]: _component(leg["source"], leg.get("observation_id"), leg.get("observed_utc"), observed),
    }
    if instant(game["observed_utc"]) < kickoff:
        raise ValueError("final_observed_before_kickoff")
    if instant(leg["observed_utc"]) < instant(game["observed_utc"]):
        raise ValueError("stat_observed_before_final_evidence")
    participation_fields = ("participation_source", "participation_observed_utc", "participation_observation_id")
    if any(field in leg for field in participation_fields):
        if leg.get("participation_source") != SNAP_SOURCE:
            raise ValueError("unsupported_participation_source")
        part = _component(SNAP_SOURCE, leg.get("participation_observation_id"), leg.get("participation_observed_utc"), observed)
        if instant(part["observed_utc"]) < instant(game["observed_utc"]):
            raise ValueError("participation_observed_before_final_evidence")
        if SNAP_SOURCE in components and fingerprint(components[SNAP_SOURCE]) != fingerprint(part):
            raise ValueError("conflicting_snap_observations")
        components[SNAP_SOURCE] = part
    if leg.get("result") == "void":
        if (leg.get("reason") != "did_not_play" or leg.get("played") is not False
                or leg.get("actual") is not None or leg["source"] != SNAP_SOURCE
                or leg.get("participation_basis") != DNP_BASIS):
            raise ValueError("unsupported_or_unexplained_observed_void")
        actual, status, hit, reason = None, "VOID", None, "did_not_play"
        basis = DNP_BASIS
    else:
        if leg.get("result") not in ("hit", "miss") or leg.get("played") is not True:
            raise ValueError("missing_binary_grade_or_participation")
        if leg["source"] != STAT_SOURCE or not finite_number(leg.get("actual")):
            raise ValueError("missing_actual_stat")
        actual = float(leg["actual"]) if leg["actual"] else 0.0
        hit = actual >= source["line"]
        if hit != (leg["result"] == "hit"):
            raise ValueError("grade_actual_mismatch")
        status, reason, basis = "FINAL", None, leg.get("participation_basis")
        if basis not in ("stat_row", "r106_positive_offensive_snaps_no_stat_row"):
            raise ValueError("invalid_participation_basis")
        if basis == "r106_positive_offensive_snaps_no_stat_row" and (actual != 0 or SNAP_SOURCE not in components):
            raise ValueError("missing_zero_stat_participation_evidence")
    measurement = {"version": 1, "canonical_contract": list(key), "week": source["week"],
                   "actual": actual, "played": leg["played"], "status": status,
                   "hit": hit, "reason": reason, "participation_basis": basis,
                   "game": {"season": key[0], "week": source["week"], "game_id": key[1],
                            "kickoff_utc": kickoff.isoformat(), "home": game["home"], "away": game["away"], "state": "FINAL"}}
    measurement_id = fingerprint(measurement)
    observation_id = fingerprint({"measurement_id": measurement_id, "components": components})
    resolved = max((component["observed_utc"] for component in components.values()), key=instant)
    value = {"receipt_id": receipt["receipt_id"], "contract_id": receipt["contract_id"],
             "event_id": receipt["event_id"], "status": status, "hit": hit, "actual": actual,
             "resolved_utc": resolved, "evidence_observed_utc": resolved,
             "source": leg["source"], "reason": reason, "authoritative": True,
             "final_evidence_status": "FINAL", "evidence_basis": BRIDGE_VERSION,
             "measurement_id": measurement_id, "measurement": measurement,
             "evidence_observation_id": observation_id, "source_observations": components,
             "observation_watermarks": copy.deepcopy(components),
             "game_evidence": {"status": game["status"], "kickoff_utc": game["kickoff_utc"],
                               "source": GAME_SOURCE, "observed_utc": game["observed_utc"],
                               "observation_id": game["observation_id"], "final_utc": None,
                               "source_updated_utc": None},
             "timing_limitations": {"upstream_updated_utc": None, "provider_final_revision_known": False},
             "participation_basis": basis}
    trace_keys = ("evidence_capture_id", "pool_snapshot_id", "pool_snapshot_ids", "generation_ids", "identity_join_policy",
                  "raw_row_digests", "stat_row_digest", "participation_row_digest", "participation_coverage",
                  "source_row_sha256", "evidence_quality")
    trace = {field: copy.deepcopy(leg[field]) for field in trace_keys if field in leg}
    if trace:
        fingerprint(trace)  # Trace remains JSON data, not trusted instructions.
        value["producer_trace"] = trace
    validate_journal_resolution(receipt, value)
    # Underlying stat identity ignores the selected rung/model vintage.
    scope = (key[0], key[1], key[2], key[3], key[5])
    state = fingerprint({k: measurement[k] for k in ("actual", "played", "status", "reason")})
    return value, scope, state


def _validate_observed_history(nodes, receipt):
    for node in nodes.values():
        value = node["resolution"]
        if value.get("evidence_basis") != BRIDGE_VERSION:
            continue
        if "source_updated_utc" in value or "source_revision_id" in value:
            raise ValueError("mixed_existing_clock_policy")
        if value.get("measurement_id") != fingerprint(value.get("measurement")):
            raise ValueError("existing_measurement_hash_mismatch")
        measurement = value["measurement"]
        if (not isinstance(measurement, dict) or type(measurement.get("version")) is not int
                or measurement["version"] != 1
                or fingerprint(measurement.get("canonical_contract")) != receipt["contract_id"]
                or any(fingerprint(measurement.get(field)) != fingerprint(value.get(field))
                       for field in ("actual", "status", "hit", "reason", "participation_basis"))):
            raise ValueError("existing_measurement_resolution_mismatch")
        components = value.get("source_observations")
        watermarks = value.get("observation_watermarks")
        if not isinstance(components, dict) or not isinstance(watermarks, dict) or not components:
            raise ValueError("missing_existing_observation_components")
        if value.get("evidence_observation_id") != fingerprint({"measurement_id": value["measurement_id"], "components": components}):
            raise ValueError("existing_observation_hash_mismatch")
        for source, component in watermarks.items():
            if source not in (STAT_SOURCE, SNAP_SOURCE, GAME_SOURCE) or not isinstance(component, dict):
                raise ValueError("invalid_existing_observation_watermark")
            expected = _component(source, component.get("observation_id"), component.get("observed_utc"), instant(value["evidence_observed_utc"]))
            if fingerprint(expected) != fingerprint(component):
                raise ValueError("invalid_existing_observation_watermark")
        for source, component in components.items():
            if source not in watermarks or fingerprint(component) != fingerprint(watermarks[source]):
                raise ValueError("missing_existing_observation_watermark")
        if GAME_SOURCE not in components or value.get("source") not in components:
            raise ValueError("missing_existing_required_observation")
        game_clock = instant(components[GAME_SOURCE]["observed_utc"])
        if any(instant(c["observed_utc"]) < game_clock for source, c in components.items() if source != GAME_SOURCE):
            raise ValueError("existing_stat_observed_before_final_evidence")
        if (value["resolved_utc"] != value["evidence_observed_utc"]
                or instant(value["resolved_utc"]) != max(instant(c["observed_utc"]) for c in components.values())):
            raise ValueError("existing_observation_clock_mismatch")
        expected_id = BRIDGE_VERSION + ":" + fingerprint({"receipt_id": receipt["receipt_id"],
            "observation_id": value["evidence_observation_id"], "supersedes": node["supersedes"]})
        if node["revision_id"] != expected_id:
            raise ValueError("existing_observation_revision_id_mismatch")
    for node in nodes.values():
        value, parent_id = node["resolution"], node["supersedes"]
        if value.get("evidence_basis") != BRIDGE_VERSION:
            continue
        if parent_id is None:
            if fingerprint(value["source_observations"]) != fingerprint(value["observation_watermarks"]):
                raise ValueError("existing_source_observation_watermark_union_mismatch")
            continue
        parent = nodes[parent_id]["resolution"]
        if parent.get("evidence_basis") != BRIDGE_VERSION:
            raise ValueError("mixed_existing_clock_policy")
        expected = copy.deepcopy(parent["observation_watermarks"])
        for source, component in value["source_observations"].items():
            old = expected.get(source)
            if old and instant(component["observed_utc"]) < instant(old["observed_utc"]):
                raise ValueError("existing_source_observation_time_regressed")
            if old and component["observation_id"] == old["observation_id"] and component["observed_utc"] != old["observed_utc"]:
                raise ValueError("existing_source_observation_clock_mutated")
            if old and source == value["source"] and value["measurement_id"] != parent["measurement_id"] and (
                    instant(component["observed_utc"]) <= instant(old["observed_utc"])
                    or component["observation_id"] == old["observation_id"]):
                raise ValueError("existing_stale_conflicting_primary_observation")
            expected[source] = component
        if fingerprint(expected) != fingerprint(value["observation_watermarks"]):
            raise ValueError("existing_source_observation_watermark_union_mismatch")


def observation_record(resolution):
    """Immutable observation fence, separate from an outcome revision/clock.

    Callers persist every accepted record atomically with any outcome proposal.
    An unchanged outcome therefore retains its original revision and time while
    a later provider capture still fences out subsequently delivered stale data.
    """
    unsigned = {"version": 1, "kind": OBSERVATION_KIND, "evidence_basis": BRIDGE_VERSION,
        **{field: copy.deepcopy(resolution[field]) for field in (
            "receipt_id", "contract_id", "event_id", "measurement_id", "measurement",
            "evidence_observation_id", "source_observations")},
        "observed_utc": resolution["evidence_observed_utc"]}
    return {**unsigned, "observation_id": fingerprint(unsigned)}


def _observations(rows, receipt, observed, reference_measurement):
    known, evidence_ids, components, primary_states, primary_clock_states = {}, {}, {}, {}, {}
    expected_keys = {"version", "kind", "evidence_basis", "receipt_id", "contract_id", "event_id",
                     "measurement_id", "measurement", "evidence_observation_id", "source_observations",
                     "observed_utc", "observation_id"}
    for row in rows:
        if (not isinstance(row, dict) or set(row) != expected_keys
                or type(row.get("version")) is not int or row["version"] != 1
                or row.get("kind") != OBSERVATION_KIND or row.get("evidence_basis") != BRIDGE_VERSION
                or row.get("observation_id") != fingerprint({k: v for k, v in row.items() if k != "observation_id"})):
            raise ValueError("invalid_existing_observation_record")
        for field in ("receipt_id", "contract_id", "event_id"):
            if row[field] != receipt[field]:
                raise ValueError("existing_observation_receipt_mismatch")
        measurement, source_components = row["measurement"], row["source_observations"]
        if (not isinstance(measurement, dict) or type(measurement.get("version")) is not int
                or measurement["version"] != 1 or row["measurement_id"] != fingerprint(measurement)
                or fingerprint(measurement.get("canonical_contract")) != receipt["contract_id"]
                or not isinstance(source_components, dict) or GAME_SOURCE not in source_components):
            raise ValueError("invalid_existing_observation_measurement")
        if (set(measurement) != set(reference_measurement)
                or type(measurement.get("week")) is not int
                or measurement["week"] != reference_measurement["week"]
                or fingerprint(measurement.get("game")) != fingerprint(reference_measurement["game"])):
            raise ValueError("existing_observation_game_identity_mismatch")
        if row["evidence_observation_id"] != fingerprint({"measurement_id": row["measurement_id"], "components": source_components}):
            raise ValueError("invalid_existing_observation_hash")
        status, actual, hit = measurement.get("status"), measurement.get("actual"), measurement.get("hit")
        basis = measurement.get("participation_basis")
        if status == "FINAL":
            primary = STAT_SOURCE
            if (measurement.get("played") is not True or type(hit) is not bool or not finite_number(actual)
                    or basis not in ("stat_row", "r106_positive_offensive_snaps_no_stat_row")
                    or hit != (actual >= float(measurement["canonical_contract"][4]))
                    or measurement.get("reason") is not None):
                raise ValueError("invalid_existing_observation_grade")
            if basis == "r106_positive_offensive_snaps_no_stat_row" and (actual != 0 or SNAP_SOURCE not in source_components):
                raise ValueError("missing_existing_participation_observation")
        elif status == "VOID":
            primary = SNAP_SOURCE
            if (measurement.get("played") is not False or actual is not None or hit is not None
                    or measurement.get("reason") != "did_not_play" or basis != DNP_BASIS):
                raise ValueError("invalid_existing_observation_void")
        else:
            raise ValueError("invalid_existing_observation_status")
        if primary not in source_components or not isinstance(source_components[primary], dict):
            raise ValueError("missing_existing_required_observation")
        primary_key = (primary, source_components[primary].get("observation_id"))
        state = fingerprint({field: measurement.get(field) for field in (
            "actual", "status", "played", "hit", "reason", "participation_basis")})
        if primary_key in primary_states and primary_states[primary_key] != state:
            raise ValueError("conflicting_existing_primary_observation_measurement")
        primary_states[primary_key] = state
        for source, component in source_components.items():
            if source not in (GAME_SOURCE, STAT_SOURCE, SNAP_SOURCE) or not isinstance(component, dict):
                raise ValueError("invalid_existing_observation_component")
            expected = _component(source, component.get("observation_id"), component.get("observed_utc"), observed)
            if fingerprint(component) != fingerprint(expected):
                raise ValueError("invalid_existing_observation_component")
            component_key = (source, component["observation_id"])
            if component_key in components and fingerprint(components[component_key]) != fingerprint(component):
                raise ValueError("existing_source_observation_clock_mutated")
            components[component_key] = component
        # Distinct capture IDs at the same primary observation instant cannot
        # establish an ordering for contradictory facts. Quarantine that
        # ambiguity even when only one capture has an outcome revision.
        primary_clock = (primary, instant(source_components[primary]["observed_utc"]))
        if primary_clock in primary_clock_states and primary_clock_states[primary_clock] != state:
            raise ValueError("conflicting_equal_clock_primary_measurements")
        primary_clock_states[primary_clock] = state
        game_clock = instant(source_components[GAME_SOURCE]["observed_utc"])
        if game_clock < instant(receipt["event_start_utc"]) or any(
                instant(c["observed_utc"]) < game_clock for source, c in source_components.items() if source != GAME_SOURCE):
            raise ValueError("existing_observation_before_final_evidence")
        if instant(row["observed_utc"]) != max(instant(c["observed_utc"]) for c in source_components.values()):
            raise ValueError("existing_observation_clock_mismatch")
        old = known.get(row["observation_id"])
        if old is not None and fingerprint(old) != fingerprint(row):
            raise ValueError("conflicting_existing_observation_record")
        other = evidence_ids.get(row["evidence_observation_id"])
        if other is not None and fingerprint(other) != fingerprint(row):
            raise ValueError("conflicting_existing_observation_id")
        known[row["observation_id"]] = row
        evidence_ids[row["evidence_observation_id"]] = row
    return known, evidence_ids


def resolve_observed(receipts, source_receipts, observed_legs, game_observations, *, observed_utc,
                     existing_revisions=(), existing_observations=()):
    """Propose immutable observation-clock revisions for every supplied vintage.

    Stable-current-measurement retries retain the original outcome clock. Exact
    historical observation retries replay their original revision, never create
    a head-reverting correction. Only newer, nonregressing captured evidence can
    change a measurement. Database insertion clocks remain outside this module.
    """
    observed = instant(observed_utc)
    rejected, persisted, sources, grades, histories = [], defaultdict(list), defaultdict(list), defaultdict(list), defaultdict(list)
    observations = defaultdict(list)

    def reject(reason, sid=None):
        rejected.append({"reason": str(reason), "source_receipt_id": sid if isinstance(sid, str) else None})

    for receipt in receipts:
        try:
            validate_probability_receipt(receipt)
            if receipt["domain"] == "nfl" and receipt["task"] == "nfl.parlay_leg":
                persisted[receipt["source_receipt_id"]].append(receipt)
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            reject(exc)
    for source in source_receipts:
        if not isinstance(source, dict) or not isinstance(source.get("receipt_id"), str):
            reject("invalid_source_receipt")
        else:
            sources[source["receipt_id"]].append(source)
    for row in existing_revisions:
        if not isinstance(row, dict) or not isinstance(row.get("resolution"), dict) or not isinstance(row["resolution"].get("receipt_id"), str):
            raise ValueError("malformed_existing_revision_history")
        histories[row["resolution"]["receipt_id"]].append(row)
    for row in existing_observations:
        if not isinstance(row, dict) or not isinstance(row.get("receipt_id"), str):
            raise ValueError("malformed_existing_observation_history")
        observations[row["receipt_id"]].append(row)
    for leg in observed_legs:
        if not isinstance(leg, dict) or not isinstance(leg.get("source_receipt_id"), str):
            reject("invalid_observed_leg")
        else:
            grades[leg["source_receipt_id"]].append(leg)
    candidates, states = {}, defaultdict(set)
    bad_contracts, bad_scopes = set(), set()
    for sid in sorted(grades):
        affected, scope = set(), None
        try:
            known, original = persisted.get(sid, []), sources.get(sid, [])
            affected = {row["contract_id"] for row in known}
            if not known or not original:
                raise ValueError("missing_persisted_or_original_source_receipt")
            if len({fingerprint(row) for row in known}) != 1 or len({fingerprint(row) for row in original}) != 1:
                raise ValueError("conflicting_source_or_persisted_receipts")
            receipt, source = known[0], original[0]
            source_key = _source(receipt, source, {**source, "model_prob": source.get("served_prob")})
            scope = (source_key[0], source_key[1], source_key[2], source_key[3], source_key[5])
            values = {}
            for leg in grades[sid]:
                if "receipt_id" in leg and leg["receipt_id"] != sid:
                    raise ValueError("conflicting_score_receipt_ids")
                value, scope, state = _evidence(receipt, source, leg, game_observations.get(source.get("game_id")), observed)
                values[fingerprint(value)] = value
                states[scope].add(state)
            if len(values) != 1:
                raise ValueError("conflicting_duplicate_observations")
            candidates[sid] = (receipt, next(iter(values.values())), scope)
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError) as exc:
            bad_contracts.update(affected)
            if scope is not None:
                bad_scopes.add(scope)
            reject(exc, sid)
    staged, staged_observations, historical_only = [], [], 0
    for sid, (receipt, evidence, scope) in sorted(candidates.items()):
        try:
            if len(states[scope]) != 1:
                raise ValueError("conflicting_underlying_stat_observations")
            if receipt["contract_id"] in bad_contracts or scope in bad_scopes:
                raise ValueError("quarantined_contract_evidence")
            nodes, head = _history(histories.get(receipt["receipt_id"], []), receipt, observed)
            _validate_observed_history(nodes, receipt)
            prior_observations = observations.get(receipt["receipt_id"], [])
            _, known_observations = _observations(prior_observations, receipt, observed, evidence["measurement"])
            for node in nodes.values():
                value = node["resolution"]
                if value.get("evidence_basis") == BRIDGE_VERSION:
                    proof = known_observations.get(value["evidence_observation_id"])
                    if proof is None or fingerprint(proof) != fingerprint(observation_record(value)):
                        raise ValueError("missing_existing_observation_proof")
            record = observation_record(evidence)
            # Validate the candidate against all durable observations before an
            # unchanged-measurement replay may short-circuit outcome creation.
            _observations([*prior_observations, record], receipt, observed, evidence["measurement"])
            staged_observations.append((sid, record, scope))
            exact = [node for node in nodes.values()
                     if node["resolution"].get("evidence_basis") == BRIDGE_VERSION
                     and node["resolution"].get("evidence_observation_id") == evidence["evidence_observation_id"]]
            if len(exact) > 1:
                raise ValueError("conflicting_existing_observation_ids")
            if exact:
                staged.append((sid, _proposal(exact[0]), scope, True))
                continue
            if head and head["resolution"].get("evidence_basis") != BRIDGE_VERSION:
                raise ValueError("incompatible_existing_clock_policy")
            if head and head["resolution"]["measurement_id"] == evidence["measurement_id"]:
                staged.append((sid, _proposal(head), scope, True))
                continue
            if evidence["evidence_observation_id"] in known_observations:
                # A prior unchanged capture may have no outcome revision of its
                # own. Replaying that known old capture must not revert a head.
                historical_only += 1
                continue
            watermarks = {}
            for prior in known_observations.values():
                for source, component in prior["source_observations"].items():
                    if source not in watermarks or instant(component["observed_utc"]) > instant(watermarks[source]["observed_utc"]):
                        watermarks[source] = copy.deepcopy(component)
            for source, component in evidence["source_observations"].items():
                old = watermarks.get(source)
                if old and instant(component["observed_utc"]) < instant(old["observed_utc"]):
                    raise ValueError("stale_conflicting_source_observation")
                if old and source == evidence["source"] and (
                        instant(component["observed_utc"]) <= instant(old["observed_utc"])
                        or component["observation_id"] == old["observation_id"]):
                    raise ValueError("stale_conflicting_primary_observation")
            if head:
                previous = head["resolution"]
                if fingerprint(evidence["measurement"]["game"]) != fingerprint(previous["measurement"]["game"]):
                    raise ValueError("changed_final_game_identity")
                if instant(evidence["resolved_utc"]) <= instant(previous["resolved_utc"]):
                    raise ValueError("stale_conflicting_observation")
                watermarks = copy.deepcopy(previous["observation_watermarks"])
                for source, component in evidence["source_observations"].items():
                    old = watermarks.get(source)
                    if old and instant(component["observed_utc"]) < instant(old["observed_utc"]):
                        raise ValueError("stale_conflicting_source_observation")
                    if old and source == evidence["source"] and (
                            instant(component["observed_utc"]) <= instant(old["observed_utc"])
                            or component["observation_id"] == old["observation_id"]):
                        raise ValueError("stale_conflicting_primary_observation")
                    if old and component["observation_id"] == old["observation_id"] and component["observed_utc"] != old["observed_utc"]:
                        raise ValueError("conflicting_source_observation_clock")
                    watermarks[source] = copy.deepcopy(component)
                evidence["observation_watermarks"] = watermarks
            parent = head["revision_id"] if head else None
            revision_id = BRIDGE_VERSION + ":" + fingerprint({"receipt_id": receipt["receipt_id"],
                "observation_id": evidence["evidence_observation_id"], "supersedes": parent})
            staged.append((sid, {"revision_id": revision_id, "supersedes": parent,
                "reason": "changed_observed_provider_measurement" if head else None,
                "resolution": evidence}, scope, False))
        except (ValueError, TypeError, KeyError, OverflowError) as exc:
            bad_contracts.add(receipt["contract_id"])
            bad_scopes.add(scope)
            reject(exc, sid)
    proposals, replayed = [], 0
    observation_proposals = [record for _, record, scope in staged_observations
                             if scope not in bad_scopes and record["contract_id"] not in bad_contracts]
    for sid, proposal, scope, replay in staged:
        if proposal["resolution"]["contract_id"] in bad_contracts or scope in bad_scopes:
            reject("quarantined_contract_evidence", sid)
        else:
            proposals.append(proposal)
            replayed += int(replay)
    for sid in sorted(set(persisted) - set(grades)):
        reject("no_observed_grade", sid)
    rejected.sort(key=lambda row: (row["reason"], str(row["source_receipt_id"])))
    return {"version": 1, "adapter": BRIDGE_VERSION, "proposals": proposals,
            "observation_proposals": observation_proposals, "rejections": rejected,
            "summary": {"persisted_source_receipts": len(persisted), "proposed_revisions": len(proposals),
                        "replayed_revisions": replayed, "observation_records": len(observation_proposals),
                        "historical_observation_retries": historical_only, "excluded_records": len(rejected),
                        "exclusion_reasons": dict(sorted(Counter(row["reason"] for row in rejected).items())), "writes": False}}
