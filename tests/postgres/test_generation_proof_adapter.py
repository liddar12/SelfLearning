"""Python deadline-aware proof port over real SQL and actual producer fixtures."""
from test_capture_application_adapter import application_bridge, journal, raw_producer, initial, prepare, commit_first
from selflearn_core.adapters.nfl_capture_application_v2 import bounded


def test_real_scoped_prepare_resumes_one_generation_proof_without_journal_writes(journal, application_bridge, raw_producer):
    initial(journal, raw_producer)
    documents = [row["request"]["payload"] for row in raw_producer["initial"]["generations"]]
    source = documents[0]["receipts"][0]["learning_receipt"]
    scope = {"policy": "nfl.capture_game_scope_v1", "season": source["season"],
             "week": source["week"], "game_id": source["game_id"]}
    before = journal.read_observed_game_snapshot(scope)
    first = journal._application_rpc("selflearn_journal_prepare_capture_generation_proofs_v1",
                                     {"p_game_scope_json": bounded(scope, 1024)})
    assert first["verified_generation_count"] == 1
    assert first["remaining_generation_count"] > 0
    start = len(application_bridge.calls)
    prepared = journal.prepare_capture_membership(source["season"], [source["week"]], game_scope=scope)
    assert prepared["member_count"] == len(before["receipts"])
    assert prepared["durable_ack"] is False
    after = journal.read_observed_game_snapshot(scope)
    assert after == before
    calls = application_bridge.calls[start:]
    proof_calls = [call for call in calls if call["path"].endswith("selflearn_journal_prepare_capture_generation_proofs_v1")]
    assert len(proof_calls) == first["remaining_generation_count"]
    final = journal.prepare_capture_generation_proofs(scope)
    assert final["added"] is False
    assert final["prepared_object_id"] is None
    assert final["remaining_generation_count"] == 0


def test_real_scoped_batch_uses_extended_route_and_exact_replay(journal, application_bridge, raw_producer):
    initial(journal, raw_producer)
    first = raw_producer["initial"]["generations"][0]["request"]["payload"]
    source = first["receipts"][0]["learning_receipt"]
    scope = {"policy": "nfl.capture_game_scope_v1", "season": source["season"],
             "week": source["week"], "game_id": source["game_id"]}
    journal.prepare_capture_membership(scope["season"], [scope["week"]], game_scope=scope)
    ctx, seal, _ = prepare(journal, raw_producer["captures"][0], game_scope=scope)
    journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
    result = commit_first(journal, ctx, seal)
    assert result["added_revisions"] == result["added_observations"] == 4
    writes = [call for call in application_bridge.calls if "commit_capture_application_batch" in call["path"]]
    assert [call["path"].rsplit("/", 1)[-1] for call in writes] == ["selflearn_journal_commit_capture_application_batch_v3"]
    assert commit_first(journal, ctx, seal) == result
    assert [call for call in application_bridge.calls if "commit_capture_application_batch" in call["path"]] == writes
    assert journal.get_capture_application(seal["application_id"])["terminal"]["counts"]["accepted_members"] == 4


def test_real_report_bundle_keeps_one_rpc_and_same_cross_view_checks(journal, application_bridge, raw_producer):
    from copy import deepcopy
    from unittest.mock import patch
    import pytest
    from test_capture_application_api import BASE, api_client, headers, receiver_for
    from selflearn_core.store.supabase_store import StoreError
    from services.web.nfl_capture_applications import CaptureApplicationReceiver

    initial(journal, raw_producer)
    ctx, seal, _ = prepare(journal, raw_producer["captures"][0])
    journal.register_capture_application(ctx["request"], seal, watermark=ctx["snapshot"]["watermark"])
    aid = seal["application_id"]
    active = journal.get_capture_application(aid)
    commit_first(journal, ctx, seal)
    original = journal.get_capture_application(aid)
    assert original["terminal"] is not None
    begin = len(application_bridge.calls)
    assert journal.get_capture_application_read_report(aid) == original
    assert [call["path"].rsplit("/", 1)[-1] for call in application_bridge.calls[begin:]] == [
        "selflearn_journal_read_capture_application_bundle_v1"]
    assert journal.get_capture_application_read_report(aid, ledger_watermark=active["ledger_watermark"]) == active

    receiver, storage, quota = receiver_for(journal, destination=ctx["request"]["capture_request"]["destination_id"])
    optimized = CaptureApplicationReceiver(journal, receiver.evidence, quota, destination_id=receiver.destination_id,
        producer_id=receiver.producer_id, report_bundle=True)
    with api_client(receiver) as before, api_client(optimized) as after:
        path = f"{BASE}/{aid}"
        for suffix in ("?view=registration", "?view=groups&limit=1", "?view=batches", "", "?view=terminal",
                       "/steps/"+seal["batches"][0]["step_id"], "?view=terminal&ledger_watermark="+active["ledger_watermark"]):
            wanted = before.get(path+suffix, headers=headers())
            count = len(application_bridge.calls)
            got = after.get(path+suffix, headers=headers())
            assert (got.status_code, got.content) == (200, wanted.content)
            assert len(application_bridge.calls) - count == 1
        assert not storage.calls

    send = journal._request
    calls = []

    def request_pages(method, path, *, data):
        calls.append(path)
        if path.endswith("read_capture_application_bundle_v1"):
            return {"version": 2, "kind": "nfl.capture_application_report_bundle.v1",
                    "status": "page_required", "pages": None}
        return send(method, path, data=data)

    with patch.object(journal, "_request", side_effect=request_pages):
        assert journal.get_capture_application_read_report(aid) == original
    assert calls[0].endswith("read_capture_application_bundle_v1")
    assert len(calls) == 6 and all(path.endswith("read_capture_application_v2") for path in calls[1:])

    def corrupt(method, path, *, data):
        response = deepcopy(send(method, path, data=data))
        assert path.endswith("read_capture_application_bundle_v1")
        response["pages"]["status"]["records"][0]["committed_batches"] = 0
        return response
    with patch.object(journal, "_request", side_effect=corrupt), pytest.raises(StoreError) as error:
        journal.get_capture_application_read_report(aid)
    assert error.value.status == 502
