"""Contract fakes, not database atomicity or public source-authority evidence."""
import copy

import pytest

from selflearn_core.adapters.nfl_observed_job import (
    ObservedCommitError, resolve_and_commit_observed)
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.receipt_journal import JournalConflict
from test_nfl_observed_resolution import PROCESSING, evidence, resolve


class FakeJournal:
    def __init__(self, receipt, *, conflicts=0, snapshot=None, ack=None):
        self.receipt, self.conflicts = receipt, conflicts
        self.snapshot = snapshot
        self.ack, self.reads, self.writes = ack, 0, []

    def read_observed_snapshot(self):
        self.reads += 1
        return copy.deepcopy(self.snapshot if self.snapshot is not None else {
            "version": 1, "watermark": str(self.reads), "receipts": [self.receipt],
            "generation_records": [], "revisions": [], "observations": [], "observation_records": []})

    def commit_observed(self, observations, proposals, *, watermark):
        self.writes.append(copy.deepcopy((observations, proposals, watermark)))
        if self.conflicts:
            self.conflicts -= 1
            raise JournalConflict("untrusted diagnostic must not escape exhaustion")
        return copy.deepcopy(self.ack if self.ack is not None else {
            "version": 1, "added_observations": len(observations),
            "added_revisions": len(proposals), "watermark": str(int(watermark) + 2)})


def run(store, source, leg, games, **kw):
    return resolve_and_commit_observed(store, [source], [leg], games,
                                       clock=lambda: PROCESSING, **kw)


def test_success_is_after_both_streams_commit_and_leaves_input_unchanged():
    r, s, leg, games = evidence()
    before = fingerprint([s, leg, games])
    store = FakeJournal(r)
    result = run(store, s, leg, games)
    assert result["durable"] is True and result["attempts"] == 1
    assert result["commit"]["added_observations"] == result["commit"]["added_revisions"] == 1
    assert fingerprint([s, leg, games]) == before


def test_conflict_rereads_and_resolves_against_new_history_not_old_proposals():
    r, s, leg, games = evidence()
    first = resolve(r, s, [leg], games)
    store = FakeJournal(r, conflicts=1)
    original_read = store.read_observed_snapshot

    def read():
        snap = original_read()
        if store.reads > 1:
            snap.update(revisions=first["proposals"], observations=first["observation_proposals"])
            store.ack = {"version": 1, "added_observations": 0, "added_revisions": 0, "watermark": "2"}
        return snap

    store.read_observed_snapshot = read
    result = run(store, s, leg, games)
    assert result["durable"] is True and result["attempts"] == 2
    assert [row[2] for row in store.writes] == ["1", "2"]
    assert store.writes[1][1] == first["proposals"]


def test_conflicts_are_bounded_and_no_transport_error_becomes_false_success():
    r, s, leg, games = evidence()
    store = FakeJournal(r, conflicts=9)
    with pytest.raises(JournalConflict, match="^observed_commit_conflict_retry_exhausted$"):
        run(store, s, leg, games, max_attempts=2)
    assert store.reads == len(store.writes) == 2
    store = FakeJournal(r)

    def unavailable(*args, **kw):
        raise ObservedCommitError("unavailable")

    store.commit_observed = unavailable
    with pytest.raises(ObservedCommitError, match="unavailable"):
        run(store, s, leg, games)
    assert store.reads == 1


@pytest.mark.parametrize("ack", [
    {}, {"version": True, "added_observations": 1, "added_revisions": 1, "watermark": "3"},
    {"version": 1, "added_observations": True, "added_revisions": 1, "watermark": "3"},
    {"version": 1, "added_observations": 2, "added_revisions": 1, "watermark": "3"},
    {"version": 1, "added_observations": 1, "added_revisions": 1, "watermark": "1"},
    {"version": 1, "added_observations": 0, "added_revisions": 0, "watermark": "0"},
    {"version": 1, "added_observations": 1, "added_revisions": 1, "watermark": 3},
    {"version": 1, "added_observations": 0, "added_revisions": 0, "watermark": "1"},
    {"version": 1, "added_observations": 0, "added_revisions": 1, "watermark": "3"},
    {"version": 1, "added_observations": 1, "added_revisions": 0, "watermark": "3"},
])
def test_malformed_ack_is_not_durable_success(ack):
    r, s, leg, games = evidence()
    with pytest.raises(ObservedCommitError):
        run(FakeJournal(r, ack=ack), s, leg, games)


def test_rejected_evidence_or_invalid_snapshot_is_never_committed():
    r, s, leg, games = evidence()
    store = FakeJournal(r)
    result = run(store, s, {**leg, "actual": None}, games)
    assert result["durable"] is False and result["status"] == "no_accepted_evidence"
    assert store.writes == []
    store.snapshot = {"version": 1, "watermark": "1", "receipts": [r]}
    with pytest.raises(ObservedCommitError, match="invalid_observed_snapshot"):
        run(store, s, leg, games)
    assert store.writes == []


@pytest.mark.parametrize("attempts", [True, 0, 6, 1.5])
def test_invalid_attempt_limit_fails_before_reads(attempts):
    r, s, leg, games = evidence()
    store = FakeJournal(r)
    with pytest.raises(ValueError):
        run(store, s, leg, games, max_attempts=attempts)
    assert store.reads == 0
