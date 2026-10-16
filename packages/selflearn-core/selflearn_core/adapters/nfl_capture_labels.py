"""Recompute staged observed evidence from raw bytes and frozen journal authority.

The caller supplies a TRUSTED server-owned complete journal, not a JSON receipt
subset. This function takes its own pinned snapshot and loads only immutable
objects referenced by its accepted generations. It never imports an NFL checkout
or opens resources. A new journal watermark requires a fresh bind before CAS.

Raw capture integrity is checked here, not supplied as a flag. Every statistics/
participation reread is hashed again before any result is returned. Normalized
``authoritative`` fields are kernel-schema evidence, NOT authenticated provider,
official book settlement, public availability, durable ACK or adoption claims.
Source/getter errors propagate to the trusted caller; HTTP wrappers must sanitize.
"""
from __future__ import annotations

from collections import defaultdict
import copy
import csv
import hashlib
import io
import math
import re
import unicodedata

from . import nfl_capture_evidence as capture
from .nfl import canonical_key, validate_nfl_receipt
from ..probability_receipts import fingerprint, instant, validate_probability_receipt

POLICY = "nfl.capture_labels_v1"
JOIN_POLICY = "frozen_pool_exact_name_team_position_v1"
MAX_PROVENANCE_BYTES = 64 * 1024 * 1024
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SEQ = re.compile(r"(?:0|[1-9][0-9]{0,18})\Z")
_EVENT = re.compile(r"nfl:(\d{4}):game:(.+)\Z")
_REASON = re.compile(r"[a-z][a-z0-9_]{0,99}\Z")
_MARKETS = {"qb_pass_yds": ("QB", "passing_yards"), "rb_rush_yds": ("RB", "rushing_yards"),
            "wr_rec_yds": ("WR", "receiving_yards")}
_LEG_KEYS = {"game_id", "gsis_id", "market", "line", "side", "direction", "comparison_operator", "model_prob", "selection", "position"}
_MANIFEST_KEYS = ("version", "season", "week", "pool_generated_utc", "source_snapshots", "offered_cards",
                  "selection_policy_version", "joint_model_version")
_PARITY = ("base_prob", "served_prob", "base_model_version", "calibrator_version", "forecast_created_utc",
           "source_snapshot_id", "source_timestamps", "features", "publication_status")
_PLAYER_NAMES = {"hollywood brown": "Marquise Brown", "gabe davis": "Gabriel Davis", "cam newton": "Cameron Newton",
    "josh palmer": "Joshua Palmer", "chig okonkwo": "Chigoziem Okonkwo", "d.k. metcalf": "DK Metcalf",
    "d.j. moore": "DJ Moore", "mike thomas": "Michael Thomas"}


class CaptureBindingError(ValueError):
    """Bounded authority/provenance failure; no partial staged result is returned."""


def _fail(code):
    raise CaptureBindingError(code)


def _same(left, right):
    return capture._encoded(left) == capture._encoded(right)


def _hash(value):
    if type(value) is not str or not _HASH.fullmatch(value):
        _fail("invalid_frozen_digest")
    return value


def _name(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    value = _PLAYER_NAMES.get(value.lower(), value)
    parts = value.split(" ")
    if len(parts) > 1 and parts[-1].lower().rstrip(".") in ("iv", "iii", "ii", "jr", "sr"):
        value = " ".join(parts[:-1])
    value = unicodedata.normalize("NFD", value)
    value = "".join(char for char in value if not unicodedata.combining(char)).lower()
    value = re.sub(r"[.'’`]", "", value)
    parts = re.sub(r"[^a-z0-9]+", " ", value).strip().split()
    while len(parts) > 1 and parts[-1] in ("jr", "sr", "ii", "iii", "iv", "v"):
        parts.pop()
    return " ".join(parts)


def _alias(left, right):
    return bool(left and right and " " in left and " " in right
                and left[0] == right[0] and left.split(" ", 1)[1] == right.split(" ", 1)[1])


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _integer(value):
    number = _number(value)
    return int(number) if number is not None and number.is_integer() else None


def _scope(source):
    key = canonical_key(source, source["season"])
    return (key[0], key[1], key[2], key[3], key[5])


def _safe_reason(exc):
    reason = str(exc)
    return reason if _REASON.fullmatch(reason) else "invalid_frozen_member"


def _ack(record, receipt_map):
    doc, ack = record["payload"], record["ack"]
    rows = doc.get("receipts")
    if (type(rows) is not list or type(ack) is not dict
            or set(ack) != {"version", "object_id", "durable", "receipts", "rejections"}
            or type(ack["version"]) is not int or ack["version"] != 1
            or ack["object_id"] != record["object_id"] or ack["durable"] is not True
            or type(ack["receipts"]) is not list or type(ack["rejections"]) is not list):
        _fail("invalid_generation_ack")
    indexed = {}
    for row in rows:
        if type(row) is not dict or _hash(row.get("receipt_id")) in indexed:
            _fail("invalid_generation_receipt_membership")
        indexed[row["receipt_id"]] = row
    accepted, partition = {}, []
    for row in ack["receipts"]:
        if (type(row) is not dict or set(row) != {"source_receipt_id", "receipt_id", "status"}
                or row["status"] not in ("recorded", "duplicate")):
            _fail("invalid_generation_ack_receipt")
        sid, rid = _hash(row["source_receipt_id"]), _hash(row["receipt_id"])
        known = receipt_map.get(rid)
        if known is None or known["source_receipt_id"] != sid:
            _fail("generation_ack_missing_persisted_receipt")
        accepted[sid] = known
        partition.append(sid)
    for row in ack["rejections"]:
        if type(row) is not dict or not isinstance(row.get("reason"), str) or not row["reason"].strip():
            _fail("invalid_generation_ack_rejection")
        partition.append(_hash(row.get("source_receipt_id")))
    if len(partition) != len(set(partition)) or set(partition) != set(indexed):
        _fail("generation_ack_partition_mismatch")
    return indexed, accepted


def _frozen_entries(journal, season, weeks):
    snapshot = copy.deepcopy(journal.read_observed_snapshot())
    if (type(snapshot) is not dict or type(snapshot.get("version")) is not int or snapshot["version"] != 1
            or type(snapshot.get("watermark")) is not str or not _SEQ.fullmatch(snapshot["watermark"])
            or type(snapshot.get("receipts")) is not list or type(snapshot.get("generation_records")) is not list):
        _fail("invalid_complete_journal_snapshot")
    receipt_map, scoped_receipts, original_ids = {}, {}, set()
    for receipt in snapshot["receipts"]:
        validate_probability_receipt(receipt)
        if receipt["receipt_id"] in receipt_map:
            _fail("duplicate_persisted_receipt")
        receipt_map[receipt["receipt_id"]] = receipt
        event = _EVENT.fullmatch(receipt["event_id"])
        if receipt["domain"] == "nfl" and receipt["task"] == "nfl.parlay_leg":
            if event is None:
                _fail("unlocatable_nfl_membership")
            if int(event[1]) == season:
                if receipt["source_receipt_id"] in original_ids:
                    _fail("duplicate_persisted_source_receipt")
                original_ids.add(receipt["source_receipt_id"])
                scoped_receipts[receipt["receipt_id"]] = receipt
    objects, entries, seen, records = {}, {}, set(), set()

    def get_object(oid, recorded):
        _hash(oid)
        if oid not in objects:
            wrapper = copy.deepcopy(journal.get_object(oid))
            if (type(wrapper) is not dict or wrapper.get("kind") != "source_snapshot"
                    or wrapper.get("object_id") != oid or "received_utc" not in wrapper
                    or fingerprint(wrapper.get("payload")) != oid):
                _fail("missing_or_changed_frozen_source_object")
            instant(wrapper["received_utc"])
            objects[oid] = wrapper
        if instant(objects[oid]["received_utc"]) > instant(recorded):
            _fail("frozen_source_imported_after_generation")
        return objects[oid]["payload"]

    for record in snapshot["generation_records"]:
        if type(record) is not dict or not {"object_id", "payload", "ack", "recorded_utc"} <= set(record):
            _fail("invalid_generation_record")
        oid, doc = _hash(record["object_id"]), record["payload"]
        if (oid in records or type(doc) is not dict or doc.get("envelope_id") != oid
                or fingerprint({k: v for k, v in doc.items() if k != "envelope_id"}) != oid):
            _fail("generation_envelope_hash_mismatch")
        records.add(oid)
        instant(record["recorded_utc"])
        indexed, accepted = _ack(record, receipt_map)
        relevant = {sid: stored for sid, stored in accepted.items() if stored["receipt_id"] in scoped_receipts}
        seen.update(stored["receipt_id"] for stored in relevant.values())
        for sid, stored in relevant.items():
            row = indexed[sid]
            source = row.get("learning_receipt")
            # First prove immutable source identity. Unlocatable corruption may
            # not be silently removed before claiming complete group membership.
            if type(source) is not dict or source.get("receipt_id") != sid:
                _fail("unlocatable_original_source_receipt")
            key = validate_nfl_receipt(source, {**source, "model_prob": source.get("served_prob")}, season, source.get("week"))
            if (type(source.get("week")) is not int or not 1 <= source["week"] <= 18
                    or stored["contract_id"] != source["contract_id"]
                    or stored["event_id"] != f"nfl:{season}:game:{key[1]}"
                    or any(not _same(stored[field], source.get(field)) for field in _PARITY)):
                _fail("persisted_original_source_mismatch")
            if (type(doc.get("season")) is not int or doc["season"] != season
                    or type(doc.get("week")) is not int or doc["week"] != source["week"]):
                _fail("unlocatable_generation_period")
            if source["week"] not in weeks:
                continue
            scope = _scope(source)
            entry = {"source": source, "stored": stored, "scope": scope, "identity": None, "game": None,
                     "pool_snapshot_ids": [], "generation_ids": [], "generation_object_ids": [oid], "reasons": []}
            try:
                if type(doc.get("version")) is not int or doc["version"] != 1 or len(doc.get("generations", [])) != 1:
                    _fail("invalid_generation_document")
                generation = doc["generations"][0]
                if (type(generation) is not dict or type(generation.get("version")) is not int or generation["version"] != 1
                        or type(generation.get("season")) is not int or generation["season"] != season
                        or type(generation.get("week")) is not int or generation["week"] != source["week"]
                        or generation.get("publication_status") != "generated_unverified"
                        or generation.get("population") != "eligible_pool_prop_rungs_before_display_dedupe"
                        or generation.get("generation_id") != fingerprint({k: generation[k] for k in _MANIFEST_KEYS})
                        or generation.get("receipt_ids") != sorted(indexed)):
                    _fail("invalid_generation_manifest")
                refs = generation["source_snapshots"]
                if type(refs) is not dict or set(refs) != {"leg_pool", "schedule", "parlay_calibration"}:
                    _fail("invalid_generation_source_references")
                payloads = {name: get_object(value, record["recorded_utc"]) for name, value in refs.items()}
                pool, schedule = payloads["leg_pool"], payloads["schedule"]
                if (type(pool) is not dict or type(pool.get("players")) is not list or type(schedule) is not dict
                        or type(schedule.get("games")) is not list or pool.get("season") != season
                        or pool.get("week") != source["week"] or schedule.get("season", season) != season
                        or pool.get("generated_utc") != generation.get("pool_generated_utc")):
                    _fail("source_generation_period_mismatch")
                candidates = [(player, rung) for player in pool["players"] if type(player) is dict
                    for rung in player.get("rungs", []) if type(rung) is dict and type(rung.get("learning_receipt")) is dict
                    and rung["learning_receipt"].get("receipt_id") == sid]
                if len(candidates) != 1:
                    _fail("missing_or_ambiguous_frozen_player")
                player, rung = candidates[0]
                leg = {**player, **rung}
                if (not _same(rung["learning_receipt"], source) or row.get("generation_id") != generation["generation_id"]
                        or row.get("publication_status") != "generated_unverified"
                        or not _same({k: v for k, v in leg.items() if k in _LEG_KEYS}, row.get("leg"))):
                    _fail("frozen_leg_receipt_mismatch")
                # Legacy frozen compact legs omit the NFL producer's explicit
                # over/gte defaults; source receipts themselves remain exact.
                validate_nfl_receipt(source, {"direction": "over", "comparison_operator": "gte", **leg}, season, source["week"])
                stamps = source.get("source_timestamps")
                if (type(stamps) is not dict or not stamps or any(not isinstance(k, str) or not k.strip() for k in stamps)
                        or source.get("base_model_version") != pool.get("base_model_version")
                        or source.get("forecast_created_utc") != pool.get("generated_utc")):
                    _fail("invalid_frozen_source_lineage")
                for stamp in stamps.values():
                    instant(stamp)
                position = _MARKETS[source["market"]][0]
                if player.get("position") != position or not _name(player.get("player")):
                    _fail("invalid_frozen_player_identity")
                games = [game for game in schedule["games"] if type(game) is dict and str(game.get("game_id")) == source["game_id"]]
                if len(games) != 1:
                    _fail("missing_or_ambiguous_frozen_game")
                game, team = games[0], capture._team(player.get("team"))
                created, recorded, kickoff = instant(source["forecast_created_utc"]), instant(row.get("recorded_utc")), instant(game.get("kickoff_utc"))
                if (team is None or game.get("week") != source["week"] or capture._team(game.get(source["side"])) != team
                        or kickoff != instant(row.get("kickoff_utc")) or kickoff != instant(stored["event_start_utc"])
                        or game.get("status") != "STATUS_SCHEDULED"):
                    _fail("frozen_player_game_mismatch")
                if not created <= recorded < kickoff or row.get("recorded_utc") != generation.get("recorded_utc"):
                    _fail("not_captured_pregame")
                entry.update(identity={"player": player["player"], "team": team, "position": position},
                    game={k: game[k] for k in ("game_id", "week", "home", "away", "kickoff_utc", "status")},
                    pool_snapshot_ids=[refs["leg_pool"]], generation_ids=[generation["generation_id"]])
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                entry["reasons"].append(_safe_reason(exc))
            if sid in entries:
                old = entries[sid]
                if not _same({k: old[k] for k in ("source", "identity", "game")},
                             {k: entry[k] for k in ("source", "identity", "game")}):
                    old["reasons"].append("conflicting_frozen_receipt_identity")
                for field in ("pool_snapshot_ids", "generation_ids", "generation_object_ids", "reasons"):
                    old[field] = sorted(set(old[field] + entry[field]))
            else:
                entries[sid] = entry
    if set(scoped_receipts) - seen:
        _fail("unlocatable_missing_generation_membership")
    return snapshot["watermark"], entries


def _read_rows(observation, raw_chunks):
    """Read full raw CSV again, with terminal hash and receiver work limits."""
    reader = capture._RawReader(raw_chunks(observation["raw_sha256"]), observation["raw_sha256"], observation["raw_bytes"])
    try:
        with io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8-sig", newline="") as text:
            line = text.readline(capture.MAX_CSV_HEADER_BYTES + 1)
            if len(line.encode("utf-8")) > capture.MAX_CSV_HEADER_BYTES:
                _fail("capture_csv_header_exceeds_work_limit")
            header = next(csv.reader([line], strict=True))
            if (not header or len(header) > capture.MAX_CSV_COLUMNS or len(set(header)) != len(header)
                    or any(not key or "\0" in key or len(key) > capture.MAX_CSV_HEADER_CHARS for key in header)):
                _fail("invalid_capture_csv_header")
            class Lines:
                size = 0

                def __iter__(self): return self

                def __next__(self):
                    value = text.readline(capture.MAX_CSV_LINE_BYTES + 1)
                    if not value: raise StopIteration
                    size = len(value.encode("utf-8"))
                    self.size += size
                    if size > capture.MAX_CSV_LINE_BYTES or self.size > capture.MAX_CSV_RECORD_BYTES:
                        _fail("capture_csv_record_exceeds_work_limit")
                    return value
            lines, count = Lines(), 0
            for cells in csv.reader(lines, strict=True):
                lines.size = 0
                if not cells: continue
                count += 1
                if (count > capture.MAX_CSV_ROWS or len(cells) != len(header)
                        or any("\0" in cell or len(cell) > capture.MAX_CSV_FIELD_CHARS for cell in cells)):
                    _fail("invalid_capture_csv_reread")
                yield dict(zip(header, cells))
            if not reader.verified:
                _fail("capture_raw_reread_not_verified")
    except (UnicodeError, csv.Error, StopIteration):
        _fail("invalid_capture_csv_reread")
    finally:
        reader.close()


def _identity(entry):
    identity, source = entry["identity"], entry["source"]
    return (source["week"], _name(identity["player"]), identity["team"], identity["position"])


def _raw_facts(entries, observations, raw_chunks):
    identities = {_identity(entry) for entry in entries.values() if entry["identity"] is not None}
    by_week = defaultdict(list)
    for identity in identities: by_week[identity[0]].append(identity)
    facts = {identity: {"matches": [], "problem": None, "snap_error": None, "snap_alias": False} for identity in identities}
    published, sheets, global_errors, provenance_bytes = set(), {}, {}, 0

    def provenance(row):
        nonlocal provenance_bytes
        encoded = capture._encoded(row)
        provenance_bytes += len(encoded)
        if provenance_bytes > MAX_PROVENANCE_BYTES:
            _fail("capture_provenance_work_exceeds_limit")
        return encoded

    for row in _read_rows(observations[capture.STATS_PROFILE], raw_chunks):
        season, week = _integer(row.get("season")), _integer(row.get("week"))
        name = _name(row.get("player_display_name") or row.get("player_name"))
        team = capture._team(row.get("team") or row.get("recent_team"))
        position = {"FB": "RB", "HB": "RB"}.get(str(row.get("position", "")).upper(), str(row.get("position", "")).upper())
        regular = row.get("season_type") == "REG"
        if season == observations[capture.STATS_PROFILE]["season"] and regular: published.add(week)
        encoded = None
        for identity in by_week.get(week, ()) if week is not None else identities:
            expected_week, expected_name, expected_team, expected_position = identity
            fact = facts[identity]
            same_name, alias = name == expected_name, _alias(name, expected_name)
            if (same_name and season == observations[capture.STATS_PROFILE]["season"] and week == expected_week
                    and regular and team == expected_team and position == expected_position):
                if len(fact["matches"]) < 2:
                    encoded = provenance(row) if encoded is None else encoded
                    market = next(m for m, (pos, _) in _MARKETS.items() if pos == expected_position)
                    fact["matches"].append((_number(row.get(_MARKETS[market][1])), "sha256:" + hashlib.sha256(encoded).hexdigest()))
            if fact["problem"] is None:
                if same_name or alias:
                    if season != observations[capture.STATS_PROFILE]["season"] or week != expected_week or not regular or team != expected_team or position != expected_position:
                        fact["problem"] = "unjoined_stat_identity_requires_crosswalk"
                    elif alias: fact["problem"] = "possible_stat_alias_requires_crosswalk"
                if not name and team in (None, expected_team):
                    fact["problem"] = "unidentified_stat_row_prevents_zero_inference"
    for index, row in enumerate(_read_rows(observations[capture.SNAPS_PROFILE], raw_chunks)):
        season, week = _integer(row.get("season")), _integer(row.get("week"))
        if (row.get("game_type") == "POST" or (week is not None and week not in by_week)
                or (season is not None and season != observations[capture.SNAPS_PROFILE]["season"])):
            continue
        team, name = capture._team(row.get("team")), _name(row.get("player"))
        relevant = by_week[week] if week is not None else identities
        for identity in relevant:
            fact = facts[identity]
            if not team and not name:
                global_errors.setdefault(identity[0], (index, "unlocatable_participation_identity"))
            valid_period = (season == observations[capture.SNAPS_PROFILE]["season"] and week == identity[0]
                            and row.get("game_type") == "REG")
            recognizable = name == identity[1] or _alias(name, identity[1])
            if (team == identity[2] or recognizable) and fact["snap_error"] is None:
                if not valid_period or not team or not name:
                    fact["snap_error"] = (index, "missing_or_invalid_participation_sheet")
                elif recognizable and team != identity[2]:
                    fact["snap_error"] = (index, "unjoined_participation_identity_requires_crosswalk")
            if team == identity[2] and _alias(name, identity[1]): fact["snap_alias"] = True
        if week is None or team is None or not any(identity[2] == team for identity in by_week[week]):
            continue
        sheet = sheets.setdefault((week, team), {"count": 0, "names": set(), "duplicate": False, "invalid": False,
            "hash": hashlib.sha256(b"["), "players": {}})
        encoded = provenance(row)
        if sheet["count"]: sheet["hash"].update(b",")
        sheet["hash"].update(encoded)
        sheet["count"] += 1
        sheet["duplicate"] |= name in sheet["names"]
        sheet["names"].add(name)
        snaps = _number(row.get("offense_snaps"))
        sheet["invalid"] |= not name or snaps is None or snaps < 0 or not snaps.is_integer()
        if any(identity[1] == name and identity[2] == team for identity in by_week[week]):
            sheet["players"][name] = (snaps, "sha256:" + hashlib.sha256(encoded).hexdigest())
    for sheet in sheets.values():
        sheet["hash"].update(b"]")
        sheet["digest"] = "sha256:" + sheet["hash"].hexdigest()
    return facts, published, sheets, global_errors


def _measurement(entry, facts, published, sheets, global_errors, game, observations, capture_id):
    source, identity = entry["source"], entry["identity"]
    if game is None or game["status"] not in capture._FINAL or type(game.get("home_score")) is not int or type(game.get("away_score")) is not int:
        _fail("game_not_final_with_complete_scores")
    frozen = entry["game"]
    if (game["season"] != source["season"] or game["week"] != source["week"]
            or instant(game["kickoff_utc"]) != instant(frozen["kickoff_utc"])
            or any(capture._team(game[side]) != capture._team(frozen[side]) for side in ("home", "away"))):
        _fail("observed_game_frozen_identity_mismatch")
    stat, snap = observations[capture.STATS_PROFILE], observations[capture.SNAPS_PROFILE]
    if instant(stat["observed_utc"]) < instant(game["observed_utc"]):
        _fail("stat_observed_before_final_evidence")
    if source["week"] not in published: _fail("stats_week_not_published")
    key, participation = _identity(entry), {}
    fact = facts[key]
    matches = fact["matches"]
    if len(matches) > 1: _fail("ambiguous_stat_identity")
    if matches:
        actual, row_hash = matches[0]
        if actual is None: _fail("missing_stat_value")
        played, basis, primary, observation = True, "stat_row", "nflverse.stats_player", stat
    else:
        if fact["problem"]: _fail(fact["problem"])
        if instant(snap["observed_utc"]) < instant(game["observed_utc"]): _fail("participation_observed_before_final_evidence")
        errors = [value for value in (fact["snap_error"], global_errors.get(source["week"])) if value is not None]
        if errors: _fail(min(errors)[1])
        sheet = sheets.get((source["week"], identity["team"]))
        if not sheet or not sheet["count"] or sheet["invalid"]: _fail("missing_or_invalid_participation_sheet")
        if sheet["duplicate"]: _fail("ambiguous_participation_sheet")
        if key[1] in sheet["players"]:
            snaps, row_hash = sheet["players"][key[1]]
            if snaps < 1: _fail("zero_offensive_snaps_requires_settlement_evidence")
            played, actual, basis = True, 0.0, "r106_positive_offensive_snaps_no_stat_row"
        else:
            if fact["snap_alias"]: _fail("possible_participation_alias_requires_crosswalk")
            played, actual, basis, row_hash = False, None, "r106_team_snap_absence_inference", sheet["digest"]
        primary, observation = ("nflverse.stats_player", stat) if played else ("nflverse.snap_counts", snap)
        participation = {"participation_source": "nflverse.snap_counts", "participation_observed_utc": snap["observed_utc"],
                         "participation_observation_id": snap["observation_id"]}
    leg = {field: source[field] for field in ("season", "week", "game_id", "player_id", "market", "line", "side", "direction", "comparison_operator")}
    leg.update(identity)
    leg.update(source_receipt_id=source["receipt_id"], model_prob=source["served_prob"], played=played,
        result=("hit" if actual >= source["line"] else "miss") if played else "void", actual=actual,
        reason=None if played else "did_not_play", status=game["status"], authoritative=True, source=primary,
        observed_utc=observation["observed_utc"], observation_id=observation["observation_id"], participation_basis=basis,
        evidence_quality="observed_source_snapshot", evidence_capture_id=capture_id, identity_join_policy=JOIN_POLICY,
        source_row_sha256=row_hash, pool_snapshot_ids=entry["pool_snapshot_ids"], generation_ids=entry["generation_ids"], **participation)
    normalized = {field: game[field] for field in ("season", "week", "game_id", "status", "kickoff_utc", "home", "away", "home_score", "away_score", "observed_utc")}
    normalized.update(source="espn.scoreboard", observation_id=game["source_observation_id"], final_utc=None, source_updated_utc=None)
    return leg, normalized


def bind_capture_labels(journal, capture_envelope, raw_chunks, *, max_total_bytes=capture.DEFAULT_MAX_TOTAL_BYTES):
    """Bind ALL accepted original members at one journal fence; stage only.

    A trusted complete journal supplies read_observed_snapshot()/get_object().
    Neither caller-selected receipts nor a precomputed integrity flag is input.
    Immutable source getters and the entire cohort are frozen before raw reads.
    CAS orchestration MUST call this again on every changed journal watermark.
    """
    if (type(max_total_bytes) is not int or not 0 <= max_total_bytes <= capture.DEFAULT_MAX_TOTAL_BYTES
            or not callable(raw_chunks)):
        _fail("invalid_capture_limit_or_source")
    envelope = capture._snapshot(capture_envelope)
    manifest, sources, _ = capture._preflight(envelope, max_total_bytes)
    watermark, entries = _frozen_entries(journal, manifest["season"], set(manifest["weeks"]))
    integrity = capture.verify_capture_envelope(envelope, raw_chunks, max_total_bytes=max_total_bytes)
    observations = {observation["profile"]: {"observation_id": oid, **observation} for oid, observation in sources
                    if observation["profile"] in (capture.STATS_PROFILE, capture.SNAPS_PROFILE)}
    grouped = defaultdict(list)
    for sid, entry in entries.items(): grouped[entry["scope"]].append(sid)
    groups, legs, games, rejections = [], [], {}, []
    if entries:
        facts, published, sheets, global_errors = _raw_facts(entries, observations, raw_chunks)
    observed_games = {row["game_id"]: row for row in integrity["scoreboard_rows"]}
    for scope, sids in sorted(grouped.items()):
        sids.sort()
        membership = {"binding_policy": POLICY, "scope": list(scope), "receipt_ids": sorted(entries[sid]["stored"]["receipt_id"] for sid in sids),
            "source_receipt_ids": sids, "generation_object_ids": sorted({oid for sid in sids for oid in entries[sid]["generation_object_ids"]})}
        proposed, reasons = [], set()
        identities = {(_name(entries[sid]["identity"]["player"]), entries[sid]["identity"]["team"],
                       entries[sid]["identity"]["position"]) for sid in sids if entries[sid]["identity"]}
        if len(identities) > 1:
            # Equal measured numbers cannot reconcile different frozen people
            # attached to one underlying player ID across model vintages.
            reasons.add("conflicting_frozen_group_identity")
        for sid in sids:
            entry = entries[sid]
            if entry["reasons"]:
                reasons.update(entry["reasons"])
                continue
            if "conflicting_frozen_group_identity" in reasons:
                continue
            try:
                proposed.append(_measurement(entry, facts, published, sheets, global_errors,
                    observed_games.get(entry["source"]["game_id"]), observations, manifest["capture_id"]))
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                reasons.add(_safe_reason(exc))
        if len({fingerprint({key: leg[key] for key in ("actual", "played", "reason")}) for leg, _ in proposed}) > 1:
            reasons.add("conflicting_underlying_stat_observations")
        if reasons:
            rejections.extend({"source_receipt_id": sid, "reason": reason} for sid in sids for reason in sorted(reasons))
        else:
            for leg, game in proposed:
                legs.append(leg)
                games[game["game_id"]] = game
        groups.append({"group_id": fingerprint(list(scope)), **{key: value for key, value in membership.items() if key != "binding_policy"},
                       "membership_id": fingerprint(membership), "status": "withheld" if reasons else "staged", "reason_codes": sorted(reasons)})
    return {"version": 1, "kind": "nfl.capture_labels.v1", "binding_policy": POLICY, "capture_id": manifest["capture_id"],
        "season": manifest["season"], "weeks": manifest["weeks"], "membership_watermark": watermark,
        "membership_id": fingerprint({"binding_policy": POLICY, "groups": [group["membership_id"] for group in groups]}),
        "groups": groups, "source_receipts": [entries[sid]["source"] for sid in sorted(entries)],
        "observed_legs": sorted(legs, key=lambda leg: leg["source_receipt_id"]), "game_observations": games,
        "rejections": sorted(rejections, key=lambda row: (row["source_receipt_id"], row["reason"])), "capture_integrity": integrity,
        "status": "no_supported_membership" if not entries else "staged" if legs else "withheld",
        "scope": "staged_evidence_only", "durable_ack": False}
