"""Actual Python discovery RPC -> PostgreSQL-WASM; no hosted inference."""
import copy

import pytest

from selflearn_core.probability_receipts import fingerprint
from selflearn_core.learning_jobs import JobConflict
from selflearn_core.store.supabase_store import StoreError
from test_learning_jobs_adapter import actual, command, definition, port


def test_real_discovery_is_one_scoped_rpc_and_claim_still_owns_the_fence(actual):
    journal, bridge = actual
    spec = definition(journal)
    initial = journal.register_job(spec)
    arguments = {"code_id": spec["code_id"], "policy_object_id": spec["policy_object_id"], "limit": 1}
    before = len(bridge.calls)
    found = journal.list_claimable_jobs(spec["namespace"], spec["operation"], **arguments)
    assert found["job_ids"] == [spec["job_id"]]
    assert found["code_id"] == spec["code_id"] and found["policy_object_id"] == spec["policy_object_id"]
    assert len(bridge.calls) == before + 1
    assert bridge.calls[-1]["path"].endswith("selflearn_jobs_discover_v1")
    journal.append_job_command(command(initial))
    assert journal.list_claimable_jobs(spec["namespace"], spec["operation"], **arguments)["job_ids"] == []
    with pytest.raises(JobConflict):
        journal.append_job_command(command(initial, worker="loser"))
    current = journal.read_job(spec["job_id"])
    journal.append_job_command(command(current, "release", reason="worker_error"))
    assert journal.list_claimable_jobs(spec["namespace"], spec["operation"], **arguments)["job_ids"] == [spec["job_id"]]
    assert journal.list_claimable_jobs(spec["namespace"], spec["operation"], **{
        **arguments, "policy_object_id": fingerprint("different-policy")})["job_ids"] == []


def test_real_response_scope_echo_cannot_be_relabelled_by_transport(actual):
    journal, bridge = actual
    spec = definition(journal)
    journal.register_job(spec)
    original = bridge.request
    def relabelled(method, path, *, data):
        response = copy.deepcopy(original(method, path, data=data))
        if path.endswith("selflearn_jobs_discover_v1"):
            response["policy_object_id"] = fingerprint("another-policy")
        return response
    with pytest.raises(StoreError) as error:
        port(relabelled).list_claimable_jobs(spec["namespace"], spec["operation"],
            code_id=spec["code_id"], policy_object_id=spec["policy_object_id"])
    assert error.value.status == 502
