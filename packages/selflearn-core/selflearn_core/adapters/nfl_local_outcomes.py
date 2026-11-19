"""Apply verified captures to a local NFL publication journal."""
from collections import Counter
import json

from ..probability_receipts import instant
from ..store.receipt_journal import JournalConflict, _json
from .nfl_capture_labels import bind_capture_labels
from .nfl_observed_journal import _validate_commit, _validate_fences
from .nfl_observed_resolution import resolve_observed
from .nfl_publication_journal import NFLPublicationJournal


_SCHEMA = """
CREATE TABLE IF NOT EXISTS nfl_local_outcome_observations (
    observation_id TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nfl_local_outcome_captures (
    capture_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    recorded_utc TEXT NOT NULL,
    PRIMARY KEY (capture_id, membership_id)
);
CREATE TRIGGER IF NOT EXISTS nfl_local_outcome_observations_no_update
BEFORE UPDATE ON nfl_local_outcome_observations BEGIN SELECT RAISE(ABORT, 'immutable outcome observation'); END;
CREATE TRIGGER IF NOT EXISTS nfl_local_outcome_observations_no_delete
BEFORE DELETE ON nfl_local_outcome_observations BEGIN SELECT RAISE(ABORT, 'immutable outcome observation'); END;
CREATE TRIGGER IF NOT EXISTS nfl_local_outcome_captures_no_update
BEFORE UPDATE ON nfl_local_outcome_captures BEGIN SELECT RAISE(ABORT, 'immutable outcome capture'); END;
CREATE TRIGGER IF NOT EXISTS nfl_local_outcome_captures_no_delete
BEFORE DELETE ON nfl_local_outcome_captures BEGIN SELECT RAISE(ABORT, 'immutable outcome capture'); END;
"""


class _FrozenView:
    def __init__(self, journal, snapshot):
        self.get_object = journal.get_object  # Source objects are immutable.
        self.snapshot = snapshot

    def read_observed_snapshot(self):
        return self.snapshot


class NFLLocalObservedJournal(NFLPublicationJournal):
    def __init__(self, path, *, read_only=False):
        super().__init__(path, read_only=read_only)
        self._read_only = read_only
        if not read_only:
            try:
                self._db.executescript(_SCHEMA)
            except Exception:
                self.close()
                raise

    def _has_observation_storage(self):
        return self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nfl_local_outcome_observations'",
        ).fetchone() is not None

    def _observed_snapshot(self):
        with self._db:
            self._db.execute("BEGIN")
            snapshot = self._publication_fit_snapshot()
            observations = [json.loads(row[0]) for row in self._db.execute(
                "SELECT payload FROM nfl_local_outcome_observations ORDER BY observation_id",
            )] if self._has_observation_storage() else []
            snapshot["observations"] = observations
            snapshot["watermark"] = str(int(snapshot["watermark"]) + len(observations))
            return snapshot

    def apply_capture(self, envelope, raw_chunks, *, write=False):
        """Rebind replayed captures and commit new outcomes atomically under their original IDs."""
        if write and self._read_only:
            raise ValueError("local_outcome_journal_read_only")
        snapshot = self._observed_snapshot()
        binding = bind_capture_labels(_FrozenView(self, snapshot), envelope, raw_chunks)
        now = self._publication_recorded_utc()
        observed = instant(now)
        if observed < instant(envelope["manifest"]["completed_utc"]):
            raise ValueError("local_outcome_capture_is_in_the_future")
        result = resolve_observed(
            snapshot["receipts"], binding["source_receipts"], binding["observed_legs"],
            binding["game_observations"], observed_utc=now, existing_revisions=snapshot["revisions"],
            existing_observations=snapshot["observations"],
        )
        existing_observations = {row["observation_id"] for row in snapshot["observations"]}
        existing_revisions = {row["revision_id"] for row in snapshot["revisions"]}
        observations = [row for row in result["observation_proposals"] if row["observation_id"] not in existing_observations]
        proposals = [row for row in result["proposals"] if row["revision_id"] not in existing_revisions]
        revisions = [{"version": 1, **row, "recorded_utc": now} for row in proposals]
        _validate_fences(snapshot["receipts"], snapshot["revisions"] + revisions,
                         snapshot["observations"] + observations, observed)
        _validate_commit(snapshot, observations, revisions, observed)
        if write:
            with self._db:
                self._db.execute("BEGIN IMMEDIATE")
                watermark = self._publication_watermark() + self._db.execute(
                    "SELECT count(*) FROM nfl_local_outcome_observations",
                ).fetchone()[0]
                if str(watermark) != snapshot["watermark"]:
                    raise JournalConflict("local_outcome_snapshot_changed_rebind")
                for row in proposals:
                    self._append_resolution(**row)
                self._db.executemany(
                    "INSERT INTO nfl_local_outcome_observations VALUES (?,?)",
                    [(row["observation_id"], _json(row)) for row in observations],
                )
                self._db.execute(
                    "INSERT INTO nfl_local_outcome_captures VALUES (?,?,?) ON CONFLICT DO NOTHING",
                    (binding["capture_id"], binding["membership_id"], self._publication_recorded_utc()),
                )
        return {
            "capture_id": binding["capture_id"], "membership_id": binding["membership_id"],
            "binding_status": binding["status"], "source_forecasts": len(binding["source_receipts"]),
            "bound_measurements": len(binding["observed_legs"]),
            "withheld": dict(Counter(row["reason"] for row in binding["rejections"])),
            "new_revisions": len(proposals), "new_observations": len(observations),
            "committed": write, "adoption_approved": False,
        }
