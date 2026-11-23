"""Bounded proof scheduling contract over fake transport; SQL tested separately."""
import copy
import pytest

from selflearn_core.adapters.nfl_capture_application_journal import NFLCaptureApplicationJournal, CaptureApplicationBudgetExceeded
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.supabase_store import StoreError

SCOPE = {"policy": "nfl.capture_game_scope_v1", "season": 2026, "week": 6, "game_id": "G1"}


class Proofs:
    def __init__(self, total=3):
        self.total, self.verified, self.calls, self.mutate, self.clock = total, 0, [], None, 0

    def request(self, method, path, *, data):
        self.calls.append((method, path, copy.deepcopy(data)))
        if path.endswith("selflearn_journal_prepare_capture_membership_v3"):
            assert self.verified == self.total
            return {"version": 2, "kind": "nfl.capture_membership_cache.v2", "scope": "derived_membership_cache_only",
                    "durable_ack": False, "learning_accepted": False, "season": 2026, "weeks": [6], "game_scope": SCOPE,
                    "authority_watermark": "20", "policy_id": fingerprint("membership-policy"), "cache_id": fingerprint("cache"),
                    "membership_id": fingerprint("membership"), "member_count": 8, "group_count": 2}
        assert path.endswith("selflearn_journal_prepare_capture_generation_proofs_v1")
        added = self.verified < self.total
        if added:
            self.verified += 1
        self.clock += 1
        result = {"version": 2, "kind": "nfl.capture_generation_proofs.v1", "scope": "derived_generation_envelope_proofs_only",
                  "game_scope": copy.deepcopy(SCOPE), "policy_id": fingerprint("policy"), "total_generation_count": self.total,
                  "verified_generation_count": self.verified, "remaining_generation_count": self.total - self.verified,
                  "prepared_object_id": fingerprint(self.verified) if added else None, "added": added,
                  "durable_ack": False, "learning_accepted": False}
        if self.mutate:
            self.mutate(result)
        return result


def port(fake, **kwargs):
    return NFLCaptureApplicationJournal("https://offline.supabase.invalid", "sb_secret_proof_fixture", transport=fake, **kwargs)


def test_scoped_prepare_warms_bounded_generations_before_membership_and_reuses_completed_proofs():
    fake = Proofs()
    with port(fake) as journal:
        assert journal.prepare_capture_membership(2026, [6], game_scope=SCOPE)["member_count"] == 8
        assert len(fake.calls) == 4
        assert all(call[2] == {"p_game_scope_json": '{"game_id":"G1","policy":"nfl.capture_game_scope_v1","season":2026,"week":6}'}
                   for call in fake.calls[:-1])
        assert journal.prepare_capture_generation_proofs(SCOPE)["prepared_object_id"] is None
        assert len(fake.calls) == 5


def test_deadline_leaves_completed_proofs_and_next_attempt_resumes():
    fake = Proofs()
    with port(fake, max_run_seconds=2, monotonic=lambda: fake.clock) as journal:
        with pytest.raises(CaptureApplicationBudgetExceeded):
            journal.prepare_capture_membership(2026, [6], game_scope=SCOPE)
    assert fake.verified == 2
    assert len(fake.calls) == 2
    with port(fake) as journal:
        assert journal.prepare_capture_membership(2026, [6], game_scope=SCOPE)["member_count"] == 8
    assert fake.verified == 3
    assert len(fake.calls) == 4


@pytest.mark.parametrize("changes", [
    {"total_generation_count": True}, {"verified_generation_count": 5}, {"remaining_generation_count": 0},
    {"durable_ack": True}, {"learning_accepted": 0}, {"added": 1}, {"prepared_object_id": None},
    {"prepared_object_id": "bad"}, {"policy_id": None}, {"game_scope": {**SCOPE, "game_id": "OTHER"}},
    {"kind": "ack"}, {"extra": "untrusted"}
])
def test_malformed_proof_metadata_never_reaches_membership(changes):
    fake = Proofs()
    fake.mutate = lambda result: result.update(changes)
    with port(fake) as journal, pytest.raises(StoreError) as error:
        journal.prepare_capture_membership(2026, [6], game_scope=SCOPE)
    assert error.value.status == 502
    assert len(fake.calls) == 1


def test_empty_scope_requires_no_certificates_and_mid_attempt_policy_change_is_refused():
    fake = Proofs(total=0)
    with port(fake) as journal:
        assert journal.prepare_capture_generation_proofs(SCOPE)["remaining_generation_count"] == 0
    fake = Proofs()
    fake.mutate = lambda result: result.update(policy_id=fingerprint(fake.verified))
    with port(fake) as journal, pytest.raises(StoreError) as error:
        journal.prepare_capture_generation_proofs(SCOPE)
    assert error.value.status == 502
    assert len(fake.calls) == 2


def test_job_deadline_callback_can_stop_between_verified_generations_without_port_deadline():
    fake = Proofs()
    def check():
        if fake.verified >= 1:
            raise CaptureApplicationBudgetExceeded("pending")
    with port(fake) as journal, pytest.raises(CaptureApplicationBudgetExceeded):
        journal.prepare_capture_membership(2026, [6], game_scope=SCOPE, check=check)
    assert fake.verified == 1
    assert len(fake.calls) == 1


def test_scoped_registration_warms_before_raw_bind_and_exact_original_recovery_stays_first():
    from selflearn_core.adapters import nfl_capture_application_job as job
    from selflearn_core.adapters import nfl_capture_application_v2 as V
    from test_nfl_capture_application_job import FakeApplicationPort

    fake = FakeApplicationPort()
    request = V.make_request(fake.request["capture_request"], game_scope=SCOPE)
    warmed = []
    def pending(season, weeks, *, game_scope):
        warmed.append((season, weeks, game_scope))
        raise CaptureApplicationBudgetExceeded("proofs_pending")
    fake.prepare_capture_membership = pending
    with pytest.raises(CaptureApplicationBudgetExceeded):
        job.register_capture_application(fake, request, fake.raw)
    assert warmed == [(2026, [6], SCOPE)]
    assert fake.raw_reads == []
    assert fake.registers == []
    original = {"original": "registration"}
    fake.application = {"request": request, "registration": original}
    assert job.register_capture_application(fake, request, fake.raw) == original
    assert len(warmed) == 1
