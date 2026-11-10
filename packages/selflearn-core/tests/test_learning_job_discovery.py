"""Actual SQLite discovery and scoped RPC DTO checks; no worker ownership."""
import copy
import datetime as dt
from unittest.mock import patch

import pytest

from selflearn_core import learning_jobs as jobs
from selflearn_core.learning_job_discovery import validate
from selflearn_core.probability_receipts import fingerprint
from selflearn_core.store.learning_job_journal import LearningJobJournal
from selflearn_core.store.supabase_learning_jobs import SupabaseLearningJobJournal
from selflearn_core.store.supabase_store import StoreError
from test_learning_jobs import command

AUTHORITY = fingerprint("local-job-owner")
NS = {"domain": "example", "task": "binary.forecast", "producer_id": "discovery-fixture"}
CODE = fingerprint("current-worker-code")
OPERATION = "bounded_observation"


def seed(journal, label, now, **changes):
    objects = [{"input": label}, {"policy": "no-activation"}]
    for payload in objects:
        journal.append_object("source_snapshot", fingerprint(payload), payload)
    return journal.register_job(jobs.seal_spec({"version": 1, "kind": "selflearn.job_definition.v1",
        "namespace": NS, "operation": OPERATION, "code_id": CODE,
        "input_object_id": fingerprint(objects[0]), "policy_object_id": fingerprint(objects[1]),
        "not_before_utc": jobs.stamp(now - dt.timedelta(seconds=10)),
        "not_after_utc": jobs.stamp(now + dt.timedelta(seconds=300)),
        "lease_seconds": 30, "max_runtime_seconds": 60, "max_attempts": 3, "max_steps": 4,
        **changes}, authority_id=journal.authority_id))


def test_sqlite_discovers_only_due_claimable_jobs_and_never_reads_histories(tmp_path):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
    with LearningJobJournal(tmp_path / "jobs.db", authority_id=AUTHORITY) as journal:
        clock = [now]
        journal._now = lambda: jobs.stamp(clock[0])
        queued = seed(journal, "queued", now)
        seed(journal, "not-due", now, not_before_utc=jobs.stamp(now + dt.timedelta(seconds=10)))
        seed(journal, "expired", now, not_after_utc=jobs.stamp(now + dt.timedelta(seconds=1)))
        running = seed(journal, "running", now)
        expired_lease = seed(journal, "expired-lease", now, lease_seconds=1)
        runtime = seed(journal, "runtime", now, lease_seconds=1, max_runtime_seconds=1)
        for current in (running, expired_lease, runtime):
            journal.append_job_command(command(current))
        for name, action in (("failed", "fail"), ("complete", "complete"), ("exhausted", "release")):
            current = seed(journal, name, now, **({"max_attempts": 1} if name == "exhausted" else {}))
            journal.append_job_command(command(current))
            current = journal.read_job(current["definition"]["job_id"])
            journal.append_job_command(command(current, action,
                reason="worker_error" if action == "release" else "invalid_input" if action == "fail" else None,
                object_id=current["definition"]["input_object_id"] if action == "complete" else None))
        clock[0] = now + dt.timedelta(seconds=2)
        before = journal._db.total_changes
        with patch.object(journal, "_snapshot", side_effect=AssertionError("discovery loaded full history")):
            result = journal.list_claimable_jobs(NS, OPERATION, code_id=CODE)
        assert set(result["job_ids"]) == {queued["definition"]["job_id"], expired_lease["definition"]["job_id"]}
        assert result["observed_utc"] == jobs.stamp(clock[0])
        assert journal._db.total_changes == before
        assert set(result) == {"version", "authority_id", "namespace", "operation", "code_id", "policy_object_id", "observed_utc", "job_ids"}
        assert journal.receipts() == [] and journal.generations() == []


def test_scope_code_limit_fair_retry_order_and_claim_race(tmp_path):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
    path = tmp_path / "jobs.db"
    with LearningJobJournal(path, authority_id=AUTHORITY) as journal:
        clock = [now]
        journal._now = lambda: jobs.stamp(clock[0])
        old = seed(journal, "retry-oldest", now)
        clock[0] += dt.timedelta(microseconds=10)
        untouched = seed(journal, "untouched", now)
        clock[0] += dt.timedelta(microseconds=10)
        seed(journal, "old-code", now, code_id=fingerprint("old-worker-code"))
        seed(journal, "other-operation", now, operation="different_operation")
        seed(journal, "other-namespace", now, namespace={**NS, "producer_id": "other"})
        journal.append_job_command(command(old))
        old = journal.read_job(old["definition"]["job_id"])
        clock[0] += dt.timedelta(microseconds=10)
        journal.append_job_command(command(old, "release", reason="worker_error"))
        clock[0] += dt.timedelta(microseconds=10)
        assert journal.list_claimable_jobs(NS, OPERATION, code_id=CODE, limit=1)["job_ids"] == [untouched["definition"]["job_id"]]
        result = journal.list_claimable_jobs(NS, OPERATION, code_id=CODE)
        assert result["job_ids"] == [untouched["definition"]["job_id"], old["definition"]["job_id"]]
        assert len(journal.list_claimable_jobs(NS, OPERATION)["job_ids"]) == 3
        stale = journal.read_job(untouched["definition"]["job_id"])
        journal.append_job_command(command(stale))
        with pytest.raises(jobs.JobConflict):
            journal.append_job_command(command(stale, worker="competitor"))
    with LearningJobJournal(path, authority_id=fingerprint("another-authority")) as other:
        assert other.list_claimable_jobs(NS, OPERATION)["job_ids"] == []


def test_submillisecond_not_before_and_exact_expiry_are_not_rounded(tmp_path):
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=123000) + dt.timedelta(seconds=2)
    with LearningJobJournal(tmp_path / "jobs.db", authority_id=AUTHORITY) as journal:
        journal._now = lambda: jobs.stamp(now)
        soon = seed(journal, "one-microsecond-future", now,
                    not_before_utc=jobs.stamp(now + dt.timedelta(microseconds=1)))
        expires = seed(journal, "exact-expiry", now, not_after_utc=jobs.stamp(now + dt.timedelta(microseconds=1)))
        assert journal.list_claimable_jobs(NS, OPERATION)["job_ids"] == [expires["definition"]["job_id"]]
        journal._now = lambda: jobs.stamp(now + dt.timedelta(microseconds=1))
        assert journal.list_claimable_jobs(NS, OPERATION)["job_ids"] == [soon["definition"]["job_id"]]


def test_policy_filter_finds_current_job_behind_more_than_one_batch_of_old_policy(tmp_path):
    now = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=1)
    with LearningJobJournal(tmp_path / "jobs.db", authority_id=AUTHORITY) as journal:
        journal._now = lambda: jobs.stamp(now)
        for index in range(17):
            seed(journal, "older-policy-" + str(index), now)
        payload = {"policy": "current-origin-policy"}
        policy_id = fingerprint(payload)
        journal.append_object("source_snapshot", policy_id, payload)
        now += dt.timedelta(microseconds=1)
        current = seed(journal, "current-policy", now, policy_object_id=policy_id)
        assert current["definition"]["job_id"] not in journal.list_claimable_jobs(NS, OPERATION, code_id=CODE)["job_ids"]
        result = journal.list_claimable_jobs(NS, OPERATION, code_id=CODE, policy_object_id=policy_id)
        assert result["job_ids"] == [current["definition"]["job_id"]]
        assert result["policy_object_id"] == policy_id


@pytest.mark.parametrize("limit", [True, False, 0, -1, 17, 1.0, "1", None])
def test_bad_limit_refuses_before_database_or_transport(limit):
    calls = []
    port = SupabaseLearningJobJournal("https://offline.supabase.invalid", "sb_secret_discovery_fixture",
        authority_id=AUTHORITY, transport=lambda *a, **k: calls.append((a, k)))
    with pytest.raises(ValueError):
        port.list_claimable_jobs(NS, OPERATION, limit=limit)
    assert calls == []


def test_supabase_roundtrip_uses_one_bounded_id_only_rpc():
    expected = {"version": 1, "authority_id": AUTHORITY, "namespace": NS, "operation": OPERATION,
        "code_id": CODE, "policy_object_id": None, "observed_utc": "2026-11-10T12:00:00.000001Z", "job_ids": [fingerprint("job")]}
    calls = []
    def transport(method, path, *, data):
        calls.append((method, path, data))
        return copy.deepcopy(expected)
    port = SupabaseLearningJobJournal("https://offline.supabase.invalid", "sb_secret_discovery_fixture",
        authority_id=AUTHORITY, transport=transport)
    assert port.list_claimable_jobs(NS, OPERATION, code_id=CODE, limit=1) == expected
    assert calls == [("POST", "/rest/v1/rpc/selflearn_jobs_discover_v1", {"p_authority_id": AUTHORITY,
        "p_namespace_json": jobs.canonical(NS), "p_operation": OPERATION, "p_code_id": CODE, "p_policy_object_id": None, "p_limit": 1})]
    for key, bad in (("authority_id", fingerprint("other")), ("namespace", {**NS, "producer_id": "other"}),
                     ("operation", "other"), ("code_id", None), ("policy_object_id", fingerprint("wrong-policy")), ("version", True),
                     ("observed_utc", "not-clock"), ("job_ids", [fingerprint("job")] * 2)):
        corrupted = {**expected, key: bad}
        port._request = lambda *a, **k: corrupted
        with pytest.raises(StoreError) as error:
            port.list_claimable_jobs(NS, OPERATION, code_id=CODE, limit=1)
        assert error.value.status == 502
