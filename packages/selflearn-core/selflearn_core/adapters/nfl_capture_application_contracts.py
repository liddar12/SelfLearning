"""Strict v2 response/seal contracts; hashes are integrity, not source authority."""
from __future__ import annotations

from . import nfl_capture_application_v2 as V
from . import nfl_capture_application as capacity
from . import nfl_capture_delivery_journal as D
from .nfl_observed_resolution import DNP_BASIS, GAME_SOURCE, STAT_SOURCE, SNAP_SOURCE
from ..probability_receipts import finite_number, fingerprint, instant

SEAL_KEYS = {"version", "kind", "application_id", "request_id", "capture_intent_id", "capture_id", "execution_policy",
             "binding_policy", "season", "weeks", "source_watermark", "membership_id", "source_vector_id", "limits",
             "groups", "batches", "seal_id"}
GROUP_KEYS = {"group_id", *V.MEMBERSHIP_KEYS, "membership_id", "status", "reason_codes", "batch_ordinal", "fact", "fact_id"}
REGISTRATION_KEYS = {"version", "kind", "application_id", "request_id", "capture_intent_id", "capture_id", "seal_id",
    "membership_id", "source_vector_id", "status", "group_count", "member_count", "batch_count", "next_step_id",
    "source_watermark", "application_seq", "recorded_utc", "scope", "durable", "learning_accepted",
    "source_authentication", "registration_id"}
BATCH_KEYS = {"version", "kind", "application_id", "step_id", "ordinal", "seal_id", "membership_id", "status", "groups",
    "added_observations", "added_revisions", "input_watermark", "watermark", "application_seq", "recorded_utc",
    "durable", "scope", "source_authentication", "batch_result_id"}
TERMINAL_KEYS = {"version", "kind", "application_id", "request_id", "capture_intent_id", "capture_id", "seal_id",
    "membership_id", "source_vector_id", "status", "reason_codes", "current_membership_id", "group_report_id", "counts",
    "registered_watermark", "watermark", "application_seq", "recorded_utc", "processing_complete", "complete_success",
    "learning_accepted", "durable", "scope", "source_authentication", "terminal_result_id"}
STATUS_KEYS = {"version", "kind", "application_id", "registration_id", "seal_id", "membership_id", "status", "batch_count",
    "committed_batches", "next_step_id", "terminal_result_id", "ledger_watermark", "scope", "durable_ack", "execution_ready",
    "source_authentication"}
PAGE_KEYS = {"version", "kind", "found", "application_id", "view", "ledger_watermark", "registration_id", "seal_id",
             "records", "total_records", "next_after"}
VIEWS = {"registration", "status", "groups", "batches", "batch", "terminal"}
IDENTITIES = ("application_id", "request_id", "capture_intent_id", "capture_id", "seal_id", "membership_id", "source_vector_id")
COUNT_KEYS = {"groups", "members", "accepted_groups", "accepted_members", "withheld_groups", "withheld_members",
              "not_applied_groups", "not_applied_members", "batches", "committed_batches"}


def keys(value, expected):
    if type(value) is not dict or set(value) != expected:
        raise ValueError("invalid_capture_application_contract")


def integer(value, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError("invalid_capture_application_integer")
    return value


def hashed(value, name):
    D._hash(value[name])
    if value[name] != fingerprint({key: item for key, item in value.items() if key != name}):
        raise ValueError("capture_application_result_hash_mismatch")


def _base(value, expected, kind, scope):
    keys(value, expected)
    if (type(value["version"]) is not int or value["version"] != 2 or value["kind"] != kind
            or value["scope"] != scope or value["durable"] is not True
            or value["source_authentication"] != "not_performed"):
        raise ValueError("invalid_capture_application_result")
    if V.sequence(value["application_seq"]) == 0:
        raise ValueError("invalid_capture_application_result_sequence")
    instant(value["recorded_utc"])


def _same(value, seal, fields):
    for field in fields:
        D._hash(value[field])
        if value[field] != seal[field]:
            raise ValueError("capture_application_result_identity_mismatch")


def _fact(value, scope, seal, request):
    keys(value, {*V.FACT_KEYS, "source_observations"})
    integer(value["week"], 1, 18)
    if value["week"] not in seal["weeks"]:
        raise ValueError("capture_application_fact_week_mismatch")
    game = value["game"]
    keys(game, {"season", "week", "game_id", "kickoff_utc", "home", "away", "state"})
    if (type(game["season"]) is not int or game["season"] != scope[0]
            or type(game["week"]) is not int or game["week"] != value["week"]
            or game["game_id"] != scope[1] or game["state"] != "FINAL"
            or any(type(game[key]) is not str or not 1 <= len(game[key]) <= 200 for key in ("home", "away"))
            or game["home"] == game["away"]):
        raise ValueError("invalid_capture_application_game_fact")
    kickoff = instant(game["kickoff_utc"])
    sources = value["source_observations"]
    if type(sources) is not dict or not {GAME_SOURCE} < set(sources) <= {GAME_SOURCE, STAT_SOURCE, SNAP_SOURCE}:
        raise ValueError("invalid_capture_application_fact_sources")
    if value["status"] == "VOID":
        if (value["actual"] is not None or value["played"] is not False or value["reason"] != "did_not_play"
                or value["participation_basis"] != DNP_BASIS or set(sources) != {GAME_SOURCE, SNAP_SOURCE}):
            raise ValueError("invalid_capture_application_void_fact")
    elif (value["status"] != "FINAL" or value["played"] is not True or value["reason"] is not None
            or not finite_number(value["actual"]) or STAT_SOURCE not in sources
            or value["participation_basis"] not in ("stat_row", "r106_positive_offensive_snaps_no_stat_row")
            or (value["participation_basis"] == "r106_positive_offensive_snaps_no_stat_row"
                and (value["actual"] != 0 or SNAP_SOURCE not in sources))):
        raise ValueError("invalid_capture_application_final_fact")
    envelope = request["capture_request"]["envelope"]
    profiles = {GAME_SOURCE: "espn_scoreboard_v1", STAT_SOURCE: "nflverse_stats_player_v1", SNAP_SOURCE: "nflverse_snap_counts_v1"}
    for name, component in sources.items():
        keys(component, {"source", "observation_id", "observed_utc"})
        D._hash(component["observation_id"])
        source = envelope["observations"].get(component["observation_id"])
        if (component["source"] != name or source is None or source["profile"] != profiles[name]
                or source["season"] != scope[0]
                or (name == GAME_SOURCE and source["week"] != value["week"])
                or component["observed_utc"] != source["observed_utc"]):
            raise ValueError("capture_application_fact_component_mismatch")
        if instant(component["observed_utc"]) < kickoff:
            raise ValueError("capture_application_fact_before_kickoff")
    if any(instant(row["observed_utc"]) < instant(sources[GAME_SOURCE]["observed_utc"]) for row in sources.values()):
        raise ValueError("capture_application_fact_clock_order")


def validate_seal(seal, request):
    request = V.validate_request(request)
    keys(seal, SEAL_KEYS)
    if (type(seal["version"]) is not int or seal["version"] != 2 or seal["kind"] != "nfl.capture_application_seal.v2"
            or seal["execution_policy"] != V.EXECUTION_POLICY or seal["binding_policy"] != V.BINDING_POLICY
            or seal["application_id"] != request["application_id"] or seal["request_id"] != fingerprint(request)
            or seal["capture_intent_id"] != request["capture_request"]["delivery_id"]
            or seal["capture_id"] != request["capture_request"]["capture_id"]):
        raise ValueError("invalid_capture_application_seal_identity")
    manifest = request["capture_request"]["envelope"]["manifest"]
    if (type(seal["season"]) is not int or seal["season"] != manifest["season"]
            or V.bounded(seal["weeks"], 1024) != V.bounded(manifest["weeks"], 1024)
            or seal["source_vector_id"] != V.source_vector_id(request["capture_request"]["envelope"])):
        raise ValueError("capture_application_seal_period_or_vector_mismatch")
    V.sequence(seal["source_watermark"])
    keys(seal["limits"], {"members", "observations", "revisions", "rpc_bytes"})
    for name, limit in seal["limits"].items():
        integer(limit, 1, V.MAX_RPC_BYTES if name == "rpc_bytes" else V.MAX_BATCH_ITEMS)
    groups, batches = seal["groups"], seal["batches"]
    if type(groups) is not list or len(groups) > V.MAX_GROUPS or type(batches) is not list or len(batches) > V.MAX_GROUPS:
        raise ValueError("capture_application_seal_work_limit")
    gids, members, sources, planned = [], set(), set(), {}
    for group in groups:
        keys(group, GROUP_KEYS)
        if type(group["status"]) is not str or group["status"] not in ("planned", "withheld"):
            raise ValueError("invalid_capture_application_sealed_status")
        original = {key: group[key] for key in capacity._GROUP_KEYS}
        original["status"] = "staged" if group["status"] == "planned" else "withheld"
        capacity._group(original)
        if any(len(value) > 200 for value in group["scope"][1:]):
            raise ValueError("capture_application_scope_text_work_limit")
        if group["scope"][0] != seal["season"] or members.intersection(group["receipt_ids"]) or sources.intersection(group["source_receipt_ids"]):
            raise ValueError("duplicate_or_foreign_capture_application_members")
        gids.append(group["group_id"]); members.update(group["receipt_ids"]); sources.update(group["source_receipt_ids"])
        if group["status"] == "planned":
            integer(group["batch_ordinal"], 0, V.MAX_GROUPS - 1)
            if len(group["receipt_ids"]) > min(seal["limits"]["members"], seal["limits"]["observations"]):
                raise ValueError("capture_application_group_exceeds_sealed_limit")
            _fact(group["fact"], group["scope"], seal, request)
            if group["fact_id"] != fingerprint(group["fact"]):
                raise ValueError("capture_application_fact_hash_mismatch")
            planned[group["group_id"]] = group
        elif any(group[key] is not None for key in ("batch_ordinal", "fact", "fact_id")):
            raise ValueError("capture_application_withheld_group_has_assignment")
    if gids != sorted(set(gids)) or len(members) > V.MAX_MEMBERS or seal["membership_id"] != V.membership_id(groups):
        raise ValueError("invalid_capture_application_global_membership")
    assigned = []
    for ordinal, batch in enumerate(batches):
        keys(batch, {"ordinal", "step_id", "group_ids", "member_count"})
        integer(batch["ordinal"], 0, V.MAX_GROUPS - 1)
        if (batch["ordinal"] != ordinal or batch["step_id"] != V.step_id(seal["application_id"], ordinal)
                or type(batch["group_ids"]) is not list or not batch["group_ids"]
                or any(type(gid) is not str or gid not in planned for gid in batch["group_ids"])
                or batch["group_ids"] != sorted(set(batch["group_ids"]))):
            raise ValueError("invalid_capture_application_batch_assignment")
        integer(batch["member_count"], 1, min(seal["limits"]["members"], seal["limits"]["observations"]))
        if (batch["member_count"] != sum(len(planned[gid]["receipt_ids"]) for gid in batch["group_ids"])
                or any(planned[gid]["batch_ordinal"] != ordinal for gid in batch["group_ids"])):
            raise ValueError("capture_application_assignment_member_mismatch")
        assigned.extend(batch["group_ids"])
    if assigned != list(planned):
        raise ValueError("incomplete_capture_application_assignment")
    hashed(seal, "seal_id")
    V.bounded(seal, V.MAX_SEAL_BYTES)
    return seal


def validate_registration(value, seal):
    _base(value, REGISTRATION_KEYS, "nfl.capture_application_registration.v2", "sealed_application_only")
    _same(value, seal, IDENTITIES)
    if (value["status"] != "registered" or value["learning_accepted"] is not False
            or value["source_watermark"] != seal["source_watermark"]
            or value["next_step_id"] != (seal["batches"][0]["step_id"] if seal["batches"] else None)):
        raise ValueError("invalid_capture_application_registration")
    for name, expected in (("group_count", len(seal["groups"])), ("member_count", sum(len(g["receipt_ids"]) for g in seal["groups"])),
                           ("batch_count", len(seal["batches"]))):
        integer(value[name], 0, V.MAX_MEMBERS)
        if value[name] != expected:
            raise ValueError("capture_application_registration_count_mismatch")
    hashed(value, "registration_id")
    return value


def validate_group_result(result, sealed):
    keys(result, D._GROUP_KEYS)
    if any(V.bounded(result[key], V.MAX_SEAL_BYTES) != V.bounded(sealed[key], V.MAX_SEAL_BYTES)
           for key in ("group_id", *V.MEMBERSHIP_KEYS, "membership_id")):
        raise ValueError("capture_application_result_membership_mismatch")
    # Assigned batches always contain <=500; initial withheld groups can be
    # larger, so use the unbounded membership validator for their no-write row.
    if len(result["receipt_ids"]) <= V.MAX_BATCH_ITEMS:
        D._group(result)
    else:
        capacity._group({key: result[key] for key in capacity._GROUP_KEYS})
        if result["status"] != "withheld" or result["observation_ids"] != [] or result["revision_ids"] != []:
            raise ValueError("oversized_capture_application_group_has_writes")
    if sealed["status"] == "withheld" and (result["status"] != "withheld"
            or result["reason_codes"] != sealed["reason_codes"] or result["observation_ids"] or result["revision_ids"]):
        raise ValueError("initial_capture_application_withholding_changed")
    if len(result["revision_ids"]) > len(result["receipt_ids"]):
        raise ValueError("capture_application_group_revision_work_limit")
    return result


def validate_batch(value, seal):
    _base(value, BATCH_KEYS, "nfl.capture_application_batch_result.v2", "observations_and_batch_results")
    _same(value, seal, ("application_id", "seal_id", "membership_id"))
    integer(value["ordinal"], 0, len(seal["batches"]) - 1)
    assignment = seal["batches"][value["ordinal"]]
    if value["step_id"] != assignment["step_id"] or value["status"] not in ("committed", "not_applied"):
        raise ValueError("invalid_capture_application_batch_result")
    if type(value["groups"]) is not list or [g.get("group_id") for g in value["groups"] if type(g) is dict] != assignment["group_ids"]:
        raise ValueError("capture_application_batch_group_partition_mismatch")
    original = {g["group_id"]: g for g in seal["groups"]}
    seen_observations, seen_revisions = set(), set()
    for group in value["groups"]:
        validate_group_result(group, original[group["group_id"]])
        if seen_observations.intersection(group["observation_ids"]) or seen_revisions.intersection(group["revision_ids"]):
            raise ValueError("duplicate_capture_application_result_proof")
        seen_observations.update(group["observation_ids"]); seen_revisions.update(group["revision_ids"])
        if value["status"] == "not_applied" and (group["status"] != "withheld" or group["reason_codes"] != ["application_membership_changed"]):
            raise ValueError("invalid_capture_application_not_applied_group")
    low, high = V.sequence(value["input_watermark"]), V.sequence(value["watermark"])
    if (len(seen_observations) > seal["limits"]["observations"]
            or len(seen_revisions) > seal["limits"]["revisions"]):
        raise ValueError("capture_application_batch_proof_work_limit")
    for key, ids in (("added_observations", seen_observations), ("added_revisions", seen_revisions)):
        integer(value[key], 0, len(ids))
    if (low < V.sequence(seal["source_watermark"]) or high < low
            or bool(value["added_observations"] + value["added_revisions"]) != (high != low)
            or (value["status"] == "not_applied" and (high != low or seen_observations or seen_revisions))):
        raise ValueError("capture_application_batch_watermark_mismatch")
    hashed(value, "batch_result_id")
    return value


def validate_report(seal, groups, batches, *, terminal_reasons=None):
    """Bind complete paged report rows to their immutable full seal and ACKs."""
    if (type(groups) is not list or len(groups) != len(seal["groups"])
            or type(batches) is not list or len(batches) != len(seal["batches"])):
        raise ValueError("incomplete_capture_application_report")
    results_by_group, assignments, ended = {}, {}, False
    seen_observations, seen_revisions = set(), set()
    previous_watermark, previous_sequence = V.sequence(seal["source_watermark"]), 0
    for row, assignment in zip(batches, seal["batches"]):
        keys(row, {"assignment", "result"})
        if V.bounded(row["assignment"], V.MAX_SEAL_BYTES) != V.bounded(assignment, V.MAX_SEAL_BYTES):
            raise ValueError("capture_application_report_assignment_mismatch")
        for gid in assignment["group_ids"]:
            assignments[gid] = row
        result = row["result"]
        if result is None:
            ended = True
        else:
            validate_batch(result, seal)
            if ended or result["ordinal"] != assignment["ordinal"]:
                raise ValueError("capture_application_report_batch_order_mismatch")
            if (V.sequence(result["input_watermark"]) < previous_watermark
                    or V.sequence(result["application_seq"]) <= previous_sequence):
                raise ValueError("capture_application_report_batch_chronology_mismatch")
            previous_watermark = V.sequence(result["watermark"])
            previous_sequence = V.sequence(result["application_seq"])
            for group in result["groups"]:
                if (seen_observations.intersection(group["observation_ids"])
                        or seen_revisions.intersection(group["revision_ids"])):
                    raise ValueError("duplicate_capture_application_report_proof")
                seen_observations.update(group["observation_ids"])
                seen_revisions.update(group["revision_ids"])
            results_by_group.update({group["group_id"]: group for group in result["groups"]})
            if result["status"] == "not_applied":
                if terminal_reasons != ["application_membership_changed"]:
                    raise ValueError("capture_application_not_applied_without_terminal")
                ended = True
    for row, sealed in zip(groups, seal["groups"]):
        keys(row, {"sealed_group", "execution_state", "result"})
        if V.bounded(row["sealed_group"], V.MAX_SEAL_BYTES) != V.bounded(sealed, V.MAX_SEAL_BYTES):
            raise ValueError("capture_application_report_sealed_group_mismatch")
        state, result, gid = row["execution_state"], row["result"], sealed["group_id"]
        if sealed["status"] == "withheld":
            if state != "initial_withheld" or result is None:
                raise ValueError("capture_application_initial_withheld_state_changed")
        elif gid in results_by_group:
            expected = "committed" if assignments[gid]["result"]["status"] == "committed" else "not_applied"
            if state != expected or V.bounded(result, V.MAX_SEAL_BYTES) != V.bounded(results_by_group[gid], V.MAX_SEAL_BYTES):
                raise ValueError("capture_application_report_group_batch_mismatch")
        elif terminal_reasons is not None:
            if (state != "not_applied" or not terminal_reasons or type(result) is not dict
                    or result.get("status") != "withheld" or result.get("reason_codes") != terminal_reasons):
                raise ValueError("capture_application_terminal_pending_group_mismatch")
        elif state != "pending" or result is not None:
            raise ValueError("capture_application_pending_group_state_changed")
        if result is not None:
            validate_group_result(result, sealed)
    return groups, batches


def validate_terminal(value, seal, groups, batches):
    _base(value, TERMINAL_KEYS, "nfl.capture_application_result.v2", "application_group_results")
    _same(value, seal, IDENTITIES)
    D._hash(value["current_membership_id"])
    normal = {"accepted", "partial", "withheld", "no_supported_membership"}
    if value["status"] not in normal | {"incomplete_membership_changed", "incomplete_integrity_or_capacity"}:
        raise ValueError("invalid_capture_application_terminal_status")
    reasons = value["reason_codes"]
    allowed = {"application_membership_changed", "source_integrity_failed", "runtime_capacity_exceeded"}
    if (type(reasons) is not list or any(type(reason) is not str or reason not in allowed for reason in reasons)
            or reasons != sorted(set(reasons)) or (value["status"] in normal) != (not reasons)
            or (value["status"] == "incomplete_membership_changed" and reasons != ["application_membership_changed"])
            or (value["status"] == "incomplete_integrity_or_capacity"
                and (not reasons or not set(reasons) <= {"source_integrity_failed", "runtime_capacity_exceeded"}))):
        raise ValueError("invalid_capture_application_terminal_reasons")
    validate_report(seal, groups, batches, terminal_reasons=reasons)
    keys(value["counts"], COUNT_KEYS)
    counts = {key: 0 for key in COUNT_KEYS}
    counts.update(groups=len(groups), members=sum(len(row["sealed_group"]["receipt_ids"]) for row in groups),
                  batches=len(batches), committed_batches=sum(row["result"] is not None and row["result"]["status"] == "committed" for row in batches))
    for row in groups:
        state, result, group = row["execution_state"], row["result"], row["sealed_group"]
        if state == "pending" or result is None:
            raise ValueError("capture_application_terminal_has_pending_group")
        accepted = result["status"] == "accepted"
        prefix = "accepted" if accepted else "withheld"
        counts[prefix + "_groups"] += 1; counts[prefix + "_members"] += len(group["receipt_ids"])
        if state == "not_applied":
            counts["not_applied_groups"] += 1; counts["not_applied_members"] += len(group["receipt_ids"])
    for key in COUNT_KEYS:
        integer(value["counts"][key], 0, V.MAX_MEMBERS)
    complete = value["status"] in normal
    expected = ("no_supported_membership" if not groups else "accepted" if counts["accepted_groups"] == len(groups)
                else "partial" if counts["accepted_groups"] else "withheld")
    if (value["counts"] != counts or value["group_report_id"] != fingerprint(groups)
            or value["processing_complete"] is not complete or value["complete_success"] is not (value["status"] == "accepted")
            or value["learning_accepted"] is not bool(counts["accepted_groups"])
            or (complete and (value["status"] != expected or counts["not_applied_groups"]
                              or counts["committed_batches"] != len(batches)))
            or (complete and value["current_membership_id"] != seal["membership_id"])
            or (value["status"] == "incomplete_membership_changed" and value["current_membership_id"] == seal["membership_id"])):
        raise ValueError("capture_application_terminal_report_mismatch")
    if (value["registered_watermark"] != seal["source_watermark"]
            or V.sequence(value["watermark"]) < max([V.sequence(value["registered_watermark"])]
                + [V.sequence(row["result"]["watermark"]) for row in batches if row["result"] is not None])
            or V.sequence(value["application_seq"]) <= max([0]
                + [V.sequence(row["result"]["application_seq"]) for row in batches if row["result"] is not None])):
        raise ValueError("capture_application_terminal_watermark_mismatch")
    hashed(value, "terminal_result_id")
    return value
