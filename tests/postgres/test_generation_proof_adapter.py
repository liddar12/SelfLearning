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
