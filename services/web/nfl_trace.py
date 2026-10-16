"""Read-only, pinned forecast views; no inferred publication or active model.

Only explicit journal snapshots are interpreted. No filesystem, network, clock,
environment, writes, fitting or promotion occurs here. Values returned from
receipts are evidence claims, never instructions or server configuration.
"""
import re

from selflearn_core.outcome_revisions import validate_journal_resolution
from selflearn_core.probability_receipts import fingerprint, instant, validate_probability_receipt

HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SEQ = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
MAX_RECEIPTS = 50000
MANIFEST_FIELDS = ("version", "season", "week", "pool_generated_utc", "source_snapshots",
                   "offered_cards", "selection_policy_version", "joint_model_version")


class TraceError(ValueError):
    """Static diagnostic, never arbitrary stored content or backend errors."""


def read_snapshot(journal):
    method = getattr(journal, "read_observed_snapshot", None)
    if not callable(method):
        method = getattr(journal, "read_snapshot", None)
    if not callable(method):
        raise TraceError("pinned_journal_read_unavailable")
    return method()


def _receipts(snapshot):
    if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or type(snapshot.get("watermark")) is not str or not SEQ.fullmatch(snapshot["watermark"])
            or any(type(snapshot.get(k)) is not list for k in ("receipts", "generation_records", "revisions"))
            or len(snapshot["receipts"]) > MAX_RECEIPTS):
        raise TraceError("invalid_or_unsupported_trace_snapshot")
    out, seen = [], set()
    for row in snapshot["receipts"]:
        try:
            validate_probability_receipt(row)
        except (ValueError, TypeError, KeyError, OverflowError):
            raise TraceError("invalid_trace_forecast") from None
        if row["receipt_id"] in seen:
            raise TraceError("duplicate_trace_forecast")
        seen.add(row["receipt_id"])
        if row["domain"] == "nfl" and row["task"] == "nfl.parlay_leg":
            out.append(row)
    return out


def _history(snapshot, receipt):
    nodes, children = {}, {}
    for node in snapshot["revisions"]:
        if not isinstance(node, dict) or not isinstance(node.get("resolution"), dict):
            raise TraceError("invalid_trace_revision")
        if node["resolution"].get("receipt_id") != receipt["receipt_id"]:
            continue
        try:
            validate_journal_resolution(receipt, node["resolution"])
            if instant(node["recorded_utc"]) < instant(node["resolution"]["resolved_utc"]):
                raise ValueError("future outcome")
        except (ValueError, TypeError, KeyError, OverflowError):
            raise TraceError("invalid_trace_revision") from None
        rid, parent = node.get("revision_id"), node.get("supersedes")
        if (type(node.get("version")) is not int or node["version"] != 1
                or type(rid) is not str or not 1 <= len(rid) <= 200 or rid in nodes
                or (parent is not None and (type(parent) is not str or not 1 <= len(parent) <= 200))
                or parent in children):
            raise TraceError("invalid_trace_revision_chain")
        nodes[rid], children[parent] = node, rid
    ordered, cursor, seen = [], children.get(None), set()
    while cursor is not None:
        if cursor in seen or cursor not in nodes:
            raise TraceError("invalid_trace_revision_chain")
        seen.add(cursor)
        node = nodes[cursor]
        if ordered and (instant(node["recorded_utc"]) < instant(ordered[-1]["recorded_utc"])
                        or instant(node["resolution"]["resolved_utc"]) < instant(ordered[-1]["resolution"]["resolved_utc"])):
            raise TraceError("regressing_trace_revision_clock")
        ordered.append(node)
        cursor = children.get(cursor)
    if len(seen) != len(nodes):
        raise TraceError("invalid_trace_revision_chain")
    return ordered


def _summary(receipt, history):
    latest = history[-1]["resolution"] if history else None
    return {"receipt_id": receipt["receipt_id"], "source_receipt_id": receipt["source_receipt_id"],
        "contract_id": receipt["contract_id"], "event_id": receipt["event_id"],
        "market": receipt["features"].get("market"), "line": receipt["features"].get("line"),
        "base_prob": receipt["base_prob"], "served_prob": receipt["served_prob"],
        "base_model_version_claim": receipt["base_model_version"],
        "calibrator_version_claim": receipt["calibrator_version"],
        "forecast_created_utc_claim": receipt["forecast_created_utc"],
        "event_start_utc_claim": receipt["event_start_utc"],
        "outcome_state": "pending" if latest is None else "void" if latest["status"] == "VOID" else "graded",
        "outcome_hit": latest["hit"] if latest else None,
        "revision_count": len(history), "public_availability": "unverified", "model_adoption": "unverified"}


def _generation_manifest(record, doc, receipts):
    """Validate the sealed manifest and complete ACK partition, not adoption.

    Re-sealing an envelope must not attach unrelated policy/source claims to an
    unchanged generation ID. Rejected source rows remain rejected: this read
    model does not require their malformed forecast payloads to become valid.
    """
    try:
        manifests, originals, ack = doc["generations"], doc["receipts"], record["ack"]
        if (type(doc.get("version")) is not int or doc["version"] != 1
                or type(doc.get("season")) is not int or not 1900 <= doc["season"] <= 2200
                or type(doc.get("week")) is not int or not 1 <= doc["week"] <= 18
                or type(manifests) is not list or len(manifests) != 1 or type(manifests[0]) is not dict
                or type(originals) is not list or not originals
                or type(ack.get("version")) is not int or ack["version"] != 1
                or type(ack.get("rejections")) is not list):
            raise ValueError("invalid structure")
        gen = manifests[0]
        if (type(gen.get("version")) is not int or gen["version"] != 1
                or type(gen.get("season")) is not int or gen["season"] != doc["season"]
                or type(gen.get("week")) is not int or gen["week"] != doc["week"]
                or gen.get("generation_id") != fingerprint({k: gen[k] for k in MANIFEST_FIELDS})
                or gen.get("population") != "eligible_pool_prop_rungs_before_display_dedupe"
                or gen.get("publication_status") != "generated_unverified"
                or any(type(gen[k]) is not str or not HASH.fullmatch(gen[k]) for k in
                       ("selection_policy_version", "joint_model_version"))
                or type(gen["source_snapshots"]) is not dict
                or set(gen["source_snapshots"]) != {"leg_pool", "schedule", "parlay_calibration"}
                or any(type(v) is not str or not HASH.fullmatch(v) for v in gen["source_snapshots"].values())
                or instant(gen["pool_generated_utc"]) > instant(gen["recorded_utc"])
                or instant(record["recorded_utc"]) < instant(gen["recorded_utc"])):
            raise ValueError("invalid manifest")
        source_ids = []
        for original in originals:
            if (type(original) is not dict or type(original.get("receipt_id")) is not str
                    or not HASH.fullmatch(original["receipt_id"])
                    or original.get("generation_id") != gen["generation_id"]):
                raise ValueError("invalid source membership")
            source_ids.append(original["receipt_id"])
        if len(set(source_ids)) != len(source_ids) or gen.get("receipt_ids") != sorted(source_ids):
            raise ValueError("invalid source partition")
        if (type(gen["offered_cards"]) is not list or any(type(c) is not dict
                or type(c.get("card_id")) is not str or not c["card_id"]
                or type(c.get("receipt_ids")) is not list
                or any(type(sid) is not str or sid not in source_ids for sid in c["receipt_ids"])
                for c in gen["offered_cards"])):
            raise ValueError("invalid offering membership")
        stored = {r["receipt_id"]: r for r in receipts}
        partition = []
        for accepted in ack["receipts"]:
            row = stored.get(accepted.get("receipt_id")) if type(accepted) is dict else None
            if (row is None or accepted.get("source_receipt_id") != row["source_receipt_id"]
                    or accepted.get("status") not in ("recorded", "duplicate")):
                raise ValueError("invalid accepted membership")
            partition.append(accepted["source_receipt_id"])
        for rejected in ack["rejections"]:
            if (type(rejected) is not dict or type(rejected.get("source_receipt_id")) is not str
                    or type(rejected.get("reason")) is not str or not rejected["reason"]):
                raise ValueError("invalid rejected membership")
            partition.append(rejected["source_receipt_id"])
        if len(set(partition)) != len(partition) or sorted(partition) != sorted(source_ids):
            raise ValueError("incomplete acknowledgement partition")
        return gen
    except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
        raise TraceError("invalid_trace_generation_manifest") from None


def catalog(snapshot, *, query="", limit=25):
    if type(query) is not str or len(query) > 128 or type(limit) is not int or not 1 <= limit <= 50:
        raise TraceError("invalid_trace_search")
    query = query.strip().casefold()
    receipts = _receipts(snapshot)
    matched = [r for r in receipts if not query or any(query in r[k].casefold()
               for k in ("receipt_id", "source_receipt_id", "contract_id", "event_id"))]
    matched.sort(key=lambda r: (instant(r["forecast_created_utc"]), r["receipt_id"]), reverse=True)
    return {"version": 1, "kind": "nfl.forecast_catalog.v1", "watermark": snapshot["watermark"],
        "total_forecasts": len(receipts), "matched_forecasts": len(matched), "returned": min(limit, len(matched)),
        "records": [_summary(r, _history(snapshot, r)) for r in matched[:limit]],
        "read_only": True, "production_connection_verified": False,
        "scope": "pinned internal forecast journal; not public publication or model adoption"}


def forecast_trace(snapshot, receipt_id):
    if type(receipt_id) is not str or not HASH.fullmatch(receipt_id):
        raise TraceError("invalid_trace_receipt_id")
    receipts = _receipts(snapshot)
    rows = [r for r in receipts if r["receipt_id"] == receipt_id]
    if not rows:
        return None
    receipt = rows[0]
    history = _history(snapshot, receipt)
    generations = []
    for record in snapshot["generation_records"]:
        if (not isinstance(record, dict) or not isinstance(record.get("ack"), dict)
                or type(record["ack"].get("receipts")) is not list):
            raise TraceError("invalid_trace_generation")
        accepted = [r for r in record["ack"].get("receipts", []) if isinstance(r, dict) and r.get("receipt_id") == receipt_id]
        if not accepted:
            continue
        doc = record.get("payload")
        if (len(accepted) != 1 or accepted[0].get("source_receipt_id") != receipt["source_receipt_id"]
                or record["ack"].get("durable") is not True
                or record["ack"].get("object_id") != record.get("object_id") or not isinstance(doc, dict)
                or doc.get("envelope_id") != record.get("object_id")
                or fingerprint({k: v for k, v in doc.items() if k != "envelope_id"}) != record["object_id"]):
            raise TraceError("invalid_trace_generation_binding")
        originals = [r for r in doc.get("receipts", []) if isinstance(r, dict) and r.get("receipt_id") == receipt["source_receipt_id"]]
        manifests = doc.get("generations")
        if (len(originals) != 1 or type(manifests) is not list or len(manifests) != 1
                or not isinstance(manifests[0], dict) or originals[0].get("generation_id") != manifests[0].get("generation_id")):
            raise TraceError("invalid_trace_generation_binding")
        gen = _generation_manifest(record, doc, receipts)
        source = originals[0].get("learning_receipt")
        if (type(source) is not dict or source.get("receipt_id") != receipt["source_receipt_id"]
                or fingerprint({k: v for k, v in source.items() if k != "receipt_id"}) != receipt["source_receipt_id"]
                or any(fingerprint(source.get(k)) != fingerprint(receipt[k]) for k in
                       ("contract_id", "base_prob", "served_prob", "base_model_version", "calibrator_version",
                        "forecast_created_utc", "source_snapshot_id", "source_timestamps", "features"))):
            raise TraceError("invalid_trace_original_forecast_binding")
        generations.append({"envelope_id": record["object_id"], "generation_id": gen.get("generation_id"),
            "database_recorded_utc": record.get("recorded_utc"), "selection_policy_version_claim": gen.get("selection_policy_version"),
            "joint_model_version_claim": gen.get("joint_model_version"),
            "source_snapshot_ids": {k: (gen.get("source_snapshots") or {}).get(k) for k in ("leg_pool", "schedule", "parlay_calibration")},
            "public_availability": "unverified"})
    revisions = []
    for node in history:
        outcome = node["resolution"]
        revisions.append({"revision_id": node["revision_id"], "supersedes": node.get("supersedes"),
            "database_recorded_utc": node["recorded_utc"], "status": outcome["status"], "hit": outcome["hit"],
            "actual": outcome.get("actual"), "source_claim": outcome.get("source"),
            "resolved_utc_claim": outcome["resolved_utc"], "source_updated_utc_claim": outcome.get("source_updated_utc"),
            "evidence_observed_utc_claim": outcome.get("evidence_observed_utc"),
            "evidence_observation_id": outcome.get("evidence_observation_id"),
            "participation_basis": outcome.get("participation_basis")})
    return {"version": 1, "kind": "nfl.forecast_trace.v1", "watermark": snapshot["watermark"],
        "forecast": _summary(receipt, history), "source_snapshot_id": receipt["source_snapshot_id"],
        "source_timestamps_claims": receipt["source_timestamps"], "source_freshness": "unverified",
        "generations": generations, "outcome_revisions": revisions,
        "observed_proof_history_available": type(snapshot.get("observations")) is list,
        "read_only": True, "production_connection_verified": False,
        "warnings": ["Internal capture/database receipt does not prove public pre-event availability.",
                     "Model and calibrator versions are serving claims, not proof of approved adoption.",
                     "Missing source freshness, provider authentication and deployment evidence remain unverified."]}
