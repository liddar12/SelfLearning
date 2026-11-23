"""Python deadline-aware proof port over real SQL and actual producer fixtures."""
from test_capture_application_adapter import application_bridge, journal, raw_producer, initial
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
