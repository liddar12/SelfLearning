"""Independent NFL raw-capture integrity, NOT outcome or provider authority.

Only the three versioned producer profiles are supported. This module opens no
resources, imports no NFL checkout and derives no player grades. Caller-owned
``raw_chunks(digest)`` yields canonical 1 MiB byte chunks except the final chunk;
it never receives a URL. Trusted callback errors propagate and a future HTTP
wrapper must sanitize them and enforce its actual incoming wire-byte limit.

Verified hashes, audits and ordered DECLARED clocks do not authenticate provider
bytes, historical retrieval, publication, sportsbook settlement or database time.
No label, durable ACK or partial successful report is emitted by this module.
Receiver CSV work limits below are stricter than the original producer's raw
capture checks. A producer-valid source outside them is held as unsupported
work, not evidence that the provider bytes are corrupt or its labels incorrect.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import re

from ..evidence_chunks import MAX_CHUNK_BYTES, MAX_REQUEST_BYTES, DEFAULT_MAX_TOTAL_BYTES
from ..probability_receipts import fingerprint, instant

STATS_PROFILE = "nflverse_stats_player_v1"
SNAPS_PROFILE = "nflverse_snap_counts_v1"
SCOREBOARD_PROFILE = "espn_scoreboard_v1"
CSV_LIMIT = 16 * 1024 * 1024
SCOREBOARD_LIMIT = 4 * 1024 * 1024
MAX_METADATA_DOCUMENT = 65536
# Versioned receiver work limits, not claims about upstream/producer validation.
MAX_CSV_COLUMNS = 256
MAX_CSV_HEADER_CHARS = 128
MAX_CSV_HEADER_BYTES = 16 * 1024
MAX_CSV_LINE_BYTES = 256 * 1024
MAX_CSV_RECORD_BYTES = 1024 * 1024
MAX_CSV_FIELD_CHARS = 128 * 1024
MAX_CSV_ROWS = 250000
POLICY = "nfl.capture_integrity_v1"
_CLOCK_POLICY = "producer observations only; upstream issue and exact game-finish times unknown"
_PUBLICATION = "source_capture_not_public_forecast_evidence"
_MEASUREMENT_POLICY = "raw values retained; missing/negative cells are not coerced or graded"
_FINAL = frozenset(("STATUS_FINAL", "STATUS_FINAL_OVERTIME"))
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}\Z")
_MANIFEST = {"version", "kind", "season", "weeks", "started_utc", "completed_utc", "sources",
             "clock_policy", "publication_status", "capture_id", "audit"}
_OBSERVATION = {"version", "kind", "evidence_class", "profile", "season", "week", "source_url",
                "raw_sha256", "raw_bytes", "fetch_started_utc", "observed_utc", "upstream_updated_utc", "http", "audit"}
_SCORE_COUNTS = {"events", "normalized_games", "final_games", "nonfinal_games", "malformed_events",
                 "duplicate_events", "conflicting_game_ids", "invalid_final_scores"}
_TEAMS = ("ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN", "DET", "GB",
          "HOU", "IND", "JAX", "KC", "LV", "LAC", "LAR", "MIA", "MIN", "NE", "NO", "NYG",
          "NYJ", "PHI", "PIT", "SF", "SEA", "TB", "TEN", "WAS")
_TEAM_NAMES = ("arizona cardinals", "atlanta falcons", "baltimore ravens", "buffalo bills", "carolina panthers",
    "chicago bears", "cincinnati bengals", "cleveland browns", "dallas cowboys", "denver broncos",
    "detroit lions", "green bay packers", "houston texans", "indianapolis colts", "jacksonville jaguars",
    "kansas city chiefs", "las vegas raiders", "los angeles chargers", "los angeles rams", "miami dolphins",
    "minnesota vikings", "new england patriots", "new orleans saints", "new york giants", "new york jets",
    "philadelphia eagles", "pittsburgh steelers", "san francisco 49ers", "seattle seahawks",
    "tampa bay buccaneers", "tennessee titans", "washington commanders")
_RENAMES = dict(zip(_TEAM_NAMES, _TEAMS)) | {"wsh": "WAS", "oak": "LV", "sd": "LAC", "stl": "LAR", "la": "LAR"}


class CaptureIntegrityError(ValueError):
    """Sanitized integrity failure; never a trusted source callback exception."""


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode("utf-8")


def _fields(value, names):
    if type(value) is not dict or len(value) != len(names) or set(value) != names:
        raise CaptureIntegrityError("invalid_capture_fields")


def _hash(value):
    if type(value) is not str or len(value) != 71 or not _HASH.fullmatch(value):
        raise CaptureIntegrityError("invalid_capture_digest")
    return value


def _clock(value):
    if type(value) is not str or len(value) > 128:
        raise CaptureIntegrityError("invalid_capture_clock")
    try:
        return instant(value)
    except (ValueError, OverflowError):
        raise CaptureIntegrityError("invalid_capture_clock") from None


def _snapshot(value, depth=0, budget=None):
    """Bound metadata BEFORE canonical encoding; copy away all callback aliases."""
    budget = [0, 0] if budget is None else budget
    budget[0] += 1
    if depth > 24 or budget[0] > 16384:
        raise CaptureIntegrityError("capture_metadata_too_complex")
    kind = type(value)
    if kind is str:
        if len(value) > MAX_METADATA_DOCUMENT:
            raise CaptureIntegrityError("capture_metadata_field_too_large")
        budget[1] += len(value)
        if budget[1] > MAX_REQUEST_BYTES:
            raise CaptureIntegrityError("capture_metadata_too_large")
        return value
    if value is None or kind is bool:
        return value
    if kind is int and value.bit_length() <= 63:
        return value
    if kind is list and len(value) <= 2048:
        return [_snapshot(item, depth + 1, budget) for item in value]
    if kind is dict and len(value) <= 2048:
        if any(type(key) is not str or len(key) > 128 for key in value):
            raise CaptureIntegrityError("invalid_capture_metadata_key")
        return {key: _snapshot(item, depth + 1, budget) for key, item in value.items()}
    raise CaptureIntegrityError("invalid_capture_metadata_type")


def _ids(value, maximum):
    if (type(value) is not list or len(value) > maximum
            or any(type(item) is not str or not _GAME.fullmatch(item) for item in value)
            or value != sorted(set(value))):
        raise CaptureIntegrityError("invalid_capture_quarantine_ids")


def _audit_shape(audit, profile=None):
    if profile in (STATS_PROFILE, SNAPS_PROFILE):
        _fields(audit, {"rows", "duplicate_rows", "outside_regular_week_rows", "measurement_policy"})
        if (type(audit["rows"]) is not int or not 1 <= audit["rows"] <= CSV_LIMIT
                or any(type(audit[k]) is not int or not 0 <= audit[k] <= audit["rows"]
                       for k in ("duplicate_rows", "outside_regular_week_rows"))
                or audit["measurement_policy"] != _MEASUREMENT_POLICY):
            raise CaptureIntegrityError("invalid_capture_csv_audit")
    elif profile == SCOREBOARD_PROFILE:
        _fields(audit, _SCORE_COUNTS | {"quarantined_game_ids"})
        if any(type(audit[k]) is not int or not 0 <= audit[k] <= 32 for k in _SCORE_COUNTS) or not audit["events"]:
            raise CaptureIntegrityError("invalid_capture_scoreboard_audit")
        _ids(audit["quarantined_game_ids"], 32)
    else:
        _fields(audit, {"cross_week_conflicting_game_ids", "quarantined_game_ids", "normalized_games"})
        if type(audit["normalized_games"]) is not int or not 0 <= audit["normalized_games"] <= 576:
            raise CaptureIntegrityError("invalid_capture_global_audit")
        _ids(audit["cross_week_conflicting_game_ids"], 576)
        _ids(audit["quarantined_game_ids"], 576)


def _spec(profile, season, week):
    if profile == SCOREBOARD_PROFILE and type(week) is int and 1 <= week <= 18:
        return ("https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?"
                f"seasontype=2&dates={season}&week={week}"), SCOREBOARD_LIMIT
    if profile == STATS_PROFILE and week is None:
        return ("https://github.com/nflverse/nflverse-data/releases/download/stats_player/"
                f"stats_player_week_{season}.csv"), CSV_LIMIT
    if profile == SNAPS_PROFILE and week is None:
        return ("https://github.com/nflverse/nflverse-data/releases/download/snap_counts/"
                f"snap_counts_{season}.csv"), CSV_LIMIT
    raise CaptureIntegrityError("unsupported_capture_profile")


def _preflight(envelope, maximum):
    value = _snapshot(envelope)
    _fields(value, {"version", "kind", "scope", "manifest", "observations", "raw_objects"})
    if (type(value["version"]) is not int or value["version"] != 1 or value["kind"] != "nfl.capture_envelope.v1"
            or value["scope"] != "uploaded_only"):
        raise CaptureIntegrityError("invalid_capture_envelope")
    manifest, observations, descriptors = value["manifest"], value["observations"], value["raw_objects"]
    _fields(manifest, _MANIFEST)
    if (type(manifest["version"]) is not int or manifest["version"] != 1
            or manifest["kind"] != "nfl.outcome_capture.v1" or manifest["clock_policy"] != _CLOCK_POLICY
            or manifest["publication_status"] != _PUBLICATION or type(manifest["season"]) is not int
            or not 2000 <= manifest["season"] <= 2100):
        raise CaptureIntegrityError("invalid_capture_manifest")
    weeks = manifest["weeks"]
    if (type(weeks) is not list or not 1 <= len(weeks) <= 18
            or any(type(week) is not int or not 1 <= week <= 18 for week in weeks)
            or weeks != sorted(set(weeks))):
        raise CaptureIntegrityError("invalid_capture_weeks")
    specs = [(SCOREBOARD_PROFILE, week) for week in weeks] + [(STATS_PROFILE, None), (SNAPS_PROFILE, None)]
    if (type(manifest["sources"]) is not list or len(manifest["sources"]) != len(specs)
            or type(observations) is not dict or len(observations) != len(specs)
            or type(descriptors) is not list or not 1 <= len(descriptors) <= 20):
        raise CaptureIntegrityError("invalid_capture_membership")
    _audit_shape(manifest["audit"])
    if (len(_encoded(manifest)) > MAX_METADATA_DOCUMENT or len(_encoded(value)) > MAX_REQUEST_BYTES
            or _hash(manifest["capture_id"]) != fingerprint({k: v for k, v in manifest.items() if k != "capture_id"})):
        raise CaptureIntegrityError("capture_manifest_hash_or_size_mismatch")
    objects = {}
    for descriptor in descriptors:
        _fields(descriptor, {"raw_sha256", "raw_bytes"})
        raw_id, size = _hash(descriptor["raw_sha256"]), descriptor["raw_bytes"]
        if raw_id in objects or type(size) is not int or not 1 <= size <= CSV_LIMIT:
            raise CaptureIntegrityError("invalid_capture_raw_descriptor")
        objects[raw_id] = size
    previous, completed = _clock(manifest["started_utc"]), _clock(manifest["completed_utc"])
    expected_observations, expected_raw, total, sources = set(), set(), 0, []
    for ref, (profile, week) in zip(manifest["sources"], specs):
        _fields(ref, {"profile", "season", "week", "observation_id"})
        oid = _hash(ref["observation_id"])
        expected_ref = {"profile": profile, "season": manifest["season"], "week": week, "observation_id": oid}
        if _encoded(ref) != _encoded(expected_ref) or oid not in observations:
            raise CaptureIntegrityError("capture_source_spec_mismatch")
        observation = observations[oid]
        _fields(observation, _OBSERVATION)
        url, limit = _spec(profile, manifest["season"], week)
        if (type(observation["version"]) is not int or observation["version"] != 1
                or observation["kind"] != "nfl.outcome_source_observation.v1"
                or observation["evidence_class"] != "observed_source_snapshot"
                or any(_encoded(observation[k]) != _encoded(expected_ref[k]) for k in ("profile", "season", "week"))
                or observation["source_url"] != url or observation["upstream_updated_utc"] is not None):
            raise CaptureIntegrityError("invalid_capture_source_observation")
        http = observation["http"]
        if (type(http) is not dict or not {"status"} <= set(http) <= {"status", "etag", "last_modified", "content_type"}
                or type(http["status"]) is not int or http["status"] != 200
                or any(type(v) is not str or len(v) > 512 or any(ord(c) < 32 for c in v)
                       for k, v in http.items() if k != "status")):
            raise CaptureIntegrityError("invalid_capture_http_metadata")
        _audit_shape(observation["audit"], profile)
        if len(_encoded(observation)) > MAX_METADATA_DOCUMENT or fingerprint(observation) != oid:
            raise CaptureIntegrityError("capture_observation_hash_or_size_mismatch")
        began, observed = _clock(observation["fetch_started_utc"]), _clock(observation["observed_utc"])
        if not previous <= began <= observed <= completed:
            raise CaptureIntegrityError("capture_clock_regressed")
        previous = observed
        rid, size = _hash(observation["raw_sha256"]), observation["raw_bytes"]
        if type(size) is not int or not 1 <= size <= limit or objects.get(rid) != size:
            raise CaptureIntegrityError("capture_source_size_mismatch")
        total += size  # Charge EACH source reference, not deduplicated storage bytes.
        if total > maximum:
            raise CaptureIntegrityError("capture_total_size_exceeds_limit")
        expected_observations.add(oid)
        expected_raw.add(rid)
        sources.append((oid, observation))
    if set(observations) != expected_observations or set(objects) != expected_raw:
        raise CaptureIntegrityError("capture_object_membership_mismatch")
    return manifest, sources, objects


class _RawReader(io.RawIOBase):
    """Caller-owned canonical fragments, with bounded reads and a terminal hash."""
    def __init__(self, iterator, raw_id, size):
        self.iterator, self.raw_id, self.size = iter(iterator), raw_id, size
        self.digest, self.total, self.pending, self.position = hashlib.sha256(), 0, b"", 0
        self.verified = False

    def readable(self):
        return True

    def _fill(self):
        try:
            part = next(self.iterator)
        except StopIteration:
            if self.total != self.size or "sha256:" + self.digest.hexdigest() != self.raw_id:
                raise CaptureIntegrityError("capture_raw_hash_or_size_mismatch") from None
            self.verified = True
            return False
        if type(part) is not bytes or len(part) != min(MAX_CHUNK_BYTES, self.size - self.total) or not part:
            raise CaptureIntegrityError("invalid_capture_raw_chunk_framing")
        self.digest.update(part)
        self.total += len(part)
        self.pending, self.position = part, 0
        return True

    def readinto(self, target):
        count = 0
        while count < len(target):
            if self.position == len(self.pending) and not self._fill():
                break
            take = min(len(target) - count, len(self.pending) - self.position)
            target[count:count + take] = self.pending[self.position:self.position + take]
            self.position += take
            count += take
        return count

    def close(self):
        if not self.closed:
            closer = getattr(self.iterator, "close", None)
            try:
                if callable(closer):
                    closer()
            finally:
                super().close()


def _csv_audit(reader, observation):
    try:
        with io.TextIOWrapper(io.BufferedReader(reader), encoding="utf-8-sig", newline="") as text:
            # Header metadata cannot be multiplied by every subsequent row.
            header_line = text.readline(MAX_CSV_HEADER_BYTES + 1)
            if len(header_line) > MAX_CSV_HEADER_BYTES or len(header_line.encode("utf-8")) > MAX_CSV_HEADER_BYTES:
                raise CaptureIntegrityError("capture_csv_header_exceeds_work_limit")
            header = next(csv.reader([header_line], strict=True))
            if (not header or len(header) > MAX_CSV_COLUMNS or len(set(header)) != len(header)
                    or any(not key or "\0" in key or len(key) > MAX_CSV_HEADER_CHARS for key in header)):
                raise CaptureIntegrityError("invalid_capture_csv_header")
            profile = observation["profile"]
            required = ({"season", "week", "position", "passing_yards", "rushing_yards", "receiving_yards"}
                        if profile == STATS_PROFILE else {"season", "week", "team", "player", "offense_snaps"})
            if (not required <= set(header) or (profile == STATS_PROFILE and (
                    not {"team", "recent_team"} & set(header) or not {"player_display_name", "player_name"} & set(header)))):
                raise CaptureIntegrityError("missing_capture_csv_columns")
            class Lines:
                record_bytes = 0

                def __iter__(self):
                    return self

                def __next__(self):
                    line = text.readline(MAX_CSV_LINE_BYTES + 1)
                    if not line:
                        raise StopIteration
                    if len(line) > MAX_CSV_LINE_BYTES or len(line.encode("utf-8")) > MAX_CSV_LINE_BYTES:
                        raise CaptureIntegrityError("capture_csv_line_exceeds_work_limit")
                    self.record_bytes += len(line.encode("utf-8"))
                    if self.record_bytes > MAX_CSV_RECORD_BYTES:
                        raise CaptureIntegrityError("capture_csv_record_exceeds_work_limit")
                    return line

            lines = Lines()
            rows = csv.reader(lines, strict=True)
            season_index, week_index = header.index("season"), header.index("week")
            seen, count, duplicates, outside = set(), 0, 0, 0
            for cells in rows:
                lines.record_bytes = 0
                if not cells:
                    continue
                if len(cells) != len(header) or any("\0" in cell or len(cell) > MAX_CSV_FIELD_CHARS for cell in cells):
                    raise CaptureIntegrityError("malformed_capture_csv_row")
                try:
                    if int(cells[season_index]) != observation["season"]:
                        raise ValueError()
                    outside += int(not 1 <= int(cells[week_index]) <= 18)
                except (ValueError, OverflowError):
                    raise CaptureIntegrityError("capture_csv_period_mismatch") from None
                # Under one unique fixed header, equal cell arrays and equal
                # canonical row dictionaries are the SAME duplicate relation.
                # Fixed-size hashes use the same collision assumption as the
                # existing raw content IDs; unused headers aren't rehashed.
                row_digest = hashlib.sha256(_encoded(cells)).digest()
                duplicates += int(row_digest in seen)
                seen.add(row_digest)
                count += 1
                if count > MAX_CSV_ROWS:
                    raise CaptureIntegrityError("capture_csv_rows_exceed_work_limit")
            if not count:
                raise CaptureIntegrityError("empty_capture_csv_source")
            return {"rows": count, "duplicate_rows": duplicates, "outside_regular_week_rows": outside,
                    "measurement_policy": _MEASUREMENT_POLICY}
    except (UnicodeError, csv.Error, StopIteration):
        raise CaptureIntegrityError("invalid_capture_csv_source") from None


def _pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CaptureIntegrityError("duplicate_capture_json_key")
        value[key] = item
    return value


def _json(raw):
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(CaptureIntegrityError("nonfinite_capture_json")))
        stack = [(value, 0)]
        while stack:
            node, depth = stack.pop()
            if depth > 24 or (type(node) is float and not math.isfinite(node)):
                raise CaptureIntegrityError("invalid_capture_json_depth_or_number")
            children = node.values() if type(node) is dict else node if type(node) is list else ()
            stack.extend((child, depth + 1) for child in children)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, CaptureIntegrityError):
            raise
        raise CaptureIntegrityError("invalid_capture_json_source") from None


def _team(value):
    if value is None:
        return None
    value = str(value).strip()
    return value.upper() if value.upper() in _TEAMS else _RENAMES.get(value.lower())


def _score(value):
    if type(value) is bool:
        return None
    try:
        number = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return int(number) if math.isfinite(number) and number >= 0 and number.is_integer() else None


def _scoreboard(raw, observation):
    doc = _json(raw)
    if type(doc) is not dict or type(doc.get("events")) is not list or not 1 <= len(doc["events"]) <= 32:
        raise CaptureIntegrityError("invalid_capture_scoreboard")
    season, week = observation["season"], observation["week"]
    for metadata, field, expected in ((doc.get("season"), "year", season), (doc.get("week"), "number", week)):
        if metadata is not None and (type(metadata) is not dict or type(metadata.get(field)) is not int or metadata[field] != expected):
            raise CaptureIntegrityError("capture_scoreboard_period_mismatch")
    season_type = (doc.get("season") or {}).get("type")
    if season_type is not None and (type(season_type) is not int or season_type != 2):
        raise CaptureIntegrityError("capture_scoreboard_reg_mismatch")
    rows, originals, conflicts, malformed = {}, {}, set(), set()
    audit = {key: 0 for key in _SCORE_COUNTS}
    audit["events"] = len(doc["events"])
    for event in doc["events"]:
        gid = None
        try:
            if type(event) is not dict or type(event.get("id")) is not str or not _GAME.fullmatch(event["id"]):
                raise ValueError()
            gid = event["id"]
            if gid in originals:
                audit["duplicate_events"] += 1
                if _encoded(originals[gid]) != _encoded(event):
                    conflicts.add(gid)
                continue
            originals[gid] = event
            status, kickoff = event["status"]["type"]["name"], event["date"]
            if type(status) is not str or not status.startswith("STATUS_"):
                raise ValueError()
            _clock(kickoff)
            competitions = event["competitions"]
            if type(competitions) is not list or len(competitions) != 1:
                raise ValueError()
            for status_doc in (event["status"], competitions[0].get("status")):
                if status_doc is None:
                    continue
                if type(status_doc) is not dict or type(status_doc.get("type")) is not dict:
                    raise ValueError()
                other = status_doc["type"]
                if (("name" in other and other["name"] != status) or ("completed" in other and (
                        type(other["completed"]) is not bool or other["completed"] != (status in _FINAL)))):
                    raise ValueError()
            for metadata, field, expected in ((event.get("season"), "year", season), (event.get("week"), "number", week)):
                if metadata is not None and (type(metadata) is not dict or type(metadata.get(field)) is not int or metadata[field] != expected):
                    raise ValueError()
            event_type = (event.get("season") or {}).get("type")
            if event_type is not None and (type(event_type) is not int or event_type != 2):
                raise ValueError()
            competitors = competitions[0]["competitors"]
            if type(competitors) is not list or len(competitors) != 2:
                raise ValueError()
            sides = {competitor["homeAway"]: competitor for competitor in competitors}
            if set(sides) != {"home", "away"}:
                raise ValueError()
            teams = {side: _team(competitor["team"]["abbreviation"]) for side, competitor in sides.items()}
            if None in teams.values() or teams["home"] == teams["away"]:
                raise ValueError()
            row = {"season": season, "week": week, "game_id": gid, "status": status, "kickoff_utc": kickoff,
                   **teams, "final": status in _FINAL, "final_utc": None, "upstream_updated_utc": None}
            if status in _FINAL:
                home, away = _score(sides["home"].get("score")), _score(sides["away"].get("score"))
                if home is None or away is None or _clock(kickoff) > _clock(observation["observed_utc"]):
                    audit["invalid_final_scores"] += 1
                    raise ValueError()
                row.update(home_score=home, away_score=away)
            rows[gid] = row
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            audit["malformed_events"] += 1
            if gid is not None:
                malformed.add(gid)
    for gid in conflicts:
        rows.pop(gid, None)
    audit.update(conflicting_game_ids=len(conflicts), quarantined_game_ids=sorted(conflicts | malformed),
                 normalized_games=len(rows), final_games=sum(row["final"] for row in rows.values()))
    audit["nonfinal_games"] = len(rows) - audit["final_games"]
    return [rows[gid] for gid in sorted(rows)], audit


def verify_capture_envelope(envelope, raw_chunks, *, max_total_bytes=DEFAULT_MAX_TOTAL_BYTES):
    """Verify a complete staged raw capture, returning integrity metadata only.

    ``raw_chunks(sha256_id)`` must yield exact 1 MiB chunks except the final
    chunk, then terminate. All metadata is snapshotted/bounded before callbacks.
    Source multiplicity counts toward the 64 MiB budget even when bytes dedupe.
    The caller owns iterator resources; generator ``close`` is invoked on exit.
    No report is returned if any later source/hash/parser/callback fails.
    """
    if (type(max_total_bytes) is not int or not 0 <= max_total_bytes <= DEFAULT_MAX_TOTAL_BYTES
            or not callable(raw_chunks)):
        raise CaptureIntegrityError("invalid_capture_limit_or_source")
    manifest, sources, objects = _preflight(envelope, max_total_bytes)
    source_audits, games, withheld, actual = [], [], set(), 0
    for oid, observation in sources:
        reader = _RawReader(raw_chunks(observation["raw_sha256"]), observation["raw_sha256"], observation["raw_bytes"])
        try:
            if observation["profile"] == SCOREBOARD_PROFILE:
                with io.BufferedReader(reader) as stream:
                    rows, audit = _scoreboard(stream.read(), observation)
                games.extend({**row, "source_observation_id": oid, "raw_sha256": observation["raw_sha256"],
                              "observed_utc": observation["observed_utc"]} for row in rows)
                withheld.update(audit["quarantined_game_ids"])
            else:
                audit = _csv_audit(reader, observation)
            if not reader.verified or _encoded(audit) != _encoded(observation["audit"]):
                raise CaptureIntegrityError("capture_source_audit_mismatch")
            actual += reader.total
            if actual > max_total_bytes:
                raise CaptureIntegrityError("capture_total_size_exceeds_limit")
            source_audits.append({"observation_id": oid, "profile": observation["profile"],
                                 "season": observation["season"], "week": observation["week"], "audit": audit})
        finally:
            reader.close()
    seen, conflicts = set(), set()
    for row in games:
        if row["game_id"] in seen:
            conflicts.add(row["game_id"])
        seen.add(row["game_id"])
    withheld.update(conflicts)
    games = [row for row in games if row["game_id"] not in withheld]
    audit = {"cross_week_conflicting_game_ids": sorted(conflicts), "quarantined_game_ids": sorted(withheld),
             "normalized_games": len(games)}
    if _encoded(audit) != _encoded(manifest["audit"]):
        raise CaptureIntegrityError("capture_global_audit_mismatch")
    return {"version": 1, "kind": "nfl.capture_integrity_report.v1", "verifier_policy": POLICY,
            "capture_id": manifest["capture_id"], "season": manifest["season"], "weeks": manifest["weeks"],
            "verified_raw_bytes": actual, "distinct_unique_raw_bytes": sum(objects.values()),
            "verified_objects": [{"raw_sha256": key, "raw_bytes": objects[key]} for key in sorted(objects)],
            "source_audits": source_audits, "capture_audit": audit, "scoreboard_rows": games,
            "scope": "capture_integrity_only", "source_authentication": "not_performed",
            "labels_accepted": False, "durable_ack": False}
