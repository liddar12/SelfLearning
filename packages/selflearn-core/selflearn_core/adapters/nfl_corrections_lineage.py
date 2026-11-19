"""Resolve correction baselines from the original scoped frozen generation."""
from types import SimpleNamespace

from ..probability_receipts import fingerprint, instant
from .nfl import adapt_prospective
from .nfl_collection_handoff import scope, validate_graph
from .nfl_corrections_fit import _authorize_snapshot
from .nfl_corrections_policy import CorrectionsError, is_digest, lineage_pair
from .nfl_generation import validate_generation


def _require(value, reason="unverified_serving_lineage"):
    if not value:
        raise CorrectionsError(reason)


def _same(left, right):
    return fingerprint(left) == fingerprint(right)


def authorize_serving_baselines(parsed, artifact, journal, collection, repository, read_clock):
    """Return per-leg original event starts and integrity holds from trusted ports.

    The collection lookup precedes the fit snapshot. Both use the same journal,
    and the immutable objects joined afterward must have existed before its ACK.
    """
    reference = parsed["baseline_generation"]
    declared = lineage_pair({"base_model_version": parsed["baseline_model_version"],
                             "calibrator_version": parsed["calibrator_version"]})
    _require(reference is not None and all(is_digest(reference[k]) for k in
             ("intent_id", "generation_id", "generation_object_id")), "missing_baseline_lineage")
    _require(journal is not None and collection is not None and repository is not None,
             "missing_baseline_lineage")
    key = scope(repository, reference["workflow"], reference["run_id"], reference["run_attempt"])
    view = collection.read(key[1:])
    intent = view["intent"]
    _require(intent is not None and tuple(intent[k] for k in ("repository", "workflow", "run_id", "run_attempt")) == key
             and all(intent[k] == reference[k] for k in ("intent_id", "generation_id", "generation_object_id"))
             and not view["missing_object_ids"])
    fence_utc = read_clock()
    fence = instant(fence_utc)
    _require(instant(intent["stored_utc"]) <= instant(view["observed_utc"]) <= fence)
    snapshot = journal.read_publication_fit_snapshot()
    identity = reference["generation_object_id"]
    records = [row for row in snapshot["generation_records"] if row["object_id"] == identity]
    _require(len(records) == 1)
    record = records[0]
    committed = instant(record["recorded_utc"])
    _require(committed <= fence)
    cache = {}

    def get_object(object_id):
        row = journal.get_object(object_id)
        _require(row is not None and row["object_id"] == object_id and instant(row["received_utc"]) <= committed)
        cache[object_id] = row
        return row

    document = validate_generation(SimpleNamespace(get_object=get_object), identity, now_utc=fence_utc)
    validate_graph(intent, document)
    _require(_same(document, record["payload"]))
    generation = document["generations"][0]
    _require(instant(generation["recorded_utc"]) <= instant(intent["stored_utc"])
             <= instant(cache[identity]["received_utc"]))
    _require(document["season"] == parsed["season"] and document["week"] == parsed["week"]
             and instant(generation["pool_generated_utc"]) == instant(parsed["generated_at"]))
    ack = record["ack"]
    _require(type(ack) is dict and set(ack) == {"version", "object_id", "durable", "receipts", "rejections"}
             and type(ack["version"]) is int and ack["version"] == 1 and ack["object_id"] == identity
             and ack["durable"] is True and ack["rejections"] == [] and type(ack["receipts"]) is list)
    rebuilt = adapt_prospective(journal_docs=[document])
    _require(not rebuilt["rejections"] and bool(rebuilt["receipts"]))
    expected = {row["source_receipt_id"]: row for row in rebuilt["receipts"]}
    accepted = {}
    for member in ack["receipts"]:
        _require(type(member) is dict and set(member) == {"source_receipt_id", "receipt_id", "status"}
                 and member["status"] in ("recorded", "duplicate") and member["source_receipt_id"] not in accepted)
        accepted[member["source_receipt_id"]] = member["receipt_id"]
    _require(accepted == {sid: row["receipt_id"] for sid, row in expected.items()}
             and set(accepted) == set(generation["receipt_ids"]))
    expected_ids = set(accepted.values())
    actual = {row["receipt_id"]: row for row in snapshot["receipts"] if row["receipt_id"] in expected_ids}
    _require(actual.keys() == expected_ids and all(_same(row, actual[row["receipt_id"]]) for row in expected.values()))
    actual_pairs = {lineage_pair(row) for row in actual.values()}
    _require(len(actual_pairs) == 1, "mixed_serving_lineage")
    _require(declared in actual_pairs and declared == lineage_pair(artifact["lineage"]), "incompatible_baseline_lineage")
    _require(all(committed < instant(row["event_start_utc"]) for row in actual.values()), "late_baseline_generation")
    _authorize_snapshot(snapshot, artifact)

    sources = {row["receipt_id"]: row["learning_receipt"] for row in document["receipts"]}
    references = [row["source_receipt_id"] for row in parsed["legs"] if row["source_receipt_id"] is not None]
    _require(len(references) == len(set(references)), "duplicate_baseline_receipt")
    starts, refusals = {}, {}
    for leg in parsed["legs"]:
        sid = leg["source_receipt_id"]
        source = sources.get(sid)
        if sid is None or leg["contract_id"] is None:
            refusals[leg["leg_id"]] = "missing_baseline_lineage"
        elif (source is None or leg["contract_id"] != source["contract_id"]
              or leg["game_id"] != source["game_id"] or leg["market"] != source["market"]
              or leg["selection"] != source["direction"] or leg["baseline_prob"] != source["served_prob"]):
            refusals[leg["leg_id"]] = "unverified_baseline_receipt"
        else:
            starts[leg["leg_id"]] = instant(expected[sid]["event_start_utc"])
    return starts, refusals, fence
