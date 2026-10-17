"""Explicit server-only observed-NFL journal; never selected by the live API.

The additive RPC owns the authoritative index, insertion clocks and final
compare-and-swap transaction. This port does not retry a stale fence, chunk an
atomic batch, migrate a database, or fall back to a filesystem journal.
"""
from __future__ import annotations

from collections import defaultdict
import copy
from datetime import datetime, timezone
import json

from .nfl import canonical_key
from .nfl_observed_resolution import (
    BRIDGE_VERSION, _observations,
    _validate_observed_history, observation_record,
)
from .nfl_resolution import _history
from ..probability_receipts import fingerprint, instant
from ..store.receipt_journal import JournalConflict
from ..store.supabase_journal import (
    SupabaseReceiptJournal, _canonical, _clock, _decode, _revision_envelope,
)
from ..store.supabase_store import StoreConflict, StoreError

MAX_BATCH_ITEMS = 500
MAX_BATCH_BYTES = 8 * 1024 * 1024
_READ_RPC = "selflearn_journal_read_observed_v1"
_COMMIT_RPC = "selflearn_journal_commit_observed_v1"


def _validate_game(record, receipt):
    measurement = record.get("measurement")
    if not isinstance(measurement, dict):
        raise ValueError("invalid_observation_measurement")
    key, game = measurement.get("canonical_contract"), measurement.get("game")
    if not isinstance(key, list) or len(key) != 8 or not isinstance(key[4], str):
        raise ValueError("observation_canonical_contract_mismatch")
    try:
        rebuilt = canonical_key({"game_id": key[1], "player_id": key[2], "market": key[3],
            "line": float(key[4]), "side": key[5], "direction": key[6], "comparison_operator": key[7]}, key[0])
    except (ValueError, TypeError, OverflowError):
        raise ValueError("observation_canonical_contract_mismatch") from None
    if (fingerprint(list(rebuilt)) != fingerprint(key) or fingerprint(key) != receipt["contract_id"]
            or not isinstance(game, dict) or set(game) != {"season", "week", "game_id", "kickoff_utc", "home", "away", "state"}
            or type(game.get("season")) is not int or game["season"] != key[0]
            or type(game.get("week")) is not int or not 1 <= game["week"] <= 18
            or game["week"] != measurement.get("week") or game.get("game_id") != key[1]
            or receipt["event_id"] != f"nfl:{key[0]}:game:{key[1]}"
            or game.get("state") != "FINAL"
            or any(not isinstance(game.get(field), str) or not game[field].strip() for field in ("home", "away"))
            or game["home"] == game["away"]
            or instant(game.get("kickoff_utc")) != instant(receipt["event_start_utc"])):
        raise ValueError("observation_game_identity_mismatch")


def _validate_fences(receipts, revisions, observations, observed):
    """Validate complete immutable streams, not an asserted caller ID subset."""
    receipt_map = {r["receipt_id"]: r for r in receipts}
    history, records = defaultdict(list), defaultdict(list)
    for revision in revisions:
        rid = revision["resolution"]["receipt_id"]
        if rid not in receipt_map:
            raise ValueError("outcome_unknown_receipt")
        history[rid].append(revision)
    for record in observations:
        if not isinstance(record, dict) or record.get("receipt_id") not in receipt_map:
            raise ValueError("observation_unknown_receipt")
        receipt = receipt_map[record["receipt_id"]]
        if receipt["domain"] != "nfl" or receipt["task"] != "nfl.parlay_leg":
            raise ValueError("observation_unsupported_receipt")
        _validate_game(record, receipt)
        records[record["receipt_id"]].append(record)
    for rid in set(records) | set(history):
        receipt = receipt_map[rid]
        nodes, _ = _history(history[rid], receipt, observed)
        observed_nodes = [n for n in nodes.values() if n["resolution"].get("evidence_basis") == BRIDGE_VERSION]
        if observed_nodes and len(observed_nodes) != len(nodes):
            raise ValueError("mixed_observed_and_legacy_outcome_history")
        if not records[rid] and not observed_nodes:
            continue
        reference = (records[rid][0]["measurement"] if records[rid] else observed_nodes[0]["resolution"]["measurement"])
        _, known = _observations(records[rid], receipt, observed, reference)
        _validate_observed_history(nodes, receipt)
        for node in observed_nodes:
            value = node["resolution"]
            proof = known.get(value["evidence_observation_id"])
            if proof is None or fingerprint(proof) != fingerprint(observation_record(value)):
                raise ValueError("missing_existing_observation_proof")
        measurement_ids = {n["resolution"]["measurement_id"] for n in observed_nodes}
        if any(r["measurement_id"] not in measurement_ids for r in records[rid]):
            raise ValueError("observation_without_corresponding_outcome")


def _validate_commit(snapshot, observations, revisions, observed):
    """New fences must describe the resulting head, not merely an old state."""
    receipts = {r["receipt_id"]: r for r in snapshot["receipts"]}
    old_history, merged_history, prior_observations = defaultdict(list), defaultdict(list), defaultdict(list)
    for revision in snapshot["revisions"]:
        rid = revision["resolution"]["receipt_id"]
        old_history[rid].append(revision)
        merged_history[rid].append(revision)
    for revision in revisions:
        merged_history[revision["resolution"]["receipt_id"]].append(revision)
    for record in snapshot["observations"]:
        prior_observations[record["receipt_id"]].append(record)
    known_ids = {r["observation_id"] for r in snapshot["observations"]}
    for record in observations:
        if record["observation_id"] in known_ids:
            continue  # An already indexed historical replay cannot move a head.
        _, head = _history(merged_history[record["receipt_id"]], receipts[record["receipt_id"]], observed)
        if head is None or head["resolution"].get("measurement_id") != record["measurement_id"]:
            raise ValueError("new_observation_requires_current_outcome")
    known_revisions = {r["revision_id"] for r in snapshot["revisions"]}
    for revision in revisions:
        if revision["revision_id"] in known_revisions:
            continue
        value = revision["resolution"]
        rid = value["receipt_id"]
        _, head = _history(old_history[rid], receipts[rid], observed)
        if head:
            previous = head["resolution"]
            if previous.get("measurement_id") == value["measurement_id"]:
                raise ValueError("unchanged_measurement_requires_original_revision")
            if instant(value["resolved_utc"]) <= instant(previous["resolved_utc"]):
                raise ValueError("stale_conflicting_observation")
        watermarks = {}
        for record in prior_observations[rid]:
            for source, component in record["source_observations"].items():
                if source not in watermarks or instant(component["observed_utc"]) > instant(watermarks[source]["observed_utc"]):
                    watermarks[source] = component
        for source, component in value["source_observations"].items():
            old = watermarks.get(source)
            if old and instant(component["observed_utc"]) < instant(old["observed_utc"]):
                raise ValueError("stale_conflicting_source_observation")
            if old and source == value["source"] and (
                    instant(component["observed_utc"]) <= instant(old["observed_utc"])
                    or component["observation_id"] == old["observation_id"]):
                raise ValueError("stale_conflicting_primary_observation")


class NFLObservedJournal(SupabaseReceiptJournal):
    """Opt-in additive journal with a complete observation-aware global fence.

    Constructing this class performs no I/O. Existing API configuration still
    selects SupabaseReceiptJournal, never this class. Successful reads verify
    the new RPC exists; they do not assert hosted deployment/concurrency proof.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._observed_snapshot = None

    def _observed_rpc(self, name, data):
        try:
            response = self._request("POST", "/rest/v1/rpc/" + name, data=data)
        except StoreConflict:
            raise JournalConflict("observed_journal_conflict_reread_required") from None
        except StoreError as exc:
            raise StoreError(exc.status) from None
        except Exception:
            raise StoreError(503) from None
        if (not isinstance(response, dict) or type(response.get("version")) is not int
                or response["version"] != 1):
            raise StoreError(502)
        return response

    def _rpc(self, operation, data):
        # Reuse every base page/cursor check without duplicating its loop.
        if operation == "read":
            return self._observed_rpc(_READ_RPC, data)
        return super()._rpc(operation, data)

    def append_resolution(self, resolution, *, revision_id, supersedes=None, reason=None):
        if isinstance(resolution, dict) and resolution.get("evidence_basis") == BRIDGE_VERSION:
            raise ValueError("observed_resolution_requires_atomic_commit")
        return super().append_resolution(resolution, revision_id=revision_id, supersedes=supersedes, reason=reason)

    def _load_observed_snapshot(self, watermark=None):
        self._observed_snapshot = None
        receipt_rows, watermark = self._read("receipts", watermark=watermark)
        receipts = self._receipt_rows(receipt_rows)
        receipt_map = {r["receipt_id"]: r for r in receipts}
        generation_rows, _ = self._read("generations", watermark=watermark)
        generations = self._generation_rows(generation_rows)
        for generation in generations:
            for accepted in generation["ack"]["receipts"]:
                stored = receipt_map.get(accepted["receipt_id"])
                if stored is None or stored["source_receipt_id"] != accepted["source_receipt_id"]:
                    raise StoreError(502)
        revision_rows, _ = self._read("revisions", watermark=watermark)
        revisions = self._revision_rows(revision_rows, receipt_map)
        self._validate_chains(revisions)
        snapshot = {"version": 1, "watermark": watermark,
            "receipts": sorted(receipts, key=lambda r: r["receipt_id"]),
            "generation_records": generations, "revisions": revisions}
        rows, _ = self._read("observations", watermark=snapshot["watermark"])
        records, seen = [], set()
        try:
            for row in rows:
                if set(row) != {"observation_id", "receipt_id", "payload_json", "recorded_utc", "journal_seq"}:
                    raise ValueError("invalid_observation_row")
                payload = _decode(row["payload_json"])
                if (not isinstance(payload, dict) or payload.get("observation_id") != row["observation_id"]
                        or payload.get("receipt_id") != row["receipt_id"] or row["observation_id"] in seen
                        or instant(payload.get("observed_utc")) > instant(_clock(row["recorded_utc"]))):
                    raise ValueError("invalid_observation_identity_or_clock")
                seen.add(row["observation_id"])
                records.append({"observation_id": row["observation_id"], "payload": payload,
                                "recorded_utc": row["recorded_utc"]})
            observations = [r["payload"] for r in records]
            clocks = [r["recorded_utc"] for r in records] + [r["recorded_utc"] for r in snapshot["revisions"]]
            observed = max((instant(c) for c in clocks), default=datetime.now(timezone.utc))
            _validate_fences(snapshot["receipts"], snapshot["revisions"], observations, observed)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
            raise StoreError(502) from None
        snapshot.update(observations=observations, observation_records=records)
        self._observed_snapshot = copy.deepcopy(snapshot)
        return snapshot

    def read_observed_snapshot(self):
        """Read every receipt, generation, revision and observation at one fence."""
        return self._load_observed_snapshot()

    def commit_observed(self, observations, proposals, *, watermark):
        """CAS one bounded atomic batch; stale writers must reread and recompute."""
        self._sequence(watermark)
        if (not isinstance(observations, list) or not isinstance(proposals, list)
                or len(observations) > MAX_BATCH_ITEMS or len(proposals) > MAX_BATCH_ITEMS):
            raise ValueError("observed_batch_array_bound")
        revisions = []
        for proposal in proposals:
            if not isinstance(proposal, dict) or set(proposal) != {"revision_id", "supersedes", "reason", "resolution"}:
                raise ValueError("invalid_observed_revision_proposal")
            if not isinstance(proposal["resolution"], dict) or proposal["resolution"].get("evidence_basis") != BRIDGE_VERSION:
                raise ValueError("observed_batch_requires_v2_resolution")
            revisions.append(_revision_envelope(**proposal))
        batch = {"version": 1, "observations": observations, "revisions": revisions}
        encoded = _canonical(batch)
        data = {"p_batch_json": encoded, "p_watermark": watermark}
        if len(encoded.encode("utf-8")) > MAX_BATCH_BYTES or len(json.dumps(data, allow_nan=False).encode("utf-8")) > MAX_BATCH_BYTES:
            raise ValueError("observed_batch_byte_bound")
        if any(not isinstance(r, dict) or not isinstance(r.get("observation_id"), str) for r in observations):
            raise ValueError("invalid_observed_record")
        if (len({r["observation_id"] for r in observations}) != len(observations)
                or len({r["revision_id"] for r in revisions}) != len(revisions)):
            raise ValueError("duplicate_observed_batch_identity")
        snapshot = self._observed_snapshot
        if snapshot is None or snapshot["watermark"] != watermark:
            snapshot = self._load_observed_snapshot(watermark)
        combined = snapshot["revisions"] + revisions
        observed = datetime.now(timezone.utc)
        _validate_fences(snapshot["receipts"], combined, snapshot["observations"] + observations, observed)
        _validate_commit(snapshot, observations, revisions, observed)
        prior_observation_ids = {r["observation_id"] for r in snapshot["observations"]}
        prior_revision_ids = {r["revision_id"] for r in snapshot["revisions"]}
        expected_observations = sum(r["observation_id"] not in prior_observation_ids for r in observations)
        expected_revisions = sum(r["revision_id"] not in prior_revision_ids for r in revisions)
        try:
            response = self._observed_rpc(_COMMIT_RPC, data)
        except (JournalConflict, StoreError):
            self._observed_snapshot = None
            raise
        self._observed_snapshot = None  # Successful writes also invalidate the fence.
        if (set(response) != {"version", "added_observations", "added_revisions", "watermark"}
                or type(response.get("added_observations")) is not int
                or type(response.get("added_revisions")) is not int
                or not 0 <= response["added_observations"] <= len(observations)
                or not 0 <= response["added_revisions"] <= len(revisions)
                or response["added_observations"] != expected_observations
                or response["added_revisions"] != expected_revisions
                or self._sequence(response.get("watermark")) < self._sequence(watermark)):
            raise StoreError(502)
        added = response["added_observations"] + response["added_revisions"]
        if bool(added) != (response["watermark"] != watermark):
            raise StoreError(502)
        return response
